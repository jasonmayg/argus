"""
`search_news` — semantic news search exposed to autonomous agents.

Thin wrapper around `core.vector_store.search_news`. The wrapper exists
(rather than handing the LLM the raw SearchHit list) so we can:
  * Bound the per-call result size & doc length (context budget)
  * Render each hit in a compact, model-friendly line
  * Pass `request_id` + `endpoint` through to RAG telemetry so each
    tool call shows up in the same /api/rag/stats dashboard the
    /api/chat and /api/brief endpoints already populate
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .base import Tool, ToolResult

# Trim each rendered hit to this many chars so a single tool call can't
# swallow the LLM's context budget. The agent can still see ~10 hits
# × ~250 chars = ~2.5k chars per call.
_MAX_DOC_CHARS = 250
_DEFAULT_TOP_K = 8
_MAX_TOP_K = 20


class SearchNews(Tool):
    name = "search_news"
    description = (
        "Semantic search over the news collection (Fed and other crawled news). "
        "Returns up to top_k items most relevant to `query`. Use this when the "
        "user asks about recent events, central-bank announcements, or "
        "macroeconomic news. Pair with a date filter (`since`) for time-bounded "
        "questions like 'what happened in the last week'."
    )
    input_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Free-text search query.",
            },
            "top_k": {
                "type": "integer",
                "description": f"Max results, 1-{_MAX_TOP_K}. Default {_DEFAULT_TOP_K}.",
            },
            "since": {
                "type": "string",
                "description": "Optional ISO date (YYYY-MM-DD). Only hits with `date >= since` are returned.",
            },
        },
        "required": ["query"],
    }

    def __init__(self, request_id: Optional[str] = None) -> None:
        """`request_id` is set by IntelligenceAgent before each run so
        every tool call shares the agent's request_id in rag_queries."""
        self.request_id = request_id

    def execute(self, args: Dict[str, Any]) -> ToolResult:
        query = (args.get("query") or "").strip()
        if not query:
            return ToolResult.failure("query is required")
        raw_top_k = args.get("top_k")
        if raw_top_k in (None, ""):
            top_k = _DEFAULT_TOP_K
        else:
            # Reject non-coercible values explicitly so the runner shows
            # the model a specific "must be an integer" message instead
            # of a generic "tool raised: ValueError" string.
            try:
                top_k = int(raw_top_k)
            except (TypeError, ValueError):
                return ToolResult.failure(
                    f"top_k must be an integer between 1 and {_MAX_TOP_K}, got {raw_top_k!r}"
                )
        top_k = max(1, min(_MAX_TOP_K, top_k))
        since = (args.get("since") or "").strip() or None

        where = {"date": {"$gte": since}} if since else None

        try:
            from core.vector_store import get_vector_store
            store = get_vector_store()
            hits = store.search_news(
                query, n_results=top_k, where=where,
                request_id=self.request_id, endpoint="agent.intelligence.search_news",
            )
        except Exception as e:
            return ToolResult.failure(f"vector_store error: {e}")

        if not hits:
            return ToolResult.success(
                content=f"No news matched '{query}'"
                + (f" since {since}" if since else "")
                + ".",
                structured={"hits": []},
            )

        lines: List[str] = []
        for i, h in enumerate(hits, 1):
            m = h.metadata or {}
            doc = (h.document or "")[:_MAX_DOC_CHARS].replace("\n", " ")
            date = m.get("date") or ""
            cat = m.get("risk_category") or ""
            tag_bits = [p for p in (date, cat) if p]
            tag = f" ({' | '.join(tag_bits)})" if tag_bits else ""
            lines.append(f"[N{i}] {doc}{tag}")

        return ToolResult.success(
            content="\n".join(lines),
            structured={
                "hits": [
                    {
                        "id": h.id,
                        "title": (h.document or "").split("\n", 1)[0][:200],
                        "date": (h.metadata or {}).get("date"),
                        "risk_category": (h.metadata or {}).get("risk_category"),
                        "distance": h.distance,
                    }
                    for h in hits
                ],
            },
        )
