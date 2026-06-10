# Skills Router Integration Guideline

This guide is for terminal, IDE, desktop, and web AI-agent hosts that want to
call Skills Router safely.

Skills Router manages AI-agent skill/plugin metadata and routing. It does not own
package files. Treat it as the review, index, route, and audit layer that sits
between a human, a host agent, and whatever package manager actually installed
the skill.

## Operating Contract

Use these rules in every integration:

- Skills Router reviews/registers complete AI-agent skill/plugin packages.
- For npm/GitHub links without a manifest, Skills Router may infer a draft
  manifest from bounded source evidence; treat inferred manifests as untrusted
  until the normal review pipeline completes.
- A partial request means "install the full package, but activate only selected
  routes after human choice."
- Skills Router writes its own Brain Index, dependency graph, lockfile, audit log,
  and `skills-router.json`.
- Package files, repositories, virtual environments, IDE extensions, and host
  plugin resources stay owned by their host package manager.
- `uninstall` removes Skills Router-owned metadata/routing only. It does not
  delete package resources.
- `index` reconciles routes for packages already known to Skills Router.
- `refine` discovers externally installed workspace/global skills, imports
  metadata, and reconciles routes.
- Newly discovered external routes stay `needs_selection` until the human
  confirms activation.
- Every conflict or stale route shown to a human must include Skills Router's
  recommendation.
- Do not use `--yes`, `auto_approve`, or approval policies unless the human
  explicitly accepts risk.

## Preferred Host Flow

When the human writes a slash command, prefer this order:

1. Call MCP `run_slash_command` with the full human text.
2. If MCP is unavailable, run:
   `skills-router chat "<request>" --target <target> --agent-id <agent-id> --json`.
3. Use strict CLI commands only when the host already has structured arguments.

Examples:

```powershell
skills-router chat "/skills-router install writer-pack for me" --target codex --agent-id codex-local --json
skills-router chat "/skills-router analyze https://github.com/owner/repo" --target codex --agent-id codex-local --json
skills-router chat "/skills-router index" --target codex --agent-id codex-local --json
skills-router chat "/skills-router refine writer-pack engram" --target codex --agent-id codex-local --json
skills-router chat "/skills-router route draft article" --target codex --agent-id codex-local --json
skills-router chat "/skills-router status" --target codex --agent-id codex-local --json
```

For `/skills-router refine` in chat, workspace-discovered routes default to
`workspace:<agent-id>` while route comparison still spans all visible scopes.

## Local Setup

Install from this checkout into the current Python environment:

```powershell
cd F:\my-repos\skill-forge
py -m pip install -e .
```

Then check the command and imported module path:

```powershell
skills-router --help
py -c "import skills_router; print(skills_router.__file__)"
```

Recommended safer setup is a local virtual environment:

```powershell
cd F:\my-repos\skill-forge
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -e .
skills-router --help
```

Optional real embedding support can be installed in either environment:

```powershell
python -m pip install -e ".[ml]"
```

The ML extra is optional. Without it, Skills Router uses deterministic fallback
embeddings for local/test workflows.

For a local Node wrapper command:

```powershell
cd skills-router-npx
npm link
cd ..
skills-router --help
```

For local linked development, the wrapper uses this repository's `src/`
directory as `PYTHONPATH`.

Generate an agent-host setup kit:

```powershell
skills-router connect --target codex --json
skills-router connect --target codex --write-instructions
skills-router connect --target codex --from-source --json
```

`connect` returns the MCP config, bridge prompt, target instruction files, and
CLI fallback command. `--write-instructions` writes a managed bridge prompt
block to the first target instruction file in the current workspace.

## CLI Commands

### Connect

Use connect when a human wants to attach Skills Router to an AI-agent host:

```powershell
skills-router connect --target codex --json
skills-router connect --target cursor --write-instructions
skills-router connect --target cursor --write-instructions --dry-run
skills-router connect --target codex --from-source --json
```

Expected behavior:

- Return a host-ready MCP config for `skills-router mcp`.
- Return the compact bridge prompt and target instruction file paths.
- In `--from-source` mode, use Python plus `PYTHONPATH` to run this checkout.
- Only write instruction files when `--write-instructions` is present.
- `--dry-run` previews the instruction-file action without writing files.

