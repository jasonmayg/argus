"""
Unit tests for `routes/admin_routes.py` — `/api/admin/agents` +
`/api/admin/agents/<name>/recent`.

These pin the JSON shape, status-code contract, query-param clamping,
and graceful behavior when the telemetry db doesn't exist yet (the
zero-runs case). All tests use a temp sqlite db seeded with synthetic
agent_executions rows — no real telemetry, no real LLM calls.

Run:
    pytest tests/test_admin_routes.py -v
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from flask import Flask

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from routes import admin_routes
from routes.admin_routes import admin_bp


def _now_iso(offset_hours: int = 0) -> str:
    return (datetime.now(timezone.utc) +
            timedelta(hours=offset_hours)).isoformat()


def _seed_db(path: Path, rows: list[dict]) -> None:
    """Create a fresh telemetry sqlite db at `path` with the
    expected schemas + the given agent_executions rows."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS agent_executions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_name      TEXT    NOT NULL,
            invoked_by      TEXT    NOT NULL,
            request_id      TEXT    NOT NULL,
            started_at      TEXT    NOT NULL,
            finished_at     TEXT,
            status          TEXT    NOT NULL,
            turns_used      INTEGER DEFAULT 0,
            tokens_in       INTEGER DEFAULT 0,
            tokens_out      INTEGER DEFAULT 0,
            cost_usd        REAL    DEFAULT 0,
            tool_calls_json TEXT,
            output_preview  TEXT,
            output_full_path TEXT,
            error_class     TEXT,
            error_message   TEXT
        );
        CREATE TABLE IF NOT EXISTS llm_calls (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT NOT NULL,
            task        TEXT NOT NULL,
            provider    TEXT NOT NULL,
            model       TEXT NOT NULL,
            status      TEXT NOT NULL,
            latency_ms  INTEGER,
            tokens_in   INTEGER DEFAULT 0,
            tokens_out  INTEGER DEFAULT 0,
            error_short TEXT,
            fallback_index INTEGER DEFAULT 0,
            request_id  TEXT
        );
        CREATE TABLE IF NOT EXISTS rag_queries (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp       TEXT NOT NULL,
            endpoint        TEXT NOT NULL,
            collection      TEXT NOT NULL,
            query_text      TEXT,
            n_results       INTEGER,
            top_score       REAL,
            latency_ms      INTEGER,
            embedder_model  TEXT,
            embedder_version TEXT,
            request_id      TEXT
        );
    """)
    for r in rows:
        conn.execute("""
            INSERT INTO agent_executions
              (agent_name, invoked_by, request_id, started_at, finished_at,
               status, turns_used, tokens_in, tokens_out, cost_usd,
               tool_calls_json, output_preview, error_class, error_message)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            r.get("agent_name", "intelligence"),
            r.get("invoked_by", "api"),
            r["request_id"],
            r.get("started_at", _now_iso()),
            r.get("finished_at"),
            r.get("status", "ok"),
            r.get("turns_used", 1),
            r.get("tokens_in", 100),
            r.get("tokens_out", 50),
            r.get("cost_usd", 0.001),
            r.get("tool_calls_json"),
            r.get("output_preview"),
            r.get("error_class"),
            r.get("error_message"),
        ))
    conn.commit()
    conn.close()


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Flask test client with admin_bp registered + DB redirected
    to a temp file each test gets a clean db."""
    db = tmp_path / "telemetry.db"
    monkeypatch.setattr(admin_routes, "_DB_PATH", db)

    app = Flask(__name__)
    app.register_blueprint(admin_bp)
    client = app.test_client()
    client._db_path = db   # tests can read this to seed
    return client


# ──────────────────────────────────────────────────────────────────
# /api/admin/agents — summary
# ──────────────────────────────────────────────────────────────────

class TestAgentsSummary:
    def test_returns_empty_when_db_missing(self, client):
        """Pre-first-run state: telemetry db doesn't exist yet.
        Endpoint must succeed with empty `agents` rather than 500."""
        # client._db_path was never created — db absent.
        r = client.get("/api/admin/agents")
        assert r.status_code == 200
        body = r.get_json()
        assert body["agents"] == {}
        assert "telemetry db not present" in body.get("message", "")

    def test_aggregates_per_agent(self, client):
        _seed_db(client._db_path, [
            {"agent_name": "intelligence", "request_id": "r1",
             "status": "ok", "turns_used": 2, "tokens_in": 1000,
             "tokens_out": 200, "cost_usd": 0.005},
            {"agent_name": "intelligence", "request_id": "r2",
             "status": "ok", "turns_used": 3, "tokens_in": 1200,
             "tokens_out": 300, "cost_usd": 0.008},
            {"agent_name": "intelligence", "request_id": "r3",
             "status": "error", "turns_used": 1, "tokens_in": 100,
             "tokens_out": 0, "cost_usd": 0.0001,
             "error_class": "TimeoutError"},
            {"agent_name": "response", "request_id": "r4",
             "status": "ok", "turns_used": 2, "tokens_in": 800,
             "tokens_out": 150, "cost_usd": 0.003},
        ])
        r = client.get("/api/admin/agents?hours=24")
        assert r.status_code == 200
        body = r.get_json()
        intel = body["agents"]["intelligence"]
        assert intel["total_runs"] == 3
        assert intel["success"] == 2
        assert intel["errors"] == 1
        assert intel["success_rate"] == pytest.approx(0.667, abs=0.01)
        assert intel["status_counts"] == {"ok": 2, "error": 1}
        assert intel["tokens_in_total"]  == 2300
        assert intel["tokens_out_total"] == 500
        assert intel["cost_usd_total"]   == pytest.approx(0.0131, abs=1e-4)
        # Error class breakdown
        assert intel["top_error_classes"][0] == {"class": "TimeoutError",
                                                  "count": 1}
        # Other agents present too
        assert body["agents"]["response"]["total_runs"] == 1

    def test_window_clamps_hours_param(self, client):
        """?hours=99999 must be clamped to the 168-hour ceiling so
        a misconfigured monitor can't run a 10-year query."""
        _seed_db(client._db_path, [
            {"agent_name": "intelligence", "request_id": "r1"}
        ])
        r = client.get("/api/admin/agents?hours=99999")
        assert r.status_code == 200
        # Body reports the clamped value, not the requested one.
        assert r.get_json()["window_hours"] == 168

    def test_window_handles_invalid_hours(self, client):
        """Non-int `hours` falls back to default 24, doesn't 500."""
        _seed_db(client._db_path, [
            {"agent_name": "intelligence", "request_id": "r1"}
        ])
        r = client.get("/api/admin/agents?hours=garbage")
        assert r.status_code == 200
        assert r.get_json()["window_hours"] == 24

    def test_excludes_runs_outside_window(self, client):
        """A run from 30h ago when the window is 24h must NOT appear
        in the aggregates."""
        _seed_db(client._db_path, [
            {"agent_name": "intelligence", "request_id": "r_old",
             "started_at": _now_iso(-30)},
            {"agent_name": "intelligence", "request_id": "r_new",
             "started_at": _now_iso(-1)},
        ])
        r = client.get("/api/admin/agents?hours=24")
        body = r.get_json()
        assert body["agents"]["intelligence"]["total_runs"] == 1


