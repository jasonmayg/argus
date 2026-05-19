"""
Phase 2.7 — report renderer.

Two surfaces:
  * Markdown   — human-facing, written to data/persona_eval/<date>.md
  * JSON       — machine-facing, written to data/persona_eval/<date>.json

Both consume the same EvalRun dataclass so they stay in sync.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .data_loader import ScoredAlert, WindowSummary
from .differentiation import DifferentiationReport, flag_outliers
from .llm_judge import JudgeResult, WeightAdjustment


@dataclass
class EvalRun:
    """Everything needed to render a report. Built up incrementally by
    scripts/persona_eval.py as each phase of the pipeline runs."""
    run_at: str                          # ISO timestamp
    window: WindowSummary
    top_n: int = 20
    scored_by_persona: Dict[str, List[ScoredAlert]] = field(default_factory=dict)
    differentiation: Optional[DifferentiationReport] = None
    judge_results: Dict[str, JudgeResult] = field(default_factory=dict)
    llm_model: Optional[str] = None
    llm_cost_usd: Optional[float] = None
    notes: Optional[str] = None


# ──────────────────────────────────────────────────────────────────
# Markdown
# ──────────────────────────────────────────────────────────────────

def _matrix_table(matrix: Dict[str, Dict[str, float]]) -> str:
    """Render the Jaccard matrix as a markdown table. Personas appear
    in the same order on both axes so the diagonal reads cleanly."""
    personas = sorted(matrix.keys())
    header = "| | " + " | ".join(personas) + " |"
    sep    = "|---|" + "|".join("---" for _ in personas) + "|"
    rows = [header, sep]
    for p in personas:
        cells = [f"{matrix[p].get(q, 0):.2f}" for q in personas]
        rows.append(f"| **{p}** | " + " | ".join(cells) + " |")
    return "\n".join(rows)


def _adjustments_table(adjs: List[WeightAdjustment]) -> str:
    if not adjs:
        return "_no consensus adjustments_\n"
    rows = [
        "| category | current | suggested | direction | confidence | rationale |",
        "|---|---|---|---|---|---|",
    ]
    for a in adjs:
        cur = "—" if a.current is None else f"{a.current:.2f}"
        sug = "—" if a.suggested is None else f"{a.suggested:.2f}"
        rationale = (a.rationale or "").replace("|", "\\|")[:200]
        rows.append(
            f"| {a.category} | {cur} | {sug} | {a.direction} | {a.confidence} | {rationale} |"
        )
    return "\n".join(rows)


def render_markdown(run: EvalRun) -> str:
    parts: List[str] = []
    parts.append(f"# Persona Weight Evaluation — {run.run_at[:10]}")
    parts.append(f"Run at: {run.run_at}")
    parts.append(
        f"Window: {run.window.window_start} → {run.window.window_end} "
        f"({(_parse_date(run.window.window_end) - _parse_date(run.window.window_start)).days} days)"
    )
    parts.append(
        f"Alerts kept: {run.window.kept_count} / {run.window.raw_count} raw "
        f"({_pct(run.window.kept_count, run.window.raw_count)}%)"
    )
    parts.append("")

    # ─── Summary ─────────────────────────────────────────────────
    parts.append("## Summary")
    personas = sorted(run.scored_by_persona.keys())
    parts.append(f"- Personas evaluated: {len(personas)} ({', '.join(personas)})")
    if run.differentiation:
        parts.append(f"- Off-diagonal Jaccard avg: **{run.differentiation.avg_jaccard:.2f}** "
                     f"(max: {run.differentiation.max_jaccard:.2f})")
        flags = flag_outliers(run.differentiation)
        if flags:
            parts.append(f"- ⚠ {len(flags)} differentiation flag(s) — see below")
        else:
            parts.append("- ✓ no differentiation flags")
    if run.judge_results:
        consensus_count = sum(len(r.consensus_adjustments) for r in run.judge_results.values())
        disagreement_count = sum(len(r.disagreement_adjustments) for r in run.judge_results.values())
        parts.append(f"- LLM judge: {consensus_count} consensus adjustment(s), "
                     f"{disagreement_count} disagreement(s)")
        if run.llm_cost_usd is not None:
            parts.append(f"- LLM cost: ${run.llm_cost_usd:.4f} (model: {run.llm_model or 'unknown'})")
    parts.append("")

    # ─── Method A ────────────────────────────────────────────────
    if run.differentiation:
        parts.append("## Method A — Cross-Persona Differentiation")
        parts.append("")
        parts.append(_matrix_table(run.differentiation.matrix))
        parts.append("")
        flags = flag_outliers(run.differentiation)
        if flags:
            parts.append("### Outlier pairs")
            for f in flags:
                parts.append(f"- {f}")
            parts.append("")
        # Top 3 worst pairs always shown for context, even when not flagged.
        if run.differentiation.pairs:
            parts.append("### Highest-overlap pairs (top 3)")
            for p in run.differentiation.pairs[:3]:
                parts.append(
                    f"- **{p.persona_a} × {p.persona_b}**: jaccard={p.jaccard:.2f}, "
                    f"shared={len(p.shared_ids)}, unique to A={len(p.only_in_a)}, "
                    f"unique to B={len(p.only_in_b)}"
                )
            parts.append("")

    # ─── Method B ────────────────────────────────────────────────
    if run.judge_results:
        parts.append("## Method B — LLM Judge (per persona)")
        for p in personas:
            jr = run.judge_results.get(p)
            if jr is None:
                continue
            parts.append(f"\n### {p}")
            # Use the first run that parsed for overall scores.
            valid = [r for r in jr.runs if r.parse_error is None]
            if valid:
                first = valid[0]
                if first.overall_relevance is not None:
                    parts.append(f"- Overall relevance (run 1): {first.overall_relevance:.2f}")
                if first.high_relevance_count is not None:
                    parts.append(f"- High-relevance count: {first.high_relevance_count}/{run.top_n}")
            failed = [i for i, r in enumerate(jr.runs) if r.parse_error]
            if failed:
                parts.append(f"- ⚠ {len(failed)}/{len(jr.runs)} runs failed to parse")

            parts.append("\n**Consensus adjustments**")
            parts.append(_adjustments_table(jr.consensus_adjustments))
            if jr.disagreement_adjustments:
                parts.append("\n**Disagreements (manual review)**")
                for d in jr.disagreement_adjustments:
                    parts.append(f"- `{d['category']}`: {d['reason']}")
                    for prop in d.get("proposals", []):
                        parts.append(
                            f"    - suggested={prop.get('suggested')} "
                            f"(confidence={prop.get('confidence')})"
                        )
            parts.append("")

    # ─── Recommendations ─────────────────────────────────────────
    parts.append("## Recommendations")
    auto_apply: List[str] = []
    review: List[str] = []
    for persona, jr in run.judge_results.items():
        for a in jr.consensus_adjustments:
            line = f"`{persona}` / `{a.category}`: {_fmt_v(a.current)} → {_fmt_v(a.suggested)}"
            if a.confidence == "high":
                auto_apply.append(line)
            else:
                review.append(f"{line} (confidence={a.confidence})")
        for d in jr.disagreement_adjustments:
            review.append(f"`{persona}` / `{d['category']}`: {d['reason']}")

    parts.append("\n**Auto-apply candidates** (high confidence; still gated on human PR review):")
    parts.extend(f"- {x}" for x in auto_apply) if auto_apply else parts.append("- _none_")
    parts.append("\n**Manual review queue**:")
    parts.extend(f"- {x}" for x in review) if review else parts.append("- _none_")
    parts.append("")

    return "\n".join(parts)


# ──────────────────────────────────────────────────────────────────
# JSON
# ──────────────────────────────────────────────────────────────────

def render_json(run: EvalRun) -> dict:
    """Full machine-readable dump. Future trend dashboards consume this."""
    def alert_brief(s: ScoredAlert) -> dict:
        return {
            "alert_id":        s.alert.alert_id,
            "title":           s.alert.title,
            "country":         s.alert.country,
            "category":        s.alert.category,
            "base_score":      s.alert.score,
            "effective_score": s.effective_score,
            "url":             s.alert.url,
        }

    diff_block = None
    if run.differentiation:
        diff_block = {
            "matrix":       run.differentiation.matrix,
            "avg_jaccard":  run.differentiation.avg_jaccard,
            "max_jaccard":  run.differentiation.max_jaccard,
            "pairs": [
                {
                    "persona_a":   p.persona_a,
                    "persona_b":   p.persona_b,
                    "jaccard":     p.jaccard,
                    "shared":      p.shared_ids,
                    "only_in_a":   p.only_in_a,
                    "only_in_b":   p.only_in_b,
                }
                for p in run.differentiation.pairs
            ],
            "flags": flag_outliers(run.differentiation),
        }

    return {
        "run_at": run.run_at,
        "window": asdict(run.window),
        "top_n": run.top_n,
        "scored_by_persona": {
            p: [alert_brief(s) for s in scored[: run.top_n]]
            for p, scored in run.scored_by_persona.items()
        },
        "differentiation": diff_block,
        "judge": {
            p: {
                "runs": [
                    {
                        "overall_relevance":    r.overall_relevance,
                        "high_relevance_count": r.high_relevance_count,
                        "off_target_alerts":    r.off_target_alerts,
                        "category_signals":     r.category_signals,
                        "weight_adjustments":   [asdict(a) for a in r.weight_adjustments],
                        "parse_error":          r.parse_error,
                    }
                    for r in jr.runs
                ],
                "consensus_adjustments":    [asdict(a) for a in jr.consensus_adjustments],
                "disagreement_adjustments": jr.disagreement_adjustments,
            }
            for p, jr in run.judge_results.items()
        },
        "llm_model":     run.llm_model,
        "llm_cost_usd":  run.llm_cost_usd,
        "notes":         run.notes,
    }


# ──────────────────────────────────────────────────────────────────
# Small helpers
# ──────────────────────────────────────────────────────────────────

def _parse_date(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def _pct(num: int, denom: int) -> str:
    if not denom:
        return "0"
    return f"{100 * num / denom:.0f}"


def _fmt_v(v: Optional[float]) -> str:
    return "—" if v is None else f"{v:.2f}"