### Analyze

Use analyze when the human pastes an npm/GitHub link and wants to see the
inferred Skills Router manifest before installing:

```powershell
skills-router analyze https://github.com/owner/repo --json
skills-router analyze https://www.npmjs.com/package/@scope/writer-pack --json
skills-router analyze npm:@scope/writer-pack@1.2.3 --json
```

Expected behavior:

- Fetch bounded metadata/docs only; never execute package code.
- Treat README/package docs as untrusted evidence, not agent instructions.
- Return an evidence packet, warnings, confidence, and inferred manifest in JSON.

### Install

Use install when the human wants Skills Router to review and register a package or
manifest:

```powershell
skills-router install examples/sample_manifests/weather_tool.json --scope global
skills-router install writer-pack --package-type skillset --scope workspace:codex-local
skills-router install https://github.com/owner/repo --infer --dry-run --explain --json
skills-router install https://www.npmjs.com/package/@scope/writer-pack --infer --json
skills-router install writer-pack --package-type skillset --routing-mode selective_routes --scope workspace:codex-local --json
skills-router install writer-pack --package-type skillset --all-agents --json
skills-router install writer-pack --package-type skillset --all-agents --agent-target codex,cursor --json
skills-router install writer-pack --dry-run --explain --json
```

Important install flags:

- `--scope global|workspace:<id>` controls route visibility and comparison.
- `--all-agents` stores one global install and records the default all-agent
  target set.
- `--agent-target <target>` narrows `--all-agents` routing to specific agent
  targets; repeat or comma-separate values.
- `--package-type auto|skillset|plugin|tool` controls generated route shape.
- `--routing-mode full_package|selective_routes` activates routes or leaves
  them for human selection.
- `--dry-run` evaluates without writing state.
- `--infer` maps a supported npm/GitHub source link to a conservative manifest
  before the normal review pipeline runs.
- `--decision-policy approve|cancel|prompt` and `--yes` must be reserved for
  explicit human approval.

### Index

Use index after a package was updated/removed by its host package manager or
when routes look stale:

```powershell
skills-router index --json
skills-router index --scope workspace:codex-local --json
skills-router index --dry-run --json
```

Expected behavior:

- Refresh vectors for installed package records.
- Rebuild route metadata.
- Mark missing package routes as `missing_from_index`.
- Compare visible packages for overlap/conflict.
- Return recommendations and `requires_human_decision` when review is needed.
- With `--dry-run`, do not persist refreshed vectors or route metadata.

Do not translate stale routes into package deletion. Stale means Skills Router
cannot currently see that package in its Brain Index.

### Refine

Use refine when skills may exist outside local Skills Router workspace state:

```powershell
skills-router refine --json
skills-router refine writer-pack engram --json
skills-router refine --workspace-scope workspace:codex-local --json
skills-router refine --no-discovery --json
skills-router refine --dry-run --json
```

Expected behavior:

- Discover workspace skills from `.agents/skills` and host-specific skill dirs.
- Discover global skills from `$CODEX_HOME/skills`, `~/.codex/skills`, and
  host-specific global skill dirs.
- Include nested skill folders such as `.system/<skill>/SKILL.md`.
- Import metadata only; host package resources remain untouched.
- Keep newly discovered external routes at `needs_selection`.
- Compare overlaps and return recommendations.
- With `--dry-run`, discover and compare through a scratch store without writing
  Brain Index or routing changes.

Use `--scope` to limit comparison visibility. Use `--workspace-scope` to choose
the scope assigned to workspace-discovered skills.

### Route

Use route when the host needs the current route for a task:

```powershell
skills-router route "draft article about release notes" --scope workspace:codex-local --json
skills-router route "draft article about release notes" --scope workspace:codex-local --target codex --json
skills-router route "draft article" --include-inactive --json
```

`route` reads `skills-router.json`, ranks matching rules by score then priority,
filters target-specific routes when `--target` is provided, and returns `OK`,
`REVIEW_NEEDED`, or `NO_ROUTE`. Do not use a `needs_selection` route until the
human confirms activation.

### Status

