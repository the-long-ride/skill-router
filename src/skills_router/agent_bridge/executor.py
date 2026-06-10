"""Execute parsed agent bridge slash commands."""

from __future__ import annotations

from typing import Any

from skills_router.config import SkillsRouterConfig
from skills_router.layers.lockfile import SkillsRouterLockfile
from skills_router.layers.registry_resolver import RegistryResolutionError, RegistryResolver
from skills_router.layers.source_analyzer import (
    SourceAnalysisError,
    SourceAnalyzer,
    is_supported_source_ref,
)
from skills_router.orchestrator import SkillsRouterOrchestrator
from skills_router.storage.base import AbstractBrainIndexStore
from skills_router.storage.memory_store import MemoryBrainIndexStore

from skills_router.agent_bridge.indexer import index_installed_skillsets
from skills_router.agent_bridge.parser import SlashCommandIntent, parse_slash_command
from skills_router.agent_bridge.routing import (
    build_routing_plan,
    persist_routing_plan,
)
from skills_router.agent_bridge.uninstaller import uninstall_skill_metadata


RISK_PROMPT_MARKERS = (
    "low trust",
    "dependency conflict",
    "exact duplicate",
    "duplicate",
    "overlap",
    "redundancy",
    "cannot auto-compare",
    "llm behavioral overlap",
    "trust degraded",
)


def execute_slash_command(
    text: str,
    config: SkillsRouterConfig,
    *,
    target: str | None = None,
    agent_id: str = "local-agent",
    default_scope: str | None = None,
) -> dict[str, Any]:
    """Parse and execute a chat-shaped Skills Router slash command."""
    intent = parse_slash_command(
        text,
        target=target,
        agent_id=agent_id,
        default_scope=default_scope,
    )
    result = execute_intent(intent, config)
    result.setdefault("intent", intent.to_dict())
    result.setdefault("human_summary", summarize_result(result))
    return result


def execute_intent(intent: SlashCommandIntent, config: SkillsRouterConfig) -> dict[str, Any]:
    """Execute a structured bridge intent."""
    store = _build_store(config)
    if intent.command == "analyze":
        return SourceAnalyzer(config).analyze(intent.arguments["source_ref"])
    if intent.command == "install":
        return _install(intent, config, store)
    if intent.command == "uninstall":
        return uninstall_skill_metadata(
            config,
            store,
            intent.arguments["tool_id"],
            user_id=intent.user_id,
            scope=intent.scope,
            dry_run=intent.dry_run,
        )
    if intent.command == "index":
        return index_installed_skillsets(
            config,
            store,
            scope=intent.scope,
            persist=not intent.dry_run,
        )
    if intent.command == "refine":
        from skills_router.agent_bridge.indexer import refine_installed_skillsets

        return refine_installed_skillsets(
            config,
            store,
            skillset_names=intent.arguments.get("skillsets", []),
            scope=intent.scope,
            workspace_scope=_refine_workspace_scope(intent),
            persist=not intent.dry_run,
        )
    if intent.command == "list":
        orchestrator = SkillsRouterOrchestrator(config=config, store=store)
        tools = orchestrator.list_tools(scope=intent.scope)
        return {"status": "OK", "tools": tools, "count": len(tools)}
    if intent.command == "status":
        from skills_router.status import build_router_status

        return build_router_status(config, store)
    if intent.command == "inspect":
        orchestrator = SkillsRouterOrchestrator(config=config, store=store)
        tool_id = intent.arguments["tool_id"]
        tool = orchestrator.inspect_tool(tool_id)
        if tool is None:
            return {"status": "NOT_FOUND", "tool_id": tool_id}
        return {"status": "OK", "tool": tool}
    if intent.command == "watch":
        return _watch_once(config, store, dry_run=intent.dry_run)
    if intent.command == "route":
        from skills_router.agent_bridge.routing import route_task

        return route_task(
            config,
            intent.arguments["task"],
            scope=intent.scope,
            agent_target=intent.target,
            include_inactive=True,
        )
    if intent.command == "audit":
        return _audit(intent, config)
    return {"status": "ERROR", "error": f"Unsupported bridge command: {intent.command}"}


