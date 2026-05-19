"""
Unit tests for core.persona_eval.differentiation.

The Jaccard math is short; what matters is that:
  * Identical top sets → 1.0
  * Disjoint top sets → 0.0
  * Threshold flags fire correctly
  * Pairs are deduplicated (each pair counted once, off-diagonal only)

Run:
    pytest tests/test_persona_eval_diff.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.persona_eval.differentiation import (  # noqa: E402
    JACCARD_HIGH,
    JACCARD_LOW,
    flag_outliers,
    jaccard_matrix,
)


class TestJaccardMatrix:
    def test_identical_top_sets(self):
        r = jaccard_matrix({"a": ["x", "y", "z"], "b": ["x", "y", "z"]})
        assert r.matrix["a"]["b"] == 1.0
        assert r.avg_jaccard == 1.0
        assert r.max_jaccard == 1.0

    def test_disjoint_top_sets(self):
        r = jaccard_matrix({"a": ["x"], "b": ["y"]})
        assert r.matrix["a"]["b"] == 0.0
        assert r.avg_jaccard == 0.0

    def test_partial_overlap(self):
        # |A∩B| = 2, |A∪B| = 4 → 0.5
        r = jaccard_matrix({
            "a": ["x", "y", "z"],
            "b": ["x", "y", "q"],
        })
        assert r.matrix["a"]["b"] == 0.5
        assert r.matrix["b"]["a"] == 0.5    # symmetric
        assert r.matrix["a"]["a"] == 1.0    # diagonal

    def test_four_personas_off_diagonal_count(self):
        """C(4,2) = 6 pairs. The pairs list must contain exactly that
        many entries — duplicate (a,b) and (b,a) would inflate the
        averages and break the report."""
        r = jaccard_matrix({
            "p1": ["a", "b"],
            "p2": ["a", "c"],
            "p3": ["c", "d"],
            "p4": ["a", "d"],
        })
        assert len(r.pairs) == 6
        # Each pair represented exactly once
        seen = {(p.persona_a, p.persona_b) for p in r.pairs}
        assert len(seen) == 6

    def test_pairs_sorted_by_jaccard_desc(self):
        r = jaccard_matrix({
            "p1": ["a", "b", "c"],
            "p2": ["a", "b", "c"],     # identical to p1
            "p3": ["x", "y", "z"],     # disjoint from p1/p2
        })
        jaccs = [p.jaccard for p in r.pairs]
        assert jaccs == sorted(jaccs, reverse=True)

    def test_empty_persona_set(self):
        """An evaluation with zero top items for one persona shouldn't
        crash — jaccard with empty set is 0 (except both empty → 1)."""
        r = jaccard_matrix({"a": [], "b": ["x"]})
        assert r.matrix["a"]["b"] == 0.0


class TestFlagOutliers:
    def test_high_overlap_flagged(self):
        r = jaccard_matrix({
            "a": ["x", "y", "z", "q"],
            "b": ["x", "y", "z", "q"],
        })
        flags = flag_outliers(r)
        assert len(flags) == 1
        assert "too similar" in flags[0]

    def test_low_overlap_flagged(self):
        r = jaccard_matrix({
            "a": ["x", "y", "z", "p", "q", "r"],
            "b": ["m", "n", "o"],
        })
        flags = flag_outliers(r)
        assert any("disjoint" in f for f in flags)

    def test_healthy_middle_not_flagged(self):
        # Jaccard ≈ 0.5 — healthy zone
        r = jaccard_matrix({
            "a": ["x", "y", "z", "q"],
            "b": ["x", "y", "m", "n"],
        })
        assert flag_outliers(r) == []

    def test_threshold_constants_sane(self):
        """Sanity check on the thresholds themselves — they're the
        spec contract with the report renderer; tightening or loosening
        should be a deliberate decision, not a typo."""
        assert 0 < JACCARD_LOW < 0.5
        assert 0.5 < JACCARD_HIGH < 1.0
