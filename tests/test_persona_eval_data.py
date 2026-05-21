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

    def test_alert_id_includes_published_date_in_key(self, tmp_path):
        """Same URL across different dates must produce distinct ids —
        Guardian re-emits the same source URL on every fetch (e.g. the
        ECB FX endpoint), and each fetch is a distinct event. If the
        URL-only key were used, only one of these would survive dedup."""
        path = _snapshot(tmp_path, [
            _raw_alert(1, url="https://feed/x", days_ago=1, score=50),
            _raw_alert(1, url="https://feed/x", days_ago=5, score=50),
        ])
        win = data_loader.load_alerts_window(days=30, snapshot_path=path, now=_now())
        assert win.kept_count == 2, "same url on different dates must not collapse"


class TestSnapshotsDirMode:
    """The production source of truth is the directory of timestamped
    snapshots, not the single alerts_latest.json. Tests exercise the
    multi-file walk path with a small temp directory."""

    def test_walks_every_snapshot_file_in_dir(self, tmp_path):
        snaps = tmp_path / "snapshots"
        snaps.mkdir()
        for i, days_ago in enumerate([1, 3, 7]):
            f = snaps / f"alerts_20260{i+1:02d}.json"
            f.write_text(json.dumps({"alerts": [_raw_alert(i+1, days_ago=days_ago)]}))
        win = data_loader.load_alerts_window(
            days=30, snapshots_dir=snaps, now=_now(),
        )
        assert win.kept_count == 3
        assert win.raw_count == 3

    def test_dedupes_across_files(self, tmp_path):
        """Same alert appearing in two snapshots (overlapping fetches)
        must only contribute one row to the window."""
        snaps = tmp_path / "snapshots"
        snaps.mkdir()
        same = _raw_alert(1, url="https://feed/x", days_ago=2, score=50)
        (snaps / "alerts_a.json").write_text(json.dumps({"alerts": [same]}))
        (snaps / "alerts_b.json").write_text(json.dumps({"alerts": [same]}))
        win = data_loader.load_alerts_window(
            days=30, snapshots_dir=snaps, now=_now(),
        )
        assert win.kept_count == 1
        assert win.raw_count == 2

    def test_dedup_prefers_newest_snapshot(self, tmp_path):
        """Regression — when a scoring change ships (e.g. the bridge's
        switch to a neutral persona on 2026-05-20), today's snapshot
        will have a different `score` value than yesterday's for the
        same (url, date) alert. The window walk MUST keep the newest
        version, otherwise persona_eval silently runs on stale scores.

        Filename ordering encodes the production timestamp convention
        (`alerts_YYYYMMDD_HHMMSS.json`), so sorting in reverse picks
        newest first."""
        snaps = tmp_path / "snapshots"
        snaps.mkdir()
        # Both scores must clear MIN_GUARDIAN_SCORE (40) or the
        # production-parity filter drops them before dedup runs —
        # the dedup-ordering test would then pass trivially with
        # win.kept_count == 0, hiding a regression. Copilot PR #51
        # review caught this.
        old = _raw_alert(1, url="https://feed/x", days_ago=2, score=42)
        new = _raw_alert(1, url="https://feed/x", days_ago=2, score=99)
        # Filename `alerts_20260518_*.json` < `alerts_20260519_*.json`
        # so the second file is the "newer" snapshot.
        (snaps / "alerts_20260518_120000.json").write_text(
            json.dumps({"alerts": [old]})
        )
        (snaps / "alerts_20260519_120000.json").write_text(
            json.dumps({"alerts": [new]})
        )
        win = data_loader.load_alerts_window(
            days=30, snapshots_dir=snaps, now=_now(),
        )
        assert win.kept_count == 1
        # The kept alert must be the NEW one (score 99), not the
        # stale one (score 42). If the walk regresses to ascending
        # order this test fails with score==42.
        assert win.alerts[0].score == 99

    def test_corrupt_file_does_not_abort_walk(self, tmp_path):
        snaps = tmp_path / "snapshots"
        snaps.mkdir()
        (snaps / "alerts_good.json").write_text(
            json.dumps({"alerts": [_raw_alert(1, days_ago=1)]})
        )
        (snaps / "alerts_broken.json").write_text("{ not valid json")
        win = data_loader.load_alerts_window(
            days=30, snapshots_dir=snaps, now=_now(),
        )
        assert win.kept_count == 1

    def test_prod_filters_drop_below_min_score(self, tmp_path):
        """Copilot PR #46 review: eval must mirror the production
        scoring path's filters (MIN_GUARDIAN_SCORE + weather/disaster
        excludes) so persona_eval measures the pool users actually see
        — not the raw upstream feed."""
        snaps = tmp_path / "snapshots"
        snaps.mkdir()
        (snaps / "alerts_x.json").write_text(json.dumps({"alerts": [
            _raw_alert(1, days_ago=1, score=10),  # below MIN=40
            _raw_alert(2, days_ago=1, score=70),  # above
        ]}))
        win = data_loader.load_alerts_window(days=30, snapshots_dir=snaps, now=_now())
        assert win.kept_count == 1
        assert win.alerts[0].score == 70

    def test_prod_filters_drop_disaster_category(self, tmp_path):
        """Default exclude_groups=('weather', 'disaster') strips any
        alert whose category contains 'disaster' so persona_eval's
        Method-A top-N matches what _load_guardian_alerts surfaces."""
        snaps = tmp_path / "snapshots"
        snaps.mkdir()
        (snaps / "alerts_x.json").write_text(json.dumps({"alerts": [
            _raw_alert(1, days_ago=1, category_label="Disaster Risk", score=70),
            _raw_alert(2, days_ago=1, category_label="Liquidity Risk", score=70),
        ]}))
        win = data_loader.load_alerts_window(days=30, snapshots_dir=snaps, now=_now())
        assert win.kept_count == 1
        assert win.alerts[0].category == "Liquidity Risk"

    def test_prod_filters_drop_noaa_sources_with_weather_advisory(self, tmp_path):
        """NOAA-source alerts whose title looks like a routine advisory
        (Small Craft Advisory etc.) are dropped — matching the prod
        logic in generate_actions._load_guardian_alerts."""
        snaps = tmp_path / "snapshots"
        snaps.mkdir()
        (snaps / "alerts_x.json").write_text(json.dumps({"alerts": [
            {"url": "https://x/1", "title": "Small Craft Advisory", "score": 80,
             "source": "noaa_nws", "published_at": "2026-05-18", "country": "USA"},
            {"url": "https://x/2", "title": "Major Hurricane Approaching", "score": 80,
             "source": "noaa_nws", "published_at": "2026-05-18", "country": "USA"},
        ]}))
        win = data_loader.load_alerts_window(days=30, snapshots_dir=snaps, now=_now())
        # Both excluded: noaa_nws source is in EXCLUDED_SOURCES, AND
        # "Advisory" matches the title-keyword filter.
        assert win.kept_count == 0

    def test_include_all_disables_prod_filters(self, tmp_path):
        """`apply_prod_filters=False` (and the CLI `--include-all`)
        bypasses MIN_SCORE + exclude logic for raw / diagnostic runs."""
        snaps = tmp_path / "snapshots"
        snaps.mkdir()
        (snaps / "alerts_x.json").write_text(json.dumps({"alerts": [
            _raw_alert(1, days_ago=1, score=5),    # would be dropped by MIN
            _raw_alert(2, days_ago=1, category_label="Disaster Risk", score=70),
        ]}))
        win = data_loader.load_alerts_window(
            days=30, snapshots_dir=snaps, now=_now(), apply_prod_filters=False,
        )
        assert win.kept_count == 2

    def test_only_alerts_glob_matches_picked_up(self, tmp_path):
        """A stray *.txt or differently-named file in snapshots/ must
        not be parsed as JSON — we look only for alerts_*.json."""
        snaps = tmp_path / "snapshots"
        snaps.mkdir()
        (snaps / "alerts_x.json").write_text(
            json.dumps({"alerts": [_raw_alert(1, days_ago=1)]})
        )
        (snaps / "README.md").write_text("docs")
        (snaps / "scratch.json").write_text("{not json")
        win = data_loader.load_alerts_window(
            days=30, snapshots_dir=snaps, now=_now(),
        )
        assert win.kept_count == 1