def summarize_result(result: dict[str, Any]) -> str:
    """Build a compact human-facing summary for agent responses."""
    status = result.get("status", "UNKNOWN")
    tool_id = result.get("tool_id")
    if status in {"INSTALLED", "DRY_RUN_APPROVED"}:
        action = "Installed" if status == "INSTALLED" else "Dry run approved"
        case = result.get("wg_case", "review")
        routing = result.get("skills_routing", {})
        route_note = ""
        if routing.get("status") == "needs_selection":
            route_note = " Routing needs user selection before activation."
        elif routing.get("rules"):
            route_note = f" Activated {len(routing['rules'])} route(s)."
        target_note = _agent_target_summary(result)
        return (
            f"{action}: {tool_id or 'tool'} passed Skills Router review "
            f"({case}).{route_note}{target_note}"
        )
    if status in {"CANCELLED", "DRY_RUN_CANCELLED"}:
        case = result.get("wg_case", "review")
        return f"Stopped: {tool_id or 'tool'} needs human approval ({case})."
    if status == "DRY_RUN_UNINSTALLED":
        return (
            f"Dry run: would uninstall {tool_id or 'tool'} from Skills Router "
            "metadata/routing. Package resources would not be removed."
        )
    if status == "UNINSTALLED":
        return (
            f"Uninstalled {tool_id or 'tool'} from Skills Router metadata/routing. "
            "Package resources were not removed."
        )
    if status == "HARD_REJECT":
        return f"Rejected: {tool_id or 'tool'} failed the trust gate."
    if status == "OK" and "tools" in result:
        count = result.get("count", len(result.get("tools", [])))
        return f"Listed {count} Skills Router tools."
    if status == "OK" and "state_paths" in result and "skill_paths" in result:
        return str(result.get("human_summary") or "Skills Router status loaded.")
    if status == "OK" and "manifest" in result and "source" in result:
        manifest = result.get("manifest", {})
        source = result.get("source", {})
        confidence = (
            manifest.get("layer_meta", {})
            .get("source_analysis", {})
            .get("confidence", "unknown")
        )
        return (
            f"Analyzed {source.get('identifier', 'source')}; "
            f"inferred {manifest.get('tool_id', 'tool')} ({confidence} confidence)."
        )
    if status in {"OK", "REVIEW_NEEDED", "EMPTY"} and "indexed_tools" in result:
        conflicts = result.get("conflict_count", 0)
        stale = result.get("stale_route_count", 0)
        if result.get("command") == "refine":
            discovered = result.get("discovery", {}).get("record_count", 0)
            activations = len(result.get("activation_reviews", []))
            if status == "EMPTY":
                return "Refined 0 packages. No installed AI-agent skills found."
            if conflicts or stale or activations:
                return (
                    f"Refined {len(result.get('refined_tool_ids', []))} skillset(s); "
                    f"discovered {discovered}, {conflicts} conflict(s), "
                    f"{stale} stale route(s), {activations} activation review(s). "
                    "Ask the human to choose routing; recommendations are included."
                )
            return (
                f"Refined {len(result.get('refined_tool_ids', []))} skillset(s); "
                f"discovered {discovered}. No routing conflicts found."
            )
        if status == "EMPTY":
            return "Indexed 0 packages. No installed Skills Router skills found."
        if conflicts or stale:
            return (
                f"Indexed {result.get('indexed_tools', 0)} package(s); "
                f"{conflicts} conflict(s), {stale} stale route(s). "
                "Ask the human to choose routing; recommendations are included."
            )
        return (
            f"Indexed {result.get('indexed_tools', 0)} package(s). "
            "No routing conflicts found."
        )
    if status == "OK" and "tool" in result:
        tool = result.get("tool") or {}
        return f"Inspected {tool.get('tool_id', tool_id or 'tool')}."
    if status in {"OK", "REVIEW_NEEDED", "NO_ROUTE"} and "routes" in result:
        routes = result.get("routes", [])
        if status == "OK" and routes:
            return f"Route matched: {routes[0]['route']}."
        if status == "REVIEW_NEEDED" and routes:
            return (
                f"Route candidate needs activation: {routes[0]['route']}. "
                "Ask the human before using it."
            )
        return "No active Skills Router route matched this task."
    if status == "NOT_FOUND":
        missing = tool_id or result.get("tool_id") or "item"
        return f"Not found: {missing}."
    if status == "ERROR":
        return f"Skills Router error: {result.get('error', 'unknown error')}."
    return f"Skills Router returned {status}."


def _install(
    intent: SlashCommandIntent,
    config: SkillsRouterConfig,
    store: AbstractBrainIndexStore,
) -> dict[str, Any]:
    manifest_ref = intent.arguments["package_or_manifest"]
    manifest, source_analysis = _resolve_manifest(config, manifest_ref)
    scope = "global" if intent.all_agents else intent.scope or "global"
    target_report = None
    if intent.all_agents:
        from skills_router.agent_bridge.targeting import build_agent_target_report

        target_report = build_agent_target_report(
            config,
            targets=intent.agent_targets,
        )
        manifest.setdefault("layer_meta", {})["target_agents"] = (
            target_report["target_names"]
        )
    routing_plan = build_routing_plan(
        manifest,
        scope=scope,
        package_type=intent.package_type,
        routing_mode=intent.routing_mode,
        target_agents=(
            target_report["target_names"] if target_report is not None else None
        ),
    )
    orchestrator = SkillsRouterOrchestrator(
        config=config,
        store=store,
        decision_callback=_decision_callback(
            auto_approve=intent.auto_approve,
            scope=scope,
        ),
    )
    result = orchestrator.install(
        manifest,
        scope=scope,
        user_id=intent.user_id,
        dry_run=intent.dry_run,
    )
    result["dry_run"] = intent.dry_run
    if result.get("status") in ("INSTALLED", "DRY_RUN_APPROVED"):
        result["skills_routing"] = routing_plan
    if source_analysis is not None:
        result["source_analysis"] = source_analysis
    if target_report is not None:
        result["agent_targets"] = target_report
    if result.get("status") == "INSTALLED":
        installed = store.get_tool(result["tool_id"]) or manifest
        if not isinstance(installed, dict):
            installed = manifest
        SkillsRouterLockfile(config.registry_lockfile_path).upsert(
            installed,
            requested=manifest_ref,
            scope=scope,
        )
        persist_routing_plan(config, routing_plan)
    return result


