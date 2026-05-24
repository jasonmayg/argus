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
    """New signature: `_is_low_quality(news_score, body)`. Body is
    passed in (not re-extracted from a row dict) so this sees the
    exact same text source as `_apply_keyword_bonus` above it in
    `rescore_news_alerts` — Copilot review on the original refactor
    flagged that the old signature re-extracted with a different
    fallback chain (no title fallback), causing title-only alerts
    with long titles to be over-filtered."""

    def test_high_score_never_low_quality(self):
        """Even with empty body, score >= threshold protects the row."""
        assert newsalerts._is_low_quality(5.5, "") is False
        assert newsalerts._is_low_quality(5.0, "") is False  # exactly threshold

    def test_low_score_short_body_is_low_quality(self):
        # body 10 chars, well under the 50-char floor
        assert newsalerts._is_low_quality(4.0, "short") is True

    def test_low_score_long_body_is_not_low_quality(self):
        long_body = "x" * 100
        assert newsalerts._is_low_quality(4.0, long_body) is False

    def test_whitespace_only_body_counted_as_empty(self):
        """Leading/trailing whitespace is stripped before length
        check, so a body of "   " counts as 0 chars and triggers
        filtering."""
        assert newsalerts._is_low_quality(4.0, "   \n\t  ") is True

    def test_consistency_with_extract_title_body(self):
        """When `rescore_news_alerts` calls `_extract_title_body(row)`
        and then `_is_low_quality(news_score, body)`, a title-only
        alert with a long title must NOT be filtered (body falls
        back to title via the helper, so body length = title length).
        Previously `_is_low_quality` did its own field lookup with
        no title fallback and treated this case as `len(body)=0`."""
        row = {"title": "x" * 120}   # title-only, long title, no body field
        _, body = newsalerts._extract_title_body(row)
        # body now equals title (120 chars) → should NOT be low-quality
        assert newsalerts._is_low_quality(4.0, body) is False


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

    def test_title_only_long_title_not_overfiltered(self, force_rules_backend):
        """Regression test for the Copilot review on the first fix
        PR: a Low-severity alert with no body field but a long title
        used to be filtered because `_is_low_quality` did its own
        empty-string fallback when `body` was missing. After the fix,
        `_extract_title_body` falls back to title and `_is_low_quality`
        sees that text — the row stays."""
        long_title = "Detailed credit downgrade notice " * 5   # ~165 chars
        out = newsalerts.rescore_news_alerts([
            _alert(title=long_title, severity=0.15),   # no body field
        ])
        assert len(out) == 1, "title-only long-title row was wrongly filtered"
        assert out[0]["title"] == long_title


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


# ──────────────────────────────────────────────────────────────────
# rescore_news_alerts — ML failure handling
# ──────────────────────────────────────────────────────────────────

class _FlakyScorer:
    """Fake `_scorer` that always raises — exercises the per-row ML
    failure fallback path without depending on an actual ML model."""
    def predict_one(self, **kwargs):
        raise RuntimeError("model file corrupt")


class TestMLFailureSummary:
    """Copilot review on the rules-fallback PR flagged that the
    per-row `print()` on ML failure could flood logs with one line
    per alert on a batch of thousands. The summary should be a
    single line at the end of the batch with the failure count +
    the first error message."""

    def test_failures_collapse_to_one_summary_line(self, monkeypatch, capsys):
        monkeypatch.setattr(newsalerts, "_ML_AVAILABLE", True)
        monkeypatch.setattr(newsalerts, "_scorer",       _FlakyScorer())

        alerts = [
            _alert(title=f"Alert {i}", severity=0.6, body="x" * 100)
            for i in range(5)
        ]
        out = newsalerts.rescore_news_alerts(alerts)
        captured = capsys.readouterr()

        # All 5 fell back to rules-based scoring (not dropped).
        assert len(out) == 5
        # Exactly ONE summary line, not 5 per-row lines.
        failure_lines = [
            ln for ln in captured.out.splitlines()
            if "ML predict failed" in ln
        ]
        assert len(failure_lines) == 1, (
            f"Expected exactly 1 summary line, got {len(failure_lines)}:\n"
            + "\n".join(failure_lines)
        )
        # Summary mentions count + the first row's title + the underlying error.
        assert "5 alert(s)" in failure_lines[0]
        assert "Alert 0"   in failure_lines[0]
        assert "model file corrupt" in failure_lines[0]

    def test_no_summary_when_no_failures(self, force_rules_backend, capsys):
        """When ML isn't even running (rules backend), the failure
        summary must not print — it would be misleading to claim ML
        failed when ML was never tried."""
        newsalerts.rescore_news_alerts([
            _alert(title="X", severity=0.6, body="x"*100),
        ])
        captured = capsys.readouterr()
        assert "ML predict failed" not in captured.out


