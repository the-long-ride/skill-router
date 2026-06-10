"""Status reporting for Skills Router state and host skill paths."""

from __future__ import annotations

import os
import re
from collections import Counter
from pathlib import Path
from typing import Any

from skills_router.agent_bridge.routing import read_routing_state, routing_file_path
from skills_router.config import SkillsRouterConfig
from skills_router.storage.base import AbstractBrainIndexStore


_ENV_VAR_PATTERN = re.compile(
    r"\$[A-Za-z_][A-Za-z0-9_]*|\$\{[^}]+\}|%[A-Za-z_][A-Za-z0-9_]*%"
)


def build_router_status(
    config: SkillsRouterConfig,
    store: AbstractBrainIndexStore,
) -> dict[str, Any]:
    """Return current Skills Router metadata paths, skill paths, and counts."""
    tools = store.get_all_tools()
    dep_graph = store.get_dep_graph()
    routing_state = read_routing_state(config)
    packages = routing_state.get("packages", {})
    workspace_paths = _skill_path_entries(
        config.workspace_skill_dirs,
        scope="workspace",
        workspace_root=Path(config.workspace_root),
    )
    global_paths = _skill_path_entries(
        config.global_skill_dirs,
        scope="global",
        workspace_root=None,
    )
    package_statuses = Counter(
        str(package.get("status", "unknown")) for package in packages.values()
    )
    route_statuses = Counter()
    for package in packages.values():
        package_status = str(package.get("status", "unknown"))
        for rule in package.get("rules", []):
            route_statuses[str(rule.get("status") or package_status)] += 1

    counts = {
        "indexed_tools": len(tools),
        "dependency_entries": len(dep_graph),
        "routing_packages": len(packages),
        "routing_rules": sum(route_statuses.values()),
        "active_routes": route_statuses.get("active", 0),
        "workspace_skill_dirs_configured": len(workspace_paths),
        "workspace_skill_dirs_existing": _existing_count(workspace_paths),
        "global_skill_dirs_configured": len(global_paths),
        "global_skill_dirs_existing": _existing_count(global_paths),
    }
    router_status = _router_status(counts, package_statuses, route_statuses)
    state_paths = _state_paths(config)
    result = {
        "status": "OK",
        "router_status": router_status,
        "storage_backend": config.storage_backend,
        "registry_base_url": config.registry_base_url,
        "data_dir": _resolved_path(config.data_dir),
        "global_data_dir": _resolved_path(config.global_data_dir),
        "workspace_root": _resolved_path(config.workspace_root),
        "state_paths": state_paths,
        "skill_paths": {
            "workspace": workspace_paths,
            "global": global_paths,
        },
        "counts": counts,
        "package_statuses": dict(sorted(package_statuses.items())),
        "route_statuses": dict(sorted(route_statuses.items())),
        "human_summary": _human_summary(router_status, counts, config),
    }
    return result


def _state_paths(config: SkillsRouterConfig) -> list[dict[str, Any]]:
    paths = [
        ("data_dir", config.data_dir, "dir"),
        ("global_data_dir", config.global_data_dir, "dir"),
        ("brain_index", config.brain_index_path, "file"),
        ("dependency_graph", config.dep_graph_path, "file"),
        ("routing_file", str(routing_file_path(config)), "file"),
        ("registry_lockfile", config.registry_lockfile_path, "file"),
        ("registry_cache", config.registry_cache_dir, "dir"),
        ("registry_watch_state", config.registry_watch_state_path, "file"),
        ("audit_log", config.audit_log_path, "file"),
    ]
    return [
        {
            "name": name,
            "path": _resolved_path(raw),
            "kind": kind,
            "exists": Path(_resolved_path(raw)).exists(),
        }
        for name, raw, kind in paths
    ]


def _skill_path_entries(
    raw_paths: list[str],
    *,
    scope: str,
    workspace_root: Path | None,
) -> list[dict[str, Any]]:
    entries = []
    for raw in raw_paths:
        expanded = os.path.expandvars(raw)
        unresolved = bool(_ENV_VAR_PATTERN.search(expanded))
        path = Path(expanded).expanduser()
        if workspace_root is not None and not path.is_absolute():
            path = workspace_root / path
        if unresolved:
            resolved = str(path)
            exists = False
        else:
            resolved = str(path.resolve(strict=False))
            exists = Path(resolved).exists()
        entries.append({
            "scope": scope,
            "configured": raw,
            "path": resolved,
            "exists": exists,
            "env_unresolved": unresolved,
        })
    return entries


def _resolved_path(raw: str | Path) -> str:
    return str(Path(os.path.expandvars(str(raw))).expanduser().resolve(strict=False))


def _existing_count(paths: list[dict[str, Any]]) -> int:
    return sum(1 for path in paths if path.get("exists"))


def _router_status(
    counts: dict[str, int],
    package_statuses: Counter[str],
    route_statuses: Counter[str],
) -> str:
    review_statuses = {"missing_from_index", "needs_selection"}
    if any(package_statuses.get(status, 0) for status in review_statuses):
        return "review_needed"
    if any(route_statuses.get(status, 0) for status in review_statuses):
        return "review_needed"
    if counts["indexed_tools"] == 0 and counts["routing_packages"] == 0:
        return "empty"
    return "ready"


def _human_summary(
    router_status: str,
    counts: dict[str, int],
    config: SkillsRouterConfig,
) -> str:
    return (
        f"Skills Router status: {router_status}; "
        f"{counts['indexed_tools']} indexed tool(s), "
        f"{counts['routing_packages']} routing package(s), "
        f"{counts['active_routes']} active route(s). "
        f"Metadata path: {_resolved_path(config.data_dir)}."
    )
