"""
Unit tests for core.agent_runner.AgentRunner.

Stubs the Anthropic client with a scripted-response object so tests
never hit the wire. Each case programs the stub to return a specific
stop_reason + content sequence and verifies the runner produces the
right AgentResult.

Run:
    pytest tests/test_agent_runner.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable, List

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.agent_runner import AgentRunner, _estimate_cost  # noqa: E402
from core.tools.base import Tool, ToolResult  # noqa: E402


# ────────────────────────────────────────────────────────────────
# Stubs
# ────────────────────────────────────────────────────────────────

def _resp(stop_reason: str, *, text: str = "", tool_uses=(),
          tokens_in: int = 100, tokens_out: int = 50,
          fail_with: Exception | None = None) -> Any:
    """Return an Anthropic-shaped response, or a sentinel that the
    stub client converts into a raised exception (for testing the
    runner's error-path branch)."""
    if fail_with is not None:
        return ("raise", fail_with)
    content: List[Any] = []
    if text:
        content.append(SimpleNamespace(type="text", text=text))
    for tu in tool_uses:
        content.append(SimpleNamespace(
            type="tool_use", name=tu["name"],
            input=tu.get("input", {}), id=tu.get("id", "tu_1"),
        ))
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=content,
        usage=SimpleNamespace(input_tokens=tokens_in, output_tokens=tokens_out),
    )


class _StubMessages:
    def __init__(self, responses: Iterable[Any]) -> None:
        self._responses = list(responses)
        self._i = 0
        self.calls: List[dict] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self._i >= len(self._responses):
            return _resp("end_turn", text="(default)")
        r = self._responses[self._i]
        self._i += 1
        if isinstance(r, tuple) and r and r[0] == "raise":
            raise r[1]
        return r


class _StubClient:
    def __init__(self, responses: Iterable[Any]) -> None:
        self.messages = _StubMessages(responses)


class _Echo(Tool):
    name = "echo"
    description = "echo"
    input_schema = {"type": "object", "properties": {"msg": {"type": "string"}}}
    def execute(self, args):
        return ToolResult.success(content=f"echo:{args.get('msg')}")


class _Raises(Tool):
    """A buggy tool — tests that a tool which raises (instead of
    returning ToolResult.failure) is still recovered by the runner."""
    name = "raises"
    description = "raises"
    input_schema = {"type": "object"}
    def execute(self, args):
        raise RuntimeError("kaboom")


@pytest.fixture(autouse=True)
def _isolate_telemetry(tmp_path, monkeypatch):
    """Point both telemetry modules at temp DBs so tests don't pollute
    the prod argus_news.db. Without this, every test run leaves
    agent_executions rows behind."""
    import core.agent_telemetry as at
    import core.llm_telemetry as lt
    monkeypatch.setattr(at, "DB_PATH", tmp_path / "agent.db")
    monkeypatch.setattr(lt, "DB_PATH", tmp_path / "llm.db")
    at.reset_schema_cache()
    # llm_telemetry uses a module-level _initialized flag like agent_telemetry —
    # but there's no public reset. Best-effort reset:
    if hasattr(lt, "_initialized"):
        monkeypatch.setattr(lt, "_initialized", False, raising=False)


def _runner(client, **kwargs) -> AgentRunner:
    defaults = dict(
        agent_name="dummy", client=client, model="claude-sonnet-4-6",
        system_prompt="test", tools=[_Echo()],
        invoked_by="test",
    )
    defaults.update(kwargs)
    return AgentRunner(**defaults)


# ────────────────────────────────────────────────────────────────
# Happy paths
# ────────────────────────────────────────────────────────────────

class TestHappyPaths:
    def test_end_turn_first_response(self):
        client = _StubClient([_resp("end_turn", text="Done.")])
        r = _runner(client).run("hi")
        assert r.status == "ok"
        assert r.turns_used == 1
        assert r.output == "Done."
        assert r.tool_calls == []

    def test_tool_use_then_end_turn(self):
        client = _StubClient([
            _resp("tool_use",
                tool_uses=[{"name": "echo", "input": {"msg": "hi"}, "id": "tu_A"}]),
            _resp("end_turn", text="OK."),
        ])
        r = _runner(client).run("call echo")
        assert r.status == "ok"
        assert r.turns_used == 2
        assert r.output == "OK."
        assert r.tool_calls == [{"tool": "echo", "ok": True,
                                  "latency_ms": r.tool_calls[0]["latency_ms"]}]

    def test_tool_call_messages_include_tool_result_block(self):
        """After dispatching a tool, the runner must send the next
        request with both the assistant's tool_use content AND a
        user-role tool_result block. Anthropic enforces this shape."""
        client = _StubClient([
            _resp("tool_use",
                tool_uses=[{"name": "echo", "input": {"msg": "x"}, "id": "tu_X"}]),
            _resp("end_turn", text="bye"),
        ])
        _runner(client).run("go")
        # Second request to messages.create() carries the appended
        # assistant + tool_result messages.
        second_msgs = client.messages.calls[1]["messages"]
        assert second_msgs[0]["role"] == "user"
        assert second_msgs[-2]["role"] == "assistant"   # tool_use carrier
        assert second_msgs[-1]["role"] == "user"
        tr = second_msgs[-1]["content"][0]
        assert tr["type"] == "tool_result"
        assert tr["tool_use_id"] == "tu_X"
        assert tr["is_error"] is False


