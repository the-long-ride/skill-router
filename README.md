# Skills Router

[![Version](https://img.shields.io/badge/version-v0.0.1-orange.svg)](CHANGELOG.md)
[![License](https://img.shields.io/badge/license-GPLv3-blue.svg)](LICENSE)
[![Author](https://img.shields.io/badge/author-the--long--ride-green.svg)](https://github.com/the-long-ride)
[![Build Status](https://img.shields.io/badge/tests-199%20passed-success.svg)](tests/)

[English](README.md) | [Español](readme/es.md) | [简体中文](readme/zh.md) | [日本語](readme/ja.md) | [Deutsch](readme/de.md) | [Français](readme/fr.md)

`skills-router` is the CLI command and PyPI package name. The npm wrapper package
is [`@the-long-ride/skills-router`](https://www.npmjs.com/package/@the-long-ride/skills-router).

**Skills Router is an AI-agent skillset manager.** It reviews, registers,
discovers, indexes, compares, and routes AI-agent skills/plugins so a host agent
can use the right capability without silently taking over package resources.

Skills Router is not a general package manager. It owns metadata, decisions, audit
logs, and routing state. Package files, virtual environments, IDE extensions,
and host-agent skill directories stay owned by the tool that installed them.

## Why skills-router?

AI-agent skills are useful, but they get scattered across CLIs, IDEs, MCP
servers, global folders, workspace folders, and host-specific package managers.
That makes it hard to answer simple questions: which skill should this agent
use, who approved it, where is it active, and what happens if another package
overlaps with it?

`skills-router` gives agents one shared control plane for that problem. It lets
you install or discover skills once, review them through trust and behavior
checks, and route each agent to the right capability without copying package
files or stuffing huge route tables into prompts. The package manager still
owns package resources; Skills Router owns the decisions, metadata, audit
trail, and routing layer.

## What It Does

- Reviews complete skill/plugin manifests through trust, dependency, semantic,
  capability, and health checks.
- Analyzes npm and GitHub source links, builds evidence packets, and infers
  conservative Skills Router manifests when a repo does not provide one.
- Stores approved package metadata in a Brain Index.
- Writes `skills-router.json` rules that host agents can query through MCP or
  CLI.
- Installs a skill once for all configured AI-agent hosts with `--all-agents`
  or `/skills-router install <package> for all agents`.
- Supports narrowed all-agent installs with target lists such as
  `--agent-target codex,cursor`.
- Enforces target-aware routing when agents call `route_task` or
  `skills-router route --target <agent>`.
- Shows current metadata, routing, and configured skill-folder paths with
  `skills-router status`.
- Supports the default all-agent host set: `antigravity`, `antigravity-cli`,
  `antigravity-ide`, `codex`, `claude`, `hermes-agent`, `opencode`, `cline`,
  `cursor`, and `windsurf`.
- Treats partial installs as selective route activation, not partial package
  extraction.
- Removes Skills Router-owned metadata/routing on uninstall, then re-indexes the
  remaining route surface.
- Reconciles routes with `/skills-router index`.
- Discovers externally installed workspace/global skills with
  `/skills-router refine`.
- Scans shared and host-specific workspace/global skill directories, including
  nested system skill folders.
- Keeps newly discovered external routes at `needs_selection` until the human
  confirms activation.
- Publishes release descriptions from the matching `CHANGELOG.md` section on
  tag pushes, with package links appended by CI.

## What It Does Not Do

- It does not delete package-owned files, repositories, virtual environments, or
  IDE/plugin resources.
- It does not replace `pip`, `npm`, IDE extension managers, or host-agent plugin
  managers.
- It does not auto-approve trust warnings, dependency conflicts, duplicate
  routes, or unknown behavior unless the human explicitly says to approve risk.
- It does not inject large route tables into agent prompts. Agents should query
  Skills Router dynamically.

## Architecture

```mermaid
flowchart LR
    Human[Human] --> Host[AI agent host]
    Host --> Bridge[Agent Bridge]
    Host --> MCP[MCP stdio server]
    Host --> CLI[CLI]

    Bridge --> Parser[Slash parser]
    Parser --> Executor[Bridge executor]
    MCP --> Executor
    CLI --> Commands[Strict commands]

    Commands --> Install[Install pipeline]
    Executor --> Install
    Commands --> Reconcile[Index and refine]
    Executor --> Reconcile
    Commands --> Lookup[Route lookup]
    Executor --> Lookup
    Commands --> Remove[Uninstall metadata]
    Executor --> Remove

    Install --> Resolver[Registry resolver]
    Resolver --> Trust[Trust gate]
    Trust --> Deps[Dependency resolver]
    Deps --> Semantic[Semantic evaluator]
    Semantic --> Capability[Capability checker]
    Capability --> WG[Workspace/global prompt]
    WG --> Health[Health check]

    Reconcile --> Discovery[Skill discovery]
    Discovery --> SkillDirs[Workspace/global skill dirs]
    Discovery --> GlobalIndex[Global Skills Router index]
    Reconcile --> Conflict[Conflict detector]

    Install --> Store[(Brain Index)]
    Reconcile --> Store
    Conflict --> Routes[(skills-router.json)]
    Lookup --> Routes
    Remove --> Store
    Remove --> Routes
    Remove --> Lock
    Remove --> Audit
    Install --> Lock[(Lockfile)]
    Install --> Audit[(Audit log)]
```

## Core Workflow

```mermaid
flowchart TD
    A[Human asks an AI agent] --> B{Request shape}
    B -->|/skills-router install pkg for me| C[Parse slash request]
    B -->|MCP structured call| D[Call MCP tool]
    B -->|Strict CLI| E[Run command]

    C --> F[Resolve package or manifest]
    D --> F
    E --> F
    F --> G[Trust and dependency checks]
    G -->|Hard reject| H[Stop and summarize]
    G -->|Review needed| I[Ask human]
    I -->|Cancel| H
    I -->|Approve| J[Semantic and capability comparison]
    G -->|Safe| J
    J -->|Overlap| K[Recommend route choice]
    K --> L[Human selects routing]
    J -->|No conflict| M[Persist metadata]
    L --> M
    M --> N[Write routing and lockfile]
    N --> O[Health check]
    O -->|Fail| P[Clean Skills Router state]
    O -->|Pass| Q[Audit and short summary]

    R[Host package manager changes files] --> S["/skills-router index"]
    T[External skill appears] --> U["/skills-router refine"]
    S --> V[Refresh routes and mark stale]
    U --> W[Discover and import metadata]
    V --> X[Recommend route decisions]
    W --> X
```

## Install

```bash
# Core local install
pip install -e .

# Optional real embedding support
pip install -e ".[ml]"

# Optional pgvector backend
pip install -e ".[pgvector]"

# Run through npm/npx
npx @the-long-ride/skills-router --help
```

The default storage backend is JSON-backed local memory under
`~/.skills-router`. A local Node wrapper is available in `skills-router-npx/` for
`npx` and IDE workflows; see [GUIDELINE.md](GUIDELINE.md).

## Quick Start

```bash
# Review and register a local manifest
skills-router install examples/sample_manifests/weather_tool.json --scope global

# Review and register by registry package name
skills-router install writer-pack --package-type skillset --scope workspace:codex-local

# Analyze arbitrary source links without installing
skills-router analyze https://github.com/owner/repo --json
skills-router analyze https://www.npmjs.com/package/@scope/writer-pack --json

# Infer a manifest from a source link, then run the normal review pipeline
skills-router install https://github.com/owner/repo --infer --dry-run --explain --json
skills-router install https://www.npmjs.com/package/@scope/writer-pack --infer --json

# Install once and make routes visible to all configured AI-agent hosts
skills-router install writer-pack --package-type skillset --all-agents --json

# Install once but expose routes only to selected agent hosts
skills-router install writer-pack --package-type skillset --all-agents --agent-target codex,cursor --json

# Install the full package but leave routes inactive until selection
skills-router install writer-pack --package-type skillset --routing-mode selective_routes --scope workspace:codex-local --json

# Preview review decisions without writing state
skills-router install writer-pack --dry-run --explain --json

# Remove Skills Router metadata/routing only
skills-router uninstall writer-pack --json
skills-router uninstall writer-pack --dry-run --json

# Reconcile already indexed packages and routes
skills-router index --json
skills-router index --dry-run --json

# Discover workspace/global host-agent skills and refine routes
skills-router refine --json
skills-router refine writer-pack engram --json
skills-router refine --workspace-scope workspace:codex-local --json
skills-router refine --dry-run --json

# Ask Skills Router which route matches a task for the current host
skills-router route "draft article about release notes" --scope workspace:codex-local --target codex --json

# Show metadata paths, configured skill paths, and routing counts
skills-router status --json

# Let an AI-agent host execute a human slash request
skills-router chat "/skills-router install writer-pack for me" --target codex --agent-id codex-local --json
skills-router chat "/skills-router install writer-pack for all installed agents" --target codex --agent-id codex-local --json
skills-router chat "/skills-router refine writer-pack engram" --target codex --agent-id codex-local --json

# Expose Skills Router through stdio JSON-RPC
skills-router mcp

# Generate one-command AI-agent setup kit
skills-router connect --target codex --json
skills-router connect --target codex --write-instructions
skills-router connect --target codex --write-instructions --dry-run

# Render only bridge instructions for a host
skills-router prompt --target codex
skills-router prompt --list
```

## Command Surface

| Command | Purpose |
| :--- | :--- |
| `analyze <npm-or-github-source>` | Read bounded source evidence and infer a reviewable Skills Router manifest without installing. |
| `install <manifest-or-package-or-source>` | Resolve or infer, review, register, and route a package. |
| `index` | Rebuild indexed vectors/routes and detect conflicts or stale routes. |
| `refine [skillset ...]` | Discover external skills, import metadata, and reconcile routes. |
| `route <task>` | Query active or review-needed routes for a task. |
| `uninstall <tool_id>` | Remove Skills Router-owned metadata/routing only. |
| `list` | List indexed tools. |
| `status` | Show metadata paths, configured skill paths, and route counts. |
| `inspect <tool_id>` | Print one Brain Index entry. |
| `audit` | Query audit events. |
| `watch` | Run Registry Watch once or as a daemon. |
| `connect` | Render MCP config plus bridge instructions, and optionally write them. |
| `prompt` | Render host-specific bridge instructions. |
| `chat` | Parse and execute chat-shaped `/skills-router` requests. |
| `mcp` | Run the local stdio JSON-RPC tool server. |

## Dry Run

Every write-capable command supports a no-write path:

```bash
skills-router install writer-pack --dry-run --json
skills-router uninstall writer-pack --dry-run --json
skills-router index --dry-run --json
skills-router refine --dry-run --json
skills-router watch --once --dry-run --json
skills-router connect --target codex --write-instructions --dry-run --json
```

Dry-run responses include `dry_run: true`. For MCP, pass `dry_run: true` to
write-capable tools such as `install_tool`, `uninstall_tool`, `index_routes`,
`refine_routes`, and `watch_once`.

## One-Time All-Agent Installs

All-agent installs are a core workflow:

```bash
skills-router install writer-pack --package-type skillset --all-agents --json
```

The package is still registered once in Skills Router. The generated
routes are global, and each configured host reaches them through MCP or the CLI
bridge. Skills Router owns metadata and routing only; package resources
remain owned by the host package manager or skill installer.

Default all-agent targets:

```text
antigravity, antigravity-cli, antigravity-ide, codex, claude,
hermes-agent, opencode, cline, cursor, windsurf
```

Use `--agent-target` when a skill should apply to only part of that set:

```bash
skills-router install writer-pack \
  --package-type skillset \
  --all-agents \
  --agent-target codex,cursor \
  --json
```

When a target list is stored, route lookup respects it only when the caller
identifies the current host:

```bash
skills-router route "draft release notes" --target codex --json
skills-router route "draft release notes" --target cursor --json
```

For chat-shaped requests, agents can use:

```text
/skills-router install <package> for all installed agents
```

## Routing Model

Skills Router separates package presence from agent activation:

- **Package presence:** the host package manager installs or updates the full
  package.
- **Brain Index:** Skills Router stores manifest, trust, dependency, vector,
  behavior, and scope metadata.
- **Routing:** Skills Router writes `skills-router.json` packages and rules.
- **Selection:** route conflicts and externally discovered skills use
  `needs_selection` until the human confirms activation.
- **Lookup:** agents call MCP `route_task` or `skills-router route` with their
  target instead of reading route files directly.
- **Stale routes:** `index` marks missing packages `missing_from_index`; it does
  not delete package files.

## Refine And Discovery

`skills-router refine` closes the gap where a human installs skills outside the
workspace, for example through `npx`, a host-agent skill installer, or a global
Codex skill directory.

Discovery sources:

- Workspace skill dirs: `.agents/skills` plus host-specific dirs such as
  `.codex/skills`, `.claude/skills`, `.cline/skills`, `.cursor/skills`,
  `.windsurf/skills`, `.opencode/skills`, `.agent/skills`,
  `.antigravity/skills`, `.hermes/skills`, and `.kiro/skills`
- Global skill dirs: `$CODEX_HOME/skills`, `~/.codex/skills`, and the
  matching host-specific global skill dirs
- Nested skill folders, including `.system/.../SKILL.md`
- Global Skills Router state from `global_data_dir`

Blank refine discovers all visible installed skills. Named refine discovers and
reports only matching skillsets while comparing them against the visible route
surface. Chat-shaped `/skills-router refine` assigns workspace-discovered routes
to `workspace:<agent-id>` while still comparing against all visible scopes.

## Slash Commands For Agents

The Agent Bridge accepts natural human requests and turns them into strict
operations:

```text
/skills-router install <package> for me
/skills-router install <package> for all agents
/skills-router install <package> globally dry run
/skills-router analyze <npm-or-github-link>
/skills-router install <npm-or-github-link> for me
/skills-router install <package> skillset only needed skills for me
/skills-router uninstall <tool_id>
/skills-router index
/skills-router refine
/skills-router refine <skillset> <skillset>
/skills-router route <task>
/skills-router list
/skills-router status
/skills-router inspect <tool_id>
/skills-router audit --tool <tool_id>
/skills-router watch --once
```

The bridge defaults install scope to `workspace:<agent-id>` unless the human
says global. `for all agents` means one global install for the default
all-agent target set; custom `--agent-target` lists are enforced by
target-aware route lookup. The parser removes filler words such as `for me` and
returns `human_summary` for short agent replies.

## MCP Tool Surface

`skills-router mcp` exposes:

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

Use `run_slash_command` for human chat text. Use the structured tools only when
the host already has clean arguments. MCP `content` text is intentionally
compact; full machine-readable data stays in `structuredContent`.

Structured MCP install calls can pass `all_agents: true` and optional
`target_agents`. Structured route calls can pass `target` so stored target
lists are enforced for the calling host.

## Supported Agent Hosts

| Target | Instruction locations |
| :--- | :--- |
| `antigravity` | `.agent/rules/skills-router.md`, `AGENTS.md` |
| `antigravity-cli` | `.agent/rules/skills-router.md`, `AGENTS.md` |
| `antigravity-ide` | `.agent/rules/skills-router.md`, `.antigravity/rules/skills-router.md`, `AGENTS.md` |
| `codex` | `AGENTS.md` |
| `cline` | `.clinerules/skills-router.md`, `AGENTS.md` |
| `cursor` | `.cursor/rules/skills-router.md`, `AGENTS.md` |
| `kiro` | `.kiro/steering/skills-router.md`, `AGENTS.md` |
| `claude` | `CLAUDE.md`, `.claude/commands/skills-router.md` |
| `github-copilot` | `.github/copilot-instructions.md`, `AGENTS.md` |
| `opencode` | `AGENTS.md`, `.opencode/agent/skills-router.md` |
| `hermes-agent` | `SOUL.md`, `AGENTS.md` |
| `windsurf` | `.windsurf/rules/skills-router.md`, `AGENTS.md` |

Generate a setup kit for an agent host with:

```bash
skills-router connect --target codex --json
skills-router connect --target cursor --write-instructions
skills-router connect --target codex --from-source --json
```

`connect` returns the MCP server config, target instruction file paths, bridge
prompt, and CLI fallback command. `--write-instructions` writes a managed bridge
prompt block to the target's first workspace instruction file.

Render only target-specific bridge text with:

```bash
skills-router prompt --target codex
skills-router prompt --target cursor
skills-router prompt --target windsurf
skills-router prompt --target codex --detail full
```

The default prompt is compact so persistent agent instructions cost fewer
tokens. Use `--detail full` only when generating docs or debugging an
integration.

## Configuration

`~/.skills-router/config.json` can override `SkillsRouterConfig` fields such as:

```json
{
  "storage_backend": "memory",
  "workspace_root": "/path/to/workspace",
  "workspace_skill_dirs": [".agents/skills", ".codex/skills", ".cursor/skills"],
  "global_skill_dirs": ["$CODEX_HOME/skills", "~/.codex/skills", "~/.cursor/skills"],
  "pgvector_dsn": "postgresql://user:pass@localhost:5432/skills_router"
}
```

## Release Automation

The repository CI validates Python, the Node wrapper, and package builds. On
tag pushes, the workflow can publish the npm wrapper and then create or update
the GitHub release. The release description is generated from the matching
`CHANGELOG.md` entry and appends links to:

- the tag-specific changelog
- the PyPI package:
  https://pypi.org/project/skills-router/
- the npm package:
  https://www.npmjs.com/package/@the-long-ride/skills-router

## Roadmap

- [x] Core install review pipeline.
- [x] Agent Bridge for popular AI-agent hosts.
- [x] Full-package install with generated route plans.
- [x] One-time all-agent installs with target-aware routing.
- [x] `/skills-router index` reconciliation and conflict recommendations.
- [x] `/skills-router refine` discovery and route refinement.
- [x] Dynamic route lookup through MCP and CLI.
- [x] Registry Watch daemon with trust degradation alerts.
- [ ] Route-choice persistence API for applying human selections.
- [ ] pgvector-native production migration.
- [ ] Dashboard for routing history, audit logs, and conflict decisions.

## License

This project is licensed under the **GNU General Public License (GPLv3)**.

Developed by **the-long-ride**.
