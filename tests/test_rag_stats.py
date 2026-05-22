"""
Unit tests for routes.rag_stats_routes.

The stats endpoint is the operator's window into Phase 2 retrieval
health, so the tests guard the contract callers depend on:

  * Aggregations over a trailing window — no leakage from rows
    outside the window.
  * Empty / missing-table cases return a usable shape (not 500),
    so the admin page never has to special-case "no data."
  * Input validation rejects garbage `hours` with 400.
  * Hit-rate math is right (zero_hits / total).
  * Percentile math is right on edge cases (single row, tie values).

Tests seed an in-memory-like SQLite file via tmp_path and point the
route at it via DB_PATH monkeypatch — same pattern used by the
regulatory-search tests.
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ──────────────────────────────────────────────────────────────
# DB fixture: builds a rag_queries table with controlled
# timestamps so we can assert window filtering.
# ──────────────────────────────────────────────────────────────

@pytest.fixture
def stats_db(tmp_path, monkeypatch):
    db = tmp_path / "argus_news.db"
    conn = sqlite3.connect(db)
    conn.execute("""
        CREATE TABLE rag_queries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            collection TEXT NOT NULL,
            query_text TEXT,
            n_results INTEGER,
            top_score REAL,
            latency_ms INTEGER,
            embedder_model TEXT,
            embedder_version TEXT,
            request_id TEXT
        )
    """)
    conn.commit()
    conn.close()

    import routes.rag_stats_routes as rr
    monkeypatch.setattr(rr, "DB_PATH", db)
    return db


def _insert(db: Path, *rows):
    """Each row is a tuple (hours_ago, endpoint, collection, n_results,
    latency_ms, query_text). Timestamps are derived from `hours_ago`."""
    conn = sqlite3.connect(db)
    for hours_ago, endpoint, collection, n_results, latency_ms, query in rows:
        ts = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
        conn.execute(
            "INSERT INTO rag_queries (timestamp, endpoint, collection, "
            "query_text, n_results, latency_ms, embedder_model, embedder_version) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (ts, endpoint, collection, query, n_results, latency_ms, "m1", "v1"),
        )
    conn.commit()
    conn.close()


@pytest.fixture
def client():
    """Tiny flask app with just the stats blueprint mounted."""
    from flask import Flask
    import routes.rag_stats_routes as rr
    app = Flask(__name__, template_folder=str(ROOT / "templates"))
    app.register_blueprint(rr.rag_stats_bp)
    return app.test_client()


# ──────────────────────────────────────────────────────────────
# JSON endpoint shape + filtering
# ──────────────────────────────────────────────────────────────

class TestStatsShape:
    def test_returns_usable_shape_when_table_missing(self, tmp_path, client, monkeypatch):
        """If rag_queries doesn't exist yet (no RAG call has ever logged
        telemetry), the endpoint must still return a valid 200 with the
        full shape — empty arrays + zero counters — so the admin page
        can render without branching."""
        empty_db = tmp_path / "empty.db"
        sqlite3.connect(empty_db).close()   # create file but no tables

        import routes.rag_stats_routes as rr
        monkeypatch.setattr(rr, "DB_PATH", empty_db)

        r = client.get("/api/admin/rag/stats?hours=24")
        assert r.status_code == 200
        d = r.json
        assert d["status"] == "success"
        assert d["total_queries"] == 0
        assert d["by_collection"] == []
        assert d["by_endpoint"] == []
        assert "p50" in d["latency_ms"]

    def test_returns_usable_shape_when_window_empty(self, stats_db, client):
        """Table exists but no rows in window — same empty shape,
        not a 500."""
        _insert(stats_db,
            (240, "api.chat", "news", 3, 50, "old query"),  # 10 days ago
        )
        r = client.get("/api/admin/rag/stats?hours=24")
        assert r.status_code == 200
        d = r.json
        assert d["total_queries"] == 0
        assert d["by_collection"] == []

    def test_aggregates_within_window_only(self, stats_db, client):
        _insert(stats_db,
            (1,   "api.chat", "news", 5, 100, "fresh"),
            (5,   "api.chat", "news", 3,  80, "fresh-ish"),
            (240, "api.chat", "news", 0, 999, "stale"),     # outside 24h
        )
        d = client.get("/api/admin/rag/stats?hours=24").json
        assert d["total_queries"] == 2          # stale row excluded
        assert d["zero_hits"] == 0
        assert d["hit_rate_pct"] == 100.0
        assert len(d["by_collection"]) == 1
        assert d["by_collection"][0]["collection"] == "news"
        assert d["by_collection"][0]["count"] == 2


class TestHitRateMath:
    def test_hit_rate_with_mixed_results(self, stats_db, client):
        """2 hits, 2 zero-hits → 50% hit rate, zero_hits=2."""
        _insert(stats_db,
            (1, "api.chat", "news", 5, 50, "q1"),
            (1, "api.chat", "news", 3, 60, "q2"),
            (1, "api.chat", "news", 0, 40, "q3"),
            (1, "api.chat", "news", 0, 45, "q4"),
        )
        d = client.get("/api/admin/rag/stats?hours=24").json
        assert d["total_queries"] == 4
        assert d["zero_hits"] == 2
        assert d["hit_rate_pct"] == 50.0

    def test_hit_rate_all_zero(self, stats_db, client):
        _insert(stats_db,
            (1, "api.chat", "news", 0, 50, "miss1"),
            (1, "api.chat", "news", 0, 60, "miss2"),
        )
        d = client.get("/api/admin/rag/stats?hours=24").json
        assert d["zero_hits"] == 2
        assert d["hit_rate_pct"] == 0.0


class TestLatencyPercentiles:
    def test_single_row_p50_equals_value(self, stats_db, client):
        _insert(stats_db, (1, "api.chat", "news", 1, 42, "solo"))
        d = client.get("/api/admin/rag/stats?hours=24").json
        # With a single observation, every percentile is that value.
        assert d["latency_ms"]["p50"] == 42
        assert d["latency_ms"]["p95"] == 42
        assert d["latency_ms"]["p99"] == 42

    def test_percentiles_on_sorted_distribution(self, stats_db, client):
        # 100 evenly-spaced latencies: 1ms, 2ms, ..., 100ms.
        # p50 should land near 50, p95 near 95.
        rows = [(1, "api.chat", "news", 1, ms, "q") for ms in range(1, 101)]
        _insert(stats_db, *rows)
        d = client.get("/api/admin/rag/stats?hours=24").json
        assert 45 <= d["latency_ms"]["p50"] <= 55
        assert 90 <= d["latency_ms"]["p95"] <= 100


class TestGroupBy:
    def test_breakdown_by_collection_and_endpoint(self, stats_db, client):
        _insert(stats_db,
            (1, "api.chat",       "news",       5, 100, "x"),
            (1, "api.chat",       "alerts",     3,  20, "x"),
            (1, "api.regulatory", "regulatory", 0, 200, "x"),
            (1, "api.regulatory", "regulatory", 2, 150, "x"),
        )
        d = client.get("/api/admin/rag/stats?hours=24").json

        # By collection: regulatory has 2 entries, others 1 each.
        by_col = {c["collection"]: c for c in d["by_collection"]}
        assert by_col["regulatory"]["count"] == 2
        assert by_col["regulatory"]["zero_hits"] == 1
        assert by_col["news"]["count"] == 1
        assert by_col["alerts"]["count"] == 1

        # By endpoint: api.regulatory grouped to 2.
        by_ep = {e["endpoint"]: e for e in d["by_endpoint"]}
        assert by_ep["api.regulatory"]["count"] == 2
        assert by_ep["api.chat"]["count"] == 2


class TestSlowestList:
    def test_top_10_sorted_descending(self, stats_db, client):
        rows = [(1, "api.x", "news", 1, ms, f"q{ms}") for ms in range(1, 21)]
        _insert(stats_db, *rows)
        d = client.get("/api/admin/rag/stats?hours=24").json
        slowest = d["slowest"]
        assert len(slowest) == 10
        # First row should be the largest latency (20).
        assert slowest[0]["latency_ms"] == 20
        # Strictly descending.
        ms_values = [r["latency_ms"] for r in slowest]
        assert ms_values == sorted(ms_values, reverse=True)


class TestInputValidation:
    def test_non_numeric_hours_returns_400(self, stats_db, client):
        r = client.get("/api/admin/rag/stats?hours=abc")
        assert r.status_code == 400

    def test_negative_hours_clamped_to_one(self, stats_db, client):
        """Out-of-band values should clamp, not 500. Treats `-5` as 1h."""
        r = client.get("/api/admin/rag/stats?hours=-5")
        assert r.status_code == 200
        assert r.json["window_hours"] == 1

    def test_huge_hours_clamped_to_30d(self, stats_db, client):
        r = client.get("/api/admin/rag/stats?hours=99999")
        assert r.status_code == 200
        # Clamp ceiling is 24 * 30 = 720h.
        assert r.json["window_hours"] == 720


# ──────────────────────────────────────────────────────────────
# /admin/rag — legacy URL preserved as a 302 to the new SPA tab
# ──────────────────────────────────────────────────────────────

class TestAdminRagRedirect:
    """Before the multi-tab Admin SPA, /admin/rag served a standalone
    RAG health page. That page is now the first tab of /admin; the
    route is kept as a redirect so existing bookmarks and the old
    sidebar link don't break."""

    def test_legacy_admin_rag_redirects_to_spa_tab(self, client):
        r = client.get("/admin/rag", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["Location"] == "/admin#rag"
