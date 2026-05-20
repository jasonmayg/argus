"""
`/api/health/*` — deployment health probes.

Born from a real prod incident on 2026-05-20: the test deploy was
missing `chromadb` from its venv, so every search_* tool failed
with the same `ModuleNotFoundError`. The IntelligenceAgent caught
it gracefully (told the user "vector store unavailable") but
there was no first-class way to *check* deployment health
without trying a real query and parsing the agent's reply.

`/api/health/rag` returns a structured snapshot of the vector
store stack:
  - chromadb installed (yes/no + version)
  - sentence-transformers installed (yes/no + version)
  - persist_dir present
  - per-collection document counts (news / alerts / regulatory)
  - any specific check that failed

HTTP status mapping:
  - 200 + status="ok"        — fully healthy
  - 200 + status="degraded"  — partial (e.g. one collection empty
                                but the rest work)
  - 503 + status="down"      — chromadb / persist_dir missing,
                                no RAG possible

Endpoint is deliberately unauthenticated — health probes have to
work from cron / monitoring / a developer's terminal without
threading credentials. It only returns deployment metadata, no
data contents.
"""

from __future__ import annotations

import importlib
import logging
import os
from pathlib import Path
from typing import Any, Dict, List

from flask import Blueprint, jsonify

logger = logging.getLogger(__name__)

health_bp = Blueprint("health_routes", __name__)


def _check_module(name: str) -> Dict[str, Any]:
    """Try importing a module. Return whether it loaded + its version
    string (best-effort) + the short import error string when it
    didn't. Catches everything because some optional deps' import
    errors are wild (chromadb pulls in onnxruntime which can ImportError
    on glibc mismatch, etc.) and we never want the health probe itself
    to crash."""
    try:
        mod = importlib.import_module(name)
        version = getattr(mod, "__version__", "unknown")
        return {"installed": True, "version": str(version)}
    except Exception as e:
        return {
            "installed": False,
            "error": f"{type(e).__name__}: {str(e)[:200]}",
        }


def _check_persist_dir() -> Dict[str, Any]:
    """Inspect the chroma persistence directory: does it exist, is
    it non-empty? An empty dir means chromadb is installed but no
    one has ingested data — RAG works but returns nothing."""
    # Mirrors core.vector_store._load_config / from_config defaults.
    # Hard-coding here rather than importing to keep the health
    # endpoint independent of vector_store's import chain — if that
    # chain itself is what's broken, we still want a useful answer.
    root = Path(__file__).resolve().parent.parent
    candidates = [
        Path(os.getenv("ARGUS_CHROMA_DIR", "")).expanduser() if os.getenv("ARGUS_CHROMA_DIR") else None,
        root / "data" / "chroma_db",
    ]
    for path in candidates:
        if path is None:
            continue
        if path.exists():
            try:
                children = list(path.iterdir())
                return {
                    "path": str(path),
                    "exists": True,
                    "empty": len(children) == 0,
                    "entries": len(children),
                }
            except OSError as e:
                return {"path": str(path), "exists": True,
                        "error": f"cannot list: {e}"}
    return {
        "path": str(candidates[-1]),
        "exists": False,
        "error": "persist_dir not found",
    }


def _check_vector_store() -> Dict[str, Any]:
    """Try to actually instantiate the VectorStore singleton and
    fetch a doc count per collection. This is the strongest health
    signal — passing means a search_* tool call from an agent would
    also succeed."""
    try:
        from core.vector_store import get_vector_store
        store = get_vector_store()
    except Exception as e:
        return {
            "instantiated": False,
            "error": f"{type(e).__name__}: {str(e)[:200]}",
        }

    out: Dict[str, Any] = {"instantiated": True, "collections": {}}
    # We poke each well-known collection independently — one
    # collection failing shouldn't mask the others' state. Failures
    # are logged but the endpoint reply stays 200 / degraded.
    for name in ("news", "alerts", "regulatory"):
        try:
            collection = store._get(name)
            count = collection.count()
            out["collections"][name] = {"count": int(count)}
        except Exception as e:
            out["collections"][name] = {
                "error": f"{type(e).__name__}: {str(e)[:160]}",
            }
    return out


@health_bp.get("/api/health/rag")
def health_rag():
    """GET /api/health/rag — structured snapshot of the vector
    store deployment. Use after a deploy to confirm chromadb +
    persist_dir + collection counts are sane."""
    payload: Dict[str, Any] = {}
    failed: List[str] = []

    # Module checks
    payload["chromadb"] = _check_module("chromadb")
    if not payload["chromadb"]["installed"]:
        failed.append("chromadb")

    payload["sentence_transformers"] = _check_module("sentence_transformers")
    if not payload["sentence_transformers"]["installed"]:
        failed.append("sentence_transformers")

    # Filesystem
    payload["persist_dir"] = _check_persist_dir()
    if not payload["persist_dir"].get("exists"):
        failed.append("persist_dir")

    # If the prerequisites passed, attempt to actually instantiate.
    if not failed:
        payload["vector_store"] = _check_vector_store()
        if not payload["vector_store"].get("instantiated"):
            failed.append("vector_store")
        else:
            # Empty collections are "degraded" not "down" — RAG
            # tools will return helpful "no hits" messages, the
            # agent path still works.
            empty_cols = [
                name for name, info in payload["vector_store"]["collections"].items()
                if info.get("count") == 0
            ]
            if empty_cols:
                payload["empty_collections"] = empty_cols
    else:
        # Skip the vector_store probe because the prerequisites
        # already prove it can't work — don't add cascading
        # noise to the response.
        payload["vector_store"] = {
            "skipped": True,
            "reason": f"prerequisites failed: {', '.join(failed)}",
        }

    # Status + HTTP code
    if not failed and not payload.get("empty_collections"):
        status = "ok"
        http_code = 200
    elif "chromadb" in failed or "persist_dir" in failed or "vector_store" in failed:
        status = "down"
        http_code = 503
    else:
        status = "degraded"
        http_code = 200

    payload["status"]        = status
    payload["checks_failed"] = failed
    return jsonify(payload), http_code