Use status when the host or human needs to see where Skills Router stores
metadata and which host skill directories are configured:

```powershell
skills-router status --json
```

Expected behavior:

- Report Skills Router-owned metadata paths, including Brain Index,
  dependency graph, lockfile, audit log, registry cache, and routing file.
- Report configured workspace and global host skill directories with existence
  flags.
- Return counts for indexed tools, dependency entries, routing packages, and
  active routes.
- Clarify that package resources stay owned by host package managers.

### Uninstall

Use uninstall when the human wants Skills Router to stop routing to a skill:

```powershell
skills-router uninstall writer-pack --json
skills-router uninstall writer-pack --dry-run --json
```

Expected behavior:

- Remove the skill from the Brain Index.
- Remove its dependency graph contribution.
- Remove its lockfile entry and routing rules.
- Write an audit event.
- Re-index remaining skills for conflicts.
- Leave package resources untouched.
- With `--dry-run`, return `would_remove` and route-reconciliation output without
  deleting Brain Index, lockfile, dependency, routing, or audit state.

If the human also wants files, environments, IDE extensions, or host plugins
removed, use the host package manager too, then run `skills-router index --json`.

## MCP Server

Start the stdio JSON-RPC server:

```powershell
skills-router mcp
```

For most hosts, generate the config instead of hand-writing it:

```powershell
skills-router connect --target cursor --json
```

Tool surface:

- `get_agent_prompt`
- `get_router_status`
- `parse_slash_command`
- `run_slash_command`
- `analyze_package_source`
- `install_tool`
- `uninstall_tool`
- `index_routes`
- `refine_routes`
- `route_task`
- `list_tools`
- `inspect_tool`
- `watch_once`

Write-capable MCP tools accept `dry_run: true`: `install_tool`,
`uninstall_tool`, `index_routes`, `refine_routes`, and `watch_once`.
Dry-run watch checks do not save watch state, update trust scores, or send
notifications.

MCP responses keep `content[0].text` compact for low-token agent replies and
put the full object in `structuredContent`.

Minimal MCP config:

```json
{
  "mcpServers": {
    "skills-router": {
      "command": "skills-router",
      "args": ["mcp"]
    }
  }
}
```

