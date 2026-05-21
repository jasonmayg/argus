"""
Unit tests for B1 — the inline scoring step in
`core/guardian_loader._load_guardian_snapshot_from_gcs`.

Before B1 the raw-CSV path returned unclassified alerts and a
separate bridge script (`scripts/refresh_local_snapshot_from_gcs.py`)
had to be re-run to produce a scored snapshot on disk. Downstream
consumers (`/api/actions`, persona_eval) read that on-disk file
directly, so when the bridge hadn't been re-run the actions API
silently served stale data — root cause of \"Decision Support shows
nothing\" 2026-05-21.

These tests pin the new contract: when
`_score_raw_gcs_alerts` is invoked on a list of raw GCS alert
dicts, it produces classified + scored alerts in the shape
downstream consumers already expect.

Run:
    pytest tests/test_guardian_loader_scoring.py -v
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.guardian_loader import (  # noqa: E402
    _normalize_gcs_source,
    _parse_gcs_published_at,
    _raw_alerts_to_guardian_events,
    _score_raw_gcs_alerts,
)


# ──────────────────────────────────────────────────────────────────
# Source normalization — the mapping has to match exactly because
# `GuardianEngine._base_severity` only special-cases the short keys
# ("ecb" → 0.3 after PR #47). A wrong mapping silently degrades
# severity floors.
# ──────────────────────────────────────────────────────────────────

class TestNormalizeGcsSource:
    @pytest.mark.parametrize("raw,expected", [
        ("European Central Bank", "ecb"),
        ("Federal Reserve",       "fed"),
        ("U.S. Treasury",         "treasury"),
        ("Bank of England",       "boe"),
        ("Bank of Japan",         "boj"),
        ("Bank of Canada",        "bank_of_canada"),
        ("FCA UK",                "fca"),
        ("USGS",                  "usgs"),
    ])
    def test_known_sources_mapped(self, raw, expected):
        assert _normalize_gcs_source(raw) == expected

    def test_unknown_source_snake_cases(self):
        assert _normalize_gcs_source("Some New Outlet") == "some_new_outlet"
        assert _normalize_gcs_source("PBOC (China)") == "pboc_china"

    def test_empty_input_safe(self):
        assert _normalize_gcs_source("") == ""
        assert _normalize_gcs_source("   ") == ""


# ──────────────────────────────────────────────────────────────────
# Date parsing — GCS mixes RFC 2822 (press release feeds) and ISO
# 8601 (econ-series CSVs). Both must parse without crashing.
# ──────────────────────────────────────────────────────────────────

class TestParseGcsPublishedAt:
    def test_rfc_2822_with_offset(self):
        dt = _parse_gcs_published_at("Mon, 18 May 2026 09:06:58 +0100")
        assert dt is not None and dt.tzinfo is not None

    def test_iso_8601_with_offset(self):
        dt = _parse_gcs_published_at("2026-05-19T12:00:00+00:00")
        assert dt is not None and dt.tzinfo is not None

    def test_iso_zulu(self):
        dt = _parse_gcs_published_at("2026-05-19T12:00:00Z")
        assert dt is not None

    def test_naive_iso_gets_utc(self):
        """Bare datetimes without timezone must be stamped UTC so
        downstream arithmetic against tz-aware values doesn't
        TypeError."""
        dt = _parse_gcs_published_at("2026-05-19T12:00:00")
        assert dt is not None and dt.tzinfo is not None

    def test_empty_or_garbage_returns_none(self):
        assert _parse_gcs_published_at("") is None
        assert _parse_gcs_published_at(None) is None
        assert _parse_gcs_published_at("yesterday-ish") is None


# ──────────────────────────────────────────────────────────────────
# Event conversion — most of the 2000+ GCS raw rows are econ-series
# CSVs with no title; they must drop before reaching score_events.
# ──────────────────────────────────────────────────────────────────

class TestRawAlertsToGuardianEvents:
    def test_empty_titles_dropped(self):
        raw = [
            {"title": "",   "url": "u1", "source": "gcs_raw"},
            {"title": "  ", "url": "u2", "source": "gcs_raw"},
            {"title": "Real headline", "url": "u3",
             "source": "Federal Reserve",
             "published_at": "2026-05-19T12:00:00+00:00"},
        ]
        events = _raw_alerts_to_guardian_events(raw)
        assert len(events) == 1
        assert events[0].title == "Real headline"

    def test_source_normalization_flows_into_event(self):
        raw = [{"title": "X", "url": "u", "source": "European Central Bank",
                "published_at": "2026-05-19T12:00:00+00:00"}]
        events = _raw_alerts_to_guardian_events(raw)
        assert events[0].source == "ecb"

    def test_country_hint_into_summary_only_when_known(self):
        """`country='Unknown'` must NOT plant the literal 'Unknown'
        string into the Event's summary — `_detect_countries` would
        match nothing useful anyway and we don't want 'Unknown' to
        leak into downstream text-matching."""
        raw = [{
            "title": "USGS earthquake report",
            "url": "u", "source": "USGS",
            "country": "Unknown",
            "published_at": "2026-05-19T12:00:00+00:00",
        }]
        events = _raw_alerts_to_guardian_events(raw)
        assert "Unknown" not in events[0].summary

    def test_country_hint_used_when_present(self):
        """Country hint should flow into the summary so the engine's
        text-based `_detect_countries` can match it on a short
        press-release title that doesn't mention the country."""
        raw = [{
            "title": "Policy statement released",
            "url": "u", "source": "Federal Reserve",
            "country": "USA",
            "published_at": "2026-05-19T12:00:00+00:00",
        }]
        events = _raw_alerts_to_guardian_events(raw)
        assert "USA" in events[0].summary

    def test_unparseable_date_stamps_now_does_not_drop(self):
        """A row with a garbage published_at must NOT be dropped —
        we stamp now() so it still surfaces in today's window. (The
        engine's recency score will be high either way.)"""
        before = datetime.now(timezone.utc)
        raw = [{"title": "X", "url": "u", "source": "Fed",
                "published_at": "garbage"}]
        events = _raw_alerts_to_guardian_events(raw)
        after = datetime.now(timezone.utc)
        assert len(events) == 1
        assert before <= events[0].published_at <= after


