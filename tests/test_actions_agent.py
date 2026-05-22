"""
Unit tests for `core.agents.actions_agent`.

Mirrors the IntelligenceAgent / ResponseAgent test structure since
all three follow the same Phase 3 runner-composition pattern. What
we pin specifically:

  1. The `actions` block in agent_runtime.yaml gets loaded with the
     Opus-justified budget (15 turns, $1.00 cap, 150k tokens).
  2. The Phase 3D tools (cash positions, historical actions,
     coherence) are wired into the agent's tool list.
  3. End-to-end run with a stubbed Anthropic client produces the
     documented `ActionsResult` shape so the route layer can rely on
     it.
  4. Error / crash paths return `status='error'` rather than raising,
     since `routes.actions_routes` decides legacy-fallback based on
     the status field.

Run:
    pytest tests/test_actions_agent.py -v
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

from core.agents.actions_agent import (  # noqa: E402
    _TOOL_REGISTRY,
    _build_tools,
    _load_agent_config,
    run_actions,
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
    def test_actions_block_has_required_fields(self):
        """`actions` agent must define all 4 budget knobs so the
        runner picks them up from yaml rather than silently falling
        through to defaults that may not match production."""
        cfg = _load_agent_config("actions")
        assert cfg.get("model"), "actions agent yaml needs a model"
        assert "max_turns" in cfg
        assert "max_total_tokens" in cfg
        assert "max_cost_usd" in cfg
        tools = set(cfg.get("tools") or [])
        # Phase 3D tool whitelist — three new tools plus the persona /
        # fx anchors carried over from Phase 3A.
        for t in ("get_cash_positions", "get_historical_actions",
                  "score_action_coherence", "get_persona_weights",
                  "get_fx_rate"):
            assert t in tools, f"actions agent yaml missing tool: {t}"

    def test_actions_budget_higher_than_response(self):
        """A regression guard: ActionsAgent must have a LARGER cost /
        turn cap than ResponseAgent. Business-decision recommendations
        justify Opus + more turns; chat does not. If someone flattens
        the cap, the per-action cost profile collapses to chat-tier."""
        from core.agents.response_agent import _load_agent_config as load_resp
        actions = _load_agent_config("actions")
        resp = load_resp("response")
        assert actions["max_turns"] >= resp["max_turns"]
        assert actions["max_cost_usd"] >= resp["max_cost_usd"]


class TestToolWiring:
    def test_registry_covers_phase_3d_tools(self):
        for name in ("get_cash_positions", "get_historical_actions",
                     "score_action_coherence"):
            assert name in _TOOL_REGISTRY, f"missing: {name}"

    def test_build_tools_threads_request_id(self):
        tools = _build_tools(
            ["get_historical_actions", "get_persona_weights"],
            request_id="rid_77",
        )
        assert {t.name for t in tools} == {
            "get_historical_actions", "get_persona_weights",
        }
        hist = next(t for t in tools if t.name == "get_historical_actions")
        # Phase 3D RAG-side tools carry the request_id; non-RAG tools
        # are built via the no-args fallback.
        assert getattr(hist, "request_id", None) == "rid_77"

    def test_build_tools_skips_unknown(self):
        """Typos in agent_runtime.yaml shouldn't crash the agent."""
        tools = _build_tools(
            ["get_cash_positions", "imaginary_tool"], request_id="r",
        )
        assert {t.name for t in tools} == {"get_cash_positions"}


# ──────────────────────────────────────────────────────────────────
# End-to-end with stubbed Anthropic client
# ──────────────────────────────────────────────────────────────────

class TestRunActions:
    def test_happy_path_returns_actions_result(self):
        client = _StubClient([_resp("end_turn", text='{"actions": []}')])
        out = run_actions("signals: [...]", client=client)
        assert out.status == "ok"
        assert out.answer == '{"actions": []}'
        assert out.turns_used == 1
        assert out.cost_usd >= 0
        assert out.request_id

    def test_request_id_shared_with_tools(self, monkeypatch):
        """Same regression test as Intelligence/ResponseAgent's — the
        request_id we mint for tool telemetry must also flow to
        AgentRunner.run() so agent_executions / rag_queries rows can
        be joined in /api/admin/agents/actions/recent."""
        seen = {}
        from core.agents import actions_agent as aa
        from core.agent_runner import AgentResult

        def _fake_run(self, user_message, *, request_id=None):
            seen["runner_request_id"] = request_id
            seen["tool_ids"] = [
                getattr(t, "request_id", None)
                for t in self.tools_by_name.values()
                if hasattr(t, "request_id")
            ]
            return AgentResult(
                status="ok", output='{"actions":[]}', turns_used=1,
                tokens_in=0, tokens_out=0, cost_usd=0.0,
                tool_calls=[], request_id=request_id or "",
            )

        monkeypatch.setattr(aa.AgentRunner, "run", _fake_run)
        client = _StubClient([_resp("end_turn", text='{"actions":[]}')])
        out = aa.run_actions("prompt", client=client)

        assert seen["runner_request_id"]
        assert all(tid == seen["runner_request_id"] for tid in seen["tool_ids"])
        assert out.request_id == seen["runner_request_id"]

    def test_error_status_carries_message(self):
        """Upstream Anthropic failures surface as status='error' with a
        message, NOT as an unhandled exception. The route layer reads
        status to decide on legacy fallback."""
        class _Boom:
            class messages:
                @staticmethod
                def create(**_kw):
                    raise RuntimeError("429 rate limited")
        out = run_actions("query", client=_Boom())
        assert out.status == "error"
        assert out.message and "429" in out.message

    def test_invoked_by_label_default(self, monkeypatch):
        """The runner emits this label into agent_executions.invoked_by
        so admin telemetry shows the call origin. Default must be
        api.actions so route-driven runs are distinguishable from
        future scripted ones."""
        captured = {}
        from core.agents import actions_agent as aa
        original_init = aa.AgentRunner.__init__

        def _capture(self, *a, **kw):
            captured["invoked_by"] = kw.get("invoked_by")
            original_init(self, *a, **kw)

        monkeypatch.setattr(aa.AgentRunner, "__init__", _capture)
        client = _StubClient([_resp("end_turn", text='{"actions":[]}')])
        aa.run_actions("q", client=client)
        assert captured["invoked_by"] == "api.actions"
