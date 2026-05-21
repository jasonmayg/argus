"""
Unit tests for `GuardianEngine._infer_categories` after the
classification-granularity expansion (B2 in the 2026-05-20 cleanup).

Background:
  Phase 2.7 persona eval flagged that 142/162 GCS-sourced alerts
  were landing in Market Risk because the source defaults only
  mapped ECB / world_bank / fred / bank_of_canada to single
  categories, and the new GCS sources (fed / boe / boj / fca /
  treasury) had no entries at all so they fell through to
  term-based detection that mostly missed.

  This file pins the new behavior so a future "let me just clean
  up these mappings" PR doesn't quietly collapse persona
  differentiation again.

Run:
    pytest tests/test_guardian_infer_categories.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from guardian_poc.src.guardian import GuardianEngine

CONFIG = ROOT / "guardian_poc" / "config"


@pytest.fixture(scope="module")
def engine():
    """A real engine with the real config — we want
    `_infer_categories` to filter against the actual risk_taxonomy
    keys, not an empty stub."""
    return GuardianEngine(
        countries_cfg=str(CONFIG / "countries.yaml"),
        keywords_cfg=str(CONFIG / "risk_keywords.yaml"),
        personas_cfg=str(CONFIG / "personas.yaml"),
    )


def _keys(cats):
    """Strip the (key, high_alert) pairs to a sorted set of keys
    for readable assertions."""
    return sorted(k for k, _ in cats)


class TestNewSourceDefaults:
    """Each press-release source the GCS bridge feeds in must have
    a source default — otherwise short headlines produce zero
    categories and the alert drops at `score_events` time.
    Sources NOT covered here today: AI_Treasury_Project may add
    more in the future; add entries to source_defaults when they do."""

    def test_fed_maps_to_market_and_regulatory(self, engine):
        # Empty text → only source_defaults fire. A Fed press release
        # with a non-keyword headline must still surface in both
        # Market Risk and Regulatory Risk pools so the engine has
        # something to score, and the two persona weights
        # (market vs regulatory) discriminate.
        cats = engine._infer_categories(text="", source="fed")
        assert _keys(cats) == ["market_risk", "regulatory_risk"]

    def test_boe_maps_to_market_and_regulatory(self, engine):
        cats = engine._infer_categories(text="", source="boe")
        assert _keys(cats) == ["market_risk", "regulatory_risk"]

    def test_boj_maps_to_market_only(self, engine):
        """BoJ headlines have historically been monetary policy
        heavy; mapping to regulatory_risk too would dilute signal
        without much basis. Single category by design."""
        cats = engine._infer_categories(text="", source="boj")
        assert _keys(cats) == ["market_risk"]

    def test_fca_maps_to_regulatory_and_operational(self, engine):
        """FCA UK is a conduct authority — no market policy mandate.
        Regulatory + operational risk only."""
        cats = engine._infer_categories(text="", source="fca")
        assert _keys(cats) == ["operational_risk", "regulatory_risk"]

    def test_treasury_maps_to_market_and_political(self, engine):
        """U.S. Treasury straddles debt management / rates
        (market_risk) and sanctions / political tools
        (political_risk). Not regulatory — that's other agencies."""
        cats = engine._infer_categories(text="", source="treasury")
        assert _keys(cats) == ["market_risk", "political_risk"]


class TestExpandedExistingDefaults:
    """Pre-existing source_defaults were tightened: ECB / BoC / world
    bank / IMF / BIS now also fan out into a second category. Pin
    the new behavior so we don't silently regress when refactoring."""

    def test_ecb_gains_regulatory(self, engine):
        cats = engine._infer_categories(text="", source="ecb")
        assert "market_risk" in _keys(cats)
        assert "regulatory_risk" in _keys(cats)

    def test_bank_of_canada_gains_regulatory(self, engine):
        cats = engine._infer_categories(text="", source="bank_of_canada")
        assert "market_risk" in _keys(cats)
        assert "regulatory_risk" in _keys(cats)

    def test_world_bank_gains_credit(self, engine):
        cats = engine._infer_categories(text="", source="world_bank")
        assert "market_risk" in _keys(cats)
        assert "credit_risk" in _keys(cats)

    def test_imf_gains_credit(self, engine):
        cats = engine._infer_categories(text="", source="imf")
        assert "market_risk" in _keys(cats)
        assert "credit_risk" in _keys(cats)


class TestNewTermMapEntries:
    """The added vocab fires when the trigger word appears in title
    or summary. Tests use bare strings (no source) to isolate term
    detection from source_defaults."""

    @pytest.mark.parametrize("text,expected", [
        # Central bank vocab — should produce market_risk.
        ("Fed announces monetary policy update", "market_risk"),
        ("FOMC raises rate decision",            "market_risk"),
        # Supervisory vocab — should produce regulatory_risk (and
        # sometimes credit_risk for stress test).
        ("Capital requirements tightened",       "regulatory_risk"),
        ("Strengthening banking supervision",    "regulatory_risk"),
        ("New supervisory framework released",   "regulatory_risk"),
        ("Enforcement action announced",         "regulatory_risk"),
        # Crypto / digital asset → regulatory.
        ("FCA paper on tokenisation",            "regulatory_risk"),
        ("BoE views on stablecoin regulation",   "regulatory_risk"),
        ("Digital asset oversight refresh",      "regulatory_risk"),
        # Operational.
        ("Major payment system outage",          "operational_risk"),
        ("Bank discloses cyber breach",          "operational_risk"),
    ])
    def test_term_triggers(self, engine, text, expected):
        cats = engine._infer_categories(text=text.lower(), source="unknown_source")
        assert expected in _keys(cats), (
            f"text {text!r} should have produced {expected}, "
            f"got {_keys(cats)}"
        )

    def test_stress_test_fires_both_credit_and_regulatory(self, engine):
        """Multi-category trigger — "stress test" appears twice in
        term_map intentionally so the same headline fans out into
        BOTH credit and regulatory pools. Persona weights for these
        two are different, so the fan-out is the discrimination
        signal."""
        cats = engine._infer_categories(
            text="bank of england stress test results",
            source="boe",
        )
        keys = _keys(cats)
        assert "credit_risk" in keys
        assert "regulatory_risk" in keys


class TestNoDuplicateCategoryKeys:
    """Returned list must dedupe — if a category comes both from
    term_map AND source_defaults, score_events would otherwise
    produce two identical ScoredAlerts per (country, category)
    pair, double-counting in the alert pool."""

    def test_dedupe_when_text_matches_source_default(self, engine):
        # Text contains "interest" (term_map → market_risk) AND
        # source is "ecb" (source_defaults → also market_risk).
        # Should appear exactly once.
        cats = engine._infer_categories(
            text="interest rates",
            source="ecb",
        )
        keys = [k for k, _ in cats]
        assert keys.count("market_risk") == 1


class TestUnknownSourceFallback:
    """A novel source not in source_defaults must still get
    categories from term_map alone. This is the silent-regression
    path — if the engine's text matching breaks, headlines from
    every source the GCS bridge doesn't have a hint for
    contribute nothing."""

    def test_unknown_source_still_term_classified(self, engine):
        cats = engine._infer_categories(
            text="inflation hits new high",
            source="some_brand_new_outlet",
        )
        assert "market_risk" in _keys(cats)

    def test_unknown_source_no_text_returns_empty(self, engine):
        """Empty text + unknown source → empty result is the
        expected drop behavior. score_events filters these out."""
        cats = engine._infer_categories(text="", source="some_brand_new_outlet")
        assert cats == []