def _resolve_manifest(
    config: SkillsRouterConfig,
    manifest_ref: str,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    try:
        return RegistryResolver(config).resolve(manifest_ref), None
    except RegistryResolutionError as exc:
        if not is_supported_source_ref(manifest_ref):
            raise
        try:
            analysis = SourceAnalyzer(config).analyze(manifest_ref)
        except SourceAnalysisError as source_exc:
            raise ValueError(
                f"{exc}; source inference also failed: {source_exc}"
            ) from source_exc
        return analysis["manifest"], analysis


def _agent_target_summary(result: dict[str, Any]) -> str:
    report = result.get("agent_targets") or {}
    if not report:
        return ""
    return (
        " Applies to "
        f"{report.get('target_count', 0)} agent target(s) through global routing."
    )


def _decision_callback(auto_approve: bool, scope: str):
    def decide(prompt_text: str, options: list[str]) -> int:
        scope_choice = _scope_option_index(scope, options)
        if auto_approve:
            return 0 if scope_choice is None else scope_choice
        prompt = prompt_text.lower()
        if any(marker in prompt for marker in RISK_PROMPT_MARKERS):
            return max(0, len(options) - 1)
        return 0 if scope_choice is None else scope_choice

    return decide


def _refine_workspace_scope(intent: SlashCommandIntent) -> str | None:
    if intent.scope and intent.scope.startswith("workspace:"):
        return intent.scope
    if intent.scope is None:
        return f"workspace:{intent.user_id}"
    return None


def _scope_option_index(scope: str, options: list[str]) -> int | None:
    desired = "workspace" if scope.startswith("workspace:") else "global"
    for idx, option in enumerate(options):
        lower = option.lower()
        if desired == "workspace" and "workspace" in lower:
            return idx
        if desired == "global" and "global" in lower:
            return idx
    return None


def _watch_once(
    config: SkillsRouterConfig,
    store: AbstractBrainIndexStore,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    from skills_router.daemon.live_signal_fetcher import LiveSignalFetcher
    from skills_router.daemon.registry_watch import RegistryWatchDaemon
    from skills_router.layers.semantic_evaluator import SemanticEvaluator
    from skills_router.layers.trust_gate import TrustGate
    from skills_router.wg.notifier import WGNotifier

    daemon = RegistryWatchDaemon(
        evaluator=SemanticEvaluator(
            model_name=config.embedding_model,
            similarity_threshold=config.similarity_threshold,
        ),
        trust_gate=TrustGate(
            hard_block_threshold=config.trust_hard_block_threshold,
            soft_warn_threshold=config.trust_soft_warn_threshold,
        ),
        brain_index_db=store,
        wg_notifier=WGNotifier(admin_channel_id=config.admin_channel_id, quiet=True),
        live_signal_fetcher=LiveSignalFetcher(
            max_retries=config.max_retries,
            backoff_base=config.backoff_base,
            failure_threshold=config.circuit_failure_threshold,
            reset_seconds=config.circuit_reset_seconds,
        ),
        admin_channel_id=config.admin_channel_id,
        state_path=config.registry_watch_state_path,
        dry_run=dry_run,
    )
    return daemon.run_once(seed_hashes=True, dry_run=dry_run)


def _audit(intent: SlashCommandIntent, config: SkillsRouterConfig) -> dict[str, Any]:
    from skills_router.audit.logger import AuditLogger

    audit_logger = AuditLogger(log_path=config.audit_log_path)
    entries = audit_logger.query(
        tool_id=intent.arguments.get("tool"),
        wg_case=None,
        limit=intent.arguments.get("limit") or 50,
    )
    return {"status": "OK", "entries": entries, "count": len(entries)}


def _build_store(config: SkillsRouterConfig) -> AbstractBrainIndexStore:
    if config.storage_backend == "pgvector":
        from skills_router.storage.pgvector_store import PgVectorBrainIndexStore

        return PgVectorBrainIndexStore(config.pgvector_dsn)
    return MemoryBrainIndexStore(
        brain_index_path=config.brain_index_path,
        dep_graph_path=config.dep_graph_path,
    )