# ────────────────────────────────────────────────────────────────
# Guard rails
# ────────────────────────────────────────────────────────────────

class TestBudgetGuards:
    def test_max_turns(self):
        """Model keeps requesting tools forever → runner stops at cap."""
        loop = [
            _resp("tool_use",
                tool_uses=[{"name": "echo", "input": {"msg": f"t{i}"}, "id": f"tu_{i}"}])
            for i in range(10)
        ]
        client = _StubClient(loop)
        r = _runner(client, max_turns=3).run("loop")
        assert r.status == "max_turns"
        assert r.turns_used == 3

    def test_token_budget(self):
        client = _StubClient([
            _resp("tool_use",
                tool_uses=[{"name": "echo", "input": {"msg": "a"}, "id": "tu_1"}],
                tokens_in=50_000, tokens_out=60_000),
        ])
        r = _runner(client, max_total_tokens=100_000).run("burn tokens")
        assert r.status == "budget_exceeded"
        assert "token" in r.error.lower()

    def test_cost_budget(self):
        """Tiny token budget but tight cost cap → cost guard trips first."""
        # 1M input tokens on Sonnet costs $3, well above max_cost_usd=0.01.
        client = _StubClient([
            _resp("tool_use",
                tool_uses=[{"name": "echo", "input": {"msg": "a"}, "id": "tu_1"}],
                tokens_in=1_000_000, tokens_out=100),
        ])
        r = _runner(client, max_total_tokens=10_000_000,
                    max_cost_usd=0.01).run("burn cost")
        assert r.status == "budget_exceeded"
        assert "cost" in r.error.lower()


class TestErrorPaths:
    def test_anthropic_call_raises(self):
        """Transport/rate-limit etc. — single failed call ends the run
        (mid-run provider switching is not safe)."""
        client = _StubClient([_resp("ignored", fail_with=RuntimeError("429 rate limit"))])
        r = _runner(client).run("hi")
        assert r.status == "error"
        assert "429" in r.error

    def test_unknown_tool_name(self):
        """Model hallucinated a tool the agent isn't whitelisted for —
        runner surfaces the failure as a tool_result so the model can
        recover on the next turn."""
        client = _StubClient([
            _resp("tool_use",
                tool_uses=[{"name": "ghost", "input": {}, "id": "tu_1"}]),
            _resp("end_turn", text="ack"),
        ])
        r = _runner(client).run("call ghost")
        assert r.status == "ok"
        # The runner recorded the call as failed.
        assert r.tool_calls[0] == {"tool": "ghost", "ok": False,
                                   "latency_ms": r.tool_calls[0]["latency_ms"]}
        # And forwarded the error to the LLM as tool_result with is_error=True.
        second_msgs = client.messages.calls[1]["messages"]
        tr = second_msgs[-1]["content"][0]
        assert tr["is_error"] is True
        assert "not registered" in tr["content"]

    def test_tool_that_raises_is_recovered(self):
        """A tool that raises (instead of returning ToolResult.failure)
        must not crash the run — the runner wraps every execute() call."""
        client = _StubClient([
            _resp("tool_use",
                tool_uses=[{"name": "raises", "input": {}, "id": "tu_1"}]),
            _resp("end_turn", text="ack"),
        ])
        r = _runner(client, tools=[_Raises()]).run("call raises")
        assert r.status == "ok"
        assert r.tool_calls[0]["ok"] is False


# ────────────────────────────────────────────────────────────────
# Cost estimator
# ────────────────────────────────────────────────────────────────

class TestCostEstimator:
    def test_sonnet_pricing(self):
        # 1M in @ $3, 1M out @ $15
        assert _estimate_cost("claude-sonnet-4-6", 1_000_000, 1_000_000) == pytest.approx(18.0)

    def test_unknown_model_falls_back_to_sonnet(self):
        """Better to over-estimate than under — budget breaches should
        trip on the safe side when an unfamiliar model id appears."""
        unknown = _estimate_cost("claude-future-99", 1_000_000, 1_000_000)
        sonnet = _estimate_cost("claude-sonnet-4-6", 1_000_000, 1_000_000)
        assert unknown == sonnet
