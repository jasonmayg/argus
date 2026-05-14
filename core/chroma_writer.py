"""
Fail-soft Chroma dual-write helpers (Phase 2E).

Called from SQLite writers and snapshot-loaders so new records land in the
vector store in real time, eliminating the need to re-run the Phase 2A
backfill after every pipeline tick.

Every wrapper:
  * Reads ``dual_write`` from ``config/rag_config.yaml``; per-source can be
    disabled without touching code (kill-switch).
  * Catches any exception (embedder unreachable, Chroma locked, malformed
    row) and logs a WARNING — it MUST NOT raise back into the SQLite write
    path, since a Chroma hiccup must not abort a regulatory commit or
    classification run.
  * Returns the underlying writer's success indicator on the happy path, or
    a falsy default on skip / fail (so callers can log counts without
    branching on the disabled case).
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "rag_config.yaml"


@lru_cache(maxsize=1)
def _dual_write_cfg() -> dict:
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        return cfg.get("dual_write") or {}
    except Exception as exc:
        logger.warning(
            "chroma_writer: failed to load %s (%s) — dual-write disabled",
            _CONFIG_PATH, exc,
        )
        return {"enabled": False}


def reset_config_cache() -> None:
    """Test hook: drop the cached config so a yaml edit re-reads."""
    _dual_write_cfg.cache_clear()


def _enabled(source: str) -> bool:
    cfg = _dual_write_cfg()
    if cfg.get("enabled") is False:
        return False
    per_source = cfg.get(source)
    # If the per-source key is explicitly False, skip; otherwise default-on
    # so adding a new collection doesn't require a config bump.
    return per_source is not False


def _store():
    # Import lazily so importing this module doesn't pull in chromadb /
    # sentence_transformers on every codepath that touches a writer.
    from core.vector_store import get_vector_store
    return get_vector_store()


def dual_write_news(news_id: Any, row: dict) -> bool:
    if not _enabled("news"):
        return False
    try:
        return _store().add_news(news_id, row)
    except Exception as exc:
        logger.warning("chroma_writer: news add failed for id=%s (%s)", news_id, exc)
        return False


def dual_write_regulatory(reg_id: Any, row: dict) -> bool:
    if not _enabled("regulatory"):
        return False
    try:
        return _store().add_regulatory(reg_id, row)
    except Exception as exc:
        logger.warning(
            "chroma_writer: regulatory add failed for id=%s (%s)", reg_id, exc,
        )
        return False


def dual_write_alerts(snapshot_date: str, alerts: list[dict]) -> int:
    if not _enabled("alerts"):
        return 0
    if not alerts:
        return 0
    try:
        return _store().add_alerts(snapshot_date, alerts)
    except Exception as exc:
        logger.warning(
            "chroma_writer: alerts add failed for snapshot %s (%s)",
            snapshot_date, exc,
        )
        return 0


def dual_write_action_history(action: dict) -> bool:
    if not _enabled("actions"):
        return False
    try:
        return _store().add_action_history(action)
    except Exception as exc:
        logger.warning(
            "chroma_writer: action_history add failed (%s)", exc,
        )
        return False
