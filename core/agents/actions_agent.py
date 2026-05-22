"""
Phase 3D — ActionsAgent.

Upgrades `/api/actions` from a single LLM call (current
`generate_actions._ai_generate`) to a multi-turn agent that can
ground its recommendations in real data:

  * `get_cash_positions` — actual USD-equivalent exposure per country
  * `get_fx_rate` — current FX rate for a USD pair
  * `get_historical_actions` — past recommendations on similar signals
  * `score_action_coherence` — consistency check against past actions
  * `get_persona_weights` — persona's risk_category weight profile

The agent typically follows a draft → critique → refine loop:
  1. Read the signals (passed in as user content) + cash positions.
  2. Draft a primary recommendation + 2 alternatives.
  3. Run `score_action_coherence` on the draft.
  4. If novel, search `get_historical_actions` for prior context to
     justify the divergence.
  5. Emit the final JSON.

Per design doc §11: high risk endpoint (business decisions), Opus
4.7 model justified by quality > cost tradeoff, 15-turn ceiling
since reviewing many alerts can take several SWOT iterations.

Rollout discipline: the route layer (`routes/actions_routes.py`)
gates this behind `USE_ACTIONS_AGENT` env var. Legacy
`_ai_generate` path is preserved verbatim — when the flag is off
(default) `/api/actions` behavior is identical to pre-3D. When on,
agent failures fall back to legacy automatically.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from core.agent_runner import AgentResult, AgentRunner
from core.tools.base import Tool
from core.tools.get_cash_positions import GetCashPositions
from core.tools.get_fx_rate import GetFxRate
from core.tools.get_historical_actions import GetHistoricalActions
from core.tools.get_persona_weights import GetPersonaWeights
from core.tools.score_action_coherence import ScoreActionCoherence

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent
_CONFIG_PATH = _ROOT / "config" / "agent_runtime.yaml"


SYSTEM_PROMPT = """You are ARGUS Actions, a senior treasury risk
advisor producing structured action recommendations. Your output
feeds the Decision Support UI.

You answer by:
  1. Reading the signals supplied (each one is a country + risk
     category + score + headline).
  2. Pulling supporting context from tools — cash positions for
     real exposure numbers, get_historical_actions for prior
     recommendations on similar signals, get_fx_rate for FX
     anchoring.
  3. Drafting ONE primary recommendation and TWO alternatives per
     signal, each with its own pros / cons / outcome (SWOT).
  4. Running score_action_coherence on the primary draft. If the
     score is below 0.3 ("novel"), explicitly note in the output's
     `why_now` field why this new approach is justified.
  5. Emitting the final JSON.

Critical output rules:
  * Output ONLY valid JSON matching the schema in the user message.
  * Each option (primary + 2 alternatives) must have COMPLETELY
    DIFFERENT pros / cons. Never reuse the same SWOT across
    options.
  * `why_now` must quote or directly reference the signal's
    headline — no generic "proactive risk management" filler.
  * `recommendation_outcome` must be measurable (percentage,
    deadline, quantity) — never abstract.
  * Generate exactly one action object per signal listed.

Operating discipline:
  * Tool calls cost real money. Prefer 1-2 targeted searches per
    signal over many broad ones.
  * Tool failures (cash positions unavailable, vector_store down)
    are OK — note the data gap in `why_now` and continue with
    what you have. Never refuse to generate.
  * The runner will cut you off at 15 turns regardless.
