"""
`score_action_coherence` — quantitative consistency check for a
proposed action against the actions history.

Phase 3D ActionsAgent uses this AFTER drafting a recommendation
(but before finalizing) to ask "have we said something similar
before?" The agent decides what to do with the answer:
  * Score >= 0.7  → strong precedent, lean on the past wording.
  * 0.3 <= Score < 0.7 → some precedent, may need to acknowledge
    differences.
  * Score < 0.3  → novel territory, either justify the new
    approach or reconsider.

Implementation: pure-vector consistency check. We search the
`actions_history` collection for top-K similar past actions, then
compute a single coherence score from their semantic distances.
**No LLM call inside this tool** — the agent already decides what
to do with the score, and adding another LLM call here would
double the cost of every coherence check without adding signal the
calling agent can't compute itself.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .base import Tool, ToolResult

# How many past actions to consider when computing the coherence
# score. Larger windows smooth out single-outlier matches but cost
# more vector-distance computation; 10 is a stable midpoint.
_NEIGHBORS_K = 10

# Distance below which we count an item as "strongly similar". Chroma
# returns squared L2 on normalized vectors, so values < 0.6 are
# typically near-paraphrases of the query. Calibrated empirically;
# bump after observing miscalibration in production data.
_STRONG_MATCH_THRESHOLD = 0.6


class ScoreActionCoherence(Tool):
    name = "score_action_coherence"
    description = (
        "Score how consistent a proposed action recommendation is with "
        "past actions Argus has recommended. Returns a float 0-1 "
        "(higher = more precedent in the actions history) plus the "
        "top similar past actions so the agent can read the actual "
        "language used. Use AFTER drafting a recommendation, BEFORE "
        "writing it into the final output."
    )
    input_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "action_text": {
                "type": "string",
                "description": "The proposed action recommendation text.",
            },
            "country": {
                "type": "string",
                "description": "Optional country filter to scope the comparison.",
            },
            "category": {
                "type": "string",
                "description": (
                    "Optional risk category filter. Strongly recommended — "
                    "scoring an FX hedge against past credit recommendations "
                    "produces noise."
                ),
            },
        },
        "required": ["action_text"],
    }

    def __init__(self, request_id: Optional[str] = None) -> None:
        self.request_id = request_id

    def execute(self, args: Dict[str, Any]) -> ToolResult:
        action_text = (args.get("action_text") or "").strip()
        if not action_text:
            return ToolResult.failure("action_text is required")

        country  = (args.get("country") or "").strip() or None
        category = (args.get("category") or "").strip() or None

        where: Dict[str, Any] = {}
        if country:  where["country"]  = {"$eq": country}
        if category: where["category"] = {"$eq": category}

        try:
            from core.vector_store import get_vector_store
            store = get_vector_store()
            hits = store.search_actions_history(
                action_text, n_results=_NEIGHBORS_K, where=where or None,
                request_id=self.request_id,
                endpoint="agent.actions.score_action_coherence",
            )
        except Exception as e:
            return ToolResult.failure(f"vector_store error: {e}")

        if not hits:
            # No history at all → coherence is undefined. We surface 0.0
            # explicitly with a flag so the agent knows the score isn't
            # "this is novel" but rather "we have no precedent dataset".
            return ToolResult.success(
                content=(
                    f"No past actions to compare against (country={country!r}, "
                    f"category={category!r}). Treat coherence as undefined."
                ),
                structured={
                    "coherence_score": 0.0,
                    "interpretation":  "no_history",
                    "neighbors":       [],
                },
            )

        # Build the score:
        #   * `nearest_distance`  — Chroma distance of the top match
        #                            (lower = more similar)
        #   * `strong_match_ratio` — fraction of neighbors with distance
        #                            below _STRONG_MATCH_THRESHOLD
        # Combine into a single 0-1: weight the strong-match ratio more
        # since multiple similar precedents are a stronger signal than
        # one perfect-but-isolated match.
        distances = [h.distance for h in hits if h.distance is not None]
        if not distances:
            # Embedder didn't return distances — falls back to "we have
            # SOME history" without a quantitative score.
            return ToolResult.success(
                content=f"Found {len(hits)} past actions; coherence undefined "
                        f"(no distance data).",
                structured={
                    "coherence_score": 0.0,
                    "interpretation":  "no_distance_data",
                    "neighbors":       _shape_neighbors(hits),
                },
            )

        nearest    = min(distances)
        # Map nearest distance ∈ [0, 1.5+] to similarity ∈ [0, 1].
        # Linear inversion, clamped. 0.0 distance → 1.0 similarity.
        nearest_sim = max(0.0, min(1.0, 1.0 - nearest))

        strong_count = sum(1 for d in distances if d < _STRONG_MATCH_THRESHOLD)
        # Denominator is the K we ASKED for, not the number actually
        # returned. With a tiny corpus (e.g. 2 past actions, both
        # strong matches) `strong_count / len(distances) = 1.0` would
        # falsely inflate the score to "strong_precedent". Anchoring
        # on K means "2 strong matches out of 10 we'd want to see" is
        # honestly weighted as 0.2 — the corpus simply isn't deep
        # enough to claim strong precedent.
        strong_ratio = strong_count / _NEIGHBORS_K

        # Weighted blend: strong_ratio carries 60% weight (multiple
        # precedents > single near-match), nearest_sim 40% (sanity-anchor
        # in case strong_ratio is 0 but the very top hit is good).
        coherence = round(0.6 * strong_ratio + 0.4 * nearest_sim, 3)

        interpretation = (
            "strong_precedent"   if coherence >= 0.7 else
            "some_precedent"     if coherence >= 0.3 else
            "novel"
        )

        content_lines = [
            f"Coherence score: {coherence:.2f} ({interpretation})",
            f"  nearest match distance:  {nearest:.3f}",
            f"  strong matches (<{_STRONG_MATCH_THRESHOLD}): {strong_count}/{_NEIGHBORS_K} requested ({len(distances)} returned)",
            "Top similar past actions:",
        ]
        for i, h in enumerate(hits[:3], 1):
            doc = (h.document or "")[:200].replace("\n", " ")
            content_lines.append(f"  [{i}] {doc}")

        return ToolResult.success(
            content="\n".join(content_lines),
            structured={
                "coherence_score":  coherence,
                "interpretation":   interpretation,
                "nearest_distance": round(nearest, 4),
                "strong_match_count": strong_count,
                "neighbors_examined": len(distances),
                "neighbors":         _shape_neighbors(hits),
            },
        )


def _shape_neighbors(hits) -> list:
    """Return a stable subset of fields for the structured payload —
    same shape as `get_historical_actions` for cross-tool consistency."""
    out = []
    for h in hits[:5]:
        m = h.metadata or {}
        out.append({
            "id":            h.id,
            "title":         (h.document or "").split("\n", 1)[0][:200],
            "country":       m.get("country"),
            "category":      m.get("category"),
            "score":         m.get("score"),
            "generated_at":  m.get("generated_at"),
            "distance":      h.distance,
        })
    return out
