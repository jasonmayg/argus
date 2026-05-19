"""
Phase 2.7 — `persona_eval_runs` SQLite table.

One row per persona_eval.py invocation, capturing the high-level
summary stats and pointers to the on-disk report files. Lets a future
trend dashboard answer "how has the cash_manager × treasury_ops jaccard
moved over time" without re-running the eval.

Schema matches Argus_Phase2_7_PersonaEval_Design.md §3.3.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

logger = logging.getLogger(__name__)

DB_PATH = Path(os.getenv("ARGUS_NEWS_DB", "data/fed_news/argus_news.db"))

_DDL = """
CREATE TABLE IF NOT EXISTS persona_eval_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at          TEXT NOT NULL,
    window_start    TEXT NOT NULL,
    window_end      TEXT NOT NULL,
    news_count      INTEGER,
    alerts_count    INTEGER,
    avg_jaccard     REAL,
    max_jaccard     REAL,
    llm_model       TEXT,
    llm_cost_usd    REAL,
    report_md_path  TEXT,
    report_json_path TEXT,
    notes           TEXT
);

CREATE INDEX IF NOT EXISTS ix_persona_eval_runs_at ON persona_eval_runs(run_at);
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


def reset_schema_cache() -> None:
    global _initialized
    _initialized = False


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    _ensure_schema()
    c = sqlite3.connect(DB_PATH)
    try:
        yield c
        c.commit()
    finally:
        c.close()


def log_run(
    *,
    run_at: Optional[datetime] = None,
    window_start: str,
    window_end: str,
    news_count: int = 0,
    alerts_count: int = 0,
    avg_jaccard: Optional[float] = None,
    max_jaccard: Optional[float] = None,
    llm_model: Optional[str] = None,
    llm_cost_usd: Optional[float] = None,
    report_md_path: Optional[str] = None,
    report_json_path: Optional[str] = None,
    notes: Optional[str] = None,
) -> int:
    run_at = run_at or datetime.now(timezone.utc)
    with _conn() as c:
        cur = c.execute(
            """
            INSERT INTO persona_eval_runs (
                run_at, window_start, window_end, news_count, alerts_count,
                avg_jaccard, max_jaccard, llm_model, llm_cost_usd,
                report_md_path, report_json_path, notes
            ) VALUES (?, ?, ?, ?, ?,  ?, ?, ?, ?,  ?, ?, ?)
            """,
            (
                run_at.isoformat(), window_start, window_end,
                news_count, alerts_count,
                avg_jaccard, max_jaccard, llm_model, llm_cost_usd,
                report_md_path, report_json_path, notes,
            ),
        )
        return int(cur.lastrowid)


def log_run_safe(**kwargs) -> Optional[int]:
    """Telemetry must never break the foreground evaluation — log a
    warning and return None if the write fails. Same contract as the
    other *_safe writers in core/."""
    try:
        return log_run(**kwargs)
    except Exception as exc:
        logger.warning("persona_eval_runs: write failed (%s)", exc)
        return None
