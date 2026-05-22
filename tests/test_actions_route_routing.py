"""
Integration tests for the Phase 3D routing in `/api/actions`.

What these pin:

  1. Feature flag (env `USE_ACTIONS_AGENT`) off → legacy `call_llm`
     path always. This is the rollback contract — flipping the env
     back to false must take effect for fresh requests.
  2. Flag on + agent returns valid JSON → agent path. Response
     carries `agent` block alongside the existing keys, AND the
     legacy `citations` / `rag` envelope is preserved.
  3. Agent crash, import failure, status='error', malformed JSON, or
     empty actions → automatic fallback to legacy. The UI must
     never see a 500 because the agent SDK misbehaved.
  4. Both paths return the same top-level envelope keys so the
     frontend doesn't care which one ran.

Run:
    pytest tests/test_actions_route_routing.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from flask import Flask

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from routes.actions_routes import _strip_json_fence, actions_bp


# Minimal pipeline that yields exactly one alert above min_score=40
# and outside the default exclude list. Used by every test below so
# `_collect_alerts` returns non-empty (otherwise the route short-
# circuits before hitting the agent / LLM at all).
_PIPELINE = {
    "country_groups": [
        {
            "country": "Mexico",
            "category_cards": [
                {
                    "category": "FX Risk",
                    "effective_score": 75,
                    "top_alert_title": "MXN drops 4% on central-bank surprise",
                }
            ],
        }
    ],
}

_LEGACY_LLM_JSON = json.dumps({
    "summary": "Legacy summary",
    "posture": "ELEVATED",
    "actions": [{
        "country":  "Mexico",
        "category": "FX Risk",
        "effective_score": 75,
        "top_title": "MXN drops 4% on central-bank surprise",
        "why_now": "MXN drops 4% on central-bank surprise — hedging window",
        "recommendation": "Hedge MXN exposure via 3M forwards",
        "recommendation_pros": ["Locks rate", "Auditable"],
        "recommendation_cons": ["Forfeits upside", "Counterparty risk"],
        "recommendation_outcome": "Caps MXN downside at 4%",
        "alternatives": [
            {"action": "Reduce MXN exposure",
             "pros": ["No counterparty"], "cons": ["Tax leakage"],
             "outcome": "Cuts exposure 20%"},
            {"action": "Increase MXN buffer",
             "pros": ["Operational simplicity"], "cons": ["Carry cost"],
             "outcome": "30d runway"},
        ],
        "alerts": [],
    }],
})

_AGENT_JSON = json.dumps({
    "summary": "Agent-derived summary",
    "posture": "CRITICAL",
    "actions": [{
        "country":  "Mexico",
        "category": "FX Risk",
        "effective_score": 75,
        "top_title": "MXN drops 4% on central-bank surprise",
        "why_now": "MXN drops 4% on central-bank surprise — multi-tool grounded",
        "recommendation": "Layered MXN hedge anchored on history H1",
        "recommendation_pros": ["Grounded in coherence=0.82", "Cited H1, H3"],
        "recommendation_cons": ["FX rate volatile", "3M tenor mismatch"],
        "recommendation_outcome": "MXN VaR cut 35% inside 30 days",
        "alternatives": [
            {"action": "Switch to USD-denominated AP terms",
             "pros": ["Removes FX entirely"], "cons": ["Supplier pushback"],
             "outcome": "Removes 100% MXN AP exposure"},
            {"action": "Spread hedge across CME + OTC",
             "pros": ["Liquidity diversification"], "cons": ["Op complexity"],
             "outcome": "Reduces single-venue tail risk"},
        ],
        "alerts": [],
    }],
})


def _agent_result(answer=_AGENT_JSON, status="ok"):
    """Build an ActionsResult-like object so the route can serialize
    it without booting the real Anthropic SDK."""
    return SimpleNamespace(
        status=status, answer=answer,
        turns_used=4, tokens_in=1200, tokens_out=600,
        cost_usd=0.187,
        tool_calls=[
            {"tool": "get_cash_positions", "ok": True, "latency_ms": 30},
            {"tool": "get_historical_actions", "ok": True, "latency_ms": 80},
            {"tool": "score_action_coherence", "ok": True, "latency_ms": 60},
        ],
        request_id="req_actions_test",
        message=None,
    )


def _rag_ctx_stub():
    """A build_actions_context return value — empty-text but with the
    attribute surface the route reads."""
    return SimpleNamespace(
        used=False,
        few_shot_text="", regulatory_text="",
        citations=[], history_hits=0, regulatory_hits=0,
    )


@pytest.fixture
def client():
    """Spin up a minimal Flask app with just the actions blueprint.
    Faster + more isolated than booting the full create_app()."""
    app = Flask(__name__)
    app.register_blueprint(actions_bp)
    return app.test_client()


@pytest.fixture(autouse=True)
def _stub_cache_io(monkeypatch, tmp_path):
    """Redirect _save_cache to a tmp file so the test suite doesn't
    overwrite the real shared_storage/latest_actions.json."""
    import routes.actions_routes as ar
    monkeypatch.setattr(ar, "ACTIONS_CACHE", tmp_path / "actions_cache.json")
    monkeypatch.setattr(ar, "_load_pipeline_actions", lambda: _PIPELINE)


# ──────────────────────────────────────────────────────────────────
# Feature flag — OFF
# ──────────────────────────────────────────────────────────────────

class TestFlagOffAlwaysLegacy:
    """When the env flag is off, generation MUST stay on the
    `call_llm` single-shot path. This is the rollback contract."""

    def test_legacy_when_flag_unset(self, client, monkeypatch):
        monkeypatch.delenv("USE_ACTIONS_AGENT", raising=False)
        with patch("routes.actions_routes.call_llm",
                   return_value=_LEGACY_LLM_JSON), \
             patch("routes.actions_routes.build_actions_context",
                   return_value=_rag_ctx_stub()), \
             patch("core.agents.actions_agent.run_actions") as run_actions_mock:
            r = client.post("/api/actions/generate", json={})

        assert r.status_code == 200
        body = r.get_json()
        assert body["summary"] == "Legacy summary"
        # Hard pin: the agent path was never invoked when flag is off.
        run_actions_mock.assert_not_called()
        # Legacy responses don't carry the `agent` block.
        assert "agent" not in body

    def test_legacy_when_flag_false(self, client, monkeypatch):
        """Explicit `false` value behaves the same as unset."""
        monkeypatch.setenv("USE_ACTIONS_AGENT", "false")
        with patch("routes.actions_routes.call_llm",
                   return_value=_LEGACY_LLM_JSON), \
             patch("routes.actions_routes.build_actions_context",
                   return_value=_rag_ctx_stub()), \
             patch("core.agents.actions_agent.run_actions") as run_actions_mock:
            r = client.post("/api/actions/generate", json={})

        body = r.get_json()
        assert body["summary"] == "Legacy summary"
        run_actions_mock.assert_not_called()


# ──────────────────────────────────────────────────────────────────
# Feature flag — ON
# ──────────────────────────────────────────────────────────────────

class TestFlagOnRoutesToAgent:
    def test_agent_path_used_when_flag_on(self, client, monkeypatch):
        monkeypatch.setenv("USE_ACTIONS_AGENT", "1")
        with patch("core.agents.actions_agent.run_actions",
                   return_value=_agent_result()), \
             patch("routes.actions_routes.call_llm") as legacy_call, \
             patch("routes.actions_routes.build_actions_context",
                   return_value=_rag_ctx_stub()):
            r = client.post("/api/actions/generate", json={})

        body = r.get_json()
        assert body["summary"] == "Agent-derived summary"
        assert body["posture"] == "CRITICAL"
        # Legacy LLM was NOT called in the happy path.
        legacy_call.assert_not_called()
        # Agent-only diagnostics surface for telemetry / admin UI.
        assert body["agent"]["used"] is True
        assert body["agent"]["turns_used"] == 4
        assert body["agent"]["cost_usd"] == 0.187
        assert {tc["tool"] for tc in body["agent"]["tool_calls"]} == {
            "get_cash_positions", "get_historical_actions",
            "score_action_coherence",
        }

    def test_agent_path_preserves_legacy_envelope(self, client, monkeypatch):
        """Adding the agent path must not drop `status` / `citations`
        / `rag` — the frontend reads those regardless of route."""
        monkeypatch.setenv("USE_ACTIONS_AGENT", "1")
        with patch("core.agents.actions_agent.run_actions",
                   return_value=_agent_result()), \
             patch("routes.actions_routes.build_actions_context",
                   return_value=_rag_ctx_stub()):
            r = client.post("/api/actions/generate", json={})

        body = r.get_json()
        for k in ("status", "generated_at", "persona",
                  "request_id", "citations", "rag", "actions"):
            assert k in body, f"agent response missing key: {k}"


# ──────────────────────────────────────────────────────────────────
# Graceful fallback when agent path breaks
# ──────────────────────────────────────────────────────────────────

class TestFallbackOnAgentFailure:
    """The /api/actions endpoint must never 500 because the agent
    misbehaved. Any agent-side failure falls through to the legacy
    `call_llm` path."""

    def test_agent_crash_falls_back(self, client, monkeypatch):
        monkeypatch.setenv("USE_ACTIONS_AGENT", "1")
        with patch("core.agents.actions_agent.run_actions",
                   side_effect=RuntimeError("boom")), \
             patch("routes.actions_routes.call_llm",
                   return_value=_LEGACY_LLM_JSON), \
             patch("routes.actions_routes.build_actions_context",
                   return_value=_rag_ctx_stub()):
            r = client.post("/api/actions/generate", json={})

        assert r.status_code == 200
        body = r.get_json()
        assert body["summary"] == "Legacy summary"
        assert "agent" not in body, (
            "fallback should not advertise agent.used; it didn't"
        )

    def test_agent_status_error_falls_back(self, client, monkeypatch):
        """Soft agent failure (status='error') is the most common
        rollout problem — upstream Anthropic 5xx, budget exceeded
        before any output. Must drop to legacy."""
        monkeypatch.setenv("USE_ACTIONS_AGENT", "1")
        with patch("core.agents.actions_agent.run_actions",
                   return_value=_agent_result(status="error", answer="")), \
             patch("routes.actions_routes.call_llm",
                   return_value=_LEGACY_LLM_JSON), \
             patch("routes.actions_routes.build_actions_context",
                   return_value=_rag_ctx_stub()):
            r = client.post("/api/actions/generate", json={})

        body = r.get_json()
        assert body["summary"] == "Legacy summary"

    def test_agent_malformed_json_falls_back(self, client, monkeypatch):
        """If the agent's `answer` isn't valid JSON, the route layer
        catches the parse failure and falls through. Generative drift
        from Opus is a real risk vector here."""
        monkeypatch.setenv("USE_ACTIONS_AGENT", "1")
        with patch("core.agents.actions_agent.run_actions",
                   return_value=_agent_result(
                       answer="Sorry, I couldn't comply.")), \
             patch("routes.actions_routes.call_llm",
                   return_value=_LEGACY_LLM_JSON), \
             patch("routes.actions_routes.build_actions_context",
                   return_value=_rag_ctx_stub()):
            r = client.post("/api/actions/generate", json={})

        body = r.get_json()
        assert body["summary"] == "Legacy summary"

    def test_agent_empty_actions_falls_back(self, client, monkeypatch):
        """An `actions: []` payload from the agent is treated as
        misfire — we have alerts to act on, so empty output is
        worse than the legacy LLM's best-effort guess."""
        monkeypatch.setenv("USE_ACTIONS_AGENT", "1")
        with patch("core.agents.actions_agent.run_actions",
                   return_value=_agent_result(
                       answer=json.dumps({"summary": "blank",
                                          "posture": "MODERATE",
                                          "actions": []}))), \
             patch("routes.actions_routes.call_llm",
                   return_value=_LEGACY_LLM_JSON), \
             patch("routes.actions_routes.build_actions_context",
                   return_value=_rag_ctx_stub()):
            r = client.post("/api/actions/generate", json={})

        body = r.get_json()
        assert body["summary"] == "Legacy summary"


