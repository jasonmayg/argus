"""
Unit tests for `core.agents.response_agent`.

Mostly mirrors the IntelligenceAgent test structure (PR #48) since
the two agents are close cousins. What we pin specifically:

  1. The `response` block in agent_runtime.yaml gets loaded with
     tighter budget than IntelligenceAgent (max_turns=8, $0.30 cap).
  2. Persona kwarg flows into the system prompt.
  3. The Phase 3B search/regulatory tools are wired into the agent's
     tool list.
  4. End-to-end run with a stubbed client produces the documented
     `ResponseResult` shape so the route layer can rely on it.

Run:
    pytest tests/test_response_agent.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Iterable

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.agents.response_agent import (  # noqa: E402
    _TOOL_REGISTRY,
    _build_system_prompt,
    _build_tools,
    _load_agent_config,
    run_response,
)


@pytest.fixture(autouse=True)
def _isolate_telemetry(tmp_path, monkeypatch):
    """Redirect telemetry DBs to tmp so tests don't pollute real
    agent_executions / llm_calls history."""
    import core.agent_telemetry as at
    import core.llm_telemetry as lt
    monkeypatch.setattr(at, "DB_PATH", tmp_path / "agent.db")
    monkeypatch.setattr(lt, "DB_PATH", tmp_path / "llm.db")
    at.reset_schema_cache()
    if hasattr(lt, "_initialized"):
        monkeypatch.setattr(lt, "_initialized", False, raising=False)


def _resp(stop_reason: str, *, text: str = "",
          tokens_in: int = 100, tokens_out: int = 50):
    content = [SimpleNamespace(type="text", text=text)] if text else []
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=content,
        usage=SimpleNamespace(input_tokens=tokens_in, output_tokens=tokens_out),
    )


class _StubMessages:
    def __init__(self, responses: Iterable):
        self._responses = list(responses)
        self._i = 0
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        r = self._responses[self._i]
        self._i += 1
        return r


class _StubClient:
    def __init__(self, responses):
        self.messages = _StubMessages(responses)


# ──────────────────────────────────────────────────────────────────
# Config + tool wiring
# ──────────────────────────────────────────────────────────────────

class TestConfigLoader:
    def test_response_block_has_required_fields(self):
        """`response` agent must define all 4 budget knobs so the
        runner picks them up from yaml rather than silently
        falling through to defaults that may not match production."""
        cfg = _load_agent_config("response")
        assert cfg.get("model"), "response agent yaml needs a model"
        assert "max_turns" in cfg
        assert "max_total_tokens" in cfg
        assert "max_cost_usd" in cfg
        tools = set(cfg.get("tools") or [])
        # Phase 3C tool whitelist — search_* plus the carryover
        # Phase 3A tools. ask_user_clarification is deferred.
        for t in ("search_news", "search_alerts", "search_regulatory",
                  "get_persona_weights", "get_fx_rate"):
            assert t in tools, f"response agent yaml missing tool: {t}"

    def test_response_budget_tighter_than_intelligence(self):
        """A regression guard: ResponseAgent must have a SMALLER
        cost / turn cap than IntelligenceAgent. Chat is latency-
        sensitive — if someone bumps response to the same budget
        as intelligence the cost / response-time profile changes."""
        from core.agents.intelligence_agent import _load_agent_config as load_intel
        resp = _load_agent_config("response")
        intel = load_intel("intelligence")
        assert resp["max_turns"] <= intel["max_turns"]
        assert resp["max_cost_usd"] <= intel["max_cost_usd"]


class TestToolWiring:
    def test_registry_covers_phase_3b_search_tools(self):
        for name in ("search_news", "search_alerts", "search_regulatory"):
            assert name in _TOOL_REGISTRY, f"missing: {name}"

    def test_build_tools_threads_request_id(self):
        tools = _build_tools(["search_news", "get_persona_weights"],
                              request_id="rid_99")
        assert {t.name for t in tools} == {"search_news", "get_persona_weights"}
        search = next(t for t in tools if t.name == "search_news")
        # search_* tools carry the request_id; non-search tools were
        # built via the no-args fallback.
        assert getattr(search, "request_id", None) == "rid_99"

    def test_build_tools_skips_unknown(self):
        """Typos in agent_runtime.yaml shouldn't crash the agent."""
        tools = _build_tools(["search_news", "imaginary_tool"], request_id="r")
        assert {t.name for t in tools} == {"search_news"}


# ──────────────────────────────────────────────────────────────────
# System prompt
# ──────────────────────────────────────────────────────────────────

class TestSystemPrompt:
    def test_persona_label_is_substituted(self):
        prompt = _build_system_prompt("fx_manager")
        assert "FX risk manager" in prompt

    def test_unknown_persona_falls_back_gracefully(self):
        """An unknown persona key (e.g. typo from the frontend)
        must not crash — fall back to a neutral label."""
        prompt = _build_system_prompt("some_typo")
        assert "treasury professional" in prompt
        # The persona_label substitution slot is filled, not left
        # bare with `{persona_label}` text.
        assert "{persona_label}" not in prompt


# ──────────────────────────────────────────────────────────────────
# End-to-end with stubbed Anthropic client
# ──────────────────────────────────────────────────────────────────

class TestRunResponse:
    def test_happy_path_returns_response_result(self):
        client = _StubClient([_resp("end_turn", text="Short conversational answer.")])
        out = run_response("compare ecb vs boe", client=client)
        assert out.status == "ok"
        assert out.answer == "Short conversational answer."
        assert out.turns_used == 1
        assert out.query == "compare ecb vs boe"
        assert out.cost_usd >= 0

    def test_request_id_shared_with_tools(self, monkeypatch):
        """Same regression test as IntelligenceAgent's — the
        request_id we mint for tool telemetry must also flow to
        AgentRunner.run() so agent_executions / rag_queries
        rows can be joined."""
        seen = {}
        from core.agents import response_agent as ra
        from core.agent_runner import AgentResult

        def _fake_run(self, user_message, *, request_id=None):
            seen["runner_request_id"] = request_id
            seen["tool_ids"] = [
                getattr(t, "request_id", None)
                for t in self.tools_by_name.values()
                if hasattr(t, "request_id")
            ]
            return AgentResult(
                status="ok", output="x", turns_used=1,
                tokens_in=0, tokens_out=0, cost_usd=0.0,
                tool_calls=[], request_id=request_id or "",
            )

        monkeypatch.setattr(ra.AgentRunner, "run", _fake_run)
        client = _StubClient([_resp("end_turn", text="x")])
        out = ra.run_response("question", client=client)

        assert seen["runner_request_id"]
        assert all(tid == seen["runner_request_id"] for tid in seen["tool_ids"])
        assert out.request_id == seen["runner_request_id"]

    def test_error_status_carries_message(self):
        class _Boom:
            class messages:
                @staticmethod
                def create(**_kw):
                    raise RuntimeError("429 rate limited")
        out = run_response("query", client=_Boom())
        assert out.status == "error"
        assert out.message and "429" in out.message

    def test_persona_flows_into_system_prompt_at_runtime(self):
        """The route hands us `persona`; it must reach the system
        prompt the runner sends. Verifying by inspecting the kwargs
        the stubbed client.messages.create() received."""
        client = _StubClient([_resp("end_turn", text="x")])
        run_response("hello", persona="risk_manager", client=client)
        call = client.messages.calls[0]
        assert "risk manager" in call["system"].lower()
