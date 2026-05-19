"""
Unit tests for core.persona_eval.run_log.

Mirrors test_agent_telemetry's pattern: point DB_PATH at a temp file
so no test ever touches the production SQLite.

Run:
    pytest tests/test_persona_eval_run_log.py -v
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def run_log(tmp_path, monkeypatch):
    import core.persona_eval.run_log as rl
    monkeypatch.setattr(rl, "DB_PATH", tmp_path / "test.db")
    rl.reset_schema_cache()
    yield rl
    rl.reset_schema_cache()


class TestSchema:
    def test_table_created_on_first_log(self, run_log):
        run_log.log_run(
            window_start="2026-04-19", window_end="2026-05-19",
            alerts_count=80, avg_jaccard=0.5, max_jaccard=0.7,
        )
        with sqlite3.connect(run_log.DB_PATH) as c:
            row = c.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='persona_eval_runs'"
            ).fetchone()
        assert row is not None


class TestLogRun:
    def test_round_trip(self, run_log):
        new_id = run_log.log_run(
            window_start="2026-04-19", window_end="2026-05-19",
            alerts_count=80, avg_jaccard=0.56, max_jaccard=0.71,
            llm_model="claude-sonnet-4-6", llm_cost_usd=0.12,
            report_md_path="/tmp/r.md", report_json_path="/tmp/r.json",
            notes="first run",
        )
        assert new_id > 0
        with sqlite3.connect(run_log.DB_PATH) as c:
            row = c.execute(
                "SELECT alerts_count, avg_jaccard, llm_model, notes "
                "FROM persona_eval_runs WHERE id=?", (new_id,),
            ).fetchone()
        assert row == (80, 0.56, "claude-sonnet-4-6", "first run")


class TestLogRunSafe:
    def test_swallows_exception(self, run_log, monkeypatch):
        def boom(**_kwargs):
            raise sqlite3.OperationalError("disk full")
        monkeypatch.setattr(run_log, "log_run", boom)
        out = run_log.log_run_safe(
            window_start="x", window_end="y",
            alerts_count=0, avg_jaccard=0.0, max_jaccard=0.0,
        )
        assert out is None
