"""Profiles for AI-agent hosts that can call skills-router."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentProfile:
    """Small target profile used to render agent-specific bridge guidance."""

    target: str
    display_name: str
    aliases: tuple[str, ...]
    instruction_files: tuple[str, ...]
    mcp_config_hint: str
    invocation_hint: str
    notes: tuple[str, ...] = ()
    workspace_skill_dirs: tuple[str, ...] = ()
    global_skill_dirs: tuple[str, ...] = ()


DEFAULT_ALL_AGENT_TARGETS: tuple[str, ...] = (
    "antigravity",
    "antigravity-cli",
    "antigravity-ide",
    "codex",
    "codex-ide",
    "claude",
    "hermes-agent",
    "opencode",
    "cline",
    "cursor",
    "windsurf",
)


_PROFILES: dict[str, AgentProfile] = {
    "codex": AgentProfile(
        target="codex",
        display_name="OpenAI Codex CLI",
        aliases=("openai-codex", "codex-cli"),
        instruction_files=("AGENTS.md",),
        mcp_config_hint=(
            'Prefer a local stdio MCP server: command "skills-router", '
            'args ["mcp"].'
        ),
        invocation_hint=(
            'If MCP is unavailable, run '
            '`skills-router chat "<slash request>" --target codex --json`.'
        ),
        notes=(
            "Keep the bridge prompt in AGENTS.md or the project instructions Codex reads.",
            "Prefer JSON output and summarize only the human-facing result.",
        ),
        workspace_skill_dirs=(".codex/skills", ".agents/skills"),
        global_skill_dirs=("$CODEX_HOME/skills", "~/.codex/skills"),
    ),
    "codex-ide": AgentProfile(
        target="codex-ide",
        display_name="OpenAI Codex IDE Extension",
        aliases=(
            "codex-vscode",
            "codex-vs-code",
            "codex-extension",
            "openai-codex-ide",
            "openai-codex-vscode",
            "openai-chatgpt",
            "chatgpt-vscode",
        ),
        instruction_files=("AGENTS.md",),
        mcp_config_hint=(
            'Use a Codex plugin or IDE MCP config that starts command '
            '"skills-router" with args ["mcp"].'
        ),
        invocation_hint=(
            'If MCP/plugins are unavailable, run '
            '`python -m skills_router.cli chat "<slash request>" --target codex-ide --json` '
            'from this checkout, or `skills-router chat ...` when installed on PATH.'
        ),
        notes=(
            "The IDE extension slash picker lists built-in commands; keep this bridge "
            "in AGENTS.md so the model can route Skills Router requests.",
            "If `/skills-router` is intercepted by the IDE input, send "
            "`skills-router ...` as ordinary chat text.",
            "Prefer JSON output and summarize only the human-facing result.",
        ),
        workspace_skill_dirs=(".codex/skills", ".agents/skills"),
        global_skill_dirs=("$CODEX_HOME/skills", "~/.codex/skills"),
    ),
    "cline": AgentProfile(
        target="cline",
        display_name="Cline",
        aliases=("cline-ai", "cline-vscode"),
        instruction_files=(".clinerules/skills-router.md", "AGENTS.md"),
        mcp_config_hint=(
            'Use Cline MCP settings with command "skills-router" and args ["mcp"].'
        ),
        invocation_hint=(
            'If MCP is unavailable, run '
            '`skills-router chat "<slash request>" --target cline --json`.'
        ),
        notes=(
            "Rules should trigger on /skills-router, install agent tool, "
            "registry, and MCP package requests.",
            "Do not paste large Skills Router JSON into chat unless the human asks for it.",
        ),
        workspace_skill_dirs=(".cline/skills", ".agents/skills"),
        global_skill_dirs=("~/.cline/skills",),
    ),
    "kiro": AgentProfile(
        target="kiro",
        display_name="Kiro",
        aliases=("kiro-ide", "kiro-cli"),
        instruction_files=(".kiro/steering/skills-router.md", "AGENTS.md"),
        mcp_config_hint=(
            'Workspace MCP config can start skills-router with command '
            '"skills-router", args ["mcp"].'
        ),
        invocation_hint=(
            'If MCP is unavailable, run '
            '`skills-router chat "<slash request>" --target kiro --json`.'
        ),
        notes=(
            "Use steering for persistent bridge rules and keep the installed "
            "scope workspace-local by default.",
        ),
        workspace_skill_dirs=(".kiro/skills", ".agents/skills"),
        global_skill_dirs=("~/.kiro/skills",),
    ),
    "claude": AgentProfile(
        target="claude",
        display_name="Claude Code",
        aliases=("claude-code", "anthropic-claude"),
        instruction_files=("CLAUDE.md", ".claude/commands/skills-router.md"),
        mcp_config_hint=(
            'Use Claude Code MCP with command "skills-router", args ["mcp"].'
        ),
        invocation_hint=(
            'If MCP is unavailable, run '
            '`skills-router chat "<slash request>" --target claude --json`.'
        ),
        notes=(
            "The custom command file can forward its arguments through MCP or the chat command.",
        ),
        workspace_skill_dirs=(".claude/skills", ".agents/skills"),
        global_skill_dirs=("$CLAUDE_HOME/skills", "~/.claude/skills"),
    ),
    "github-copilot": AgentProfile(
        target="github-copilot",
        display_name="GitHub Copilot",
        aliases=("copilot", "gh-copilot", "github"),
        instruction_files=(".github/copilot-instructions.md", "AGENTS.md"),
        mcp_config_hint=(
            "For VS Code/Copilot MCP, add a workspace server that runs "
            'command "skills-router", args ["mcp"].'
        ),
        invocation_hint=(
            'If MCP is unavailable, use a terminal task: '
            '`skills-router chat "<slash request>" --target github-copilot --json`.'
        ),
        notes=(
            "Use repository instructions so Copilot Agent mode knows when to "
            "call Skills Router.",
        ),
        workspace_skill_dirs=(".github/skills", ".agents/skills"),
        global_skill_dirs=("~/.github-copilot/skills",),
    ),
    "antigravity": AgentProfile(
        target="antigravity",
        display_name="Google Antigravity",
        aliases=("google-antigravity",),
        instruction_files=(".agent/rules/skills-router.md", "AGENTS.md"),
        mcp_config_hint=(
            'Use Antigravity MCP config with command "skills-router", '
            'args ["mcp"].'
        ),
        invocation_hint=(
            'If MCP is unavailable, run '
            '`skills-router chat "<slash request>" --target antigravity --json`.'
        ),
        notes=(
            "Use the generic Antigravity target when the host does not "
            "distinguish CLI and IDE profiles.",
            "Keep registry/package mutations explicit in Antigravity permissions.",
        ),
        workspace_skill_dirs=(".agent/skills", ".antigravity/skills", ".agents/skills"),
        global_skill_dirs=("$ANTIGRAVITY_HOME/skills", "~/.antigravity/skills"),
    ),
    "antigravity-cli": AgentProfile(
        target="antigravity-cli",
        display_name="Google Antigravity CLI",
        aliases=("google-antigravity-cli",),
        instruction_files=(".agent/rules/skills-router.md", "AGENTS.md"),
        mcp_config_hint=(
            'Use Antigravity MCP config with command "skills-router", '
            'args ["mcp"].'
        ),
        invocation_hint=(
            'If MCP is unavailable, run '
            '`skills-router chat "<slash request>" --target antigravity-cli --json`.'
        ),
        notes=(
            "Keep permissions for Skills Router explicit; registry installs "
            "can mutate local tool state.",
        ),
        workspace_skill_dirs=(".agent/skills", ".antigravity/skills", ".agents/skills"),
        global_skill_dirs=("$ANTIGRAVITY_HOME/skills", "~/.antigravity/skills"),
    ),
    "antigravity-ide": AgentProfile(
        target="antigravity-ide",
        display_name="Google Antigravity IDE",
        aliases=("antigravity-editor", "google-antigravity-ide"),
        instruction_files=(
            ".agent/rules/skills-router.md",
            ".antigravity/rules/skills-router.md",
            "AGENTS.md",
        ),
        mcp_config_hint=(
            'Use Antigravity IDE MCP config with command "skills-router", '
            'args ["mcp"].'
        ),
        invocation_hint=(
            'If MCP is unavailable, run '
            '`skills-router chat "<slash request>" --target antigravity-ide --json`.'
        ),
        notes=(
            "Use IDE workspace rules for persistent /skills-router routing guidance.",
            "Prefer MCP route lookup over embedding generated route tables in the IDE prompt.",
        ),
        workspace_skill_dirs=(".antigravity/skills", ".agent/skills", ".agents/skills"),
        global_skill_dirs=("$ANTIGRAVITY_HOME/skills", "~/.antigravity/skills"),
    ),
    "opencode": AgentProfile(
        target="opencode",
        display_name="OpenCode",
        aliases=("open-code", "opencode-ai"),
        instruction_files=("AGENTS.md", ".opencode/agent/skills-router.md"),
        mcp_config_hint=(
            'In opencode config, add local MCP "skills-router" with command '
            '["skills-router", "mcp"] and enable it only for agents that need package management.'
        ),
        invocation_hint=(
            'If MCP is unavailable, run '
            '`skills-router chat "<slash request>" --target opencode --json`.'
        ),
        notes=(
            "OpenCode benefits from a narrow MCP tool surface instead of many "
            "package-manager tools.",
        ),
        workspace_skill_dirs=(".opencode/skills", ".agents/skills"),
        global_skill_dirs=("~/.opencode/skills",),
    ),
    "hermes-agent": AgentProfile(
        target="hermes-agent",
        display_name="Hermes Agent",
        aliases=("hermes", "nous-hermes"),
        instruction_files=("SOUL.md", "AGENTS.md"),
        mcp_config_hint=(
            "Add an MCP stdio server in Hermes config that runs command "
            '"skills-router" with args ["mcp"].'
        ),
        invocation_hint=(
            'If MCP is unavailable, run '
            '`skills-router chat "<slash request>" --target hermes-agent --json`.'
        ),
        notes=(
            "Prefer a dedicated Hermes profile for package management to "
            "avoid loading broad tools every turn.",
        ),
        workspace_skill_dirs=(".hermes/skills", ".agents/skills"),
        global_skill_dirs=("~/.hermes-agent/skills", "~/.hermes/skills"),
    ),
    "cursor": AgentProfile(
        target="cursor",
        display_name="Cursor",
        aliases=("cursor-ai", "cursor-agent"),
        instruction_files=(".cursor/rules/skills-router.md", "AGENTS.md"),
        mcp_config_hint=(
            'Use Cursor MCP settings with command "skills-router" and args ["mcp"].'
        ),
        invocation_hint=(
            'If MCP is unavailable, run '
            '`skills-router chat "<slash request>" --target cursor --json`.'
        ),
        notes=(
            "Use Cursor rules for persistent bridge guidance and keep route lookup dynamic.",
        ),
        workspace_skill_dirs=(".cursor/skills", ".agents/skills"),
        global_skill_dirs=("~/.cursor/skills",),
    ),
    "windsurf": AgentProfile(
        target="windsurf",
        display_name="Windsurf",
        aliases=("windsurf-ai", "windsurf-agent"),
        instruction_files=(".windsurf/rules/skills-router.md", "AGENTS.md"),
        mcp_config_hint=(
            'Use Windsurf MCP settings with command "skills-router" and args ["mcp"].'
        ),
        invocation_hint=(
            'If MCP is unavailable, run '
            '`skills-router chat "<slash request>" --target windsurf --json`.'
        ),
        notes=(
            "Use Windsurf rules for the static bridge and route tasks through MCP or CLI.",
        ),
        workspace_skill_dirs=(".windsurf/skills", ".agents/skills"),
        global_skill_dirs=("~/.windsurf/skills",),
    ),
}

_ALIASES: dict[str, str] = {}
for _target, _profile in _PROFILES.items():
    _ALIASES[_target] = _target
    for _alias in _profile.aliases:
        _ALIASES[_alias] = _target


def normalize_agent_target(target: str | None) -> str:
    """Normalize a target name or alias to a canonical profile key."""
    if not target:
        return "codex"
    key = target.strip().lower().replace("_", "-")
    try:
        return _ALIASES[key]
    except KeyError as exc:
        known = ", ".join(sorted(_PROFILES))
        raise ValueError(f"Unknown agent target: {target}. Known targets: {known}") from exc


def get_agent_profile(target: str | None = None) -> AgentProfile:
    """Return an agent profile by canonical target or alias."""
    return _PROFILES[normalize_agent_target(target)]


def list_agent_profiles() -> list[AgentProfile]:
    """Return all supported agent profiles in stable display order."""
    return [_PROFILES[key] for key in sorted(_PROFILES)]


def default_all_agent_targets() -> tuple[str, ...]:
    """Return the targets used by one-time all-agent installs."""
    return DEFAULT_ALL_AGENT_TARGETS