# ──────────────────────────────────────────────────────────────────
# Fence-stripping (shared by legacy + agent paths)
# ──────────────────────────────────────────────────────────────────

class TestStripJsonFence:
    """`_strip_json_fence` runs on both paths. A bug here would silently
    corrupt provider responses across the whole endpoint — hence pinning
    every fence variant we've seen in the wild plus the scalar-JSON case
    that the original `lstrip("json")` implementation would have broken
    (`null` → `ull`)."""

    def test_unfenced_passthrough(self):
        assert _strip_json_fence('{"actions": []}') == '{"actions": []}'

    def test_unfenced_with_whitespace(self):
        assert _strip_json_fence('  {"a":1}\n') == '{"a":1}'

    def test_fenced_json_lang(self):
        raw = '```json\n{"actions": [1, 2]}\n```'
        assert _strip_json_fence(raw) == '{"actions": [1, 2]}'

    def test_fenced_no_lang(self):
        """A bare ``` opener (no language tag) is also common."""
        raw = '```\n{"actions": []}\n```'
        assert _strip_json_fence(raw) == '{"actions": []}'

    def test_fenced_scalar_null(self):
        """The original `.lstrip("json")` implementation would have
        corrupted this to `ull`. Pin the fix so it can't regress."""
        raw = '```json\nnull\n```'
        out = _strip_json_fence(raw)
        assert out == 'null'
        # json.loads round-trip — the actual contract callers rely on.
        assert json.loads(out) is None

    def test_fenced_unknown_lang(self):
        """Some providers emit ```javascript or ```js — same line-aware
        strip should still work."""
        raw = '```javascript\n{"actions": []}\n```'
        assert _strip_json_fence(raw) == '{"actions": []}'

    def test_empty_input(self):
        assert _strip_json_fence("") == ""
        assert _strip_json_fence(None) == ""


