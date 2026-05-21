"""
Phase 3E — `/api/admin/*` endpoints for agent telemetry visibility.

Phase 3A wired `agent_executions`, Phase 3B + 3C wrote to it, but
there's been no first-class way to *see* what those agents have been
doing without opening sqlite directly. This blueprint exposes
aggregated stats + recent-runs detail so:

  * Operators can answer "is the IntelligenceAgent / ResponseAgent
    actually being called in prod? Are they succeeding?"
  * Cost discipline: per-agent USD totals, p95 turns, top error classes.
  * Phase 3D (ActionsAgent) verification benefits from having a probe
    here BEFORE we ship the agent — same shape, same dashboard.

Data sources (all in `data/fed_news/argus_news.db`):
  - `agent_executions` — Phase 3A's run-level log
  - `llm_calls`        — per-turn LLM call records, joins by request_id
  - `rag_queries`      — per-tool RAG retrieval, joins by request_id

The endpoint is intentionally unauthenticated for now — matches the
existing pattern (`/api/health/rag`, `/api/rag/stats`). Public deploys
should put this behind a reverse-proxy auth or restrict its CIDR.
Documented in §13 of the Phase 3 design doc as a follow-up.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

admin_bp = Blueprint("admin_routes", __name__)

# Mirror agent_telemetry / llm_telemetry / rag_telemetry's DB_PATH
# resolution. Hard-coding the env var name + default keeps this
# blueprint independent of those modules' import chains (so a broken
# import in one of them doesn't take down `/api/admin/agents`).
_DB_PATH = Path(os.getenv("ARGUS_NEWS_DB", "data/fed_news/argus_news.db"))

# Cap query windows / row counts so a noisy caller can't OOM the
# process. Limits chosen to fit Argus's volume (a few hundred agent
# runs / day at most) with comfortable headroom.
_MAX_WINDOW_HOURS = 7 * 24      # 7-day max aggregation window
_DEFAULT_WINDOW_HOURS = 24      # 24-h default
_MAX_RECENT = 200
_DEFAULT_RECENT = 25


def _connect() -> Optional[sqlite3.Connection]:
    """Open a read-only-ish connection to the telemetry sqlite db.
    Returns None when the file doesn't exist yet — that's the
    pre-first-agent-run state, which downstream callers handle
    gracefully."""
    if not _DB_PATH.exists():
        return None
    try:
        # SQLite URI mode + ro=1 enforces read-only at the driver
        # level — defence in depth so an admin endpoint bug can't
        # accidentally wipe telemetry.
        uri = f"file:{_DB_PATH}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as e:
        logger.warning("admin: cannot open telemetry db: %s", e)
        return None


def _window_from_query(args) -> tuple[int, str]:
    """Read + clamp the `hours` query parameter. Returns the (hours,
    ISO cutoff) tuple. Cutoff is UTC ISO so it slots directly into
    SQL WHERE clauses against the `started_at` / `timestamp` columns
    (which are also UTC ISO)."""
    try:
        hours = int(args.get("hours", _DEFAULT_WINDOW_HOURS))
    except (TypeError, ValueError):
        hours = _DEFAULT_WINDOW_HOURS
    hours = max(1, min(_MAX_WINDOW_HOURS, hours))
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    return hours, cutoff


# ──────────────────────────────────────────────────────────────────
# GET /api/admin/agents — per-agent aggregated stats
# ──────────────────────────────────────────────────────────────────

@admin_bp.get("/api/admin/agents")
def agents_summary():
    """Per-agent aggregated stats over a configurable window.

    Query params:
      ?hours=<int>  — window size in hours (default 24, max 168)

    Response (`agents` keyed by agent_name):
      {
        "window_hours": 24,
        "since": "2026-05-20T...",
        "agents": {
          "intelligence": {
            "total_runs": 42,
            "success": 38,
            "errors": 4,
            "success_rate": 0.905,
            "status_counts": {"ok": 38, "max_turns": 2, "error": 2},
            "tokens_in_total": 12000,
            "tokens_out_total": 4500,
            "cost_usd_total": 0.42,
            "cost_usd_avg":   0.010,
            "turns_p50": 2, "turns_p95": 4, "turns_max": 7,
            "top_error_classes": [{"class": "TimeoutError", "count": 3}, ...]
          },
          "response": {...}
        }
      }
    """
    try:
        hours, cutoff = _window_from_query(request.args)
        conn = _connect()
        if conn is None:
            return jsonify({
                "window_hours": hours, "since": cutoff,
                "agents": {},
                "message": "telemetry db not present yet (no agent runs recorded)",
            })

        with conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT agent_name, status, turns_used, tokens_in, tokens_out,
                       cost_usd, error_class
                  FROM agent_executions
                 WHERE started_at >= ?
            """, (cutoff,))
            rows = cur.fetchall()

        # Group by agent_name in Python rather than SQL — clearer for
        # the small N (a handful of agents), and we need both
        # aggregates AND distinct breakdowns (status_counts +
        # top_error_classes) which is awkward in one SQL pass.
        by_agent: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            name = r["agent_name"]
            slot = by_agent.setdefault(name, {
                "total_runs":         0,
                "status_counts":      {},
                "tokens_in_total":    0,
                "tokens_out_total":   0,
                "cost_usd_total":     0.0,
                "_turns_samples":     [],
                "_error_counts":      {},
            })
            slot["total_runs"]       += 1
            slot["status_counts"][r["status"]] = slot["status_counts"].get(r["status"], 0) + 1
            slot["tokens_in_total"]  += int(r["tokens_in"]  or 0)
            slot["tokens_out_total"] += int(r["tokens_out"] or 0)
            slot["cost_usd_total"]   += float(r["cost_usd"] or 0)
            slot["_turns_samples"].append(int(r["turns_used"] or 0))
            ec = r["error_class"]
            if ec:
                slot["_error_counts"][ec] = slot["_error_counts"].get(ec, 0) + 1

        for name, slot in by_agent.items():
            success = slot["status_counts"].get("ok", 0)
            errors  = slot["total_runs"] - success
            slot["success"]      = success
            slot["errors"]       = errors
            slot["success_rate"] = round(success / slot["total_runs"], 3) if slot["total_runs"] else 0.0
            slot["cost_usd_avg"] = round(slot["cost_usd_total"] / slot["total_runs"], 5) if slot["total_runs"] else 0.0
            slot["cost_usd_total"] = round(slot["cost_usd_total"], 5)
            samples = sorted(slot.pop("_turns_samples"))
            slot["turns_p50"] = _percentile(samples, 0.50)
            slot["turns_p95"] = _percentile(samples, 0.95)
            slot["turns_max"] = samples[-1] if samples else 0
            ec = slot.pop("_error_counts")
            slot["top_error_classes"] = [
                {"class": k, "count": v}
                for k, v in sorted(ec.items(), key=lambda x: -x[1])[:5]
            ]

        return jsonify({
            "window_hours": hours,
            "since":        cutoff,
            "agents":       by_agent,
        })
    except Exception as exc:
        logger.exception("admin agents_summary failed")
        return jsonify({
            "status":  "error",
            "message": f"{type(exc).__name__}: {str(exc)[:200]}",
        }), 500


