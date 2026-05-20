"""
Unit tests for core.tools.get_fx_rate.

Stubs `_fetch_series` so tests don't talk to GCS. The pair-resolution
table (_USD_PAIRS) is the easiest thing to drift if AI_Treasury_Project
renames a series id, so we lock the canonical pairs by name + verify
both pair orderings (USDJPY and JPYUSD) resolve to the same csv id.

Run:
    pytest tests/test_tool_get_fx_rate.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.tools.get_fx_rate import GetFxRate, _USD_PAIRS, _resolve_csv_id  # noqa: E402


class TestPairResolution:
    def test_both_orderings_resolve_to_same_csv_id(self):
        assert _resolve_csv_id("USD", "JPY") == "jp_usdjpy"
        assert _resolve_csv_id("JPY", "USD") == "jp_usdjpy"

    def test_lowercase_input_accepted(self):
        """LLM-emitted args are not always uppercase — the tool must
        normalize before lookup."""
        assert _resolve_csv_id("usd", "eur") == "eu_usdeur"

    def test_unsupported_pair_returns_none(self):
        assert _resolve_csv_id("EUR", "JPY") is None  # non-USD cross
        assert _resolve_csv_id("USD", "XYZ") is None  # bogus

    def test_pairs_table_filenames_match_ai_treasury_convention(self):
        """Every csv id must be `<country>_usd<ccy>` lowercase — that's
        the convention AI_Treasury_Project writes. If a value here
        diverges, the GCS fetch silently 404s and the tool reports
        'no rows' — easier to catch the drift here."""
        for ccy, csv_id in _USD_PAIRS.items():
            assert csv_id.endswith(f"usd{ccy.lower()}"), (ccy, csv_id)


class TestExecute:
    @pytest.fixture
    def tool(self, monkeypatch) -> GetFxRate:
        t = GetFxRate()
        # Default stub: return two rows so the "prior" field gets filled.
        def fake_fetch(csv_id):
            return [
                {"date": "2026-05-10", "value": "150.20"},
                {"date": "2026-05-11", "value": "151.05"},
            ]
        monkeypatch.setattr(t, "_fetch_series", fake_fetch)
        return t

    def test_happy_path_with_prior_row(self, tool):
        r = tool.execute({"from_ccy": "USD", "to_ccy": "JPY"})
        assert r.ok is True
        assert "151.05" in r.content
        assert "150.20" in r.content    # prior surfaces for direction
        assert r.structured["csv_id"] == "jp_usdjpy"
        assert r.structured["row_count"] == 2

    def test_unsupported_pair_fails_with_helpful_message(self, tool):
        r = tool.execute({"from_ccy": "EUR", "to_ccy": "JPY"})
        assert r.ok is False
        assert "Unsupported" in r.error or "unsupported" in r.error.lower()

    def test_missing_args(self, tool):
        r = tool.execute({"from_ccy": "USD"})
        assert r.ok is False
        assert "required" in r.error.lower()

    def test_empty_series_fails_softly(self, tool, monkeypatch):
        monkeypatch.setattr(tool, "_fetch_series", lambda _csv_id: [])
        r = tool.execute({"from_ccy": "USD", "to_ccy": "JPY"})
        assert r.ok is False
        assert "no rows" in r.error.lower()

    def test_single_row_series_omits_prior(self, tool, monkeypatch):
        """When only one observation is available the tool still works,
        it just doesn't include the 'Prior:' line."""
        monkeypatch.setattr(tool, "_fetch_series",
                            lambda _csv_id: [{"date": "2026-05-11", "value": "151.05"}])
        r = tool.execute({"from_ccy": "USD", "to_ccy": "JPY"})
        assert r.ok is True
        assert "Prior" not in r.content
        assert r.structured["prior"] is None

    def test_descending_input_still_picks_newest_as_latest(self, tool, monkeypatch):
        """Regression — confirmed against today's GCS bucket
        (2026-05-20): AI_Treasury_Project writes FX CSVs in
        DESCENDING date order (newest first). Older code took
        `rows[-1]` assuming ascending and surfaced the OLDEST date
        as "Latest", which the IntelligenceAgent on the test deploy
        flagged with a "date ordering appears inverted" note.

        Tool must sort defensively so it works regardless of the
        writer's order."""
        # Mimic the GCS shape: newest at index 0, oldest at the end.
        monkeypatch.setattr(tool, "_fetch_series", lambda _csv_id: [
            {"date": "2026-05-15", "value": "1.1627"},   # newest
            {"date": "2026-05-14", "value": "1.1678"},
            {"date": "2026-05-13", "value": "1.1711"},
            {"date": "2026-04-07", "value": "1.1571"},
            {"date": "2026-04-06", "value": "1.1545"},   # oldest
        ])
        r = tool.execute({"from_ccy": "USD", "to_ccy": "EUR"})
        assert r.ok is True
        # Latest must be the NEW date (05-15), not the OLD date (04-06).
        assert r.structured["latest"]["date"] == "2026-05-15"
        assert r.structured["latest"]["value"] == "1.1627"
        # Prior is the second-newest (05-14), not the second-oldest.
        assert r.structured["prior"]["date"] == "2026-05-14"
        assert r.structured["prior"]["value"] == "1.1678"

    def test_unsorted_input_still_sorted_before_pick(self, tool, monkeypatch):
        """Defence-in-depth: even if the upstream writer's order
        becomes randomized (or sparse with backfills), the tool
        still picks the newest by date."""
        monkeypatch.setattr(tool, "_fetch_series", lambda _csv_id: [
            {"date": "2026-05-10", "value": "1.10"},
            {"date": "2026-05-15", "value": "1.16"},    # actually newest
            {"date": "2026-04-01", "value": "1.05"},
            {"date": "2026-05-12", "value": "1.13"},
        ])
        r = tool.execute({"from_ccy": "USD", "to_ccy": "EUR"})
        assert r.ok is True
        assert r.structured["latest"]["date"] == "2026-05-15"
        assert r.structured["prior"]["date"] == "2026-05-12"
