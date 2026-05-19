"""
Unit tests for core.tools.base — Tool ABC + ToolResult + ToolRegistry.

Run:
    pytest tests/test_tools_base.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.tools.base import Tool, ToolRegistry, ToolResult  # noqa: E402


class _Dummy(Tool):
    name = "dummy"
    description = "test tool"
    input_schema = {"type": "object", "properties": {"x": {"type": "string"}}}

    def execute(self, args):
        return ToolResult.success(content=f"got {args.get('x')}")


class TestToolResult:
    def test_success_factory(self):
        r = ToolResult.success("hello", structured={"k": 1})
        assert r.ok is True
        assert r.content == "hello"
        assert r.structured == {"k": 1}
        assert r.error is None

    def test_failure_factory_prefixes_error_in_content(self):
        """The runner forwards ToolResult.content into the LLM's
        tool_result block; prefixing 'ERROR:' makes it unambiguous to
        the model that the tool didn't succeed."""
        r = ToolResult.failure("bad arg")
        assert r.ok is False
        assert r.content == "ERROR: bad arg"
        assert r.error == "bad arg"


class TestToolSpec:
    def test_anthropic_spec_shape(self):
        t = _Dummy()
        spec = t.anthropic_spec
        assert spec["name"] == "dummy"
        assert spec["description"] == "test tool"
        assert spec["input_schema"]["type"] == "object"


class TestToolRegistry:
    def test_register_and_get(self):
        reg = ToolRegistry()
        reg.register(_Dummy())
        t = reg.get("dummy")
        assert t is not None and t.name == "dummy"
        assert reg.get("missing") is None

    def test_register_rejects_empty_name(self):
        class _Nameless(_Dummy):
            name = ""
        reg = ToolRegistry()
        with pytest.raises(ValueError, match="non-empty"):
            reg.register(_Nameless())

    def test_register_rejects_duplicate(self):
        """Silently shadowing a registered tool would surface as
        bewildering production behavior — fail loud at registration."""
        reg = ToolRegistry()
        reg.register(_Dummy())
        with pytest.raises(ValueError, match="already registered"):
            reg.register(_Dummy())

    def test_specs_for_preserves_order_and_skips_missing(self):
        reg = ToolRegistry()
        reg.register(_Dummy())
        specs = reg.specs_for(["dummy", "nonexistent"])
        assert [s["name"] for s in specs] == ["dummy"]

    def test_instances_for_only_returns_registered(self):
        reg = ToolRegistry()
        reg.register(_Dummy())
        instances = reg.instances_for(["dummy", "ghost"])
        assert list(instances) == ["dummy"]