# ──────────────────────────────────────────────────────────────────
# /api/admin/agents/<name>/recent
# ──────────────────────────────────────────────────────────────────

class TestAgentRecentRuns:
    def test_empty_when_no_runs(self, client):
        _seed_db(client._db_path, [])
        r = client.get("/api/admin/agents/intelligence/recent")
        assert r.status_code == 200
        body = r.get_json()
        assert body["agent"] == "intelligence"
        assert body["runs"] == []

    def test_returns_most_recent_first(self, client):
        _seed_db(client._db_path, [
            {"agent_name": "intelligence", "request_id": "r_a",
             "started_at": _now_iso(-5)},
            {"agent_name": "intelligence", "request_id": "r_b",
             "started_at": _now_iso(-1)},
            {"agent_name": "intelligence", "request_id": "r_c",
             "started_at": _now_iso(-3)},
        ])
        r = client.get("/api/admin/agents/intelligence/recent")
        runs = r.get_json()["runs"]
        # Most recent (r_b at -1h) must be first.
        assert [run["request_id"] for run in runs] == ["r_b", "r_c", "r_a"]

    def test_filter_by_agent_name(self, client):
        _seed_db(client._db_path, [
            {"agent_name": "intelligence", "request_id": "ri"},
            {"agent_name": "response",     "request_id": "rr"},
        ])
        r = client.get("/api/admin/agents/intelligence/recent")
        ids = [run["request_id"] for run in r.get_json()["runs"]]
        assert ids == ["ri"]   # response run filtered out

    def test_limit_clamped(self, client):
        rows = [{"agent_name": "intelligence", "request_id": f"r{i}"}
                for i in range(50)]
        _seed_db(client._db_path, rows)
        # Requested limit 9999 must clamp to _MAX_RECENT (200).
        r = client.get("/api/admin/agents/intelligence/recent?limit=9999")
        body = r.get_json()
        assert body["limit"] == 200
        # We seeded 50, so we get all 50.
        assert len(body["runs"]) == 50

    def test_joins_llm_and_rag_counts(self, client):
        """Per-run `llm_calls_count` and `rag_queries_count` must
        reflect rows in those tables joined by request_id. This is
        the field downstream debugging uses to spot "agent ran but
        no tools were called" anomalies."""
        _seed_db(client._db_path, [
            {"agent_name": "intelligence", "request_id": "r1"},
        ])
        # Manually seed 3 llm_calls + 2 rag_queries linked to r1.
        conn = sqlite3.connect(str(client._db_path))
        for _ in range(3):
            conn.execute("""INSERT INTO llm_calls (timestamp, task,
                provider, model, status, request_id)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (_now_iso(), "agent.intelligence", "anthropic",
                 "claude", "ok", "r1"))
        for _ in range(2):
            conn.execute("""INSERT INTO rag_queries (timestamp,
                endpoint, collection, request_id)
                VALUES (?, ?, ?, ?)""",
                (_now_iso(), "agent.intelligence.search_news",
                 "news", "r1"))
        conn.commit()
        conn.close()

        r = client.get("/api/admin/agents/intelligence/recent")
        run = r.get_json()["runs"][0]
        assert run["llm_calls_count"]   == 3
        assert run["rag_queries_count"] == 2


# ──────────────────────────────────────────────────────────────────
# Connection failure modes — distinguish missing vs unreadable
# ──────────────────────────────────────────────────────────────────

class TestConnectFailureModes:
    """Copilot PR #58 review: the original `_connect()` returned
    `None` on both 'file missing' AND 'open failed', so a corrupt /
    permissions-denied db looked the same as a clean pre-first-run.
    These tests pin the distinction:
      * missing → 200 + empty body + "not present" message
      * open failure → 500 + error class in the message
    """

    def test_missing_file_returns_200_empty(self, client):
        # client fixture creates a tmp path but never seeds it.
        assert not client._db_path.exists()
        r = client.get("/api/admin/agents")
        assert r.status_code == 200
        body = r.get_json()
        assert body["agents"] == {}
        assert "not present" in body.get("message", "")

    def test_unreadable_db_returns_500(self, client, monkeypatch):
        """Simulate a file that exists but can't be opened (corrupt
        / permissions). The endpoint must surface 500, not 200,
        because monitoring needs to escalate this — it's a real
        operational failure, not 'no data yet'."""
        # Make the file exist so _connect() proceeds past the
        # missing-file check, but stub sqlite3.connect to raise.
        client._db_path.write_text("not a real sqlite db")
        import sqlite3 as _sqlite3
        def _raise(*_a, **_kw):
            raise _sqlite3.DatabaseError("file is not a database")
        monkeypatch.setattr(_sqlite3, "connect", _raise)
        r = client.get("/api/admin/agents")
        assert r.status_code == 500
        body = r.get_json()
        # The handler logs + returns the error class so ops can
        # diagnose without reading the gunicorn log.
        assert "DatabaseError" in body.get("message", "")


# ──────────────────────────────────────────────────────────────────
# Optional admin-token guard
# ──────────────────────────────────────────────────────────────────

class TestAdminTokenGuard:
    """Defence-in-depth shim from Copilot review: when
    ARGUS_ADMIN_TOKEN env is set, the admin surface requires a
    matching X-Admin-Token header. Unset = no auth (preserves the
    local-dev / private-network default)."""

    def test_no_env_means_no_auth_required(self, client, monkeypatch):
        monkeypatch.delenv("ARGUS_ADMIN_TOKEN", raising=False)
        r = client.get("/api/admin/agents")
        assert r.status_code == 200    # no token needed

    def test_env_set_blocks_missing_header(self, client, monkeypatch):
        monkeypatch.setenv("ARGUS_ADMIN_TOKEN", "s3cret")
        r = client.get("/api/admin/agents")
        assert r.status_code == 401
        assert "admin token" in r.get_json().get("message", "").lower()

    def test_env_set_blocks_wrong_header(self, client, monkeypatch):
        monkeypatch.setenv("ARGUS_ADMIN_TOKEN", "s3cret")
        r = client.get("/api/admin/agents",
                        headers={"X-Admin-Token": "guess"})
        assert r.status_code == 401

    def test_correct_header_allowed(self, client, monkeypatch):
        monkeypatch.setenv("ARGUS_ADMIN_TOKEN", "s3cret")
        r = client.get("/api/admin/agents",
                        headers={"X-Admin-Token": "s3cret"})
        assert r.status_code == 200

    def test_guard_also_on_recent_endpoint(self, client, monkeypatch):
        """The recent-runs endpoint exposes the same sensitive fields
        — tool_calls_json, error_message, output_preview — so the
        guard must protect it too."""
        monkeypatch.setenv("ARGUS_ADMIN_TOKEN", "s3cret")
        r = client.get("/api/admin/agents/intelligence/recent")
        assert r.status_code == 401


# ──────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────

class TestPercentileHelper:
    """Pin the percentile rounding behavior so future refactors
    don't shift the p95 number that monitoring scrapes."""

    def test_empty_returns_zero(self):
        from routes.admin_routes import _percentile
        assert _percentile([], 0.5) == 0

    def test_single_sample(self):
        from routes.admin_routes import _percentile
        assert _percentile([7], 0.5)  == 7
        assert _percentile([7], 0.95) == 7

    def test_typical_distribution(self):
        from routes.admin_routes import _percentile
        samples = sorted([1, 2, 2, 3, 3, 4, 5, 5, 6, 8, 10])
        assert _percentile(samples, 0.5)  in (3, 4)   # nearest-rank tolerance
        assert _percentile(samples, 0.95) in (8, 10)


