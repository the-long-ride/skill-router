"""Remove Skills Router-owned skill metadata and routes."""

from __future__ import annotations

import copy
from typing import Any

from skills_router.agent_bridge.indexer import index_installed_skillsets
from skills_router.agent_bridge.routing import read_routing_state, remove_tool_routes
from skills_router.audit.logger import AuditLogger
from skills_router.config import SkillsRouterConfig
from skills_router.layers.lockfile import SkillsRouterLockfile
from skills_router.models.audit_log import AuditEntry
from skills_router.models.enums import WGDecision
from skills_router.storage.base import AbstractBrainIndexStore
from skills_router.storage.memory_store import MemoryBrainIndexStore


def uninstall_skill_metadata(
    config: SkillsRouterConfig,
    store: AbstractBrainIndexStore,
    tool_id: str,
    *,
    user_id: str = "local-agent",
    scope: str | None = None,
    reindex: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Uninstall a skill/package from Skills Router-owned state.

    This removes Brain Index, dependency, lockfile, and routing metadata. It does
    not delete package-owned files, virtual environments, repositories, or host
    plugin resources.
    """
    tool = store.get_tool(tool_id)
    lockfile = SkillsRouterLockfile(config.registry_lockfile_path)
    lock_data = lockfile.read()
    lock_record = lock_data.get("tools", {}).get(tool_id)

    removed = {
        "brain_index": False,
        "dependencies": False,
        "lockfile": lock_record is not None,
        "routing": False,
    }
    would_remove = {
        "brain_index": tool is not None,
        "dependencies": _tool_has_dependencies(store.get_dep_graph(), tool_id),
        "lockfile": lock_record is not None,
        "routing": _tool_has_routes(config, tool_id),
    }

    if dry_run:
        if not any(would_remove.values()):
            return {
                "status": "NOT_FOUND",
                "tool_id": tool_id,
                "dry_run": True,
                "removed": removed,
                "would_remove": would_remove,
                "package_resources_removed": False,
                "human_summary": f"Not found in Skills Router skill metadata: {tool_id}.",
            }
        scratch = _scratch_without_tool(store, tool_id)
        reconciliation = (
            index_installed_skillsets(config, scratch, scope=scope, persist=False)
            if reindex
            else None
        )
        result = {
            "status": "DRY_RUN_UNINSTALLED",
            "tool_id": tool_id,
            "dry_run": True,
            "removed": removed,
            "would_remove": would_remove,
            "package_resources_removed": False,
            "human_summary": _dry_run_summary(tool_id, reconciliation),
        }
        if reconciliation is not None:
            result["route_reconciliation"] = reconciliation
            result["requires_human_decision"] = bool(
                reconciliation.get("requires_human_decision")
            )
        return result

    before_deps = store.get_dep_graph()

    if tool is not None:
        removed["brain_index"] = store.delete_tool(tool_id)

    store.remove_deps_for_tool(tool_id)
    removed["dependencies"] = before_deps != store.get_dep_graph()

    if lock_record is not None:
        lockfile.remove(tool_id)

    removed["routing"] = remove_tool_routes(config, tool_id)

    if not any(removed.values()):
        return {
            "status": "NOT_FOUND",
            "tool_id": tool_id,
            "removed": removed,
            "package_resources_removed": False,
            "human_summary": f"Not found in Skills Router skill metadata: {tool_id}.",
        }

    _log_uninstall(config, tool_id, user_id, tool, lock_record)
    reconciliation = (
        index_installed_skillsets(config, store, scope=scope) if reindex else None
    )
    result = {
        "status": "UNINSTALLED",
        "tool_id": tool_id,
        "dry_run": False,
        "removed": removed,
        "package_resources_removed": False,
        "human_summary": _human_summary(tool_id, reconciliation),
    }
    if reconciliation is not None:
        result["route_reconciliation"] = reconciliation
        result["requires_human_decision"] = bool(
            reconciliation.get("requires_human_decision")
        )
    return result


def _tool_has_dependencies(dep_graph: dict[str, Any], tool_id: str) -> bool:
    for info in dep_graph.values():
        required_by = info.get("required_by", []) if isinstance(info, dict) else []
        if tool_id in required_by:
            return True
    return False


def _tool_has_routes(config: SkillsRouterConfig, tool_id: str) -> bool:
    packages = read_routing_state(config).get("packages", {})
    return tool_id in packages


def _scratch_without_tool(
    store: AbstractBrainIndexStore,
    tool_id: str,
) -> MemoryBrainIndexStore:
    scratch = MemoryBrainIndexStore()
    for record in store.get_all_tools():
        if record.get("tool_id") != tool_id:
            scratch.save_tool(copy.deepcopy(record))
    dep_graph = copy.deepcopy(store.get_dep_graph())
    scratch.update_dep_graph(dep_graph)
    scratch.remove_deps_for_tool(tool_id)
    return scratch


def _log_uninstall(
    config: SkillsRouterConfig,
    tool_id: str,
    user_id: str,
    tool: dict[str, Any] | None,
    lock_record: dict[str, Any] | None,
) -> None:
    meta = (tool or {}).get("layer_meta", {})
    prov = (tool or {}).get("layer_5_provenance", {})
    entry = AuditEntry(
        user_id=user_id,
        tool_id=tool_id,
        tool_version=(tool or lock_record or {}).get("version", ""),
        wg_case="UNINSTALL",
        decision=WGDecision.APPROVE.value,
        reason=(
            "Removed Skills Router-owned Brain Index, dependency, lockfile, "
            "and routing metadata; package resources were not deleted."
        ),
        install_scope=meta.get("install_scope") or (lock_record or {}).get("scope", ""),
        trust_score_at_install=float(prov.get("trust_score", 0) or 0),
    )
    AuditLogger(log_path=config.audit_log_path).log(entry)


def _human_summary(
    tool_id: str,
    reconciliation: dict[str, Any] | None,
) -> str:
    base = (
        f"Uninstalled {tool_id} from Skills Router metadata and routing. "
        "Package resources were not removed."
    )
    if not reconciliation:
        return base
    if reconciliation.get("requires_human_decision"):
        conflicts = reconciliation.get("conflict_count", 0)
        stale = reconciliation.get("stale_route_count", 0)
        return (
            f"{base} Remaining routing needs review: {conflicts} conflict(s), "
            f"{stale} stale route(s). Recommendations are included."
        )
    return f"{base} Remaining routes were re-indexed cleanly."


def _dry_run_summary(
    tool_id: str,
    reconciliation: dict[str, Any] | None,
) -> str:
    base = (
        f"Dry run: would uninstall {tool_id} from Skills Router metadata and "
        "routing. Package resources would not be removed."
    )
    if not reconciliation:
        return base
    if reconciliation.get("requires_human_decision"):
        conflicts = reconciliation.get("conflict_count", 0)
        stale = reconciliation.get("stale_route_count", 0)
        return (
            f"{base} Remaining routing would need review: {conflicts} conflict(s), "
            f"{stale} stale route(s). Recommendations are included."
        )
    return f"{base} Remaining routes would re-index cleanly."
