# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.0.3] - 2026-06-07

### Added
- Added PyPI publishing to the tag release workflow using GitHub OIDC trusted publishing.

### Changed
- Pinned the npm wrapper bootstrap install to the matching `skills-router` PyPI version.
- Updated the npm package README to explain the wrapper first, then include the full project README.

## [0.0.2] - 2026-06-06

### Added
- Added one-time all-agent skill installs with target-aware route metadata.
- Added bridge profiles for Antigravity, Cursor, Windsurf, and related agent targets.
- Added source-link analysis for npm and GitHub packages with conservative inferred manifest drafts.
- Added router status reporting for data paths, configured skill paths, and route counts.
- Added agent bridge setup output through `connect`, including MCP config and target instructions.

### Changed
- Tightened route lookup so custom agent target lists are enforced when a host target is provided.
- Hardened state writes on Windows test and runtime paths.
- Expanded dry-run support across write-capable CLI and MCP commands.
- Documented local editable installs, virtualenv setup, and source-based agent connection flows.

## [0.0.1] - 2026-05-27

### Added
- Initial project release of `skills-router` at v0.0.1.
- NPM wrapper project structure inside `skills-router-npx/` for seamless `npx` execution.
- Added type validation, boundary verification, and exception safety to orchestrator's decision callback handler (`_get_decision`) to fail-closed safely to cancel/no-op on error.
- Integration tests in `tests/test_orchestrator.py` covering callback safety guards.
- Comprehensive publishing and integration guidelines in `GUIDELINE.md`.

### Changed
- Replaced terminology referencing "Workgroup" and "WG" to "Workspace/Global" throughout docstrings, CLI, comments, and diagrams.
- Shortened and polished the main `README.md` to present key features, installation, quick usage, and roadmap, linking to `GUIDELINE.md` for deep details.
- Updated project license metadata in `pyproject.toml` and `package.json` to GPLv3.
