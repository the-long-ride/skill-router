"""Render compact bridge prompts for AI-agent hosts."""

from __future__ import annotations

from textwrap import dedent

from skills_router.agent_bridge.profiles import (
    AgentProfile,
    get_agent_profile,
    list_agent_profiles,
)


def render_agent_prompt(
    target: str | None = None,
    *,
    agent_id: str = "<agent-id>",
    detail: str = "compact",
) -> str:
    """Render bridge instructions for one AI-agent target.

    Compact is the default because this text is often pasted into persistent
    agent instructions and paid for on every turn.
    """
    profile = get_agent_profile(target)
    if detail == "full":
        return _render_full_agent_prompt(profile, agent_id=agent_id)
    if detail != "compact":
        raise ValueError("Prompt detail must be 'compact' or 'full'")
    return _render_compact_agent_prompt(profile, agent_id=agent_id)


def _render_compact_agent_prompt(profile: AgentProfile, *, agent_id: str) -> str:
    instruction_files = ", ".join(profile.instruction_files)
    notes = "; ".join(profile.notes)
    return dedent(
        f"""
        # Skills Router Bridge: {profile.display_name}

        Trigger: user text starting `/skills-router` or `skills-router` is a
        registry/routing request.
        Cheapest path:
        1. Prefer MCP `run_slash_command` with the full user text.
        2. For structured calls, use `refine_routes` or `route_task`;
           never paste route tables.
        3. Fallback: `skills-router chat "<request>" --target {profile.target}
           --agent-id {agent_id} --json`.

        Examples: `/skills-router install <pkg> for me`, `skills-router install
        <pkg> for all agents`, `/skills-router refine`, `skills-router route <task>`.
        Scope: default `workspace:{agent_id}`; `global` or `all agents` uses
        global routes. Blank/named refine discovers while comparing visible scopes.
        Safety: uninstall removes only Skills Router metadata. Keep
        `needs_selection` inactive until the human confirms. Use `--yes` or
        `auto_approve` only when the user explicitly accepts risk.
        Reply: prefer `human_summary`; otherwise status + next action.
        Do not paste raw JSON unless asked.

        Setup: {instruction_files}. MCP: {profile.mcp_config_hint}
        CLI: {profile.invocation_hint}
        Notes: {notes}
        """
    ).strip()


def _render_full_agent_prompt(profile: AgentProfile, *, agent_id: str) -> str:
    instruction_files = ", ".join(profile.instruction_files)
    notes = "\n".join(f"- {note}" for note in profile.notes)
    return dedent(
        f"""
        # Skills Router Bridge: {profile.display_name}

        You can manage this agent's tool registry through Skills Router. Treat any user
        message that starts with `/skills-router` or `skills-router` as a
        registry-management request. Some IDE chat inputs reserve unknown slash
        commands; when that happens, route the plain-text `skills-router ...`
        form the same way.

        Preferred execution order:
        1. If MCP tools are available, call `run_slash_command` with the user's full text.
           Use `refine_routes` directly when the host already has structured skillset names.
           Use `route_task` when the host needs the current skill route for a task.
        2. Otherwise run:
           `skills-router chat "<request>" --target {profile.target} --agent-id {agent_id} --json`.
        3. Use strict CLI commands only as a fallback.

        Parsing rules:
        - `/skills-router install <pkg> for me` means install `<pkg>` for `workspace:{agent_id}`.
        - `/skills-router install <pkg> for all agents` means install once at
          `global` scope and mark the route for every configured all-agent target.
        - `/skills-router uninstall <tool_id>` means remove Skills Router-owned
          Brain Index, lockfile, dependency, and routing metadata for that skill,
          then re-index remaining skills for routing conflicts.
        - `/skills-router index` means re-index installed skills/plugins, detect
          routing conflicts, include a recommendation for every comparison, and
          ask the human to choose routing when review is needed.
        - `/skills-router refine` means discover host/global AI-agent skills,
          refresh the route index, and leave newly discovered external routes
          inactive until the human confirms activation.
        - `/skills-router refine <name> <name>` means refine only those named
          skillsets while comparing them against the visible installed route set.
        - `/skills-router route <task>` means look up the current route in
          `skills-router.json`; do not activate a `needs_selection` route without
          human confirmation.
        - `skillset`, `skill`, or `plugin` words describe the package type.
        - `partial`, `only needed`, or `selected skillset` means install the
          full package but leave routes inactive until the human chooses routes.
        - `global` or `globally` means scope `global`; otherwise use workspace scope.
        - `dry run`, `preview`, or `check first` means evaluate without installing.
        - Do not pass filler words such as `for me` or `please` to strict CLI commands.

        Safety rules:
        - Skills Router uninstall does not delete package resources. If the human
          wants package files, environments, or host plugins removed, tell them
          to use the host package manager too.
        - Do not use `--yes` or `auto_approve` unless the user explicitly says to auto-approve,
          force install, or accept risk.
        - If Skills Router returns `HARD_REJECT`, `CANCELLED`, or `DRY_RUN_CANCELLED`,
          stop and tell the human the short reason.
        - For trust warnings, dependency conflicts, duplicate tools, or unknown LLM behavior,
          ask the human for the decision instead of inventing approval.

        Response contract:
        - Keep the human-facing answer short: status, tool id/package, risk state, next action.
        - Mention routing status if Skills Router returns `skills_routing`.
        - For index/conflict output, show each comparison with Skills Router's
          recommendation and then ask the human for the route choice.
        - For refine output, mention discovered external/global skill records
          and ask before activating any `needs_selection` route.
        - For uninstall output with `route_reconciliation.requires_human_decision`,
          show the included recommendation and ask the human for the new route choice.
        - Do not paste raw JSON unless the human asks for it.
        - If a command fails, include only the actionable error and the command surface used.

        Target setup:
        - Instruction location: {instruction_files}
        - MCP hint: {profile.mcp_config_hint}
        - CLI fallback: {profile.invocation_hint}

        Target notes:
        {notes}
        """
    ).strip()


def render_supported_targets() -> list[dict[str, object]]:
    """Return JSON-friendly target metadata for CLI and MCP callers."""
    return [
        {
            "target": profile.target,
            "display_name": profile.display_name,
            "aliases": list(profile.aliases),
            "instruction_files": list(profile.instruction_files),
            "mcp_config_hint": profile.mcp_config_hint,
            "invocation_hint": profile.invocation_hint,
            "notes": list(profile.notes),
        }
        for profile in list_agent_profiles()
    ]
