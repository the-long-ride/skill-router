"""Tests for CLI slash command normalisation and registry resolver integration."""

from __future__ import annotations

import argparse
import sys
from unittest.mock import MagicMock, patch

from skills_router.config import SkillsRouterConfig


def test_normalize_slash_args_supports_global_options_before_command():
    """Slash command tokens can be mixed with normal CLI global options."""
    from skills_router.cli import _normalize_slash_args

    assert _normalize_slash_args(
        ["--data-dir", "tmp-data", "/skills-router", "/list"]
    ) == ["--data-dir", "tmp-data", "list"]


def test_decision_callback_yes_auto_approves():
    """--yes should select the first review option for local agent automation."""
    from skills_router.cli import _decision_callback_for_install

    callback = _decision_callback_for_install(
        argparse.Namespace(yes=True, decision_policy="prompt")
    )

    assert callback("Review?", ["Proceed", "Cancel"]) == 0


def test_decision_callback_cancel_policy_fails_closed():
    """Explicit cancel policy should select the safest final option."""
    from skills_router.cli import _decision_callback_for_install

    callback = _decision_callback_for_install(
        argparse.Namespace(yes=False, decision_policy="cancel")
    )

    assert callback("Review?", ["Proceed", "Cancel"]) == 1


def test_decision_callback_approve_prefers_requested_workspace_scope():
    """Auto-approve should not choose global when workspace scope is requested."""
    from skills_router.cli import _decision_callback_for_install

    callback = _decision_callback_for_install(
        argparse.Namespace(
            yes=True,
            decision_policy="prompt",
            scope="workspace:codex-local",
        )
    )

    assert callback(
        "Decision: new capability scope",
        ["Install globally", "Install for this workspace only", "Cancel"],
    ) == 1


@patch("skills_router.layers.registry_resolver.RegistryResolver.resolve")
@patch("skills_router.cli._build_store")
@patch("skills_router.cli.SkillsRouterOrchestrator")
def test_cmd_install_json_dry_run(
    mock_orchestrator, mock_build_store, mock_resolve, tmp_path, capsys
):
    """Install supports machine-readable dry-run output for local agents."""
    from skills_router.cli import cmd_install

    config = SkillsRouterConfig(data_dir=str(tmp_path))
    args = argparse.Namespace(
        manifest="my-weather-tool",
        scope="global",
        user="cli-user",
        yes=True,
        decision_policy="prompt",
        dry_run=True,
        explain=False,
        json_output=True,
    )
    manifest_data = {"tool_id": "my-weather-tool", "name": "Weather", "version": "1.0.0"}
    mock_resolve.return_value = manifest_data

    mock_inst = MagicMock()
    mock_inst.install.return_value = {
        "status": "DRY_RUN_APPROVED",
        "tool_id": "my-weather-tool",
        "wg_case": "CASE_1",
        "decision": "APPROVE",
    }
    mock_orchestrator.return_value = mock_inst

    rc = cmd_install(args, config)

    assert rc == 0
    mock_inst.install.assert_called_once_with(
        manifest_data,
        scope="global",
        user_id="cli-user",
        dry_run=True,
    )
    assert '"status": "DRY_RUN_APPROVED"' in capsys.readouterr().out


@patch("skills_router.cli.cmd_list")
@patch("sys.argv", ["skills-router", "/skills-router", "list"])
def test_main_strips_slash_skills_router_prefix(mock_cmd_list):
    """Test that '/skills-router' is stripped if passed as first arg after binary."""
    from skills_router.cli import main
    main()
    mock_cmd_list.assert_called_once()


@patch("skills_router.cli.cmd_install")
@patch("sys.argv", ["skills-router", "/skills-router", "/install", "weather-tool"])
def test_main_strips_slash_skills_router_and_slash_subcommand(mock_cmd_install):
    """Test that '/skills-router /install' normalises to standard command 'install'."""
    from skills_router.cli import main
    main()
    mock_cmd_install.assert_called_once()


@patch("skills_router.cli.cmd_install")
@patch("sys.argv", ["skills-router", "/install", "weather-tool"])
def test_main_strips_subcommand_slash(mock_cmd_install):
    """Test that a standalone '/install' subcommand is normalised to 'install'."""
    from skills_router.cli import main
    main()
    mock_cmd_install.assert_called_once()


