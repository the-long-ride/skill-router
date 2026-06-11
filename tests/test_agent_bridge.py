"""Tests for AI-agent bridge profiles, prompts, and slash parsing."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from skills_router.config import SkillsRouterConfig


def test_profiles_include_requested_agent_targets():
    from skills_router.agent_bridge.profiles import list_agent_profiles

    targets = {profile.target for profile in list_agent_profiles()}

    assert {
        "antigravity",
        "antigravity-cli",
        "antigravity-ide",
        "codex",
        "codex-ide",
        "cline",
        "cursor",
        "kiro",
        "claude",
        "github-copilot",
        "opencode",
        "hermes-agent",
        "windsurf",
    }.issubset(targets)


def test_render_agent_prompt_is_target_specific_and_compact():
    from skills_router.agent_bridge.prompts import render_agent_prompt

    prompt = render_agent_prompt("github-copilot", agent_id="copilot-agent")

    assert "GitHub Copilot" in prompt
    assert "/skills-router install <pkg> for me" in prompt
    assert "workspace:copilot-agent" in prompt
    assert "run_slash_command" in prompt
    assert "Preferred execution order" not in prompt
    assert len(prompt) < 1400


def test_render_agent_prompt_full_detail_keeps_expanded_contract():
    from skills_router.agent_bridge.prompts import render_agent_prompt

    prompt = render_agent_prompt("codex", agent_id="codex-local", detail="full")

    assert "Preferred execution order" in prompt
    assert "Parsing rules" in prompt


def test_codex_ide_profile_accepts_vscode_alias_and_plain_trigger(tmp_path):
    from skills_router.agent_bridge.connect import build_agent_connection
    from skills_router.agent_bridge.prompts import render_agent_prompt

    config = SkillsRouterConfig(data_dir=str(tmp_path / "data"))
    config.workspace_root = str(tmp_path)

    result = build_agent_connection(
        config,
        target="codex-vscode",
        agent_id="codex-ide-local",
    )
    prompt = render_agent_prompt("chatgpt-vscode", agent_id="codex-ide-local")

    assert result["target"] == "codex-ide"
    assert result["display_name"] == "OpenAI Codex IDE Extension"
    assert "--target codex-ide" in result["fallback_command"]
    assert result["skill_dirs"][0]["configured"] == ".codex/skills"
    assert "`skills-router`" in prompt
    assert "ordinary chat text" in prompt


def test_build_agent_connection_from_source_includes_mcp_env(tmp_path):
    from skills_router.agent_bridge.connect import build_agent_connection

    config = SkillsRouterConfig(data_dir=str(tmp_path / "data"))
    config.workspace_root = str(tmp_path)

    result = build_agent_connection(
        config,
        target="cursor",
        agent_id="cursor-local",
        from_source=True,
    )

    server = result["mcp_config"]["mcpServers"]["skills-router"]
    assert result["target"] == "cursor"
    assert server["args"] == ["-m", "skills_router.cli", "mcp"]
    assert "PYTHONPATH" in server["env"]
    assert result["instruction_files"][0]["configured"] == (
        ".cursor/rules/skills-router.md"
    )
    assert "run_slash_command" in result["bridge_prompt"]


def test_write_bridge_instructions_creates_managed_block(tmp_path):
    from skills_router.agent_bridge.connect import (
        BEGIN_MARKER,
        build_agent_connection,
        write_bridge_instructions,
    )

    config = SkillsRouterConfig(data_dir=str(tmp_path / "data"))
    config.workspace_root = str(tmp_path)
    result = build_agent_connection(config, target="codex", agent_id="codex-local")

    first = write_bridge_instructions(config, result)
    result["bridge_prompt"] = "# Changed Bridge"
    second = write_bridge_instructions(config, result)

    text = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert first["action"] == "created"
    assert second["action"] == "updated"
    assert text.count(BEGIN_MARKER) == 1
    assert "# Changed Bridge" in text


def test_write_bridge_skill_creates_managed_skill_file(tmp_path):
    from skills_router.agent_bridge.connect import (
        SKILL_BEGIN_MARKER,
        build_agent_connection,
        write_bridge_skill,
    )

    config = SkillsRouterConfig(data_dir=str(tmp_path / "data"))
    config.workspace_root = str(tmp_path)
    result = build_agent_connection(
        config,
        target="codex-vscode",
        agent_id="codex-ide-local",
    )

    first = write_bridge_skill(config, result)
    result["bridge_prompt"] = "# Changed Skill Bridge"
    second = write_bridge_skill(config, result)
    dry = write_bridge_skill(config, result, dry_run=True)

    path = tmp_path / ".codex" / "skills" / "skills-router" / "SKILL.md"
    text = path.read_text(encoding="utf-8")
    assert first["action"] == "created"
    assert second["action"] == "updated"
    assert dry["action"] == "would_update"
    assert first["path"] == str(path)
    assert text.startswith("---\nname: skills-router\n")
    assert text.count(SKILL_BEGIN_MARKER) == 1
    assert "# Changed Skill Bridge" in text


def test_parse_install_for_me_uses_workspace_scope():
    from skills_router.agent_bridge.parser import parse_slash_command

    intent = parse_slash_command(
        "/skills-router install weather-tool for me",
        target="cline",
        agent_id="cline-local",
    )

    assert intent.command == "install"
    assert intent.target == "cline"
    assert intent.arguments["package_or_manifest"] == "weather-tool"
    assert intent.scope == "workspace:cline-local"
    assert intent.delegated is True
    assert intent.auto_approve is False


def test_parse_analyze_source_link():
    from skills_router.agent_bridge.parser import parse_slash_command

    intent = parse_slash_command(
        "/skills-router analyze https://github.com/owner/repo",
        target="codex",
        agent_id="codex-local",
    )

    assert intent.command == "analyze"
    assert intent.arguments["source_ref"] == "https://github.com/owner/repo"


def test_parse_global_dry_run_auto_approve():
    from skills_router.agent_bridge.parser import parse_slash_command

    intent = parse_slash_command(
        "/skills-router install github:owner/repo globally dry run auto approve",
        target="antigravity",
        agent_id="ag",
    )

    assert intent.command == "install"
    assert intent.target == "antigravity"
    assert intent.arguments["package_or_manifest"] == "github:owner/repo"
    assert intent.scope == "global"
    assert intent.dry_run is True
    assert intent.auto_approve is True


def test_parse_partial_skillset_install_sets_selective_routes():
    from skills_router.agent_bridge.parser import parse_slash_command

    intent = parse_slash_command(
        "/skills-router install writer-pack skillset only needed skills for me",
        target="codex",
        agent_id="codex-local",
    )

    assert intent.arguments["package_or_manifest"] == "writer-pack"
    assert intent.package_type == "skillset"
    assert intent.routing_mode == "selective_routes"


def test_parse_all_agents_install_sets_global_target_scope():
    from skills_router.agent_bridge.parser import parse_slash_command

    intent = parse_slash_command(
        "/skills-router install writer-pack for all installed agents",
        target="codex",
        agent_id="codex-local",
    )

    assert intent.command == "install"
    assert intent.scope == "global"
    assert intent.all_agents is True
    assert "codex" in intent.agent_targets
    assert "codex-ide" in intent.agent_targets
    assert "cursor" in intent.agent_targets
    assert "windsurf" in intent.agent_targets


def test_parse_plain_skills_router_text_supports_ide_fallback():
    from skills_router.agent_bridge.parser import parse_slash_command

    intent = parse_slash_command(
        "skills-router status",
        target="codex-vscode",
        agent_id="codex-ide-local",
    )

    assert intent.command == "status"
    assert intent.target == "codex-ide"


def test_parse_index_command_defaults_to_all_scopes():
    from skills_router.agent_bridge.parser import parse_slash_command

    intent = parse_slash_command(
        "/skills-router index",
        target="codex",
        agent_id="codex-local",
    )

    assert intent.command == "index"
    assert intent.scope is None


def test_parse_status_command_defaults_to_all_scopes():
    from skills_router.agent_bridge.parser import parse_slash_command

    intent = parse_slash_command(
        "/skills-router status",
        target="codex",
        agent_id="codex-local",
    )

    assert intent.command == "status"
    assert intent.arguments == {}
    assert intent.scope is None


def test_parse_refine_command_accepts_blank_and_named_skillsets():
    from skills_router.agent_bridge.parser import parse_slash_command

    blank = parse_slash_command(
        "/skills-router refine",
        target="codex",
        agent_id="codex-local",
    )
    named = parse_slash_command(
        "/skills-router refine writer-pack engram",
        target="codex",
        agent_id="codex-local",
    )

    assert blank.command == "refine"
    assert blank.arguments["skillsets"] == []
    assert blank.scope is None
    assert named.arguments["skillsets"] == ["writer-pack", "engram"]


def test_parse_route_command_extracts_task_text():
    from skills_router.agent_bridge.parser import parse_slash_command

    intent = parse_slash_command(
        "/skills-router route draft an article",
        target="codex",
        agent_id="codex-local",
    )

    assert intent.command == "route"
    assert intent.arguments["task"] == "draft article"


def test_parse_uninstall_skill_for_me():
    from skills_router.agent_bridge.parser import parse_slash_command

    intent = parse_slash_command(
        "/skills-router uninstall skill writer-pack for me",
        target="codex",
        agent_id="codex-local",
    )

    assert intent.command == "uninstall"
    assert intent.arguments["tool_id"] == "writer-pack"
    assert intent.scope == "workspace:codex-local"
    assert intent.package_type == "skillset"
    assert intent.delegated is True


def test_bridge_decision_callback_matches_workspace_scope():
    from skills_router.agent_bridge.executor import _decision_callback

    callback = _decision_callback(auto_approve=False, scope="workspace:codex-local")

    assert callback(
        "Decision: new capability scope",
        ["Install globally", "Install for this workspace only", "Cancel"],
    ) == 1


def test_build_routing_plan_keeps_full_package_and_routes_selectively():
    from skills_router.agent_bridge.routing import build_routing_plan

    manifest = {
        "tool_id": "writer-pack",
        "name": "Writer Pack",
        "version": "1.0.0",
        "agent_package": {
            "type": "skillset",
            "skillsets": [
                {
                    "id": "draft",
                    "name": "Draft Writer",
                    "use_when": "drafting long form content",
                    "permissions": ["filesystem: read_workspace"],
                }
            ],
        },
    }

    plan = build_routing_plan(
        manifest,
        scope="workspace:codex-local",
        package_type="auto",
        routing_mode="selective_routes",
    )

    assert plan["physical_install"] == "full_package"
    assert plan["package_type"] == "skillset"
    assert plan["status"] == "needs_selection"
    assert plan["rules"][0]["status"] == "needs_selection"
    assert "writer-pack.draft" in plan["rules"][0]["prompt_snippet"]


def test_build_routing_plan_records_agent_target_subset():
    from skills_router.agent_bridge.routing import build_routing_plan

    plan = build_routing_plan(
        {
            "tool_id": "writer-pack",
            "name": "Writer Pack",
            "version": "1.0.0",
        },
        scope="global",
        target_agents=["codex", "claude", "codex"],
    )

    assert plan["scope"] == "global"
    assert plan["applies_to_all_agents"] is False
    assert plan["target_agents"] == ["codex", "claude"]


def test_build_routing_plan_marks_default_target_set_as_all_agents():
    from skills_router.agent_bridge.profiles import default_all_agent_targets
    from skills_router.agent_bridge.routing import build_routing_plan

    plan = build_routing_plan(
        {
            "tool_id": "writer-pack",
            "name": "Writer Pack",
            "version": "1.0.0",
        },
        scope="global",
        target_agents=list(default_all_agent_targets()),
    )

    assert plan["applies_to_all_agents"] is True


def test_agent_target_report_detects_configured_agent_dirs(tmp_path):
    from skills_router.agent_bridge.targeting import build_agent_target_report

    cursor_dir = tmp_path / ".cursor" / "skills"
    cursor_dir.mkdir(parents=True)
    config = SkillsRouterConfig(data_dir=str(tmp_path / "data"))
    config.workspace_root = str(tmp_path)

    report = build_agent_target_report(config, targets=["cursor", "codex"])

    cursor = next(item for item in report["targets"] if item["target"] == "cursor")
    assert report["target_names"] == ["cursor", "codex"]
    assert cursor["installed"] is True


def test_agent_target_report_ignores_shared_agent_skill_dir(tmp_path):
    from skills_router.agent_bridge.targeting import build_agent_target_report

    shared_dir = tmp_path / ".agents" / "skills"
    shared_dir.mkdir(parents=True)
    config = SkillsRouterConfig(data_dir=str(tmp_path / "data"))
    config.workspace_root = str(tmp_path)

    report = build_agent_target_report(config, targets=["cursor"])

    cursor = next(item for item in report["targets"] if item["target"] == "cursor")
    assert cursor["installed"] is False
    assert report["installed_target_count"] == 0


def test_index_marks_missing_routes_without_deleting(tmp_path):
    from skills_router.agent_bridge.indexer import index_installed_skillsets
    from skills_router.agent_bridge.routing import (
        build_routing_plan,
        persist_routing_plan,
    )
    from skills_router.storage.memory_store import MemoryBrainIndexStore

    config = SkillsRouterConfig(data_dir=str(tmp_path))
    plan = build_routing_plan(
        {"tool_id": "writer-pack", "name": "Writer Pack", "version": "1.0.0"},
        scope="workspace:codex-local",
    )

    persist_routing_plan(config, plan)
    store = MemoryBrainIndexStore(
        brain_index_path=config.brain_index_path,
        dep_graph_path=config.dep_graph_path,
    )
    result = index_installed_skillsets(config, store)

    routing_file = tmp_path / "skills-router.json"
    packages = json.loads(routing_file.read_text())["packages"]
    assert "writer-pack" in packages
    assert packages["writer-pack"]["status"] == "missing_from_index"
    assert result["status"] == "REVIEW_NEEDED"
    assert result["stale_route_count"] == 1


def test_index_detects_conflict_and_recommends_route(tmp_path):
    from skills_router.agent_bridge.indexer import index_installed_skillsets
    from skills_router.storage.memory_store import MemoryBrainIndexStore

    config = SkillsRouterConfig(data_dir=str(tmp_path))
    store = MemoryBrainIndexStore(
        brain_index_path=config.brain_index_path,
        dep_graph_path=config.dep_graph_path,
    )
    base_caps = {
        "inputs": ["plain text"],
        "outputs": ["draft article"],
        "permissions": ["filesystem: read_workspace"],
    }
    store.save_tool({
        "tool_id": "writer-alpha",
        "name": "Writer Alpha",
        "version": "1.2.0",
        "layer_1_domain_tags": ["writing"],
        "layer_3_capabilities": base_caps,
        "layer_5_provenance": {"trust_score": 0.92},
        "layer_meta": {"install_scope": "global"},
    })
    store.save_tool({
        "tool_id": "writer-beta",
        "name": "Writer Beta",
        "version": "1.0.0",
        "layer_1_domain_tags": ["writing"],
        "layer_3_capabilities": base_caps,
        "layer_5_provenance": {"trust_score": 0.72},
        "layer_meta": {"install_scope": "global"},
    })

    result = index_installed_skillsets(config, store)

    assert result["status"] == "REVIEW_NEEDED"
    assert result["conflict_count"] == 1
    conflict = result["conflicts"][0]
    assert conflict["recommendation"]["recommended_tool_id"] == "writer-alpha"
    assert "Recommendation:" in result["human_prompt"]
    routing_file = tmp_path / "skills-router.json"
    packages = json.loads(routing_file.read_text())["packages"]
    assert packages["writer-alpha"]["status"] == "needs_selection"
    assert packages["writer-beta"]["status"] == "needs_selection"


def test_refine_discovers_workspace_skill_and_requires_activation(tmp_path):
    from skills_router.agent_bridge.indexer import refine_installed_skillsets
    from skills_router.agent_bridge.routing import read_routing_state
    from skills_router.storage.memory_store import MemoryBrainIndexStore

    skill_dir = tmp_path / ".agents" / "skills" / "engram"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: engram\n"
        "description: Load and save workspace memory safely.\n"
        "---\n"
        "# Engram\n",
        encoding="utf-8",
    )
    config = SkillsRouterConfig(data_dir=str(tmp_path / "data"))
    config.workspace_root = str(tmp_path)
    config.global_skill_dirs = []
    store = MemoryBrainIndexStore(
        brain_index_path=config.brain_index_path,
        dep_graph_path=config.dep_graph_path,
    )

    result = refine_installed_skillsets(
        config,
        store,
        scope="workspace:codex-local",
    )

    refreshed = MemoryBrainIndexStore(
        brain_index_path=config.brain_index_path,
        dep_graph_path=config.dep_graph_path,
    )
    routes = read_routing_state(config)["packages"]
    assert result["status"] == "REVIEW_NEEDED"
    assert result["imported_record_count"] == 1
    assert result["activation_reviews"][0]["tool_id"] == "engram"
    assert refreshed.get_tool("engram")["layer_meta"]["physical_install"] == "external_discovery"
    assert routes["engram"]["scope"] == "workspace:codex-local"
    assert routes["engram"]["status"] == "needs_selection"


def test_refine_discovers_nested_system_skills(tmp_path):
    from skills_router.agent_bridge.indexer import refine_installed_skillsets
    from skills_router.storage.memory_store import MemoryBrainIndexStore

    skill_dir = tmp_path / ".codex" / "skills" / ".system" / "engram"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: engram\n"
        "description: Load workspace memory.\n"
        "---\n",
        encoding="utf-8",
    )
    config = SkillsRouterConfig(data_dir=str(tmp_path / "data"))
    config.workspace_root = str(tmp_path)
    config.global_skill_dirs = []
    store = MemoryBrainIndexStore(
        brain_index_path=config.brain_index_path,
        dep_graph_path=config.dep_graph_path,
    )

    result = refine_installed_skillsets(config, store, skillset_names=["engram"])

    assert result["imported_record_count"] == 1
    assert result["discovered_records"][0]["path"].endswith(
        ".codex\\skills\\.system\\engram\\SKILL.md"
    ) or result["discovered_records"][0]["path"].endswith(
        ".codex/skills/.system/engram/SKILL.md"
    )


def test_chat_refine_defaults_workspace_discovery_to_agent_scope(tmp_path):
    from skills_router.agent_bridge.executor import execute_slash_command
    from skills_router.agent_bridge.routing import read_routing_state

    skill_dir = tmp_path / ".agents" / "skills" / "engram"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: engram\n"
        "description: Load workspace memory.\n"
        "---\n",
        encoding="utf-8",
    )
    config = SkillsRouterConfig(data_dir=str(tmp_path / "data"))
    config.workspace_root = str(tmp_path)
    config.global_skill_dirs = []

    result = execute_slash_command(
        "/skills-router refine",
        config,
        target="codex",
        agent_id="codex-local",
    )

    routes = read_routing_state(config)["packages"]
    assert result["intent"]["scope"] is None
    assert result["scope"] is None
    assert routes["engram"]["scope"] == "workspace:codex-local"


def test_execute_slash_command_status_reports_paths(tmp_path):
    from skills_router.agent_bridge.executor import execute_slash_command

    config = SkillsRouterConfig(data_dir=str(tmp_path))

    result = execute_slash_command(
        "/skills-router status",
        config,
        target="codex",
        agent_id="codex-local",
    )

    assert result["status"] == "OK"
    assert result["router_status"] == "empty"
    assert "state_paths" in result
    assert "skill_paths" in result
    assert result["human_summary"].startswith("Skills Router status:")


def test_refine_imports_global_skills_router_records_for_workspace(tmp_path):
    from skills_router.agent_bridge.indexer import refine_installed_skillsets
    from skills_router.storage.memory_store import MemoryBrainIndexStore

    global_dir = tmp_path / "global"
    workspace_dir = tmp_path / "workspace"
    global_store = MemoryBrainIndexStore(
        brain_index_path=str(global_dir / "brain_index.json"),
        dep_graph_path=str(global_dir / "dep_graph.json"),
    )
    global_store.save_tool({
        "tool_id": "writer-pack",
        "name": "Writer Pack",
        "version": "1.0.0",
        "layer_1_domain_tags": ["writing"],
        "layer_3_capabilities": {"outputs": ["draft article"]},
        "layer_5_provenance": {"trust_score": 0.9},
        "layer_meta": {"install_scope": "global"},
    })
    config = SkillsRouterConfig(data_dir=str(workspace_dir))
    config.global_data_dir = str(global_dir)
    config.workspace_root = str(tmp_path / "empty-workspace")
    store = MemoryBrainIndexStore(
        brain_index_path=config.brain_index_path,
        dep_graph_path=config.dep_graph_path,
    )

    result = refine_installed_skillsets(config, store, skillset_names=["writer-pack"])

    refreshed = MemoryBrainIndexStore(
        brain_index_path=config.brain_index_path,
        dep_graph_path=config.dep_graph_path,
    )
    assert result["status"] == "OK"
    assert result["discovery"]["sources"] == ["skills-router-global"]
    assert result["refined_tool_ids"] == ["writer-pack"]
    assert refreshed.get_tool("writer-pack")["layer_meta"]["discovered_source"] == (
        "skills-router-global"
    )


def test_route_task_matches_active_route(tmp_path):
    from skills_router.agent_bridge.routing import (
        build_routing_plan,
        persist_routing_plan,
        route_task,
    )

    config = SkillsRouterConfig(data_dir=str(tmp_path))
    manifest = {
        "tool_id": "writer-pack",
        "name": "Writer Pack",
        "version": "1.0.0",
        "agent_package": {
            "type": "skillset",
            "skillsets": [
                {
                    "id": "draft",
                    "name": "Draft Writer",
                    "use_when": "draft article",
                }
            ],
        },
    }
    persist_routing_plan(
        config,
        build_routing_plan(manifest, scope="workspace:codex-local"),
    )

    result = route_task(
        config,
        "draft article about the launch",
        scope="workspace:codex-local",
    )

    assert result["status"] == "OK"
    assert result["routes"][0]["route"] == "writer-pack.draft"


def test_route_task_uses_priority_when_scores_tie(tmp_path):
    from skills_router.agent_bridge.routing import (
        build_routing_plan,
        persist_routing_plan,
        read_routing_state,
        route_task,
        write_routing_state,
    )

    config = SkillsRouterConfig(data_dir=str(tmp_path))
    first = {
        "tool_id": "writer-first",
        "name": "Writer First",
        "version": "1.0.0",
        "agent_package": {
            "type": "skillset",
            "skillsets": [{"id": "draft", "name": "Draft", "use_when": "draft article"}],
        },
    }
    second = {
        "tool_id": "writer-second",
        "name": "Writer Second",
        "version": "1.0.0",
        "agent_package": {
            "type": "skillset",
            "skillsets": [{"id": "draft", "name": "Draft", "use_when": "draft article"}],
        },
    }
    persist_routing_plan(config, build_routing_plan(first, scope="global"))
    persist_routing_plan(config, build_routing_plan(second, scope="global"))
    state = read_routing_state(config)
    state["packages"]["writer-first"]["rules"][0]["priority"] = 200
    state["packages"]["writer-second"]["rules"][0]["priority"] = 10
    write_routing_state(config, state)

    result = route_task(config, "draft article")

    assert result["routes"][0]["route"] == "writer-second.draft"


def test_route_task_filters_targeted_routes_by_agent(tmp_path):
    from skills_router.agent_bridge.routing import (
        build_routing_plan,
        persist_routing_plan,
        route_task,
    )

    config = SkillsRouterConfig(data_dir=str(tmp_path))
    manifest = {
        "tool_id": "writer-pack",
        "name": "Writer Pack",
        "version": "1.0.0",
        "agent_package": {
            "type": "skillset",
            "skillsets": [
                {
                    "id": "draft",
                    "name": "Draft Writer",
                    "use_when": "draft article",
                }
            ],
        },
    }
    persist_routing_plan(
        config,
        build_routing_plan(manifest, scope="global", target_agents=["codex"]),
    )

    codex = route_task(config, "draft article", agent_target="codex")
    cursor = route_task(config, "draft article", agent_target="cursor")

    assert codex["status"] == "OK"
    assert cursor["status"] == "NO_ROUTE"


def test_execute_slash_command_uninstalls_skills_router_state(tmp_path):
    from skills_router.agent_bridge.executor import execute_slash_command
    from skills_router.agent_bridge.routing import (
        build_routing_plan,
        persist_routing_plan,
        read_routing_state,
    )
    from skills_router.audit.logger import AuditLogger
    from skills_router.layers.lockfile import SkillsRouterLockfile
    from skills_router.storage.memory_store import MemoryBrainIndexStore

    config = SkillsRouterConfig(data_dir=str(tmp_path))
    store = MemoryBrainIndexStore(
        brain_index_path=config.brain_index_path,
        dep_graph_path=config.dep_graph_path,
    )
    manifest = {
        "tool_id": "writer-pack",
        "name": "Writer Pack",
        "version": "1.0.0",
        "dependencies": {"markdown": ">=3.0"},
        "layer_5_provenance": {"trust_score": 0.91},
        "layer_meta": {"install_scope": "workspace:codex-local"},
    }
    store.save_tool(manifest)
    store.merge_deps_for_tool("writer-pack", manifest["dependencies"])
    SkillsRouterLockfile(config.registry_lockfile_path).upsert(
        manifest,
        requested="writer-pack",
        scope="workspace:codex-local",
    )
    persist_routing_plan(
        config,
        build_routing_plan(manifest, scope="workspace:codex-local"),
    )

    result = execute_slash_command(
        "/skills-router uninstall skill writer-pack for me",
        config,
        target="codex",
        agent_id="codex-local",
    )

    refreshed = MemoryBrainIndexStore(
        brain_index_path=config.brain_index_path,
        dep_graph_path=config.dep_graph_path,
    )
    assert result["status"] == "UNINSTALLED"
    assert result["package_resources_removed"] is False
    assert result["route_reconciliation"]["status"] == "EMPTY"
    assert result["requires_human_decision"] is False
    assert refreshed.get_tool("writer-pack") is None
    assert refreshed.get_dep_graph() == {}
    assert "writer-pack" not in SkillsRouterLockfile(
        config.registry_lockfile_path
    ).read()["tools"]
    assert "writer-pack" not in read_routing_state(config)["packages"]
    audit_entries = AuditLogger(log_path=config.audit_log_path).query(
        tool_id="writer-pack"
    )
    assert audit_entries[0]["wg_case"] == "UNINSTALL"
    assert "Package resources were not removed" in result["human_summary"]


def test_execute_slash_command_uninstall_dry_run_preserves_state(tmp_path):
    from skills_router.agent_bridge.executor import execute_slash_command
    from skills_router.agent_bridge.routing import (
        build_routing_plan,
        persist_routing_plan,
        read_routing_state,
    )
    from skills_router.layers.lockfile import SkillsRouterLockfile
    from skills_router.storage.memory_store import MemoryBrainIndexStore

    config = SkillsRouterConfig(data_dir=str(tmp_path))
    store = MemoryBrainIndexStore(
        brain_index_path=config.brain_index_path,
        dep_graph_path=config.dep_graph_path,
    )
    manifest = {
        "tool_id": "writer-pack",
        "name": "Writer Pack",
        "version": "1.0.0",
        "dependencies": {"markdown": ">=3.0"},
        "layer_meta": {"install_scope": "workspace:codex-local"},
    }
    store.save_tool(manifest)
    store.merge_deps_for_tool("writer-pack", manifest["dependencies"])
    SkillsRouterLockfile(config.registry_lockfile_path).upsert(
        manifest,
        requested="writer-pack",
        scope="workspace:codex-local",
    )
    persist_routing_plan(
        config,
        build_routing_plan(manifest, scope="workspace:codex-local"),
    )

    result = execute_slash_command(
        "/skills-router uninstall writer-pack for me dry run",
        config,
        target="codex",
        agent_id="codex-local",
    )

    refreshed = MemoryBrainIndexStore(
        brain_index_path=config.brain_index_path,
        dep_graph_path=config.dep_graph_path,
    )
    assert result["status"] == "DRY_RUN_UNINSTALLED"
    assert result["dry_run"] is True
    assert result["would_remove"]["brain_index"] is True
    assert refreshed.get_tool("writer-pack") is not None
    assert "markdown" in refreshed.get_dep_graph()
    assert "writer-pack" in SkillsRouterLockfile(
        config.registry_lockfile_path
    ).read()["tools"]
    assert "writer-pack" in read_routing_state(config)["packages"]


def test_uninstall_reindexes_remaining_conflicts(tmp_path):
    from skills_router.agent_bridge.uninstaller import uninstall_skill_metadata
    from skills_router.storage.memory_store import MemoryBrainIndexStore

    config = SkillsRouterConfig(data_dir=str(tmp_path))
    store = MemoryBrainIndexStore(
        brain_index_path=config.brain_index_path,
        dep_graph_path=config.dep_graph_path,
    )
    base_caps = {
        "inputs": ["plain text"],
        "outputs": ["draft article"],
        "permissions": ["filesystem: read_workspace"],
    }
    store.save_tool({
        "tool_id": "writer-pack",
        "name": "Writer Pack",
        "version": "1.0.0",
        "layer_meta": {"install_scope": "global"},
    })
    store.save_tool({
        "tool_id": "writer-alpha",
        "name": "Writer Alpha",
        "version": "1.2.0",
        "layer_1_domain_tags": ["writing"],
        "layer_3_capabilities": base_caps,
        "layer_5_provenance": {"trust_score": 0.92},
        "layer_meta": {"install_scope": "global"},
    })
    store.save_tool({
        "tool_id": "writer-beta",
        "name": "Writer Beta",
        "version": "1.0.0",
        "layer_1_domain_tags": ["writing"],
        "layer_3_capabilities": base_caps,
        "layer_5_provenance": {"trust_score": 0.72},
        "layer_meta": {"install_scope": "global"},
    })

    result = uninstall_skill_metadata(config, store, "writer-pack", scope="global")

    reconciliation = result["route_reconciliation"]
    assert result["status"] == "UNINSTALLED"
    assert result["requires_human_decision"] is True
    assert reconciliation["status"] == "REVIEW_NEEDED"
    assert reconciliation["conflict_count"] == 1
    assert (
        reconciliation["conflicts"][0]["recommendation"]["recommended_tool_id"]
        == "writer-alpha"
    )
    assert "Recommendations are included" in result["human_summary"]


@patch("skills_router.layers.registry_resolver.RegistryResolver.resolve")
@patch("skills_router.agent_bridge.executor.SkillsRouterOrchestrator")
def test_execute_slash_command_installs_safe_case(
    mock_orchestrator,
    mock_resolve,
    tmp_path,
):
    from skills_router.agent_bridge.executor import execute_slash_command

    config = SkillsRouterConfig(data_dir=str(tmp_path))
    manifest = {"tool_id": "weather-tool", "name": "Weather", "version": "1.0.0"}
    mock_resolve.return_value = manifest
    mock_inst = MagicMock()
    mock_inst.install.return_value = {
        "status": "INSTALLED",
        "tool_id": "weather-tool",
        "wg_case": "CASE_1",
        "decision": "APPROVE",
    }
    mock_orchestrator.return_value = mock_inst

    result = execute_slash_command(
        "/skills-router install weather-tool for me",
        config,
        target="codex",
        agent_id="codex-local",
    )

    assert result["status"] == "INSTALLED"
    assert result["intent"]["scope"] == "workspace:codex-local"
    assert result["skills_routing"]["physical_install"] == "full_package"
    assert result["skills_routing"]["status"] == "active"
    assert "human_summary" in result
    routing_file = tmp_path / "skills-router.json"
    assert json.loads(routing_file.read_text())["packages"]["weather-tool"]
    mock_inst.install.assert_called_once_with(
        manifest,
        scope="workspace:codex-local",
        user_id="codex-local",
        dry_run=False,
    )


@patch("skills_router.layers.registry_resolver.RegistryResolver.resolve")
@patch("skills_router.agent_bridge.executor.SkillsRouterOrchestrator")
def test_execute_slash_command_installs_once_for_all_agents(
    mock_orchestrator,
    mock_resolve,
    tmp_path,
):
    from skills_router.agent_bridge.executor import execute_slash_command

    config = SkillsRouterConfig(data_dir=str(tmp_path))
    manifest = {"tool_id": "weather-tool", "name": "Weather", "version": "1.0.0"}
    mock_resolve.return_value = manifest
    mock_inst = MagicMock()
    mock_inst.install.return_value = {
        "status": "INSTALLED",
        "tool_id": "weather-tool",
        "wg_case": "CASE_1",
        "decision": "APPROVE",
    }
    mock_orchestrator.return_value = mock_inst

    result = execute_slash_command(
        "/skills-router install weather-tool for all installed agents",
        config,
        target="codex",
        agent_id="codex-local",
    )

    assert result["status"] == "INSTALLED"
    assert result["intent"]["all_agents"] is True
    assert result["intent"]["scope"] == "global"
    assert result["agent_targets"]["target_count"] == 11
    assert "cursor" in result["skills_routing"]["target_agents"]
    assert "Applies to 11 agent target(s)" in result["human_summary"]
    routing_file = tmp_path / "skills-router.json"
    packages = json.loads(routing_file.read_text())["packages"]
    assert packages["weather-tool"]["scope"] == "global"
    assert packages["weather-tool"]["applies_to_all_agents"] is True
    mock_inst.install.assert_called_once_with(
        manifest,
        scope="global",
        user_id="codex-local",
        dry_run=False,
    )
