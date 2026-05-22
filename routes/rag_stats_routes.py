"""
routes/rag_stats_routes.py

RAG retrieval observability — surfaces the data that core.rag_telemetry
has been writing to the `rag_queries` table since Phase 2. The point of
this endpoint is to answer "is retrieval healthy?" without manually
running SQL: are queries arriving, are they finding hits, is latency
sane, where are the zero-hit blind spots.

Endpoints:
  GET /api/admin/rag/stats?hours=24   — JSON aggregations over the window
  GET /admin/rag                       — Standalone HTML dashboard (template)

Moved to `/api/admin/` 2026-05-21 to share the ops-only namespace
with `/api/admin/agents` and `/api/admin/health/rag` — keep
operator endpoints out of the user-facing API surface. No
backwards-compat shim; the only consumer was `templates/admin_rag.html`
which is updated in the same change.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

from flask import Blueprint, jsonify, redirect, request

rag_stats_bp = Blueprint("rag_stats_routes", __name__)

DB_PATH = Path(os.getenv("ARGUS_NEWS_DB", "data/fed_news/argus_news.db"))


def _percentile(sorted_values: List[float], pct: float) -> float:
    """Nearest-rank percentile on a pre-sorted list. Avoid pulling numpy
    for a single helper — this is exact enough for ops dashboards."""
    if not sorted_values:
        return 0.0
    k = max(0, min(len(sorted_values) - 1, int(round((pct / 100) * len(sorted_values) - 1))))
    return float(sorted_values[k])


def _safe_table_exists(conn: sqlite3.Connection, name: str) -> bool:
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,),
    )
    return cur.fetchone() is not None


def _rag_stats(conn: sqlite3.Connection, hours: int) -> Dict[str, Any]:
    """Compute aggregations over the trailing window.

    Returns a single JSON-serializable dict shaped for direct rendering
    on the admin page. Empty-but-valid response when no rows match —
    callers should still get a usable object, not a 500.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

    if not _safe_table_exists(conn, "rag_queries"):
        # rag_queries is created lazily on first telemetry write. Before
        # any RAG call has run, the table genuinely doesn't exist — the
        # honest answer is "no data yet."
        return _empty_stats(hours, reason="rag_queries table not yet created")

    total_row = conn.execute(
        "SELECT COUNT(*), COUNT(CASE WHEN n_results = 0 THEN 1 END) "
        "FROM rag_queries WHERE timestamp >= ?",
        (cutoff,),
    ).fetchone()
    total, zero_hits = (total_row[0] or 0), (total_row[1] or 0)

    if total == 0:
        return _empty_stats(hours, reason="no queries in window")

    # Latency percentiles — pull only the column we need and sort in
    # Python; SQLite has no native percentile and this dataset is small.
    latencies = [
        row[0] for row in conn.execute(
            "SELECT latency_ms FROM rag_queries "
            "WHERE timestamp >= ? AND latency_ms IS NOT NULL "
            "ORDER BY latency_ms",
            (cutoff,),
        )
    ]
    p50 = _percentile(latencies, 50)
    p95 = _percentile(latencies, 95)
    p99 = _percentile(latencies, 99)

    by_collection = [
        {
            "collection": row[0] or "(unknown)",
            "count":      row[1],
            "zero_hits":  row[2],
            "avg_hits":   round(row[3] or 0, 2),
            "avg_ms":     round(row[4] or 0, 1),
        }
        for row in conn.execute(
            "SELECT collection, COUNT(*), "
            "       COUNT(CASE WHEN n_results = 0 THEN 1 END), "
            "       AVG(n_results), AVG(latency_ms) "
            "FROM rag_queries WHERE timestamp >= ? "
            "GROUP BY collection ORDER BY COUNT(*) DESC",
            (cutoff,),
        )
    ]

    by_endpoint = [
        {
            "endpoint":  row[0] or "(unknown)",
            "count":     row[1],
            "zero_hits": row[2],
            "avg_ms":    round(row[3] or 0, 1),
        }
        for row in conn.execute(
            "SELECT endpoint, COUNT(*), "
            "       COUNT(CASE WHEN n_results = 0 THEN 1 END), "
            "       AVG(latency_ms) "
            "FROM rag_queries WHERE timestamp >= ? "
            "GROUP BY endpoint ORDER BY COUNT(*) DESC",
            (cutoff,),
        )
    ]

    slowest = [
        {
            "timestamp":  row[0],
            "endpoint":   row[1],
            "collection": row[2],
            "latency_ms": row[3],
            "query":      (row[4] or "")[:100],
        }
        for row in conn.execute(
            "SELECT timestamp, endpoint, collection, latency_ms, query_text "
            "FROM rag_queries WHERE timestamp >= ? AND latency_ms IS NOT NULL "
            "ORDER BY latency_ms DESC LIMIT 10",
            (cutoff,),
        )
    ]

    hit_rate_pct = round(100 * (total - zero_hits) / total, 1) if total else 0.0

    return {
        "window_hours":  hours,
        "generated_at":  datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total_queries": total,
        "zero_hits":     zero_hits,
        "hit_rate_pct":  hit_rate_pct,
        "latency_ms":    {"p50": p50, "p95": p95, "p99": p99},
        "by_collection": by_collection,
        "by_endpoint":   by_endpoint,
        "slowest":       slowest,
    }


def _empty_stats(hours: int, reason: str) -> Dict[str, Any]:
    """Fallback shape so /admin/rag never has to special-case "no data"."""
    return {
        "window_hours":  hours,
        "generated_at":  datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total_queries": 0,
        "zero_hits":     0,
        "hit_rate_pct":  0.0,
        "latency_ms":    {"p50": 0, "p95": 0, "p99": 0},
        "by_collection": [],
        "by_endpoint":   [],
        "slowest":       [],
        "message":       reason,
    }


@rag_stats_bp.get("/api/admin/rag/stats")
def api_rag_stats():
    """Return RAG telemetry aggregations over the trailing window."""
    try:
        hours = int(request.args.get("hours", 24))
        # Clamp to a sane band — a 0-hour window is meaningless, and an
        # unbounded one defeats SQLite by reading the full table.
        hours = max(1, min(hours, 24 * 30))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Invalid 'hours'"}), 400

    if not DB_PATH.exists():
        return jsonify({"status": "error", "message": "Database not found"}), 500

    conn = sqlite3.connect(str(DB_PATH))
    try:
        return jsonify({"status": "success", **_rag_stats(conn, hours)})
    finally:
        conn.close()


@rag_stats_bp.get("/admin/rag")
def admin_rag_page():
    """Backwards-compat redirect to the new multi-tab Admin SPA at
    `/admin#rag`. The standalone `admin_rag.html` template was folded
    into `templates/admin.html` as the first tab; keep this redirect
    for any bookmarks / old sidebar links pointing at /admin/rag."""
    return redirect("/admin#rag", code=302)
