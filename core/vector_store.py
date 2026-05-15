"""
ARGUS vector store — Chroma client + collection helpers.

This is the only file that talks to Chroma. Routes and scripts call into the
`VectorStore` singleton via `get_vector_store()`.

Design contract:
  - SQLite remains the source of truth for news / regulatory. Chroma is a
    retrieval mirror.
  - Writes are best-effort. If embedding fails, the SQLite insert still
    succeeds — RAG quality degrades for that row but the pipeline doesn't.
  - Every retrieve goes through `_search()` which logs to rag_queries
    (best-effort telemetry).
  - Collections are versioned by embedder.embedder_version stored in
    metadata. chroma_audit can flag mismatches if the embedder is swapped.

Collection schemas (see Phase 2 design doc §2 for full detail):

  news:        title + summary;  meta: date, source, region, risk_category,
                                       risk_score, matrix_level, url
  regulatory:  title + before/after diff; meta: country, category, severity,
                                                effective_date, confidence
  alerts:      title + summary;  meta: snapshot_date, country, category, score
  actions_history: action card text; meta: persona, country, category, score,
                                           generated_at
"""

from __future__ import annotations

import os
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import yaml

from core.embedder import EmbeddingProvider, embed_with_fallback, get_embedder
from core.rag_telemetry import log_query_safe

ROOT = Path(__file__).resolve().parent.parent
_CONFIG_PATH = Path(os.getenv("RAG_CONFIG", ROOT / "config" / "rag_config.yaml"))

COLLECTION_NAMES = ("news", "regulatory", "alerts", "actions_history")


# ─────────────────────────────────────────────────────────────────────────
# Public types
# ─────────────────────────────────────────────────────────────────────────
@dataclass
class SearchHit:
    id: str
    document: str
    metadata: dict
    distance: float           # Chroma's distance score, lower = more similar


# ─────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────
def _load_config() -> dict:
    if not _CONFIG_PATH.exists():
        raise FileNotFoundError(f"RAG config not found: {_CONFIG_PATH}")
    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _trim(text: str, max_chars: int) -> str:
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _strip_html(text: str) -> str:
    """Cheap HTML strip — replaces tags with space, collapses whitespace.
    Good enough for news summaries; not a full parser."""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = (text.replace("&nbsp;", " ")
                .replace("&amp;", "&")
                .replace("&lt;", "<")
                .replace("&gt;", ">")
                .replace("&quot;", '"')
                .replace("&#39;", "'"))
    return re.sub(r"\s+", " ", text).strip()


def _sanitize_meta(d: dict) -> dict:
    """Chroma metadata must be scalars (str/int/float/bool/None).
    Convert lists/dicts to strings, drop None silently."""
    out: dict = {}
    for k, v in (d or {}).items():
        if v is None:
            continue
        if isinstance(v, (str, int, float, bool)):
            out[k] = v
        elif isinstance(v, (list, tuple, set)):
            out[k] = ", ".join(str(x) for x in v if x is not None)
        else:
            out[k] = str(v)
    return out


# ─────────────────────────────────────────────────────────────────────────
# Singleton
# ─────────────────────────────────────────────────────────────────────────
_store: Optional["VectorStore"] = None


def get_vector_store() -> "VectorStore":
    global _store
    if _store is None:
        _store = VectorStore.from_config()
    return _store


def reset_store() -> None:
    """Used by tests."""
    global _store
    _store = None


