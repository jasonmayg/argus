"""
Unit tests for the search_news / search_alerts / search_regulatory tools.

All three share the same shape — they wrap `core.vector_store.search_*`,
render hits as `[N#]` / `[A#]` / `[R#]` lines, and pass `request_id`
through for telemetry. Tests stub the vector_store factory so nothing
loads sentence_transformers or chromadb.

Run:
    pytest tests/test_tool_search_collections.py -v
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

from core.tools.search_alerts import SearchAlerts
from core.tools.search_news import SearchNews
from core.tools.search_regulatory import SearchRegulatory


# ──────────────────────────────────────────────────────────────────
# Fake vector store
# ──────────────────────────────────────────────────────────────────

@dataclass
class _Hit:
    id: str
    document: str
    metadata: dict
    distance: float


class _FakeStore:
    """Records every search_* call so tests can assert on `where`
    filters and `request_id` forwarding too."""

    def __init__(self, news: Optional[List[_Hit]] = None,
                 alerts: Optional[List[_Hit]] = None,
                 regulatory: Optional[List[_Hit]] = None) -> None:
        self._news = news or []
        self._alerts = alerts or []
        self._regulatory = regulatory or []
        self.calls: List[Dict[str, Any]] = []

    def _record(self, name, query, n_results, where, request_id, endpoint):
        self.calls.append({
            "collection": name, "query": query, "n_results": n_results,
            "where": where, "request_id": request_id, "endpoint": endpoint,
        })

    def search_news(self, query, n_results=None, where=None,
                    request_id=None, endpoint=None):
        self._record("news", query, n_results, where, request_id, endpoint)
        return self._news[: (n_results or len(self._news))]

    def search_alerts(self, query, n_results=None, where=None,
                      request_id=None, endpoint=None):
        self._record("alerts", query, n_results, where, request_id, endpoint)
        return self._alerts[: (n_results or len(self._alerts))]

    def search_regulatory(self, query, n_results=None, where=None,
                          request_id=None, endpoint=None):
        self._record("regulatory", query, n_results, where, request_id, endpoint)
        return self._regulatory[: (n_results or len(self._regulatory))]


# ──────────────────────────────────────────────────────────────────
# search_news
# ──────────────────────────────────────────────────────────────────

class TestSearchNews:
    def test_happy_path_renders_N_tags(self):
        store = _FakeStore(news=[
            _Hit("n1", "Fed cuts rates by 25bp", {"date": "2026-05-15", "risk_category": "Monetary Policy"}, 0.1),
            _Hit("n2", "ECB holds steady",       {"date": "2026-05-14"}, 0.2),
        ])
        with patch("core.vector_store.get_vector_store", return_value=store):
            r = SearchNews(request_id="rid_xyz").execute({"query": "rates"})

        assert r.ok is True
        assert "[N1]" in r.content and "[N2]" in r.content
        assert "Monetary Policy" in r.content     # metadata surfaces in line
        assert r.structured["hits"][0]["id"] == "n1"

    def test_empty_query_fails(self):
        r = SearchNews().execute({"query": "   "})
        assert r.ok is False
        assert "required" in r.error.lower()

    def test_top_k_clamped_and_passed(self):
        """top_k=99 must clamp to the tool's max; vector_store sees the
        clamped value."""
        store = _FakeStore()
        with patch("core.vector_store.get_vector_store", return_value=store):
            SearchNews().execute({"query": "x", "top_k": 999})
        assert store.calls[0]["n_results"] <= 20    # _MAX_TOP_K

    def test_since_becomes_where_clause(self):
        store = _FakeStore()
        with patch("core.vector_store.get_vector_store", return_value=store):
            SearchNews().execute({"query": "x", "since": "2026-04-01"})
        assert store.calls[0]["where"] == {"date": {"$gte": "2026-04-01"}}

    def test_request_id_threaded_to_telemetry(self):
        """Tools accept a request_id from the agent so per-tool rag
        rows join the agent_executions row in /api/rag/stats."""
        store = _FakeStore()
        with patch("core.vector_store.get_vector_store", return_value=store):
            SearchNews(request_id="abc123").execute({"query": "x"})
        assert store.calls[0]["request_id"] == "abc123"
        # endpoint string is what /api/rag/stats groups by
        assert "agent.intelligence" in store.calls[0]["endpoint"]

    def test_empty_results_returns_helpful_message(self):
        with patch("core.vector_store.get_vector_store", return_value=_FakeStore()):
            r = SearchNews().execute({"query": "needle"})
        assert r.ok is True
        assert "No news matched" in r.content
        assert r.structured["hits"] == []

    def test_vector_store_exception_surfaced_as_failure(self):
        with patch("core.vector_store.get_vector_store",
                   side_effect=RuntimeError("chroma down")):
            r = SearchNews().execute({"query": "x"})
        assert r.ok is False
        assert "vector_store error" in r.error


# ──────────────────────────────────────────────────────────────────
# search_alerts
# ──────────────────────────────────────────────────────────────────

class TestSearchAlerts:
    def test_country_filter_in_where(self):
        store = _FakeStore()
        with patch("core.vector_store.get_vector_store", return_value=store):
            SearchAlerts().execute({"query": "x", "country": "China"})
        assert store.calls[0]["where"] == {"country": {"$eq": "China"}}

    def test_min_score_filter_in_where(self):
        store = _FakeStore()
        with patch("core.vector_store.get_vector_store", return_value=store):
            SearchAlerts().execute({"query": "x", "min_score": 70})
        assert store.calls[0]["where"] == {"score": {"$gte": 70.0}}

    def test_invalid_min_score_rejected(self):
        r = SearchAlerts().execute({"query": "x", "min_score": "abc"})
        assert r.ok is False
        assert "min_score" in r.error

    def test_renders_A_tags_and_metadata(self):
        store = _FakeStore(alerts=[
            _Hit("a1", "Yuan slides on PBOC fixing",
                 {"country": "China", "category_label": "FX Volatility", "score": 78}, 0.15),
        ])
        with patch("core.vector_store.get_vector_store", return_value=store):
            r = SearchAlerts().execute({"query": "yuan"})
        assert "[A1]" in r.content
        assert "China" in r.content and "FX Volatility" in r.content

    def test_invalid_top_k_returns_explicit_failure(self):
        """Regression: an LLM-supplied non-numeric `top_k` must produce
        a specific 'must be an integer' failure rather than a generic
        'tool raised: ValueError' from int() blowing up."""
        r = SearchAlerts().execute({"query": "x", "top_k": "ten"})
        assert r.ok is False
        assert "top_k" in r.error and "integer" in r.error

    def test_min_score_zero_surfaces_in_empty_details(self):
        """Regression: `min_score=0` is a valid filter ('any score');
        the empty-results message must still mention it so the agent
        knows the floor was applied. Previously a truthy check (`if
        min_score:`) dropped it from the details string."""
        store = _FakeStore()  # no hits
        with patch("core.vector_store.get_vector_store", return_value=store):
            r = SearchAlerts().execute({"query": "x", "min_score": 0})
        assert r.ok is True
        assert "score>=0" in r.content


# ──────────────────────────────────────────────────────────────────
# search_regulatory
# ──────────────────────────────────────────────────────────────────

class TestSearchRegulatory:
    def test_country_and_category_compose_where(self):
        store = _FakeStore()
        with patch("core.vector_store.get_vector_store", return_value=store):
            SearchRegulatory().execute({
                "query": "ecb", "country": "EU", "category": "Monetary Policy",
            })
        assert store.calls[0]["where"] == {
            "country": {"$eq": "EU"},
            "category": {"$eq": "Monetary Policy"},
        }

    def test_renders_R_tags_with_severity_effective_date(self):
        store = _FakeStore(regulatory=[
            _Hit("r1", "ECB cuts deposit facility rate by 25bp",
                 {"country": "EU", "category": "Monetary Policy",
                  "severity": "High", "effective_date": "2026-06-01"}, 0.1),
        ])
        with patch("core.vector_store.get_vector_store", return_value=store):
            r = SearchRegulatory().execute({"query": "ecb rate"})
        assert "[R1]" in r.content
        assert "EU" in r.content
        assert "Monetary Policy" in r.content
        assert "High" in r.content
        assert "2026-06-01" in r.content
