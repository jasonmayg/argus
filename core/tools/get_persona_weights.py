"""
`get_persona_weights` — surfaces persona risk weights to the agent.

Agents use this to calibrate "relevance" — when answering "should this
ECB rate move trigger an action for the fx_manager?", the LLM can pull
fx_manager's weight for monetary_policy / regulatory_risk first and
score the impact against that, rather than relying on a generic prior.

Backed by personas/persona_loader, so any region override or future
profile-override change picks up automatically.
"""

from __future__ import annotations

import json
from typing import Any, Dict

from personas import persona_loader

from .base import Tool, ToolResult


class GetPersonaWeights(Tool):
    name = "get_persona_weights"
    description = (
        "Return the risk-category weights configured for a persona, optionally "
        "narrowed to a specific region. Use this before scoring relevance of an "
        "event for a persona — weights >= 1.4 indicate elevated sensitivity, "
        "<= 0.4 indicate the persona effectively ignores that category."
    )
    input_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "persona": {
                "type": "string",
                "description": "Persona key (e.g. cash_manager, fx_manager, risk_manager, treasury_ops).",
            },
            "region": {
                "type": "string",
                "description": (
                    "Optional region key for region_overrides (e.g. us, china, eu). "
                    "Omit for the global persona defaults."
                ),
            },
        },
        "required": ["persona"],
    }

    def execute(self, args: Dict[str, Any]) -> ToolResult:
        persona = (args.get("persona") or "").strip()
        region = (args.get("region") or "").strip() or None
        if not persona:
            return ToolResult.failure("persona argument is required")

        try:
            if region:
                cfg = persona_loader.load_defaults_for_region(persona, region)
            else:
                cfg = persona_loader.load_defaults(persona)
        except Exception as e:
            return ToolResult.failure(f"persona_loader error: {e}")

        if not cfg:
            return ToolResult.failure(
                f"persona '{persona}'"
                + (f" / region '{region}'" if region else "")
                + " not found"
            )

        weights = cfg.get("risk_weights") or {}
        threshold = cfg.get("alert_threshold")

        # Compact summary for the LLM. Sort by weight desc so the most
        # salient categories appear first — the model tends to anchor
        # on the head of any list.
        if weights:
            top_lines = [
                f"  {cat}: {w}"
                for cat, w in sorted(weights.items(), key=lambda kv: -float(kv[1]))
            ]
            content = (
                f"Persona '{persona}'"
                + (f" / region '{region}'" if region else "")
                + (f" (alert_threshold={threshold})" if threshold is not None else "")
                + ":\n" + "\n".join(top_lines)
            )
        else:
            content = f"Persona '{persona}' has no risk weights configured."

        return ToolResult.success(
            content=content,
            structured={
                "persona": persona,
                "region": region,
                "alert_threshold": threshold,
                "risk_weights": dict(weights),
            },
        )
