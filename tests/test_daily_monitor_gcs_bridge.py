"""
Unit tests for `daily_monitor.DailyMonitor._run_gcs_bridge`.

The method shells out to the bridge script and then re-reads
`alerts_latest.json` to surface stats + push to Chroma. Both
behaviors are pinned here with subprocess + the snapshot file
overridden to a tmp path, so the test runs offline and doesn't
depend on the real GCS bucket.

Run:
    pytest tests/test_daily_monitor_gcs_bridge.py -v
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from daily_monitor import DailyMonitor


@pytest.fixture
def monitor(tmp_path):
    """DailyMonitor with paths redirected to tmp so tests don't
    mutate the real `guardian_poc/data/alerts_latest.json`."""
    # DataAgent's constructor pulls in crawler config we don't need;
    # patch it out for these unit tests.
    with patch("daily_monitor.DataAgent"):
        m = DailyMonitor()
        # Redirect the snapshot path so reads during the test land
        # on a tmp file we control.
        m.snapshot_path = tmp_path / "alerts_latest.json"
        # Bridge script path stays real; missing-script tests
        # override it explicitly when needed.
        yield m


def _completed(returncode=0, stdout="", stderr=""):
    """Build a CompletedProcess-shaped object for subprocess.run mocks."""
    p = MagicMock()
    p.returncode = returncode
    p.stdout = stdout
    p.stderr = stderr
    return p


def _snapshot_json(event_count=10, alert_count=12, triggered_count=5):
    """Match the shape `_build_snapshot` writes (the bridge contract)."""
    return {
        "event_count":     event_count,
        "alert_count":     alert_count,
        "triggered_count": triggered_count,
        "data_mode":       "bridge_gcs_raw_to_scored",
        "alerts":          [{"score": 50, "title": "x"} for _ in range(alert_count)],
    }


class TestMissingBridgeScript:
    def test_missing_script_returns_skipped(self, monitor):
        """If the bridge script doesn't exist (e.g. someone deleted
        it locally), daily_monitor MUST NOT crash — return a
        `skipped` status instead so the rest of the daily run
        completes."""
        monitor.gcs_bridge_path = Path("/nonexistent/bridge.py")
        out = monitor._run_gcs_bridge(verbose=False)
        assert out["status"] == "skipped"
        assert out["alerts_count"] == 0


class TestHappyPath:
    def test_success_populates_counts_from_snapshot_file(self, monitor):
        """Stub subprocess to return rc=0 and pre-write the snapshot
        file the bridge would have produced. Confirm the method
        reads counts from that file (not from stdout — decoupled
        by design)."""
        monitor.snapshot_path.write_text(json.dumps(_snapshot_json(
            event_count=229, alert_count=162, triggered_count=58,
        )))
        with patch("subprocess.run", return_value=_completed(returncode=0)):
            with patch.object(monitor, "_mirror_alerts_to_chroma",
                              return_value=0):
                out = monitor._run_gcs_bridge(verbose=False)

        assert out["status"] == "success"
        assert out["events_count"] == 229
        assert out["alerts_count"] == 162
        assert out["triggered_count"] == 58
        assert out["data_mode"] == "bridge_gcs_raw_to_scored"

    def test_calls_chroma_mirror_after_success(self, monitor):
        """The chroma mirror is what makes the fresh GCS data visible
        to semantic retrieval. If we forget to call it the dashboard
        regresses — pin the call."""
        monitor.snapshot_path.write_text(json.dumps(_snapshot_json()))

        with patch("subprocess.run", return_value=_completed(returncode=0)):
            with patch.object(monitor, "_mirror_alerts_to_chroma",
                              return_value=7) as mirror:
                out = monitor._run_gcs_bridge(verbose=False)

        assert mirror.called
        assert out["chroma_mirrored"] == 7

    def test_subprocess_invoked_with_python_and_bridge_path(self, monitor):
        """The subprocess invocation should target the same python
        interpreter (so the venv used to run daily_monitor is used)
        and point at the configured bridge path."""
        monitor.snapshot_path.write_text(json.dumps(_snapshot_json()))
        with patch("subprocess.run", return_value=_completed(returncode=0)) as run:
            with patch.object(monitor, "_mirror_alerts_to_chroma", return_value=0):
                monitor._run_gcs_bridge(verbose=False)
        cmd = run.call_args.args[0]
        assert cmd[0] == sys.executable
        assert cmd[1] == str(monitor.gcs_bridge_path)


class TestFailurePaths:
    def test_nonzero_exit_returns_error(self, monitor):
        """A failing subprocess (rc != 0) must produce status=error
        with the captured stderr exposed for debugging — NOT crash
        the daily monitor and NOT update counts to fake values."""
        with patch("subprocess.run", return_value=_completed(
            returncode=1, stderr="GCS auth failed", stdout="",
        )):
            out = monitor._run_gcs_bridge(verbose=False)
        assert out["status"] == "error"
        assert "GCS auth failed" in (out.get("message") or "")
        assert out["alerts_count"] == 0

    def test_timeout_returns_error(self, monitor):
        """A bridge that hangs > 60s must trip timeout and be reported
        as an error so cron logs surface the regression rather than
        the run silently blocking forever."""
        with patch("subprocess.run",
                   side_effect=subprocess.TimeoutExpired(cmd="x", timeout=60)):
            out = monitor._run_gcs_bridge(verbose=False)
        assert out["status"] == "error"
        assert "timed out" in (out.get("message") or "").lower()

    def test_subprocess_exception_returns_error(self, monitor):
        """Defence in depth: any unexpected exception from subprocess
        must convert to a structured error, not propagate."""
        with patch("subprocess.run", side_effect=OSError("fork bomb")):
            out = monitor._run_gcs_bridge(verbose=False)
        assert out["status"] == "error"
        assert "fork bomb" in (out.get("message") or "")

    def test_missing_snapshot_after_success_does_not_crash(self, monitor):
        """If the bridge claims success but didn't actually write the
        snapshot file, daily_monitor must still return success with
        zeroed counts (and not raise on the missing file)."""
        # monitor.snapshot_path is intentionally not created.
        assert not monitor.snapshot_path.exists()
        with patch("subprocess.run", return_value=_completed(returncode=0)):
            with patch.object(monitor, "_mirror_alerts_to_chroma",
                              return_value=0):
                out = monitor._run_gcs_bridge(verbose=False)
        assert out["status"] == "success"
        assert out["alerts_count"] == 0
        assert out["events_count"] == 0

    def test_corrupt_snapshot_does_not_crash(self, monitor):
        """If the snapshot exists but isn't valid JSON, the read step
        must be defensive — don't propagate a JSONDecodeError up the
        run() call chain and abort the whole daily monitor."""
        monitor.snapshot_path.write_text("{ not valid json ")
        with patch("subprocess.run", return_value=_completed(returncode=0)):
            with patch.object(monitor, "_mirror_alerts_to_chroma",
                              return_value=0):
                out = monitor._run_gcs_bridge(verbose=False)
        assert out["status"] == "success"
        assert out["alerts_count"] == 0
