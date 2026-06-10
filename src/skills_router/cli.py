"""CLI entry point for skills-router.

Provides subcommands: install, uninstall, index, list, status, inspect, audit,
watch, prompt, connect, chat.
"""

from __future__ import annotations

import argparse
import json
import sys
from enum import Enum
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from skills_router.config import SkillsRouterConfig
from skills_router.metrics import REGISTRY as METRICS
from skills_router.orchestrator import SkillsRouterOrchestrator
from skills_router.storage.base import AbstractBrainIndexStore
from skills_router.storage.memory_store import MemoryBrainIndexStore

console = Console()
COMMAND_NAMES = {
    "analyze",
    "install",
    "uninstall",
    "index",
    "refine",
    "list",
    "status",
    "inspect",
    "audit",
    "watch",
    "mcp",
    "prompt",
    "connect",
    "chat",
    "route",
}
EXIT_SUCCESS = 0
EXIT_ERROR = 1
EXIT_REJECTED = 2
EXIT_CANCELLED = 3


def _build_store(config: SkillsRouterConfig) -> AbstractBrainIndexStore:
    """Create the storage backend from config."""
    if config.storage_backend == "pgvector":
        from skills_router.storage.pgvector_store import PgVectorBrainIndexStore

        return PgVectorBrainIndexStore(config.pgvector_dsn)
    if config.storage_backend != "memory":
        raise ValueError(f"Unsupported storage_backend: {config.storage_backend}")
    return MemoryBrainIndexStore(
        brain_index_path=config.brain_index_path,
        dep_graph_path=config.dep_graph_path,
    )