@patch("skills_router.cli.cmd_watch")
@patch("sys.argv", ["skills-router", "/skills-router", "watch", "--once", "--interval", "5"])
def test_main_parses_slash_watch_options(mock_cmd_watch):
    """Test that local slash-style watch commands can pass daemon options."""
    from skills_router.cli import main

    main()

    args = mock_cmd_watch.call_args[0][0]
    assert args.once is True
    assert args.interval == 5


@patch("skills_router.cli.cmd_index")
@patch("sys.argv", ["skills-router", "/skills-router", "/index"])
def test_main_strips_slash_index(mock_cmd_index):
    """Test that '/skills-router /index' normalises to standard command 'index'."""
    from skills_router.cli import main

    main()

    mock_cmd_index.assert_called_once()


@patch("skills_router.cli.cmd_uninstall")
@patch("sys.argv", ["skills-router", "/skills-router", "/uninstall", "writer-pack"])
def test_main_strips_slash_uninstall(mock_cmd_uninstall):
    """Test that '/skills-router /uninstall' normalises to uninstall."""
    from skills_router.cli import main

    main()

    mock_cmd_uninstall.assert_called_once()


@patch("skills_router.cli.cmd_refine")
@patch("sys.argv", ["skills-router", "/skills-router", "/refine", "writer-pack", "engram"])
def test_main_strips_slash_refine(mock_cmd_refine):
    """Test that '/skills-router /refine' normalises to refine."""
    from skills_router.cli import main

    main()

    args = mock_cmd_refine.call_args[0][0]
    assert args.skillsets == ["writer-pack", "engram"]


@patch("skills_router.cli.cmd_route")
@patch("sys.argv", ["skills-router", "/skills-router", "/route", "draft", "article"])
def test_main_strips_slash_route(mock_cmd_route):
    """Test that '/skills-router /route' normalises to route."""
    from skills_router.cli import main

    main()

    args = mock_cmd_route.call_args[0][0]
    assert args.text == ["draft", "article"]


@patch("skills_router.cli.cmd_status")
@patch("sys.argv", ["skills-router", "/skills-router", "/status"])
def test_main_strips_slash_status(mock_cmd_status):
    """Test that '/skills-router /status' normalises to status."""
    from skills_router.cli import main

    main()

    mock_cmd_status.assert_called_once()


@patch("skills_router.cli.cmd_connect")
@patch("sys.argv", ["skills-router", "/skills-router", "/connect", "--target", "codex"])
def test_main_strips_slash_connect(mock_cmd_connect):
    """Test that '/skills-router /connect' normalises to connect."""
    from skills_router.cli import main

    main()

    mock_cmd_connect.assert_called_once()


@patch("skills_router.cli.cmd_analyze")
@patch("sys.argv", ["skills-router", "/skills-router", "/analyze", "github:owner/repo"])
def test_main_strips_slash_analyze(mock_cmd_analyze):
    """Test that '/skills-router /analyze' normalises to analyze."""
    from skills_router.cli import main

    main()

    mock_cmd_analyze.assert_called_once()


@patch("skills_router.layers.registry_resolver.RegistryResolver.resolve")
@patch("skills_router.cli._build_store")
@patch("skills_router.cli.SkillsRouterOrchestrator")
def test_cmd_install_resolves_unseen_path_via_registry(
    mock_orchestrator, mock_build_store, mock_resolve, tmp_path
):
    """Test that cmd_install resolves manifest strings via RegistryResolver."""
    from skills_router.cli import cmd_install

    config = SkillsRouterConfig(data_dir=str(tmp_path))
    args = argparse.Namespace(manifest="my-weather-tool", scope="global", user="cli-user")
    args.yes = False
    args.decision_policy = "prompt"
    args.dry_run = False
    args.explain = False
    args.json_output = False

    # Mock resolver response
    manifest_data = {"tool_id": "my-weather-tool", "name": "Weather", "version": "1.0.0"}
    mock_resolve.return_value = manifest_data

    # Setup orchestrator mock return
    mock_inst = MagicMock()
    mock_inst.install.return_value = {
        "status": "INSTALLED",
        "tool_id": "my-weather-tool",
        "wg_case": "CASE_1",
        "decision": "APPROVE",
    }
    mock_orchestrator.return_value = mock_inst

    rc = cmd_install(args, config)

    # Assert resolver was called to fetch the tool name
    mock_resolve.assert_called_once_with("my-weather-tool")

    # Assert orchestrator install was called with the fetched manifest dictionary
    mock_inst.install.assert_called_once_with(
        manifest_data,
        scope="global",
        user_id="cli-user",
        dry_run=False,
    )
    assert rc == 0


