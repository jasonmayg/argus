"""
Unit tests for `query_econ_indicator`.

The two correctness anchors:
  1. Input validation — series_id pattern + country-prefix allowlist
     prevent the agent from coaxing the tool into reading arbitrary
     GCS blobs (e.g. `../foo/bar`).
  2. Empty-series vs network-error are distinct outcomes the agent
     should treat differently. `_fetch_series` returns [] vs None
     respectively and the tool reflects that distinction in errors.

Run:
    pytest tests/test_tool_query_econ_indicator.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.tools.query_econ_indicator import QueryEconIndicator


@pytest.fixture
def tool(monkeypatch) -> QueryEconIndicator:
    t = QueryEconIndicator()
    # Default: 5 deterministic rows so happy-path tests are reproducible.
    def fake(series_id):
        return [
            {"date": "2026-05-10", "value": "1.0"},
            {"date": "2026-05-11", "value": "1.1"},
            {"date": "2026-05-12", "value": "1.2"},
            {"date": "2026-05-13", "value": "1.3"},
            {"date": "2026-05-14", "value": "1.4"},
        ]
    monkeypatch.setattr(t, "_fetch_series", fake)
    return t


class TestValidation:
    """Schema enforcement — the agent can hallucinate series_ids; the
    tool must refuse malformed ones rather than poking GCS."""

    def test_empty_series_id_rejected(self, tool):
        r = tool.execute({"series_id": "   "})
        assert r.ok is False
        assert "required" in r.error.lower()

    def test_pattern_violation_rejected(self, tool):
        """Anything outside `[a-z][a-z0-9_]{1,40}` (after lowercasing)
        — including path traversal attempts — must be refused at the
        validator. Uppercase is normalized first by design (see
        `test_lowercase_normalization`), so `US_CPI` isn't in this list."""
        for bad in ["../foo", "us cpi", "us-cpi", "1_cpi", "us/cpi", "x" * 60]:
            r = tool.execute({"series_id": bad})
            assert r.ok is False, f"id {bad!r} should be rejected"
            assert "invalid" in r.error.lower() or "unsupported" in r.error.lower()

    def test_unknown_country_prefix_rejected(self, tool):
        """Series ids like `xyz_cpi` are syntactically valid but the
        country prefix is not allowlisted → refuse. Keeps the tool
        from being weaponized to enumerate the bucket."""
        r = tool.execute({"series_id": "xyz_cpi"})
        assert r.ok is False
        assert "unsupported country prefix" in r.error.lower()
        # Error should list the allowlist so the LLM can recover.
        assert "us" in r.error and "jp" in r.error

    def test_lowercase_normalization(self, tool):
        """Args from an LLM are not guaranteed lowercase. The
        validator normalizes before the pattern check."""
        r = tool.execute({"series_id": "  US_CPI  "})
        # Pattern check is on lowercased input, so "us_cpi" matches.
        assert r.ok is True


class TestHappyPath:
    def test_returns_tail_rows(self, tool):
        r = tool.execute({"series_id": "us_cpi"})
        assert r.ok is True
        # Default tail is 10; we stubbed 5 rows → tail returns all 5.
        assert r.structured["total_rows"] == 5
        assert len(r.structured["tail"]) == 5
        # Latest + prior surface for direction reasoning.
        assert r.structured["latest"]["value"] == "1.4"
        assert r.structured["prior"]["value"] == "1.3"

    def test_tail_clamped(self, tool):
        r = tool.execute({"series_id": "us_cpi", "tail": 999})
        # Should clamp; with 5 stubbed rows + clamp at _MAX_TAIL we
        # just get the full series back.
        assert len(r.structured["tail"]) == 5

    def test_content_includes_value_and_date(self, tool):
        r = tool.execute({"series_id": "us_cpi"})
        assert "us_cpi" in r.content
        assert "2026-05-14" in r.content
        assert "1.4" in r.content


class TestFailureModes:
    def test_empty_series_is_failure(self, tool, monkeypatch):
        """File exists but has 0 rows → failure with specific text so
        the agent can react ('try a sibling indicator', etc.)."""
        monkeypatch.setattr(tool, "_fetch_series", lambda _id: [])
        r = tool.execute({"series_id": "us_cpi"})
        assert r.ok is False
        assert "no rows" in r.error.lower()

    def test_network_failure_distinct_error(self, tool, monkeypatch):
        """`_fetch_series` returns None on network/auth error so the
        agent knows the difference between 'empty' and 'unreachable'."""
        monkeypatch.setattr(tool, "_fetch_series", lambda _id: None)
        r = tool.execute({"series_id": "us_cpi"})
        assert r.ok is False
        assert "gcs fetch failed" in r.error.lower()

    def test_invalid_tail_returns_explicit_failure(self, tool):
        """Regression: an LLM-supplied non-numeric `tail` must yield a
        specific 'must be an integer' failure rather than a generic
        'tool raised: ValueError' that the runner would have to wrap."""
        r = tool.execute({"series_id": "us_cpi", "tail": "lots"})
        assert r.ok is False
        assert "tail" in r.error and "integer" in r.error
