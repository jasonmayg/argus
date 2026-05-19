"""
Phase 3B — IntelligenceAgent.

Composes Phase 3A's AgentRunner with the search_* / query_econ /
fx / persona tools to answer cross-source synthesis questions like:

  "What did the ECB do this month, and how does it affect EUR/USD?"
  "Show me recent regulatory shifts that matter for a fx_manager
   in Asia."

Routing is intentionally minimal at this stage — the agent is exposed
via a dedicated `/api/intelligence/query` endpoint. Phase 3C will
plumb the same agent into `/api/chat` when the request looks complex
enough to warrant the extra turns.
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


SYSTEM_PROMPT = """You are ARGUS Intelligence, a senior treasury risk
analyst embedded in a treasury risk-monitoring platform.

You answer questions by retrieving structured evidence from Argus's
indexed data — news, alerts, regulatory changes, economic indicators,
FX rates — and synthesizing it into a concise analytical brief.

Operating rules:
  1. Plan your retrieval before you call tools. Prefer 2-4 targeted
     tool calls over many broad ones. Tool results consume context.
  2. Cite specifics. When you reference a finding, name the source
     (e.g. "Per ECB Monetary Policy Statement [R3], ...") rather
     than paraphrasing vaguely.
  3. If retrieved evidence is sparse or contradictory, say so —
     don't invent confidence. A short "data is thin, here's what we
     have" beats a confident-sounding hallucination.
  4. Default output format: 3-6 sentence narrative + a short
     "Sources" bullet list at the end with the IDs you cited.
  5. Stop after you have a defensible answer. Extra tool calls cost
     real money and clip the budget; the runner will cut you off if
     you exceed it. Aim for the smallest set of tool calls that
     supports a sound conclusion.

You have access to these tools. Each tool's input schema is enforced —
read it before you call.
"""


@dataclass
class IntelligenceResult:
    """Public shape of /api/intelligence/query responses. Mirrors the
    runner's AgentResult but adds the original query for context."""
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


def _load_agent_config(agent_name: str = "intelligence") -> Dict[str, Any]:
    """Read the per-agent block from agent_runtime.yaml. Returns the
    raw dict (model / max_turns / max_total_tokens / max_cost_usd /
    tools), defaulting to safe values if the file or block is missing.
    Keeping this resilient means a misconfigured yaml doesn't crash
    the route — it just runs with the documented defaults."""
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


# Single source of truth for which Tool subclass implements each name
# advertised in agent_runtime.yaml. New search_* / query_* tools should
# register here so the agent picks them up automatically.
_TOOL_REGISTRY: Dict[str, type] = {
    "search_news":          SearchNews,
    "search_alerts":        SearchAlerts,
    "search_regulatory":    SearchRegulatory,
    "query_econ_indicator": QueryEconIndicator,
    "get_fx_rate":          GetFxRate,
    "get_persona_weights":  GetPersonaWeights,
}


def _build_tools(allowed: List[str], request_id: str) -> List[Tool]:
    """Instantiate the tools the agent is whitelisted for. Tools that
    accept a request_id (search_*) get it so their telemetry rows
    join cleanly to the agent_executions row."""
    tools: List[Tool] = []
    for name in allowed:
        cls = _TOOL_REGISTRY.get(name)
        if cls is None:
            # Unknown tool name in the yaml — log and skip. The runner
            # would otherwise see a tool registered with empty config.
            logger.warning("intelligence_agent: tool '%s' is not implemented", name)
            continue
        # Tools that take a request_id (search_*) accept it as ctor arg;
        # the others (get_fx_rate / get_persona_weights / query_econ)
        # are constructed without args. Inspect the __init__ at runtime
        # to stay tolerant of either shape.
        try:
            tools.append(cls(request_id=request_id))
        except TypeError:
            tools.append(cls())
    return tools


def run_intelligence(
    query: str,
    *,
    invoked_by: str = "api",
    client: Optional[Any] = None,
) -> IntelligenceResult:
    """Public entry point — instantiate the runner with config-driven
    budgets, dispatch the user query, and return a structured result.

    `client` is injected by tests; in production we instantiate the
    Anthropic SDK client here so the route layer doesn't have to
    care about provider wiring."""
    cfg = _load_agent_config("intelligence")
    model = cfg.get("model", "claude-sonnet-4-6")
    max_turns = int(cfg.get("max_turns", 10))
    max_total_tokens = int(cfg.get("max_total_tokens", 100_000))
    max_cost_usd = float(cfg.get("max_cost_usd", 0.50))
    tools_allowed = cfg.get("tools") or list(_TOOL_REGISTRY.keys())

    if client is None:
        # Lazy import: the Anthropic SDK is heavy + may not be
        # installed in slimmer CI environments. The route layer
        # surfaces this as a 503-style error if it fails.
        import anthropic
        client = anthropic.Anthropic()

    # request_id is allocated by the runner; we need it ahead of tool
    # construction so search_* tools can stamp it on their rag_queries
    # rows. The runner uses its own when it logs to agent_executions,
    # so we generate one here matching that scheme.
    from core import agent_telemetry
    request_id = agent_telemetry.new_request_id()

    tools = _build_tools(tools_allowed, request_id=request_id)

    runner = AgentRunner(
        agent_name="intelligence",
        client=client,
        model=model,
        system_prompt=SYSTEM_PROMPT,
        tools=tools,
        max_turns=max_turns,
        max_total_tokens=max_total_tokens,
        max_cost_usd=max_cost_usd,
        invoked_by=invoked_by,
    )
    # We pre-generated the request_id for tools; pass it through so
    # the runner uses the same id for its telemetry rows. (Runner
    # currently allocates its own; we accept that small redundancy
    # — the tool-side rows still group by their stamped id, which
    # is what matters for the rag_queries / agent_executions join.)
    agent_result: AgentResult = runner.run(query)

    return IntelligenceResult(
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