def _jsonable(value: Any) -> Any:
    """Convert common Python/numpy objects into JSON-serializable values."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if hasattr(value, "tolist"):
        return value.tolist()
    return value


def _print_json(data: Any) -> None:
    console.print_json(data=_jsonable(data))


def _result_exit_code(result: dict) -> int:
    status = result.get("status")
    if status in (
        "INSTALLED",
        "UNINSTALLED",
        "DRY_RUN_UNINSTALLED",
        "DRY_RUN_APPROVED",
        "OK",
        "REVIEW_NEEDED",
        "EMPTY",
    ):
        return EXIT_SUCCESS
    if status == "HARD_REJECT":
        return EXIT_REJECTED
    if status in ("CANCELLED", "DRY_RUN_CANCELLED", "NOT_FOUND"):
        return EXIT_CANCELLED
    if status == "ERROR":
        return EXIT_ERROR
    return EXIT_SUCCESS


def _cli_decision(prompt_text: str, options: list[str]) -> int:
    """Interactive CLI decision callback for Workspace/Global steps."""
    console.print()
    console.print(
        Panel(
            prompt_text,
            title="[bold cyan]Workspace/Global Review[/bold cyan]",
            border_style="cyan",
            padding=(1, 2),
        )
    )
    console.print()
    console.print("[bold]Options:[/bold]")
    for i, opt in enumerate(options):
        console.print(f"  [cyan]{i + 1}[/cyan]) {opt}")
    console.print()

    while True:
        try:
            choice = console.input("[bold]Select option (number): [/bold]")
            idx = int(choice.strip()) - 1
            if 0 <= idx < len(options):
                return idx
            console.print(
                f"[red]Please enter a number between 1 and {len(options)}[/red]"
            )
        except (ValueError, EOFError):
            console.print("[red]Invalid input. Please enter a number.[/red]")


def _auto_approve_decision(prompt_text: str, options: list[str]) -> int:
    """Decision callback that selects the first option."""
    return 0


def _auto_approve_decision_for_scope(scope: str | None):
    """Decision callback that prefers the option matching the requested scope."""

    def decide(prompt_text: str, options: list[str]) -> int:
        scope_choice = _scope_option_index(scope or "global", options)
        return 0 if scope_choice is None else scope_choice

    return decide


def _auto_cancel_decision(prompt_text: str, options: list[str]) -> int:
    """Decision callback that selects the safest option."""
    return max(0, len(options) - 1)


def _decision_callback_for_install(args: argparse.Namespace):
    """Choose the install decision mode for interactive and local-agent runs."""
    if getattr(args, "yes", False):
        return _auto_approve_decision_for_scope(getattr(args, "scope", None))

    policy = getattr(args, "decision_policy", "prompt")
    if policy == "approve":
        return _auto_approve_decision_for_scope(getattr(args, "scope", None))
    if policy == "cancel":
        return _auto_cancel_decision

    if sys.stdin.isatty():
        return _cli_decision

    console.print(
        "[yellow]Non-interactive input detected; review decisions will default "
        "to cancel. Use --yes or --decision-policy approve to auto-approve.[/yellow]"
    )
    return _auto_cancel_decision


def _scope_option_index(scope: str, options: list[str]) -> int | None:
    desired = "workspace" if scope.startswith("workspace:") else "global"
    for idx, option in enumerate(options):
        lower = option.lower()
        if desired == "workspace" and "workspace" in lower:
            return idx
        if desired == "global" and "global" in lower:
            return idx
    return None


def _split_agent_targets(values: list[str] | None) -> list[str]:
    targets: list[str] = []
    for value in values or []:
        targets.extend(
            item.strip()
            for item in str(value).split(",")
            if item.strip()
        )
    return targets


def _agent_target_note(result: dict[str, Any]) -> str:
    report = result.get("agent_targets") or {}
    if not report:
        return ""
    return (
        "\n   Applies to: "
        f"{report.get('target_count', 0)} agent target(s) via global routing "
        f"({report.get('installed_target_count', 0)} detected locally)"
    )


def _resolve_manifest_for_install(
    manifest_ref: str,
    config: SkillsRouterConfig,
    *,
    infer: bool = False,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    from skills_router.layers.registry_resolver import (
        RegistryResolutionError,
        RegistryResolver,
    )
    from skills_router.layers.source_analyzer import (
        SourceAnalysisError,
        SourceAnalyzer,
        is_supported_source_ref,
    )

    if infer:
        try:
            analysis = SourceAnalyzer(config).analyze(manifest_ref)
        except SourceAnalysisError as exc:
            raise RegistryResolutionError(str(exc)) from exc
        return analysis["manifest"], analysis

    resolver = RegistryResolver(config)
    try:
        return resolver.resolve(manifest_ref), None
    except RegistryResolutionError as exc:
        if not is_supported_source_ref(manifest_ref):
            raise
        try:
            analysis = SourceAnalyzer(config).analyze(manifest_ref)
        except SourceAnalysisError as source_exc:
            raise RegistryResolutionError(
                f"{exc}; source inference also failed: {source_exc}"
            ) from source_exc
        return analysis["manifest"], analysis


def cmd_analyze(args: argparse.Namespace, config: SkillsRouterConfig) -> int:
    """Handle the analyze subcommand."""
    from skills_router.layers.source_analyzer import SourceAnalysisError, SourceAnalyzer

    try:
        result = SourceAnalyzer(config).analyze(args.source)
    except SourceAnalysisError as exc:
        if getattr(args, "json_output", False):
            _print_json({"status": "ERROR", "error": str(exc)})
        else:
            console.print(f"[red]Error: {exc}[/red]")
        return EXIT_ERROR

    if getattr(args, "json_output", False):
        _print_json(result)
        return EXIT_SUCCESS

    manifest = result["manifest"]
    analysis = manifest.get("layer_meta", {}).get("source_analysis", {})
    warnings = result.get("warnings") or []
    warning_text = "\n".join(f"   Warning: {warning}" for warning in warnings)
    if warning_text:
        warning_text = "\n" + warning_text
    console.print(
        Panel(
            f"Analyzed [bold cyan]{result['source']['identifier']}[/bold cyan]\n"
            f"   Manifest: {manifest['tool_id']} ({manifest['version']})\n"
            f"   Confidence: {analysis.get('confidence', 'unknown')}\n"
            f"   Evidence files: {len(result['evidence'].get('files_seen', []))}"
            f"{warning_text}",
            border_style="cyan",
        )
    )
    return EXIT_SUCCESS


def cmd_install(args: argparse.Namespace, config: SkillsRouterConfig) -> int:
    """Handle the install subcommand."""
    from skills_router.layers.registry_resolver import (
        RegistryResolutionError,
    )

    try:
        manifest, source_analysis = _resolve_manifest_for_install(
            args.manifest,
            config,
            infer=bool(getattr(args, "infer", False)),
        )
    except RegistryResolutionError as e:
        if getattr(args, "json_output", False):
            _print_json({"status": "ERROR", "error": str(e)})
        else:
            console.print(f"[red]Error: {e}[/red]")
        return EXIT_ERROR

    store = _build_store(config)
    all_agents = bool(getattr(args, "all_agents", False))
    agent_targets = _split_agent_targets(getattr(args, "agent_targets", None))
    scope = "global" if all_agents else args.scope or "global"
    target_report = None
    if all_agents:
        from skills_router.agent_bridge.targeting import build_agent_target_report

        target_report = build_agent_target_report(config, targets=agent_targets)
        manifest.setdefault("layer_meta", {})["target_agents"] = (
            target_report["target_names"]
        )

    decision_args = argparse.Namespace(**vars(args))
    decision_args.scope = scope
    orchestrator = SkillsRouterOrchestrator(
        config=config,
        store=store,
        decision_callback=_decision_callback_for_install(decision_args),
    )

    result = orchestrator.install(
        manifest,
        scope=scope,
        user_id=args.user or "cli-user",
        dry_run=getattr(args, "dry_run", False),
    )
    result["dry_run"] = bool(getattr(args, "dry_run", False))
    if source_analysis is not None:
        result["source_analysis"] = source_analysis
    if target_report is not None:
        result["agent_targets"] = target_report
    routing_plan = None
    if result.get("status") in ("INSTALLED", "DRY_RUN_APPROVED"):
        from skills_router.agent_bridge.routing import (
            build_routing_plan,
            persist_routing_plan,
        )

        routing_mode = getattr(args, "routing_mode", "full_package").replace("-", "_")
        routing_plan = build_routing_plan(
            manifest,
            scope=scope,
            package_type=getattr(args, "package_type", "auto"),
            routing_mode=routing_mode,
            target_agents=(
                target_report["target_names"] if target_report is not None else None
            ),
        )
        result["skills_routing"] = routing_plan
    if result.get("status") == "INSTALLED":
        METRICS.inc("installs_total")
        from skills_router.layers.lockfile import SkillsRouterLockfile

        installed = store.get_tool(result["tool_id"]) or manifest
        if not isinstance(installed, dict):
            installed = manifest
        SkillsRouterLockfile(config.registry_lockfile_path).upsert(
            installed,
            requested=args.manifest,
            scope=scope,
        )
        if routing_plan is not None:
            persist_routing_plan(config, routing_plan)
    elif result.get("status") == "HARD_REJECT":
        METRICS.inc("install_rejections_total")
    elif result.get("status") in ("CANCELLED", "DRY_RUN_CANCELLED"):
        METRICS.inc("install_cancellations_total")

    if getattr(args, "json_output", False):
        _print_json(result)
        return _result_exit_code(result)

    console.print()
    if result["status"] == "INSTALLED":
        console.print(
            Panel(
                f"OK [bold green]{result['tool_id']}[/bold green] installed successfully\n"
                f"   Case: {result['wg_case']}  |  Decision: {result['decision']}\n"
                f"   Routing: {result.get('skills_routing', {}).get('status', 'N/A')}"
                f"{_agent_target_note(result)}",
                border_style="green",
            )
        )
    elif result["status"] == "DRY_RUN_APPROVED":
        console.print(
            Panel(
                f"DRY RUN OK [bold green]{result['tool_id']}[/bold green] "
                "would be installed\n"
                f"   Case: {result['wg_case']}  |  Decision: {result['decision']}\n"
                f"   Routing: {result.get('skills_routing', {}).get('status', 'N/A')}"
                f"{_agent_target_note(result)}",
                border_style="green",
            )
        )
    elif result["status"] == "DRY_RUN_CANCELLED":
        console.print(
            Panel(
                f"DRY RUN CANCELLED [bold yellow]{result['tool_id']}[/bold yellow]\n"
                f"   Case: {result['wg_case']}",
                border_style="yellow",
            )
        )
    elif result["status"] == "HARD_REJECT":
        console.print(
            Panel(
                f"REJECTED [bold red]{result['tool_id']}[/bold red] (trust too low)\n"
                f"   {result['details'].get('reason', '')}",
                border_style="red",
            )
        )
    elif result["status"] == "CANCELLED":
        console.print(
            Panel(
                f"CANCELLED [bold yellow]{result['tool_id']}[/bold yellow] "
                "installation cancelled\n"
                f"   Case: {result['wg_case']}",
                border_style="yellow",
            )
        )
    else:
        console.print(f"[yellow]Result: {result}[/yellow]")

    if getattr(args, "explain", False) and result.get("decision_summary"):
        console.print()
        console.print(Panel.fit(
            json.dumps(_jsonable(result["decision_summary"]), indent=2),
            title="[bold cyan]Decision Summary[/bold cyan]",
            border_style="cyan",
        ))

    return _result_exit_code(result)


def cmd_index(args: argparse.Namespace, config: SkillsRouterConfig) -> int:
    """Handle the index subcommand."""
    from skills_router.agent_bridge.indexer import index_installed_skillsets

    store = _build_store(config)
    result = index_installed_skillsets(
        config,
        store,
        scope=args.scope,
        persist=not getattr(args, "dry_run", False),
    )

    if args.json_output:
        _print_json(result)
        return _result_exit_code(result)

    if result["status"] == "EMPTY":
        console.print("[dim]No installed Skills Router skills found.[/dim]")
        return EXIT_SUCCESS

    if result["status"] == "REVIEW_NEEDED":
        console.print(
            Panel(
                result["human_prompt"],
                title="[bold yellow]Routing Review Needed[/bold yellow]",
                border_style="yellow",
            )
        )
        return EXIT_SUCCESS

    console.print(
        Panel(
            f"Indexed {result['indexed_tools']} package(s). "
            "No routing conflicts found.",
            border_style="green",
        )
    )
    return EXIT_SUCCESS


def cmd_refine(args: argparse.Namespace, config: SkillsRouterConfig) -> int:
    """Handle the refine subcommand."""
    from skills_router.agent_bridge.indexer import refine_installed_skillsets

    store = _build_store(config)
    result = refine_installed_skillsets(
        config,
        store,
        skillset_names=args.skillsets,
        scope=args.scope,
        workspace_scope=args.workspace_scope,
        persist=not getattr(args, "dry_run", False),
        discover=not getattr(args, "no_discovery", False),
    )

    if args.json_output:
        _print_json(result)
        return _result_exit_code(result)

    discovery = result.get("discovery", {})
    summary = (
        f"Refined {len(result.get('refined_tool_ids', []))} skill route(s). "
        f"Discovered {discovery.get('record_count', 0)} external/global record(s)."
    )
    if result["status"] == "REVIEW_NEEDED":
        console.print(
            Panel(
                result["human_prompt"],
                title="[bold yellow]Refine Review Needed[/bold yellow]",
                border_style="yellow",
            )
        )
        return EXIT_SUCCESS
    if result["status"] == "EMPTY":
        console.print("[dim]No installed Skills Router skills found.[/dim]")
        return EXIT_SUCCESS

    console.print(Panel(summary, border_style="green"))
    unmatched = discovery.get("unmatched_requested") or []
    if unmatched:
        console.print(
            "[yellow]No discovered or indexed skillsets matched: "
            + ", ".join(unmatched)
            + "[/yellow]"
        )
    return EXIT_SUCCESS


def cmd_uninstall(args: argparse.Namespace, config: SkillsRouterConfig) -> int:
    """Handle the uninstall subcommand."""
    from skills_router.agent_bridge.uninstaller import uninstall_skill_metadata

    store = _build_store(config)
    result = uninstall_skill_metadata(
        config,
        store,
        args.tool_id,
        user_id=args.user or "cli-user",
        scope=args.scope,
        dry_run=getattr(args, "dry_run", False),
    )

    if args.json_output:
        _print_json(result)
        return _result_exit_code(result)

    if result["status"] == "DRY_RUN_UNINSTALLED":
        console.print(
            Panel(
                f"DRY RUN OK [bold green]{result['tool_id']}[/bold green] "
                "would be removed from Skills Router metadata/routing\n"
                "   Package resources would not be removed.",
                border_style="green",
            )
        )
    elif result["status"] == "UNINSTALLED":
        console.print(
            Panel(
                f"OK [bold green]{result['tool_id']}[/bold green] "
                "removed from Skills Router metadata/routing\n"
                "   Package resources were not removed.",
                border_style="green",
            )
        )
        reconciliation = result.get("route_reconciliation") or {}
        if reconciliation.get("requires_human_decision"):
            console.print(
                Panel(
                    reconciliation["human_prompt"],
                    title="[bold yellow]Routing Review Needed[/bold yellow]",
                    border_style="yellow",
                )
            )
    elif result["status"] == "NOT_FOUND":
        console.print(
            f"[yellow]Tool not found in Skills Router metadata: {args.tool_id}[/yellow]"
        )
    else:
        console.print(f"[yellow]Result: {result}[/yellow]")

    return _result_exit_code(result)


def cmd_list(args: argparse.Namespace, config: SkillsRouterConfig) -> int:
    """Handle the list subcommand."""
    store = _build_store(config)
    orchestrator = SkillsRouterOrchestrator(config=config, store=store)
    tools = orchestrator.list_tools(scope=args.scope)

    if args.json_output:
        _print_json({"status": "OK", "tools": tools, "count": len(tools)})
        return EXIT_SUCCESS

    if not tools:
        console.print("[dim]No tools installed.[/dim]")
        return EXIT_SUCCESS

    table = Table(
        title="Installed Tools",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Tool ID", style="bold")
    table.add_column("Name")
    table.add_column("Version")
    table.add_column("Scope")
    table.add_column("Trust", justify="right")

    for t in tools:
        trust = (
            f"{t['trust_score']:.2f}"
            if isinstance(t["trust_score"], float)
            else str(t["trust_score"])
        )
        table.add_row(
            t["tool_id"],
            t["name"],
            t["version"],
            t["scope"],
            trust,
        )

    console.print(table)
    return EXIT_SUCCESS


def cmd_status(args: argparse.Namespace, config: SkillsRouterConfig) -> int:
    """Handle the status subcommand."""
    from skills_router.status import build_router_status

    store = _build_store(config)
    result = build_router_status(config, store)

    if args.json_output:
        _print_json(result)
        return EXIT_SUCCESS

    console.print(
        Panel(
            result["human_summary"],
            title="[bold cyan]Skills Router Status[/bold cyan]",
            border_style="cyan",
        )
    )
    _print_status_state_paths(result)
    _print_status_skill_paths(result)
    return EXIT_SUCCESS


def _print_status_state_paths(result: dict[str, Any]) -> None:
    table = Table(
        title="Router Metadata Paths",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Name", style="bold")
    table.add_column("Kind")
    table.add_column("Exists")
    table.add_column("Path")
    for entry in result.get("state_paths", []):
        table.add_row(
            str(entry.get("name", "")),
            str(entry.get("kind", "")),
            "yes" if entry.get("exists") else "no",
            str(entry.get("path", "")),
        )
    console.print(table)


def _print_status_skill_paths(result: dict[str, Any]) -> None:
    table = Table(
        title="Configured Host Skill Paths",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Scope", style="bold")
    table.add_column("Configured")
    table.add_column("Exists")
    table.add_column("Path")
    rows = []
    for scope in ("workspace", "global"):
        rows.extend(result.get("skill_paths", {}).get(scope, []))
    for entry in rows:
        exists = "unresolved env" if entry.get("env_unresolved") else (
            "yes" if entry.get("exists") else "no"
        )
        table.add_row(
            str(entry.get("scope", "")),
            str(entry.get("configured", "")),
            exists,
            str(entry.get("path", "")),
        )
    console.print(table)


def cmd_inspect(args: argparse.Namespace, config: SkillsRouterConfig) -> int:
    """Handle the inspect subcommand."""
    store = _build_store(config)
    orchestrator = SkillsRouterOrchestrator(config=config, store=store)
    tool = orchestrator.inspect_tool(args.tool_id)

    if tool is None:
        if args.json_output:
            _print_json({"status": "NOT_FOUND", "tool_id": args.tool_id})
            return EXIT_CANCELLED
        console.print(f"[yellow]Tool not found: {args.tool_id}[/yellow]")
        return EXIT_CANCELLED

    if args.json_output:
        _print_json({"status": "OK", "tool": tool})
    else:
        _print_json(tool)
    return EXIT_SUCCESS


def cmd_audit(args: argparse.Namespace, config: SkillsRouterConfig) -> int:
    """Handle the audit subcommand."""
    from skills_router.audit.logger import AuditLogger

    audit_logger = AuditLogger(log_path=config.audit_log_path)
    entries = audit_logger.query(
        tool_id=args.tool,
        wg_case=args.case,
        limit=args.limit or 50,
    )

    if args.json_output:
        _print_json({"status": "OK", "entries": entries, "count": len(entries)})
        return EXIT_SUCCESS

    if not entries:
        console.print("[dim]No audit entries found.[/dim]")
        return EXIT_SUCCESS

    table = Table(
        title="Audit Log",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Timestamp", style="dim")
    table.add_column("Tool ID", style="bold")
    table.add_column("Case")
    table.add_column("Decision")
    table.add_column("Scope")
    table.add_column("Trust", justify="right")

    for e in entries:
        table.add_row(
            e.get("timestamp", "")[:19],
            e.get("tool_id", ""),
            e.get("wg_case", ""),
            e.get("decision", ""),
            e.get("install_scope", ""),
            f"{e.get('trust_score_at_install', 0):.2f}",
        )

    console.print(table)
    return EXIT_SUCCESS


def cmd_watch(args: argparse.Namespace, config: SkillsRouterConfig) -> int:
    """Handle the watch subcommand — starts the Registry Watch Daemon."""
    import time

    from skills_router.daemon.live_signal_fetcher import LiveSignalFetcher
    from skills_router.daemon.registry_watch import RegistryWatchDaemon
    from skills_router.layers.semantic_evaluator import SemanticEvaluator
    from skills_router.layers.trust_gate import TrustGate
    from skills_router.wg.notifier import WGNotifier

    check_interval = (
        config.check_interval_seconds if args.interval is None else args.interval
    )
    if check_interval <= 0:
        console.print("[red]Error: --interval must be greater than 0[/red]")
        sys.exit(1)
    admin_channel_id = args.admin_channel or config.admin_channel_id

    store = _build_store(config)
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
        wg_notifier=WGNotifier(
            admin_channel_id=admin_channel_id,
            quiet=args.json_output,
        ),
        live_signal_fetcher=LiveSignalFetcher(
            max_retries=config.max_retries,
            backoff_base=config.backoff_base,
            failure_threshold=config.circuit_failure_threshold,
            reset_seconds=config.circuit_reset_seconds,
        ),
        admin_channel_id=admin_channel_id,
        check_interval_seconds=check_interval,
        soft_warn_threshold=config.trust_soft_warn_threshold,
        hysteresis_band=config.hysteresis_band,
        state_path=config.registry_watch_state_path,
        dry_run=bool(getattr(args, "dry_run", False)),
    )

    if args.once:
        result = daemon.run_once(
            seed_hashes=True,
            dry_run=bool(getattr(args, "dry_run", False)),
        )
        if args.json_output:
            _print_json(result)
        else:
            if getattr(args, "dry_run", False):
                console.print(
                    "[green]DRY RUN OK Registry Watch completed one check cycle "
                    "without writing state or sending notifications[/green]"
                )
            else:
                console.print("[green]OK Registry Watch completed one check cycle[/green]")
        return EXIT_SUCCESS

    if args.json_output:
        console.print("[red]Error: --json is only supported with watch --once[/red]")
        return EXIT_ERROR

    metrics_server = None
    if args.metrics_port:
        from skills_router.metrics import start_metrics_server

        metrics_server = start_metrics_server(args.metrics_port)
    metrics_status = (
        f"http://127.0.0.1:{args.metrics_port}/metrics"
        if args.metrics_port
        else "disabled"
    )

    console.print(
        Panel(
            f"Registry Watch Daemon started\n"
            f"  Check interval: {check_interval}s\n"
            f"  Admin channel: {admin_channel_id}\n"
            f"  Metrics: {metrics_status}\n"
            f"  Dry run: {'yes' if getattr(args, 'dry_run', False) else 'no'}\n"
            f"  Press Ctrl+C to stop",
            title="[bold cyan]Registry Watch[/bold cyan]",
            border_style="cyan",
        )
    )

    daemon.start()

    # Block until Ctrl+C
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        daemon.stop(timeout=5)
        if metrics_server:
            metrics_server.shutdown()
        console.print("[dim]Daemon stopped.[/dim]")
    return EXIT_SUCCESS


def cmd_mcp(args: argparse.Namespace, config: SkillsRouterConfig) -> int:
    """Run the local stdio tool server for IDE/desktop AI agents."""
    from skills_router.mcp_server import run_mcp_server

    run_mcp_server(config)
    return EXIT_SUCCESS


def cmd_prompt(args: argparse.Namespace, config: SkillsRouterConfig) -> int:
    """Render AI-agent bridge prompts and target metadata."""
    from skills_router.agent_bridge.prompts import (
        render_agent_prompt,
        render_supported_targets,
    )

    if args.list:
        targets = render_supported_targets()
        if args.json_output:
            _print_json({"status": "OK", "targets": targets})
        else:
            table = Table(
                title="Skills Router Bridge Targets",
                show_header=True,
                header_style="bold cyan",
            )
            table.add_column("Target", style="bold")
            table.add_column("Name")
            table.add_column("Instruction Files")
            for target in targets:
                table.add_row(
                    str(target["target"]),
                    str(target["display_name"]),
                    ", ".join(target["instruction_files"]),
                )
            console.print(table)
        return EXIT_SUCCESS

    try:
        prompt_text = render_agent_prompt(
            args.target,
            agent_id=args.agent_id,
            detail=getattr(args, "detail", "compact"),
        )
    except ValueError as exc:
        if args.json_output:
            _print_json({"status": "ERROR", "error": str(exc)})
        else:
            console.print(f"[red]Error: {exc}[/red]")
        return EXIT_ERROR

    if args.json_output:
        _print_json(
            {
                "status": "OK",
                "target": args.target,
                "agent_id": args.agent_id,
                "prompt": prompt_text,
            }
        )
    else:
        console.print(prompt_text)
    return EXIT_SUCCESS


def cmd_connect(args: argparse.Namespace, config: SkillsRouterConfig) -> int:
    """Render or write AI-agent connection setup."""
    from skills_router.agent_bridge.connect import (
        build_agent_connection,
        write_bridge_instructions,
    )

    try:
        result = build_agent_connection(
            config,
            target=args.target,
            agent_id=args.agent_id,
            detail=args.detail,
            from_source=args.from_source,
        )
        if args.write_instructions:
            result["written_instruction"] = write_bridge_instructions(
                config,
                result,
                instruction_file=args.instruction_file,
                dry_run=bool(getattr(args, "dry_run", False)),
            )
        result["dry_run"] = bool(getattr(args, "dry_run", False))
    except ValueError as exc:
        if args.json_output:
            _print_json({"status": "ERROR", "error": str(exc)})
        else:
            console.print(f"[red]Error: {exc}[/red]")
        return EXIT_ERROR

    if args.json_output:
        _print_json(result)
        return EXIT_SUCCESS

    console.print(
        Panel(
            result["human_summary"],
            title="[bold cyan]Skills Router Connect[/bold cyan]",
            border_style="cyan",
        )
    )
    console.print(Panel.fit(
        json.dumps(result["mcp_config"], indent=2),
        title="[bold cyan]MCP Config[/bold cyan]",
        border_style="cyan",
    ))
    table = Table(
        title="Instruction Files",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Use")
    table.add_column("Exists")
    table.add_column("Path")
    for item in result["instruction_files"]:
        table.add_row(
            "yes" if item["recommended"] else "",
            "yes" if item["exists"] else "no",
            str(item["path"]),
        )
    console.print(table)
    if result.get("written_instruction"):
        written = result["written_instruction"]
        console.print(
            f"[green]{written['action'].title()} bridge prompt:[/green] "
            f"{written['path']}"
        )
    else:
        console.print(Panel(
            result["bridge_prompt"],
            title="[bold cyan]Bridge Prompt[/bold cyan]",
            border_style="cyan",
        ))
    console.print(f"[dim]CLI fallback: {result['fallback_command']}[/dim]")
    return EXIT_SUCCESS


def cmd_chat(args: argparse.Namespace, config: SkillsRouterConfig) -> int:
    """Execute or parse a chat-shaped /skills-router request."""
    text = " ".join(args.text)
    try:
        if args.parse_only:
            from skills_router.agent_bridge.parser import parse_slash_command

            intent = parse_slash_command(
                text,
                target=args.target,
                agent_id=args.agent_id,
                default_scope=args.scope,
            )
            result = {"status": "OK", "intent": intent.to_dict()}
        else:
            from skills_router.agent_bridge.executor import execute_slash_command

            result = execute_slash_command(
                text,
                config,
                target=args.target,
                agent_id=args.agent_id,
                default_scope=args.scope,
            )
    except Exception as exc:
        result = {"status": "ERROR", "error": str(exc)}

    if args.json_output:
        _print_json(result)
    else:
        console.print(result.get("human_summary") or result.get("error") or result)
    return _result_exit_code(result)


def cmd_route(args: argparse.Namespace, config: SkillsRouterConfig) -> int:
    """Handle the route subcommand."""
    from skills_router.agent_bridge.routing import route_task

    result = route_task(
        config,
        " ".join(args.text),
        scope=args.scope,
        agent_target=getattr(args, "target", None),
        limit=args.limit,
        include_inactive=args.include_inactive,
    )
    if args.json_output:
        _print_json(result)
        return _result_exit_code(result)
    console.print(result.get("recommendation") or result)
    return _result_exit_code(result)


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="skills-router",
        description="Skills Router - manage agent skills, plugins, and routes",
    )
    parser.add_argument(
        "--data-dir",
        help="Override the data directory (default: ~/.skills-router/)",
    )

    subs = parser.add_subparsers(dest="command", help="Available commands")

    # -- analyze ---------------------------------------------------------------
    p_analyze = subs.add_parser(
        "analyze",
        help="Analyze an npm/GitHub source link and infer a Skills Router manifest",
    )
    p_analyze.add_argument(
        "source",
        help="GitHub URL, npm package URL, github:owner/repo, or npm:<package>",
    )
    _add_json_arg(p_analyze)

    # -- install ---------------------------------------------------------------
    p_install = subs.add_parser(
        "install",
        help="Install a tool from a manifest, registry package, or source link",
    )
    p_install.add_argument(
        "manifest",
        help=(
            "Path to manifest JSON, registry package name, or supported "
            "npm/GitHub source link"
        ),
    )
    p_install.add_argument("--scope", help="Install scope (global, workspace:<id>)")
    p_install.add_argument(
        "--all-agents",
        action="store_true",
        help=(
            "Install once with global routes visible to all configured agent "
            "targets"
        ),
    )
    p_install.add_argument(
        "--agent-target",
        action="append",
        dest="agent_targets",
        help=(
            "Agent target for --all-agents; repeat or comma-separate values "
            "(default: antigravity, antigravity-cli, antigravity-ide, codex, "
            "claude, hermes-agent, opencode, cline, cursor, windsurf)"
        ),
    )
    p_install.add_argument("--user", help="User ID (default: cli-user)")
    p_install.add_argument(
        "--dry-run",
        action="store_true",
        help="Evaluate install gates without writing registry state",
    )
    p_install.add_argument(
        "--explain",
        action="store_true",
        help="Print the decision summary after install or dry-run",
    )
    p_install.add_argument(
        "--infer",
        action="store_true",
        help=(
            "Infer a Skills Router manifest from a supported npm/GitHub source "
            "instead of requiring an existing skills-router.json"
        ),
    )
    p_install.add_argument(
        "--package-type",
        choices=["auto", "skillset", "plugin", "tool"],
        default="auto",
        help="Package kind used for generated AI-agent routing rules",
    )
    p_install.add_argument(
        "--routing-mode",
        choices=["full_package", "selective_routes", "full-package", "selective-routes"],
        default="full_package",
        help="Route all package skills or leave selected routes inactive for human choice",
    )
    _add_json_arg(p_install)
    p_install.add_argument(
        "--decision-policy",
        choices=["prompt", "cancel", "approve"],
        default="prompt",
        help="How to answer review prompts (default: prompt, non-interactive prompt cancels)",
    )
    p_install.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Auto-select the first review option for non-interactive local agents",
    )

    # -- index -----------------------------------------------------------------
    p_index = subs.add_parser(
        "index",
        help="Re-index installed skills/plugins and detect routing conflicts",
    )
    p_index.add_argument(
        "--scope",
        help="Review global plus this workspace scope, or all scopes if omitted",
    )
    p_index.add_argument(
        "--dry-run",
        action="store_true",
        help="Analyze without writing refreshed vectors or routing metadata",
    )
    _add_json_arg(p_index)

    # -- refine ----------------------------------------------------------------
    p_refine = subs.add_parser(
        "refine",
        help="Discover installed AI-agent skills, then refine routing decisions",
    )
    p_refine.add_argument(
        "skillsets",
        nargs="*",
        help="Optional skillset names to refine. Blank means all discovered skills.",
    )
    p_refine.add_argument(
        "--scope",
        help="Review global plus this workspace scope, or all scopes if omitted",
    )
    p_refine.add_argument(
        "--workspace-scope",
        help=(
            "Scope assigned to workspace-discovered skills "
            "(default: workspace:local)"
        ),
    )
    p_refine.add_argument(
        "--dry-run",
        action="store_true",
        help="Analyze discovery and route decisions without writing state",
    )
    p_refine.add_argument(
        "--no-discovery",
        action="store_true",
        help="Skip host/global skill discovery and only refine the current index",
    )
    _add_json_arg(p_refine)

    # -- uninstall ------------------------------------------------------------
    p_uninstall = subs.add_parser(
        "uninstall",
        help="Remove a skill from Skills Router metadata/routing",
    )
    p_uninstall.add_argument(
        "tool_id",
        help="Tool or skill package ID to remove from Skills Router state",
    )
    p_uninstall.add_argument(
        "--scope",
        help="Scope to use when re-indexing remaining routes after uninstall",
    )
    p_uninstall.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview metadata/routing removal without writing state",
    )
    p_uninstall.add_argument("--user", help="User ID (default: cli-user)")
    _add_json_arg(p_uninstall)

    # -- list ------------------------------------------------------------------
    p_list = subs.add_parser("list", help="List installed tools")
    p_list.add_argument("--scope", help="Filter by scope")
    _add_json_arg(p_list)

    # -- status ----------------------------------------------------------------
    p_status = subs.add_parser(
        "status",
        help="Show Skills Router metadata paths, skill paths, and route state",
    )
    _add_json_arg(p_status)

    # -- inspect ---------------------------------------------------------------
    p_inspect = subs.add_parser("inspect", help="Inspect a tool's full Brain Index entry")
    p_inspect.add_argument("tool_id", help="Tool ID to inspect")
    _add_json_arg(p_inspect)

    # -- audit -----------------------------------------------------------------
    p_audit = subs.add_parser("audit", help="Query the audit log")
    p_audit.add_argument("--tool", help="Filter by tool_id")
    p_audit.add_argument("--case", help="Filter by WG case")
    p_audit.add_argument("--limit", type=int, help="Max entries to show")
    _add_json_arg(p_audit)

    # -- watch -----------------------------------------------------------------
    p_watch = subs.add_parser("watch", help="Registry Watch Daemon (Phase 3)")
    p_watch.add_argument(
        "--once",
        action="store_true",
        help="Run one registry watch cycle and exit",
    )
    p_watch.add_argument(
        "--interval",
        type=int,
        help="Override check interval in seconds for daemon mode",
    )
    p_watch.add_argument(
        "--admin-channel",
        help="Override admin channel ID used for fallback notifications",
    )
    p_watch.add_argument(
        "--metrics-port",
        type=int,
        help="Serve Prometheus metrics on this local port in daemon mode",
    )
    p_watch.add_argument(
        "--dry-run",
        action="store_true",
        help="Run checks without saving watch state, trust updates, or notifications",
    )
    _add_json_arg(p_watch)

    # -- mcp -------------------------------------------------------------------
    subs.add_parser("mcp", help="Run local stdio tool server for AI agents")

    # -- prompt ----------------------------------------------------------------
    p_prompt = subs.add_parser(
        "prompt",
        help="Render compact bridge prompts for AI-agent hosts",
    )
    p_prompt.add_argument(
        "--target",
        default="codex",
        help=(
            "Agent target or alias (antigravity, antigravity-cli, "
            "antigravity-ide, codex, claude, hermes-agent, opencode, "
            "cline, cursor, windsurf)"
        ),
    )
    p_prompt.add_argument(
        "--agent-id",
        default="<agent-id>",
        help="Agent id placeholder to embed in the prompt",
    )
    p_prompt.add_argument(
        "--list",
        action="store_true",
        help="List supported agent targets",
    )
    p_prompt.add_argument(
        "--detail",
        choices=["compact", "full"],
        default="compact",
        help="Prompt detail level (default: compact to reduce agent context cost)",
    )
    _add_json_arg(p_prompt)

    # -- connect ---------------------------------------------------------------
    p_connect = subs.add_parser(
        "connect",
        help="Render MCP config and optional bridge instructions for an AI-agent host",
    )
    p_connect.add_argument(
        "--target",
        default="codex",
        help="Agent target or alias",
    )
    p_connect.add_argument(
        "--agent-id",
        default="local-agent",
        help="Agent/user id for workspace scope examples",
    )
    p_connect.add_argument(
        "--detail",
        choices=["compact", "full"],
        default="compact",
        help="Bridge prompt detail level",
    )
    p_connect.add_argument(
        "--from-source",
        action="store_true",
        help="Generate MCP config that runs this checkout through Python/PYTHONPATH",
    )
    p_connect.add_argument(
        "--write-instructions",
        action="store_true",
        help="Write or update a managed bridge prompt block in an instruction file",
    )
    p_connect.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview --write-instructions without writing files",
    )
    p_connect.add_argument(
        "--instruction-file",
        help="Workspace-relative instruction file to write when --write-instructions is set",
    )
    _add_json_arg(p_connect)

    # -- chat ------------------------------------------------------------------
    p_chat = subs.add_parser(
        "chat",
        help="Parse and execute a chat-shaped /skills-router request",
    )
    p_chat.add_argument("text", nargs="+", help="Full slash request text")
    p_chat.add_argument(
        "--target",
        default="codex",
        help="Agent target or alias",
    )
    p_chat.add_argument(
        "--agent-id",
        default="local-agent",
        help="Agent/user id for workspace scope and audit records",
    )
    p_chat.add_argument(
        "--scope",
        help="Default scope when the request does not say global/workspace",
    )
    p_chat.add_argument(
        "--parse-only",
        action="store_true",
        help="Only parse the request and return the structured intent",
    )
    _add_json_arg(p_chat)

    # -- route -----------------------------------------------------------------
    p_route = subs.add_parser(
        "route",
        help="Find the current Skills Router route for a task description",
    )
    p_route.add_argument("text", nargs="+", help="Task text to route")
    p_route.add_argument("--scope", help="Limit lookup to global plus this workspace")
    p_route.add_argument("--target", help="Agent target for target-specific routes")
    p_route.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Maximum candidate routes to return",
    )
    p_route.add_argument(
        "--include-inactive",
        action="store_true",
        help="Include routes that still need human activation",
    )
    _add_json_arg(p_route)

    return parser


def _add_json_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Print machine-readable JSON output",
    )


def main() -> None:
    """CLI entry point."""
    clean_argv = _normalize_slash_args(sys.argv[1:])

    parser = build_parser()
    args = parser.parse_args(clean_argv)

    if not args.command:
        parser.print_help()
        sys.exit(0)

    # Build config
    config_kwargs = {}
    if args.data_dir:
        config_kwargs["data_dir"] = args.data_dir
    config = SkillsRouterConfig(**config_kwargs)

    # Dispatch
    commands = {
        "analyze": cmd_analyze,
        "install": cmd_install,
        "uninstall": cmd_uninstall,
        "index": cmd_index,
        "refine": cmd_refine,
        "list": cmd_list,
        "status": cmd_status,
        "inspect": cmd_inspect,
        "audit": cmd_audit,
        "watch": cmd_watch,
        "mcp": cmd_mcp,
        "prompt": cmd_prompt,
        "connect": cmd_connect,
        "chat": cmd_chat,
        "route": cmd_route,
    }

    handler = commands.get(args.command)
    if handler:
        rc = handler(args, config)
        if isinstance(rc, int) and rc != 0:
            sys.exit(rc)
    else:
        parser.print_help()


def _normalize_slash_args(argv: list[str]) -> list[str]:
    """Normalize chat-style slash command tokens for local wrappers."""
    clean_argv = [arg for arg in argv if arg != "/skills-router"]
    for idx, arg in enumerate(clean_argv):
        if arg.startswith("/") and arg[1:] in COMMAND_NAMES:
            clean_argv[idx] = arg[1:]
            break
    return clean_argv


if __name__ == "__main__":
    main()
