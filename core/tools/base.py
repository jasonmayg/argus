"""
Phase 3A — Tool ABC + ToolResult + a process-local registry.

A Tool is the unit of capability an autonomous agent can call. Each tool
declares:
  * a `name` (must match across the Anthropic tools-API spec and the
    registry key — the LLM references it verbatim)
  * a human-readable `description`
  * an `input_schema` (JSON Schema, the same shape Anthropic's tool_use
    API expects)
  * an `execute(args)` method that returns a `ToolResult`

The runner dispatches `tool_use` blocks from Anthropic responses by
looking up the name in the registry. Tools never raise to the runner —
errors are surfaced as `ToolResult(ok=False, error=...)`, which the
runner then echoes back to the LLM as a `tool_result` block so the
model can either retry with different args or back off.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class ToolResult:
    """Outcome of a single tool invocation.

    Attributes:
        ok:         True if the call produced usable output.
        content:    Markdown-friendly summary the LLM sees as the
                    tool_result block on the next turn. Keep this short
                    — long tool_result content burns the model's
                    context budget faster than anything else.
        structured: Optional raw payload (dict/list/etc.) the runner
                    can dump to telemetry but does NOT show the LLM —
                    use it when downstream debugging needs the full
                    response but the prompt only needs a summary.
        error:      Short human-readable error string when ok=False.
                    Becomes the `content` the LLM sees, prefixed with
                    "ERROR: ", so the model can act on the failure.
    """
    ok: bool
    content: str
    structured: Optional[dict] = None
    error: Optional[str] = None

    @classmethod
    def success(cls, content: str, structured: Optional[dict] = None) -> "ToolResult":
        return cls(ok=True, content=content, structured=structured)

    @classmethod
    def failure(cls, error: str) -> "ToolResult":
        return cls(ok=False, content=f"ERROR: {error}", error=error)


class Tool(ABC):
    """Subclass and define ``name``, ``description``, ``input_schema``,
    and ``execute``. Instances should be cheap to construct — the
    runner instantiates whichever tools its agent has whitelisted at
    invocation time, not at module load."""

    name: str = ""
    description: str = ""
    input_schema: Dict[str, Any] = {}

    @abstractmethod
    def execute(self, args: Dict[str, Any]) -> ToolResult:
        """Run the tool against validated args. Implementations must
        return a ToolResult — never raise. Wrap risky work in
        try/except and emit ToolResult.failure() on error."""

    @property
    def anthropic_spec(self) -> Dict[str, Any]:
        """The shape Anthropic's Messages API expects in ``tools=[...]``.
        Centralized here so individual Tool subclasses don't repeat the
        boilerplate; they just set the three class attributes."""
        return {
            "name":         self.name,
            "description":  self.description,
            "input_schema": self.input_schema,
        }


# ──────────────────────────────────────────────────────────────────
# Process-local registry
# ──────────────────────────────────────────────────────────────────
# Agents declare their tools by name in agent_runtime.yaml. The
# registry resolves those names to Tool instances at runtime. Keeping
# this process-local (no global mutation across imports) makes tests
# trivially isolated — each test can register its own fakes.

class ToolRegistry:
    def __init__(self) -> None:
        self._tools: Dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if not tool.name:
            raise ValueError("Tool.name must be a non-empty string")
        if tool.name in self._tools:
            # Two distinct Tool instances with the same name would
            # silently shadow each other. Refuse rather than guess.
            raise ValueError(f"Tool '{tool.name}' already registered")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools.keys())

    def specs_for(self, names: list[str]) -> list[Dict[str, Any]]:
        """Return Anthropic tool specs for the given subset, preserving
        order. Skips names not registered — the runner is responsible
        for warning when a configured tool is missing."""
        out: list[Dict[str, Any]] = []
        for n in names:
            t = self._tools.get(n)
            if t is not None:
                out.append(t.anthropic_spec)
        return out

    def instances_for(self, names: list[str]) -> Dict[str, Tool]:
        return {n: t for n, t in ((n, self._tools.get(n)) for n in names) if t is not None}
