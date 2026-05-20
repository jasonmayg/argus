"""
Unit tests for `scripts/refresh_local_snapshot_from_gcs.py`.

The end-to-end run hits GCS, so those don't go in the unit suite —
they live as the smoke run in the PR description.

What we DO pin here is the bits that are easy to get subtly wrong
and that would silently corrupt the snapshot:

  1. Source name normalization — `"European Central Bank"` must map
     to `"ecb"` so `_base_severity` returns the PR #47 lowered 0.3
     floor. Mapping mistakes would silently reintroduce noise.
  2. RFC 2822 + ISO 8601 date parsing — the GCS path mixes both
     formats; either failing returns None and the converter has to
     handle that without crashing.
  3. Filter behavior — empty-title rows (the bulk of GCS today,
     ~1900 of 2093) must drop so they don't waste cycles in
     `score_events`.

Run:
    pytest tests/test_refresh_local_snapshot_from_gcs.py -v
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Import as a module so tests stay loaded even if the script grows
# additional helpers later.
import importlib.util
SPEC = importlib.util.spec_from_file_location(
    "refresh_local_snapshot_from_gcs",
    ROOT / "scripts" / "refresh_local_snapshot_from_gcs.py",
)
bridge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bridge)


class TestSourceNormalization:
    """Mapping GCS-emitted display strings to Guardian short keys.
    Wrong mapping silently breaks `_base_severity` lookups — e.g.
    `"European Central Bank"` not matching `"ecb"` would mean the
    PR #47 noise filter doesn't apply and ECB items dominate again."""

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
    def test_known_sources_mapped_to_short_keys(self, raw, expected):
        assert bridge._normalize_source(raw) == expected

    def test_unknown_source_falls_through_to_snake_case(self):
        """An unrecognized source must still produce a stable
        snake_case key so downstream stats can group by it."""
        assert bridge._normalize_source("Some New Source") == "some_new_source"
        assert bridge._normalize_source("PBOC (China)") == "pboc_china"

    def test_empty_source_returns_empty(self):
        """Empty input → empty output (caller decides if that's drop-worthy);
        no crash from regex on empty string."""
        assert bridge._normalize_source("") == ""
        assert bridge._normalize_source("   ") == ""


class TestPublishedAtParsing:
    """The GCS path mixes RFC 2822 (from press release RSS feeds) and
    ISO 8601 (from API CSVs). We have to accept both and return None
    rather than crash on unparseable input."""

    def test_rfc_2822_with_timezone(self):
        dt = bridge._parse_published_at("Mon, 18 May 2026 09:06:58 +0100")
        assert dt is not None
        assert dt.tzinfo is not None
        # 2026-05-18 09:06:58 +0100 → 2026-05-18 08:06:58 UTC
        assert dt.utcoffset().total_seconds() == 3600

    def test_rfc_2822_gmt(self):
        dt = bridge._parse_published_at("Fri, 15 May 2026 21:00:00 GMT")
        assert dt is not None
        assert dt.year == 2026 and dt.month == 5 and dt.day == 15

    def test_iso_8601_with_tz(self):
        dt = bridge._parse_published_at("2026-05-19T12:00:00+00:00")
        assert dt is not None
        assert dt.tzinfo is not None

    def test_iso_8601_zulu(self):
        """The `Z` suffix has to be coerced into `+00:00` for fromisoformat."""
        dt = bridge._parse_published_at("2026-05-19T12:00:00Z")
        assert dt is not None
        assert dt.utcoffset().total_seconds() == 0

    def test_iso_naive_gets_utc_default(self):
        """A bare datetime with no tz must be stamped UTC so we don't
        get a TypeError later in arithmetic against tz-aware values."""
        dt = bridge._parse_published_at("2026-05-19T12:00:00")
        assert dt is not None
        assert dt.tzinfo is not None

    def test_empty_returns_none(self):
        assert bridge._parse_published_at("") is None
        assert bridge._parse_published_at(None) is None

    def test_unparseable_returns_none(self):
        """Garbage in → None out, NOT a crash. The caller stamps now()
        as a fallback so the alert still ends up in today's window."""
        assert bridge._parse_published_at("yesterday afternoon") is None


