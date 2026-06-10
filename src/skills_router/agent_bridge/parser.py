"""Parse chat-shaped /skills-router requests into structured intents."""

from __future__ import annotations

import shlex
from dataclasses import asdict, dataclass, field
from typing import Any

from skills_router.agent_bridge.profiles import (
    default_all_agent_targets,
    normalize_agent_target,
)


COMMAND_ALIASES = {
    "analyze": "analyze",
    "analyse": "analyze",
    "install": "install",
    "add": "install",
    "uninstall": "uninstall",
    "remove": "uninstall",
    "rm": "uninstall",
    "index": "index",
    "reindex": "index",
    "scan": "index",
    "refine": "refine",
    "refresh": "refine",
    "reconcile": "refine",
    "list": "list",
    "ls": "list",
    "status": "status",
    "paths": "status",
    "where": "status",
    "inspect": "inspect",
    "show": "inspect",
    "audit": "audit",
    "watch": "watch",
    "check": "watch",
    "route": "route",
    "route-task": "route",
    "use": "route",
}

FILLER_TOKENS = {
    "a",
    "an",
    "can",
    "could",
    "for",
    "me",
    "my",
    "now",
    "please",
    "pls",
    "the",
    "to",
    "tool",
    "you",
}

PACKAGE_TYPE_TOKENS = {
    "agent": "skillset",
    "agents": "skillset",
    "skill": "skillset",
    "skills": "skillset",
    "skillset": "skillset",
    "skillsets": "skillset",
    "plugin": "plugin",
    "plugins": "plugin",
}

SELECTIVE_ROUTE_MARKERS = (
    "partial",
    "partially",
    "only needed",
    "only the needed",
    "needed skill",
    "needed skillset",
    "needed plugin",
    "selected skill",
    "selected plugin",
    "just the",
    "only use",
)


