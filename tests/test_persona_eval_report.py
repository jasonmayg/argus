"""
Unit tests for core.persona_eval.report.

Tests the rendering layer only — assemble small EvalRun fixtures by
hand and verify the markdown/JSON surface contracts. Method A/B/CLI
get their own test files.

Run:
    pytest tests/test_persona_eval_report.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.persona_eval.data_loader import NormalizedAlert, ScoredAlert, WindowSummary  # noqa: E402
from core.persona_eval.differentiation import jaccard_matrix  # noqa: E402
from core.persona_eval.llm_judge import (  # noqa: E402
    JudgeResult, JudgeSingleRunResult, WeightAdjustment,
)
from core.persona_eval.report import EvalRun, render_json, render_markdown  # noqa: E402


def _scored(i: int) -> ScoredAlert:
    return ScoredAlert(
        alert=NormalizedAlert(
            alert_id=f"a{i}", title=f"Alert {i}", country="USA",
            category="Liquidity Risk", score=50.0, published_at="2026-05-15",
            source="gdelt", severity="Medium", url=f"https://x/a{i}",
        ),
        effective_score=50.0,
    )


def _build_run() -> EvalRun:
    window = WindowSummary(
        alerts=[], window_start="2026-04-19", window_end="2026-05-19",
        raw_count=100, kept_count=80,
    )
    diff = jaccard_matrix({
        "cash_manager":  [f"a{i}" for i in range(20)],
        "fx_manager":    [f"a{i}" for i in range(10, 30)],     # 50% overlap
        "risk_manager":  [f"b{i}" for i in range(20)],         # disjoint
        "treasury_ops":  [f"a{i}" for i in range(19)] + ["x"],  # near-clone of cash_manager
    })
    judge_results = {
        "fx_manager": JudgeResult(
            persona="fx_manager",
            runs=[
                JudgeSingleRunResult(overall_relevance=0.75, high_relevance_count=14),
                JudgeSingleRunResult(overall_relevance=0.70, high_relevance_count=13),
            ],
            consensus_adjustments=[
                WeightAdjustment(
                    category="political_risk", current=0.5, suggested=0.9,
                    confidence="high", rationale="fx exposed to politics",
                ),
            ],
            disagreement_adjustments=[
                {"category": "credit_risk",
                 "reason": "runs disagreed on direction: ['down', 'up']",
                 "proposals": [{"suggested": 0.7, "confidence": "medium"},
                               {"suggested": 1.3, "confidence": "medium"}]},
            ],
        ),
    }
    return EvalRun(
        run_at="2026-05-19T06:00:00+00:00",
        window=window,
        top_n=20,
        scored_by_persona={
            "cash_manager":   [_scored(i) for i in range(20)],
            "fx_manager":     [_scored(i) for i in range(20)],
            "risk_manager":   [_scored(i) for i in range(20)],
            "treasury_ops":   [_scored(i) for i in range(20)],
        },
        differentiation=diff,
        judge_results=judge_results,
        llm_model="claude-sonnet-4-6",
        llm_cost_usd=0.12,
    )


class TestMarkdown:
    def test_includes_summary_block(self):
        md = render_markdown(_build_run())
        assert "# Persona Weight Evaluation" in md
        assert "## Summary" in md
        assert "Personas evaluated: 4" in md
        assert "cash_manager" in md
        assert "fx_manager" in md

    def test_includes_jaccard_matrix(self):
        md = render_markdown(_build_run())
        assert "Cross-Persona Differentiation" in md
        # 4 personas × 4 columns + header row + sep → at least 6 lines
        # containing pipe characters in the matrix section.
        assert md.count("|") >= 24

    def test_high_overlap_pair_flagged(self):
        """cash_manager and treasury_ops near-clone → flag must appear."""
        md = render_markdown(_build_run())
        assert "Outlier pairs" in md
        assert "cash_manager" in md
        assert "treasury_ops" in md
        assert "too similar" in md

    def test_per_persona_section_renders_when_judge_present(self):
        md = render_markdown(_build_run())
        assert "## Method B" in md
        assert "### fx_manager" in md
        assert "political_risk" in md         # the consensus adjustment
        assert "credit_risk" in md            # the disagreement

    def test_recommendations_split_high_vs_review(self):
        md = render_markdown(_build_run())
        assert "Auto-apply candidates" in md
        assert "Manual review queue" in md
        # fx_manager / political_risk has confidence=high → auto-apply
        # credit_risk disagreement → review
        # Both should be present in their respective sections.
        sections = md.split("Manual review queue")
        assert "political_risk" in sections[0]   # appears before "Manual review queue"

    def test_handles_no_judge_results(self):
        """--no-llm mode produces a report without Method B sections."""
        run = _build_run()
        run.judge_results = {}
        md = render_markdown(run)
        assert "Method A" in md
        # Method B header should not appear when there are no judge results.
        assert "## Method B" not in md


class TestJson:
    def test_round_trip_structure(self):
        js = render_json(_build_run())
        assert js["run_at"].startswith("2026-05-19")
        assert "differentiation" in js and js["differentiation"]["matrix"]
        assert "judge" in js
        assert "fx_manager" in js["judge"]
        assert len(js["judge"]["fx_manager"]["consensus_adjustments"]) == 1

    def test_top_n_capped(self):
        """If a persona's scored list has more than top_n, JSON output
        must not leak extras — the report contract says top-N only."""
        run = _build_run()
        run.scored_by_persona["cash_manager"] = [_scored(i) for i in range(50)]
        run.top_n = 5
        js = render_json(run)
        assert len(js["scored_by_persona"]["cash_manager"]) == 5