class TestScoreForPersona:
    """These tests target the scoring formula reuse and ordering. They
    use apply_prod_filters=False so production filters (MIN_SCORE +
    weather/disaster excludes) don't interfere — the scoring layer
    must be exercised independently of the filter layer."""

    def test_uses_production_compute_function(self, tmp_path):
        """Sanity: a high-liquidity-weight persona (cash_manager) must
        rank a Liquidity Risk alert above an unrelated alert. If this
        breaks, generate_actions._compute_effective_score has drifted
        and the evaluation no longer measures the real product."""
        path = _snapshot(tmp_path, [
            _raw_alert(1, days_ago=1, category_label="Liquidity Risk", score=50),
            _raw_alert(2, days_ago=1, category_label="Disaster Risk", score=50),
        ])
        win = data_loader.load_alerts_window(
            days=30, snapshot_path=path, now=_now(), apply_prod_filters=False,
        )
        scored = data_loader.score_for_persona(win.alerts, "cash_manager")
        # cash_manager: liquidity_risk=1.6, disaster_risk=0.3 — liquidity must win
        assert scored[0].alert.category == "Liquidity Risk"
        assert scored[0].effective_score > scored[1].effective_score

    def test_unknown_persona_falls_back_to_unweighted(self, tmp_path):
        """A typo in persona_key should not raise — fall back to weight=1.0
        across the board so the eval still produces SOMETHING (even if
        the row in the report will show 'unknown_persona' clearly)."""
        path = _snapshot(tmp_path, [_raw_alert(1, days_ago=1, score=42)])
        win = data_loader.load_alerts_window(
            days=30, snapshot_path=path, now=_now(), apply_prod_filters=False,
        )
        scored = data_loader.score_for_persona(win.alerts, "nonexistent_persona")
        # unweighted means effective_score == base score
        assert scored[0].effective_score == 42.0

    def test_top_n_ids_preserves_score_order(self, tmp_path):
        alerts = [_raw_alert(i, days_ago=1, score=10 + i) for i in range(5)]
        path = _snapshot(tmp_path, alerts)
        win = data_loader.load_alerts_window(
            days=30, snapshot_path=path, now=_now(), apply_prod_filters=False,
        )
        scored = data_loader.score_for_persona(win.alerts, "cash_manager")
        ids = data_loader.top_n_ids(scored, n=3)
        assert len(ids) == 3
        # All alerts share the same category, so the score-only ordering
        # should match desc base score; top should be id from alert 4.
        assert "a4" in ids[0]
