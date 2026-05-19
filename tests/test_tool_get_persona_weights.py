"""
Unit tests for core.tools.get_persona_weights.

Uses the real personas/persona_loader against the real defaults.yaml —
the tool's whole point is to surface real persona data to the LLM, so
testing against fakes would miss any drift between the YAML schema and
the tool's content format.

Run:
    pytest tests/test_tool_get_persona_weights.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.tools.get_persona_weights import GetPersonaWeights  # noqa: E402


@pytest.fixture
def tool() -> GetPersonaWeights:
    return GetPersonaWeights()


class TestHappyPath:
    def test_global_persona_returns_weights(self, tool):
        r = tool.execute({"persona": "cash_manager"})
        assert r.ok is True
        assert "cash_manager" in r.content
        # structured payload includes machine-readable weights
        assert isinstance(r.structured["risk_weights"], dict)
        assert "liquidity_risk" in r.structured["risk_weights"]
        assert r.structured["alert_threshold"] is not None
        # Highest-weight category should appear before lower-weighted
        # ones (the content is sorted desc by weight).
        weights = r.structured["risk_weights"]
        if len(weights) > 1:
            sorted_cats = [
                c for c, _ in sorted(weights.items(), key=lambda kv: -float(kv[1]))
            ]
            # The content lists them after the colon-newline; the first
            # category in sorted order should appear before the second.
            i0 = r.content.find(sorted_cats[0])
            i1 = r.content.find(sorted_cats[1])
            assert 0 <= i0 < i1

    def test_region_override_path(self, tool):
        r = tool.execute({"persona": "cash_manager", "region": "us"})
        assert r.ok is True
        # Region appears in both content and structured payload.
        assert "us" in r.content
        assert r.structured["region"] == "us"

    def test_blank_region_treated_as_global(self, tool):
        """`region: ''` from an LLM should not be passed downstream as
        a region key — the loader would treat it as a missing region."""
        r = tool.execute({"persona": "cash_manager", "region": "   "})
        assert r.ok is True
        assert r.structured["region"] is None


class TestErrorPaths:
    def test_missing_persona_arg(self, tool):
        r = tool.execute({})
        assert r.ok is False
        assert "persona" in r.error.lower()

    def test_unknown_persona(self, tool):
        r = tool.execute({"persona": "not_a_real_persona"})
        assert r.ok is False
        assert "not found" in r.error.lower()

    def test_unsupported_region_rejected(self, tool):
        """Copilot PR #45 review: an unknown region key used to silently
        fall through to global, making the tool claim a region override
        it didn't apply. Now it fails loudly listing the supported set."""
        r = tool.execute({"persona": "cash_manager", "region": "eu"})
        assert r.ok is False
        assert "unsupported region" in r.error.lower()
        # Error should help the LLM recover by listing valid options.
        assert "us" in r.error
        assert "japan" in r.error

    def test_explicit_global_region_passes(self, tool):
        """`region='global'` is a synonym for omitting the arg — it
        must not be rejected by the unsupported-region validator."""
        r = tool.execute({"persona": "cash_manager", "region": "global"})
        assert r.ok is True
