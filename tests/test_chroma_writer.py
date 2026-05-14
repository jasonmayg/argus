"""
Unit tests for ``core.chroma_writer`` (Phase 2E dual-write helpers).

The tests stub out ``core.chroma_writer._store`` so they never touch the
real ChromaDB / sentence_transformers stack. Each test re-imports the
module via ``importlib.reload`` so the ``lru_cache``'d config helper
starts fresh and tests can swap the config file or env vars without
leaking state across cases.

Run:
    pytest tests/test_chroma_writer.py -v
"""

from __future__ import annotations

import importlib
import sys
import textwrap
from pathlib import Path
from typing import Iterator

import pytest


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ────────────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────────────

@pytest.fixture
def chroma_writer(monkeypatch, tmp_path: Path) -> Iterator:
    """Yield a freshly-loaded ``chroma_writer`` module with a writable
    temporary config path so tests can rewrite the dual_write block
    without polluting the real ``config/rag_config.yaml``.
    """
    cfg_path = tmp_path / "rag_config.yaml"
    cfg_path.write_text(textwrap.dedent("""
        dual_write:
          enabled: true
          news: true
          regulatory: true
          alerts: true
          actions: true
    """).lstrip())

    # Drop any previously-imported instance so the lru_cache is empty
    # and our patched _CONFIG_PATH takes effect.
    sys.modules.pop("core.chroma_writer", None)
    import core.chroma_writer as cw
    monkeypatch.setattr(cw, "_CONFIG_PATH", cfg_path, raising=True)
    cw.reset_config_cache()
    yield cw


def _rewrite_config(cw, cfg_path: Path, body: str) -> None:
    cfg_path.write_text(textwrap.dedent(body).lstrip())
    cw.reset_config_cache()


class _FakeStore:
    """Captures every dual_write_* call so the test can assert against it."""
    def __init__(self) -> None:
        self.news: list[tuple] = []
        self.regulatory: list[tuple] = []
        self.alerts: list[tuple] = []
        self.actions: list[dict] = []

    def add_news(self, news_id, row):
        self.news.append((news_id, row))
        return True

    def add_regulatory(self, reg_id, row):
        self.regulatory.append((reg_id, row))
        return True

    def add_alerts(self, snapshot_date, alerts):
        self.alerts.append((snapshot_date, alerts))
        return len(alerts)

    def add_action_history(self, action):
        self.actions.append(action)
        return True


class _RaisingStore:
    """Used to assert fail-soft behaviour."""
    def add_news(self, *_, **__):       raise RuntimeError("chroma down")
    def add_regulatory(self, *_, **__): raise RuntimeError("chroma down")
    def add_alerts(self, *_, **__):     raise RuntimeError("chroma down")
    def add_action_history(self, *_, **__): raise RuntimeError("chroma down")


# ────────────────────────────────────────────────────────────────
# Config gating
# ────────────────────────────────────────────────────────────────

class TestEnabled:
    def test_default_on_when_yaml_present(self, chroma_writer):
        assert chroma_writer._enabled("news") is True
        assert chroma_writer._enabled("regulatory") is True
        assert chroma_writer._enabled("alerts") is True
        assert chroma_writer._enabled("actions") is True

    def test_unknown_source_defaults_on(self, chroma_writer):
        """Adding a new collection later shouldn't require a config bump."""
        assert chroma_writer._enabled("brand_new_collection") is True

    def test_global_kill_switch_disables_every_source(self, chroma_writer, tmp_path):
        _rewrite_config(chroma_writer, chroma_writer._CONFIG_PATH, """
            dual_write:
              enabled: false
              news: true
              regulatory: true
        """)
        assert chroma_writer._enabled("news") is False
        assert chroma_writer._enabled("regulatory") is False
        assert chroma_writer._enabled("alerts") is False

    def test_per_source_false_disables_only_that_source(self, chroma_writer):
        _rewrite_config(chroma_writer, chroma_writer._CONFIG_PATH, """
            dual_write:
              enabled: true
              news: false
              regulatory: true
        """)
        assert chroma_writer._enabled("news") is False
        assert chroma_writer._enabled("regulatory") is True

    def test_missing_config_file_disables_everything(self, chroma_writer, tmp_path, monkeypatch):
        """If we can't read the yaml at all we must not silently double-write."""
        monkeypatch.setattr(chroma_writer, "_CONFIG_PATH", tmp_path / "does_not_exist.yaml")
        chroma_writer.reset_config_cache()
        # The loader logs a WARNING and returns {"enabled": False}, so every
        # source ends up disabled. That's the conservative default.
        assert chroma_writer._enabled("news") is False
        assert chroma_writer._enabled("regulatory") is False

    def test_config_cache_is_invalidated_by_reset(self, chroma_writer):
        first = chroma_writer._dual_write_cfg()
        assert first["enabled"] is True
        _rewrite_config(chroma_writer, chroma_writer._CONFIG_PATH, """
            dual_write:
              enabled: false
        """)
        second = chroma_writer._dual_write_cfg()
        assert second["enabled"] is False


# ────────────────────────────────────────────────────────────────
# Happy path — store is reachable, source is enabled
# ────────────────────────────────────────────────────────────────

