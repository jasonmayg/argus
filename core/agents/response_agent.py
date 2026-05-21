"""
Phase 3C — ResponseAgent.

Backs the `complex` branch of `/api/chat`. Same AgentRunner +
Phase 3B tools as IntelligenceAgent, but:

  * Tighter budget (8 turns / $0.30 vs Intelligence's 10 / $0.50)
    because chat is latency-sensitive — a long-running agent
    behind the chat UI is worse than a less rich answer.
  * Shorter system prompt: chat answers should sound conversational,
    not analyst-brief format with Sources blocks.
  * Optional persona kwarg: the chat caller passes the active
    persona so the agent can frame the answer for that role.

The router decision lives in `core.agents.chat_router.classify`.
Simple queries continue to use the existing legacy RAG path; only
queries the router flags as "complex" reach this agent.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from core.agent_runner import AgentResult, AgentRunner
from core.tools.base import Tool
from core.tools.get_fx_rate import GetFxRate
from core.tools.get_persona_weights import GetPersonaWeights
from core.tools.query_econ_indicator import QueryEconIndicator
from core.tools.search_alerts import SearchAlerts
from core.tools.search_news import SearchNews
from core.tools.search_regulatory import SearchRegulatory

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent
_CONFIG_PATH = _ROOT / "config" / "agent_runtime.yaml"


SYSTEM_PROMPT_TEMPLATE = """You are ARGUS Chat, the conversational
risk assistant inside a treasury risk-monitoring platform. The user
is a {persona_label}.

Answer conversationally — short paragraphs, no headings, no
"Sources:" footer. Plain inline citations like "Per ECB statement
[R3]" are fine when you reference a tool result, but skip them
when the answer is general knowledge.

Operating rules:
  1. The user already saw a chat-thread history; don't re-explain
     context they obviously have.
  2. Prefer 1-3 targeted tool calls over many broad ones. Each
     call costs latency that the chat UI feels.
  3. If retrieved evidence is sparse, say so plainly ("I don't see
     a recent statement on this") rather than making something up.
  4. Keep answers under ~150 words unless the question explicitly
     asks for a deep brief.
  5. Stop after you have a defensible answer — the runner will
     cut you off at 8 turns regardless.
"""


_PERSONA_LABELS = {
    "cash_manager":   "treasury cash manager",
    "fx_manager":     "FX risk manager",
    "risk_manager":   "enterprise risk manager",
    "treasury_ops":   "treasury operations lead",
}


@dataclass
class ResponseResult:
    """Result returned by `run_response`. Mirrors the parts of
    `AgentResult` the chat route cares about, plus the original
    query for telemetry / debug correlation."""
    status: str
    query: str
    answer: str
    turns_used: int
    tokens_in: int
    tokens_out: int
    cost_usd: float
    tool_calls: List[Dict[str, Any]]
    request_id: str
    message: Optional[str] = None


def _load_agent_config(agent_name: str = "response") -> Dict[str, Any]:
    """Read the `response` block from agent_runtime.yaml. See
    intelligence_agent._load_agent_config for the rationale (safe
    fallback when yaml is missing / malformed)."""
    if not _CONFIG_PATH.exists():
        return {}
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception as exc:
        logger.warning("agent_runtime.yaml read failed (%s)", exc)
        return {}
    agents = data.get("agents") or {}
    return agents.get(agent_name) or {}


# Same registry as intelligence_agent — kept duplicated rather than
# imported because Phase 3D's ActionsAgent will diverge (its tools
# include cash-position helpers that don't belong here). Local
# registries keep each agent's tool surface auditable in one place.
_TOOL_REGISTRY: Dict[str, type] = {
    "search_news":          SearchNews,
    "search_alerts":        SearchAlerts,
    "search_regulatory":    SearchRegulatory,
    "query_econ_indicator": QueryEconIndicator,
    "get_fx_rate":          GetFxRate,
    "get_persona_weights":  GetPersonaWeights,
}


def _build_tools(allowed: List[str], request_id: str) -> List[Tool]:
    """Instantiate the whitelisted tools. Threads `request_id` into
    `search_*` tools so their `rag_queries` telemetry rows join
    the agent_executions row by request_id."""
    tools: List[Tool] = []
    for name in allowed:
        cls = _TOOL_REGISTRY.get(name)
        if cls is None:
            logger.warning("response_agent: tool '%s' is not implemented", name)
            continue
        try:
            tools.append(cls(request_id=request_id))
        except TypeError:
            tools.append(cls())
    return tools


def _build_system_prompt(persona: str) -> str:
    label = _PERSONA_LABELS.get(persona, "treasury professional")
    return SYSTEM_PROMPT_TEMPLATE.format(persona_label=label)


def run_response(
    query: str,
    *,
    persona: str = "cash_manager",
    invoked_by: str = "api.chat",
    client: Optional[Any] = None,
) -> ResponseResult:
    """Public entry point. The chat route calls this for queries the
    router flagged as `complex`.

    `client` is injected by tests; production instantiates the
    Anthropic SDK client here so the route layer doesn't have to
    know about provider wiring."""
    cfg = _load_agent_config("response")
    model            = cfg.get("model", "claude-sonnet-4-6")
    max_turns        = int(cfg.get("max_turns", 8))
    max_total_tokens = int(cfg.get("max_total_tokens", 60_000))
    max_cost_usd     = float(cfg.get("max_cost_usd", 0.30))
    tools_allowed    = cfg.get("tools") or list(_TOOL_REGISTRY.keys())

    if client is None:
        # Lazy import — the Anthropic SDK is heavy + may not be
        # installed in slimmer CI environments. The route layer
        # surfaces this as an error and falls back to legacy RAG.
        import anthropic
        client = anthropic.Anthropic()

    # Pre-allocate request_id so tool-side rag_queries telemetry +
    # runner-side agent_executions / llm_calls rows share one key.
    from core import agent_telemetry
    request_id = agent_telemetry.new_request_id()

    tools = _build_tools(tools_allowed, request_id=request_id)

    runner = AgentRunner(
        agent_name="response",
        client=client,
        model=model,
        system_prompt=_build_system_prompt(persona),
        tools=tools,
        max_turns=max_turns,
        max_total_tokens=max_total_tokens,
        max_cost_usd=max_cost_usd,
        invoked_by=invoked_by,
    )
    agent_result: AgentResult = runner.run(query, request_id=request_id)

    return ResponseResult(
        status=agent_result.status,
        query=query,
        answer=agent_result.output,
        turns_used=agent_result.turns_used,
        tokens_in=agent_result.tokens_in,
        tokens_out=agent_result.tokens_out,
        cost_usd=agent_result.cost_usd,
        tool_calls=agent_result.tool_calls,
        request_id=agent_result.request_id,
        message=agent_result.message,
    )
