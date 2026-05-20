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
                                or one collection errors but the
                                rest work)
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
from typing import Any, Dict, List, Optional

import yaml
from flask import Blueprint, jsonify

logger = logging.getLogger(__name__)

health_bp = Blueprint("health_routes", __name__)

_ROOT = Path(__file__).resolve().parent.parent
_RAG_CONFIG_PATH = Path(os.getenv("RAG_CONFIG", _ROOT / "config" / "rag_config.yaml"))


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


def _resolve_configured_persist_dir() -> Path:
    """Read the persist_dir the app would actually use.

    Mirrors `core.vector_store.from_config` resolution order:
      1. `RAG_CONFIG` env → path to a yaml file (default
         `config/rag_config.yaml`)
      2. `storage.persist_dir` key in that yaml, joined to repo root
      3. Fallback: `data/chroma_db` under repo root.

    We parse the yaml directly rather than importing `core.vector_store`
    so this probe still works when the vector_store import chain
    itself is broken (chromadb missing etc.). Returns the absolute
    path regardless of whether the directory exists — the caller
    checks existence.
    """
    rel = "data/chroma_db"
    if _RAG_CONFIG_PATH.exists():
        try:
            with open(_RAG_CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            rel = (cfg.get("storage") or {}).get("persist_dir", rel)
        except Exception as e:
            logger.warning("rag_config parse failed: %s", e)
    return (_ROOT / rel).resolve()


def _check_persist_dir() -> Dict[str, Any]:
    """Inspect the chroma persistence directory the app would use.
    Returns the resolved path (whether it exists or not), so a
    misconfigured `RAG_CONFIG` / `storage.persist_dir` doesn't
    silently fall back to a default the app isn't actually using —
    which used to make this probe report \"down at /default/path\"
    even when the real config pointed elsewhere."""
    path = _resolve_configured_persist_dir()
    if not path.exists():
        return {
            "path": str(path),
            "exists": False,
            "error": "persist_dir not found",
        }
    try:
        children = list(path.iterdir())
        return {
            "path":   str(path),
            "exists": True,
            "empty":  len(children) == 0,
            "entries": len(children),
        }
    except OSError as e:
        return {"path": str(path), "exists": True,
                "error": f"cannot list: {e}"}


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
    # Poke each well-known collection independently — one collection
    # failing must not mask the others' state. Use the public
    # `store.count(name)` API rather than reaching into `_get` so
    # this probe survives internal refactors of VectorStore.
    for name in ("news", "alerts", "regulatory"):
        try:
            out["collections"][name] = {"count": int(store.count(name))}
        except Exception as e:
            logger.warning("health probe: collection %s count failed: %s",
                           name, e)
            out["collections"][name] = {
                "error": f"{type(e).__name__}: {str(e)[:160]}",
            }
    return out


def _classify_state(payload: Dict[str, Any], failed: List[str]) -> tuple[str, int]:
    """Map the gathered probe results into (status, http_code).

    Rules:
      * chromadb / persist_dir / vector_store missing or failed → down (503)
      * any collection errors                                    → degraded (200)
      * empty collections (count == 0)                           → degraded (200)
      * sentence_transformers missing alone                      → down (503)
        (RAG fundamentally needs the embedder for new queries —
        existing indexed docs return as cached hits, but new ones
        won't embed; treat as down so monitoring escalates)
      * everything else                                          → ok (200)
    """
    hard_down = {"chromadb", "persist_dir", "vector_store", "sentence_transformers"}
    if hard_down & set(failed):
        return "down", 503

    vs = payload.get("vector_store") or {}
    collections = vs.get("collections") or {}
    if any("error" in info for info in collections.values()):
        return "degraded", 200
    if payload.get("empty_collections"):
        return "degraded", 200
    return "ok", 200


@health_bp.get("/api/health/rag")
def health_rag():
    """GET /api/health/rag — structured snapshot of the vector
    store deployment. Use after a deploy to confirm chromadb +
    persist_dir + collection counts are sane.

    Wrapped in a top-level try/except so monitoring tooling always
    gets a parseable JSON body back, never an HTML 500 from Flask's
    default error handler — the whole point of a health probe is
    that you can curl it and reliably scrape `status` / `checks_failed`."""
    try:
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

        # If the hard prerequisites passed, attempt to actually
        # instantiate the store. Treat sentence_transformers as
        # "hard" too — see _classify_state docstring.
        if not any(f in failed for f in ("chromadb", "persist_dir")):
            payload["vector_store"] = _check_vector_store()
            if not payload["vector_store"].get("instantiated"):
                failed.append("vector_store")
            else:
                empty_cols = [
                    name for name, info in payload["vector_store"]["collections"].items()
                    if info.get("count") == 0
                ]
                if empty_cols:
                    payload["empty_collections"] = empty_cols
                error_cols = [
                    name for name, info in payload["vector_store"]["collections"].items()
                    if "error" in info
                ]
                if error_cols:
                    payload["collection_errors"] = error_cols
        else:
            payload["vector_store"] = {
                "skipped": True,
                "reason":  f"prerequisites failed: {', '.join(failed)}",
            }

        status, http_code = _classify_state(payload, failed)
        payload["status"]        = status
        payload["checks_failed"] = failed
        return jsonify(payload), http_code

    except Exception as exc:
        # Catastrophic failure — something inside the probe raised
        # despite each helper's own try/except. Return a structured
        # JSON 503 anyway so cron / dashboards keep working.
        logger.exception("health_rag unexpected error")
        return jsonify({
            "status":        "down",
            "checks_failed": ["probe_internal_error"],
            "error":         f"{type(exc).__name__}: {str(exc)[:200]}",
        }), 503
