"""Build local AI-agent connection instructions for Skills Router."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from skills_router.agent_bridge.profiles import get_agent_profile
from skills_router.agent_bridge.prompts import render_agent_prompt
from skills_router.config import SkillsRouterConfig


BEGIN_MARKER = "<!-- BEGIN SKILLS ROUTER BRIDGE -->"
END_MARKER = "<!-- END SKILLS ROUTER BRIDGE -->"
SKILL_BEGIN_MARKER = "<!-- BEGIN SKILLS ROUTER BRIDGE SKILL -->"
SKILL_END_MARKER = "<!-- END SKILLS ROUTER BRIDGE SKILL -->"


def build_agent_connection(
    config: SkillsRouterConfig,
    *,
    target: str = "codex",
    agent_id: str = "local-agent",
    detail: str = "compact",
    from_source: bool = False,
) -> dict[str, Any]:
    """Return MCP config, bridge prompt, and instruction paths for one target."""
    profile = get_agent_profile(target)
    bridge_prompt = render_agent_prompt(
        profile.target,
        agent_id=agent_id,
        detail=detail,
    )
    mcp_server = _mcp_server_spec(from_source=from_source)
    instruction_files = [
        _instruction_entry(raw, config, recommended=idx == 0)
        for idx, raw in enumerate(profile.instruction_files)
    ]
    skill_dirs = [
        _skill_dir_entry(raw, config, recommended=idx == 0)
        for idx, raw in enumerate(profile.workspace_skill_dirs)
    ]
    fallback_command = _fallback_command(
        profile.target,
        agent_id=agent_id,
        from_source=from_source,
    )
    return {
        "status": "OK",
        "target": profile.target,
        "display_name": profile.display_name,
        "agent_id": agent_id,
        "mode": "from_source" if from_source else "installed_cli",
        "mcp_config": {"mcpServers": {"skills-router": mcp_server}},
        "mcp_server": mcp_server,
        "bridge_prompt": bridge_prompt,
        "instruction_files": instruction_files,
        "skill_dirs": skill_dirs,
        "fallback_command": fallback_command,
        "human_summary": (
            f"Connection kit ready for {profile.display_name}. Add the MCP "
            "server config and the bridge prompt to the target instruction file."
        ),
    }


def write_bridge_instructions(
    config: SkillsRouterConfig,
    connection: dict[str, Any],
    *,
    instruction_file: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Write or update a managed bridge prompt block in an instruction file."""
    target = instruction_file or _default_instruction_file(connection)
    path = _resolve_workspace_path(target, config, label="Instruction files")
    block = _managed_block(str(connection["bridge_prompt"]))
    action = "created"
    if path.exists():
        current = path.read_text(encoding="utf-8")
        updated = _replace_or_append_block(current, block)
        action = "updated" if BEGIN_MARKER in current and END_MARKER in current else "appended"
    else:
        updated = block + "\n"
    if dry_run:
        preview_action = {
            "created": "would_create",
            "updated": "would_update",
            "appended": "would_append",
        }.get(action, f"would_{action}")
        return {
            "status": "DRY_RUN",
            "dry_run": True,
            "action": preview_action,
            "path": str(path),
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(updated, encoding="utf-8")
    return {
        "status": "OK",
        "dry_run": False,
        "action": action,
        "path": str(path),
    }


def write_bridge_skill(
    config: SkillsRouterConfig,
    connection: dict[str, Any],
    *,
    skill_dir: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Write or update a managed Skills Router SKILL.md for one agent target."""
    target = skill_dir or _default_skill_dir(connection)
    root = _resolve_workspace_path(target, config, label="Skill directories")
    path = root / "skills-router" / "SKILL.md"
    content = _skill_document(connection)
    action = "created"
    if path.exists():
        current = path.read_text(encoding="utf-8")
        if SKILL_BEGIN_MARKER not in current or SKILL_END_MARKER not in current:
            raise ValueError(
                "Refusing to overwrite unmanaged Skills Router skill file. "
                f"Got: {path}"
            )
        action = "updated"
    if dry_run:
        preview_action = {
            "created": "would_create",
            "updated": "would_update",
        }.get(action, f"would_{action}")
        return {
            "status": "DRY_RUN",
            "dry_run": True,
            "action": preview_action,
            "path": str(path),
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return {
        "status": "OK",
        "dry_run": False,
        "action": action,
        "path": str(path),
    }


def _mcp_server_spec(*, from_source: bool) -> dict[str, Any]:
    if not from_source:
        return {"command": "skills-router", "args": ["mcp"]}
    src_root = Path(__file__).resolve().parents[2]
    return {
        "command": sys.executable,
        "args": ["-m", "skills_router.cli", "mcp"],
        "env": {"PYTHONPATH": str(src_root)},
    }


def _fallback_command(target: str, *, agent_id: str, from_source: bool) -> str:
    base = (
        f"{sys.executable} -m skills_router.cli"
        if from_source
        else "skills-router"
    )
    return (
        f'{base} chat "/skills-router <request>" --target {target} '
        f"--agent-id {agent_id} --json"
    )


def _instruction_entry(
    raw: str,
    config: SkillsRouterConfig,
    *,
    recommended: bool,
) -> dict[str, Any]:
    path = _resolve_workspace_path(raw, config, label="Instruction files")
    return {
        "configured": raw,
        "path": str(path),
        "exists": path.exists(),
        "recommended": recommended,
    }


def _skill_dir_entry(
    raw: str,
    config: SkillsRouterConfig,
    *,
    recommended: bool,
) -> dict[str, Any]:
    path = _resolve_workspace_path(raw, config, label="Skill directories")
    skill_path = path / "skills-router" / "SKILL.md"
    return {
        "configured": raw,
        "path": str(path),
        "skill_path": str(skill_path),
        "exists": path.exists(),
        "skill_exists": skill_path.exists(),
        "recommended": recommended,
    }


def _default_instruction_file(connection: dict[str, Any]) -> str:
    files = connection.get("instruction_files") or []
    if not files:
        raise ValueError("No instruction file is configured for this agent target")
    return str(files[0]["configured"])


def _default_skill_dir(connection: dict[str, Any]) -> str:
    dirs = connection.get("skill_dirs") or []
    if not dirs:
        raise ValueError("No workspace skill directory is configured for this agent target")
    return str(dirs[0]["configured"])


def _resolve_workspace_path(raw: str, config: SkillsRouterConfig, *, label: str) -> Path:
    workspace_root = Path(config.workspace_root).resolve(strict=False)
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = workspace_root / path
    path = path.resolve(strict=False)
    try:
        path.relative_to(workspace_root)
    except ValueError as exc:
        raise ValueError(
            f"{label} must be inside the workspace root. "
            f"Got: {path}"
        ) from exc
    return path


def _managed_block(prompt: str) -> str:
    return f"{BEGIN_MARKER}\n{prompt.strip()}\n{END_MARKER}"


def _skill_document(connection: dict[str, Any]) -> str:
    prompt = str(connection["bridge_prompt"]).strip()
    return (
        "---\n"
        "name: skills-router\n"
        "description: Use when the user asks Skills Router to manage AI-agent "
        "skills, plugins, routes, or messages starting /skills-router or "
        "skills-router.\n"
        "---\n\n"
        f"{SKILL_BEGIN_MARKER}\n"
        f"{prompt}\n"
        f"{SKILL_END_MARKER}\n"
    )


def _replace_or_append_block(text: str, block: str) -> str:
    start = text.find(BEGIN_MARKER)
    end = text.find(END_MARKER)
    if start != -1 and end != -1 and end > start:
        end += len(END_MARKER)
        return text[:start] + block + text[end:]
    stripped = text.rstrip()
    if stripped:
        return stripped + "\n\n" + block + "\n"
    return block + "\n"
