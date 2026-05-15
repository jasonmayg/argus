"""
routes/regulatory_routes.py

Regulatory Changes Tracker API:
  GET /api/regulatory           — List regulatory changes (with filters)
  GET /api/regulatory/summary   — Summary stats by country/category
  POST /api/regulatory/refresh  — Trigger re-classification
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

regulatory_bp = Blueprint("regulatory_routes", __name__)

DB_PATH = Path(os.getenv("ARGUS_NEWS_DB", "data/fed_news/argus_news.db"))

# Minimum confidence to show as "regulatory"
MIN_CONFIDENCE = 0.3


def _semantic_regulatory_search(
    query: str,
    top_k_override: Optional[int] = None,
) -> Tuple[List[Tuple[int, float]], Optional[str], Optional[float]]:
    """Run a Chroma semantic search over the regulatory collection.

    Returns ``(hits, request_id, top_distance)`` where ``hits`` is a list of
    ``(reg_id, distance)`` tuples ordered by similarity. On any failure or
    when the endpoint is disabled in config the result is ``([], None, None)``
    and the caller is expected to fall back to a SQL LIKE scan — the UI must
    keep working even if the vector store is unhealthy.

    ``top_k_override`` is used by the retry-on-narrow-filter heuristic in
    ``api_regulatory`` to ask for a bigger candidate pool when post-filtering
    has thinned the result below what the user requested. Bypasses the
    ``top_k`` config value but not the ``enabled`` kill-switch.
    """
    from core.rag_context import load_endpoint_config

    cfg = load_endpoint_config("regulatory_search")
    if not cfg.get("enabled", True):
        return [], None, None

    top_k = top_k_override if top_k_override is not None else int(cfg.get("top_k", 20))
    request_id = uuid.uuid4().hex[:16]
    try:
        from core.vector_store import get_vector_store
        store = get_vector_store()
        hits = store.search_regulatory(
            query,
            n_results=top_k,
            request_id=request_id,
            endpoint="api.regulatory.search",
        )
    except Exception as exc:
        logger.warning("regulatory semantic search failed (%s)", exc)
        return [], request_id, None

    pairs: List[Tuple[int, float]] = []
    for h in hits:
        # Chroma doc ids are formatted "reg_{int_id}" by VectorStore.add_regulatory
        if not h.id.startswith("reg_"):
            continue
        try:
            reg_id_int = int(h.id.split("_", 1)[1])
        except (IndexError, ValueError):
            continue
        pairs.append((reg_id_int, float(h.distance)))

    top = pairs[0][1] if pairs else None
    return pairs, request_id, top


def _filter_sem_hits(
    conn: sqlite3.Connection,
    sem_hits: List[Tuple[int, float]],
    *,
    show_all: bool,
    country: str,
    category: str,
    severity: str,
    source: str,
    limit: int,
) -> List[Dict[str, Any]]:
    """Hydrate ``sem_hits`` from SQLite and apply the metadata filters in
    Python while preserving the semantic order. Extracted so the retry
    branch in ``api_regulatory`` can reuse it without copy-paste.
    """
    if not sem_hits:
        return []
    id_list = [h[0] for h in sem_hits]
    placeholders = ",".join("?" * len(id_list))
    cur = conn.execute(
        _SELECT_REGULATORY_COLUMNS + f" WHERE id IN ({placeholders})",
        id_list,
    )
    rows_by_id = {row["id"]: row for row in cur.fetchall()}

    items: List[Dict[str, Any]] = []
    for reg_id, dist in sem_hits:
        row = rows_by_id.get(reg_id)
        if row is None:
            # Vector hit with no matching SQLite row — Chroma is stale
            # relative to the DB; skip rather than 500.
            continue
        if not show_all and float(row["confidence"] or 0) < MIN_CONFIDENCE:
            continue
        if country  and (row["country"]  or "").lower() != country.lower():  continue
        if category and (row["category"] or "").lower() != category.lower(): continue
        if severity and (row["severity"] or "").lower() != severity.lower(): continue
        if source   and row["source_type"] != source: continue

        items.append(_row_to_item(row, distance=dist))
        if len(items) >= limit:
            break
    return items


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


def _escape_like(s: str) -> str:
    """Escape SQLite LIKE metacharacters so user-supplied search text matches
    literally instead of as a wildcard pattern. A query like ``50% rate`` or
    ``a_b`` would otherwise silently mean "anything containing 50, then any
    chars, then space rate" — surprising for an end-user search box. Use with
    ``LIKE ? ESCAPE '\\'``."""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _semantic_search_disabled() -> bool:
    """True when ``endpoints.regulatory_search.enabled: false`` — used by the
    handler to distinguish a config-driven fallback (``"disabled"``) from one
    caused by an empty/errored Chroma result (``"sql_like"``)."""
    try:
        from core.rag_context import load_endpoint_config
        cfg = load_endpoint_config("regulatory_search")
        return cfg.get("enabled", True) is False
    except Exception:
        # If config can't be read, assume enabled — the helper itself will
        # surface a real error via the warning log.
        return False


# ═══════════════════════════════════════════════════════════════════════════
# GET /api/regulatory
# ═══════════════════════════════════════════════════════════════════════════

_SELECT_REGULATORY_COLUMNS = """
    SELECT id, source_type, title, url, published_at,
           country, category, severity,
           change_before, change_after, effective_date,
           treasury_impact, action_required,
           confidence, is_regulatory, raw_summary, classified_at
    FROM regulatory_changes
