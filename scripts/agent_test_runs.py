#!/usr/bin/env python3
"""
Phase 3A sanity script — exercises the agent runner end-to-end with
a stub Anthropic client (no real API calls, no spend) so a developer
can verify the turn loop, budget guards, and telemetry writes after
a code change.

Usage:
    python scripts/agent_test_runs.py
    python scripts/agent_test_runs.py --case end_turn
    python scripts/agent_test_runs.py --case tool_use
    python scripts/agent_test_runs.py --case max_turns
    python scripts/agent_test_runs.py --case budget_tokens

The cases are deterministic — they program the stub client to return
specific stop_reasons / tool_use blocks in a fixed order. Useful as a
"does the agent_runner still work" smoke test before launching Phase 3B.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable, List

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.agent_runner import AgentRunner  # noqa: E402
from core.tools.base import Tool, ToolResult  # noqa: E402


# ────────────────────────────────────────────────────────────────
# Stub client + dummy tool
# ────────────────────────────────────────────────────────────────

class _StubMessagesAPI:
    """Anthropic-compatible enough for AgentRunner."""
    def __init__(self, responses: Iterable[Any]) -> None:
        self._responses = list(responses)
        self._i = 0

    def create(self, **_kwargs: Any) -> Any:
        if self._i >= len(self._responses):
            # Exhausted scripted responses → end gracefully.
            return _make_response("end_turn", text="(stub: out of scripted turns)")
        r = self._responses[self._i]
        self._i += 1
        return r


class _StubClient:
    def __init__(self, responses: Iterable[Any]) -> None:
        self.messages = _StubMessagesAPI(responses)


def _make_response(
    stop_reason: str,
    *, text: str = "",
    tool_uses: Iterable[dict] = (),
    tokens_in: int = 100,
    tokens_out: int = 50,
) -> SimpleNamespace:
    """Build an Anthropic-shaped response object. tool_uses is an
    iterable of {name, input, id} dicts."""
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


class _EchoTool(Tool):
    """Trivial tool: returns whatever was passed in `args['msg']`."""
    name = "echo"
    description = "Echo the msg argument back."
    input_schema = {
        "type": "object",
        "properties": {"msg": {"type": "string"}},
        "required": ["msg"],
    }
    def execute(self, args: dict) -> ToolResult:
        return ToolResult.success(content=f"echo: {args.get('msg', '')}")


# ────────────────────────────────────────────────────────────────
# Cases
# ────────────────────────────────────────────────────────────────

def case_end_turn() -> None:
    """One model call, model returns end_turn immediately."""
    client = _StubClient([_make_response("end_turn", text="Done.")])
    runner = AgentRunner(
        agent_name="dummy", client=client, model="claude-sonnet-4-6",
        system_prompt="test", tools=[_EchoTool()],
        invoked_by="test",
    )
    r = runner.run("hello")
    print(f"[end_turn]   status={r.status} turns={r.turns_used} "
          f"out_tokens={r.tokens_out} cost=${r.cost_usd:.5f} output={r.output!r}")
    assert r.status == "ok" and r.turns_used == 1


def case_tool_use() -> None:
    """Two turns: tool_use → then end_turn."""
    client = _StubClient([
        _make_response("tool_use",
            tool_uses=[{"name": "echo", "input": {"msg": "hi"}, "id": "tu_A"}]),
        _make_response("end_turn", text="OK."),
    ])
    runner = AgentRunner(
        agent_name="dummy", client=client, model="claude-sonnet-4-6",
        system_prompt="test", tools=[_EchoTool()],
        invoked_by="test",
    )
    r = runner.run("call echo with 'hi'")
    print(f"[tool_use]   status={r.status} turns={r.turns_used} "
          f"tools={[c['tool'] for c in r.tool_calls]} output={r.output!r}")
    assert r.status == "ok" and r.turns_used == 2 and r.tool_calls[0]["tool"] == "echo"


def case_max_turns() -> None:
    """Model keeps requesting tools forever → should hit max_turns guard."""
    keep_calling = [
        _make_response("tool_use",
            tool_uses=[{"name": "echo", "input": {"msg": f"t{i}"}, "id": f"tu_{i}"}])
        for i in range(20)
    ]
    client = _StubClient(keep_calling)
    runner = AgentRunner(
        agent_name="dummy", client=client, model="claude-sonnet-4-6",
        system_prompt="test", tools=[_EchoTool()],
        max_turns=3, invoked_by="test",
    )
    r = runner.run("loop forever")
    print(f"[max_turns]  status={r.status} turns={r.turns_used} (cap=3)")
    assert r.status == "max_turns" and r.turns_used == 3


def case_budget_tokens() -> None:
    """One turn returns huge token counts → triggers max_total_tokens."""
    client = _StubClient([
        _make_response("tool_use",
            tool_uses=[{"name": "echo", "input": {"msg": "a"}, "id": "tu_1"}],
            tokens_in=50_000, tokens_out=60_000),
        _make_response("end_turn", text="(should not get here)"),
    ])
    runner = AgentRunner(
        agent_name="dummy", client=client, model="claude-sonnet-4-6",
        system_prompt="test", tools=[_EchoTool()],
        max_total_tokens=100_000, invoked_by="test",
    )
    r = runner.run("burn tokens")
    print(f"[tokens]     status={r.status} tokens={r.tokens_in + r.tokens_out} "
          f"(cap=100k) error={r.error}")
    assert r.status == "budget_exceeded"


CASES = {
    "end_turn":       case_end_turn,
    "tool_use":       case_tool_use,
    "max_turns":      case_max_turns,
    "budget_tokens":  case_budget_tokens,
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=list(CASES) + ["all"], default="all")
    args = parser.parse_args()

    cases = list(CASES) if args.case == "all" else [args.case]
    print(f"Running Phase 3A sanity cases: {cases}\n")
    for name in cases:
        try:
            CASES[name]()
        except AssertionError as exc:
            print(f"FAIL {name}: {exc}")
            return 1
        except Exception as exc:
            print(f"ERROR {name}: {exc}")
            return 2
    print("\nAll cases passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
