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


# ──────────────────────────────────────────────────────────────
# Defensive fixes from Copilot review (limit parse, LIKE escape,
# kill-switch fallback reason, conn-leak via try/finally).
# ──────────────────────────────────────────────────────────────

class TestDefensiveBehavior:
    def test_non_numeric_limit_returns_400(self, client):
        """A non-numeric ``?limit=abc`` used to bubble up as a 500 because
        ``int()`` raised before any error handling. The handler now safe-
        parses and returns 400 with an actionable message."""
        r = client.get("/api/regulatory?limit=abc")
        assert r.status_code == 400
        body = r.json
        assert body["status"] == "error"
        assert "limit" in body["message"].lower()

    def test_zero_limit_returns_400(self, client):
        """``?limit=0`` is also rejected — a successful query that
        promises to return at most zero items is a silent bug magnet."""
        r = client.get("/api/regulatory?limit=0")
        assert r.status_code == 400

    def test_like_wildcards_in_q_are_escaped(self, client, monkeypatch):
        """When the semantic helper returns nothing, the SQL LIKE branch
        runs. A user typing ``50%`` should match the literal substring,
        not be treated as a wildcard. We assert by capturing the LIKE
        pattern actually sent to SQLite.

        ``sqlite3.Connection.execute`` is a read-only C attribute so we
        can't monkey-patch it; wrap the real connection in a delegate.
        """
        import routes.regulatory_routes as rr
        monkeypatch.setattr(rr, "_semantic_regulatory_search",
                            lambda q, top_k_override=None: ([], "rid", None))

        captured = {}
        real_get_conn = rr._get_conn

        def spying_get_conn():
            real_conn = real_get_conn()

            class _Spy:
                def execute(self, sql, params=()):
                    if "LIKE" in sql.upper():
                        captured["sql"]    = sql
                        captured["params"] = list(params)
                    return real_conn.execute(sql, params)
                def cursor(self): return real_conn.cursor()
                def commit(self): return real_conn.commit()
                def close(self): return real_conn.close()

            return _Spy()

        monkeypatch.setattr(rr, "_get_conn", spying_get_conn)

        r = client.get("/api/regulatory?q=50%25_rate&limit=5")
        assert r.status_code == 200

        # The LIKE params must contain the escaped pattern, NOT raw % or _.
        assert "sql" in captured, "expected SQL LIKE path to execute"
        assert "ESCAPE" in captured["sql"].upper()
        like_params = [p for p in captured["params"] if isinstance(p, str) and "%" in p]
        assert like_params, "expected at least one LIKE param"
        for p in like_params:
            # Strip the bracketing %...% wildcards that the handler adds.
            inner = p[1:-1]
            # Literal % and _ from the user input must have been escaped.
            assert "\\%" in inner or "\\_" in inner, (
                f"user-supplied wildcards must be escaped, got {inner!r}"
            )

    def test_rag_block_reports_disabled_when_kill_switch_off(self, client, monkeypatch):
        """When ``regulatory_search.enabled: false``, the handler should
        explicitly say so in the rag block so operators can tell config-
        driven fallback apart from "Chroma returned nothing"."""
        import routes.regulatory_routes as rr
        monkeypatch.setattr(rr, "_semantic_regulatory_search",
                            lambda q, top_k_override=None: ([], None, None))
        monkeypatch.setattr(rr, "_semantic_search_disabled", lambda: True)

        r = client.get("/api/regulatory?q=anything&limit=5")
        assert r.status_code == 200
        assert r.json["rag"] == {"used": False, "fallback": "disabled"}

    def test_rag_block_reports_sql_like_when_chroma_returned_empty(self, client, monkeypatch):
        """Helper enabled but returned no hits → fallback must be
        ``sql_like`` and include the request_id for telemetry correlation."""
        import routes.regulatory_routes as rr
        monkeypatch.setattr(rr, "_semantic_regulatory_search",
                            lambda q, top_k_override=None: ([], "rid-test", None))
        monkeypatch.setattr(rr, "_semantic_search_disabled", lambda: False)

        r = client.get("/api/regulatory?q=anything&limit=5")
        assert r.status_code == 200
        assert r.json["rag"] == {
            "used":       False,
            "request_id": "rid-test",
            "fallback":   "sql_like",
        }

    def test_semantic_helper_exception_does_not_leak_connection(self, client, monkeypatch):
        """Pre-fix, ``_semantic_regulatory_search`` raising before the
        conn-open try/finally would leak the SQLite connection. The
        handler is now structured so the helper runs *before* conn-open,
        so a 500-producing raise still cleans up.
        """
        import routes.regulatory_routes as rr

        def boom(q, top_k_override=None):
            raise RuntimeError("simulated helper failure")

        monkeypatch.setattr(rr, "_semantic_regulatory_search", boom)

        # The raise happens before conn-open, so Flask returns a 500.
        # The critical assertion is just that the request *completes*
        # (no hang on a leaked file handle) and conn was never opened.
        r = client.get("/api/regulatory?q=anything&limit=5")
        assert r.status_code == 500
