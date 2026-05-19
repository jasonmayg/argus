"""
Phase 2.7 — Method B: LLM judge.

Asks Claude to evaluate whether a persona's top-N alerts truly reflect
that persona's job-to-be-done, and where weights might be off.

Two independent calls (different temperature seeds / temperature 0.3)
produce a consensus signal:

  * If both calls suggest the same adjustment direction → high confidence
  * If they disagree on direction → low confidence (manual review)
  * If only one call returns valid JSON → low confidence

Per the design doc §1.2 + §2.3, this is one half of a complementary
pair with Method A (cross-persona Jaccard) — the LLM judge has bias
of its own, so we never auto-apply suggestions.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from core.llm_router import call_llm
from generate_actions import PERSONA_CONTEXT

from .data_loader import ScoredAlert

logger = logging.getLogger(__name__)

# Map confidence labels the LLM emits to a coarse sortable score.
_CONFIDENCE_RANK = {"high": 3, "medium": 2, "low": 1}


@dataclass
class WeightAdjustment:
    category: str
    current: Optional[float]
    suggested: Optional[float]
    confidence: str = "low"        # high | medium | low
    rationale: str = ""

    @property
    def direction(self) -> str:
        if self.current is None or self.suggested is None:
            return "unknown"
        if abs(self.suggested - self.current) < 1e-9:
            return "hold"
        return "up" if self.suggested > self.current else "down"


@dataclass
class JudgeSingleRunResult:
    overall_relevance: Optional[float] = None       # 0.0 - 1.0
    high_relevance_count: Optional[int] = None      # 0..N
    off_target_alerts: List[Dict] = field(default_factory=list)
    category_signals: Dict[str, str] = field(default_factory=dict)
    weight_adjustments: List[WeightAdjustment] = field(default_factory=list)
    raw_json: Optional[Dict] = None
    parse_error: Optional[str] = None


@dataclass
class JudgeResult:
    persona: str
    runs: List[JudgeSingleRunResult] = field(default_factory=list)
    consensus_adjustments: List[WeightAdjustment] = field(default_factory=list)
    disagreement_adjustments: List[Dict] = field(default_factory=list)


# ──────────────────────────────────────────────────────────────────
# Prompting
# ──────────────────────────────────────────────────────────────────

_SYSTEM = """You are a senior treasury operations advisor evaluating
the recommendation system of a treasury risk platform. You will judge
whether a persona's top alerts reflect that persona's actual job-to-be-done.

You must FIRST describe what categories the persona truly cares about
based on their job description, THEN judge the supplied top-20 list
against your independent description. Do not let the supplied list
anchor your idea of what the persona cares about.

Return ONLY valid JSON, no prose, no markdown fences.

JSON schema:
{
  "persona_priorities": [string, ...],          // YOUR independent list of categories this persona prioritizes
  "overall_relevance": number,                   // 0.0-1.0, how well top-20 matches your priorities
  "high_relevance_count": integer,               // how many of the 20 alerts are clearly on-target
  "off_target_alerts": [
    {"alert_id": string, "reason": string}       // up to 5 examples; cite specific alert ids
  ],
  "category_signals": {                          // freeform observations per category
    "<category_key>": "<one-sentence observation>"
  },
  "weight_adjustments": [
    {
      "category":   string,                       // must be one of the persona's existing risk_weight keys
      "current":    number,                       // value passed in current_weights
      "suggested":  number,                       // your recommended weight
      "confidence": "high" | "medium" | "low",
      "rationale":  string                        // 1-2 sentences citing alert evidence
    }
  ]
}"""


def _format_alerts_block(alerts: List[ScoredAlert]) -> str:
    """Compact per-alert rendering for the prompt. Limited to top 20
    to keep token usage tight; each line ~150 chars max."""
    out: List[str] = []
    for i, s in enumerate(alerts[:20], 1):
        a = s.alert
        title = a.title[:120]
        out.append(
            f"[{i}] id={a.alert_id[:40]} | {a.country} | {a.category} | "
            f"base={a.score:.0f} eff={s.effective_score:.0f} | {title}"
        )
    return "\n".join(out)


def _build_prompt(
    persona_key: str,
    persona_context: str,
    weights: Dict[str, float],
    alerts: List[ScoredAlert],
) -> str:
    weights_block = "\n".join(f"  {k}: {v}" for k, v in
                              sorted(weights.items(), key=lambda kv: -kv[1]))
    alerts_block = _format_alerts_block(alerts)
    return f"""Persona: {persona_key}
Job description:
{persona_context}

Current weight configuration:
{weights_block}

Top-20 alerts produced for this persona by the current weights
(sorted by effective_score descending):
{alerts_block}

