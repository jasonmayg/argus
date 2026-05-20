"""
Integration tests for the Phase 3C routing in `/api/chat`.

What these pin:

  1. Feature flag (env `ARGUS_RESPONSE_AGENT_ENABLED`) off → legacy
     path always. This is the rollback contract — flipping the env
     back to false must take effect for fresh requests.
  2. Flag on + router says "simple" → still legacy.
  3. Flag on + router says "complex" → ResponseAgent.
  4. Both paths return the same JSON envelope keys so the frontend
     doesn't care which one ran.
  5. ResponseAgent crash / error status → automatic fallback to
     legacy (a chat UI must NEVER return 500 because the agent SDK
     misbehaved).

Run:
    pytest tests/test_chat_route_routing.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from flask import Flask

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from routes.ai_routes import ai_bp


@pytest.fixture
def client():
    """Spin up a minimal Flask app with just the ai_routes blueprint.
    Faster + more isolated than booting the full create_app()."""
    app = Flask(__name__)
    app.register_blueprint(ai_bp)
    return app.test_client()


# ──────────────────────────────────────────────────────────────────
# Shared stubs
# ──────────────────────────────────────────────────────────────────

def _stub_legacy_helpers(*, reply: str = "legacy reply"):
    """Return a chain of context managers to stub everything the
    legacy /api/chat path depends on. Returns a list of cm objects
    callers can enter via `with ExitStack()`.

    We stub at the module level inside `routes.ai_routes` so the
    real `call_llm` / RAG context / pipeline-results loaders never
    run."""
    return [
        patch("routes.ai_routes.call_llm", return_value=reply),
        patch("routes.ai_routes._load_latest_results", return_value=None),
        patch("routes.ai_routes.build_chat_context",
              return_value=SimpleNamespace(
                  used=False, prompt_text="",
                  citations=[], news_hits=0, alerts_hits=0,
                  regulatory_hits=0,
              )),
        patch("routes.ai_routes._news_summary_text", return_value=""),
    ]


def _agent_result(answer="agent reply", status="ok"):
    """Build a ResponseResult-like object so the chat route can
    serialize it without invoking the real agent."""
    return SimpleNamespace(
        status=status, query="q", answer=answer,
        turns_used=2, tokens_in=400, tokens_out=120,
        cost_usd=0.012,
        tool_calls=[{"tool": "search_news", "ok": True, "latency_ms": 80}],
        request_id="req_test123",
        message=None,
    )


# ──────────────────────────────────────────────────────────────────
# Feature flag — OFF
# ──────────────────────────────────────────────────────────────────

class TestFlagOffAlwaysLegacy:
    """When the env flag is off, complex queries STILL go legacy.
    This is the rollback contract."""

    def test_simple_query_legacy(self, client, monkeypatch):
        monkeypatch.delenv("ARGUS_RESPONSE_AGENT_ENABLED", raising=False)
        with _stub_legacy_helpers(reply="from legacy")[0] as _, \
             _stub_legacy_helpers()[1] as _, \
             _stub_legacy_helpers()[2] as _, \
             _stub_legacy_helpers()[3] as _:
            r = client.post("/api/chat", json={"message": "hi"})
        assert r.status_code == 200
        body = r.get_json()
        assert body["route"] == "legacy"

    def test_complex_query_still_legacy_when_flag_off(self, client, monkeypatch):
        monkeypatch.delenv("ARGUS_RESPONSE_AGENT_ENABLED", raising=False)
        with _stub_legacy_helpers()[0], \
             _stub_legacy_helpers()[1], \
             _stub_legacy_helpers()[2], \
             _stub_legacy_helpers()[3]:
            # Definitely-complex query — multi-entity, length, verb
            r = client.post("/api/chat", json={
                "message":
                    "Compare ECB and BoE recent rate decisions and what they "
                    "mean for EUR/USD positioning across the next quarter",
            })
        body = r.get_json()
        assert body["route"] == "legacy", (
            "complex query routed to agent despite flag being off"
        )


# ──────────────────────────────────────────────────────────────────
# Feature flag — ON
# ──────────────────────────────────────────────────────────────────

class TestFlagOnRouting:
    """With the env flag on, the router decides per-query."""

    def test_simple_query_still_legacy(self, client, monkeypatch):
        """Cost guard: even with the flag on, short factual queries
        MUST stay on legacy. Otherwise we burn agent budget on
        every chat message."""
        monkeypatch.setenv("ARGUS_RESPONSE_AGENT_ENABLED", "1")
        with _stub_legacy_helpers()[0], \
             _stub_legacy_helpers()[1], \
             _stub_legacy_helpers()[2], \
             _stub_legacy_helpers()[3]:
            r = client.post("/api/chat", json={"message": "What's USD/CNY?"})
        body = r.get_json()
        assert body["route"] == "legacy"

    def test_complex_query_routes_to_agent(self, client, monkeypatch):
        monkeypatch.setenv("ARGUS_RESPONSE_AGENT_ENABLED", "1")
        with patch("core.agents.response_agent.run_response",
                   return_value=_agent_result(answer="rich agent answer")):
            r = client.post("/api/chat", json={
                "message":
                    "Compare ECB and BoE recent rate decisions and what they "
                    "mean for EUR/USD positioning across the next quarter",
            })
        body = r.get_json()
        assert body["route"] == "agent"
        assert body["reply"] == "rich agent answer"
        # Agent-only fields surface — frontend can use them for debug.
        assert body["agent"]["turns_used"] == 2
        assert body["agent"]["tool_calls"][0]["tool"] == "search_news"
        # Router diagnostics for visibility into why this escalated.
        assert body["agent"]["router_score"] >= 2
        assert any("verb:compare" in r for r in body["agent"]["router_reasons"])


# ──────────────────────────────────────────────────────────────────
# Shape parity — both paths return the same envelope keys
# ──────────────────────────────────────────────────────────────────

class TestEnvelopeShapeParity:
    """The frontend reads `status`, `reply`, `request_id`,
    `citations`, `rag` regardless of route. Adding the agent path
    must not drop these keys (else the frontend crashes silently
    when route=agent)."""

    _MUST_HAVE_KEYS = {"status", "reply", "request_id", "citations", "rag", "route"}

    def test_legacy_has_required_keys(self, client, monkeypatch):
        monkeypatch.delenv("ARGUS_RESPONSE_AGENT_ENABLED", raising=False)
        with _stub_legacy_helpers()[0], \
             _stub_legacy_helpers()[1], \
             _stub_legacy_helpers()[2], \
             _stub_legacy_helpers()[3]:
            r = client.post("/api/chat", json={"message": "hi"})
        body = r.get_json()
        for k in self._MUST_HAVE_KEYS:
            assert k in body, f"legacy response missing key: {k}"

    def test_agent_has_required_keys(self, client, monkeypatch):
        monkeypatch.setenv("ARGUS_RESPONSE_AGENT_ENABLED", "1")
        with patch("core.agents.response_agent.run_response",
                   return_value=_agent_result()):
            r = client.post("/api/chat", json={
                "message":
                    "Compare ECB and BoE rate decisions across UK and EU "
                    "over the past month and what it means for hedging",
            })
        body = r.get_json()
        for k in self._MUST_HAVE_KEYS:
            assert k in body, f"agent response missing key: {k}"


# ──────────────────────────────────────────────────────────────────
# Graceful fallback when agent path breaks
# ──────────────────────────────────────────────────────────────────

class TestFallbackOnAgentFailure:
    """The chat UI must never see a 500 because an agent SDK
    misbehaved. Failures fall through to legacy."""

    def test_agent_import_failure_falls_back(self, client, monkeypatch):
        """If `from core.agents.response_agent import run_response`
        raises (e.g. anthropic SDK not installed in this env), the
        route must drop back to legacy."""
        monkeypatch.setenv("ARGUS_RESPONSE_AGENT_ENABLED", "1")
        with patch("core.agents.response_agent.run_response",
                   side_effect=ImportError("anthropic missing")):
            with _stub_legacy_helpers()[0], \
                 _stub_legacy_helpers()[1], \
                 _stub_legacy_helpers()[2], \
                 _stub_legacy_helpers()[3]:
                r = client.post("/api/chat", json={
                    "message": "Compare ECB and BoE rate decisions across "
                               "UK and EU over the past month and hedging",
                })
        body = r.get_json()
        assert r.status_code == 200
        assert body["route"] == "legacy"

    def test_agent_runtime_crash_falls_back(self, client, monkeypatch):
        monkeypatch.setenv("ARGUS_RESPONSE_AGENT_ENABLED", "1")
        with patch("core.agents.response_agent.run_response",
                   side_effect=RuntimeError("boom")):
            with _stub_legacy_helpers()[0], \
                 _stub_legacy_helpers()[1], \
                 _stub_legacy_helpers()[2], \
                 _stub_legacy_helpers()[3]:
                r = client.post("/api/chat", json={
                    "message": "Compare ECB and BoE rate decisions across "
                               "UK and EU over the past month and hedging",
                })
        body = r.get_json()
        assert r.status_code == 200
        assert body["route"] == "legacy"

    def test_agent_status_error_falls_back(self, client, monkeypatch):
        """ResponseAgent itself returns status='error' on upstream
        Anthropic failures — that's a soft failure shape, route
        layer translates it to legacy fallback rather than passing
        the error to the chat UI."""
        monkeypatch.setenv("ARGUS_RESPONSE_AGENT_ENABLED", "1")
        with patch("core.agents.response_agent.run_response",
                   return_value=_agent_result(
                       status="error", answer="")), \
             patch("routes.ai_routes.call_llm", return_value="legacy fallback ok"), \
             patch("routes.ai_routes._load_latest_results", return_value=None), \
             patch("routes.ai_routes.build_chat_context",
                   return_value=SimpleNamespace(
                       used=False, prompt_text="", citations=[],
                       news_hits=0, alerts_hits=0, regulatory_hits=0,
                   )), \
             patch("routes.ai_routes._news_summary_text", return_value=""):
            r = client.post("/api/chat", json={
                "message": "Compare ECB and BoE rate decisions across "
                           "UK and EU over the past month and hedging",
            })
        body = r.get_json()
        assert body["route"] == "legacy"
        assert body["reply"] == "legacy fallback ok"
