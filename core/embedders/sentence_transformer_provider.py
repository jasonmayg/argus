"""
Local sentence-transformers embedding provider.

Default for Phase 2 because:
  - Free, no API quota
  - Runs on CPU (50ms / sentence batch-of-1, much faster batched)
  - Foundation for Phase 2.5 fine-tuning on Argus historical news

The model is lazily loaded on first embed() call. Loading takes 2-5 seconds
and pulls ~80MB of weights into memory. Subsequent embed() calls reuse the
loaded model.
"""

from __future__ import annotations

import time
from typing import Optional

from core.embedder import EmbeddingProvider, EmbeddingResult, EmbedderError


class SentenceTransformerProvider(EmbeddingProvider):
    name = "sentence_transformer"

    def __init__(self, model: str = "all-MiniLM-L6-v2", version: str = "v1"):
        self.model = model
        self.version = version
        self._model_obj = None         # lazy load

    def _load(self):
        if self._model_obj is not None:
            return self._model_obj
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:
            raise EmbedderError(
                "sentence-transformers not installed. "
                "Run: pip install sentence-transformers"
            ) from e
        try:
            self._model_obj = SentenceTransformer(self.model)
        except Exception as e:
            raise EmbedderError(f"failed to load model {self.model!r}: {e}") from e
        return self._model_obj

    def embed(self, texts: list[str]) -> EmbeddingResult:
        if not texts:
            return EmbeddingResult(vectors=[], model=self.model,
                                   version=self.embedder_version, latency_ms=0)
        model = self._load()
        t0 = time.perf_counter()
        try:
            arr = model.encode(texts, batch_size=64, show_progress_bar=False,
                               convert_to_numpy=True, normalize_embeddings=True)
        except Exception as e:
            raise EmbedderError(f"sentence-transformer encode failed: {e}") from e
        latency_ms = int((time.perf_counter() - t0) * 1000)
        return EmbeddingResult(
            vectors=[v.tolist() for v in arr],
            model=self.model,
            version=self.embedder_version,
            latency_ms=latency_ms,
        )