# ──────────────────────────────────────────────────────────────────
# Precomputed score cache — write_scored_snapshot +
# load_scored_snapshot_if_fresh
# ──────────────────────────────────────────────────────────────────

import json     # noqa: E402  — needed only by tests below
import os       # noqa: E402
import time     # noqa: E402

def _write_snapshot(path, alerts):
    """Helper: write a minimal Guardian-shaped snapshot with the
    given alerts list. Used by the cache tests below."""
    payload = {
        "alerts":   alerts,
        "data_mode": "live_local",
        "threshold": 40,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


class TestScoredSnapshotCache:
    """The bridge precomputes `<snapshot>_scored.json` on write so
    the dashboard doesn't pay the ~30s ML cost per request. These
    tests pin the freshness semantics + helper contract."""

    def test_scored_path_naming_convention(self, tmp_path):
        """`alerts_latest.json` should map to `alerts_latest_scored.json`
        in the same directory — the dashboard's lookup depends on it."""
        snap = tmp_path / "guardian_poc" / "data" / "alerts_latest.json"
        out  = newsalerts._scored_path_for(snap)
        assert out.parent == snap.parent
        assert out.name   == "alerts_latest_scored.json"

    def test_write_then_load_round_trips(self, force_rules_backend, tmp_path):
        snap = tmp_path / "alerts_latest.json"
        alerts = [
            _alert(title="Real news",     severity=0.6, body="x"*100),
            _alert(title="Christmas Day", severity=0.15),
        ]
        _write_snapshot(snap, alerts)

        out_path = newsalerts.write_scored_snapshot(snap)
        assert out_path is not None
        assert out_path.exists()

        cached = newsalerts.load_scored_snapshot_if_fresh(snap)
        assert cached is not None
        # The Christmas Day alert should have been dropped by the
        # low-quality filter at write time.
        cached_titles = [a["title"] for a in cached["alerts"]]
        assert "Christmas Day" not in cached_titles
        assert "Real news"     in cached_titles
        # Freshness metadata is wired up.
        meta = cached["_score_meta"]
        assert meta["source_mtime"] == os.path.getmtime(snap)
        assert "scored_at" in meta
        assert "alert_count" in meta

    def test_returns_none_when_source_changed(self, force_rules_backend, tmp_path):
        """If the snapshot is rewritten after the cache, mtime no
        longer matches → cache is stale → dashboard must live-rescore."""
        snap = tmp_path / "alerts_latest.json"
        _write_snapshot(snap, [_alert(title="A", severity=0.6, body="x"*100)])
        newsalerts.write_scored_snapshot(snap)
        # Sleep > filesystem mtime granularity (1s on most Linux fs)
        # so the rewrite produces a measurably different mtime.
        time.sleep(1.05)
        _write_snapshot(snap, [_alert(title="B", severity=0.6, body="x"*100)])

        cached = newsalerts.load_scored_snapshot_if_fresh(snap)
        assert cached is None, (
            "Cache must invalidate when source mtime changes — "
            "otherwise dashboard would serve stale post-bridge data."
        )

    def test_returns_none_when_cache_missing(self, force_rules_backend, tmp_path):
        snap = tmp_path / "alerts_latest.json"
        _write_snapshot(snap, [_alert(title="A", severity=0.6, body="x"*100)])
        # Never wrote the scored cache.
        assert newsalerts.load_scored_snapshot_if_fresh(snap) is None

    def test_returns_none_when_source_missing(self, force_rules_backend, tmp_path):
        """Edge case: cache file exists but snapshot was deleted.
        Dashboard would 404 anyway, but the helper shouldn't crash."""
        snap = tmp_path / "alerts_latest.json"
        scored = newsalerts._scored_path_for(snap)
        scored.parent.mkdir(parents=True, exist_ok=True)
        scored.write_text(
            '{"alerts": [], "_score_meta": {"source_mtime": 0, "ml_used": false}}',
            encoding="utf-8",
        )
        assert newsalerts.load_scored_snapshot_if_fresh(snap) is None

    def test_returns_none_when_cache_corrupt(self, force_rules_backend, tmp_path):
        """Hand-edited / partial-write corrupt JSON: don't crash,
        just return None so caller live-rescores."""
        snap = tmp_path / "alerts_latest.json"
        _write_snapshot(snap, [_alert(title="A", severity=0.6, body="x"*100)])
        scored = newsalerts._scored_path_for(snap)
        scored.write_text("{not valid json", encoding="utf-8")
        assert newsalerts.load_scored_snapshot_if_fresh(snap) is None

    def test_returns_none_when_cache_has_no_score_meta(
        self, force_rules_backend, tmp_path
    ):
        """Cache files written by a hypothetical pre-this-PR bridge
        wouldn't have `_score_meta`. They must be treated as stale."""
        snap = tmp_path / "alerts_latest.json"
        _write_snapshot(snap, [_alert(title="A", severity=0.6, body="x"*100)])
        scored = newsalerts._scored_path_for(snap)
        scored.write_text(json.dumps({"alerts": []}), encoding="utf-8")
        assert newsalerts.load_scored_snapshot_if_fresh(snap) is None

    def test_returns_none_when_ml_used_mismatches(self, monkeypatch, tmp_path):
        """The dashboard process's `_ML_AVAILABLE` flag is the source
        of truth for which backend it serves. If the cache was built
        when ML was unavailable but the dashboard now has ML (e.g.,
        bridge ran before model deploy + service restart picked up
        the model), loading the cache would silently downgrade scoring.

        Copilot review on PR #67 flagged this: source_mtime alone
        isn't enough — we have to compare backends too. Cover both
        directions: rules-built cache loaded by ML process, and
        vice versa."""
        snap = tmp_path / "alerts_latest.json"
        _write_snapshot(snap, [_alert(title="A", severity=0.6, body="x"*100)])

        # Write cache as the rules backend (_ML_AVAILABLE=False).
        monkeypatch.setattr(newsalerts, "_ML_AVAILABLE", False)
        monkeypatch.setattr(newsalerts, "_scorer",       None)
        newsalerts.write_scored_snapshot(snap)

        # Now flip to ML available — cache must be rejected.
        monkeypatch.setattr(newsalerts, "_ML_AVAILABLE", True)
        assert newsalerts.load_scored_snapshot_if_fresh(snap) is None, (
            "Rules-built cache must NOT be served by an ML-enabled "
            "process — would silently change scoring semantics."
        )

        # Reverse: write under ML, load under rules. Same problem.
        monkeypatch.setattr(newsalerts, "_ML_AVAILABLE", True)
        # `_scorer` doesn't need to be a real scorer here — the cache
        # write path doesn't call it (rescore_news_alerts uses
        # severity-based fallback when scoring fails, and the test
        # alerts have severity set).
        newsalerts.write_scored_snapshot(snap)
        monkeypatch.setattr(newsalerts, "_ML_AVAILABLE", False)
        assert newsalerts.load_scored_snapshot_if_fresh(snap) is None, (
            "ML-built cache must NOT be served by a rules-only process."
        )

    def test_ml_used_match_serves_cache(self, force_rules_backend, tmp_path):
        """Positive control for the ml_used check: when the flag
        matches (rules wrote, rules reads), cache IS served."""
        snap = tmp_path / "alerts_latest.json"
        _write_snapshot(snap, [_alert(title="A", severity=0.6, body="x"*100)])
        newsalerts.write_scored_snapshot(snap)
        cached = newsalerts.load_scored_snapshot_if_fresh(snap)
        assert cached is not None
        assert cached["_score_meta"]["ml_used"] is False

    def test_write_skips_when_source_missing(self, tmp_path):
        """Bridge calls write_scored_snapshot in a try/except, but
        the helper itself should return None (not raise) when the
        source file doesn't exist — keeps the bridge log line
        clean on edge cases."""
        snap = tmp_path / "does_not_exist.json"
        assert newsalerts.write_scored_snapshot(snap) is None
