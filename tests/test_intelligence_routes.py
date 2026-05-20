"""
Unit tests for the `/api/intelligence/query` Flask route.

The agent itself is unit-tested elsewhere — this file pins the HTTP
contract: input validation, status → HTTP-code mapping, payload shape.

Run:
    pytest tests/test_intelligence_routes.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from flask import Flask

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from routes.intelligence_routes import intelligence_bp


@pytest.fixture
def client():
    """Minimal Flask app with only the blueprint under test. Faster
    + more isolated than spinning up the full create_app()."""
    app = Flask(__name__)
    app.register_blueprint(intelligence_bp)
    return app.test_client()


def _ok_result(**overrides):
    """Build an IntelligenceResult-like SimpleNamespace for stubbing."""
    from types import SimpleNamespace
    defaults = dict(
        status="ok", query="x", answer="hello", request_id="r1",
        turns_used=2, tokens_in=100, tokens_out=50, cost_usd=0.001,
        tool_calls=[{"tool": "search_news", "ok": True, "latency_ms": 10}],
        message=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestValidation:
    def test_non_json_returns_400(self, client):
        r = client.post("/api/intelligence/query", data="not json")
        assert r.status_code == 400

    def test_malformed_json_with_json_content_type_returns_400(self, client):
        """Regression: a JSON content-type with a broken body used to
        let Flask's `request.json` raise BadRequest and bypass our
        JSON-payload error path. With `get_json(silent=True)` we
        consistently return the same 400 JSON shape."""
        r = client.post(
            "/api/intelligence/query",
            data="{not valid json,,,",
            content_type="application/json",
        )
        assert r.status_code == 400
        body = r.get_json()
        assert body["status"] == "error"
        assert "JSON" in body["message"]

    def test_json_array_body_returns_400(self, client):
        """A JSON array is valid JSON but not an object — must be
        rejected before we try `body.get("query")`."""
        r = client.post("/api/intelligence/query", json=["query"])
        assert r.status_code == 400
        assert "object" in r.get_json()["message"]

    def test_empty_query_returns_400(self, client):
        r = client.post("/api/intelligence/query", json={"query": ""})
        assert r.status_code == 400
        assert "at least" in r.get_json()["message"]

    def test_too_long_query_returns_400(self, client):
        r = client.post("/api/intelligence/query", json={"query": "x" * 3000})
        assert r.status_code == 400
        assert "exceeds" in r.get_json()["message"]


class TestStatusMapping:
    def test_ok_status_returns_200(self, client):
        with patch("core.agents.intelligence_agent.run_intelligence",
                   return_value=_ok_result()):
            r = client.post("/api/intelligence/query", json={"query": "hello world"})
        assert r.status_code == 200
        body = r.get_json()
        assert body["status"] == "ok"
        assert body["answer"] == "hello"
        assert body["tool_calls"][0]["tool"] == "search_news"

    def test_budget_exceeded_still_200_with_flag(self, client):
        """Soft outcomes — budget breach / max_turns — surface the
        partial answer with status visible so the frontend can render
        a warning alongside it. Hard 5xx would lose the partial work."""
        with patch("core.agents.intelligence_agent.run_intelligence",
                   return_value=_ok_result(
                       status="budget_exceeded",
                       answer="partial answer so far",
                       message="cost budget exceeded",
                   )):
            r = client.post("/api/intelligence/query", json={"query": "hello world"})
        assert r.status_code == 200
        body = r.get_json()
        assert body["status"] == "budget_exceeded"
        assert body["answer"].startswith("partial")
        assert "budget" in body["message"]

    def test_max_turns_still_200(self, client):
        with patch("core.agents.intelligence_agent.run_intelligence",
                   return_value=_ok_result(
                       status="max_turns",
                       message="max_turns (10) reached before end_turn",
                   )):
            r = client.post("/api/intelligence/query", json={"query": "hello world"})
        assert r.status_code == 200
        assert r.get_json()["status"] == "max_turns"

    def test_upstream_error_returns_502(self, client):
        """When the runner reports an error whose message looks like
        an Anthropic transport / rate-limit failure, the route surfaces
        a 502 (bad gateway) — distinguishable from our internal 500s
        in monitoring."""
        with patch("core.agents.intelligence_agent.run_intelligence",
                   return_value=_ok_result(
                       status="error",
                       message="anthropic 429 rate limited",
                   )):
            r = client.post("/api/intelligence/query", json={"query": "hello world"})
        assert r.status_code == 502

    def test_internal_error_returns_500(self, client):
        with patch("core.agents.intelligence_agent.run_intelligence",
                   return_value=_ok_result(
                       status="error",
                       message="ValueError: bad config",
                   )):
            r = client.post("/api/intelligence/query", json={"query": "hello world"})
        assert r.status_code == 500

    def test_crash_in_runner_returns_500(self, client):
        """Defence in depth: even if `run_intelligence` raises rather
        than returning status=error, the route catches it."""
        with patch("core.agents.intelligence_agent.run_intelligence",
                   side_effect=RuntimeError("kaboom")):
            r = client.post("/api/intelligence/query", json={"query": "hello world"})
        assert r.status_code == 500
        assert "RuntimeError" in r.get_json()["message"]