# ─────────────────────────────────────────────────────────────────────────
# Main class
# ─────────────────────────────────────────────────────────────────────────
class VectorStore:
    def __init__(
        self,
        persist_dir: Path,
        embedder: Optional[EmbeddingProvider] = None,
        collection_caps: Optional[dict[str, int]] = None,
        default_top_k: int = 10,
        max_distance: float = 1.2,
    ):
        import chromadb
        persist_dir.mkdir(parents=True, exist_ok=True)
        self.persist_dir = persist_dir
        self.client = chromadb.PersistentClient(path=str(persist_dir))
        self.embedder = embedder or get_embedder()
        self.collection_caps = collection_caps or {}
        self.default_top_k = default_top_k
        self.max_distance = max_distance
        self._collections: dict[str, Any] = {}

    @classmethod
    def from_config(cls) -> "VectorStore":
        cfg = _load_config()
        persist = ROOT / (cfg.get("storage") or {}).get("persist_dir", "data/chroma_db")
        ret = cfg.get("retrieval") or {}
        caps = {
            "news":       (cfg.get("collections") or {}).get("news_max_chars", 1500),
            "alerts":     (cfg.get("collections") or {}).get("alerts_max_chars", 800),
            "regulatory": (cfg.get("collections") or {}).get("regulatory_max_chars", 2000),
            "actions_history": 1500,
        }
        return cls(
            persist_dir=persist,
            collection_caps=caps,
            default_top_k=int(ret.get("default_top_k", 10)),
            max_distance=float(ret.get("max_distance", 1.2)),
        )

    # ── Collection management ────────────────────────────────────────
    def _get(self, name: str):
        if name not in COLLECTION_NAMES:
            raise ValueError(f"unknown collection {name!r}; valid: {COLLECTION_NAMES}")
        if name not in self._collections:
            self._collections[name] = self.client.get_or_create_collection(name=name)
        return self._collections[name]

    def count(self, name: str) -> int:
        return self._get(name).count()

    def collection_counts(self) -> dict[str, int]:
        return {n: self._get(n).count() for n in COLLECTION_NAMES}

    # ── Embedding ────────────────────────────────────────────────────
    def _embed(self, texts: list[str]) -> tuple[list[list[float]], str]:
        result = embed_with_fallback(texts)
        return result.vectors, result.version

    # ── Writers ──────────────────────────────────────────────────────
    def add_news(self, news_id: Any, item: dict) -> bool:
        """Upsert one news item. Returns True on success.

        `item` is the row dict from the SQLite `news` table joined with
        `risk_analysis` (matrix_level, risk_score, etc.). Missing keys are
        tolerated.
        """
        title = (item.get("title") or "").strip()
        summary = _strip_html(item.get("analysis_text") or item.get("content") or "")
        if not title:
            return False
        cap = self.collection_caps.get("news", 1500)
        doc = _trim(f"{title}\n\n{summary}", cap)

        meta = _sanitize_meta({
            "date":          item.get("date"),
            "source":        item.get("source") or "Federal Reserve",
            "region":        item.get("region"),
            "risk_category": item.get("risk_category"),
            "risk_score":    item.get("risk_score"),
            "matrix_level":  item.get("matrix_level"),
            "probability":   item.get("probability"),
            "impact":        item.get("impact"),
            "url":           item.get("link"),
            "llm_scored":    item.get("llm_scored"),
            "embedder_version": self.embedder.embedder_version,
        })
        return self._upsert("news", id=f"news_{news_id}", document=doc, metadata=meta)

    def add_regulatory(self, reg_id: Any, row: dict) -> bool:
        title = (row.get("title") or "").strip()
        if not title:
            return False
        before = (row.get("change_before") or "").strip()
        after = (row.get("change_after") or "").strip()
        impact = (row.get("treasury_impact") or "").strip()
        body_parts = [title]
        if before or after:
            body_parts.append(f"BEFORE: {before or 'n/a'}\nAFTER: {after or 'n/a'}")
        if impact:
            body_parts.append(f"IMPACT: {impact}")
        cap = self.collection_caps.get("regulatory", 2000)
        doc = _trim("\n\n".join(body_parts), cap)

        meta = _sanitize_meta({
            "country":        row.get("country"),
            "category":       row.get("category"),
            "severity":       row.get("severity"),
            "effective_date": row.get("effective_date"),
            "confidence":     row.get("confidence"),
            "is_regulatory":  row.get("is_regulatory"),
            "source_type":    row.get("source_type"),
            "url":            row.get("url"),
            "embedder_version": self.embedder.embedder_version,
        })
        return self._upsert("regulatory", id=f"reg_{reg_id}", document=doc, metadata=meta)

    def add_alerts(self, snapshot_date: str, alerts: list[dict]) -> int:
        """Bulk-upsert a Guardian alert batch. Returns count added/updated."""
        cap = self.collection_caps.get("alerts", 800)
        ids: list[str] = []
        docs: list[str] = []
        metas: list[dict] = []
        for i, a in enumerate(alerts):
            title = (a.get("title") or "").strip()
            if not title:
                continue
            summary = _strip_html(a.get("summary") or "")
            country = a.get("country") or "Global"
            category = a.get("category_label") or a.get("category") or "Unclassified"
            doc = _trim(f"{title}\n\n{country} | {category}\n{summary}", cap)
            uid = a.get("id") or f"{snapshot_date}_{i:04d}"
            ids.append(f"alert_{uid}")
            docs.append(doc)
            metas.append(_sanitize_meta({
                "snapshot_date":  snapshot_date,
                "country":        country,
                "category_label": category,
                "category_key":   a.get("category_key"),
                "score":          a.get("score"),
                "source":         a.get("source"),
                "seendate":       a.get("seendate"),
                "url":            a.get("url"),
                "embedder_version": self.embedder.embedder_version,
            }))
        if not ids:
            return 0
        if not self._bulk_upsert("alerts", ids, docs, metas):
            # Bulk upsert caught an exception inside Chroma; return 0 so
            # callers don't report mirrored counts for a write that never
            # happened. This preserves the fail-soft contract — the
            # exception is already logged by _bulk_upsert.
            return 0
        return len(ids)

    def add_action_history(self, action: dict) -> bool:
        persona = action.get("persona") or "unknown"
        country = action.get("country") or "Global"
        category = action.get("category") or "Unclassified"
        title = (action.get("top_title") or "").strip()
        why = (action.get("why_now") or "").strip()
        rec = (action.get("recommendation") or "").strip()
        if not rec:
            return False
        pros = ", ".join(action.get("recommendation_pros") or [])
        cons = ", ".join(action.get("recommendation_cons") or [])
        doc = _trim(
            f"{country} {category}: {title}\nWHY: {why}\nACTION: {rec}\n"
            f"PROS: {pros}\nCONS: {cons}",
            1500,
        )
        date = action.get("generated_at", "")[:10]
        slug = re.sub(r"[^a-z0-9_]+", "_", f"{persona}_{date}_{country}_{category}".lower()).strip("_")
        uid = f"action_{slug}_{uuid.uuid4().hex[:6]}"
        meta = _sanitize_meta({
            "persona":         persona,
            "country":         country,
            "category":        category,
            "effective_score": action.get("effective_score"),
            "generated_at":    action.get("generated_at"),
            "embedder_version": self.embedder.embedder_version,
        })
        return self._upsert("actions_history", id=uid, document=doc, metadata=meta)

    # ── Search ──────────────────────────────────────────────────────
    def search_news(self, query: str, n_results: Optional[int] = None,
                    where: Optional[dict] = None,
                    request_id: Optional[str] = None,
                    endpoint: Optional[str] = None) -> list[SearchHit]:
        return self._search("news", query, n_results, where, request_id, endpoint)

    def search_regulatory(self, query: str, n_results: Optional[int] = None,
                          where: Optional[dict] = None,
                          request_id: Optional[str] = None,
                          endpoint: Optional[str] = None) -> list[SearchHit]:
        return self._search("regulatory", query, n_results, where, request_id, endpoint)

    def search_alerts(self, query: str, n_results: Optional[int] = None,
                      where: Optional[dict] = None,
                      request_id: Optional[str] = None,
                      endpoint: Optional[str] = None) -> list[SearchHit]:
        return self._search("alerts", query, n_results, where, request_id, endpoint)

    def search_actions_history(self, query: str, n_results: Optional[int] = None,
                               where: Optional[dict] = None,
                               request_id: Optional[str] = None,
                               endpoint: Optional[str] = None) -> list[SearchHit]:
        return self._search("actions_history", query, n_results, where, request_id, endpoint)

    # ── Internals ───────────────────────────────────────────────────
    def _upsert(self, name: str, id: str, document: str, metadata: dict) -> bool:
        try:
            vecs, _ = self._embed([document])
            self._get(name).upsert(
                ids=[id],
                documents=[document],
                embeddings=vecs,
                metadatas=[metadata],
            )
            return True
        except Exception as e:
            import sys
            print(f"[vector_store] upsert {name}/{id} failed: {e}", file=sys.stderr)
            return False

    def _bulk_upsert(self, name: str, ids: list[str], docs: list[str], metas: list[dict]) -> bool:
        """Returns True on success, False on any embedder/Chroma failure.
        Callers that report mirrored counts (e.g. add_alerts) MUST gate
        their return on this — otherwise a swallowed exception leaves the
        wrapper claiming success it didn't deliver."""
        try:
            vecs, _ = self._embed(docs)
            self._get(name).upsert(ids=ids, documents=docs, embeddings=vecs, metadatas=metas)
            return True
        except Exception as e:
            import sys
            print(f"[vector_store] bulk_upsert {name} ({len(ids)}) failed: {e}", file=sys.stderr)
            return False

    def _search(self, name: str, query: str, n_results: Optional[int],
                where: Optional[dict], request_id: Optional[str],
                endpoint: Optional[str]) -> list[SearchHit]:
        n = n_results or self.default_top_k
        t0 = time.perf_counter()
        try:
            vecs, _ = self._embed([query])
            result = self._get(name).query(
                query_embeddings=vecs,
                n_results=n,
                where=where if where else None,
            )
        except Exception as e:
            import sys
            print(f"[vector_store] search {name} failed: {e}", file=sys.stderr)
            return []
        latency_ms = int((time.perf_counter() - t0) * 1000)

        hits: list[SearchHit] = []
        ids = (result.get("ids") or [[]])[0]
        docs = (result.get("documents") or [[]])[0]
        metas = (result.get("metadatas") or [[]])[0]
        dists = (result.get("distances") or [[]])[0]
        top_score = dists[0] if dists else None
        for i, doc_id in enumerate(ids):
            d = dists[i] if i < len(dists) else 99.0
            if d > self.max_distance:
                continue
            hits.append(SearchHit(
                id=doc_id, document=docs[i],
                metadata=metas[i] or {},
                distance=float(d),
            ))

        log_query_safe(
            endpoint=endpoint or "<unspecified>",
            collection=name,
            query_text=query,
            n_results=len(hits),
            top_score=float(top_score) if top_score is not None else None,
            latency_ms=latency_ms,
            embedder_model=self.embedder.model,
            embedder_version=self.embedder.embedder_version,
            request_id=request_id,
        )
        return hits
