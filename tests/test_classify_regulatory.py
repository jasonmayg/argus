"""
Unit test for ``classify_regulatory._upsert_regulatory``'s id resolution.

The function returns a SQLite row id that the Phase 2E Chroma mirror then
uses as the vector doc key (``reg_{id}``). If the returned id is ever
stale, the mirror writes the vector under the wrong key and silently
corrupts the index.

This test specifically guards the ON CONFLICT DO UPDATE path: on that
branch SQLite's ``last_insert_rowid()`` returns the most recent
*successful insert* on the connection — which on a batch run is an
earlier sibling row, not the one we just updated. The fix is to resolve
the id via ``SELECT ... WHERE (source_type, source_id) = ?`` every time.

Run:
    pytest tests/test_classify_regulatory.py -v
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from classify_regulatory import _upsert_regulatory, init_db  # noqa: E402


def _result() -> dict:
    return {
        "is_regulatory": True, "country": "USA",
        "category": "Banking", "severity": "Medium",
        "change_before": "", "change_after": "",
        "effective_date": "", "treasury_impact": "",
        "action_required": "", "confidence": 0.9,
    }


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    init_db(c)
    yield c
    c.close()


class TestStableRegId:
    def test_returns_correct_id_on_fresh_insert(self, conn):
        reg_id = _upsert_regulatory(conn, "fed_news", {
            "id": "alpha", "title": "T1", "summary": "",
        }, _result())
        assert reg_id > 0
        row = conn.execute(
            "SELECT title FROM regulatory_changes WHERE id=?", (reg_id,)
        ).fetchone()
        assert row[0] == "T1"

    def test_update_after_insert_returns_updated_row_id_not_prior_insert(self, conn):
        """The bug Copilot flagged: insert one row, then UPSERT a DIFFERENT
        existing row on the same connection. Pre-fix, ``cur.lastrowid``
        would have returned the prior insert's id (not the updated row's),
        so the Chroma mirror would have written the vector under the
        wrong reg_id. Post-fix, we always SELECT by the unique key.
        """
        # Step 1: insert a row that will dirty lastrowid on the connection.
        first_id = _upsert_regulatory(conn, "fed_news", {
            "id": "alpha", "title": "First", "summary": "",
        }, _result())

        # Step 2: insert a second, separate row.
        second_id = _upsert_regulatory(conn, "fed_news", {
            "id": "beta", "title": "Second", "summary": "",
        }, _result())
        assert second_id != first_id

        # Step 3: UPSERT the FIRST row again — this is the ON CONFLICT
        # DO UPDATE path. Pre-fix, lastrowid would have returned
        # second_id (the most recent successful insert).
        updated_id = _upsert_regulatory(conn, "fed_news", {
            "id": "alpha", "title": "First updated", "summary": "",
        }, _result())

        assert updated_id == first_id, (
            "ON CONFLICT DO UPDATE must return the id of the row that was "
            "actually updated, not the connection's last_insert_rowid"
        )

        # Sanity check: the title in DB matches the UPDATE, not the prior insert.
        row = conn.execute(
            "SELECT title FROM regulatory_changes WHERE id=?", (updated_id,)
        ).fetchone()
        assert row[0] == "First updated"