# ──────────────────────────────────────────────────────────────────
# GET /admin — multi-tab Admin SPA page
# ──────────────────────────────────────────────────────────────────

class TestAdminPage:
    """The /admin route just renders a static template. This test
    pins it as a regression guard so future blueprint / template-folder
    config changes don't silently 500 the operator UI."""

    def test_admin_page_returns_spa_shell(self, tmp_path, monkeypatch):
        # The shared `client` fixture builds a Flask without a
        # template_folder, which works for the JSON endpoints but not
        # the new HTML route. Build a fresh app here with templates
        # wired up to the real directory.
        monkeypatch.setattr(admin_routes, "_DB_PATH", tmp_path / "telemetry.db")
        app = Flask(__name__, template_folder=str(ROOT / "templates"))
        app.register_blueprint(admin_bp)
        client = app.test_client()

        r = client.get("/admin")
        assert r.status_code == 200
        # Smoke-check the template actually rendered (rather than the
        # route 500-falling-back to a Flask error page). These ids are
        # stable contract for the SPA shell — tab routing depends on
        # them.
        body = r.data.decode("utf-8")
        assert "panel-rag"      in body
        assert "panel-agents"   in body
        assert "panel-llm"      in body
        assert "panel-pipeline" in body


# ──────────────────────────────────────────────────────────────────
# /api/admin/llm/stats — LLM Usage tab data
# ──────────────────────────────────────────────────────────────────

