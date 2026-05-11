"""
Telemetry sink for LLM router calls.

Writes one row per call attempt to `llm_calls` in the existing ARGUS SQLite
database. Tracks fallback paths so we can later answer questions like:
  - what % of risk_classify calls actually hit Groq vs. fell back to OpenAI?
  - which provider has the worst latency / failure rate?
  - how many tokens/$ per persona dashboard render?

The table is created lazily on first write. Each insert opens its own
connection (SQLite, WAL-friendly) — no shared connection state.

Failures inside telemetry MUST NOT propagate: a logging error is never worth
breaking a successful LLM call. Helper `log_call_safe()` swallows exceptions.
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
CREATE TABLE IF NOT EXISTS llm_calls (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT    NOT NULL,
    task            TEXT    NOT NULL,
    provider        TEXT    NOT NULL,
    model           TEXT    NOT NULL,
    status          TEXT    NOT NULL,            -- 'ok' | 'failed'
    latency_ms      INTEGER,
    tokens_in       INTEGER DEFAULT 0,
    tokens_out      INTEGER DEFAULT 0,
    error_short     TEXT,
    fallback_index  INTEGER DEFAULT 0,           -- 0 = first attempt, 1 = second, ...
    request_id      TEXT                         -- groups attempts in the same call_llm() invocation
);

CREATE INDEX IF NOT EXISTS ix_llm_calls_ts       ON llm_calls(timestamp);
CREATE INDEX IF NOT EXISTS ix_llm_calls_task     ON llm_calls(task);
CREATE INDEX IF NOT EXISTS ix_llm_calls_provider ON llm_calls(provider);
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


def log_call(
    *,
    task: str,
    provider: str,
    model: str,
    status: str,
    latency_ms: int,
    tokens_in: int = 0,
    tokens_out: int = 0,
    error_short: Optional[str] = None,
    fallback_index: int = 0,
    request_id: Optional[str] = None,
) -> None:
    """Synchronous insert. Raises on failure (use log_call_safe in hot paths)."""
    _ensure_schema()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """INSERT INTO llm_calls
               (timestamp, task, provider, model, status, latency_ms,
                tokens_in, tokens_out, error_short, fallback_index, request_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                datetime.datetime.now(datetime.timezone.utc).isoformat(),
                task,
                provider,
                model,
                status,
                latency_ms,
                tokens_in,
                tokens_out,
                (error_short or "")[:500],
                fallback_index,
                request_id,
            ),
        )
        conn.commit()


def log_call_safe(**kwargs) -> None:
    """Same as log_call but swallows any exception.

    Telemetry is best-effort; never break the caller. Errors are printed to
    stderr so they are visible during dev but don't surface to users.
    """
    try:
        log_call(**kwargs)
    except Exception as e:
        print(f"[llm_telemetry] log failed: {e}", file=sys.stderr)