class TestDualWriteHappyPath:
    def test_news_forwards_to_store(self, chroma_writer, monkeypatch):
        fake = _FakeStore()
        monkeypatch.setattr(chroma_writer, "_store", lambda: fake)
        assert chroma_writer.dual_write_news(42, {"title": "t"}) is True
        assert fake.news == [(42, {"title": "t"})]

    def test_regulatory_forwards_to_store(self, chroma_writer, monkeypatch):
        fake = _FakeStore()
        monkeypatch.setattr(chroma_writer, "_store", lambda: fake)
        row = {"title": "Reg", "country": "USA"}
        assert chroma_writer.dual_write_regulatory(7, row) is True
        assert fake.regulatory == [(7, row)]

    def test_alerts_returns_count(self, chroma_writer, monkeypatch):
        fake = _FakeStore()
        monkeypatch.setattr(chroma_writer, "_store", lambda: fake)
        alerts = [{"title": "a1"}, {"title": "a2"}, {"title": "a3"}]
        assert chroma_writer.dual_write_alerts("2026-05-14", alerts) == 3
        assert fake.alerts == [("2026-05-14", alerts)]

    def test_action_history_forwards_to_store(self, chroma_writer, monkeypatch):
        fake = _FakeStore()
        monkeypatch.setattr(chroma_writer, "_store", lambda: fake)
        action = {"recommendation": "hedge", "country": "CN"}
        assert chroma_writer.dual_write_action_history(action) is True
        assert fake.actions == [action]


# ────────────────────────────────────────────────────────────────
# Short-circuits — do not call the store when there's nothing to do
# ────────────────────────────────────────────────────────────────

class TestShortCircuits:
    def test_alerts_empty_list_does_not_call_store(self, chroma_writer, monkeypatch):
        fake = _FakeStore()
        monkeypatch.setattr(chroma_writer, "_store", lambda: fake)
        assert chroma_writer.dual_write_alerts("2026-05-14", []) == 0
        assert fake.alerts == []  # store was never touched

    def test_news_disabled_source_skips_store(self, chroma_writer, monkeypatch):
        fake = _FakeStore()
        monkeypatch.setattr(chroma_writer, "_store", lambda: fake)
        _rewrite_config(chroma_writer, chroma_writer._CONFIG_PATH, """
            dual_write:
              enabled: true
              news: false
        """)
        assert chroma_writer.dual_write_news(99, {"title": "t"}) is False
        assert fake.news == []

    def test_global_kill_switch_skips_store(self, chroma_writer, monkeypatch):
        fake = _FakeStore()
        monkeypatch.setattr(chroma_writer, "_store", lambda: fake)
        _rewrite_config(chroma_writer, chroma_writer._CONFIG_PATH, """
            dual_write:
              enabled: false
        """)
        assert chroma_writer.dual_write_news(1,        {"title": "t"}) is False
        assert chroma_writer.dual_write_regulatory(1,  {"title": "t"}) is False
        assert chroma_writer.dual_write_alerts("d",    [{"title": "t"}]) == 0
        assert chroma_writer.dual_write_action_history({"recommendation": "r"}) is False
        assert fake.news == [] and fake.regulatory == [] and fake.alerts == [] and fake.actions == []


# ────────────────────────────────────────────────────────────────
# Fail-soft — store errors MUST be swallowed (Phase 2E contract)
# ────────────────────────────────────────────────────────────────

class TestFailSoft:
    def test_news_swallows_store_exception(self, chroma_writer, monkeypatch, caplog):
        monkeypatch.setattr(chroma_writer, "_store", lambda: _RaisingStore())
        with caplog.at_level("WARNING", logger="core.chroma_writer"):
            assert chroma_writer.dual_write_news(1, {"title": "t"}) is False
        assert any("news add failed" in r.message for r in caplog.records)

    def test_regulatory_swallows_store_exception(self, chroma_writer, monkeypatch, caplog):
        monkeypatch.setattr(chroma_writer, "_store", lambda: _RaisingStore())
        with caplog.at_level("WARNING", logger="core.chroma_writer"):
            assert chroma_writer.dual_write_regulatory(2, {"title": "t"}) is False
        assert any("regulatory add failed" in r.message for r in caplog.records)

    def test_alerts_swallows_store_exception(self, chroma_writer, monkeypatch, caplog):
        monkeypatch.setattr(chroma_writer, "_store", lambda: _RaisingStore())
        with caplog.at_level("WARNING", logger="core.chroma_writer"):
            assert chroma_writer.dual_write_alerts("d", [{"title": "t"}]) == 0
        assert any("alerts add failed" in r.message for r in caplog.records)

    def test_action_history_swallows_store_exception(self, chroma_writer, monkeypatch, caplog):
        monkeypatch.setattr(chroma_writer, "_store", lambda: _RaisingStore())
        with caplog.at_level("WARNING", logger="core.chroma_writer"):
            assert chroma_writer.dual_write_action_history({"recommendation": "r"}) is False
        assert any("action_history add failed" in r.message for r in caplog.records)

    def test_store_factory_failure_is_also_swallowed(self, chroma_writer, monkeypatch, caplog):
        """If _store() itself raises (e.g. chromadb import fails), the
        dual_write_* call must still return cleanly — embedder import
        errors in production should not abort a SQLite commit."""
        def broken_factory():
            raise RuntimeError("chromadb not installed")
        monkeypatch.setattr(chroma_writer, "_store", broken_factory)
        with caplog.at_level("WARNING", logger="core.chroma_writer"):
            assert chroma_writer.dual_write_news(1, {"title": "t"}) is False
            assert chroma_writer.dual_write_regulatory(1, {"title": "t"}) is False
            assert chroma_writer.dual_write_alerts("d", [{"title": "t"}]) == 0
            assert chroma_writer.dual_write_action_history({"recommendation": "r"}) is False
        assert sum(1 for r in caplog.records if "failed" in r.message) == 4