@dataclass
class SlashCommandIntent:
    """Structured command intent for agent-facing slash requests."""

    command: str
    target: str
    raw_text: str
    arguments: dict[str, Any] = field(default_factory=dict)
    scope: str | None = None
    user_id: str = "local-agent"
    package_type: str = "auto"
    routing_mode: str = "full_package"
    dry_run: bool = False
    auto_approve: bool = False
    delegated: bool = False
    all_agents: bool = False
    agent_targets: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation."""
        return asdict(self)


def parse_slash_command(
    text: str,
    *,
    target: str | None = None,
    agent_id: str = "local-agent",
    default_scope: str | None = None,
) -> SlashCommandIntent:
    """Parse a human slash request such as ``/skills-router install abc for me``.

    The parser is intentionally conservative. It extracts command intent and
    common natural-language modifiers, while leaving actual package resolution
    to the registry resolver.
    """
    if not text or not text.strip():
        raise ValueError("Slash command text is required")

    canonical_target = normalize_agent_target(target)
    tokens = _strip_quotes(_tokenize(text))
    if not tokens:
        raise ValueError("Slash command text is required")

    command_idx = _find_command_index(tokens)
    if command_idx is None:
        raise ValueError("No supported /skills-router command found")

    command = COMMAND_ALIASES[_command_token(tokens[command_idx])]
    tail = tokens[command_idx + 1 :]
    lower_text = text.lower()
    all_agents = command == "install" and _has_all_agents(lower_text, tail)
    scope_default = default_scope or f"workspace:{agent_id}"
    if all_agents:
        scope = "global"
    elif command in {"index", "refine", "status"} and not _has_explicit_scope(tail):
        scope = None
    else:
        scope = _extract_scope(tail, scope_default)
    user_id = _extract_flag_value(tail, "--user") or agent_id
    dry_run = _has_dry_run(lower_text, tail)
    auto_approve = _has_auto_approve(lower_text, tail)
    delegated = _is_delegated(lower_text)
    package_type = _extract_package_type(tail)
    routing_mode = _extract_routing_mode(lower_text)
    args = _extract_command_args(command, tail)

    return SlashCommandIntent(
        command=command,
        target=canonical_target,
        raw_text=text,
        arguments=args,
        scope=scope,
        user_id=user_id,
        package_type=package_type,
        routing_mode=routing_mode,
        dry_run=dry_run,
        auto_approve=auto_approve,
        delegated=delegated,
        all_agents=all_agents,
        agent_targets=list(default_all_agent_targets()) if all_agents else [],
    )


def _tokenize(text: str) -> list[str]:
    try:
        return shlex.split(text)
    except ValueError:
        return text.split()


def _strip_quotes(tokens: list[str]) -> list[str]:
    return [token.strip().strip("\"'") for token in tokens if token.strip()]


def _find_command_index(tokens: list[str]) -> int | None:
    for idx, token in enumerate(tokens):
        normalized = _command_token(token)
        if normalized == "skills-router":
            continue
        if normalized in COMMAND_ALIASES:
            return idx
    return None


def _command_token(token: str) -> str:
    return token.strip().lower().lstrip("/")


def _extract_command_args(command: str, tokens: list[str]) -> dict[str, Any]:
    if command == "analyze":
        source = _first_positional(tokens)
        if not source:
            raise ValueError("Analyze requires an npm/GitHub source link")
        return {"source_ref": source}
    if command == "install":
        package = _first_positional(tokens)
        if not package:
            raise ValueError("Install requires a package name or manifest path")
        return {"package_or_manifest": package}
    if command == "uninstall":
        tool_id = _first_positional(tokens)
        if not tool_id:
            raise ValueError("Uninstall requires a tool id")
        return {"tool_id": tool_id}
    if command == "index":
        return {}
    if command == "refine":
        return {"skillsets": _positionals(tokens)}
    if command == "status":
        return {}
    if command == "inspect":
        tool_id = _first_positional(tokens)
        if not tool_id:
            raise ValueError("Inspect requires a tool id")
        return {"tool_id": tool_id}
    if command == "audit":
        return {
            "tool": _extract_flag_value(tokens, "--tool"),
            "limit": _extract_int_flag(tokens, "--limit"),
        }
    if command == "watch":
        return {"once": True}
    if command == "route":
        task = _task_text(tokens)
        if not task:
            raise ValueError("Route requires task text")
        return {"task": task}
    return {}


def _first_positional(tokens: list[str]) -> str | None:
    skip_next = False
    for idx, token in enumerate(tokens):
        lower = token.lower()
        if skip_next:
            skip_next = False
            continue
        if lower in {"--scope", "--user", "--target", "--limit", "--tool"}:
            skip_next = True
            continue
        if lower.startswith("-"):
            continue
        if lower in FILLER_TOKENS:
            continue
        if idx > 0 and tokens[idx - 1].lower() in {"workspace", "scope"}:
            continue
        if lower in {
            "globally",
            "global",
            "workspace",
            "all",
            "ai",
            "dry",
            "each",
            "every",
            "installed",
            "run",
            "preview",
            "first",
            "approve",
            "approved",
            "agent",
            "agents",
            "just",
            "needed",
            "partial",
            "partially",
            "plugin",
            "plugins",
            "selected",
            "skill",
            "skills",
            "skillset",
            "skillsets",
            "use",
            "yes",
            "y",
        }:
            continue
        return token
    return None


def _positionals(tokens: list[str]) -> list[str]:
    values: list[str] = []
    skip_next = False
    for idx, token in enumerate(tokens):
        lower = token.lower()
        if skip_next:
            skip_next = False
            continue
        if lower in {"--scope", "--user", "--target", "--limit", "--tool"}:
            skip_next = True
            continue
        if lower.startswith("-"):
            continue
        if lower in FILLER_TOKENS:
            continue
        if idx > 0 and tokens[idx - 1].lower() in {"workspace", "scope"}:
            continue
        if lower in {
            "all",
            "everything",
            "globally",
            "global",
            "workspace",
            "ai",
            "dry",
            "each",
            "every",
            "installed",
            "run",
            "preview",
            "first",
            "approve",
            "approved",
            "agent",
            "agents",
            "plugin",
            "plugins",
            "skill",
            "skills",
            "skillset",
            "skillsets",
            "yes",
            "y",
        }:
            continue
        values.append(token)
    return values


def _task_text(tokens: list[str]) -> str:
    values = _positionals(tokens)
    return " ".join(values)


def _extract_scope(tokens: list[str], default_scope: str) -> str:
    explicit = _extract_flag_value(tokens, "--scope")
    if explicit:
        return explicit
    for idx, token in enumerate(tokens):
        lower = token.lower()
        if lower in {"global", "globally"}:
            return "global"
        if lower.startswith("workspace:"):
            return token
        if lower in {"workspace", "scope"} and idx + 1 < len(tokens):
            next_token = tokens[idx + 1]
            if (
                not next_token.startswith("-")
                and next_token.lower() not in FILLER_TOKENS
            ):
                if next_token.startswith("workspace:"):
                    return next_token
                return f"workspace:{next_token}"
    return default_scope


def _has_explicit_scope(tokens: list[str]) -> bool:
    for token in tokens:
        lower = token.lower()
        if lower in {"global", "globally", "workspace", "scope"}:
            return True
        if lower.startswith("workspace:"):
            return True
        if lower.startswith("--scope"):
            return True
    return False


def _extract_flag_value(tokens: list[str], flag: str) -> str | None:
    for idx, token in enumerate(tokens):
        if token == flag and idx + 1 < len(tokens):
            return tokens[idx + 1]
        prefix = flag + "="
        if token.startswith(prefix):
            return token[len(prefix) :]
    return None


def _extract_int_flag(tokens: list[str], flag: str) -> int | None:
    raw = _extract_flag_value(tokens, flag)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _has_token(tokens: list[str], *values: str) -> bool:
    lowered = {token.lower() for token in tokens}
    return any(value.lower() in lowered for value in values)


def _has_dry_run(lower_text: str, tokens: list[str]) -> bool:
    return (
        "--dry-run" in tokens
        or "dry run" in lower_text
        or "dry-run" in lower_text
        or "preview" in lower_text
        or "check first" in lower_text
        or "simulate" in lower_text
    )


def _has_auto_approve(lower_text: str, tokens: list[str]) -> bool:
    return (
        "--yes" in tokens
        or "-y" in tokens
        or "auto approve" in lower_text
        or "auto-approve" in lower_text
        or "accept risk" in lower_text
        or "approve all" in lower_text
        or "force install" in lower_text
    )


def _has_all_agents(lower_text: str, tokens: list[str]) -> bool:
    return (
        "--all-agents" in tokens
        or "all agents" in lower_text
        or "all ai agents" in lower_text
        or "all installed agents" in lower_text
        or "all installed ai agents" in lower_text
        or "all-agent" in lower_text
        or "all-agents" in lower_text
        or "every agent" in lower_text
        or "every installed agent" in lower_text
        or "each agent" in lower_text
    )


def _extract_package_type(tokens: list[str]) -> str:
    for token in tokens:
        package_type = PACKAGE_TYPE_TOKENS.get(token.lower())
        if package_type:
            return package_type
    return "auto"


def _extract_routing_mode(lower_text: str) -> str:
    if any(marker in lower_text for marker in SELECTIVE_ROUTE_MARKERS):
        return "selective_routes"
    return "full_package"


def _is_delegated(lower_text: str) -> bool:
    return (
        "for me" in lower_text
        or "please" in lower_text
        or "pls" in lower_text
        or "can you" in lower_text
        or "could you" in lower_text
    )
