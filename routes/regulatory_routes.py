"""
routes/regulatory_routes.py

Regulatory Changes Tracker API:
  GET /api/regulatory           — List regulatory changes (with filters)
  GET /api/regulatory/summary   — Summary stats by country/category
  POST /api/regulatory/refresh  — Trigger re-classification
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import Blueprint, jsonify, request

regulatory_bp = Blueprint("regulatory_routes", __name__)

DB_PATH = Path(os.getenv("ARGUS_NEWS_DB", "data/fed_news/argus_news.db"))

# Minimum confidence to show as "regulatory"
MIN_CONFIDENCE = 0.3


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

def _get_conn() -> Optional[sqlite3.Connection]:
    if not DB_PATH.exists():
        return None
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection) -> bool:
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='regulatory_changes'"
    )
    return cur.fetchone() is not None


# ═══════════════════════════════════════════════════════════════════════════
# GET /api/regulatory
# ═══════════════════════════════════════════════════════════════════════════

@regulatory_bp.get("/api/regulatory")
def api_regulatory():
    """
    Returns regulatory changes.
    Query params:
      ?country=China          filter by country
      ?category=Capital+Controls  filter by category
      ?severity=High          filter by severity
      ?source=fed_news        filter by source_type
      ?all=1                  include non-regulatory items too
      ?limit=50               max results (default 50)
    """
    conn = _get_conn()
    if not conn:
        return jsonify({"status": "error", "message": "Database not found"}), 500

    if not _table_exists(conn):
        conn.close()
        return jsonify({
            "status":  "success",
            "items":   [],
            "total":   0,
            "message": "Run Daily Jobs to classify regulatory changes",
        })

    # Build query
    conditions = []
    params: List[Any] = []

    show_all = request.args.get("all") == "1"
    if not show_all:
        conditions.append(f"confidence >= {MIN_CONFIDENCE}")

    country  = (request.args.get("country")  or "").strip()
    category = (request.args.get("category") or "").strip()
    severity = (request.args.get("severity") or "").strip()
    source   = (request.args.get("source")   or "").strip()

    if country:
        conditions.append("LOWER(country) = LOWER(?)")
        params.append(country)
    if category:
        conditions.append("LOWER(category) = LOWER(?)")
        params.append(category)
    if severity:
        conditions.append("LOWER(severity) = LOWER(?)")
        params.append(severity)
    if source:
        conditions.append("source_type = ?")
        params.append(source)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    limit = min(int(request.args.get("limit", 50)), 200)

    cur = conn.cursor()
    cur.execute(f"""
        SELECT id, source_type, title, url, published_at,
               country, category, severity,
               change_before, change_after, effective_date,
               treasury_impact, action_required,
               confidence, is_regulatory, raw_summary, classified_at
        FROM regulatory_changes
        {where}
        ORDER BY
            CASE severity WHEN 'High' THEN 1 WHEN 'Medium' THEN 2 ELSE 3 END,
            confidence DESC,
            published_at DESC
        LIMIT ?
    """, params + [limit])

    items = []
    for row in cur.fetchall():
        items.append({
            "id":             row["id"],
            "source_type":    row["source_type"],
            "title":          row["title"],
            "url":            row["url"] or "",
            "published_at":   row["published_at"] or "",
            "country":        row["country"] or "Global",
            "category":       row["category"] or "General Regulatory",
            "severity":       row["severity"] or "Low",
            "change_before":  row["change_before"] or "",
            "change_after":   row["change_after"] or "",
            "effective_date": row["effective_date"] or "",
            "treasury_impact":row["treasury_impact"] or "",
            "action_required":row["action_required"] or "",
            "confidence":     round(float(row["confidence"] or 0), 2),
            "is_regulatory":  bool(row["is_regulatory"]),
            "summary":        row["raw_summary"] or "",
            "classified_at":  row["classified_at"] or "",
        })

    conn.close()

    return jsonify({
        "status":         "success",
        "total":          len(items),
        "generated_at":   datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "filters": {
            "country":  country or None,
            "category": category or None,
            "severity": severity or None,
        },
        "items": items,
    })


# ═══════════════════════════════════════════════════════════════════════════
# GET /api/regulatory/summary
# ═══════════════════════════════════════════════════════════════════════════

@regulatory_bp.get("/api/regulatory/summary")
def api_regulatory_summary():
    """Returns counts by country and category for filter chips."""
    conn = _get_conn()
    if not conn:
        return jsonify({"status": "error", "message": "DB not found"}), 500

    if not _table_exists(conn):
        conn.close()
        return jsonify({
            "status":     "success",
            "countries":  [],
            "categories": [],
            "severities": [],
            "total":      0,
        })

    cur = conn.cursor()

    cur.execute(f"""
        SELECT country, COUNT(*) as count FROM regulatory_changes
        WHERE is_regulatory = 1 AND confidence >= {MIN_CONFIDENCE}
        GROUP BY country ORDER BY count DESC
    """)
    countries = [{"country": r[0], "count": r[1]} for r in cur.fetchall()]

    cur.execute(f"""
        SELECT category, COUNT(*) as count FROM regulatory_changes
        WHERE is_regulatory = 1 AND confidence >= {MIN_CONFIDENCE}
        GROUP BY category ORDER BY count DESC
    """)
    categories = [{"category": r[0], "count": r[1]} for r in cur.fetchall()]

    cur.execute(f"""
        SELECT severity, COUNT(*) as count FROM regulatory_changes
        WHERE is_regulatory = 1 AND confidence >= {MIN_CONFIDENCE}
        GROUP BY severity ORDER BY
            CASE severity WHEN 'High' THEN 1 WHEN 'Medium' THEN 2 ELSE 3 END
    """)
    severities = [{"severity": r[0], "count": r[1]} for r in cur.fetchall()]

    cur.execute(f"""
        SELECT COUNT(*) FROM regulatory_changes
        WHERE is_regulatory = 1 AND confidence >= {MIN_CONFIDENCE}
    """)
    total = cur.fetchone()[0]

    conn.close()

    return jsonify({
        "status":     "success",
        "total":      total,
        "countries":  countries,
        "categories": categories,
        "severities": severities,
    })


# ═══════════════════════════════════════════════════════════════════════════
# POST /api/regulatory/refresh
# ═══════════════════════════════════════════════════════════════════════════

@regulatory_bp.post("/api/regulatory/refresh")
def api_regulatory_refresh():
    """Trigger re-classification of regulatory changes."""
    force = request.json.get("force", False) if request.is_json else False
    try:
        from classify_regulatory import classify_regulatory
        result = asyncio.run(classify_regulatory(force=force, limit=100))
        return jsonify({"status": "success", "result": result})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500