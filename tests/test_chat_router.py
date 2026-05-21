"""
Tests for `core.agents.chat_router.classify`.

The router is the lever between cheap-and-fast and rich-and-slow
on /api/chat. Mis-routing costs real money + latency on the agent
side, or a thinner answer on the legacy side. So both directions
get pinned here: queries that MUST stay on legacy + queries that
MUST escalate.

Run:
    pytest tests/test_chat_router.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.agents.chat_router import classify, COMPLEX_THRESHOLD


class TestStaysOnLegacy:
    """Queries that obviously don't need agent budget."""

    def test_empty_query(self):
        d = classify("")
        assert d.route == "simple"
        assert d.score == 0

    def test_short_factual_lookup(self):
        d = classify("What's the EUR/USD rate?")
        assert d.route == "simple"

    def test_single_entity_short(self):
        d = classify("Show recent Fed news")
        assert d.route == "simple"

    def test_definition_question(self):
        """`what is X` isn't on the analytical-verb list — it's a
        definition lookup the legacy RAG handles fine."""
        d = classify("What is operational risk?")
        assert d.route == "simple"

    def test_medium_length_alone_does_not_escalate(self):
        """80-180 char query with no other signal stays on legacy.
        Length is one signal among several — alone it's only +1
        toward a 2-point threshold."""
        msg = "Tell me about the recent press release from the Federal Reserve about " \
              "their balance sheet"
        assert len(msg) >= 80 and len(msg) < 180
        d = classify(msg)
        # "fed" + "federal reserve" still dedup to one entity, no
        # analytical verb, no cross connective → only length point.
        assert d.route == "simple"


class TestEscalatesToAgent:
    """Queries that benefit from multi-turn tool use."""

    def test_compare_two_entities(self):
        d = classify("Compare ECB and BoE recent rate decisions")
        assert d.route == "complex"
        assert "verb:compare" in d.reasons
        # Both ECB and BoE are recognized entities → entity bonus too.
        assert any(r.startswith("entities:") for r in d.reasons)

    def test_analyze_with_cross_connective(self):
        d = classify("Analyze regulatory shifts across the UK and EU")
        assert d.route == "complex"

    def test_summarize_long_query(self):
        d = classify(
            "Summarize the regulatory landscape across UK, EU, and "
            "US over the past 30 days and what it means for treasury"
        )
        assert d.route == "complex"

    def test_impact_question_multi_entity(self):
        d = classify(
            "How does the Bank of Canada's recent supervisory framework "
            "change affect Canadian credit exposure?"
        )
        assert d.route == "complex"

    def test_explicit_breakdown_request(self):
        """A breakdown-style query that's also long enough to clear
        the length threshold. The verb alone isn't enough — by
        design, a short 'walk me through X' for a single entity
        shouldn't burn agent budget. Multiple signals required."""
        d = classify(
            "Walk me through the latest FOMC decision and explain "
            "what it means for USD treasury positioning in major markets"
        )
        assert d.route == "complex"


class TestScoringInternals:
    """Pin the score arithmetic so a refactor that quietly drops a
    signal source fails a test instead of regressing routing
    accuracy in production."""

    def test_analytical_verb_dedup(self):
        """A query containing the same verb twice counts ONCE.
        Without dedup, "explain how does this explain that"
        would inflate the score."""
        d = classify("explain how does explain why")
        # 'explain' fires once + 'how does' fires once + 'why did'
        # might not fire (no "did"). The exact count isn't the
        # contract — the contract is that 'explain' contributes 1,
        # not 2.
        explain_hits = [r for r in d.reasons if r == "verb:explain"]
        assert len(explain_hits) == 1

    def test_overlapping_verbs_dedup(self):
        """Regression — `_ANALYTICAL_VERBS` contains both 'how does'
        and 'how do'. Naively iterating the list, a query containing
        'how does' would also match 'how do' (substring) and add a
        second verb point. Fix: walk longest-first and consume matched
        spans. Pinning here so a future refactor can't silently
        reintroduce the overlap and mis-route every 'how does X'
        question into the expensive agent path on verb-double-count
        alone."""
        d = classify("how does interest rate policy work")
        # 'how does' should fire once. 'how do' must NOT fire as a
        # second verb hit — its span was consumed by 'how does'.
        verb_hits = [r for r in d.reasons if r.startswith("verb:")]
        assert "verb:how does" in verb_hits
        assert "verb:how do" not in verb_hits

    def test_cross_connective_preserves_word_boundary(self):
        """Regression — the connective list includes ' vs ' WITH
        spaces, specifically so it doesn't match inside 'verses' /
        'diverse'. Earlier code stripped spaces before matching,
        which broke that safeguard. Test that 'verses' doesn't
        trigger a connective hit."""
        d = classify("This verses our internal policy")
        connective_hits = [r for r in d.reasons if r.startswith("connective:")]
        assert connective_hits == []

    def test_cross_connective_caps_at_one(self):
        """Two connective hits should still only contribute +1 —
        signal is binary ('the question spans things')."""
        d = classify("US versus EU and UK across all sources")
        connective_hits = [r for r in d.reasons if r.startswith("connective:")]
        assert len(connective_hits) == 1

    def test_entity_multi_required(self):
        """A SINGLE entity mention does not contribute the entity
        point — the signal is multi-entity synthesis."""
        d = classify("recent fed news")
        ent_hits = [r for r in d.reasons if r.startswith("entities:")]
        assert ent_hits == []

    def test_threshold_constant_is_load_bearing(self):
        """If someone bumps COMPLEX_THRESHOLD, this test fails so
        they have to confirm the change is intentional. Cost-wise,
        every threshold drop sends more traffic to the paid path."""
        assert COMPLEX_THRESHOLD == 2

    def test_long_query_alone_scores_two(self):
        """A query >= 180 chars contributes +2 by itself, hitting
        the threshold. Tested explicitly so a future "let me simplify
        the length tier" PR notices it changed the routing surface."""
        long_q = "a" * 200
        d = classify(long_q)
        assert d.score >= 2
        assert d.route == "complex"


class TestEntityWordBoundary:
    """Substring matches on entity names would tag every query
    containing "use" / "uses" as a US mention. Confirm the regex
    boundaries hold."""

    def test_us_does_not_match_within_use(self):
        d = classify("How do I use the persona dashboard?")
        # No entity should fire from "use".
        ent_hits = [r for r in d.reasons if r.startswith("entities:")]
        assert ent_hits == []

    def test_treasury_matches_as_word(self):
        d = classify("us treasury and uk treasury")
        ent_hits = [r for r in d.reasons if r.startswith("entities:")]
        # Both "us" + "treasury" + "uk" + "treasury" should be
        # found (and dedup'd) — 2+ distinct entities.
        assert ent_hits == ["entities:3"] or ent_hits == ["entities:2"]


class TestReturnShape:
    """The route layer reads `route`, `score`, `reasons` — pin the
    public contract so adding a field doesn't break the consumer."""

    def test_decision_has_all_three_fields(self):
        d = classify("anything")
        assert hasattr(d, "route")
        assert hasattr(d, "score")
        assert hasattr(d, "reasons")
        assert d.route in ("simple", "complex")
        assert isinstance(d.score, int)
        assert isinstance(d.reasons, list)
