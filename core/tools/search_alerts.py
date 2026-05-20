"""
`search_alerts` — semantic search over Guardian alerts.

Same pattern as search_news. Surfaces country / category / score in each
rendered hit because alerts are the place agents reason about
"what's hot right now" — those fields drive the model's relevance
judgment.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .base import Tool, ToolResult

_MAX_DOC_CHARS = 250
_DEFAULT_TOP_K = 6
_MAX_TOP_K = 20


class SearchAlerts(Tool):
    name = "search_alerts"
    description = (
        "Semantic search over Guardian alerts. Returns up to top_k alerts most "
        "relevant to `query`. Use this when the user asks about active risk "
        "signals, ongoing events, or 'what should I be watching'. Each hit "
        "carries country / category / score metadata for the agent to reason on."
    )
    input_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Free-text search query."},
            "top_k": {
                "type": "integer",
                "description": f"Max results, 1-{_MAX_TOP_K}. Default {_DEFAULT_TOP_K}.",
            },
            "country": {
                "type": "string",
                "description": "Optional country filter (e.g. 'China', 'EU', 'Japan').",
            },
            "min_score": {
                "type": "number",
                "description": "Optional floor on Guardian alert score (0-100).",
            },
        },
        "required": ["query"],
    }

    def __init__(self, request_id: Optional[str] = None) -> None:
        self.request_id = request_id

    def execute(self, args: Dict[str, Any]) -> ToolResult:
        query = (args.get("query") or "").strip()
        if not query:
            return ToolResult.failure("query is required")
        raw_top_k = args.get("top_k")
        if raw_top_k in (None, ""):
            top_k = _DEFAULT_TOP_K
        else:
            try:
                top_k = int(raw_top_k)
            except (TypeError, ValueError):
                return ToolResult.failure(
                    f"top_k must be an integer between 1 and {_MAX_TOP_K}, got {raw_top_k!r}"
                )
        top_k = max(1, min(_MAX_TOP_K, top_k))
        country = (args.get("country") or "").strip() or None
        min_score = args.get("min_score")

        where: Dict[str, Any] = {}
        if country:
            where["country"] = {"$eq": country}
        if min_score is not None:
            try:
                where["score"] = {"$gte": float(min_score)}
            except (TypeError, ValueError):
                return ToolResult.failure(f"min_score must be a number, got {min_score!r}")

        try:
            from core.vector_store import get_vector_store
            store = get_vector_store()
            hits = store.search_alerts(
                query, n_results=top_k, where=where or None,
                request_id=self.request_id, endpoint="agent.intelligence.search_alerts",
            )
        except Exception as e:
            return ToolResult.failure(f"vector_store error: {e}")

        if not hits:
            details = []
            if country:           details.append(f"country={country}")
            # `is not None` (not truthy) so an explicit min_score=0
            # still surfaces in the explanation — a 0 floor is a valid
            # request, not the same as "no filter set".
            if min_score is not None: details.append(f"score>={min_score}")
            return ToolResult.success(
                content=f"No alerts matched '{query}'"
                + (f" ({', '.join(details)})" if details else "")
                + ".",
                structured={"hits": []},
            )

        lines: List[str] = []
        for i, h in enumerate(hits, 1):
            m = h.metadata or {}
            doc = (h.document or "")[:_MAX_DOC_CHARS].replace("\n", " ")
            tag_bits = [
                str(m[k]) for k in ("country", "category_label", "score")
                if m.get(k) is not None
            ]
            tag = f" ({' | '.join(tag_bits)})" if tag_bits else ""
            lines.append(f"[A{i}] {doc}{tag}")

        return ToolResult.success(
            content="\n".join(lines),
            structured={
                "hits": [
                    {
                        "id": h.id,
                        "title": (h.document or "").split("\n", 1)[0][:200],
                        "country": (h.metadata or {}).get("country"),
                        "category": (h.metadata or {}).get("category_label"),
                        "score": (h.metadata or {}).get("score"),
                        "distance": h.distance,
                    }
                    for h in hits
                ],
            },
        )