def _seed_llm_calls(path: Path, rows: list[dict]) -> None:
    """Insert llm_calls rows. Schema is already created by _seed_db
    (which makes all three tables), so this just appends rows."""
    conn = sqlite3.connect(str(path))
    for r in rows:
        conn.execute("""
            INSERT INTO llm_calls
              (timestamp, task, provider, model, status, latency_ms,
               tokens_in, tokens_out, error_short, fallback_index, request_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            r.get("timestamp",   _now_iso()),
            r.get("task",        "guardian_chat"),
            r.get("provider",    "anthropic_sonnet"),
            r.get("model",       "claude-sonnet-4-6"),
            r.get("status",      "ok"),
            r.get("latency_ms",  100),
            r.get("tokens_in",   1000),
            r.get("tokens_out",  200),
            r.get("error_short", None),
            r.get("fallback_index", 0),
            r.get("request_id",  "r0"),
        ))
    conn.commit()
    conn.close()


class TestLlmStats:
    """`/api/admin/llm/stats` — the data backing the Admin SPA's LLM
    Usage tab. Pins the JSON shape, per-task / per-provider
    aggregation, cost math against the shared `PROVIDER_PRICING`
    table, and the no-data / no-table fallbacks."""

    def test_returns_zero_totals_when_db_missing(self, client):
        """Pre-first-run state: db file doesn't exist. Endpoint
        returns the zero-shape, not a 500."""
        r = client.get("/api/admin/llm/stats")
        assert r.status_code == 200
        d = r.get_json()
        assert d["totals"]["calls"] == 0
        assert d["by_task"] == []
        assert d["by_provider"] == []
        assert "telemetry db not present" in d.get("message", "")

    def test_returns_zero_totals_when_table_missing(self, client):
        """db exists (other code created it) but llm_calls table
        hasn't been written to yet. Same zero-shape, not a 500."""
        _seed_db(client._db_path, [])
        # _seed_db creates the llm_calls table — drop it to simulate
        # a pre-PR-0 db that predates llm telemetry.
        conn = sqlite3.connect(str(client._db_path))
        conn.execute("DROP TABLE llm_calls")
        conn.commit()
        conn.close()

        r = client.get("/api/admin/llm/stats")
        assert r.status_code == 200
        d = r.get_json()
        assert d["totals"]["calls"] == 0
        assert d.get("message") == "llm_calls table not yet created"

    def test_aggregates_by_task_and_provider(self, client):
        _seed_db(client._db_path, [])  # creates schemas
        _seed_llm_calls(client._db_path, [
            {"task": "guardian_chat",    "provider": "anthropic_sonnet",
             "tokens_in": 1000, "tokens_out": 500, "latency_ms": 200},
            {"task": "guardian_chat",    "provider": "anthropic_sonnet",
             "tokens_in": 2000, "tokens_out": 800, "latency_ms": 300},
            {"task": "regulatory_classify", "provider": "openai_4o_mini",
             "tokens_in": 500,  "tokens_out": 100, "latency_ms": 150},
            {"task": "regulatory_classify", "provider": "openai_4o_mini",
             "status": "failed", "error_short": "timeout",
             "tokens_in": 0,    "tokens_out": 0,   "latency_ms": 30000},
        ])

        r = client.get("/api/admin/llm/stats?hours=24")
        assert r.status_code == 200
        d = r.get_json()

        # Totals
        assert d["totals"]["calls"] == 4
        assert d["totals"]["ok"]    == 3
        assert d["totals"]["fail"]  == 1
        assert d["totals"]["success_rate"] == 0.75

        # By task — sorted by calls desc, so guardian_chat (2) first
        tasks = {t["task"]: t for t in d["by_task"]}
        assert tasks["guardian_chat"]["calls"]      == 2
        assert tasks["regulatory_classify"]["calls"] == 2
        assert tasks["regulatory_classify"]["fail"]  == 1

        # By provider
        providers = {p["provider"]: p for p in d["by_provider"]}
        assert providers["anthropic_sonnet"]["calls"]   == 2
        assert providers["openai_4o_mini"]["calls"]     == 2

    def test_top_failures_buckets_by_task_provider_error(self, client):
        _seed_db(client._db_path, [])
        _seed_llm_calls(client._db_path, [
            {"task": "t1", "provider": "p1", "status": "failed",
             "error_short": "rate limit"},
            {"task": "t1", "provider": "p1", "status": "failed",
             "error_short": "rate limit"},
            {"task": "t1", "provider": "p1", "status": "failed",
             "error_short": "different err"},
        ])
        d = client.get("/api/admin/llm/stats?hours=24").get_json()
        # Same (task, provider, error) collapse into one bucket with count
        rate_limit_bucket = [
            f for f in d["top_failures"]
            if f["task"] == "t1" and f["error"] == "rate limit"
        ]
        assert rate_limit_bucket and rate_limit_bucket[0]["count"] == 2

    def test_window_excludes_old_calls(self, client):
        """24h window must skip rows from 100h ago."""
        _seed_db(client._db_path, [])
        _seed_llm_calls(client._db_path, [
            {"timestamp": _now_iso(0),    "task": "fresh"},
            {"timestamp": _now_iso(-100), "task": "stale"},
        ])
        d = client.get("/api/admin/llm/stats?hours=24").get_json()
        tasks = {t["task"] for t in d["by_task"]}
        assert tasks == {"fresh"}, f"window leak: {tasks}"

    def test_cost_uses_pricing_table(self, client):
        """Spot-check cost math against PROVIDER_PRICING from the
        CLI script. anthropic_sonnet is $3/M in + $15/M out per the
        script; 1M in + 1M out = $18."""
        _seed_db(client._db_path, [])
        _seed_llm_calls(client._db_path, [
            {"task": "x", "provider": "anthropic_sonnet",
             "tokens_in": 1_000_000, "tokens_out": 1_000_000},
        ])
        d = client.get("/api/admin/llm/stats?hours=24").get_json()
        assert d["totals"]["cost_usd"] == pytest.approx(18.00, abs=0.01)
