"""
Unit tests for ``routes.regulatory_routes`` Phase 2F semantic search,
specifically the retry-on-narrow-filter heuristic.

The retry is hard to exercise against the real local Chroma because
``max_distance: 1.2`` is the binding constraint in the current corpus —
asking for ``top_k=80`` returns no more hits than ``top_k=20``. So the
tests below stub ``_semantic_regulatory_search`` to return synthetic
``(reg_id, distance)`` lists with controlled cardinality, then assert
the retry fires (or not) under each branch.

Run:
    pytest tests/test_regulatory_search.py -v
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ──────────────────────────────────────────────────────────────
# Fixtures: an in-memory SQLite seeded with a known mix of countries
# ──────────────────────────────────────────────────────────────

@pytest.fixture
def regulatory_db(tmp_path, monkeypatch):
    """Build a tiny on-disk regulatory_changes DB and point the route
    module at it. Returns ``(db_path, ids)`` where ``ids`` maps a
    semantic-hit id to the row's country so tests can predict outcomes.
    """
    db_path = tmp_path / "argus_news.db"
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE regulatory_changes (
            id              INTEGER PRIMARY KEY,
            source_type     TEXT, source_id TEXT,
            title           TEXT, url TEXT, published_at TEXT,
            country         TEXT, category TEXT, severity TEXT,
            change_before   TEXT, change_after TEXT, effective_date TEXT,
            treasury_impact TEXT, action_required TEXT,
            confidence      REAL, is_regulatory INTEGER,
            raw_summary     TEXT, classified_at TEXT
        )
    """)
    # 20 rows: ids 1..20. Countries chosen so the first 18 are USA
    # (matching the realistic "top hits dominated by one country" case)
    # and ids 19-20 are Japan, which a Japan filter should surface.
    rows = []
    for i in range(1, 21):
        country = "Japan" if i >= 19 else "USA"
        rows.append((
            i, "fed_news", f"src_{i}",
            f"Reg {i}", "", "2026-01-01",
            country, "Banking Regulation", "Medium",
            "", "", "",
            "", "",
            0.8, 1,
            "", "2026-01-01",
        ))
    conn.executemany(
        "INSERT INTO regulatory_changes VALUES " +
        "(" + ",".join("?" * 18) + ")",
        rows,
    )
    conn.commit()
    conn.close()

    import routes.regulatory_routes as rr
    monkeypatch.setattr(rr, "DB_PATH", db_path)
    return db_path


@pytest.fixture
def client(regulatory_db, monkeypatch):
    """Flask test client + stubbable semantic helper."""
    from flask import Flask
    import routes.regulatory_routes as rr

    # Force endpoint config to known values so the test isn't coupled to
    # whatever is in rag_config.yaml.
    import core.rag_context as rc
    monkeypatch.setattr(rc, "_CONFIG_CACHE", {
        "endpoints": {
            "regulatory_search": {
                "enabled": True,
                "top_k": 5,         # small initial pool so retry has room
                "retry_top_k": 20,  # retry pulls all 20 docs
                "retry_threshold": 0.5,
            }
        }
    })

    app = Flask(__name__)
    app.register_blueprint(rr.regulatory_bp)
    return app.test_client()


def _stub_search(monkeypatch, hit_lists):
    """Make ``_semantic_regulatory_search`` return ``hit_lists[0]`` on
    first call (no override) and ``hit_lists[1]`` on retry calls. Each
    list element is a ``(reg_id, distance)`` tuple.
    """
    import routes.regulatory_routes as rr
    calls = {"n": 0}

    def fake_search(query, top_k_override=None):
        idx = 1 if top_k_override is not None else 0
        calls["n"] += 1
        return list(hit_lists[idx]), f"rid_{idx}", (hit_lists[idx][0][1] if hit_lists[idx] else None)

    monkeypatch.setattr(rr, "_semantic_regulatory_search", fake_search)
    return calls


# ──────────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────────