@patch("skills_router.layers.source_analyzer.SourceAnalyzer.analyze")
@patch("skills_router.layers.registry_resolver.RegistryResolver.resolve")
@patch("skills_router.cli._build_store")
@patch("skills_router.cli.SkillsRouterOrchestrator")
def test_cmd_install_source_link_falls_back_to_inferred_manifest(
    mock_orchestrator, mock_build_store, mock_resolve, mock_analyze, tmp_path, capsys
):
    """Supported source links can be inferred when no manifest exists."""
    from skills_router.cli import cmd_install
    from skills_router.layers.registry_resolver import RegistryResolutionError

    config = SkillsRouterConfig(data_dir=str(tmp_path))
    args = argparse.Namespace(
        manifest="github:owner/repo",
        scope="global",
        all_agents=False,
        agent_targets=None,
        user="cli-user",
        yes=True,
        decision_policy="prompt",
        dry_run=True,
        explain=False,
        json_output=True,
        package_type="skillset",
        routing_mode="full_package",
        infer=False,
    )
    manifest_data = {
        "tool_id": "repo",
        "name": "Repo",
        "version": "1.0.0",
        "agent_package": {
            "type": "skillset",
            "skillsets": [
                {"id": "default", "name": "Repo", "use_when": "repo tasks"}
            ],
        },
    }
    mock_resolve.side_effect = RegistryResolutionError("No skills-router.json")
    mock_analyze.return_value = {
        "status": "OK",
        "manifest": manifest_data,
        "source": {"identifier": "owner/repo"},
        "evidence": {},
    }
    mock_inst = MagicMock()
    mock_inst.install.return_value = {
        "status": "DRY_RUN_APPROVED",
        "tool_id": "repo",
        "wg_case": "CASE_1",
        "decision": "APPROVE",
    }
    mock_orchestrator.return_value = mock_inst

    rc = cmd_install(args, config)

    assert rc == 0
    mock_analyze.assert_called_once_with("github:owner/repo")
    mock_inst.install.assert_called_once_with(
        manifest_data,
        scope="global",
        user_id="cli-user",
        dry_run=True,
    )
    assert '"source_analysis": {' in capsys.readouterr().out


def test_cmd_connect_json_outputs_mcp_config(tmp_path, capsys):
    """Connect command renders machine-readable setup for an agent host."""
    from skills_router.cli import cmd_connect

    config = SkillsRouterConfig(data_dir=str(tmp_path / "data"))
    config.workspace_root = str(tmp_path)
    args = argparse.Namespace(
        target="codex",
        agent_id="codex-local",
        detail="compact",
        from_source=False,
        write_instructions=False,
        instruction_file=None,
        json_output=True,
    )

    rc = cmd_connect(args, config)

    assert rc == 0
    out = capsys.readouterr().out
    assert '"target": "codex"' in out
    assert '"command": "skills-router"' in out
    assert '"bridge_prompt": ' in out


def test_cmd_connect_dry_run_does_not_write_instruction_file(tmp_path, capsys):
    """Connect dry-run previews instruction writes without touching disk."""
    from skills_router.cli import cmd_connect

    config = SkillsRouterConfig(data_dir=str(tmp_path / "data"))
    config.workspace_root = str(tmp_path)
    instruction_file = "AGENTS.md"
    args = argparse.Namespace(
        target="codex",
        agent_id="codex-local",
        detail="compact",
        from_source=False,
        write_instructions=True,
        instruction_file=instruction_file,
        dry_run=True,
        json_output=True,
    )

    rc = cmd_connect(args, config)

    assert rc == 0
    assert not (tmp_path / instruction_file).exists()
    out = capsys.readouterr().out
    assert '"status": "DRY_RUN"' in out
    assert '"action": "would_create"' in out


def test_cmd_connect_write_skill_creates_skill_file(tmp_path, capsys):
    """Connect can inject the bridge as a target agent skill."""
    from skills_router.cli import cmd_connect

    config = SkillsRouterConfig(data_dir=str(tmp_path / "data"))
    config.workspace_root = str(tmp_path)
    args = argparse.Namespace(
        target="codex-vscode",
        agent_id="codex-ide-local",
        detail="compact",
        from_source=False,
        write_instructions=False,
        write_skill=True,
        instruction_file=None,
        skill_dir=None,
        dry_run=False,
        json_output=True,
    )

    rc = cmd_connect(args, config)

    assert rc == 0
    skill_path = tmp_path / ".codex" / "skills" / "skills-router" / "SKILL.md"
    assert skill_path.exists()
    text = skill_path.read_text(encoding="utf-8")
    assert "name: skills-router" in text
    assert "OpenAI Codex IDE Extension" in text
    out = capsys.readouterr().out
    assert '"written_skill": {' in out
    assert '"target": "codex-ide"' in out


