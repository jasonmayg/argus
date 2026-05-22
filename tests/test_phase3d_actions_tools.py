"""
Unit tests for the 3 Phase 3D actions-agent tools:

  * `get_cash_positions`        — current cash positions (xlsx-backed)
  * `get_historical_actions`    — semantic search over past actions
  * `score_action_coherence`    — quantitative consistency vs history

All three tools are fail-soft: never raise to the runner, return
ToolResult.failure with diagnostic text. These tests pin that
contract plus the per-tool quirks (numeric input validation,
filter wiring, empty-result rendering, score-formula edge cases).

Run:
    pytest tests/test_phase3d_actions_tools.py -v
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.tools.get_cash_positions import GetCashPositions
from core.tools.get_historical_actions import GetHistoricalActions
from core.tools.score_action_coherence import (
    ScoreActionCoherence,
    _NEIGHBORS_K,
    _STRONG_MATCH_THRESHOLD,
)


# ──────────────────────────────────────────────────────────────────
# Shared fake vector store — mirrors the search_* tools' test setup
# ──────────────────────────────────────────────────────────────────

@dataclass
class _Hit:
    id: str
    document: str
    metadata: dict
    distance: float


class _FakeStore:
    """Records search_actions_history calls so tests can assert on
    `where` filters and `request_id` threading."""

    def __init__(self, results: Optional[List[_Hit]] = None) -> None:
        self._results = results or []
        self.calls: List[Dict[str, Any]] = []

    def search_actions_history(self, query, n_results=None, where=None,
                                request_id=None, endpoint=None):
        self.calls.append({
            "query": query, "n_results": n_results, "where": where,
            "request_id": request_id, "endpoint": endpoint,
        })
        return self._results[: (n_results or len(self._results))]


# ──────────────────────────────────────────────────────────────────
# get_cash_positions
# ──────────────────────────────────────────────────────────────────

class TestGetCashPositions:

    def test_returns_all_positions_when_no_filter(self):
        """No-arg call returns the full table; LLM-friendly rendering
        is in `content`, structured `positions` is the raw payload."""
        rows = [
            {"country": "United States", "currency": "USD",
             "usd_equivalent": 5_000_000.0, "change_30d_pct": 1.2,
             "risk_level": "Low"},
            {"country": "Mexico", "currency": "MXN",
             "usd_equivalent": 4_200_000.0, "change_30d_pct": -3.4,
             "risk_level": "Medium"},
        ]
        with patch("routes.cash_routes._cache_valid", return_value=True), \
             patch("routes.cash_routes._get_positions", return_value=rows):
            r = GetCashPositions().execute({})
        assert r.ok is True
        # Both countries surface in content rendering.
        assert "United States" in r.content
        assert "Mexico" in r.content
        # Structured payload preserves the raw rows.
        assert r.structured["row_count"] == 2

    def test_country_filter_case_insensitive(self):
        rows = [
            {"country": "United States", "usd_equivalent": 5e6,
             "currency": "USD", "change_30d_pct": 1.0, "risk_level": "Low"},
            {"country": "Mexico", "usd_equivalent": 4.2e6,
             "currency": "MXN", "change_30d_pct": -3.0, "risk_level": "Medium"},
        ]
        with patch("routes.cash_routes._cache_valid", return_value=True), \
             patch("routes.cash_routes._get_positions", return_value=rows):
            r = GetCashPositions().execute({"country": "mexico"})
        assert r.ok is True
        # Only Mexico in the structured payload — filter applied.
        assert r.structured["row_count"] == 1
        assert r.structured["positions"][0]["country"] == "Mexico"

    def test_unknown_country_returns_success_with_empty(self):
        """Asking for a country that's not in the workbook returns an
        empty payload + clear message, not a failure. The agent
        decides whether to fall back to no-data reasoning."""
        with patch("routes.cash_routes._cache_valid", return_value=True), \
             patch("routes.cash_routes._get_positions",
                   return_value=[{"country": "United States", "usd_equivalent": 1}]):
            r = GetCashPositions().execute({"country": "Atlantis"})
        assert r.ok is True
        assert r.structured["positions"] == []
        assert "atlantis" in r.content.lower()

    def test_loader_failure_returns_failure(self):
        """Spreadsheet missing / GCS auth busted / pandas absent →
        loader raises. Tool must catch and return failure, NOT crash
        the agent."""
        with patch("routes.cash_routes._cache_valid", return_value=False), \
             patch("routes.cash_routes._load_excel",
                   side_effect=RuntimeError("GCS auth missing")):
            r = GetCashPositions().execute({})
        assert r.ok is False
        assert "GCS auth missing" in r.error

    def test_cap_at_50_rows(self):
        """Defensive — the spreadsheet should never have 50+ rows,
        but if a future widening lands the tool must not blow the
        LLM context with hundreds of countries."""
        many = [{"country": f"C{i}", "usd_equivalent": i,
                 "currency": "USD", "change_30d_pct": 0,
                 "risk_level": "Low"} for i in range(200)]
        with patch("routes.cash_routes._cache_valid", return_value=True), \
             patch("routes.cash_routes._get_positions", return_value=many):
            r = GetCashPositions().execute({})
        assert r.structured["row_count"] == 50


# ──────────────────────────────────────────────────────────────────
# get_historical_actions
# ──────────────────────────────────────────────────────────────────

class TestGetHistoricalActions:

    def test_empty_query_fails(self):
        r = GetHistoricalActions().execute({"query": "   "})
        assert r.ok is False
        assert "required" in r.error.lower()

    def test_happy_path_renders_H_tags(self):
        store = _FakeStore(results=[
            _Hit("a1", "Tighten MXN forwards, refresh hedge ratio",
                 {"country": "Mexico", "category": "Market Risk",
                  "score": 72, "generated_at": "2026-05-10"}, 0.12),
            _Hit("a2", "Reduce USD exposure across LatAm",
                 {"country": "Mexico", "category": "Market Risk",
                  "score": 65, "generated_at": "2026-04-22"}, 0.34),
        ])
        with patch("core.vector_store.get_vector_store", return_value=store):
            r = GetHistoricalActions(request_id="rid_xyz").execute({
                "query": "MXN hedging precedent",
            })
        assert r.ok is True
        assert "[H1]" in r.content and "[H2]" in r.content
        assert "Mexico" in r.content
        assert r.structured["hits"][0]["id"] == "a1"
        # request_id must thread to telemetry.
        assert store.calls[0]["request_id"] == "rid_xyz"
        # endpoint label distinguishes from search_* tools.
        assert "agent.actions" in store.calls[0]["endpoint"]

    def test_country_and_category_compose_where(self):
        store = _FakeStore()
        with patch("core.vector_store.get_vector_store", return_value=store):
            GetHistoricalActions().execute({
                "query": "x", "country": "Mexico", "category": "Market Risk",
            })
        assert store.calls[0]["where"] == {
            "country":  {"$eq": "Mexico"},
            "category": {"$eq": "Market Risk"},
        }

    def test_invalid_top_k_returns_explicit_failure(self):
        r = GetHistoricalActions().execute({"query": "x", "top_k": "ten"})
        assert r.ok is False
        assert "integer" in r.error

    def test_top_k_clamped(self):
        store = _FakeStore()
        with patch("core.vector_store.get_vector_store", return_value=store):
            GetHistoricalActions().execute({"query": "x", "top_k": 999})
        # _MAX_TOP_K = 15
        assert store.calls[0]["n_results"] == 15

    def test_empty_results_returns_helpful_message(self):
        with patch("core.vector_store.get_vector_store",
                   return_value=_FakeStore(results=[])):
            r = GetHistoricalActions().execute({
                "query": "rare query", "country": "Brazil",
            })
        assert r.ok is True
        assert "No historical actions matched" in r.content
        assert "Brazil" in r.content
        assert r.structured["hits"] == []

    def test_vector_store_exception_surfaced_as_failure(self):
        with patch("core.vector_store.get_vector_store",
                   side_effect=RuntimeError("chroma down")):
            r = GetHistoricalActions().execute({"query": "x"})
        assert r.ok is False
        assert "vector_store" in r.error.lower()


# ──────────────────────────────────────────────────────────────────
# score_action_coherence
# ──────────────────────────────────────────────────────────────────

class TestScoreActionCoherence:

    def test_empty_action_text_fails(self):
        r = ScoreActionCoherence().execute({"action_text": "  "})
        assert r.ok is False
        assert "required" in r.error.lower()

    def test_no_history_returns_no_history_interpretation(self):
        """No matches in actions_history → 0.0 + interpretation flag
        so the agent knows it's NOT 'this is novel' but rather 'we
        have nothing to compare against'. Important distinction —
        the agent treats them differently."""
        with patch("core.vector_store.get_vector_store",
                   return_value=_FakeStore(results=[])):
            r = ScoreActionCoherence().execute({
                "action_text": "Hedge MXN forwards 50%",
                "country": "Mexico", "category": "Market Risk",
            })
        assert r.ok is True
        assert r.structured["coherence_score"] == 0.0
        assert r.structured["interpretation"] == "no_history"

    def test_strong_precedent_when_many_close_matches(self):
        """K of K neighbors with distance <0.6 → coherence very high
        (strong_ratio=1.0 weighted 0.6 + nearest_sim weighted 0.4).
        Uses the full K=10 corpus so the denominator-fix (strong_count
        / K, not / len(distances)) doesn't penalize the genuinely-
        strong case."""
        store = _FakeStore(results=[
            _Hit(f"a{i}", "Tighten MXN forwards", {"country": "Mexico"}, 0.1 + 0.04*i)
            for i in range(_NEIGHBORS_K)
        ])
        with patch("core.vector_store.get_vector_store", return_value=store):
            r = ScoreActionCoherence().execute({
                "action_text": "Tighten MXN forwards",
                "country": "Mexico", "category": "Market Risk",
            })
        assert r.ok is True
        # All K distances are < 0.6 → strong_ratio = K/K = 1.0.
        # nearest_sim = 1.0 - 0.10 = 0.9. Coherence = 0.6*1 + 0.4*0.9 = 0.96.
        assert r.structured["coherence_score"] >= 0.9
        assert r.structured["interpretation"] == "strong_precedent"
        assert r.structured["strong_match_count"] == _NEIGHBORS_K

    def test_sparse_corpus_not_inflated_to_strong(self):
        """Regression pin for the denominator bug: with only 2 hits in
        the corpus — both strong matches — the OLD formula computed
        strong_ratio = 2/2 = 1.0 and called the result strong_precedent
        even though we have basically no comparison data. The fix
        anchors on K=10 (what we ASKED for), so 2/10 = 0.2 and the
        result downgrades to 'some_precedent' or 'novel'."""
        store = _FakeStore(results=[
            _Hit("a1", "MXN hedge action", {"country": "Mexico"}, 0.15),
            _Hit("a2", "MXN hedge action", {"country": "Mexico"}, 0.2),
        ])
        with patch("core.vector_store.get_vector_store", return_value=store):
            r = ScoreActionCoherence().execute({
                "action_text": "MXN hedge action",
                "country": "Mexico", "category": "Market Risk",
            })
        # strong_ratio = 2/10 = 0.2, nearest_sim = 1-0.15 = 0.85.
        # Coherence = 0.6*0.2 + 0.4*0.85 = 0.46 → some_precedent.
        assert r.ok is True
        assert r.structured["interpretation"] != "strong_precedent", (
            "2 strong matches in a corpus of 2 must NOT be called "
            "strong_precedent — that's the denominator-bug regression."
        )
        assert r.structured["strong_match_count"] == 2

    def test_novel_when_all_matches_far(self):
        """All 5 neighbors distance >0.8 → strong_ratio=0,
        nearest_sim modest. Coherence low → 'novel' interpretation."""
        store = _FakeStore(results=[
            _Hit(f"a{i}", "Unrelated topic", {}, 0.85 + 0.02*i)
            for i in range(5)
        ])
        with patch("core.vector_store.get_vector_store", return_value=store):
            r = ScoreActionCoherence().execute({
                "action_text": "Some totally different action",
            })
        assert r.ok is True
        assert r.structured["coherence_score"] < 0.3
        assert r.structured["interpretation"] == "novel"
        assert r.structured["strong_match_count"] == 0

    def test_mixed_some_precedent(self):
        """Some-but-not-all close matches → middle interpretation."""
        store = _FakeStore(results=[
            _Hit("a1", "x", {}, 0.3),   # strong
            _Hit("a2", "x", {}, 0.4),   # strong
            _Hit("a3", "x", {}, 0.7),   # NOT strong
            _Hit("a4", "x", {}, 0.8),   # NOT strong
            _Hit("a5", "x", {}, 0.9),   # NOT strong
        ])
        with patch("core.vector_store.get_vector_store", return_value=store):
            r = ScoreActionCoherence().execute({"action_text": "x"})
        # strong_ratio = 2/_NEIGHBORS_K = 2/10 = 0.2, nearest_sim = 0.7.
        # Coherence = 0.6*0.2 + 0.4*0.7 = 0.40 → "some_precedent" (>=0.3).
        score = r.structured["coherence_score"]
        assert 0.3 <= score < 0.7
        assert r.structured["interpretation"] == "some_precedent"

    def test_strong_match_threshold_is_load_bearing(self):
        """If someone bumps `_STRONG_MATCH_THRESHOLD`, all calibration
        downstream shifts. Pin the current value so the change has to
        be deliberate."""
        assert _STRONG_MATCH_THRESHOLD == 0.6

    def test_filter_wiring_to_vector_store(self):
        store = _FakeStore()
        with patch("core.vector_store.get_vector_store", return_value=store):
            ScoreActionCoherence().execute({
                "action_text": "x",
                "country": "Japan", "category": "Liquidity Risk",
            })
        assert store.calls[0]["where"] == {
            "country":  {"$eq": "Japan"},
            "category": {"$eq": "Liquidity Risk"},
        }

    def test_vector_store_exception_surfaced(self):
        with patch("core.vector_store.get_vector_store",
                   side_effect=RuntimeError("chroma offline")):
            r = ScoreActionCoherence().execute({"action_text": "x"})
        assert r.ok is False
        assert "vector_store" in r.error.lower()


# ──────────────────────────────────────────────────────────────────
# Integration: are the tools registered in the actions yaml block?
# ──────────────────────────────────────────────────────────────────

class TestAgentRuntimeYaml:
    """The actions agent's tool whitelist in agent_runtime.yaml must
    list each of the three new tools. If a refactor drops one, the
    agent silently loses capability — these assertions catch that."""

    def _load_actions_tools(self):
        import yaml
        with open(ROOT / "config" / "agent_runtime.yaml",
                  "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        return set((cfg.get("agents") or {}).get("actions", {}).get("tools") or [])

    def test_all_three_tools_in_yaml(self):
        tools = self._load_actions_tools()
        for t in ("get_cash_positions", "get_historical_actions",
                  "score_action_coherence"):
            assert t in tools, f"actions agent yaml missing tool: {t}"
