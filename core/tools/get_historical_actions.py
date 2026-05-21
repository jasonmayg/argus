"""
`get_historical_actions` — semantic search over past action cards.

Phase 3D ActionsAgent uses this to anchor new recommendations in
what Argus has actually recommended before for similar signals. The
agent calls this BEFORE proposing a new action so it can either:
  * Reuse the recommendation language that worked last time, or
  * Explicitly note "we changed approach because X" when diverging.

Data source: the `actions_history` chroma collection that Phase 2E's
`chroma_writer.dual_write_actions` populates on every action
generation run. Each row carries:
  - document: the action card text (recommendation + rationale)
  - metadata: persona, country, category, score, generated_at

Thin wrapper around `vector_store.search_actions_history` — same
shape as `search_news` / `search_alerts` / `search_regulatory` so
the runner's tool-dispatch contract stays uniform.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .base import Tool, ToolResult

# Trim each rendered hit so a single tool call can't swallow the
# context budget. 300 chars covers the recommendation + first
# sentence of rationale — enough for the agent to judge relevance
# without bloating prompts.
_MAX_DOC_CHARS = 300
_DEFAULT_TOP_K = 5
_MAX_TOP_K = 15


class GetHistoricalActions(Tool):
    name = "get_historical_actions"
    description = (
        "Semantic search over previously-generated action cards. Returns "
        "up to top_k past actions most relevant to `query` — use to "
        "check whether Argus has recommended something similar before, "
        "and what the wording / scope looked like. Pair with country / "
        "category filters when the question is grounded in a specific "
        "signal ('what did we recommend for Mexico FX last month?')."
    )
    input_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {"type": "string",
                       "description": "Free-text describing the signal or topic."},
            "top_k": {
                "type": "integer",
                "description": f"Max results, 1-{_MAX_TOP_K}. Default {_DEFAULT_TOP_K}.",
            },
            "country": {
                "type": "string",
                "description": "Optional country filter (case-sensitive label).",
            },
            "category": {
                "type": "string",
                "description": (
                    "Optional risk category filter. Common values: "
                    "'Market Risk', 'Liquidity Risk', 'Regulatory Risk', "
                    "'Political Risk', 'Operational Risk'."
                ),
            },
        },
        "required": ["query"],
    }

    def __init__(self, request_id: Optional[str] = None) -> None:
        """`request_id` is set by ActionsAgent before each run so this
        tool's rag_queries telemetry row joins the agent's
        agent_executions row in /api/admin/agents/<name>/recent."""
        self.request_id = request_id

    def execute(self, args: Dict[str, Any]) -> ToolResult:
        query = (args.get("query") or "").strip()
        if not query:
            return ToolResult.failure("query is required")

        # Same numeric-input validation pattern as the Phase 3B search_*
        # tools — non-integer top_k must return a clear failure rather
        # than letting int() bubble a generic ValueError up to the runner.
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

        country  = (args.get("country") or "").strip() or None
        category = (args.get("category") or "").strip() or None

        where: Dict[str, Any] = {}
        if country:  where["country"]  = {"$eq": country}
        if category: where["category"] = {"$eq": category}

        try:
            from core.vector_store import get_vector_store
            store = get_vector_store()
            hits = store.search_actions_history(
                query, n_results=top_k, where=where or None,
                request_id=self.request_id,
                endpoint="agent.actions.get_historical_actions",
            )
        except Exception as e:
            return ToolResult.failure(f"vector_store error: {e}")

        if not hits:
            details = []
            if country:  details.append(f"country={country}")
            if category: details.append(f"category={category}")
            return ToolResult.success(
                content=f"No historical actions matched '{query}'"
                + (f" ({', '.join(details)})" if details else "")
                + ".",
                structured={"hits": []},
            )

        lines: List[str] = []
        for i, h in enumerate(hits, 1):
            m   = h.metadata or {}
            doc = (h.document or "")[:_MAX_DOC_CHARS].replace("\n", " ")
            tag_bits = [
                str(m[k]) for k in ("country", "category", "score", "generated_at")
                if m.get(k) is not None
            ]
            tag = f" ({' | '.join(tag_bits)})" if tag_bits else ""
            lines.append(f"[H{i}] {doc}{tag}")

        return ToolResult.success(
            content="\n".join(lines),
            structured={
                "hits": [
                    {
                        "id":            h.id,
                        "title":         (h.document or "").split("\n", 1)[0][:200],
                        "country":       (h.metadata or {}).get("country"),
                        "category":      (h.metadata or {}).get("category"),
                        "score":         (h.metadata or {}).get("score"),
                        "generated_at":  (h.metadata or {}).get("generated_at"),
                        "distance":      h.distance,
                    }
                    for h in hits
                ],
            },
        )
