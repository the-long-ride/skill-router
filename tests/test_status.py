"""Tests for Skills Router status reporting."""

from __future__ import annotations

import argparse

from skills_router.config import SkillsRouterConfig


def test_build_router_status_reports_paths_and_counts(tmp_path):
    from skills_router.agent_bridge.routing import build_routing_plan, persist_routing_plan
    from skills_router.status import build_router_status
    from skills_router.storage.memory_store import MemoryBrainIndexStore

    workspace_root = tmp_path / "workspace"
    workspace_skill_dir = workspace_root / ".codex" / "skills"
    global_skill_dir = tmp_path / "global-skills"
    workspace_skill_dir.mkdir(parents=True)
    global_skill_dir.mkdir()

    config = SkillsRouterConfig(data_dir=str(tmp_path / "data"))
    config.workspace_root = str(workspace_root)
    config.workspace_skill_dirs = [".codex/skills", ".agents/skills"]
    config.global_skill_dirs = [
        str(global_skill_dir),
        "$MISSING_SKILLS_ROUTER_TEST/skills",
    ]
    store = MemoryBrainIndexStore(
        brain_index_path=config.brain_index_path,
        dep_graph_path=config.dep_graph_path,
    )
    manifest = {
        "tool_id": "writer-pack",
        "name": "Writer Pack",
        "version": "1.0.0",
        "dependencies": {"markdown": ">=3.0"},
    }
    store.save_tool(manifest)
    store.merge_deps_for_tool("writer-pack", manifest["dependencies"])
    persist_routing_plan(config, build_routing_plan(manifest, scope="global"))

    result = build_router_status(config, store)

    assert result["status"] == "OK"
    assert result["router_status"] == "ready"
    assert result["counts"]["indexed_tools"] == 1
    assert result["counts"]["dependency_entries"] == 1
    assert result["counts"]["routing_packages"] == 1
    assert result["counts"]["active_routes"] == 1
    assert result["counts"]["workspace_skill_dirs_existing"] == 1
    assert result["counts"]["global_skill_dirs_existing"] == 1
    assert any(
        path["configured"] == ".codex/skills" and path["exists"]
        for path in result["skill_paths"]["workspace"]
    )
    assert any(
        path["env_unresolved"] for path in result["skill_paths"]["global"]
    )
    assert any(
        path["name"] == "routing_file" and path["exists"]
        for path in result["state_paths"]
    )


def test_cmd_status_json_outputs_status(tmp_path, capsys):
    from skills_router.cli import cmd_status

    config = SkillsRouterConfig(data_dir=str(tmp_path))
    args = argparse.Namespace(json_output=True)

    rc = cmd_status(args, config)

    assert rc == 0
    out = capsys.readouterr().out
    assert '"status": "OK"' in out
    assert '"router_status": "empty"' in out
    assert '"skill_paths": {' in out
