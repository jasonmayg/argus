"""
Telemetry sink for vector store queries.

Mirrors the design of core.llm_telemetry — same database file, similar
schema, log_query_safe() swallows exceptions so telemetry failures never
break the calling endpoint.

`request_id` is the join key that ties a RAG query back to the LLM call
that consumed its results, when both happen inside the same /api/* request.
"""

from __future__ import annotations

import datetime
import os
import sqlite3
import sys
from pathlib import Path
from typing import Optional

DB_PATH = Path(os.getenv("ARGUS_NEWS_DB", "data/fed_news/argus_news.db"))

_DDL = """
CREATE TABLE IF NOT EXISTS rag_queries (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp         TEXT    NOT NULL,
    endpoint          TEXT    NOT NULL,
    collection        TEXT    NOT NULL,
    query_text        TEXT,
    n_results         INTEGER,
    top_score         REAL,
    latency_ms        INTEGER,
    embedder_model    TEXT,
    embedder_version  TEXT,
    request_id        TEXT
);

CREATE INDEX IF NOT EXISTS ix_rag_queries_ts         ON rag_queries(timestamp);
CREATE INDEX IF NOT EXISTS ix_rag_queries_collection ON rag_queries(collection);
CREATE INDEX IF NOT EXISTS ix_rag_queries_request    ON rag_queries(request_id);
"""

_initialized = False


def _ensure_schema() -> None:
    global _initialized
    if _initialized:
        return
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript(_DDL)
        conn.commit()
    _initialized = True


def log_query(
    *,
    endpoint: str,
    collection: str,
    query_text: str,
    n_results: int,
    top_score: Optional[float],
    latency_ms: int,
    embedder_model: str,
    embedder_version: str,
    request_id: Optional[str] = None,
) -> None:
    _ensure_schema()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """INSERT INTO rag_queries
               (timestamp, endpoint, collection, query_text, n_results,
                top_score, latency_ms, embedder_model, embedder_version, request_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                datetime.datetime.now(datetime.timezone.utc).isoformat(),
                endpoint, collection,
                (query_text or "")[:500],
                n_results,
                top_score,
                latency_ms,
                embedder_model,
                embedder_version,
                request_id,
            ),
        )
        conn.commit()


def log_query_safe(**kwargs) -> None:
    """Same as log_query but swallows exceptions. Use in hot paths."""
    try:
        log_query(**kwargs)
    except Exception as e:
        print(f"[rag_telemetry] log failed: {e}", file=sys.stderr)
