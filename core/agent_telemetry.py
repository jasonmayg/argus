"""
Phase 3A — `agent_executions` table.

One row per AgentRunner.run() invocation. Joins back to `llm_calls`
via `request_id` (each turn inside the agent loop logs a row there as
its own call), and surfaces aggregate cost / tool usage so we can
answer questions like "how much did IntelligenceAgent cost this week?"
or "which agent timed out on max_turns most often?".

Schema matches the spec in Argus_Phase3_Design.md §7.1.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

logger = logging.getLogger(__name__)

DB_PATH = Path(os.getenv("ARGUS_NEWS_DB", "data/fed_news/argus_news.db"))

_DDL = """
CREATE TABLE IF NOT EXISTS agent_executions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_name      TEXT    NOT NULL,           -- intelligence / response / actions / etc.
    invoked_by      TEXT    NOT NULL,           -- api / cron / test
    request_id      TEXT    NOT NULL,           -- joins to llm_calls.request_id
    started_at      TEXT    NOT NULL,
    finished_at     TEXT,
    status          TEXT    NOT NULL,           -- ok | budget_exceeded | max_turns | error
    turns_used      INTEGER DEFAULT 0,
    tokens_in       INTEGER DEFAULT 0,
    tokens_out      INTEGER DEFAULT 0,
    cost_usd        REAL    DEFAULT 0,
    tool_calls_json TEXT,                       -- JSON array: [{tool, ok, latency_ms}, ...]
    output_preview  TEXT,                       -- first 500 chars of final output
    output_full_path TEXT,                      -- path to full output on disk, if any
    error_class     TEXT,
    error_message   TEXT
);

CREATE INDEX IF NOT EXISTS ix_agent_executions_ts      ON agent_executions(started_at);
CREATE INDEX IF NOT EXISTS ix_agent_executions_agent   ON agent_executions(agent_name);
CREATE INDEX IF NOT EXISTS ix_agent_executions_request ON agent_executions(request_id);
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
    """Test hook: forces _ensure_schema to re-run. Production code never
    calls this; tests use it after pointing DB_PATH at a temp file."""
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


def new_request_id() -> str:
    """Stable id format matching the existing llm_calls convention
    (16-char hex). Generate once at the start of an agent run and
    pass through every turn so telemetry can be joined cleanly."""
    return uuid.uuid4().hex[:16]


def log_run(
    *,
    agent_name: str,
    invoked_by: str,
    request_id: str,
    started_at: datetime,
    finished_at: Optional[datetime],
    status: str,
    turns_used: int = 0,
    tokens_in: int = 0,
    tokens_out: int = 0,
    cost_usd: float = 0.0,
    tool_calls: Optional[List[Dict[str, Any]]] = None,
    output_preview: Optional[str] = None,
    output_full_path: Optional[str] = None,
    error_class: Optional[str] = None,
    error_message: Optional[str] = None,
) -> int:
    """Insert one `agent_executions` row and return the new id.

    Designed to be called once per agent invocation, after the run
    finishes (success or failure). Doing it post-hoc instead of
    INSERT-then-UPDATE keeps the schema simple and avoids the partial-
    row state on crash that the llm_calls table also avoids."""
    with _conn() as c:
        cur = c.execute(
            """
            INSERT INTO agent_executions (
                agent_name, invoked_by, request_id, started_at, finished_at,
                status, turns_used, tokens_in, tokens_out, cost_usd,
                tool_calls_json, output_preview, output_full_path,
                error_class, error_message
            ) VALUES (?, ?, ?, ?, ?,  ?, ?, ?, ?, ?,  ?, ?, ?,  ?, ?)
            """,
            (
                agent_name, invoked_by, request_id,
                started_at.isoformat(),
                finished_at.isoformat() if finished_at else None,
                status, turns_used, tokens_in, tokens_out, cost_usd,
                json.dumps(tool_calls) if tool_calls is not None else None,
                (output_preview or "")[:500] or None,
                output_full_path,
                error_class, error_message,
            ),
        )
        return int(cur.lastrowid)


def log_run_safe(**kwargs: Any) -> Optional[int]:
    """Fail-soft variant. Telemetry must never break the foreground
    agent — if the DB write throws, log and return None instead of
    propagating. Same contract as llm_telemetry.log_call_safe()."""
    try:
        return log_run(**kwargs)
    except Exception as exc:
        logger.warning("agent_telemetry: write failed (%s)", exc)
        return None
