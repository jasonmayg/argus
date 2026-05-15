"""
Unit tests for ``classify_news._init_risk_analysis_schema``.

The migration's job is to take a ``risk_analysis`` table in any of three
states — missing, partial-legacy, fully-migrated — and leave it with the
full 12-column schema that ``_upsert_analysis`` writes against. Each
test exercises one of those starting states and asserts:

  * the resulting column set is correct,
  * the call is idempotent (running it again is a no-op),
  * a real ``_upsert_analysis`` against the migrated table actually
    succeeds (the bug this fix addresses was an ``OperationalError``,
    so an end-to-end INSERT is the only honest assertion that we're done).

The tests use ``sqlite3.connect(":memory:")`` so they're hermetic and
fast — no temp file management needed.

Run:
    pytest tests/test_classify_news_schema.py -v
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from classify_news import _init_risk_analysis_schema, _upsert_analysis  # noqa: E402


EXPECTED_COLUMNS = {
    "id",
    "news_id",
    "risk_category",
    "risk_score",
    "analysis_text",
    "news_score",
    "data_score",
    "linked_indicators",
    "indicator_values",
    "triggered_alerts",
    "scored_at",
    "created_at",
}

# Mirrors the buggy "legacy" schema that production used to have: only
# six columns, missing the news_score / data_score / indicator JSONs /
# scored_at fields that _upsert_analysis writes.
LEGACY_SQL = """
    CREATE TABLE risk_analysis (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        news_id       INTEGER NOT NULL,
        risk_category TEXT,
        risk_score    INTEGER,
        analysis_text TEXT,
        created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
"""


def _columns(conn: sqlite3.Connection) -> set[str]:
    return {row[1] for row in conn.execute("PRAGMA table_info(risk_analysis)")}


def _sample_result() -> dict:
    """A minimal but realistic _classify_single() output for INSERT tests."""
    return {
        "risk_category":     "Regulatory",
        "risk_score":        2,
        "analysis_text":     "test",
        "news_score":        4.5,
        "data_score":        0.0,
        "linked_indicators": [],
        "indicator_values":  {},
        "triggered_alerts":  [],
    }


# ──────────────────────────────────────────────────────────────
# Starting state 1: table does not exist
# ──────────────────────────────────────────────────────────────

class TestFreshDB:
    def test_creates_table_with_full_schema(self):
        conn = sqlite3.connect(":memory:")
        _init_risk_analysis_schema(conn)
        assert _columns(conn) == EXPECTED_COLUMNS

    def test_idempotent_on_fresh_db(self):
        conn = sqlite3.connect(":memory:")
        _init_risk_analysis_schema(conn)
        _init_risk_analysis_schema(conn)  # must not raise or duplicate
        assert _columns(conn) == EXPECTED_COLUMNS

    def test_allows_upsert_analysis_to_succeed(self):
        """End-to-end: after migration, the SQL that production runs must
        actually commit instead of raising OperationalError."""
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE news (id INTEGER PRIMARY KEY, title TEXT)")
        conn.execute("INSERT INTO news (id, title) VALUES (1, 'hi')")
        _init_risk_analysis_schema(conn)
        _upsert_analysis(conn, news_id=1, result=_sample_result())
        row = conn.execute(
            "SELECT risk_category, news_score, data_score FROM risk_analysis WHERE news_id=1"
        ).fetchone()
        assert row == ("Regulatory", 4.5, 0.0)


# ──────────────────────────────────────────────────────────────
# Starting state 2: legacy 6-column table (the production bug case)
# ──────────────────────────────────────────────────────────────

class TestLegacyDB:
    def test_alters_partial_table_to_full_schema(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(LEGACY_SQL)
        assert _columns(conn) == {
            "id", "news_id", "risk_category", "risk_score",
            "analysis_text", "created_at",
        }
        _init_risk_analysis_schema(conn)
        assert _columns(conn) == EXPECTED_COLUMNS

    def test_preserves_existing_rows(self):
        """An ALTER TABLE ADD COLUMN must not drop pre-migration data."""
        conn = sqlite3.connect(":memory:")
        conn.execute(LEGACY_SQL)
        conn.execute(
            "INSERT INTO risk_analysis (news_id, risk_category, risk_score, analysis_text) "
            "VALUES (?, ?, ?, ?)",
            (42, "OldCat", 3, "preserved"),
        )
        conn.commit()
        _init_risk_analysis_schema(conn)
        row = conn.execute(
            "SELECT news_id, risk_category, risk_score, analysis_text, news_score "
            "FROM risk_analysis WHERE news_id=42"
        ).fetchone()
        # The legacy columns survive; the new columns come back NULL.
        assert row == (42, "OldCat", 3, "preserved", None)

    def test_idempotent_on_legacy_db(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(LEGACY_SQL)
        _init_risk_analysis_schema(conn)
        _init_risk_analysis_schema(conn)  # second pass: nothing to add
        assert _columns(conn) == EXPECTED_COLUMNS

    def test_allows_upsert_analysis_against_legacy_db(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE news (id INTEGER PRIMARY KEY, title TEXT)")
        conn.execute("INSERT INTO news (id, title) VALUES (1, 'hi')")
        conn.execute(LEGACY_SQL)
        _init_risk_analysis_schema(conn)
        _upsert_analysis(conn, news_id=1, result=_sample_result())
        assert conn.execute("SELECT COUNT(*) FROM risk_analysis").fetchone()[0] == 1


# ──────────────────────────────────────────────────────────────
# Diagnostic logging — the reviewer asked for per-column INFO so an
# operator can see what changed the first time the migration runs.
# ──────────────────────────────────────────────────────────────

class TestLogging:
    def test_info_logged_per_added_column(self, caplog):
        conn = sqlite3.connect(":memory:")
        conn.execute(LEGACY_SQL)
        with caplog.at_level("INFO", logger="classify_news"):
            _init_risk_analysis_schema(conn)
        added_messages = [r.message for r in caplog.records if "adding missing column" in r.message]
        # Six columns are missing on the legacy schema — every one must log.
        assert len(added_messages) == 6
        for col in (
            "news_score", "data_score", "linked_indicators",
            "indicator_values", "triggered_alerts", "scored_at",
        ):
            assert any(col in m for m in added_messages), f"missing log line for {col}"

    def test_silent_at_info_when_nothing_to_add(self, caplog):
        """Second invocation: schema is already complete, no INFO lines."""
        conn = sqlite3.connect(":memory:")
        _init_risk_analysis_schema(conn)            # first call adds everything
        caplog.clear()
        with caplog.at_level("INFO", logger="classify_news"):
            _init_risk_analysis_schema(conn)        # second call: no-op
        assert not [r for r in caplog.records if "adding missing column" in r.message]


# ──────────────────────────────────────────────────────────────
# Exception handling — a broken migration must be loud, not silent.
# ──────────────────────────────────────────────────────────────

class TestUniqueIndex:
    """The migration must enforce one-row-per-news_id. _upsert_analysis's
    SELECT id WHERE news_id=? branching depends on it; without the index,
    a stray duplicate row would silently break insert-vs-update."""

    def _index_exists(self, conn: sqlite3.Connection) -> bool:
        cur = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='index' AND name='idx_risk_analysis_news_id'"
        )
        return cur.fetchone() is not None

    def test_index_created_on_fresh_db(self):
        conn = sqlite3.connect(":memory:")
        _init_risk_analysis_schema(conn)
        assert self._index_exists(conn)

    def test_index_created_on_legacy_db(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(LEGACY_SQL)
        _init_risk_analysis_schema(conn)
        assert self._index_exists(conn)

    def test_duplicate_news_id_insert_is_rejected(self):
        """The whole point of the index: a second INSERT with the same
        news_id must raise IntegrityError, not silently duplicate."""
        conn = sqlite3.connect(":memory:")
        _init_risk_analysis_schema(conn)
        conn.execute(
            "INSERT INTO risk_analysis (news_id, risk_category) VALUES (?, ?)",
            (7, "A"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO risk_analysis (news_id, risk_category) VALUES (?, ?)",
                (7, "B"),
            )


class TestExceptionHandling:
    def test_operational_error_is_logged_and_raised(self, caplog):
        """Simulate the ALTER TABLE failing (e.g. corrupted DB). The
        migration must log at ERROR and re-raise rather than silently
        swallowing — silent failure was the original bug we're fixing.

        ``sqlite3.Connection.execute`` is a read-only C attribute so we
        can't monkey-patch it; instead wrap the real connection in a
        thin delegate that intercepts ALTER statements.
        """
        real_conn = sqlite3.connect(":memory:")
        real_conn.execute(LEGACY_SQL)

        class _FaultingConn:
            def execute(self, sql, *args, **kw):
                if sql.strip().upper().startswith("ALTER TABLE"):
                    raise sqlite3.OperationalError("simulated migration failure")
                return real_conn.execute(sql, *args, **kw)

            def commit(self):
                return real_conn.commit()

        with caplog.at_level("ERROR", logger="classify_news"):
            with pytest.raises(sqlite3.OperationalError, match="simulated migration failure"):
                _init_risk_analysis_schema(_FaultingConn())  # type: ignore[arg-type]
        assert any("schema init failed" in r.message for r in caplog.records)