# ──────────────────────────────────────────────────────────────────
# GET /api/admin/agents/<name>/recent — last N executions
# ──────────────────────────────────────────────────────────────────

@admin_bp.get("/api/admin/agents/<agent_name>/recent")
def agent_recent_runs(agent_name: str):
    """Most recent runs for a specific agent, with linked llm_calls
    and rag_queries counts. Use for debugging a specific incident
    ("why was that run slow / expensive / failed?").

    Query params:
      ?limit=<int>  — number of runs to return (default 25, max 200)
    """
    try:
        try:
            limit = int(request.args.get("limit", _DEFAULT_RECENT))
        except (TypeError, ValueError):
            limit = _DEFAULT_RECENT
        limit = max(1, min(_MAX_RECENT, limit))

        conn = _connect()
        if conn is None:
            return jsonify({"agent": agent_name, "runs": [], "limit": limit,
                            "message": "telemetry db not present yet"})

        with conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT id, agent_name, invoked_by, request_id, started_at,
                       finished_at, status, turns_used, tokens_in, tokens_out,
                       cost_usd, tool_calls_json, error_class, error_message,
                       output_preview
                  FROM agent_executions
                 WHERE agent_name = ?
              ORDER BY started_at DESC
                 LIMIT ?
            """, (agent_name, limit))
            run_rows = cur.fetchall()

            request_ids = tuple(r["request_id"] for r in run_rows if r["request_id"])
            llm_counts:  Dict[str, int] = {}
            rag_counts:  Dict[str, int] = {}
            if request_ids:
                placeholders = ",".join("?" * len(request_ids))
                # Per-run llm + rag attempt counts. Cheaper than
                # individually re-querying each run.
                cur.execute(
                    f"SELECT request_id, COUNT(*) c FROM llm_calls "
                    f"WHERE request_id IN ({placeholders}) GROUP BY request_id",
                    request_ids,
                )
                llm_counts = {r["request_id"]: r["c"] for r in cur.fetchall()}
                cur.execute(
                    f"SELECT request_id, COUNT(*) c FROM rag_queries "
                    f"WHERE request_id IN ({placeholders}) GROUP BY request_id",
                    request_ids,
                )
                rag_counts = {r["request_id"]: r["c"] for r in cur.fetchall()}

        runs: List[Dict[str, Any]] = []
        for r in run_rows:
            rid = r["request_id"]
            runs.append({
                "id":             r["id"],
                "request_id":     rid,
                "invoked_by":     r["invoked_by"],
                "started_at":     r["started_at"],
                "finished_at":    r["finished_at"],
                "status":         r["status"],
                "turns_used":     r["turns_used"],
                "tokens_in":      r["tokens_in"],
                "tokens_out":     r["tokens_out"],
                "cost_usd":       round(float(r["cost_usd"] or 0), 5),
                "tool_calls_json": r["tool_calls_json"],
                "llm_calls_count": llm_counts.get(rid, 0),
                "rag_queries_count": rag_counts.get(rid, 0),
                "error_class":    r["error_class"],
                "error_message":  r["error_message"],
                "output_preview": r["output_preview"],
            })

        return jsonify({"agent": agent_name, "limit": limit, "runs": runs})

    except Exception as exc:
        logger.exception("admin agent_recent_runs failed")
        return jsonify({
            "status":  "error",
            "message": f"{type(exc).__name__}: {str(exc)[:200]}",
        }), 500


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────

def _percentile(sorted_samples: List[int], p: float) -> int:
    """Nearest-rank percentile for an already-sorted int list.
    Returns 0 for empty input — callers display this as 'no data'."""
    if not sorted_samples:
        return 0
    if p <= 0:
        return sorted_samples[0]
    if p >= 1:
        return sorted_samples[-1]
    # Nearest-rank: index = ceil(p * n) - 1, clamped to [0, n-1].
    n = len(sorted_samples)
    idx = max(0, min(n - 1, int(round(p * n - 0.5))))
    return sorted_samples[idx]