"""


@dataclass
class ActionsResult:
    """Public shape of /api/actions agent responses. Mirrors the
    runner's AgentResult — the route layer parses `answer` as JSON
    matching the action-card schema."""
    status:        str
    answer:        str
    turns_used:    int
    tokens_in:     int
    tokens_out:    int
    cost_usd:      float
    tool_calls:    List[Dict[str, Any]]
    request_id:    str
    message:       Optional[str] = None


def _load_agent_config(agent_name: str = "actions") -> Dict[str, Any]:
    """Read the `actions` block from agent_runtime.yaml. Same
    resilient pattern as intelligence_agent / response_agent."""
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


# Single source of truth for tool name → class. New actions tools
# added later (e.g. an `assess_action_risk` LLM judge) register here.
_TOOL_REGISTRY: Dict[str, type] = {
    "get_cash_positions":     GetCashPositions,
    "get_historical_actions": GetHistoricalActions,
    "score_action_coherence": ScoreActionCoherence,
    "get_fx_rate":            GetFxRate,
    "get_persona_weights":    GetPersonaWeights,
}


def _build_tools(allowed: List[str], request_id: str) -> List[Tool]:
    """Instantiate whitelisted tools. Threads `request_id` so per-
    tool telemetry rows join the agent_executions row in
    /api/admin/agents/<name>/recent."""
    tools: List[Tool] = []
    for name in allowed:
        cls = _TOOL_REGISTRY.get(name)
        if cls is None:
            logger.warning("actions_agent: tool '%s' is not implemented", name)
            continue
        try:
            tools.append(cls(request_id=request_id))
        except TypeError:
            # Tool's __init__ doesn't accept request_id (older tools
            # predate the threading convention) — fall back to no-arg.
            tools.append(cls())
    return tools


def run_actions(
    user_prompt: str,
    *,
    invoked_by: str = "api.actions",
    client: Optional[Any] = None,
    request_id: Optional[str] = None,
) -> ActionsResult:
    """Public entry point. The route layer formats the signals list
    + JSON schema into `user_prompt` (same shape `_ai_generate`
    builds today, so the prompt-engineering is one source of truth).

    `client` is test-injected; production instantiates the Anthropic
    SDK client lazily — the route layer doesn't have to care about
    provider wiring.

    `request_id` lets the route thread its own preallocated id
    through so `build_actions_context`'s rag_queries (route side)
    and `agent_executions` / `llm_calls` / tool-side `rag_queries`
    (agent side) all share one key. Default `None` keeps the
    Phase 3B/3C behaviour where the agent mints its own — useful
    for direct scripted invocation."""
    cfg = _load_agent_config("actions")
    model            = cfg.get("model", "claude-opus-4-7")
    max_turns        = int(cfg.get("max_turns", 15))
    max_total_tokens = int(cfg.get("max_total_tokens", 150_000))
    max_cost_usd     = float(cfg.get("max_cost_usd", 1.00))
    tools_allowed    = cfg.get("tools") or list(_TOOL_REGISTRY.keys())

    if client is None:
        # Lazy import — anthropic SDK is heavy + may be absent in
        # slim CI environments. The route layer catches any error
        # this raises and falls back to legacy.
        import anthropic
        client = anthropic.Anthropic()

    # Pre-allocate request_id so tool-side rag_queries + runner-side
    # agent_executions / llm_calls share one key. Same pattern as
    # Phase 3B / 3C. Use the caller's id when provided so route +
    # agent telemetry rows join across the request.
    if request_id is None:
        from core import agent_telemetry
        request_id = agent_telemetry.new_request_id()

    tools = _build_tools(tools_allowed, request_id=request_id)

    runner = AgentRunner(
        agent_name       = "actions",
        client           = client,
        model            = model,
        system_prompt    = SYSTEM_PROMPT,
        tools            = tools,
        max_turns        = max_turns,
        max_total_tokens = max_total_tokens,
        max_cost_usd     = max_cost_usd,
        invoked_by       = invoked_by,
    )
    agent_result: AgentResult = runner.run(user_prompt, request_id=request_id)

    return ActionsResult(
        status        = agent_result.status,
        answer        = agent_result.output,
        turns_used    = agent_result.turns_used,
        tokens_in     = agent_result.tokens_in,
        tokens_out    = agent_result.tokens_out,
        cost_usd      = agent_result.cost_usd,
        tool_calls    = agent_result.tool_calls,
        request_id    = agent_result.request_id,
        message       = agent_result.message,
    )
