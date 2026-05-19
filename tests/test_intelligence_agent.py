"""
Unit tests for `core.agents.intelligence_agent`.

The agent is mostly composition — it reads agent_runtime.yaml,
instantiates the configured tools with the right request_id,
and hands them + the system prompt to AgentRunner. Tests pin
each composition step.

Run:
    pytest tests/test_intelligence_agent.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Iterable
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.agents.intelligence_agent import (  # noqa: E402
    _TOOL_REGISTRY,
    _build_tools,
    _load_agent_config,
    run_intelligence,
)


# ──────────────────────────────────────────────────────────────────
# Telemetry isolation — point both telemetry DBs at temp files so
# real persona_eval / agent_executions data isn't touched.
# ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _isolate_telemetry(tmp_path, monkeypatch):
    import core.agent_telemetry as at
    import core.llm_telemetry as lt
    monkeypatch.setattr(at, "DB_PATH", tmp_path / "agent.db")
    monkeypatch.setattr(lt, "DB_PATH", tmp_path / "llm.db")
    at.reset_schema_cache()
    if hasattr(lt, "_initialized"):
        monkeypatch.setattr(lt, "_initialized", False, raising=False)


# ──────────────────────────────────────────────────────────────────
# Stub Anthropic client returning end_turn immediately
# ──────────────────────────────────────────────────────────────────

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
# Composition: which tools wire up?
# ──────────────────────────────────────────────────────────────────

class TestToolWiring:
    def test_registry_covers_every_phase3b_tool(self):
        """Phase 3B's deliverable list is search_news / search_alerts /
        search_regulatory / query_econ_indicator. All four must appear
        in the registry so the agent can find them when the yaml lists
        them."""
        for name in ("search_news", "search_alerts", "search_regulatory",
                     "query_econ_indicator"):
            assert name in _TOOL_REGISTRY, f"missing tool: {name}"

    def test_registry_keeps_phase3a_tools(self):
        """The Phase 3A tools (`get_fx_rate`, `get_persona_weights`)
        stay registered — they're listed in the intelligence agent's
        yaml tool whitelist and we don't want to lose them in 3B."""
        assert "get_fx_rate" in _TOOL_REGISTRY
        assert "get_persona_weights" in _TOOL_REGISTRY

    def test_build_tools_threads_request_id_to_search_tools(self):
        """search_* tools accept `request_id` so their telemetry rows
        join the agent_executions row. Non-search tools don't have
        that ctor arg and `_build_tools` must tolerate both shapes."""
        tools = _build_tools(
            ["search_news", "get_persona_weights"], request_id="rid_42",
        )
        names = {t.name for t in tools}
        assert names == {"search_news", "get_persona_weights"}
        # search_news should carry the request_id; get_persona_weights
        # doesn't have one (instantiated via empty-args branch).
        search = next(t for t in tools if t.name == "search_news")
        assert getattr(search, "request_id", None) == "rid_42"

    def test_build_tools_skips_unknown_names(self):
        """A typo in agent_runtime.yaml shouldn't crash — log + skip
        is the documented contract. Returned list contains only the
        registered tools that resolved successfully."""
        tools = _build_tools(["search_news", "nonexistent_tool"], request_id="x")
        assert {t.name for t in tools} == {"search_news"}


# ──────────────────────────────────────────────────────────────────
# Config loader
# ──────────────────────────────────────────────────────────────────

class TestConfigLoader:
    def test_intelligence_block_has_required_fields(self):
        cfg = _load_agent_config("intelligence")
        assert cfg.get("model"), "intelligence agent yaml must define a model"
        assert "max_turns" in cfg
        assert "max_total_tokens" in cfg
        assert "max_cost_usd" in cfg
        # Tools list must include the 4 Phase 3B tools.
        tools = set(cfg.get("tools") or [])
        for t in ("search_news", "search_alerts", "search_regulatory",
                  "query_econ_indicator"):
            assert t in tools, f"yaml whitelist missing {t}"

    def test_missing_agent_returns_empty_dict(self):
        cfg = _load_agent_config("nonexistent_agent")
        assert cfg == {}


# ──────────────────────────────────────────────────────────────────
# Full run with stub client
# ──────────────────────────────────────────────────────────────────

class TestRunIntelligence:
    def test_happy_path_returns_intelligence_result(self):
        client = _StubClient([_resp("end_turn", text="Brief: ...")])
        out = run_intelligence("what did the ECB do?", client=client)
        assert out.status == "ok"
        assert out.answer == "Brief: ..."
        assert out.turns_used == 1
        assert out.query == "what did the ECB do?"
        # Cost is computed (sonnet pricing × tiny token count) — just
        # check the shape, not the exact value.
        assert out.cost_usd >= 0

    def test_query_field_preserved_in_result(self):
        """Frontend / telemetry rely on the original query string
        showing up unchanged in the response so they can correlate."""
        client = _StubClient([_resp("end_turn", text="ok")])
        out = run_intelligence("specific query xyz", client=client)
        assert out.query == "specific query xyz"

    def test_error_status_carries_message(self):
        """When the runner returns status=error, the IntelligenceResult
        surfaces the message field so the route layer can branch on
        upstream vs internal errors."""
        # Stub that raises on .create — runner records error status.
        class _Boom:
            class messages:
                @staticmethod
                def create(**_kw):
                    raise RuntimeError("429 rate limited")
        out = run_intelligence("x" * 20, client=_Boom())
        assert out.status == "error"
        assert out.message and "429" in out.message