# ──────────────────────────────────────────────────────────────────
# End-to-end: raw dicts → scored alerts via GuardianEngine
# ──────────────────────────────────────────────────────────────────

class TestScoreRawGcsAlerts:
    def test_empty_input_returns_empty(self):
        assert _score_raw_gcs_alerts([]) == []

    def test_all_empty_titles_returns_empty(self):
        """Rows that all drop at event-conversion time → no events to
        score → empty result. Most of the GCS bulk is this shape."""
        raw = [{"title": "", "source": "gcs_raw"}] * 50
        assert _score_raw_gcs_alerts(raw) == []

    def test_real_input_produces_scored_alerts(self):
        """A press-release headline from a known central bank with a
        known country hint must produce at least one scored alert
        with non-zero score and a real category_label. This is the
        path that's most user-visible — without it, Decision Support
        is empty."""
        raw = [{
            "title":   "Bank of England raises rate to 5.25%",
            "url":     "https://example.com/boe-rate",
            "source":  "Bank of England",
            "country": "UK",
            "published_at": "2026-05-19T12:00:00+00:00",
        }]
        scored = _score_raw_gcs_alerts(raw)
        assert len(scored) >= 1
        a = scored[0]
        # Score is baseline (severity × recency × country) with the
        # `neutral` persona (all weights = 1.0) — should be > 0.
        assert a.get("score", 0) > 0
        # Category should be a real risk_taxonomy label, not
        # \"Unclassified\".
        cat = a.get("category_label") or a.get("category", "")
        assert cat and cat.lower() != "unclassified"
        assert a.get("country", "").lower() == "uk"

    def test_scored_payload_shape_matches_consumer_contract(self):
        """`generate_actions._load_guardian_alerts` reads these keys:
        `score`, `category_label` (or `category`), `country`,
        `title`, `source`, `published_at`. The scored output must
        have them or downstream code crashes."""
        raw = [{
            "title":   "ECB monetary policy update",
            "url":     "https://example.com/ecb",
            "source":  "European Central Bank",
            "country": "Germany",
            "published_at": "2026-05-19T12:00:00+00:00",
        }]
        scored = _score_raw_gcs_alerts(raw)
        assert scored, "expected at least one scored alert"
        for required in ("score", "category_label", "country", "title",
                          "source", "published_at"):
            assert required in scored[0], f"scored alert missing key: {required}"

    def test_failure_in_engine_returns_empty_not_raises(self):
        """If GuardianEngine init or score_events fails for any
        reason, return [] so callers fall back to unscored data
        rather than crashing the request."""
        with patch("core.guardian_loader._raw_alerts_to_guardian_events",
                   side_effect=RuntimeError("boom")):
            # Even with one valid-looking raw alert, the helper must
            # swallow and return [].
            out = _score_raw_gcs_alerts([{"title": "x", "source": "fed",
                                           "published_at": "2026-05-19"}])
        assert out == []

    def test_failure_logs_a_warning(self, caplog):
        """Regression (self-review 2026-05-21) — silent failures
        used to leave operators guessing why the dashboard went
        empty. Each scoring failure must produce a WARNING with the
        exception class + truncated message so the gunicorn / cron
        log captures it for diagnosis."""
        from core import guardian_loader as gl
        with patch.object(gl, "_raw_alerts_to_guardian_events",
                          side_effect=RuntimeError("kaboom")):
            with caplog.at_level("WARNING", logger="core.guardian_loader"):
                gl._score_raw_gcs_alerts([{"title": "x", "source": "fed"}])
        joined = " ".join(r.message for r in caplog.records)
        assert "RuntimeError" in joined
        assert "kaboom" in joined


