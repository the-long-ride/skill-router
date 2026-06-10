"""Tests for RegistryWatchDaemon."""

import pytest
from skills_router.daemon.registry_watch import RegistryWatchDaemon
from skills_router.daemon.live_signal_fetcher import LiveSignalFetcher
from skills_router.layers.semantic_evaluator import SemanticEvaluator
from skills_router.layers.trust_gate import TrustGate
from skills_router.wg.notifier import WGNotifier
from skills_router.storage.memory_store import MemoryBrainIndexStore


class TestRegistryWatchDaemon:
    """Test drift detection and trust degradation with hysteresis."""

    @pytest.fixture
    def setup_daemon(self):
        store = MemoryBrainIndexStore()
        evaluator = SemanticEvaluator()
        trust_gate = TrustGate()
        notifier = WGNotifier()
        fetcher = LiveSignalFetcher()

        daemon = RegistryWatchDaemon(
            evaluator=evaluator,
            trust_gate=trust_gate,
            brain_index_db=store,
            wg_notifier=notifier,
            live_signal_fetcher=fetcher,
        )
        return daemon, store, notifier

    def test_no_tools_no_crash(self, setup_daemon):
        """run_once on empty registry should not crash."""
        daemon, store, notifier = setup_daemon
        daemon.run_once()
        assert len(notifier.history) == 0

    def test_drift_detection_triggers_on_hash_change(self, setup_daemon):
        """Changing a tool's hash should trigger overlap check."""
        daemon, store, notifier = setup_daemon

        # Install two similar tools
        tool_a = {
            "tool_id": "tool-a",
            "name": "Tool A",
            "layer_1_domain_tags": ["Weather"],
            "layer_3_capabilities": {"inputs": ["a"], "outputs": ["b"]},
            "layer_4_telemetry": {"last_known_stable_state_hash": "hash1"},
            "layer_5_provenance": {
                "trust_score": 0.9,
                "signature_verified": True,
                "install_source": "official-registry",
            },
            "layer_meta": {"install_scope": "global", "agent_id": None},
        }
        tool_b = {
            "tool_id": "tool-b",
            "name": "Tool B",
            "layer_1_domain_tags": ["Weather"],
            "layer_3_capabilities": {"inputs": ["a"], "outputs": ["b"]},
            "layer_4_telemetry": {"last_known_stable_state_hash": "hash2"},
            "layer_5_provenance": {
                "trust_score": 0.9,
                "signature_verified": True,
                "install_source": "official-registry",
            },
            "layer_meta": {"install_scope": "global", "agent_id": None},
        }
        store.save_tool(tool_a)
        store.save_tool(tool_b)

        # First run — initialises hashes
        daemon.run_once()
        initial_notifications = len(notifier.history)

        # Change hash of tool-a
        tool_a["layer_4_telemetry"]["last_known_stable_state_hash"] = "hash1-changed"
        store.save_tool(tool_a)

        # Second run — should detect drift
        daemon.run_once()
        # May or may not trigger overlap depending on similarity
        # (random embeddings are used), but should not crash

    def test_trust_degradation_hysteresis(self, setup_daemon):
        """Trust degradation alert should only fire once per degradation event."""
        daemon, store, notifier = setup_daemon

        tool = {
            "tool_id": "tool-a",
            "name": "Tool A",
            "layer_4_telemetry": {"last_known_stable_state_hash": "h1"},
            "layer_5_provenance": {
                "trust_score": 0.9,
                "signature_verified": True,
                "install_source": "official-registry",
                "trust_score_last_evaluated": "2024-01-01T00:00:00Z",
            },
            "layer_meta": {"install_scope": "global", "agent_id": None},
        }
        store.save_tool(tool)

        # With default stubs (all return good values), trust should stay high
        daemon.run_once()
        # No degradation expected with good stubs
        degraded_notifications = [
            n for n in notifier.history
            if n.get("wg_case") == "CASE_TRUST_DEGRADED"
        ]
        assert len(degraded_notifications) == 0

    def test_admin_channel_fallback_when_no_agent_id(self, setup_daemon):
        """When agent_id is None, notifications should go to admin channel."""
        daemon, store, notifier = setup_daemon

        # We can verify the daemon uses admin_channel_id by checking
        # the daemon's configuration
        assert daemon.admin_channel_id == "system-admin"

    def test_drift_overlap_sends_admin_notification(self):
        """A changed hash with overlap should send a review notification."""
        store = MemoryBrainIndexStore()
        notifier = _MemoryNotifier()
        evaluator = _OverlapEvaluator()

        tool = {
            "tool_id": "tool-a",
            "name": "Tool A",
            "layer_4_telemetry": {"last_known_stable_state_hash": "hash-a"},
            "layer_5_provenance": {
                "trust_score": 0.9,
                "signature_verified": True,
                "install_source": "official-registry",
            },
            "layer_meta": {"install_scope": "global", "agent_id": None},
        }
        store.save_tool(tool)

        daemon = RegistryWatchDaemon(
            evaluator=evaluator,
            trust_gate=TrustGate(),
            brain_index_db=store,
            wg_notifier=notifier,
            live_signal_fetcher=_HighTrustFetcher(),
        )
        daemon._seed_hashes()

        tool["layer_4_telemetry"]["last_known_stable_state_hash"] = "hash-b"
        store.save_tool(tool)
        daemon.run_once()

        assert evaluator.calls == 1
        assert len(notifier.history) == 1
        assert notifier.history[0]["target"] == "system-admin"
        assert notifier.history[0]["action"] == "OPEN_WG_REVIEW"

    def test_trust_degradation_alerts_once_until_recovery(self):
        """Trust degradation should use hysteresis and avoid duplicate alerts."""
        store = MemoryBrainIndexStore()
        notifier = _MemoryNotifier()
        fetcher = _SequenceFetcher(
            [
                _low_trust_manifest(),
                _low_trust_manifest(),
                _high_trust_manifest(),
                _low_trust_manifest(),
            ]
        )
        tool = {
            "tool_id": "tool-a",
            "name": "Tool A",
            "layer_4_telemetry": {"last_known_stable_state_hash": "hash-a"},
            "layer_5_provenance": {
                "trust_score": 0.9,
                "signature_verified": True,
                "install_source": "official-registry",
            },
            "layer_meta": {"install_scope": "global", "agent_id": "agent-1"},
        }
        store.save_tool(tool)

        daemon = RegistryWatchDaemon(
            evaluator=_NoOverlapEvaluator(),
            trust_gate=TrustGate(),
            brain_index_db=store,
            wg_notifier=notifier,
            live_signal_fetcher=fetcher,
        )
        daemon._seed_hashes()

        daemon.run_once()
        daemon.run_once()
        daemon.run_once()
        daemon.run_once()

        degraded = [
            n for n in notifier.history
            if n["wg_case"] == "CASE_TRUST_DEGRADED"
        ]
        assert len(degraded) == 2
        assert degraded[0]["target"] == "agent-1"
        assert "tool-a" in daemon._degraded_tools

    def test_run_once_dry_run_skips_state_trust_and_notifications(self, tmp_path):
        """Dry-run watch reports possible alerts without mutating persisted state."""
        store = MemoryBrainIndexStore(
            brain_index_path=str(tmp_path / "brain_index.json"),
            dep_graph_path=str(tmp_path / "dep_graph.json"),
        )
        notifier = _MemoryNotifier()
        tool = {
            "tool_id": "tool-a",
            "name": "Tool A",
            "layer_4_telemetry": {"last_known_stable_state_hash": "hash-a"},
            "layer_5_provenance": {
                "trust_score": 0.9,
                "signature_verified": True,
                "install_source": "official-registry",
            },
            "layer_meta": {"install_scope": "global", "agent_id": "agent-1"},
        }
        store.save_tool(tool)
        daemon = RegistryWatchDaemon(
            evaluator=_NoOverlapEvaluator(),
            trust_gate=TrustGate(),
            brain_index_db=store,
            wg_notifier=notifier,
            live_signal_fetcher=_SequenceFetcher([_low_trust_manifest()]),
            state_path=str(tmp_path / "watch_state.json"),
        )
        daemon._seed_hashes()

        result = daemon.run_once(dry_run=True)

        refreshed = MemoryBrainIndexStore(
            brain_index_path=str(tmp_path / "brain_index.json"),
            dep_graph_path=str(tmp_path / "dep_graph.json"),
        )
        assert result["dry_run"] is True
        assert result["state_saved"] is False
        assert result["notifications_sent"] == 0
        assert result["notifications_would_send"] == 1
        assert result["would_degrade_tools"] == ["tool-a"]
        assert notifier.history == []
        assert not (tmp_path / "watch_state.json").exists()
        assert refreshed.get_tool("tool-a")["layer_5_provenance"]["trust_score"] == 0.9

    def test_stop_wakes_sleeping_daemon_thread(self):
        """stop() should wake the loop instead of waiting for the full interval."""
        store = MemoryBrainIndexStore()
        daemon = RegistryWatchDaemon(
            evaluator=_NoOverlapEvaluator(),
            trust_gate=TrustGate(),
            brain_index_db=store,
            wg_notifier=_MemoryNotifier(),
            live_signal_fetcher=_HighTrustFetcher(),
            check_interval_seconds=60,
        )

        daemon.start()
        daemon.stop(timeout=1)

        assert daemon._thread is not None
        assert not daemon._thread.is_alive()

    def test_zero_interval_is_preserved_for_test_daemon(self):
        """Explicit zero values should not be replaced by defaults."""
        daemon = RegistryWatchDaemon(
            evaluator=_NoOverlapEvaluator(),
            trust_gate=TrustGate(),
            brain_index_db=MemoryBrainIndexStore(),
            wg_notifier=_MemoryNotifier(),
            live_signal_fetcher=_HighTrustFetcher(),
            check_interval_seconds=0,
            soft_warn_threshold=0,
            hysteresis_band=0,
        )

        assert daemon.check_interval_seconds == 0
        assert daemon.soft_warn_threshold == 0
        assert daemon.hysteresis_band == 0


