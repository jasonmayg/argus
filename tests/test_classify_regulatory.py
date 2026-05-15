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

from classify_regulatory import (  # noqa: E402
    EXTERNAL_NEWS_SOURCES,
    _get_unprocessed_external_news,
    _upsert_regulatory,
    init_db,
)


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


# ──────────────────────────────────────────────────────────────
# External news multi-source fetch — guards the fan-out across
# every CSV in EXTERNAL_NEWS_SOURCES (Google Radar + central
# bank press releases pulled by AI_Treasury_Project).
# ──────────────────────────────────────────────────────────────

class TestExternalNewsFanOut:
    """The fetcher is best-effort: a missing or broken CSV must not
    abort other sources, dedup must scope per source_type, and the
    tagged (source_type, item) tuple must propagate to the caller so
    _upsert_regulatory writes the right source_type."""

    def test_source_type_propagates_to_returned_tuples(self, conn, monkeypatch):
        """Tuple shape: every returned (source_type, item) must match
        the configured source_type label, not the file name or a
        defaulted string."""
        import classify_regulatory as cr

        # Stub the CSV reader so each source returns a single distinct row.
        def fake_fetch(filename: str):
            return [{"id": f"id_for_{filename}", "title": f"t_{filename}",
                     "url": "", "published_at": "", "summary": "", "source": ""}]
        monkeypatch.setattr(cr, "_fetch_external_news_csv", fake_fetch)

        out = _get_unprocessed_external_news(conn, force=True)

        # Each configured source contributed exactly one item.
        assert len(out) == len(EXTERNAL_NEWS_SOURCES)
        seen_types = [t for t, _ in out]
        assert seen_types == [t for t, _ in EXTERNAL_NEWS_SOURCES]

    def test_dedup_is_scoped_per_source_type(self, conn, monkeypatch):
        """If ECB and BoE both emit an item with id 'mps', NEITHER
        should be filtered out — they're different sources. Dedup
        only kicks in when (source_type, source_id) already exists."""
        import classify_regulatory as cr

        # Seed: ECB already classified item id='mps'. BoE has not.
        conn.execute(
            "INSERT INTO regulatory_changes (source_type, source_id, title, "
            "classified_at) VALUES (?, ?, ?, ?)",
            ("ecb_press", "mps", "Old MPS", "2026-01-01"),
        )
        conn.commit()

        def fake_fetch(filename: str):
            if filename == "news_ecb_press.csv":
                return [{"id": "mps", "title": "ECB MPS", "url": "",
                         "published_at": "", "summary": "", "source": ""}]
            if filename == "news_boe_press.csv":
                return [{"id": "mps", "title": "BoE MPS", "url": "",
                         "published_at": "", "summary": "", "source": ""}]
            return []
        monkeypatch.setattr(cr, "_fetch_external_news_csv", fake_fetch)

        out = _get_unprocessed_external_news(conn, force=False)
        by_type = {t: i for t, i in out}

        # ECB's "mps" was already classified → filtered out.
        assert "ecb_press" not in by_type
        # BoE's "mps" is independent (different source_type) → kept.
        assert "boe_press" in by_type
        assert by_type["boe_press"]["title"] == "BoE MPS"

    def test_failed_csv_does_not_abort_others(self, conn, monkeypatch):
        """If one CSV is missing/empty, the fetch for that source
        returns [] but the rest still flow through. This is the
        Phase-1A "best-effort daily fetch" contract."""
        import classify_regulatory as cr

        def fake_fetch(filename: str):
            if filename == "news_ecb_press.csv":
                return []   # simulate missing-in-GCS
            return [{"id": f"id_{filename}", "title": "ok", "url": "",
                     "published_at": "", "summary": "", "source": ""}]
        monkeypatch.setattr(cr, "_fetch_external_news_csv", fake_fetch)

        out = _get_unprocessed_external_news(conn, force=True)
        types = [t for t, _ in out]
        assert "ecb_press" not in types
        # Everyone else still represented.
        for t, _filename in EXTERNAL_NEWS_SOURCES:
            if t == "ecb_press":
                continue
            assert t in types, f"missing source: {t}"

    def test_force_bypasses_dedup(self, conn, monkeypatch):
        """`force=True` should return everything the CSV had, even if
        already-classified. Used by CLI `--force` for full re-runs."""
        import classify_regulatory as cr

        conn.execute(
            "INSERT INTO regulatory_changes (source_type, source_id, title, "
            "classified_at) VALUES (?, ?, ?, ?)",
            ("ecb_press", "id_for_news_ecb_press.csv", "old", "2026-01-01"),
        )
        conn.commit()

        def fake_fetch(filename: str):
            return [{"id": f"id_for_{filename}", "title": "t", "url": "",
                     "published_at": "", "summary": "", "source": ""}]
        monkeypatch.setattr(cr, "_fetch_external_news_csv", fake_fetch)

        without_force = _get_unprocessed_external_news(conn, force=False)
        with_force    = _get_unprocessed_external_news(conn, force=True)

        # Without force: the seeded ECB row is filtered out.
        types_no_force = [t for t, _ in without_force]
        assert "ecb_press" not in types_no_force

        # With force: all sources present including ECB.
        types_force = [t for t, _ in with_force]
        assert "ecb_press" in types_force
        assert len(with_force) == len(EXTERNAL_NEWS_SOURCES)


class TestSourcesContract:
    """The EXTERNAL_NEWS_SOURCES list is a contract with AI_Treasury_
    Project's sources.yaml — if filenames drift, the downstream silently
    finds nothing in GCS. Cheap to guard the basics here."""

    def test_no_duplicate_source_types(self):
        types = [t for t, _ in EXTERNAL_NEWS_SOURCES]
        assert len(types) == len(set(types)), \
            f"duplicate source_type would collide on dedup: {types}"

    def test_no_duplicate_filenames(self):
        files = [f for _, f in EXTERNAL_NEWS_SOURCES]
        assert len(files) == len(set(files)), \
            f"duplicate filename means we'd fetch the same CSV twice: {files}"

    def test_filenames_match_ai_treasury_news_id_convention(self):
        """Every filename must be `news_<rest>.csv` — that's the
        convention AI_Treasury_Project's RSS adapter writes. If a
        filename here doesn't match, the corresponding source id in
        sources.yaml almost certainly doesn't either."""
        for _, filename in EXTERNAL_NEWS_SOURCES:
            assert filename.startswith("news_"), filename
            assert filename.endswith(".csv"),    filename

