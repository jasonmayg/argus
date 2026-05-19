"""
Phase 2.7 — Method A: cross-persona differentiation.

Answers "do the four personas actually see different alerts, or are
their weights so similar that they all rank the same items at the top?"

Output: Jaccard similarity matrix across persona top-N sets, plus a
per-pair list of shared / non-shared alert ids so the report can quote
specific overlapping items.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


# Jaccard thresholds — picked as a conservative starting point.
# After the first few production runs we should revisit these in
# docs/PERSONA_EVAL.md based on observed distributions.
JACCARD_HIGH = 0.80        # too similar — flag for review
JACCARD_LOW = 0.20         # suspiciously disjoint — flag


@dataclass
class PairResult:
    persona_a: str
    persona_b: str
    jaccard: float
    shared_ids: List[str] = field(default_factory=list)
    only_in_a: List[str] = field(default_factory=list)
    only_in_b: List[str] = field(default_factory=list)


@dataclass
class DifferentiationReport:
    matrix: Dict[str, Dict[str, float]]    # persona -> persona -> jaccard
    pairs: List[PairResult]                # only off-diagonal, sorted by jaccard desc
    avg_jaccard: float                     # avg of off-diagonal entries
    max_jaccard: float                     # worst pair


def _jaccard(a: set[str], b: set[str]) -> float:
    """Symmetric similarity. Two empty sets are considered identical
    (1.0) — this corner case shouldn't happen in production but we
    keep the function safe to call on edge data."""
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def jaccard_matrix(top_ids_by_persona: Dict[str, List[str]]) -> DifferentiationReport:
    """Compute the full pairwise matrix + summary stats.

    `top_ids_by_persona` should be the output of data_loader.top_n_ids
    for each persona, with the same N (typically 20)."""
    personas = list(top_ids_by_persona.keys())
    sets = {p: set(ids) for p, ids in top_ids_by_persona.items()}

    matrix: Dict[str, Dict[str, float]] = {p: {} for p in personas}
    pairs: List[PairResult] = []

    for i, pa in enumerate(personas):
        for j, pb in enumerate(personas):
            if i == j:
                matrix[pa][pb] = 1.0
                continue
            if pa in matrix and pb in matrix[pa]:
                # Already filled by the symmetric (pb, pa) iteration.
                continue
            j_val = _jaccard(sets[pa], sets[pb])
            matrix[pa][pb] = j_val
            matrix[pb][pa] = j_val
            if i < j:                              # collect each pair once
                shared = sorted(sets[pa] & sets[pb])
                pairs.append(PairResult(
                    persona_a=pa, persona_b=pb, jaccard=j_val,
                    shared_ids=shared,
                    only_in_a=sorted(sets[pa] - sets[pb]),
                    only_in_b=sorted(sets[pb] - sets[pa]),
                ))

    pairs.sort(key=lambda p: p.jaccard, reverse=True)
    off_diag = [p.jaccard for p in pairs]
    avg = sum(off_diag) / len(off_diag) if off_diag else 0.0
    mx = max(off_diag) if off_diag else 0.0

    return DifferentiationReport(matrix=matrix, pairs=pairs, avg_jaccard=avg, max_jaccard=mx)


def flag_outliers(report: DifferentiationReport) -> List[str]:
    """Return human-readable flags for pairs that breach the thresholds.
    The report renderer surfaces these prominently."""
    flags: List[str] = []
    for p in report.pairs:
        if p.jaccard >= JACCARD_HIGH:
            flags.append(
                f"⚠ {p.persona_a} × {p.persona_b}: jaccard={p.jaccard:.2f} "
                f"(>= {JACCARD_HIGH}) — weights too similar, "
                f"{len(p.shared_ids)}/{len(p.shared_ids) + len(p.only_in_a)} alerts overlap"
            )
        elif p.jaccard < JACCARD_LOW:
            flags.append(
                f"⚠ {p.persona_a} × {p.persona_b}: jaccard={p.jaccard:.2f} "
                f"(< {JACCARD_LOW}) — suspiciously disjoint, one persona may be over-extreme"
            )
    return flags