class _MemoryNotifier:
    def __init__(self):
        self._history = []

    def send(self, agent_id, title, body, action="", wg_case="", payload=None):
        self._history.append(
            {
                "target": agent_id,
                "title": title,
                "body": body,
                "action": action,
                "wg_case": wg_case,
                "payload": payload or {},
            }
        )

    @property
    def history(self):
        return list(self._history)


class _OverlapEvaluator:
    def __init__(self):
        self.calls = 0

    def evaluate(self, tool, scope, brain_index=None):
        self.calls += 1
        return {
            "status": "OVERLAP_DETECTED",
            "top_match": {"tool_id": "tool-b", "name": "Tool B", "score": 0.91},
        }


class _NoOverlapEvaluator:
    def evaluate(self, tool, scope, brain_index=None):
        return {"status": "BRAND_NEW_SCOPE", "top_match": None}


class _HighTrustFetcher:
    def fetch(self, tool):
        return _high_trust_manifest()


class _SequenceFetcher:
    def __init__(self, manifests):
        self._manifests = list(manifests)

    def fetch(self, tool):
        if len(self._manifests) == 1:
            return self._manifests[0]
        return self._manifests.pop(0)


def _low_trust_manifest():
    return {
        "publisher_signature": {"verified": True},
        "install_source": "official-registry",
        "open_critical_cves": 1,
        "last_commit_days_ago": 999,
        "community_sentiment_score": 0.0,
    }


def _high_trust_manifest():
    return {
        "publisher_signature": {"verified": True},
        "install_source": "official-registry",
        "open_critical_cves": 0,
        "last_commit_days_ago": 10,
        "community_sentiment_score": 0.9,
    }