OpenCode-style config:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "skills-router": {
      "type": "local",
      "command": ["skills-router", "mcp"],
      "enabled": true
    }
  }
}
```

## Target Prompts

Render compact bridge instructions for a host:

```powershell
skills-router prompt --list
skills-router prompt --target codex
skills-router prompt --target cline
skills-router prompt --target kiro
skills-router prompt --target claude
skills-router prompt --target github-copilot
skills-router prompt --target antigravity
skills-router prompt --target antigravity-cli
skills-router prompt --target antigravity-ide
skills-router prompt --target opencode
skills-router prompt --target hermes-agent
skills-router prompt --target cursor
skills-router prompt --target windsurf
skills-router prompt --target codex --detail full
```

Use the compact default in persistent agent instructions. Use `--detail full`
only for debugging or documentation; it is intentionally larger.

Supported targets:

| Target | Suggested instruction location | Preferred call |
| :--- | :--- | :--- |
| `codex` | `AGENTS.md` | MCP `run_slash_command` |
| `cline` | `.clinerules/skills-router.md`, `AGENTS.md` | MCP `run_slash_command` |
| `kiro` | `.kiro/steering/skills-router.md`, `AGENTS.md` | MCP `run_slash_command` |
| `claude` | `CLAUDE.md`, `.claude/commands/skills-router.md` | MCP `run_slash_command` |
| `github-copilot` | `.github/copilot-instructions.md`, `AGENTS.md` | MCP `run_slash_command` |
| `antigravity` | `.agent/rules/skills-router.md`, `AGENTS.md` | MCP `run_slash_command` |
| `antigravity-cli` | `.agent/rules/skills-router.md`, `AGENTS.md` | MCP `run_slash_command` |
| `antigravity-ide` | `.agent/rules/skills-router.md`, `.antigravity/rules/skills-router.md`, `AGENTS.md` | MCP `run_slash_command` |
| `opencode` | `AGENTS.md`, `.opencode/agent/skills-router.md` | MCP `run_slash_command` |
| `hermes-agent` | `SOUL.md`, `AGENTS.md` | MCP `run_slash_command` |
| `cursor` | `.cursor/rules/skills-router.md`, `AGENTS.md` | MCP `run_slash_command` |
| `windsurf` | `.windsurf/rules/skills-router.md`, `AGENTS.md` | MCP `run_slash_command` |

The rendered prompt tells the host to:

- treat `/skills-router` as a skill/routing request,
- prefer MCP `run_slash_command`,
- fall back to `skills-router chat`,
- keep install scope workspace-local unless the human says global,
- treat uninstall as metadata/routing removal,
- treat partial requests as route selection,
- run `index` after host package updates/removals,
- run `refine` after external skill installs,
- use `route_task` or `skills-router route` instead of static route injection,
- keep human replies short.

## Process Tool Config

For hosts that cannot use MCP, expose the chat bridge:

```json
{
  "name": "skills_router_chat",
  "command": "skills-router",
  "args": [
    "chat",
    "<full-user-slash-request>",
    "--target",
    "<target>",
    "--agent-id",
    "<agent-id>",
    "--json"
  ]
}
```

Optional structured tools:

```json
{
  "name": "skills_router_index_routes",
  "command": "skills-router",
  "args": ["index", "--json"]
}
```

```json
{
  "name": "skills_router_refine_routes",
  "command": "skills-router",
  "args": ["refine", "--json"]
}
```

```json
{
  "name": "skills_router_route_task",
  "command": "skills-router",
  "args": ["route", "<task>", "--target", "<target>", "--json"]
}
```

## Python Integration

Python-based hosts can call Skills Router directly:

```python
from skills_router.agent_bridge.indexer import (
    index_installed_skillsets,
    refine_installed_skillsets,
)
from skills_router.agent_bridge.routing import route_task
from skills_router.agent_bridge.uninstaller import uninstall_skill_metadata
from skills_router.config import SkillsRouterConfig
from skills_router.orchestrator import SkillsRouterOrchestrator
from skills_router.storage.memory_store import MemoryBrainIndexStore


config = SkillsRouterConfig()
store = MemoryBrainIndexStore(
    brain_index_path=config.brain_index_path,
    dep_graph_path=config.dep_graph_path,
)
orchestrator = SkillsRouterOrchestrator(config=config, store=store)

index_result = index_installed_skillsets(config, store, scope="workspace:codex-local")
refine_result = refine_installed_skillsets(
    config,
    store,
    scope=None,
    workspace_scope="workspace:codex-local",
)
route_result = route_task(
    config,
    "draft article",
    scope="workspace:codex-local",
    agent_target="codex",
)
uninstall_result = uninstall_skill_metadata(config, store, "writer-pack")
```

Use `SkillsRouterOrchestrator(..., decision_callback=callback)` for installs that
need human review decisions.

## Registry Watch

Registry Watch is optional. It checks indexed package records for state drift
and trust degradation:

```powershell
skills-router watch --once --admin-channel local-admin --json
skills-router watch --interval 300 --admin-channel local-admin --metrics-port 9108
```

When `--metrics-port` is set, metrics are available at:

```text
http://127.0.0.1:9108/metrics
```

## Configuration

Save overrides in `~/.skills-router/config.json`:

```json
{
  "storage_backend": "memory",
  "workspace_root": "/path/to/workspace",
  "workspace_skill_dirs": [".agents/skills", ".codex/skills"],
  "global_skill_dirs": ["$CODEX_HOME/skills", "~/.codex/skills"],
  "registry_base_url": "https://registry.skillsrouter.ai/packages",
  "pgvector_dsn": "postgresql://user:pass@localhost:5432/skills_router"
}
```

For larger indexes:

```powershell
pip install -e ".[pgvector]"
```

## NPM Wrapper

The Node wrapper is a delivery layer for environments that prefer `npx`; it
does not change Skills Router's ownership model.

```bash
cd skills-router-npx
npm login
npm publish --access public
```

Users can then run:

```bash
npx skills-router chat "/skills-router refine" --target codex --agent-id codex-local --json
npx skills-router route "draft article" --scope workspace:codex-local --json
```