class TestRetryHeuristic:
    def test_retry_fires_when_filter_thins_results(self, client, monkeypatch):
        """First pass returns 5 USA hits; user filters for Japan → 0
        post-filter results. Retry expands to include Japan-tagged
        ids 19-20, which then satisfy the filter."""
        first  = [(i, 0.5 + i * 0.01) for i in range(1, 6)]            # ids 1..5 all USA
        retry  = [(i, 0.5 + i * 0.01) for i in range(1, 21)]           # ids 1..20 incl. Japan
        calls = _stub_search(monkeypatch, [first, retry])

        r = client.get("/api/regulatory?q=anything&country=Japan&limit=10")
        body = r.json

        assert calls["n"] == 2, "retry should have called the search a second time"
        assert body["rag"]["retried_top_k"] == 20
        # Items 19 and 20 are Japan-tagged — both should be returned
        japan_ids = sorted(i["id"] for i in body["items"])
        assert japan_ids == [19, 20]

    def test_retry_skipped_when_no_filter_active(self, client, monkeypatch):
        """No filter → first pass result is canonical, retry is pointless."""
        first = [(i, 0.5) for i in range(1, 3)]   # only 2 hits, no filter
        retry = [(i, 0.5) for i in range(1, 21)]
        calls = _stub_search(monkeypatch, [first, retry])

        r = client.get("/api/regulatory?q=anything&limit=10")
        assert calls["n"] == 1, "no filter active — retry must not fire"
        assert "retried_top_k" not in r.json["rag"]

    def test_retry_skipped_when_result_meets_threshold(self, client, monkeypatch):
        """First pass already returns ≥ limit * retry_threshold rows,
        so the retry would be wasted work."""
        # limit=10, threshold=0.5 → need ≥5 to skip retry
        # Six USA hits + Japan filter would still drop all six, but here
        # we filter by country=USA so all six pass.
        first = [(i, 0.5 + i * 0.01) for i in range(1, 7)]
        retry = [(i, 0.5 + i * 0.01) for i in range(1, 21)]
        calls = _stub_search(monkeypatch, [first, retry])

        r = client.get("/api/regulatory?q=anything&country=USA&limit=10")
        assert calls["n"] == 1, "result already meets threshold — retry must not fire"
        assert r.json["total"] == 6

    def test_retry_skipped_when_pool_did_not_grow(self, client, monkeypatch):
        """If the retry returns the same number of hits as the initial
        pass (Chroma already exhausted by max_distance cap), the
        endpoint should ignore the retry result so we don't churn."""
        first = [(i, 0.5) for i in range(1, 4)]   # 3 USA hits, no Japan
        retry = [(i, 0.5) for i in range(1, 4)]   # retry returns same 3
        calls = _stub_search(monkeypatch, [first, retry])

        r = client.get("/api/regulatory?q=anything&country=Japan&limit=10")
        # The retry DID fire (the heuristic doesn't know in advance the
        # pool won't grow), but the response must not advertise it since
        # we discarded the result.
        assert calls["n"] == 2
        assert "retried_top_k" not in r.json["rag"], (
            "same-size retry result must not be promoted — that would be "
            "a lie to the operator about useful work happening"
        )

    def test_retry_disabled_when_retry_top_k_zero(self, client, monkeypatch):
        """Setting retry_top_k <= top_k is the operator's opt-out."""
        import core.rag_context as rc
        monkeypatch.setattr(rc, "_CONFIG_CACHE", {
            "endpoints": {
                "regulatory_search": {
                    "enabled": True, "top_k": 5, "retry_top_k": 0,
                    "retry_threshold": 0.5,
                }
            }
        })
        first = [(i, 0.5) for i in range(1, 6)]
        retry = [(i, 0.5) for i in range(1, 21)]
        calls = _stub_search(monkeypatch, [first, retry])

        r = client.get("/api/regulatory?q=anything&country=Japan&limit=10")
        assert calls["n"] == 1, "retry_top_k=0 must keep the helper from being called twice"
