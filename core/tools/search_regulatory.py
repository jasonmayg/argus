"""
`search_regulatory` — semantic search over regulatory_changes.

This is the largest collection (~300 docs) and the one Phase 2's
chat/brief retrieval-scope fix unlocked for general use. Agents query
here when the user asks about rule changes, sanctions, central-bank
decisions — the ECB / BoE / BoJ / OFAC items that live in this
collection rather than in `news` (which is Fed-only).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .base import Tool, ToolResult

_MAX_DOC_CHARS = 300
_DEFAULT_TOP_K = 6
_MAX_TOP_K = 20


class SearchRegulatory(Tool):
    name = "search_regulatory"
    description = (
        "Semantic search over the regulatory_changes collection — central-bank "
        "decisions, sanctions actions, rule changes from ECB / BoE / BoJ / FCA "
        "/ BoC / OFAC / US Treasury. Returns up to top_k items most relevant to "
        "`query`. Use this for any 'what did <regulator> do' question and for "
        "compliance / sanctions queries; the news collection only covers Fed."
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
                "description": "Optional jurisdiction filter (e.g. 'EU', 'UK', 'Japan', 'USA').",
            },
            "category": {
                "type": "string",
                "description": (
                    "Optional category filter. Common values: 'Monetary Policy', "
                    "'Sanctions', 'Banking Regulation', 'Sovereign debt operations'."
                ),
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
        top_k = max(1, min(_MAX_TOP_K, int(args.get("top_k") or _DEFAULT_TOP_K)))
        country = (args.get("country") or "").strip() or None
        category = (args.get("category") or "").strip() or None

        where: Dict[str, Any] = {}
        if country:
            where["country"] = {"$eq": country}
        if category:
            where["category"] = {"$eq": category}

        try:
            from core.vector_store import get_vector_store
            store = get_vector_store()
            hits = store.search_regulatory(
                query, n_results=top_k, where=where or None,
                request_id=self.request_id,
                endpoint="agent.intelligence.search_regulatory",
            )
        except Exception as e:
            return ToolResult.failure(f"vector_store error: {e}")

        if not hits:
            details = []
            if country:  details.append(f"country={country}")
            if category: details.append(f"category={category}")
            return ToolResult.success(
                content=f"No regulatory items matched '{query}'"
                + (f" ({', '.join(details)})" if details else "")
                + ".",
                structured={"hits": []},
            )

        lines: List[str] = []
        for i, h in enumerate(hits, 1):
            m = h.metadata or {}
            doc = (h.document or "")[:_MAX_DOC_CHARS].replace("\n", " ")
            tag_bits = [
                str(m[k]) for k in ("country", "category", "severity", "effective_date")
                if m.get(k)
            ]
            tag = f" ({' | '.join(tag_bits)})" if tag_bits else ""
            lines.append(f"[R{i}] {doc}{tag}")

        return ToolResult.success(
            content="\n".join(lines),
            structured={
                "hits": [
                    {
                        "id": h.id,
                        "title": (h.document or "").split("\n", 1)[0][:200],
                        "country": (h.metadata or {}).get("country"),
                        "category": (h.metadata or {}).get("category"),
                        "severity": (h.metadata or {}).get("severity"),
                        "effective_date": (h.metadata or {}).get("effective_date"),
                        "distance": h.distance,
                    }
                    for h in hits
                ],
            },
        )
