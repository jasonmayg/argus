"""
Unit tests for core.agent_telemetry.

Each test points DB_PATH at a temp file so the prod DB is never
touched. `reset_schema_cache()` makes the module re-create the
schema in the new location.

Run:
    pytest tests/test_agent_telemetry.py -v
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def telemetry(tmp_path, monkeypatch):
    """Yield the agent_telemetry module pointed at a fresh temp DB."""
    import core.agent_telemetry as at
    monkeypatch.setattr(at, "DB_PATH", tmp_path / "test.db")
    at.reset_schema_cache()
    yield at
    at.reset_schema_cache()


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TestSchema:
    def test_table_created_on_first_log(self, telemetry):
        telemetry.log_run(
            agent_name="x", invoked_by="test",
            request_id="r1", started_at=_now(), finished_at=_now(),
            status="ok",
        )
        with sqlite3.connect(telemetry.DB_PATH) as c:
            rows = c.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='agent_executions'"
            ).fetchall()
        assert rows, "agent_executions table must be created on first log call"

    def test_indexes_created(self, telemetry):
        telemetry.log_run(
            agent_name="x", invoked_by="test",
            request_id="r1", started_at=_now(), finished_at=_now(), status="ok",
        )
        with sqlite3.connect(telemetry.DB_PATH) as c:
            idxs = {r[0] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND tbl_name='agent_executions'"
            ).fetchall()}
        # Indexes on timestamp, agent_name, request_id are needed for
        # the Phase 3E reporting queries to stay cheap.
        assert any("ts" in n or "started_at" in n for n in idxs)
        assert any("agent" in n for n in idxs)
        assert any("request" in n for n in idxs)


class TestLogRun:
    def test_round_trip(self, telemetry):
        new_id = telemetry.log_run(
            agent_name="intelligence", invoked_by="api",
            request_id="abc123", started_at=_now(), finished_at=_now(),
            status="ok", turns_used=3,
            tokens_in=1000, tokens_out=500, cost_usd=0.012,
            tool_calls=[{"tool": "search_news", "ok": True, "latency_ms": 42}],
            output_preview="hello world",
        )
        assert new_id > 0

        with sqlite3.connect(telemetry.DB_PATH) as c:
            row = c.execute(
                "SELECT agent_name, status, turns_used, tokens_in, tokens_out, "
                "       cost_usd, tool_calls_json, output_preview "
                "FROM agent_executions WHERE id=?", (new_id,),
            ).fetchone()
        assert row[0] == "intelligence"
        assert row[1] == "ok"
        assert row[2] == 3
        assert row[3] == 1000 and row[4] == 500
        assert row[5] == 0.012
        assert json.loads(row[6])[0]["tool"] == "search_news"
        assert row[7] == "hello world"

    def test_output_preview_truncated_to_500(self, telemetry):
        long_text = "x" * 1200
        new_id = telemetry.log_run(
            agent_name="x", invoked_by="test",
            request_id="r1", started_at=_now(), finished_at=_now(),
            status="ok", output_preview=long_text,
        )
        with sqlite3.connect(telemetry.DB_PATH) as c:
            preview = c.execute(
                "SELECT output_preview FROM agent_executions WHERE id=?", (new_id,),
            ).fetchone()[0]
        assert preview is not None and len(preview) == 500

    def test_error_path_persists_class_and_message(self, telemetry):
        telemetry.log_run(
            agent_name="x", invoked_by="test",
            request_id="r1", started_at=_now(), finished_at=_now(),
            status="error", error_class="RateLimitError",
            error_message="too many requests",
        )
        with sqlite3.connect(telemetry.DB_PATH) as c:
            row = c.execute(
                "SELECT status, error_class, error_message FROM agent_executions"
            ).fetchone()
        assert row == ("error", "RateLimitError", "too many requests")


class TestLogRunSafe:
    def test_swallows_exception_and_returns_none(self, telemetry, monkeypatch):
        """Telemetry must never break the foreground agent. If the DB
        write fails, log_run_safe returns None — same contract as
        llm_telemetry.log_call_safe."""
        def boom(**_kwargs):
            raise sqlite3.OperationalError("disk full")
        monkeypatch.setattr(telemetry, "log_run", boom)
        out = telemetry.log_run_safe(
            agent_name="x", invoked_by="test",
            request_id="r1", started_at=_now(), finished_at=_now(),
            status="ok",
        )
        assert out is None


class TestRequestId:
    def test_format_matches_llm_calls(self, telemetry):
        rid = telemetry.new_request_id()
        assert isinstance(rid, str)
        # 16-char hex matches llm_telemetry.log_call's request_id format
        # so joins between agent_executions and llm_calls work cleanly.
        assert len(rid) == 16
        int(rid, 16)  # would raise if not hex
