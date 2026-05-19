"""
Unit tests for core.persona_eval.data_loader.

Two correctness anchors:
  1. _compute_effective_score is the same function generate_actions uses
     at request time. Tests verify our loader's output matches that exact
     scoring (no re-implementation drift).
  2. Window filtering with malformed dates KEEPS rows rather than drops —
     better to include slightly stale signal than throw away half the
     window because some rows have non-ISO dates.

Run:
    pytest tests/test_persona_eval_data.py -v
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.persona_eval import data_loader  # noqa: E402


def _snapshot(tmp_path: Path, alerts) -> Path:
    p = tmp_path / "alerts.json"
    p.write_text(json.dumps({"alerts": alerts}))
    return p


def _now() -> datetime:
    return datetime(2026, 5, 19, tzinfo=timezone.utc)


def _raw_alert(idx: int, *, days_ago: int = 0, **overrides) -> dict:
    pub = (datetime(2026, 5, 19, tzinfo=timezone.utc).fromordinal(
        datetime(2026, 5, 19).toordinal() - days_ago
    )).date().isoformat()
    base = {
        "url":              f"https://example.com/a{idx}",
        "title":             f"Alert {idx}",
        "country":           "USA",
        "category_label":    "Liquidity Risk",
        "score":             50,
        "published_at":      pub,
        "source":            "gdelt",
        "severity":          "Medium",
    }
    base.update(overrides)
    return base


class TestLoadAlertsWindow:
    def test_kept_alerts_within_window(self, tmp_path):
        path = _snapshot(tmp_path, [
            _raw_alert(1, days_ago=5),
            _raw_alert(2, days_ago=10),
            _raw_alert(3, days_ago=45),     # outside 30d window
        ])
        win = data_loader.load_alerts_window(days=30, snapshot_path=path, now=_now())
        assert win.kept_count == 2
        assert win.raw_count == 3
        kept_titles = sorted(a.title for a in win.alerts)
        assert kept_titles == ["Alert 1", "Alert 2"]

    def test_missing_snapshot_returns_empty(self, tmp_path):
        win = data_loader.load_alerts_window(
            days=30, snapshot_path=tmp_path / "missing.json", now=_now(),
        )
        assert win.kept_count == 0
        assert win.raw_count == 0
        assert win.window_start  # still populated

    def test_dedupe_by_alert_id(self, tmp_path):
        """Two snapshots emitting the same URL twice — one collapsed."""
        path = _snapshot(tmp_path, [
            _raw_alert(1, days_ago=1),
            _raw_alert(1, days_ago=1),  # same id
        ])
        win = data_loader.load_alerts_window(days=30, snapshot_path=path, now=_now())
        assert win.kept_count == 1
        assert win.raw_count == 2

    def test_missing_title_dropped(self, tmp_path):
        """No title → no useful signal. Drop, don't crash."""
        path = _snapshot(tmp_path, [
            _raw_alert(1, days_ago=1),
            {"url": "https://x/y", "title": "", "score": 50},   # empty title
        ])
        win = data_loader.load_alerts_window(days=30, snapshot_path=path, now=_now())
        assert win.kept_count == 1
        assert win.raw_count == 2

    def test_malformed_date_kept_not_dropped(self, tmp_path):
        """Permissive parsing: a row with a non-ISO date stays in the
        window. Argus_Phase2_7 §2.1 spec — better to over-include than
        silently lose signal."""
        path = _snapshot(tmp_path, [
            _raw_alert(1, published_at="garbage-date"),
            _raw_alert(2, published_at=""),
        ])
        win = data_loader.load_alerts_window(days=30, snapshot_path=path, now=_now())
        assert win.kept_count == 2

    def test_alert_id_falls_back_to_title_when_no_url(self, tmp_path):
        path = _snapshot(tmp_path, [
            _raw_alert(1, url="", days_ago=1),
        ])
        win = data_loader.load_alerts_window(days=30, snapshot_path=path, now=_now())
        assert win.alerts[0].alert_id.startswith("title:Alert 1")


class TestScoreForPersona:
    def test_uses_production_compute_function(self, tmp_path):
        """Sanity: a high-liquidity-weight persona (cash_manager) must
        rank a Liquidity Risk alert above an unrelated alert. If this
        breaks, generate_actions._compute_effective_score has drifted
        and the evaluation no longer measures the real product."""
        path = _snapshot(tmp_path, [
            _raw_alert(1, days_ago=1, category_label="Liquidity Risk", score=50),
            _raw_alert(2, days_ago=1, category_label="Disaster Risk", score=50),
        ])
        win = data_loader.load_alerts_window(days=30, snapshot_path=path, now=_now())
        scored = data_loader.score_for_persona(win.alerts, "cash_manager")
        # cash_manager: liquidity_risk=1.6, disaster_risk=0.3 — liquidity must win
        assert scored[0].alert.category == "Liquidity Risk"
        assert scored[0].effective_score > scored[1].effective_score

    def test_unknown_persona_falls_back_to_unweighted(self, tmp_path):
        """A typo in persona_key should not raise — fall back to weight=1.0
        across the board so the eval still produces SOMETHING (even if
        the row in the report will show 'unknown_persona' clearly)."""
        path = _snapshot(tmp_path, [_raw_alert(1, days_ago=1, score=42)])
        win = data_loader.load_alerts_window(days=30, snapshot_path=path, now=_now())
        scored = data_loader.score_for_persona(win.alerts, "nonexistent_persona")
        # unweighted means effective_score == base score
        assert scored[0].effective_score == 42.0

    def test_top_n_ids_preserves_score_order(self, tmp_path):
        alerts = [_raw_alert(i, days_ago=1, score=10 + i) for i in range(5)]
        path = _snapshot(tmp_path, alerts)
        win = data_loader.load_alerts_window(days=30, snapshot_path=path, now=_now())
        scored = data_loader.score_for_persona(win.alerts, "cash_manager")
        ids = data_loader.top_n_ids(scored, n=3)
        assert len(ids) == 3
        # All alerts share the same category, so the score-only ordering
        # should match desc base score; top should be id from alert 4.
        assert "a4" in ids[0]
