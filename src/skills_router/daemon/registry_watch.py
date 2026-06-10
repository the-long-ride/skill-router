"""Registry Watch Daemon — v5.

Direct implementation of blueprint §11.  Includes:
- Overlap detection on hash changes with admin-channel fallback
- Trust degradation re-check with hysteresis band
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone

from skills_router.metrics import REGISTRY as METRICS

logger = logging.getLogger(__name__)


class RegistryWatchDaemon:
    """Background daemon that monitors the tool registry for drift and trust changes."""

    CHECK_INTERVAL_SECONDS = 3600
    SOFT_WARN_THRESHOLD = 0.65    # must stay in sync with TrustGate
    HYSTERESIS_BAND = 0.05        # score must recover above threshold + band

    def __init__(
        self,
        evaluator,
        trust_gate,
        brain_index_db,
        wg_notifier,
        live_signal_fetcher,
        admin_channel_id: str = "system-admin",
        check_interval_seconds: int | None = None,
        soft_warn_threshold: float | None = None,
        hysteresis_band: float | None = None,
        state_path: str | None = None,
        dry_run: bool = False,
    ):
        self.evaluator = evaluator
        self.trust_gate = trust_gate
        self.db = brain_index_db
        self.wg_notifier = wg_notifier
        self.fetcher = live_signal_fetcher
        self.admin_channel_id = admin_channel_id
        self.check_interval_seconds = (
            self.CHECK_INTERVAL_SECONDS
            if check_interval_seconds is None
            else check_interval_seconds
        )
        self.soft_warn_threshold = (
            self.SOFT_WARN_THRESHOLD
            if soft_warn_threshold is None
            else soft_warn_threshold
        )
        self.hysteresis_band = (
            self.HYSTERESIS_BAND
            if hysteresis_band is None
            else hysteresis_band
        )
        self.state_path = state_path
        self.dry_run = dry_run
        self._last_hashes: dict[str, str] = {}
        self._degraded_tools: set[str] = set()
        self._running = False
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Launch the daemon in a background thread."""
        if self._running:
            logger.warning("RegistryWatchDaemon already running")
            return
        if not self._load_state():
            self._seed_hashes()
        self._stop_event.clear()
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info("RegistryWatchDaemon started")

    def _seed_hashes(self) -> None:
        """Initialize hash baselines so the first cycle does not fire false alarms."""
        tools = self.db.get_all_tools()
        self._last_hashes = {
            t["tool_id"]: t.get("layer_4_telemetry", {}).get(
                "last_known_stable_state_hash", ""
            )
            for t in tools
        }

    def _load_state(self) -> bool:
        """Load persisted daemon state if available."""
        if not self.state_path or not os.path.exists(self.state_path):
            return False
        try:
            with open(self.state_path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            logger.warning("Failed to load RegistryWatchDaemon state")
            return False

        hashes = data.get("last_hashes", {})
        degraded = data.get("degraded_tools", [])
        if isinstance(hashes, dict):
            self._last_hashes = {str(k): str(v) for k, v in hashes.items()}
        if isinstance(degraded, list):
            self._degraded_tools = {str(tool_id) for tool_id in degraded}
        return True

    def _save_state(self) -> None:
        """Persist daemon state for future one-shot or daemon runs."""
        if not self.state_path:
            return
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        tmp_path = f"{self.state_path}.tmp"
        data = {
            "version": 1,
            "last_hashes": self._last_hashes,
            "degraded_tools": sorted(self._degraded_tools),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        try:
            os.replace(tmp_path, self.state_path)
        except PermissionError:
            with open(self.state_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    def stop(self, timeout: float | None = None) -> None:
        """Signal the daemon to stop."""
        self._running = False
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        logger.info("RegistryWatchDaemon stop requested")

    def _loop(self) -> None:
        while self._running:
            try:
                self._check_for_drift(dry_run=self.dry_run)
                self._check_trust_degradation(dry_run=self.dry_run)
                if not self.dry_run:
                    self._save_state()
                METRICS.inc("registry_watch_cycles_total")
                METRICS.set("registry_watch_degraded_tools", len(self._degraded_tools))
                METRICS.set("registry_watch_tools_checked", len(self.db.get_all_tools()))
            except Exception:
                METRICS.inc("registry_watch_errors_total")
                logger.exception("RegistryWatchDaemon cycle error")
            self._stop_event.wait(self.check_interval_seconds)

    # -- Overlap check --------------------------------------------------------

    def _check_for_drift(self, *, dry_run: bool = False) -> int:
        """Detect tools whose state hash has changed and check for new overlaps."""
        tools = self.db.get_all_tools()
        current_hashes = {
            t["tool_id"]: t.get("layer_4_telemetry", {}).get(
                "last_known_stable_state_hash", ""
            )
            for t in tools
        }
        changed = [
            tid
            for tid, h in current_hashes.items()
            if self._last_hashes.get(tid) != h
        ]
        if not changed:
            if not dry_run:
                self._last_hashes = current_hashes
            return 0

        notifications = 0
        for tool_id in changed:
            tool = self.db.get_tool(tool_id)
            if tool is None:
                continue
            scope = tool.get("layer_meta", {}).get("install_scope", "global")
            agent_id = tool.get("layer_meta", {}).get("agent_id")

            result = self.evaluator.evaluate(
                tool, scope, brain_index=self.db.get_all_tools()
            )
            if result["status"] == "OVERLAP_DETECTED":
                notifications += 1
                if dry_run:
                    continue
                target_channel = agent_id or self.admin_channel_id  # v5: admin fallback
                self.wg_notifier.send(
                    agent_id=target_channel,
                    title=f"Post-update overlap detected: {tool_id}",
                    body=(
                        f"After a recent update, {tool_id} now overlaps with "
                        f"{result['top_match']['name']} "
                        f"(score: {result['top_match']['score']:.2f})."
                    ),
                    action="OPEN_WG_REVIEW",
                )

        if not dry_run:
            self._last_hashes = current_hashes
        return notifications

    # -- Trust re-check with hysteresis ---------------------------------------

    def _check_trust_degradation(self, *, dry_run: bool = False) -> tuple[int, list[str]]:
        """Re-evaluate trust scores and alert on degradation (with hysteresis)."""
        notifications = 0
        would_degrade: list[str] = []
        for tool in self.db.get_all_tools():
            tool_id = tool["tool_id"]
            agent_id = tool.get("layer_meta", {}).get("agent_id")
            prov = tool.get("layer_5_provenance", {})
            score_at_install = prov.get("trust_score", 1.0)

            # Fetch live signals and re-evaluate trust
            live_manifest = self.fetcher.fetch(tool)
            result = self.trust_gate.evaluate(live_manifest)
            current_score = result["score"]

            if not dry_run:
                self.db.update_trust(tool_id, current_score)

            currently_degraded = tool_id in self._degraded_tools

            if (
                not currently_degraded
                and current_score < self.soft_warn_threshold
            ):
                # Newly entering degraded state — fire alert
                notifications += 1
                if dry_run:
                    would_degrade.append(tool_id)
                    continue
                self._degraded_tools.add(tool_id)
                target_channel = agent_id or self.admin_channel_id
                self.wg_notifier.send(
                    agent_id=target_channel,
                    title=f"Trust degraded: {tool.get('name', tool_id)} ({tool_id})",
                    body=(
                        f"Approved at {score_at_install:.2f}, "
                        f"now {current_score:.2f}. "
                        f"Review recommended."
                    ),
                    action="OPEN_TRUST_DEGRADED_WG",
                    wg_case="CASE_TRUST_DEGRADED",
                    payload={
                        "tool_id": tool_id,
                        "trust_score_at_install": score_at_install,
                        "current_score": current_score,
                        "trust_score_last_evaluated": prov.get(
                            "trust_score_last_evaluated"
                        ),
                    },
                )
            elif (
                currently_degraded
                and current_score
                >= self.soft_warn_threshold + self.hysteresis_band
            ):
                # Score recovered past hysteresis band — clear degraded state
                if dry_run:
                    continue
                self._degraded_tools.discard(tool_id)
                logger.info(
                    "Trust recovered for %s (score=%.2f), clearing degraded state",
                    tool_id,
                    current_score,
                )
        return notifications, sorted(would_degrade)

    # -- Single-cycle for testing ---------------------------------------------

    def run_once(self, seed_hashes: bool = False, dry_run: bool = False) -> dict:
        """Run a single check cycle (useful for testing)."""
        loaded_state = False
        if seed_hashes:
            loaded_state = self._load_state()
            if not loaded_state:
                self._seed_hashes()
        before_count = _history_count(self.wg_notifier)
        drift_notifications = self._check_for_drift(dry_run=dry_run)
        trust_notifications, would_degrade = self._check_trust_degradation(
            dry_run=dry_run
        )
        if not dry_run:
            self._save_state()
        after_count = _history_count(self.wg_notifier)
        notifications_sent = max(0, after_count - before_count)
        METRICS.inc("registry_watch_cycles_total")
        METRICS.inc("registry_watch_notifications_total", notifications_sent)
        METRICS.set("registry_watch_degraded_tools", len(self._degraded_tools))
        METRICS.set("registry_watch_tools_checked", len(self.db.get_all_tools()))
        return {
            "status": "OK",
            "dry_run": dry_run,
            "tools_checked": len(self.db.get_all_tools()),
            "notifications_sent": notifications_sent,
            "notifications_would_send": (
                drift_notifications + trust_notifications if dry_run else 0
            ),
            "degraded_tools": sorted(self._degraded_tools),
            "would_degrade_tools": would_degrade,
            "state_loaded": loaded_state,
            "state_saved": not dry_run,
        }


def _history_count(notifier) -> int:
    history = getattr(notifier, "history", [])
    if callable(history):
        history = history()
    return len(history)
