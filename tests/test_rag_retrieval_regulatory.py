"""
Unit tests for the chat/brief RAG retrieval-scope expansion that adds
regulatory collection alongside news + alerts.

The motivating bug: the `news` collection only holds Fed news (13 items
in production), while the bulk of structured signal (ECB / BoE / BoJ /
OFAC, ~300 items) lives in `regulatory`. Without these tests, future
edits could drop the regulatory branch silently and Argus chat would
again be unable to surface a question like "what did the ECB do this
month?" — there'd be no error, the retrieval would just return empty.

Stubs `core.rag_context.get_vector_store` with a fake so the tests
never load sentence_transformers / chromadb.

Run:
    pytest tests/test_rag_retrieval_regulatory.py -v
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ─────────────────────────────────────────────────────────────────
# Config isolation
# ─────────────────────────────────────────────────────────────────
# build_chat_context / build_brief_context read endpoint config through
# a module-level cache (`core.rag_context._CONFIG_CACHE`). Mutating that
# cache directly works but leaks across tests if any assertion fails
# before a manual reset. Using monkeypatch.setattr means pytest restores
# the original value automatically, even on failure — autouse so every
# test in this file is isolated without per-test boilerplate.
@pytest.fixture(autouse=True)
def reset_rag_config_cache(monkeypatch):
    import core.rag_context as rc
    monkeypatch.setattr(rc, "_CONFIG_CACHE", None, raising=False)


# ─────────────────────────────────────────────────────────────────
# Fake vector store
# ─────────────────────────────────────────────────────────────────

@dataclass
class _Hit:
    """Matches the SearchHit shape rag_context._news_line etc. consume."""
    id: str
    document: str
    metadata: dict
    distance: float


class _FakeStore:
    """Lets each test configure per-collection hit lists; records every
    call so tests can also assert which collections were queried."""

    def __init__(
        self,
        news: Optional[list[_Hit]] = None,
        alerts: Optional[list[_Hit]] = None,
        regulatory: Optional[list[_Hit]] = None,
        alerts_count: int = 100,
    ) -> None:
        self._news = news or []
        self._alerts = alerts or []
        self._regulatory = regulatory or []
        self._alerts_count = alerts_count
        self.calls: dict[str, int] = {"news": 0, "alerts": 0, "regulatory": 0}

    def count(self, name: str) -> int:
        if name == "alerts":
            return self._alerts_count
        return 0

    def search_news(self, query, n_results=None, **_):
        self.calls["news"] += 1
        return self._news[: (n_results or len(self._news))]

    def search_alerts(self, query, n_results=None, **_):
        self.calls["alerts"] += 1
        return self._alerts[: (n_results or len(self._alerts))]

    def search_regulatory(self, query, n_results=None, **_):
        self.calls["regulatory"] += 1
        return self._regulatory[: (n_results or len(self._regulatory))]


def _ecb_hit() -> _Hit:
    return _Hit(
        id="reg_123",
        document="ECB cuts deposit facility rate by 25bp\nWHY: cooling inflation",
        metadata={
            "country": "Eurozone", "category": "Monetary Policy",
            "severity": "High", "effective_date": "2026-06-01",
        },
        distance=0.18,
    )


def _ofac_hit() -> _Hit:
    return _Hit(
        id="reg_231",
        document="OFAC designates networks supplying UAV components to Iran",
        metadata={
            "country": "USA", "category": "Sanctions",
            "severity": "High",
        },
        distance=0.21,
    )


# ─────────────────────────────────────────────────────────────────
# Tests for build_chat_context
# ─────────────────────────────────────────────────────────────────

class TestChatContextIncludesRegulatory:
    """The chat retrieval branch is the one most likely to silently drop
    regulatory hits — chat users phrase questions broadly ("what did
    ECB do") and have no way to know retrieval came back empty."""

    def test_regulatory_hits_are_queried(self):
        from core.rag_context import build_chat_context
        store = _FakeStore(regulatory=[_ecb_hit(), _ofac_hit()])
        with patch("core.rag_context.get_vector_store", return_value=store):
            ctx = build_chat_context("what did the ECB do recently?")

        assert store.calls["regulatory"] == 1, \
            "regulatory collection must be queried for chat"
        assert ctx.regulatory_hits == 2
        assert ctx.used is True

    def test_regulatory_renders_with_R_tag(self):
        import core.rag_context as rc
        store = _FakeStore(regulatory=[_ecb_hit()])
        with patch("core.rag_context.get_vector_store", return_value=store):
            ctx = rc.build_chat_context("ECB rate")

        assert "[R1]" in ctx.prompt_text, "regulatory items must use R# tag"
        assert "Retrieved regulatory changes" in ctx.prompt_text
        # Country/category metadata surfaces in the rendered line
        assert "Eurozone" in ctx.prompt_text
        assert "Monetary Policy" in ctx.prompt_text

    def test_regulatory_citation_recorded(self):
        import core.rag_context as rc
        store = _FakeStore(regulatory=[_ofac_hit()])
        with patch("core.rag_context.get_vector_store", return_value=store):
            ctx = rc.build_chat_context("sanctions")

        reg_citations = [c for c in ctx.citations if c.collection == "regulatory"]
        assert len(reg_citations) == 1
        assert reg_citations[0].id == "reg_231"

    def test_only_regulatory_hits_still_marks_used(self):
        """Edge case: news + alerts return empty but regulatory has hits.
        Before this change, used=False meant /api/chat fell back to the
        legacy static summary, which loses the regulatory signal entirely."""
        import core.rag_context as rc
        store = _FakeStore(news=[], alerts=[], regulatory=[_ecb_hit()])
        with patch("core.rag_context.get_vector_store", return_value=store):
            ctx = rc.build_chat_context("ECB policy")

        assert ctx.used is True
        assert ctx.news_hits == 0
        assert ctx.alerts_hits == 0
        assert ctx.regulatory_hits == 1

    def test_empty_everything_marks_unused(self):
        """If all three collections return empty, used must still be False
        so the caller falls back to the legacy news summary."""
        import core.rag_context as rc
        store = _FakeStore(news=[], alerts=[], regulatory=[])
        with patch("core.rag_context.get_vector_store", return_value=store):
            ctx = rc.build_chat_context("anything")

        assert ctx.used is False
        assert ctx.regulatory_hits == 0

    def test_regulatory_error_does_not_drop_news(self):
        """Phase 2 best-effort contract: a failure in one collection
        must not poison the others. If regulatory.search throws, news
        results must still surface."""
        import core.rag_context as rc

        class _PartialFailStore(_FakeStore):
            def search_regulatory(self, *_args, **_kwargs):
                raise RuntimeError("collection unavailable")

        store = _PartialFailStore(
            news=[_Hit("news_1", "Fed cuts rates", {"date": "2026-05-15"}, 0.2)],
        )
        with patch("core.rag_context.get_vector_store", return_value=store):
            ctx = rc.build_chat_context("Fed rates")

        assert ctx.news_hits == 1
        assert ctx.regulatory_hits == 0
        assert ctx.used is True

    def test_regulatory_top_k_zero_skips_collection(self, monkeypatch):
        """Operational kill switch: setting regulatory_top_k=0 in config
        must make the helper skip the search call entirely — no Chroma
        round-trip, no rag_queries telemetry write."""
        import core.rag_context as rc
        # Inject config that disables regulatory via monkeypatch so the
        # cache is restored automatically at teardown, even if an
        # assertion below fails.
        monkeypatch.setattr(rc, "_CONFIG_CACHE", {
            "endpoints": {
                "chat": {
                    "enabled": True,
                    "news_top_k": 5, "alerts_top_k": 0,
                    "regulatory_top_k": 0, "alerts_min_count": 5,
                }
            }
        })
        store = _FakeStore(
            news=[_Hit("news_1", "Fed cuts", {}, 0.2)],
            regulatory=[_ecb_hit()],
        )
        with patch("core.rag_context.get_vector_store", return_value=store):
            ctx = rc.build_chat_context("anything")

        assert store.calls["regulatory"] == 0, \
            "regulatory_top_k=0 must skip the call entirely"
        assert ctx.regulatory_hits == 0


# ─────────────────────────────────────────────────────────────────
# Tests for build_brief_context (mirrors above but with persona path)
# ─────────────────────────────────────────────────────────────────

class TestBriefContextIncludesRegulatory:
    def test_regulatory_hits_in_brief(self):
        import core.rag_context as rc
        store = _FakeStore(regulatory=[_ecb_hit()])
        # build_brief_context needs a persona for query terms; use fx_manager
        # which has known risk_categories mapped via _persona_query_terms.
        with patch("core.rag_context.get_vector_store", return_value=store):
            ctx = rc.build_brief_context(persona="fx_manager")

        assert store.calls["regulatory"] == 1
        assert ctx.regulatory_hits == 1
        assert "[R1]" in ctx.prompt_text
        assert "Relevant regulatory changes" in ctx.prompt_text
