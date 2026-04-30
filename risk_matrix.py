#!/usr/bin/env python3
"""
ARGUS 2D Risk Matrix
5x5 Probability × Impact matrix for Treasury risk assessment.

Probability axis (likelihood of occurrence in the next 90 days):
  1 = Unlikely   2 = Seldom   3 = Occasional   4 = Likely   5 = Frequent

Impact axis (severity for Treasury operations, liquidity, and portfolio):
  1 = Negligible   2 = Minor   3 = Moderate   4 = Hazardous   5 = Catastrophic
"""

from typing import Dict, List

# ── Axis label maps ──────────────────────────────────────────────────────────

PROBABILITY_LABELS: Dict[int, str] = {
    1: "Unlikely",
    2: "Seldom",
    3: "Occasional",
    4: "Likely",
    5: "Frequent",
}

IMPACT_LABELS: Dict[int, str] = {
    1: "Negligible",
    2: "Minor",
    3: "Moderate",
    4: "Hazardous",
    5: "Catastrophic",
}

# ── 5×5 risk matrix ──────────────────────────────────────────────────────────
# _MATRIX[probability - 1][impact - 1]  →  risk level string
# Rows: P = 1 (Unlikely) … 5 (Frequent)
# Cols: I = 1 (Negligible) … 5 (Catastrophic)

_MATRIX: List[List[str]] = [
    ["Low",    "Low",    "Low",    "Medium", "Medium"],  # P=1  Unlikely
    ["Low",    "Low",    "Medium", "Medium", "High"  ],  # P=2  Seldom
    ["Low",    "Medium", "Medium", "High",   "High"  ],  # P=3  Occasional
    ["Medium", "Medium", "High",   "High",   "High"  ],  # P=4  Likely
    ["Medium", "High",   "High",   "High",   "High"  ],  # P=5  Frequent
]

# ── Visual styling ────────────────────────────────────────────────────────────

RISK_LEVEL_COLORS: Dict[str, str] = {
    "Low":    "#22C55E",   # green
    "Medium": "#F59E0B",   # amber
    "High":   "#EF4444",   # red
}

RISK_LEVEL_TEXT_COLORS: Dict[str, str] = {
    "Low":    "#fff",
    "Medium": "#fff",
    "High":   "#fff",
}

# Ordinal rank for sorting (0 = lowest risk)
_LEVEL_ORDER: List[str] = ["Low", "Medium", "High"]


# ── Core function ─────────────────────────────────────────────────────────────

def calculate_risk_level(probability: int, impact: int) -> Dict:
    """
    Look up the 5×5 matrix and return the final risk level for a (P, I) pair.

    Args:
        probability: integer 1 (Unlikely) … 5 (Frequent)
        impact:      integer 1 (Negligible) … 5 (Catastrophic)

    Returns:
        dict with keys:
            level            – "Low" | "Medium" | "High"
            color            – hex background colour for the cell
            text_color       – hex foreground colour for the cell
            probability      – clamped int (1–5)
            probability_label – e.g. "Occasional"
            impact           – clamped int (1–5)
            impact_label     – e.g. "Hazardous"
            matrix_score     – ordinal 0–3 (useful for sorting / comparison)
    """
    p = max(1, min(5, int(probability)))
    i = max(1, min(5, int(impact)))
    level = _MATRIX[p - 1][i - 1]
    return {
        "level":             level,
        "color":             RISK_LEVEL_COLORS[level],
        "text_color":        RISK_LEVEL_TEXT_COLORS[level],
        "probability":       p,
        "probability_label": PROBABILITY_LABELS[p],
        "impact":            i,
        "impact_label":      IMPACT_LABELS[i],
        "matrix_score":      _LEVEL_ORDER.index(level),
    }


# ── Heuristic fallback helpers ────────────────────────────────────────────────

def score_to_probability(risk_score: float) -> int:
    """
    Map a keyword risk_score (0–100) to a probability index (1–5).
    Used when the Claude API is unavailable.
    """
    if risk_score >= 80:
        return 5
    if risk_score >= 60:
        return 4
    if risk_score >= 40:
        return 3
    if risk_score >= 20:
        return 2
    return 1


def score_to_impact(risk_score: float) -> int:
    """
    Map a keyword risk_score (0–100) to an impact index (1–5).
    Uses a slightly different breakpoint from probability to reflect that
    Treasury impact can be severe even for moderate-probability signals.
    """
    if risk_score >= 80:
        return 5
    if risk_score >= 60:
        return 4
    if risk_score >= 35:
        return 3
    if risk_score >= 15:
        return 2
    return 1
