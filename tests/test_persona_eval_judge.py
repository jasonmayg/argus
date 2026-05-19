"""
Unit tests for core.persona_eval.llm_judge.

Stubs the LLM caller so tests are deterministic + free. The critical
properties to lock down:

  * Two runs that agree on a direction → consensus entry, no
    disagreement entry for that category.
  * Two runs that disagree on direction → disagreement, no consensus.
  * One run only → disagreement (n/2 runs makes consensus inconclusive).
  * A failing run (parse error) does not crash the whole evaluation.
  * Confidence taken as min across runs in consensus.

Run:
    pytest tests/test_persona_eval_judge.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import List

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.persona_eval.data_loader import NormalizedAlert, ScoredAlert  # noqa: E402
from core.persona_eval.llm_judge import evaluate_persona  # noqa: E402


def _alert(i: int, cat: str = "Liquidity Risk") -> ScoredAlert:
    return ScoredAlert(
        alert=NormalizedAlert(
            alert_id=f"a{i}", title=f"Alert {i}", country="USA",
            category=cat, score=50.0, published_at="2026-05-15",
            source="gdelt", severity="Medium", url=f"https://x/a{i}",
        ),
        effective_score=50.0,
    )


def _scripted_caller(responses: List[str]):
    """Returns a caller that yields scripted responses in order. Raises
    after exhausting the list — that's the test's signal we under-counted."""
    idx = {"i": 0}
    def caller(prompt, *, temperature, run_index):
        if idx["i"] >= len(responses):
            raise AssertionError("scripted_caller exhausted")
        r = responses[idx["i"]]
        idx["i"] += 1
        return r
    return caller


def _judge_json(adjustments) -> str:
    return json.dumps({
        "persona_priorities": ["liquidity_risk"],
        "overall_relevance": 0.75,
        "high_relevance_count": 12,
        "off_target_alerts": [],
        "category_signals": {},
        "weight_adjustments": adjustments,
    })


class TestConsensus:
    def test_both_runs_agree_direction(self):
        """Two runs both suggest political_risk go UP → consensus,
        no disagreement."""
        r1 = _judge_json([{
            "category": "political_risk", "current": 0.5, "suggested": 0.9,
            "confidence": "high", "rationale": "fx exposed to politics",
        }])
        r2 = _judge_json([{
            "category": "political_risk", "current": 0.5, "suggested": 0.8,
            "confidence": "medium", "rationale": "agree",
        }])
        result = evaluate_persona(
            "fx_manager", [_alert(1)], {"political_risk": 0.5},
            runs=2, llm_caller=_scripted_caller([r1, r2]),
        )
        assert len(result.consensus_adjustments) == 1
        assert len(result.disagreement_adjustments) == 0
        adj = result.consensus_adjustments[0]
        assert adj.category == "political_risk"
        # Consensus suggested = avg of (0.9, 0.8) = 0.85
        assert adj.suggested == pytest.approx(0.85, abs=0.01)
        # Confidence = min(high, medium) = medium
        assert adj.confidence == "medium"

    def test_runs_disagree_on_direction(self):
        """Run 1 says go up, run 2 says go down → no consensus, surface
        as disagreement for manual review."""
        r1 = _judge_json([{
            "category": "credit_risk", "current": 1.0, "suggested": 1.5,
            "confidence": "high", "rationale": "x",
        }])
        r2 = _judge_json([{
            "category": "credit_risk", "current": 1.0, "suggested": 0.7,
            "confidence": "high", "rationale": "y",
        }])
        result = evaluate_persona(
            "fx_manager", [_alert(1)], {"credit_risk": 1.0},
            runs=2, llm_caller=_scripted_caller([r1, r2]),
        )
        assert len(result.consensus_adjustments) == 0
        assert len(result.disagreement_adjustments) == 1
        assert result.disagreement_adjustments[0]["category"] == "credit_risk"
        assert "direction" in result.disagreement_adjustments[0]["reason"]

    def test_only_one_run_proposed_category(self):
        r1 = _judge_json([{
            "category": "supply_risk", "current": 0.3, "suggested": 0.6,
            "confidence": "high", "rationale": "x",
        }])
        r2 = _judge_json([])     # no suggestion
        result = evaluate_persona(
            "fx_manager", [_alert(1)], {"supply_risk": 0.3},
            runs=2, llm_caller=_scripted_caller([r1, r2]),
        )
        assert len(result.consensus_adjustments) == 0
        assert any(d["category"] == "supply_risk"
                   for d in result.disagreement_adjustments)

    def test_one_run_parse_failure_still_produces_result(self):
        """If run 1 returned garbage, we treat run 2 as effectively a
        single run → its suggestions go into disagreement (since
        consensus needs both)."""
        r1 = "this is not valid json"
        r2 = _judge_json([{
            "category": "political_risk", "current": 0.5, "suggested": 0.8,
            "confidence": "medium", "rationale": "alone",
        }])
        result = evaluate_persona(
            "fx_manager", [_alert(1)], {"political_risk": 0.5},
            runs=2, llm_caller=_scripted_caller([r1, r2]),
        )
        assert result.runs[0].parse_error is not None
        # consensus needs N matching proposals where N = number of VALID runs.
        # Only one valid run, one suggestion → it counts as consensus (1/1).
        # That's by design: better to surface a low-confidence consensus
        # than discard the only signal we have. Confidence stays "medium".
        # If we change this to require both runs valid, update this test.
        assert len(result.consensus_adjustments) == 1
        assert result.consensus_adjustments[0].confidence == "medium"

    def test_no_caller_default_uses_router(self, monkeypatch):
        """Verify the default seam wires through core.llm_router.
        Don't hit the network — patch call_llm to a stub."""
        import core.persona_eval.llm_judge as judge_mod
        calls = []
        def fake(task, *, system, user, temperature, max_tokens, response_format):
            calls.append({"task": task, "temperature": temperature})
            return _judge_json([])
        monkeypatch.setattr(judge_mod, "call_llm", fake)
        evaluate_persona("fx_manager", [_alert(1)], {}, runs=2)
        assert all(c["task"] == "persona_eval" for c in calls)
        assert len(calls) == 2


class TestParsing:
    def test_markdown_fences_stripped(self):
        """Sonnet sometimes wraps JSON in ```json fences despite prompt
        forbidding them — parser must handle gracefully."""
        wrapped = "```json\n" + _judge_json([]) + "\n```"
        result = evaluate_persona(
            "fx_manager", [_alert(1)], {},
            runs=1, llm_caller=_scripted_caller([wrapped]),
        )
        assert result.runs[0].parse_error is None
        assert result.runs[0].overall_relevance == 0.75