# ──────────────────────────────────────────────────────────────────
# request_id parity across legacy / agent paths
# ──────────────────────────────────────────────────────────────────

class TestRequestIdParity:
    """One request_id must thread through both the route's RAG
    context build AND the agent's internal telemetry, so the admin
    UI can join `rag_queries` rows (route side) with
    `agent_executions` / `llm_calls` (agent side) by request_id."""

    def test_route_passes_request_id_to_agent(self, client, monkeypatch):
        monkeypatch.setenv("USE_ACTIONS_AGENT", "1")
        captured = {}

        def _capture_run_actions(prompt, *, invoked_by, request_id=None):
            captured["request_id"] = request_id
            captured["invoked_by"] = invoked_by
            # Echo it back so the response envelope reflects the
            # threaded id, mirroring real `run_actions` behaviour.
            return SimpleNamespace(
                status="ok", answer=_AGENT_JSON,
                turns_used=1, tokens_in=10, tokens_out=10,
                cost_usd=0.01, tool_calls=[],
                request_id=request_id or "agent_minted",
                message=None,
            )

        with patch("core.agents.actions_agent.run_actions",
                   side_effect=_capture_run_actions), \
             patch("routes.actions_routes.build_actions_context",
                   return_value=_rag_ctx_stub()):
            r = client.post("/api/actions/generate", json={})

        body = r.get_json()
        assert captured["request_id"], (
            "route must pass a request_id to run_actions, got None"
        )
        assert captured["invoked_by"] == "api.actions"
        # Top-level request_id and the agent block's request_id must
        # match — that's the join key admin tooling will rely on.
        assert body["request_id"] == captured["request_id"]
        assert body["agent"]["request_id"] == captured["request_id"]