class TestGuardianEngineCaching:
    """Regression (self-review 2026-05-21) — earlier B1 instantiated
    GuardianEngine inside `_score_raw_gcs_alerts`, re-loading 3 yaml
    files on every /api/actions / dashboard request. The cache reuses
    the engine across calls within a single gunicorn worker."""

    def test_engine_instantiated_only_once_across_calls(self):
        from core import guardian_loader as gl
        gl._reset_guardian_engine_cache()
        first  = gl._get_guardian_engine()
        second = gl._get_guardian_engine()
        assert first is second, (
            "_get_guardian_engine returned a fresh instance — cache "
            "didn't hold, every request would re-load 3 yaml files"
        )

    def test_reset_cache_clears_singleton(self):
        """The reset hook lets tests swap config without state leak."""
        from core import guardian_loader as gl
        first = gl._get_guardian_engine()
        gl._reset_guardian_engine_cache()
        second = gl._get_guardian_engine()
        assert first is not second


# ──────────────────────────────────────────────────────────────────
# Integration with generate_actions._load_guardian_alerts_raw
# ──────────────────────────────────────────────────────────────────

class TestGenerateActionsReadPath:
    def test_uses_gcs_loader_when_snapshot_present(self):
        """Regression — the user-visible Decision Support bug came
        from `_load_guardian_alerts` reading a stale local file.
        After B1 it must prefer the live GCS snapshot."""
        # Stub guardian_loader to return a non-empty snapshot.
        fake_snap = {
            "ok": True,
            "snapshot": {
                "alerts": [
                    {"title": "alert-from-gcs", "score": 70,
                     "category_label": "Market Risk", "country": "USA",
                     "source": "fed", "published_at": "2026-05-19"},
                ],
            },
        }
        with patch("core.guardian_loader._load_guardian_snapshot_from_gcs",
                   return_value=fake_snap):
            from generate_actions import _load_guardian_alerts_raw
            alerts = _load_guardian_alerts_raw()
        assert any(a["title"] == "alert-from-gcs" for a in alerts), (
            "_load_guardian_alerts_raw didn't use the GCS loader"
        )

    def test_falls_back_to_local_file_when_gcs_returns_empty(self, tmp_path,
                                                              monkeypatch):
        """When GCS is unreachable / returns ok=False, the local
        `guardian_poc/data/alerts_latest.json` file is the
        fallback. The legacy file is still produced by the bridge
        script + daily_monitor for this exact scenario."""
        import generate_actions
        # Point GUARDIAN_PATH at a temp file with a sentinel alert.
        fake_file = tmp_path / "alerts_latest.json"
        fake_file.write_text('{"alerts": [{"title": "from-local-file",'
                              ' "score": 50, "category_label": "Market Risk",'
                              ' "country": "USA", "source": "fed"}]}')
        monkeypatch.setattr(generate_actions, "GUARDIAN_PATH", fake_file)

        # Stub guardian_loader to return no snapshot (GCS unreachable).
        with patch("core.guardian_loader._load_guardian_snapshot_from_gcs",
                   return_value={"ok": False, "snapshot": None,
                                  "message": "gcs unreachable"}):
            alerts = generate_actions._load_guardian_alerts_raw()

        assert any(a["title"] == "from-local-file" for a in alerts), (
            "should have fallen back to local file when GCS returned ok=False"
        )

    def test_returns_empty_when_both_paths_fail(self, tmp_path, monkeypatch):
        """Worst case: GCS down AND local file missing. Don't crash,
        return [] — downstream code handles empty alerts gracefully."""
        import generate_actions
        monkeypatch.setattr(generate_actions, "GUARDIAN_PATH",
                            tmp_path / "does-not-exist.json")
        with patch("core.guardian_loader._load_guardian_snapshot_from_gcs",
                   return_value={"ok": False, "snapshot": None}):
            alerts = generate_actions._load_guardian_alerts_raw()
        assert alerts == []