class TestEventConversion:
    """`_convert_to_events` shape-converts dict alerts → `Event` objs
    AND filters out the 90%+ of GCS rows that have no usable title
    (the econ-series CSV passthroughs)."""

    def test_empty_titles_dropped(self):
        # Mimic the gcs_raw passthrough rows.
        raw = [
            {"title": "", "url": "u1", "source": "gcs_raw"},
            {"title": "  ", "url": "u2", "source": "gcs_raw"},
            {"title": "Real News", "url": "u3", "source": "Federal Reserve",
             "published_at": "2026-05-19T12:00:00+00:00"},
        ]
        events = bridge._convert_to_events(raw)
        assert len(events) == 1
        assert events[0].title == "Real News"

    def test_source_normalization_applied_in_event(self):
        """Confirm the normalized source flows through to the Event
        object — if this regresses, scoring uses the wrong severity."""
        raw = [{"title": "X", "url": "u", "source": "European Central Bank",
                "published_at": "2026-05-19T12:00:00+00:00"}]
        events = bridge._convert_to_events(raw)
        assert events[0].source == "ecb"

    def test_unparseable_date_stamps_now(self):
        """An alert without a valid published_at should NOT be dropped —
        we stamp now() so it still surfaces in today's window. Dropping
        these would lose real signal whenever the upstream feed has
        date-format drift."""
        before = datetime.now(timezone.utc)
        raw = [{"title": "X", "url": "u", "source": "Federal Reserve",
                "published_at": "garbage"}]
        events = bridge._convert_to_events(raw)
        after = datetime.now(timezone.utc)
        assert len(events) == 1
        assert before <= events[0].published_at <= after

    def test_url_used_as_event_id_when_present(self):
        raw = [{"title": "X", "url": "https://example.com/a", "source": "Fed",
                "published_at": "2026-05-19T12:00:00+00:00"}]
        events = bridge._convert_to_events(raw)
        assert events[0].event_id == "https://example.com/a"

    def test_title_fallback_event_id_when_no_url(self):
        """Without a URL we still need a stable id — fall back to the
        title (truncated) prefixed with `title:` so it doesn't collide
        with a URL value somewhere."""
        raw = [{"title": "Some headline", "source": "Fed",
                "published_at": "2026-05-19T12:00:00+00:00"}]
        events = bridge._convert_to_events(raw)
        assert events[0].event_id.startswith("title:")
        assert "Some headline" in events[0].event_id


class TestSnapshotShape:
    """The output dict must keep the exact field names downstream code
    consumes. `core/persona_eval/data_loader._normalize` reads
    `score`, `title`, `country`, `category_label`, `source`, etc. —
    `generate_actions._load_guardian_alerts` reads the same set.
    A renamed key here breaks both silently."""

    def _scored_alert(self, **overrides):
        from guardian_poc.src.guardian import ScoredAlert
        defaults = dict(
            country="USA", category_key="market_risk",
            category_label="Market Risk", score=55.0,
            severity=0.6, recency=0.8, source="fed",
            title="Sample", url="https://example.com/sample",
            published_at=datetime(2026, 5, 19, tzinfo=timezone.utc),
            recommendation="do x", recommendation_source="rule",
            rationale="",
        )
        defaults.update(overrides)
        return ScoredAlert(**defaults)

    def test_required_top_level_keys(self):
        out = bridge._build_snapshot(
            [self._scored_alert()], persona="treasury_ops",
            threshold=40.0, source_ref="gs://bucket/path",
        )
        for k in ("generated_at_utc", "persona", "threshold",
                  "data_mode", "event_count", "alert_count",
                  "alerts"):
            assert k in out, f"missing required key: {k}"
        assert out["data_mode"] == "bridge_gcs_raw_to_scored"
        assert out["persona"] == "treasury_ops"

    def test_alert_keys_preserved(self):
        """Downstream code keys off `score`, `category_label`, etc. —
        renaming any of these would silently empty out persona_eval."""
        out = bridge._build_snapshot(
            [self._scored_alert()], persona="treasury_ops",
            threshold=40.0, source_ref="",
        )
        a = out["alerts"][0]
        for k in ("country", "category_label", "score", "severity",
                  "source", "title", "url", "published_at"):
            assert k in a, f"alert missing key: {k}"

    def test_published_at_serialized_as_iso_string(self):
        """asdict() on a ScoredAlert dataclass leaves published_at as
        a datetime; json.dumps would TypeError. Confirm we coerce
        to an ISO string."""
        out = bridge._build_snapshot(
            [self._scored_alert()], persona="treasury_ops",
            threshold=40.0, source_ref="",
        )
        assert isinstance(out["alerts"][0]["published_at"], str)
        assert "2026-05-19" in out["alerts"][0]["published_at"]

    def test_triggered_count_uses_threshold(self):
        """`triggered_count` is the # of alerts at or above the
        snapshot threshold — used by the daily monitor / dashboard
        as a "how many actionable alerts" headline."""
        out = bridge._build_snapshot([
            self._scored_alert(score=45.0),
            self._scored_alert(score=30.0),
            self._scored_alert(score=70.0),
        ], persona="treasury_ops", threshold=40.0, source_ref="")
        assert out["triggered_count"] == 2
