---
name: skills-router
description: Use when the user asks Skills Router to manage AI-agent skills, plugins, routes, or messages starting /skills-router or skills-router.
---

<!-- BEGIN SKILLS ROUTER BRIDGE SKILL -->
# Skills Router Bridge: OpenAI Codex CLI

Trigger: user text starting `/skills-router` or `skills-router` is a
registry/routing request.
Cheapest path:
1. Prefer MCP `run_slash_command` with the full user text.
2. For structured calls, use `refine_routes` or `route_task`;
   never paste route tables.
3. Fallback: `skills-router chat "<request>" --target codex
   --agent-id local-agent --json`.

Examples: `/skills-router install <pkg> for me`, `skills-router install
<pkg> for all agents`, `/skills-router refine`, `skills-router route <task>`.
Scope: default `workspace:local-agent`; `global` or `all agents` uses
global routes. Blank/named refine discovers while comparing visible scopes.
Safety: uninstall removes only Skills Router metadata. Keep
`needs_selection` inactive until the human confirms. Use `--yes` or
`auto_approve` only when the user explicitly accepts risk.
Reply: prefer `human_summary`; otherwise status + next action.
Do not paste raw JSON unless asked.

Setup: AGENTS.md. MCP: Prefer a local stdio MCP server: command "skills-router", args ["mcp"].
CLI: If MCP is unavailable, run `skills-router chat "<slash request>" --target codex --json`.
Notes: Keep the bridge prompt in AGENTS.md or the project instructions Codex reads.; Prefer JSON output and summarize only the human-facing result.
<!-- END SKILLS ROUTER BRIDGE SKILL -->