"""


def _row_to_item(row: sqlite3.Row, distance: Optional[float] = None) -> Dict[str, Any]:
    item = {
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
    }
    if distance is not None:
        item["distance"] = round(distance, 4)
    return item


@regulatory_bp.get("/api/regulatory")
def api_regulatory():
    """
    Returns regulatory changes.
    Query params:
      ?q=text                 free-text semantic search (Phase 2F)
      ?country=China          filter by country
      ?category=Capital+Controls  filter by category
      ?severity=High          filter by severity
      ?source=fed_news        filter by source_type
      ?all=1                  include non-regulatory items too
      ?limit=50               max results (default 50)
    """
    # ── Parse query params first (cheap, no DB needed). A bad `limit`
    # should produce a 400, not a 500, and parsing before opening the
    # connection means we don't have to remember to close it on this
    # error path.
    show_all = request.args.get("all") == "1"
    country  = (request.args.get("country")  or "").strip()
    category = (request.args.get("category") or "").strip()
    severity = (request.args.get("severity") or "").strip()
    source   = (request.args.get("source")   or "").strip()
    query    = (request.args.get("q")        or "").strip()
    limit_raw = request.args.get("limit", 50)
    try:
        limit = min(int(limit_raw), 200)
        if limit < 1:
            raise ValueError("limit must be >= 1")
    except (TypeError, ValueError):
        return jsonify({
            "status":  "error",
            "message": f"Invalid 'limit' value {limit_raw!r}; expected a positive integer.",
        }), 400

    # ── Run semantic search BEFORE opening the SQLite conn — keeps the
    # try/finally cleanup window as small as possible and means an
    # exception in the helper can't leak a connection.
    sem_hits: List[Tuple[int, float]] = []
    request_id: Optional[str] = None
    top_distance: Optional[float] = None
    if query:
        sem_hits, request_id, top_distance = _semantic_regulatory_search(query)

    conn = _get_conn()
    if not conn:
        return jsonify({"status": "error", "message": "Database not found"}), 500

    try:
        if not _table_exists(conn):
            return jsonify({
                "status":  "success",
                "items":   [],
                "total":   0,
                "message": "Run Daily Jobs to classify regulatory changes",
            })

        rag_info: Dict[str, Any] = {"used": False}

        # ── Branch 1: semantic search returned hits ────────────────────
        if query and sem_hits:
            items = _filter_sem_hits(
                conn, sem_hits,
                show_all=show_all, country=country, category=category,
                severity=severity, source=source, limit=limit,
            )

            # Heuristic: if a metadata filter is active and post-filter
            # row count dropped below `limit * retry_threshold`, the
            # top-K shortlist was probably dominated by docs that don't
            # match the filter. Rerun Chroma with a much larger candidate
            # pool so narrow filters get a fair shot. Skipped when no
            # filter is active (the slice is already canonical) or when
            # retry_top_k <= top_k (operator opt-out by setting it to 0).
            filters_active = bool(country or category or severity or source)
            retried_top_k: Optional[int] = None
            if filters_active:
                from core.rag_context import load_endpoint_config
                cfg = load_endpoint_config("regulatory_search")
                threshold = float(cfg.get("retry_threshold", 0.5))
                retry_top_k = int(cfg.get("retry_top_k", 80))
                initial_top_k = int(cfg.get("top_k", 20))

                under_served = len(items) < max(1, int(limit * threshold))
                if under_served and retry_top_k > initial_top_k:
                    sem_hits_retry, _, _ = _semantic_regulatory_search(
                        query, top_k_override=retry_top_k,
                    )
                    # Only adopt the retry result if it actually gave us
                    # a wider candidate pool — same-size pool means
                    # Chroma had already returned everything and the
                    # retry was an embedding call wasted.
                    if len(sem_hits_retry) > len(sem_hits):
                        items = _filter_sem_hits(
                            conn, sem_hits_retry,
                            show_all=show_all, country=country, category=category,
                            severity=severity, source=source, limit=limit,
                        )
                        retried_top_k = retry_top_k

            rag_info = {
                "used":          True,
                "request_id":    request_id,
                "hits":          len(items),
                "top_distance":  round(top_distance, 4) if top_distance is not None else None,
            }
            if retried_top_k is not None:
                rag_info["retried_top_k"] = retried_top_k
            return jsonify({
                "status":         "success",
                "total":          len(items),
                "generated_at":   datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                "query":          query,
                "filters": {
                    "country":  country  or None,
                    "category": category or None,
                    "severity": severity or None,
                },
                "rag":            rag_info,
                "items":          items,
            })

        # Semantic path returned nothing (disabled, errored, or empty
        # collection). Fall through to the SQL LIKE path below so the
        # caller still gets keyword results.

        # ── Branch 2: SQL path (no q, or q with no semantic hits) ─────
        conditions: List[str] = []
        params: List[Any] = []

        if not show_all:
            conditions.append(f"confidence >= {MIN_CONFIDENCE}")
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
        if query:
            # ESCAPE '\' so user-supplied % / _ are matched literally.
            conditions.append(
                "(LOWER(title) LIKE LOWER(?) ESCAPE '\\' "
                "OR LOWER(raw_summary) LIKE LOWER(?) ESCAPE '\\')"
            )
            like = f"%{_escape_like(query)}%"
            params.extend([like, like])

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        cur = conn.execute(
            _SELECT_REGULATORY_COLUMNS + f"""
            {where}
            ORDER BY
                CASE severity WHEN 'High' THEN 1 WHEN 'Medium' THEN 2 ELSE 3 END,
                confidence DESC,
                published_at DESC
            LIMIT ?
        """,
            params + [limit],
        )

        items = [_row_to_item(row) for row in cur.fetchall()]

        # When the SQL path runs because of a query, surface *why* the
        # semantic ranker didn't apply so clients/operators can tell the
        # difference between "kill-switch is off" and "Chroma was healthy
        # but returned nothing usable".
        if query:
            if _semantic_search_disabled():
                rag_info = {"used": False, "fallback": "disabled"}
            else:
                rag_info = {
                    "used":       False,
                    "request_id": request_id,
                    "fallback":   "sql_like",
                }

        response: Dict[str, Any] = {
            "status":         "success",
            "total":          len(items),
            "generated_at":   datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "filters": {
                "country":  country  or None,
                "category": category or None,
                "severity": severity or None,
            },
            "items": items,
        }
        if query:
            response["query"] = query
            response["rag"]   = rag_info
        return jsonify(response)
    finally:
        conn.close()


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