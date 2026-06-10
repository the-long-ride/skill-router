"""Re-index installed skill/plugin packages and routing decisions."""

from __future__ import annotations

import copy
from collections import Counter
from datetime import datetime, timezone
from typing import Any

import numpy as np
from packaging.version import InvalidVersion, Version

from skills_router.agent_bridge.routing import (
    build_routing_plan,
    read_routing_state,
    write_routing_state,
)
from skills_router.config import SkillsRouterConfig
from skills_router.layers.capability_checker import CapabilityChecker
from skills_router.layers.semantic_evaluator import SemanticEvaluator
from skills_router.storage.base import AbstractBrainIndexStore


REVIEW_STATUS = "needs_selection"
MISSING_STATUS = "missing_from_index"
CONFLICT_CASES = {
    "CASE_2_PARTIAL_OVERLAP",
    "CASE_3_PARENT_CHILD",
    "CASE_4_EXACT_MATCH",
    "CASE_LLM_OVERLAP",
}


def index_installed_skillsets(
    config: SkillsRouterConfig,
    store: AbstractBrainIndexStore,
    *,
    scope: str | None = None,
    persist: bool = True,
    focus_tool_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Refresh installed package routes and detect routing conflicts.

    This intentionally does not uninstall packages or delete route records.
    Missing packages are marked stale so a human or host package manager can
    decide what actually happened outside Skills Router.
    """
    tools = sorted(store.get_all_tools(), key=lambda item: item.get("tool_id", ""))
    evaluator = SemanticEvaluator(
        model_name=config.embedding_model,
        similarity_threshold=config.similarity_threshold,
        max_results=config.semantic_result_limit,
    )
    checker = CapabilityChecker(
        behavior_sim_threshold=config.behavior_sim_threshold,
    )

    refreshed = _refresh_vectors(tools, store, evaluator, persist=persist)
    visible_tools = [
        tool for tool in tools if _is_visible_in_scope(tool, scope)
    ]
    conflicts = _detect_conflicts(visible_tools, evaluator, checker, config)
    if focus_tool_ids is not None:
        conflicts = [
            conflict for conflict in conflicts
            if _conflict_mentions(conflict, focus_tool_ids)
        ]
    stale_routes = _sync_routing_state(
        config,
        tools,
        conflicts,
        persist=persist,
        focus_tool_ids=focus_tool_ids,
    )
    recommendation = _overall_recommendation(conflicts, stale_routes)
    human_prompt = _human_prompt(
        conflicts,
        stale_routes,
        recommendation,
        max_items=config.prompt_list_limit,
    )

    status = "OK"
    if conflicts or stale_routes:
        status = "REVIEW_NEEDED"
    elif not tools:
        status = "EMPTY"

    return {
        "status": status,
        "dry_run": not persist,
        "indexed_tools": len(tools),
        "focused_tools": len(focus_tool_ids or []),
        "visible_tools": len(visible_tools),
        "scope": scope,
        "vectors_refreshed": refreshed,
        "conflicts": conflicts,
        "conflict_count": len(conflicts),
        "stale_routes": stale_routes,
        "stale_route_count": len(stale_routes),
        "recommendation": recommendation,
        "requires_human_decision": bool(conflicts or stale_routes),
        "human_prompt": human_prompt,
    }


def refine_installed_skillsets(
    config: SkillsRouterConfig,
    store: AbstractBrainIndexStore,
    *,
    skillset_names: list[str] | None = None,
    scope: str | None = None,
    workspace_scope: str | None = None,
    persist: bool = True,
    discover: bool = True,
) -> dict[str, Any]:
    """Discover external skillsets, then refresh route/index decisions.

    ``skillset_names`` is optional. Blank means discover and re-index every
    visible installed skill. Names focus the conflict report on those skillsets
    while still comparing them against the wider route surface.
    """
    requested = _clean_requested_skillsets(skillset_names or [])
    discovery = {
        "status": "SKIPPED",
        "records": [],
        "record_count": 0,
        "sources": [],
        "requested": requested,
        "unmatched_requested": list(requested),
        "warnings": [],
    }
    imported = {
        "imported_records": [],
        "updated_records": [],
        "activation_reviews": [],
    }
    working_store = store if persist else _scratch_store(store)
    if discover:
        from skills_router.agent_bridge.discovery import discover_installed_skillsets

        discovery = discover_installed_skillsets(
            config,
            requested_names=requested,
            workspace_scope=workspace_scope or scope,
        )
        imported = _import_discovered_records(
            working_store,
            discovery.get("records", []),
        )

    tools = sorted(working_store.get_all_tools(), key=lambda item: item.get("tool_id", ""))
    focus_tool_ids = _focus_tool_ids(tools, requested) if requested else None
    result = index_installed_skillsets(
        config,
        working_store,
        scope=scope,
        persist=persist,
        focus_tool_ids=focus_tool_ids,
    )

    activation_reviews = imported["activation_reviews"]
    if activation_reviews:
        result["status"] = "REVIEW_NEEDED"
        result["requires_human_decision"] = True
        result["human_prompt"] = _append_activation_prompt(
            result["human_prompt"],
            activation_reviews,
            max_items=config.prompt_list_limit,
        )

    refined_tool_ids = (
        sorted(focus_tool_ids)
        if focus_tool_ids is not None
        else sorted(tool["tool_id"] for tool in tools)
    )
    result.update({
        "command": "refine",
        "requested_skillsets": requested,
        "refined_tool_ids": refined_tool_ids,
        "discovery": {
            "status": discovery.get("status"),
            "record_count": discovery.get("record_count", 0),
            "sources": discovery.get("sources", []),
            "unmatched_requested": discovery.get("unmatched_requested", []),
            "warnings": discovery.get("warnings", []),
        },
        "discovered_records": [
            _discovery_summary(record)
            for record in discovery.get("records", [])
        ],
        "imported_record_count": len(imported["imported_records"]),
        "updated_record_count": len(imported["updated_records"]),
        "activation_reviews": activation_reviews,
        "route_injection": _route_injection_guidance(),
    })
    if requested and not focus_tool_ids:
        result["requires_human_decision"] = bool(result.get("requires_human_decision"))
        result["recommendation"] = (
            "No matching installed skillsets were found. If the package was "
            "installed by an external command, register its Skills Router manifest "
            "or add its host skill directory to discovery config."
        )
    return result


def _scratch_store(store: AbstractBrainIndexStore) -> AbstractBrainIndexStore:
    from skills_router.storage.memory_store import MemoryBrainIndexStore

    scratch = MemoryBrainIndexStore()
    for tool in store.get_all_tools():
        scratch.save_tool(copy.deepcopy(tool))
    scratch.update_dep_graph(copy.deepcopy(store.get_dep_graph()))
    return scratch


def _clean_requested_skillsets(names: list[str]) -> list[str]:
    return [name for name in (str(raw).strip() for raw in names) if name]


def _import_discovered_records(
    store: AbstractBrainIndexStore,
    records: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    imported: list[dict[str, Any]] = []
    updated: list[dict[str, Any]] = []
    activation_reviews: list[dict[str, Any]] = []

    for record in records:
        manifest = copy.deepcopy(record["manifest"])
        tool_id = manifest["tool_id"]
        existing = store.get_tool(tool_id)
        if existing is None:
            store.save_tool(manifest)
            summary = _discovery_summary(record)
            imported.append(summary)
            if record.get("external"):
                activation_reviews.append(_activation_review(record, "new"))
            continue

        if _should_update_discovered_record(existing, manifest):
            store.save_tool(manifest)
            summary = _discovery_summary(record)
            updated.append(summary)
            if record.get("external"):
                activation_reviews.append(_activation_review(record, "changed"))

    return {
        "imported_records": imported,
        "updated_records": updated,
        "activation_reviews": activation_reviews,
    }


def _should_update_discovered_record(
    existing: dict[str, Any],
    discovered: dict[str, Any],
) -> bool:
    existing_meta = existing.get("layer_meta", {})
    discovered_meta = discovered.get("layer_meta", {})
    if not existing_meta.get("skills_router_discovered"):
        return False
    return (
        existing_meta.get("discovered_sha256")
        != discovered_meta.get("discovered_sha256")
    )


def _focus_tool_ids(
    tools: list[dict[str, Any]],
    requested: list[str],
) -> set[str]:
    requested_keys = {_normalise_key(name) for name in requested}
    return {
        tool["tool_id"]
        for tool in tools
        if _tool_matches_request(tool, requested_keys)
    }


def _tool_matches_request(tool: dict[str, Any], requested_keys: set[str]) -> bool:
    if not requested_keys:
        return True
    candidates = {
        _normalise_key(tool.get("tool_id", "")),
        _normalise_key(tool.get("name", "")),
    }
    package = tool.get("agent_package", {})
    for key in ("skillsets", "plugins"):
        candidates |= _skill_keys(package.get(key) or tool.get(key))
    return bool(candidates & requested_keys)


def _skill_keys(raw: Any) -> set[str]:
    values: list[str] = []
    if isinstance(raw, dict):
        for key, value in raw.items():
            values.append(str(key))
            if isinstance(value, dict):
                values.extend(str(value.get(field, "")) for field in ("id", "name"))
            else:
                values.append(str(value))
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                values.extend(str(item.get(field, "")) for field in ("id", "name"))
            else:
                values.append(str(item))
    elif raw:
        values.append(str(raw))
    return {_normalise_key(value) for value in values if _normalise_key(value)}


def _normalise_key(value: str) -> str:
    chars = []
    previous_dash = False
    for char in str(value).lower():
        if char.isalnum():
            chars.append(char)
            previous_dash = False
        elif not previous_dash:
            chars.append("-")
            previous_dash = True
    return "".join(chars).strip("-")


def _discovery_summary(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "tool_id": record["tool_id"],
        "name": record.get("name", record["tool_id"]),
        "source": record.get("source", "unknown"),
        "path": record.get("path", ""),
        "external": bool(record.get("external", False)),
    }


def _activation_review(record: dict[str, Any], state: str) -> dict[str, Any]:
    action = "confirm activation" if state == "new" else "confirm refreshed activation"
    return {
        "tool_id": record["tool_id"],
        "name": record.get("name", record["tool_id"]),
        "source": record.get("source", "unknown"),
        "path": record.get("path", ""),
        "state": state,
        "recommendation": (
            f"{action} before routing tasks to this externally discovered skill."
        ),
    }


def _append_activation_prompt(
    human_prompt: str,
    activation_reviews: list[dict[str, Any]],
    *,
    max_items: int,
) -> str:
    lines = [human_prompt.rstrip(), "", "New external skill routes need confirmation:"]
    for review in activation_reviews[:max_items]:
        lines.append(
            f"- {review['tool_id']}: {review['recommendation']}"
        )
    if len(activation_reviews) > max_items:
        lines.append(
            f"... {len(activation_reviews) - max_items} more external route(s) omitted."
        )
    return "\n".join(lines).strip()


def _route_injection_guidance() -> dict[str, Any]:
    return {
        "strategy": "thin_bridge_dynamic_routes",
        "summary": (
            "Keep static agent instructions small. Teach the host agent to call "
            "Skills Router MCP or CLI route surfaces, then let skills-router.json "
            "provide the current route decisions."
        ),
        "mcp_tools": ["refine_routes", "route_task"],
        "cli_fallback": "skills-router route \"<task>\" --json",
    }


def _refresh_vectors(
    tools: list[dict[str, Any]],
    store: AbstractBrainIndexStore,
    evaluator: SemanticEvaluator,
    *,
    persist: bool,
) -> int:
    refreshed = 0
    for tool in tools:
        vec = evaluator.embed(
            evaluator.create_signature(tool),
            tool.get("tool_id", ""),
        )
        vector = vec.tolist()
        if tool.get("layer_2_vector_signature") != vector:
            refreshed += 1
            if persist:
                tool["layer_2_vector_signature"] = vector
                store.save_tool(tool)
    return refreshed


def _detect_conflicts(
    tools: list[dict[str, Any]],
    evaluator: SemanticEvaluator,
    checker: CapabilityChecker,
    config: SkillsRouterConfig,
) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    for left_idx, left in enumerate(tools):
        for right in tools[left_idx + 1 :]:
            if not _scopes_intersect(_tool_scope(left), _tool_scope(right)):
                continue

            relation = checker.determine_relationship(left, right)
            score = _similarity(left, right, evaluator)
            if not _needs_routing_review(
                left,
                right,
                relation,
                score,
                config.similarity_threshold,
            ):
                continue

            recommendation = _recommend_route(left, right, relation)
            conflicts.append({
                "id": f"{left['tool_id']}::{right['tool_id']}",
                "case": relation.get("case", "UNKNOWN"),
                "similarity_score": score,
                "scope": _combined_scope(left, right),
                "tools": [_tool_summary(left), _tool_summary(right)],
                "details": _relation_details(relation),
                "recommendation": recommendation,
                "human_choice_options": [
                    f"Route shared tasks to {left['tool_id']}",
                    f"Route shared tasks to {right['tool_id']}",
                    "Keep both, but split routes by unique capability",
                    "Custom routing",
                ],
            })
    return conflicts


def _conflict_mentions(conflict: dict[str, Any], tool_ids: set[str]) -> bool:
    return any(
        tool.get("tool_id") in tool_ids
        for tool in conflict.get("tools", [])
    )


def _needs_routing_review(
    left: dict[str, Any],
    right: dict[str, Any],
    relation: dict[str, Any],
    score: float,
    threshold: float,
) -> bool:
    case = relation.get("case")
    if case in CONFLICT_CASES:
        return True
    if case == "CASE_5_TANGENTIAL" and relation.get("shared"):
        return True
    if case == "CASE_LLM_UNKNOWN":
        return score >= threshold
    if score >= threshold and _capability_surface(left) | _capability_surface(right):
        return True
    return False


def _sync_routing_state(
    config: SkillsRouterConfig,
    tools: list[dict[str, Any]],
    conflicts: list[dict[str, Any]],
    *,
    persist: bool,
    focus_tool_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    data = read_routing_state(config)
    packages = data.setdefault("packages", {})
    installed_ids = {tool["tool_id"] for tool in tools}
    conflict_counts = Counter(
        tool["tool_id"]
        for conflict in conflicts
        for tool in conflict.get("tools", [])
    )
    sync_tool_ids = None if focus_tool_ids is None else set(focus_tool_ids)
    if sync_tool_ids is not None:
        sync_tool_ids.update(conflict_counts)
    now = datetime.now(timezone.utc).isoformat()
    stale_routes: list[dict[str, Any]] = []

    for tool_id, package in list(packages.items()):
        if sync_tool_ids is not None and tool_id not in sync_tool_ids:
            continue
        if tool_id in installed_ids:
            continue
        previous_status = package.get("status", "")
        package["status"] = MISSING_STATUS
        package["last_indexed_at"] = now
        for rule in package.get("rules", []):
            rule["status"] = MISSING_STATUS
        stale_routes.append({
            "tool_id": tool_id,
            "name": package.get("name", tool_id),
            "previous_status": previous_status,
            "recommendation": (
                "Keep this route disabled until the host package manager or "
                "registry confirms the package is still installed."
            ),
        })

    for tool in tools:
        tool_id = tool["tool_id"]
        if sync_tool_ids is not None and tool_id not in sync_tool_ids:
            continue
        existing = packages.get(tool_id, {})
        old_conflict_count = int(existing.get("conflict_count", 0) or 0)
        routing_mode = existing.get("routing_mode") or _default_routing_mode(tool)
        target_agents = (
            existing.get("target_agents")
            or tool.get("layer_meta", {}).get("target_agents")
            or []
        )
        plan = build_routing_plan(
            tool,
            scope=_tool_scope(tool),
            package_type=existing.get("package_type", "auto"),
            routing_mode=routing_mode,
            target_agents=target_agents,
        )

        if conflict_counts[tool_id]:
            status = REVIEW_STATUS
        elif old_conflict_count:
            status = plan["status"]
        else:
            status = existing.get("status", plan["status"])
            if status == MISSING_STATUS:
                status = plan["status"]

        rules = _merge_rule_statuses(
            plan.get("rules", []),
            existing.get("rules", []),
            status,
            reset_conflict=bool(conflict_counts[tool_id] or old_conflict_count),
        )
        packages[tool_id] = {
            "tool_id": tool_id,
            "name": plan.get("name", tool.get("name", tool_id)),
            "version": plan.get("version", tool.get("version", "")),
            "package_type": plan.get("package_type", "tool"),
            "physical_install": _physical_install(tool),
            "routing_mode": routing_mode,
            "scope": _tool_scope(tool),
            "status": status,
            "conflict_count": conflict_counts[tool_id],
            "target_agents": plan.get("target_agents", []),
            "applies_to_all_agents": bool(plan.get("applies_to_all_agents", False)),
            "rules": rules,
            "updated_at": now,
            "last_indexed_at": now,
        }

    data["updated_at"] = now
    data["last_indexed_at"] = now
    data["index_status"] = "review_needed" if conflicts or stale_routes else "ok"
    if persist:
        write_routing_state(config, data)
    return stale_routes


def _default_routing_mode(tool: dict[str, Any]) -> str:
    meta = tool.get("layer_meta", {})
    if meta.get("skills_router_discovered") or meta.get("physical_install") == "external_discovery":
        return "selective_routes"
    return "full_package"


def _physical_install(tool: dict[str, Any]) -> str:
    return tool.get("layer_meta", {}).get("physical_install", "full_package")


def _merge_rule_statuses(
    new_rules: list[dict[str, Any]],
    old_rules: list[dict[str, Any]],
    package_status: str,
    *,
    reset_conflict: bool,
) -> list[dict[str, Any]]:
    old_by_id = {rule.get("rule_id"): rule for rule in old_rules}
    merged = []
    for rule in new_rules:
        old = old_by_id.get(rule.get("rule_id"), {})
        next_rule = dict(rule)
        if package_status == REVIEW_STATUS or reset_conflict:
            next_rule["status"] = package_status
        else:
            next_rule["status"] = old.get("status", package_status)
        merged.append(next_rule)
    return merged


def _similarity(
    left: dict[str, Any],
    right: dict[str, Any],
    evaluator: SemanticEvaluator,
) -> float:
    left_vec = _tool_vector(left, evaluator)
    right_vec = _tool_vector(right, evaluator)
    return round(evaluator.cosine(left_vec, right_vec), 4)


def _tool_vector(tool: dict[str, Any], evaluator: SemanticEvaluator) -> np.ndarray:
    return evaluator.embed(evaluator.create_signature(tool), tool.get("tool_id", ""))


def _recommend_route(
    left: dict[str, Any],
    right: dict[str, Any],
    relation: dict[str, Any],
) -> dict[str, str]:
    case = relation.get("case", "")
    preferred = _preferred_tool(left, right, case)
    other = right if preferred["tool_id"] == left["tool_id"] else left
    reason = _preference_reason(preferred, other)

    if case in {"CASE_4_EXACT_MATCH", "CASE_LLM_OVERLAP"}:
        action = "route_overlapping_tasks_to_recommended"
        text = (
            f"Prefer {preferred['tool_id']} for the overlapping route because "
            f"{reason}. Keep the other package installed but inactive for the "
            "same task unless the human chooses otherwise."
        )
    elif case in {"CASE_2_PARTIAL_OVERLAP", "CASE_3_PARENT_CHILD"}:
        action = "prefer_broader_default_keep_specialist"
        text = (
            f"Use {preferred['tool_id']} as the default route and keep "
            f"{other['tool_id']} only for capabilities it uniquely handles; "
            f"{reason}."
        )
    elif case == "CASE_5_TANGENTIAL":
        action = "split_routes_by_unique_capability"
        text = (
            f"Keep both packages, route shared tasks to {preferred['tool_id']}, "
            f"and route unique tasks to their owning package; {reason}."
        )
    else:
        action = "ask_human_manual_compare"
        text = (
            f"Ask the human to compare package READMEs before activation. "
            f"If they need a temporary default, prefer {preferred['tool_id']} "
            f"because {reason}."
        )

    return {
        "recommended_tool_id": preferred["tool_id"],
        "action": action,
        "reason": reason,
        "text": text,
    }


def _preferred_tool(
    left: dict[str, Any],
    right: dict[str, Any],
    case: str,
) -> dict[str, Any]:
    if case == "CASE_2_PARTIAL_OVERLAP":
        return _prefer_unless_much_riskier(left, right)
    if case == "CASE_3_PARENT_CHILD":
        return _prefer_unless_much_riskier(right, left)
    return max((left, right), key=_ranking_key)


def _prefer_unless_much_riskier(
    broader: dict[str, Any],
    narrower: dict[str, Any],
) -> dict[str, Any]:
    if _trust_score(broader) + 0.15 < _trust_score(narrower):
        return narrower
    return broader


def _ranking_key(tool: dict[str, Any]) -> tuple[float, int, int, Version, str]:
    return (
        _trust_score(tool),
        1 if _verified_behavior(tool) else 0,
        len(_capability_surface(tool)),
        _version(tool.get("version", "0")),
        tool.get("tool_id", ""),
    )


def _preference_reason(preferred: dict[str, Any], other: dict[str, Any]) -> str:
    if _trust_score(preferred) > _trust_score(other):
        return "it has the higher trust score"
    if len(_capability_surface(preferred)) > len(_capability_surface(other)):
        return "it covers the broader capability surface"
    if _version(preferred.get("version", "0")) > _version(other.get("version", "0")):
        return "it has the newer indexed version"
    if _verified_behavior(preferred) and not _verified_behavior(other):
        return "it has a verified BehaviorSpec"
    return "it is the safer deterministic default from the current index"


def _overall_recommendation(
    conflicts: list[dict[str, Any]],
    stale_routes: list[dict[str, Any]],
) -> str:
    if conflicts:
        first = conflicts[0]["recommendation"]
        return (
            f"Ask the human to approve new routing. Start with "
            f"{first['recommended_tool_id']}: {first['text']}"
        )
    if stale_routes:
        return (
            "Do not delete routes automatically. Keep stale routes disabled and "
            "ask the human whether the host package manager removed the package."
        )
    return "No conflicts found. Keep generated routes active."


def _human_prompt(
    conflicts: list[dict[str, Any]],
    stale_routes: list[dict[str, Any]],
    recommendation: str,
    *,
    max_items: int,
) -> str:
    if not conflicts and not stale_routes:
        return "Skills Router index is clean. No routing decision is needed."

    lines = [
        "Skills Router re-indexed installed AI-agent skills/plugins.",
        f"Recommendation: {recommendation}",
        "",
    ]
    for idx, conflict in enumerate(conflicts[:max_items], start=1):
        left, right = conflict["tools"]
        rec = conflict["recommendation"]["text"]
        lines.extend([
            (
                f"{idx}. {left['tool_id']} vs {right['tool_id']} "
                f"({conflict['case']}, score {conflict['similarity_score']})"
            ),
            f"   Recommendation: {rec}",
            (
                "   Reply with: use left, use right, split routes, or custom "
                "routing instructions."
            ),
        ])
    if len(conflicts) > max_items:
        lines.append(f"... {len(conflicts) - max_items} more conflict(s) omitted.")

    if stale_routes:
        lines.append("")
        lines.append("Stale routes kept but disabled:")
        for stale in stale_routes[:max_items]:
            lines.append(
                f"- {stale['tool_id']}: {stale['recommendation']}"
            )
    return "\n".join(lines)


def _relation_details(relation: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in relation.items()
        if key != "case" and value not in (None, "", [], {})
    }


def _tool_summary(tool: dict[str, Any]) -> dict[str, Any]:
    return {
        "tool_id": tool["tool_id"],
        "name": tool.get("name", tool["tool_id"]),
        "version": tool.get("version", ""),
        "scope": _tool_scope(tool),
        "trust_score": _trust_score(tool),
    }


def _tool_scope(tool: dict[str, Any]) -> str:
    return tool.get("layer_meta", {}).get("install_scope", "global")


def _is_visible_in_scope(tool: dict[str, Any], scope: str | None) -> bool:
    if not scope:
        return True
    tool_scope = _tool_scope(tool)
    if scope == "global":
        return tool_scope == "global"
    return tool_scope in {"global", scope}


def _scopes_intersect(left: str, right: str) -> bool:
    return left == "global" or right == "global" or left == right


def _combined_scope(left: dict[str, Any], right: dict[str, Any]) -> str:
    left_scope = _tool_scope(left)
    right_scope = _tool_scope(right)
    if left_scope == right_scope:
        return left_scope
    if "global" in {left_scope, right_scope}:
        return "global+workspace"
    return f"{left_scope}+{right_scope}"


def _trust_score(tool: dict[str, Any]) -> float:
    try:
        return float(tool.get("layer_5_provenance", {}).get("trust_score", 0))
    except (TypeError, ValueError):
        return 0.0


def _verified_behavior(tool: dict[str, Any]) -> bool:
    return (
        tool.get("layer_6_behavior_spec", {})
        .get("embedding_confidence") == "verified"
    )


def _version(raw: str) -> Version:
    try:
        return Version(str(raw))
    except InvalidVersion:
        return Version("0")


def _capability_surface(tool: dict[str, Any]) -> set[str]:
    caps = tool.get("layer_3_capabilities", tool.get("capabilities", {}))
    terms = (
        _normalised_set(caps.get("inputs", []))
        | _normalised_set(caps.get("outputs", []))
        | _normalised_set(caps.get("permissions", []))
        | _normalised_set(tool.get("layer_1_domain_tags", []))
    )
    package_meta = tool.get("agent_package", {})
    for key in ("skillsets", "plugins"):
        terms |= _skill_terms(package_meta.get(key))
        terms |= _skill_terms(tool.get(key))
    return terms


def _skill_terms(raw: Any) -> set[str]:
    if not raw:
        return set()
    if isinstance(raw, dict):
        values = []
        for key, value in raw.items():
            values.append(key)
            if isinstance(value, dict):
                values.extend(
                    value.get(field, "")
                    for field in ("name", "description", "use_when")
                )
            else:
                values.append(value)
        return _normalised_set(values)
    if isinstance(raw, list):
        values = []
        for item in raw:
            if isinstance(item, dict):
                values.extend(
                    item.get(field, "")
                    for field in ("id", "name", "description", "use_when")
                )
            else:
                values.append(item)
        return _normalised_set(values)
    return _normalised_set([raw])


def _normalised_set(values: Any) -> set[str]:
    if isinstance(values, str):
        values = [values]
    elif isinstance(values, dict):
        values = [f"{key}: {value}" for key, value in values.items()]
    return {
        " ".join(str(value).strip().lower().split())
        for value in (values or [])
        if str(value).strip()
    }