Return the JSON evaluation now."""


# ──────────────────────────────────────────────────────────────────
# Parsing
# ──────────────────────────────────────────────────────────────────

def _strip_fences(raw: str) -> str:
    """The router prompt forbids fences but we still defensively strip
    them — Sonnet occasionally adds them despite instructions."""
    return re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.MULTILINE).strip()


def _parse_single_run(raw: str, current_weights: Dict[str, float]) -> JudgeSingleRunResult:
    cleaned = _strip_fences(raw)
    try:
        obj = json.loads(cleaned)
    except json.JSONDecodeError as e:
        return JudgeSingleRunResult(parse_error=f"json decode: {e}")

    result = JudgeSingleRunResult(raw_json=obj)
    rel = obj.get("overall_relevance")
    if isinstance(rel, (int, float)):
        result.overall_relevance = float(rel)
    hrc = obj.get("high_relevance_count")
    if isinstance(hrc, int):
        result.high_relevance_count = hrc

    result.off_target_alerts = obj.get("off_target_alerts") or []
    result.category_signals = obj.get("category_signals") or {}

    for adj in obj.get("weight_adjustments") or []:
        cat = adj.get("category")
        if not cat:
            continue
        result.weight_adjustments.append(WeightAdjustment(
            category=cat,
            current=_safe_float(adj.get("current")) or current_weights.get(cat),
            suggested=_safe_float(adj.get("suggested")),
            confidence=str(adj.get("confidence", "low")).lower(),
            rationale=str(adj.get("rationale", "")),
        ))
    return result


def _safe_float(v) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


# ──────────────────────────────────────────────────────────────────
# Consensus
# ──────────────────────────────────────────────────────────────────

def _consensus(runs: List[JudgeSingleRunResult]) -> tuple[List[WeightAdjustment], List[Dict]]:
    """Combine two runs into a single adjustment list.

    Rules:
      * A category present in BOTH runs with the same direction →
        consensus. Confidence = min of the two runs' confidences.
        Suggested value = average.
      * A category in BOTH runs but disagreeing on direction →
        disagreement (do not auto-apply, flag for manual review).
      * A category in only ONE run → disagreement (low confidence by
        construction).
    """
    if not runs:
        return [], []

    by_cat: Dict[str, List[WeightAdjustment]] = {}
    for r in runs:
        for adj in r.weight_adjustments:
            by_cat.setdefault(adj.category, []).append(adj)

    consensus: List[WeightAdjustment] = []
    disagreement: List[Dict] = []

    for cat, adjs in by_cat.items():
        if len(adjs) < len(runs):
            # Category only proposed by some runs — not consensus.
            disagreement.append({
                "category": cat,
                "reason": f"only {len(adjs)}/{len(runs)} runs suggested adjustment",
                "proposals": [
                    {"suggested": a.suggested, "confidence": a.confidence,
                     "rationale": a.rationale}
                    for a in adjs
                ],
            })
            continue

        directions = {a.direction for a in adjs}
        if len(directions) > 1:
            disagreement.append({
                "category": cat,
                "reason": f"runs disagreed on direction: {sorted(directions)}",
                "proposals": [
                    {"suggested": a.suggested, "confidence": a.confidence,
                     "rationale": a.rationale}
                    for a in adjs
                ],
            })
            continue

        # All runs agree on direction. Average the suggestion and
        # take the weakest confidence as the consensus confidence.
        suggested_vals = [a.suggested for a in adjs if a.suggested is not None]
        avg_suggested = sum(suggested_vals) / len(suggested_vals) if suggested_vals else None
        weakest = min(adjs, key=lambda a: _CONFIDENCE_RANK.get(a.confidence, 0))
        consensus.append(WeightAdjustment(
            category=cat,
            current=adjs[0].current,
            suggested=round(avg_suggested, 2) if avg_suggested is not None else None,
            confidence=weakest.confidence,
            rationale="; ".join(a.rationale for a in adjs if a.rationale),
        ))

    consensus.sort(key=lambda a: -_CONFIDENCE_RANK.get(a.confidence, 0))
    return consensus, disagreement


# ──────────────────────────────────────────────────────────────────
# Public entry point
# ──────────────────────────────────────────────────────────────────

def evaluate_persona(
    persona_key: str,
    alerts: List[ScoredAlert],
    current_weights: Dict[str, float],
    *,
    runs: int = 2,
    temperature: float = 0.3,
    llm_caller=None,                            # injected for tests
) -> JudgeResult:
    """Call the LLM judge `runs` times and return a JudgeResult with
    both raw runs and a consensus view. `llm_caller` is the seam tests
    use to inject deterministic responses."""
    persona_context = PERSONA_CONTEXT.get(persona_key, "")
    prompt = _build_prompt(persona_key, persona_context, current_weights, alerts)

    caller = llm_caller or _default_llm_caller
    parsed_runs: List[JudgeSingleRunResult] = []
    for i in range(runs):
        try:
            raw = caller(prompt, temperature=temperature, run_index=i)
        except Exception as e:
            parsed_runs.append(JudgeSingleRunResult(parse_error=f"llm call: {e}"))
            continue
        parsed_runs.append(_parse_single_run(raw, current_weights))

    valid = [r for r in parsed_runs if r.parse_error is None]
    consensus, disagreement = _consensus(valid)
    return JudgeResult(
        persona=persona_key,
        runs=parsed_runs,
        consensus_adjustments=consensus,
        disagreement_adjustments=disagreement,
    )


def _default_llm_caller(prompt: str, *, temperature: float, run_index: int) -> str:
    """Wire to core.llm_router. Phase 0 router resolves which provider
    actually serves the task — config/llm_routing.yaml maps
    persona_eval → Claude Sonnet by default."""
    return call_llm(
        task="persona_eval",
        system=_SYSTEM,
        user=prompt,
        temperature=temperature,
        max_tokens=1500,
        response_format="json",
    )
