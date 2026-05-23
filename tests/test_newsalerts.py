"""
Unit tests for `core/newsalerts.py` — pin the rescore pipeline so the
ML / rules backend split doesn't silently regress.

Background: PR #64 added a keyword bonus + low-quality filter + score
amplification, but nested all three inside the `if _ML_AVAILABLE:`
branch. Production deploys don't ship the ML model, so the entire
block was dead code there — holiday alerts kept appearing and scores
stayed clustered at the discrete severity tiers.

This suite locks in the post-refactor invariants:

  * keyword bonus runs in BOTH ML and rules backends
  * low-quality filter runs in BOTH backends
  * holiday-like titles get filtered when severity is Low and body is
    title-only
  * amplification stays ML-only (rules already produces a discrete
    distribution; amplifying it would over-clamp)
  * keyword-bonus cap (±1.0 on 0-10 scale) holds
  * word-boundary regex doesn't false-match (e.g. "ban" inside "banana")

Run:
    pytest tests/test_newsalerts.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import newsalerts


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────

def _alert(
    *,
    title:    str = "Sample alert",
    severity: float = 0.6,
    body:     str | None = None,
    country:  str = "USA",
    category_key: str = "",
    recency:  float = 1.0,
) -> dict:
    """Build a minimal alert row matching what Guardian snapshots emit."""
    row = {
        "title":    title,
        "severity": severity,
        "country":  country,
        "category_key": category_key,
        "recency":  recency,
    }
    # Body coalesces to title in `_extract_title_body` if not set, so
    # leave it unset to simulate the title-only case.
    if body is not None:
        row["body"] = body
    return row


@pytest.fixture
def force_rules_backend(monkeypatch):
    """Pin _ML_AVAILABLE = False so tests exercise the rules path
    even in dev environments that happen to have the ML model
    available. The production gap this PR fixes lives on the rules
    branch."""
    monkeypatch.setattr(newsalerts, "_ML_AVAILABLE", False)
    monkeypatch.setattr(newsalerts, "_scorer",       None)


# ──────────────────────────────────────────────────────────────────
# _apply_keyword_bonus — bounded adjustment for title-only items
# ──────────────────────────────────────────────────────────────────

class TestKeywordBonus:
    def test_no_bonus_when_body_is_substantive(self):
        """A long body means the model had real content to score — the
        keyword nudge is a title-only heuristic and must not apply."""
        long_body = "x" * 200
        before = 5.0
        after  = newsalerts._apply_keyword_bonus(before, "fraud crisis", long_body)
        assert after == before

    def test_high_risk_keyword_adds_positive_nudge(self, force_rules_backend):
        # "fraud" is in the +0.3 bucket. Title-only path triggers.
        out = newsalerts._apply_keyword_bonus(5.0, "Major fraud uncovered", "")
        assert out > 5.0
        assert out <= 5.0 + newsalerts._MAX_KEYWORD_ADJUSTMENT

    def test_holiday_keyword_subtracts(self):
        out = newsalerts._apply_keyword_bonus(5.0, "Christmas Day notice", "")
        assert out < 5.0

    def test_cap_at_plus_one(self):
        """Stack several high-risk keywords; total bump must not
        exceed +1.0 on the 0-10 scale."""
        title = "fraud sanctions crisis criminal money laundering breach"
        out = newsalerts._apply_keyword_bonus(5.0, title, "")
        assert out <= 5.0 + newsalerts._MAX_KEYWORD_ADJUSTMENT + 1e-9

    def test_cap_at_minus_one(self):
        title = "holiday memorial routine remarks survey vision"
        out = newsalerts._apply_keyword_bonus(5.0, title, "")
        assert out >= 5.0 - newsalerts._MAX_KEYWORD_ADJUSTMENT - 1e-9

    def test_word_boundary_does_not_match_substring(self):
        """`ban` keyword must not fire on `banana` or `urbanization`.
        The Copilot autofix on PR #64 added \\b anchors specifically
        for this."""
        out = newsalerts._apply_keyword_bonus(5.0, "Banana exporters note urbanization", "")
        assert out == 5.0

    def test_clamps_to_zero_to_ten(self):
        # Even with a strong negative push, output stays >= 0.
        below = newsalerts._apply_keyword_bonus(0.2, "holiday memorial remarks", "")
        assert below >= 0.0
        # Strong positive push from very-near-cap doesn't go past 10.
        above = newsalerts._apply_keyword_bonus(9.9, "fraud sanctions crisis criminal", "")
        assert above <= 10.0


# ──────────────────────────────────────────────────────────────────
# _amplify_score — ML-only step, mathematical contract
# ──────────────────────────────────────────────────────────────────

class TestAmplifyScore:
    def test_midpoint_is_fixed_point(self):
        assert newsalerts._amplify_score(5.0) == 5.0

    def test_above_midpoint_expands(self):
        # 5.4 → 5 + 0.4*2.5 = 6.0
        assert newsalerts._amplify_score(5.4) == pytest.approx(6.0, abs=1e-9)

    def test_below_midpoint_expands_downward(self):
        # 4.6 → 5 + (-0.4)*2.5 = 4.0
        assert newsalerts._amplify_score(4.6) == pytest.approx(4.0, abs=1e-9)

    def test_clamps_high_end(self):
        # 8.0 → 5 + 3*2.5 = 12.5 → clamp to 10
        assert newsalerts._amplify_score(8.0) == 10.0

    def test_clamps_low_end(self):
        # 2.0 → 5 + (-3)*2.5 = -2.5 → clamp to 0
        assert newsalerts._amplify_score(2.0) == 0.0


# ──────────────────────────────────────────────────────────────────
# _is_low_quality — body-length + score-threshold gate
# ──────────────────────────────────────────────────────────────────

class TestIsLowQuality:
    def test_high_score_never_low_quality(self):
        """Even with empty body, score >= threshold protects the row."""
        assert newsalerts._is_low_quality({"body": ""}, 5.5) is False
        assert newsalerts._is_low_quality({"body": ""}, 5.0) is False  # exactly threshold

    def test_low_score_short_body_is_low_quality(self):
        # body 10 chars, well under the 50-char floor
        assert newsalerts._is_low_quality({"body": "short"}, 4.0) is True

    def test_low_score_long_body_is_not_low_quality(self):
        long_body = "x" * 100
        assert newsalerts._is_low_quality({"body": long_body}, 4.0) is False

    def test_body_field_coalescing(self):
        """`analysis_text` and `summary` are fallbacks when `body`
        isn't present — `_is_low_quality` must use the same priority
        order as `_extract_title_body` so the gate sees the same
        text the keyword bonus does."""
        # analysis_text long → not low quality
        row = {"analysis_text": "x" * 80}
        assert newsalerts._is_low_quality(row, 4.0) is False
        # All sources empty → low quality
        assert newsalerts._is_low_quality({}, 4.0) is True


# ──────────────────────────────────────────────────────────────────
# rescore_news_alerts — full pipeline, rules backend
# ──────────────────────────────────────────────────────────────────

class TestRescoreRulesBackend:
    """The original PR #64 dead-code bug. These tests would have
    failed before the refactor: keyword bonus and low-quality filter
    were nested inside `if _ML_AVAILABLE:` so the rules path saw
    none of them."""

    def test_holiday_low_severity_short_body_gets_filtered(self, force_rules_backend):
        """The flagship symptom: a Low-severity 'Christmas Day' alert
        with no body should disappear from the output."""
        alerts = [
            _alert(title="Christmas Day",  severity=0.15),
            _alert(title="Genuine credit downgrade", severity=0.6, body="x" * 200),
        ]
        out = newsalerts.rescore_news_alerts(alerts)
        titles = [a["title"] for a in out]
        assert "Christmas Day" not in titles
        assert "Genuine credit downgrade" in titles

    def test_holiday_keyword_still_drops_score_even_when_kept(self, force_rules_backend):
        """A Medium-severity holiday alert (severity 0.6 → news_score
        6.0, above filter floor) is kept but the keyword nudge
        still pulls its score down. Compare to baseline."""
        no_kw   = _alert(title="Trade meeting outcome", severity=0.6)
        with_kw = _alert(title="Holiday observance notice", severity=0.6)
        out = newsalerts.rescore_news_alerts([no_kw, with_kw])
        by_title = {a["title"]: a["score"] for a in out}
        # holiday nudge -0.5 → news_score 5.5 → final < baseline (6.0 → 60)
        assert by_title["Holiday observance notice"] < by_title["Trade meeting outcome"]

    def test_rules_path_does_not_amplify(self, force_rules_backend):
        """Amplification stays ML-only. A Medium-severity row with no
        keyword adjustment should score severity * 10 * recency *
        country_weight * persona_weight = 0.6 * 10 * 1 * 1 * 1 = 60."""
        out = newsalerts.rescore_news_alerts([
            _alert(title="Neutral news item with no keyword triggers",
                   severity=0.6, body="x" * 100),
        ])
        assert len(out) == 1
        assert out[0]["score"] == pytest.approx(60.0, abs=0.01)

    def test_high_severity_short_body_not_filtered(self, force_rules_backend):
        """Severity 0.9 → news_score 9.0, way above the 5.0 floor.
        Low-quality filter should NOT drop it even with empty body."""
        out = newsalerts.rescore_news_alerts([
            _alert(title="Critical breach", severity=0.9),
        ])
        assert len(out) == 1
        assert out[0]["title"] == "Critical breach"

    def test_china_country_weight_applied_in_rules(self, force_rules_backend):
        """The 1.2× boost for China must survive the refactor — it
        was previously inside the rules formula directly."""
        out = newsalerts.rescore_news_alerts([
            _alert(title="Foo", severity=0.6, body="x"*100, country="USA"),
            _alert(title="Bar", severity=0.6, body="x"*100, country="China"),
        ])
        scores = {a["title"]: a["score"] for a in out}
        assert scores["Bar"] == pytest.approx(scores["Foo"] * 1.2, abs=0.01)

    def test_persona_weight_applied_in_rules(self, force_rules_backend):
        """persona_weight (from risk_weights[category_key]) must still
        scale the rules-path output. ML path bakes it into prediction
        via user_weight, but rules needs the explicit multiply."""
        a = _alert(title="X", severity=0.6, body="x"*100,
                   category_key="credit_risk")
        out_default = newsalerts.rescore_news_alerts([a])
        out_boosted = newsalerts.rescore_news_alerts(
            [a], risk_weights={"credit_risk": 1.5}
        )
        assert out_boosted[0]["score"] == pytest.approx(
            out_default[0]["score"] * 1.5, abs=0.01
        )

    def test_filtered_count_log(self, force_rules_backend, capsys):
        """When low-quality rows get dropped, the log line surfaces
        the count — useful for operators to spot regressions."""
        alerts = [
            _alert(title="Christmas Day", severity=0.15),
            _alert(title="Memorial Day",  severity=0.15),
            _alert(title="Real news",     severity=0.6, body="x"*200),
        ]
        newsalerts.rescore_news_alerts(alerts)
        captured = capsys.readouterr()
        assert "Filtered 2 low-quality alerts" in captured.out


# ──────────────────────────────────────────────────────────────────
# _extract_title_body — body coalescing helper
# ──────────────────────────────────────────────────────────────────

class TestExtractTitleBody:
    def test_body_field_wins(self):
        t, b = newsalerts._extract_title_body(
            {"title": "T", "body": "B", "analysis_text": "A", "summary": "S"}
        )
        assert t == "T" and b == "B"

    def test_falls_back_to_analysis_text(self):
        _, b = newsalerts._extract_title_body(
            {"title": "T", "analysis_text": "A", "summary": "S"}
        )
        assert b == "A"

    def test_falls_back_to_summary(self):
        _, b = newsalerts._extract_title_body({"title": "T", "summary": "S"})
        assert b == "S"

    def test_falls_back_to_title_when_no_body(self):
        """Title-only path: body defaults to title so keyword matching
        still sees the words. The `_apply_keyword_bonus` gate then
        notices body length ~ title length and treats it as
        title-only."""
        _, b = newsalerts._extract_title_body({"title": "Christmas Day"})
        assert b == "Christmas Day"

    def test_headline_alias(self):
        t, _ = newsalerts._extract_title_body({"headline": "H"})
        assert t == "H"
