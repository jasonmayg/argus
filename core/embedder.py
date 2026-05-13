"""
Embedding provider abstraction for the Phase 2 vector store.

Mirrors the Phase 0 LLM router design — multiple providers, configurable
default + fallback, version-tagged outputs so the system can detect when a
model swap requires re-embedding.

Providers live under `core/embedders/`. Routing is governed by
`config/rag_config.yaml`. The factory `get_embedder()` returns the
configured default; `get_embedder("openai")` etc. lets call sites pick
explicitly.

Why split provider from store: backfill jobs may want to compare embeddings
across providers; future fine-tuning will plug in a new provider without
touching `vector_store.py`.
"""

from __future__ import annotations

import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

ROOT = Path(__file__).resolve().parent.parent
_CONFIG_PATH = Path(os.getenv("RAG_CONFIG", ROOT / "config" / "rag_config.yaml"))


# ─────────────────────────────────────────────────────────────────────────
# Errors and dataclasses
# ─────────────────────────────────────────────────────────────────────────
class EmbedderError(RuntimeError):
    """Raised when an embedding call fails. Callers decide whether to fall
    back to a different provider or skip the embed (best-effort writes)."""


@dataclass
class EmbeddingResult:
    vectors: list[list[float]]
    model: str
    version: str              # e.g. "sentence_transformer:all-MiniLM-L6-v2:v1"
    latency_ms: int


# ─────────────────────────────────────────────────────────────────────────
# Base class
# ─────────────────────────────────────────────────────────────────────────
class EmbeddingProvider(ABC):
    name: str = "<base>"      # short id, e.g. "sentence_transformer", "openai"
    model: str = "<unset>"    # specific model name
    version: str = "v1"       # bump when training data or model changes

    @abstractmethod
    def embed(self, texts: list[str]) -> EmbeddingResult: ...

    def is_available(self) -> bool:
        """Override if provider requires an API key etc."""
        return True

    @property
    def embedder_version(self) -> str:
        """Stable identifier used as Chroma metadata. If this string changes
        between two runs, vector_store.audit can detect the mismatch and
        flag rows that need re-embedding."""
        return f"{self.name}:{self.model}:{self.version}"


# ─────────────────────────────────────────────────────────────────────────
# Factory
# ─────────────────────────────────────────────────────────────────────────
_config_cache: Optional[dict] = None
_provider_cache: dict[str, EmbeddingProvider] = {}


def _load_config() -> dict:
    global _config_cache
    if _config_cache is not None:
        return _config_cache
    if not _CONFIG_PATH.exists():
        raise FileNotFoundError(f"RAG config not found: {_CONFIG_PATH}")
    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        _config_cache = yaml.safe_load(f) or {}
    return _config_cache


def reload_config() -> None:
    global _config_cache, _provider_cache
    _config_cache = None
    _provider_cache = {}


def get_embedder(name: Optional[str] = None) -> EmbeddingProvider:
    """Return the configured default embedder, or a specific one by short id.

    Lazy-imported per-provider to avoid loading torch when only OpenAI is
    needed (and vice versa).
    """
    cfg = _load_config().get("embedder") or {}
    if name is None:
        name = cfg.get("default", "sentence_transformer")

    if name in _provider_cache:
        return _provider_cache[name]

    providers_cfg = cfg.get("providers") or {}
    if name not in providers_cfg:
        raise ValueError(
            f"embedder {name!r} not in rag_config.yaml. "
            f"Available: {sorted(providers_cfg.keys())}"
        )
    pcfg = providers_cfg[name]
    ptype = pcfg.get("type") or name

    if ptype == "sentence_transformer":
        from core.embedders.sentence_transformer_provider import SentenceTransformerProvider
        provider: EmbeddingProvider = SentenceTransformerProvider(
            model=pcfg["model"],
            version=pcfg.get("version", "v1"),
        )
    elif ptype == "openai":
        from core.embedders.openai_provider import OpenAIEmbeddingProvider
        provider = OpenAIEmbeddingProvider(
            model=pcfg["model"],
            api_key_env=pcfg.get("api_key_env", "OPENAI_API_KEY"),
            version=pcfg.get("version", "v1"),
        )
    else:
        raise ValueError(f"unknown embedder type {ptype!r}")

    _provider_cache[name] = provider
    return provider


def get_fallback_embedder() -> Optional[EmbeddingProvider]:
    cfg = _load_config().get("embedder") or {}
    fb = cfg.get("fallback")
    if not fb:
        return None
    try:
        return get_embedder(fb)
    except Exception:
        return None


def embed_with_fallback(texts: list[str]) -> EmbeddingResult:
    """Try the default embedder; on EmbedderError, try the configured
    fallback. Raises if both fail."""
    default = get_embedder()
    try:
        return default.embed(texts)
    except EmbedderError as e:
        fb = get_fallback_embedder()
        if fb is None or fb.name == default.name:
            raise
        return fb.embed(texts)


# ─────────────────────────────────────────────────────────────────────────
# Helper exposed for telemetry callers
# ─────────────────────────────────────────────────────────────────────────
def now_ms() -> int:
    return int(time.perf_counter() * 1000)
