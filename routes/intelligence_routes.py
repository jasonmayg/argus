"""
Phase 3B — `/api/intelligence/query` endpoint.

Thin Flask wrapper around `core.agents.intelligence_agent.run_intelligence`.
The actual work lives in the agent layer; this route's job is to
validate input, surface the agent's IntelligenceResult as JSON, and
translate runner statuses into HTTP semantics:

  * status="ok"               → 200, full response
  * status="budget_exceeded"  → 200, partial response + flag
  * status="max_turns"        → 200, partial response + flag
  * status="error"            → 502 (upstream Anthropic error)
                                or 500 (anything else)
"""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

intelligence_bp = Blueprint("intelligence_routes", __name__)

# Floor / ceiling on query length to keep the input prompt bounded.
_MIN_QUERY_LEN = 3
_MAX_QUERY_LEN = 2000


@intelligence_bp.post("/api/intelligence/query")
def intelligence_query():
    # `request.get_json(silent=True)` parses + returns None on any
    # failure (wrong content-type, malformed JSON, missing body),
    # so we get a single 400 path for every bad-input shape. Touching
    # `request.json` directly raises BadRequest on malformed bodies
    # and bypasses our JSON error handler.
    body = request.get_json(silent=True)
    if body is None:
        return jsonify({"status": "error", "message": "JSON body required"}), 400
    if not isinstance(body, dict):
        return jsonify({"status": "error", "message": "JSON body must be an object"}), 400

    query = (body.get("query") or "").strip()
    if len(query) < _MIN_QUERY_LEN:
        return jsonify({
            "status":  "error",
            "message": f"query must be at least {_MIN_QUERY_LEN} chars",
        }), 400
    if len(query) > _MAX_QUERY_LEN:
        return jsonify({
            "status":  "error",
            "message": f"query exceeds {_MAX_QUERY_LEN} chars",
        }), 400

    try:
        # Imports kept inside the handler so a slim env without
        # anthropic / chromadb still boots the rest of the app —
        # the route just returns a 503 when it's needed.
        from core.agents.intelligence_agent import run_intelligence
    except Exception as exc:
        logger.exception("intelligence_agent import failed")
        return jsonify({
            "status":  "error",
            "message": f"intelligence agent unavailable ({type(exc).__name__})",
        }), 503

    try:
        result = run_intelligence(query, invoked_by="api")
    except Exception as exc:
        logger.exception("intelligence_agent run crashed")
        return jsonify({
            "status":  "error",
            "message": f"agent crashed: {type(exc).__name__}",
        }), 500

    payload = {
        "status":      result.status,
        "query":       result.query,
        "answer":      result.answer,
        "request_id":  result.request_id,
        "turns_used":  result.turns_used,
        "tokens_in":   result.tokens_in,
        "tokens_out":  result.tokens_out,
        "cost_usd":    round(result.cost_usd, 5),
        "tool_calls":  result.tool_calls,
        "message":     result.message,
    }

    # 200 for any soft outcome (ok / budget / max_turns) so the
    # frontend can render the partial answer; only hard errors
    # propagate as 5xx.
    if result.status == "ok":
        return jsonify(payload), 200
    if result.status in ("budget_exceeded", "max_turns"):
        return jsonify(payload), 200
    if result.status == "error":
        # Distinguish Anthropic transport errors (502 — bad gateway)
        # from internal logic problems (500). The runner stores the
        # error class in agent_executions but here we only have the
        # message string; do a best-effort heuristic.
        msg = (result.message or "").lower()
        is_upstream = any(s in msg for s in (
            "429", "rate limit", "503", "504", "overloaded",
            "connection", "timeout",
        ))
        return jsonify(payload), 502 if is_upstream else 500

    # Unknown status — surface as 500 with the raw payload for debug.
    return jsonify(payload), 500
