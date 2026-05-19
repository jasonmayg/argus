"""
Phase 3A — AgentRunner.

Owns the turn loop for an autonomous agent: send messages → receive
response → dispatch any `tool_use` blocks → append `tool_result`
blocks → loop. Stops when the model returns `end_turn`, or when one
of three hard budgets is breached:

  * `max_turns`        — abort after N model invocations
  * `max_total_tokens` — abort once input+output crosses N tokens
  * `max_cost_usd`     — abort once accumulated cost crosses $X

On any budget breach, the runner returns the partial output it has so
far rather than raising — agent failure is a graceful degraded state,
not an exception, because the JSON cache / telemetry / response shape
all need to record what happened.

Anthropic SDK is used directly here (not via core.llm_router) because
tool-use turn loops can't switch providers mid-run: tool_use_ids
are scoped to the Anthropic conversation, so a Groq fallback would
have to discard and restart from scratch. Phase 0 fallback semantics
don't match that, so the runner stays Anthropic-only and a failed run
is the caller's signal to retry as a whole.

Telemetry: every turn writes a row to `llm_calls` (so per-turn cost
shows up in existing reports). The final run-level summary writes one
row to `agent_executions`. They join on `request_id`.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from core import agent_telemetry, llm_telemetry
from core.tools.base import Tool, ToolResult

logger = logging.getLogger(__name__)


# Anthropic pricing (USD per 1M tokens) — keep in sync with
# https://www.anthropic.com/pricing. Used to derive cost_usd from
# usage.input_tokens / usage.output_tokens.
_PRICING: Dict[str, Tuple[float, float]] = {
    # model_id: (input_per_million, output_per_million)
    "claude-opus-4-7":         (15.0, 75.0),
    "claude-sonnet-4-6":       (3.0,  15.0),
    "claude-haiku-4-5":        (1.0,  5.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
}


def _estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    """Lookup-based pricing. Falls back to Sonnet rates for any model
    we haven't tabulated — better an over-estimate than under, so
    budget breaches trip on the safe side."""
    inp, out = _PRICING.get(model, _PRICING["claude-sonnet-4-6"])
    return (tokens_in / 1_000_000) * inp + (tokens_out / 1_000_000) * out


@dataclass
class AgentResult:
    """Final outcome of a single agent.run() call."""
    status: str                                  # ok | budget_exceeded | max_turns | error
    output: str = ""                             # final assistant text (best-effort)
    turns_used: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    request_id: str = ""
    # Populated whenever status != "ok": carries the budget-breach reason,
    # max_turns ceiling note, or the upstream exception message. Callers
    # rendering a UI for the user should display this verbatim alongside
    # the partial output. Empty only when the run finished cleanly.
    message: Optional[str] = None
    # Deprecated alias for `message` — kept so any external caller already
    # reading `result.error` doesn't break silently. Always equal to
    # `message` after a run completes.
    error: Optional[str] = None


# Truncation budget for exception strings we forward to the LLM as
# tool_result content. Long exception messages can balloon context and
# may leak internal paths / config; we keep just enough for the model
# to react sensibly, and log full detail to telemetry separately.
_TOOL_EXC_MAX_CHARS = 200


class AgentRunner:
    def __init__(
        self,
        *,
        agent_name: str,
        client: Any,                              # Anthropic client or compatible stub
        model: str,
        system_prompt: str,
        tools: List[Tool],
        max_turns: int = 10,
        max_total_tokens: int = 100_000,
        max_cost_usd: float = 0.50,
        max_response_tokens: int = 4096,
        invoked_by: str = "api",
    ) -> None:
        # Reuse ToolRegistry's validation (non-empty + unique names)
        # rather than rolling our own dict-build. A typo in a tool's
        # `name` attribute or two tools sharing a name used to silently
        # shadow one of them — refuse at construction instead.
        from core.tools.base import ToolRegistry
        registry = ToolRegistry()
        for t in tools:
            registry.register(t)
        self.agent_name          = agent_name
        self.client              = client
        self.model               = model
        self.system_prompt       = system_prompt
        self.tools_by_name       = registry.instances_for([t.name for t in tools])
        self.tool_specs          = [t.anthropic_spec for t in tools]
        self.max_turns           = max_turns
        self.max_total_tokens    = max_total_tokens
        self.max_cost_usd        = max_cost_usd
        self.max_response_tokens = max_response_tokens
        self.invoked_by          = invoked_by

    # ───────────────────────────────────────────────────────────
    def run(self, user_message: str) -> AgentResult:
        request_id = agent_telemetry.new_request_id()
        started_at = datetime.now(timezone.utc)
        messages: List[Dict[str, Any]] = [
            {"role": "user", "content": user_message},
        ]

        turns_used = 0
        tokens_in_total = 0
        tokens_out_total = 0
        tool_calls: List[Dict[str, Any]] = []
        last_text = ""
        status = "ok"
        error_message: Optional[str] = None
        error_class: Optional[str] = None

        try:
            for turn_idx in range(self.max_turns):
                turns_used = turn_idx + 1
                t_start = time.time()
                try:
                    resp = self.client.messages.create(
                        model=self.model,
                        system=self.system_prompt,
                        tools=self.tool_specs,
                        messages=messages,
                        max_tokens=self.max_response_tokens,
                    )
                except Exception as exc:
                    # Anthropic call failed (rate limit, transport, etc.).
                    # Record the turn-level failure and abort the loop —
                    # mid-run provider switching isn't safe (see module
                    # docstring), so the whole run is the unit of retry.
                    llm_telemetry.log_call_safe(
                        task=f"agent.{self.agent_name}", provider="anthropic",
                        model=self.model, status="failed",
                        latency_ms=int((time.time() - t_start) * 1000),
                        tokens_in=0, tokens_out=0,
                        error_short=str(exc)[:200], fallback_index=0,
                        request_id=request_id,
                    )
                    status = "error"
                    error_class = type(exc).__name__
                    error_message = str(exc)
                    break

                latency_ms = int((time.time() - t_start) * 1000)
                usage = getattr(resp, "usage", None)
                t_in = int(getattr(usage, "input_tokens", 0) or 0)
                t_out = int(getattr(usage, "output_tokens", 0) or 0)
                tokens_in_total += t_in
                tokens_out_total += t_out

                llm_telemetry.log_call_safe(
                    task=f"agent.{self.agent_name}", provider="anthropic",
                    model=self.model, status="ok",
                    latency_ms=latency_ms, tokens_in=t_in, tokens_out=t_out,
                    error_short=None, fallback_index=0, request_id=request_id,
                )

                # Capture the latest assistant text — even if the run is
                # going to be cut short by a budget, this is what we
                # return as partial output.
                last_text = self._extract_text(resp.content) or last_text

                # Budget guards. Check after the turn so we always have
                # something to log for the current turn.
                total_tokens = tokens_in_total + tokens_out_total
                cost = _estimate_cost(self.model, tokens_in_total, tokens_out_total)
                if total_tokens > self.max_total_tokens:
                    status = "budget_exceeded"
                    error_message = f"token budget exceeded ({total_tokens} > {self.max_total_tokens})"
                    break
                if cost > self.max_cost_usd:
                    status = "budget_exceeded"
                    error_message = f"cost budget exceeded (${cost:.4f} > ${self.max_cost_usd:.2f})"
                    break

                stop_reason = getattr(resp, "stop_reason", None)
                if stop_reason == "end_turn":
                    # Model decided it's done — happy path.
                    status = "ok"
                    break

                # Otherwise: tool_use blocks expected. Dispatch them
                # and append both the assistant message and our
                # tool_result blocks to messages, then loop.
                tool_use_blocks = [b for b in resp.content
                                   if getattr(b, "type", None) == "tool_use"]
                if not tool_use_blocks:
                    # Some other stop reason with no tool_use — treat
                    # as terminal so we don't spin.
                    status = "ok"
                    break

                tool_result_blocks: List[Dict[str, Any]] = []
                for tu in tool_use_blocks:
                    name = getattr(tu, "name", "")
                    tool_args = dict(getattr(tu, "input", {}) or {})
                    tool_id = getattr(tu, "id", "")
                    t_tool = time.time()

                    tool = self.tools_by_name.get(name)
                    if tool is None:
                        result = ToolResult.failure(
                            f"tool '{name}' is not registered for this agent"
                        )
                    else:
                        try:
                            result = tool.execute(tool_args)
                        except Exception as exc:
                            # Defence in depth: Tool.execute is supposed
                            # to return ToolResult.failure() on errors,
                            # never raise. If a tool implementation
                            # forgets and raises, recover here.
                            # Forward only the exception class + a
                            # truncated message to the LLM — raw repr
                            # can carry stack traces, file paths, or
                            # secrets that we don't want in conversation
                            # context. Full detail is preserved in the
                            # per-turn telemetry log below + the agent
                            # log when this exception happened.
                            logger.warning(
                                "agent_runner: tool '%s' raised in agent '%s'",
                                name, self.agent_name, exc_info=True,
                            )
                            short = f"{type(exc).__name__}: {str(exc)[:_TOOL_EXC_MAX_CHARS]}"
                            result = ToolResult.failure(
                                f"tool '{name}' raised: {short}"
                            )

                    tool_latency_ms = int((time.time() - t_tool) * 1000)
                    tool_calls.append({
                        "tool": name,
                        "ok": bool(result.ok),
                        "latency_ms": tool_latency_ms,
                    })
                    tool_result_blocks.append({
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": result.content,
                        "is_error": not result.ok,
                    })

                # Anthropic expects the assistant's full content block
                # list (including its tool_use entries) preserved
                # verbatim, then the user (tool_result) message.
                messages.append({"role": "assistant", "content": resp.content})
                messages.append({"role": "user", "content": tool_result_blocks})

            else:
                # for/else: loop completed without `break` ⇒ ran out of turns.
                status = "max_turns"
                error_message = f"max_turns ({self.max_turns}) reached before end_turn"

        except Exception as exc:
            # Anything not caught above (extraction bug etc.).
            status = "error"
            error_class = type(exc).__name__
            error_message = str(exc)
            logger.exception("agent_runner: unexpected error in run()")

        finished_at = datetime.now(timezone.utc)
        cost = _estimate_cost(self.model, tokens_in_total, tokens_out_total)

        agent_telemetry.log_run_safe(
            agent_name=self.agent_name,
            invoked_by=self.invoked_by,
            request_id=request_id,
            started_at=started_at,
            finished_at=finished_at,
            status=status,
            turns_used=turns_used,
            tokens_in=tokens_in_total,
            tokens_out=tokens_out_total,
            cost_usd=cost,
            tool_calls=tool_calls,
            output_preview=last_text,
            error_class=error_class,
            error_message=error_message,
        )

        return AgentResult(
            status=status,
            output=last_text,
            turns_used=turns_used,
            tokens_in=tokens_in_total,
            tokens_out=tokens_out_total,
            cost_usd=cost,
            tool_calls=tool_calls,
            request_id=request_id,
            # `message` is the canonical field; `error` mirrors it for
            # back-compat (the AgentResult contract used to use only
            # `error` and we don't want existing callers to break).
            message=error_message,
            error=error_message,
        )

    # ───────────────────────────────────────────────────────────
    @staticmethod
    def _extract_text(content_blocks: Any) -> str:
        """Pull out the text from an Anthropic response content list.
        Tolerant of both the real SDK object shape (with .type / .text
        attrs) and dict-style stubs."""
        out: List[str] = []
        for b in content_blocks or []:
            b_type = getattr(b, "type", None) or (b.get("type") if isinstance(b, dict) else None)
            if b_type == "text":
                text = getattr(b, "text", None) or (b.get("text") if isinstance(b, dict) else "") or ""
                if text:
                    out.append(text)
        return "\n".join(out).strip()