def test_cmd_uninstall_dry_run_preserves_tool(tmp_path, capsys):
    """Uninstall dry-run should not delete Brain Index records."""
    from skills_router.cli import cmd_uninstall
    from skills_router.storage.memory_store import MemoryBrainIndexStore

    config = SkillsRouterConfig(data_dir=str(tmp_path))
    store = MemoryBrainIndexStore(
        brain_index_path=config.brain_index_path,
        dep_graph_path=config.dep_graph_path,
    )
    store.save_tool({
        "tool_id": "writer-pack",
        "name": "Writer Pack",
        "version": "1.0.0",
    })
    args = argparse.Namespace(
        tool_id="writer-pack",
        user="cli-user",
        scope=None,
        dry_run=True,
        json_output=True,
    )

    rc = cmd_uninstall(args, config)

    refreshed = MemoryBrainIndexStore(
        brain_index_path=config.brain_index_path,
        dep_graph_path=config.dep_graph_path,
    )
    assert rc == 0
    assert refreshed.get_tool("writer-pack") is not None
    out = capsys.readouterr().out
    assert '"status": "DRY_RUN_UNINSTALLED"' in out
    assert '"dry_run": true' in out


@patch("skills_router.layers.registry_resolver.RegistryResolver.resolve")
@patch("skills_router.cli._build_store")
@patch("skills_router.cli.SkillsRouterOrchestrator")
def test_cmd_install_all_agents_forces_global_routing(
    mock_orchestrator, mock_build_store, mock_resolve, tmp_path, capsys
):
    """--all-agents installs once globally and records requested targets."""
    from skills_router.cli import cmd_install

    config = SkillsRouterConfig(data_dir=str(tmp_path))
    args = argparse.Namespace(
        manifest="writer-pack",
        scope="workspace:codex-local",
        all_agents=True,
        agent_targets=["codex,cursor"],
        user="cli-user",
        yes=True,
        decision_policy="prompt",
        dry_run=False,
        explain=False,
        json_output=True,
        package_type="skillset",
        routing_mode="full_package",
    )
    manifest_data = {
        "tool_id": "writer-pack",
        "name": "Writer Pack",
        "version": "1.0.0",
    }
    mock_resolve.return_value = manifest_data
    mock_inst = MagicMock()
    mock_inst.install.return_value = {
        "status": "INSTALLED",
        "tool_id": "writer-pack",
        "wg_case": "CASE_1",
        "decision": "APPROVE",
    }
    mock_orchestrator.return_value = mock_inst

    rc = cmd_install(args, config)

    assert rc == 0
    mock_inst.install.assert_called_once_with(
        manifest_data,
        scope="global",
        user_id="cli-user",
        dry_run=False,
    )
    out = capsys.readouterr().out
    assert '"target_names": [' in out
    assert '"codex"' in out
    assert '"cursor"' in out


def test_cmd_prompt_json_for_target(tmp_path, capsys):
    """Prompt command renders target-specific bridge guidance."""
    from skills_router.cli import cmd_prompt

    config = SkillsRouterConfig(data_dir=str(tmp_path))
    args = argparse.Namespace(
        target="hermes",
        agent_id="hermes-local",
        list=False,
        json_output=True,
    )

    rc = cmd_prompt(args, config)

    assert rc == 0
    out = capsys.readouterr().out
    assert '"target": "hermes"' in out
    assert "Hermes Agent" in out


def test_cmd_chat_parse_only_handles_for_me(tmp_path, capsys):
    """Chat command parses natural filler words without executing."""
    from skills_router.cli import cmd_chat

    config = SkillsRouterConfig(data_dir=str(tmp_path))
    args = argparse.Namespace(
        text=["/skills-router install weather-tool for me"],
        target="codex",
        agent_id="codex-local",
        scope=None,
        parse_only=True,
        json_output=True,
    )

    rc = cmd_chat(args, config)

    assert rc == 0
    out = capsys.readouterr().out
    assert '"command": "install"' in out
    assert '"scope": "workspace:codex-local"' in out
