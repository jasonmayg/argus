"""
OpenAI embedding provider (fallback / paid option).

Uses text-embedding-3-small by default — $0.02 per 1M tokens, 1536 dimensions.
A full backfill of ~10k news items costs roughly $0.06. Production use cost
is negligible for Argus's scale.

When to prefer this over sentence-transformers:
  - Phase 2.5 hasn't fine-tuned ST yet and OpenAI's general embeddings are
    still beating ours
  - Local CPU is overloaded by other workloads
  - Cold-start latency matters more than steady-state cost

Picked up by `core.embedder.get_embedder("openai")` or by configuring it as
the default in rag_config.yaml.
"""

from __future__ import annotations

import os
import time

import requests

from core.embedder import EmbeddingProvider, EmbeddingResult, EmbedderError


class OpenAIEmbeddingProvider(EmbeddingProvider):
    name = "openai"
    BASE_URL = "https://api.openai.com/v1/embeddings"

    def __init__(
        self,
        model: str = "text-embedding-3-small",
        api_key_env: str = "OPENAI_API_KEY",
        version: str = "v1",
        timeout_sec: int = 30,
    ):
        self.model = model
        self.version = version
        self._api_key_env = api_key_env
        self.timeout_sec = timeout_sec

    @property
    def api_key(self) -> str:
        return os.getenv(self._api_key_env, "").strip()

    def is_available(self) -> bool:
        return bool(self.api_key)

    def embed(self, texts: list[str]) -> EmbeddingResult:
        if not texts:
            return EmbeddingResult(vectors=[], model=self.model,
                                   version=self.embedder_version, latency_ms=0)
        if not self.api_key:
            raise EmbedderError(f"{self._api_key_env} not set")

        # OpenAI accepts up to ~2048 inputs per request; we cap at 256 to keep
        # individual requests responsive and to make rate-limit recovery easier.
        BATCH = 256
        all_vectors: list[list[float]] = []
        t0 = time.perf_counter()
        try:
            for start in range(0, len(texts), BATCH):
                batch = texts[start:start + BATCH]
                r = requests.post(
                    self.BASE_URL,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={"model": self.model, "input": batch},
                    timeout=self.timeout_sec,
                )
                if r.status_code != 200:
                    raise EmbedderError(
                        f"OpenAI embeddings HTTP {r.status_code}: "
                        f"{(r.text or '')[:200]}"
                    )
                data = r.json()
                for item in data.get("data", []):
                    all_vectors.append(item["embedding"])
        except requests.RequestException as e:
            raise EmbedderError(f"network error: {e}") from e

        latency_ms = int((time.perf_counter() - t0) * 1000)
        return EmbeddingResult(
            vectors=all_vectors,
            model=self.model,
            version=self.embedder_version,
            latency_ms=latency_ms,
        )
