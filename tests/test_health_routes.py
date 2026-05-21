"""
Unit tests for `routes/health_routes.py` — `/api/admin/health/rag`.

These pin the status taxonomy + HTTP code mapping with every
expected dependency state mocked. The endpoint exists specifically
to *diagnose* missing-dep / missing-data / misconfigured-embedder
deployments, so the test matrix covers each failure mode
independently.

Run:
    pytest tests/test_health_routes.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from flask import Flask

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from routes.health_routes import health_bp


@pytest.fixture
def client():
    app = Flask(__name__)
    app.register_blueprint(health_bp)
    return app.test_client()


# ──────────────────────────────────────────────────────────────────
# Helpers — stub each layer of the probe independently
# ──────────────────────────────────────────────────────────────────

def _ok_chromadb(version="0.5.1"):
    return {"installed": True, "version": version}


def _missing_chromadb():
    return {"installed": False,
            "error": "ModuleNotFoundError: No module named 'chromadb'"}


def _ok_persist_dir(empty=False):
    return {"path": "/tmp/chroma_db", "exists": True,
            "empty": empty, "entries": 0 if empty else 4}


def _missing_persist_dir():
    return {"path": "/tmp/chroma_db", "exists": False,
            "error": "persist_dir not found"}


def _ok_vector_store(news=10, alerts=5, regulatory=3):
    return {
        "instantiated": True,
        "collections": {
            "news":       {"count": news},
            "alerts":     {"count": alerts},
            "regulatory": {"count": regulatory},
        },
    }


def _available_embedder(name, type_="openai", model="text-embedding-3-small"):
    return {"type": type_, "model": model, "available": True}


def _unavailable_embedder(name, type_="openai", error="API key not set"):
    return {"type": type_, "model": "x", "available": False, "error": error}


def _embedder_cfg(default="openai", fallback="sentence_transformer"):
    """Patch `_read_embedder_config` to return a deterministic shape
    so tests aren't coupled to whatever's in the real yaml."""
    return {
        "default":  default,
        "fallback": fallback,
        "providers": {
            "openai": {
                "type": "openai", "model": "text-embedding-3-small",
                "api_key_env": "OPENAI_API_KEY",
            },
            "sentence_transformer": {
                "type": "sentence_transformer",
                "model": "all-MiniLM-L6-v2",
            },
        },
    }


# ──────────────────────────────────────────────────────────────────
# Healthy
# ──────────────────────────────────────────────────────────────────

class TestAllHealthy:
    def test_returns_200_and_status_ok(self, client):
        with patch("routes.health_routes._check_module",
                   return_value=_ok_chromadb()), \
             patch("routes.health_routes._read_embedder_config",
                   return_value=_embedder_cfg()), \
             patch("routes.health_routes._check_embedder",
                   side_effect=[_available_embedder("openai"),
                                _available_embedder("sentence_transformer",
                                                     type_="sentence_transformer")]), \
             patch("routes.health_routes._check_persist_dir",
                   return_value=_ok_persist_dir()), \
             patch("routes.health_routes._check_vector_store",
                   return_value=_ok_vector_store()):
            r = client.get("/api/admin/health/rag")
        assert r.status_code == 200
        body = r.get_json()
        assert body["status"] == "ok"
        assert body["checks_failed"] == []
        # Embedder block shows which provider is primary + fallback.
        assert body["embedder"]["default_name"] == "openai"
        assert body["embedder"]["default"]["available"] is True
        assert body["embedder"]["fallback_name"] == "sentence_transformer"


# ──────────────────────────────────────────────────────────────────
# Hard-down (HTTP 503)
# ──────────────────────────────────────────────────────────────────

class TestDownStates:
    def test_chromadb_missing_returns_503(self, client):
        """The original incident this endpoint was built for."""
        with patch("routes.health_routes._check_module",
                   return_value=_missing_chromadb()), \
             patch("routes.health_routes._read_embedder_config",
                   return_value=_embedder_cfg()), \
             patch("routes.health_routes._check_embedder",
                   side_effect=[_available_embedder("openai"),
                                _available_embedder("sentence_transformer")]), \
             patch("routes.health_routes._check_persist_dir",
                   return_value=_ok_persist_dir()):
            r = client.get("/api/admin/health/rag")
        assert r.status_code == 503
        body = r.get_json()
        assert body["status"] == "down"
        assert "chromadb" in body["checks_failed"]
        assert body["vector_store"].get("skipped") is True

    def test_persist_dir_missing_returns_503(self, client):
        with patch("routes.health_routes._check_module",
                   return_value=_ok_chromadb()), \
             patch("routes.health_routes._read_embedder_config",
                   return_value=_embedder_cfg()), \
             patch("routes.health_routes._check_embedder",
                   side_effect=[_available_embedder("openai"),
                                _available_embedder("sentence_transformer")]), \
             patch("routes.health_routes._check_persist_dir",
                   return_value=_missing_persist_dir()):
            r = client.get("/api/admin/health/rag")
        assert r.status_code == 503
        assert "persist_dir" in r.get_json()["checks_failed"]

    def test_vector_store_crashes_returns_503(self, client):
        with patch("routes.health_routes._check_module",
                   return_value=_ok_chromadb()), \
             patch("routes.health_routes._read_embedder_config",
                   return_value=_embedder_cfg()), \
             patch("routes.health_routes._check_embedder",
                   side_effect=[_available_embedder("openai"),
                                _available_embedder("sentence_transformer")]), \
             patch("routes.health_routes._check_persist_dir",
                   return_value=_ok_persist_dir()), \
             patch("routes.health_routes._check_vector_store",
                   return_value={"instantiated": False, "error": "boom"}):
            r = client.get("/api/admin/health/rag")
        assert r.status_code == 503
        assert r.get_json()["status"] == "down"

    def test_no_usable_embedder_is_down(self, client):
        """Both default AND fallback unavailable → no embed() call can
        succeed → down. This is the case where, e.g., OPENAI_API_KEY
        isn't set AND sentence-transformers isn't installed (a fresh
        prod deploy that forgot both)."""
        with patch("routes.health_routes._check_module",
                   return_value=_ok_chromadb()), \
             patch("routes.health_routes._read_embedder_config",
                   return_value=_embedder_cfg()), \
             patch("routes.health_routes._check_embedder",
                   side_effect=[_unavailable_embedder("openai"),
                                _unavailable_embedder("sentence_transformer",
                                                       type_="sentence_transformer",
                                                       error="import failed")]), \
             patch("routes.health_routes._check_persist_dir",
                   return_value=_ok_persist_dir()):
            r = client.get("/api/admin/health/rag")
        assert r.status_code == 503
        body = r.get_json()
        assert body["status"] == "down"
        assert "no_usable_embedder" in body["checks_failed"]


# ──────────────────────────────────────────────────────────────────
# Degraded (HTTP 200 + status="degraded")
# ──────────────────────────────────────────────────────────────────

class TestDegradedStates:
    def test_empty_collections_are_degraded(self, client):
        with patch("routes.health_routes._check_module",
                   return_value=_ok_chromadb()), \
             patch("routes.health_routes._read_embedder_config",
                   return_value=_embedder_cfg()), \
             patch("routes.health_routes._check_embedder",
                   side_effect=[_available_embedder("openai"),
                                _available_embedder("sentence_transformer")]), \
             patch("routes.health_routes._check_persist_dir",
                   return_value=_ok_persist_dir(empty=True)), \
             patch("routes.health_routes._check_vector_store",
                   return_value=_ok_vector_store(news=0, alerts=0, regulatory=0)):
            r = client.get("/api/admin/health/rag")
        assert r.status_code == 200
        body = r.get_json()
        assert body["status"] == "degraded"
        assert set(body["empty_collections"]) == {"news", "alerts", "regulatory"}

    def test_default_embedder_down_fallback_works_is_degraded(self, client):
        """OpenAI default unavailable (e.g. missing API key) but ST
        fallback is installed. The box CAN still embed via fallback
        (assuming cross-provider dim caveats are accepted) — surface
        as degraded so ops can fix the API key without losing
        traffic."""
        with patch("routes.health_routes._check_module",
                   return_value=_ok_chromadb()), \
             patch("routes.health_routes._read_embedder_config",
                   return_value=_embedder_cfg()), \
             patch("routes.health_routes._check_embedder",
                   side_effect=[_unavailable_embedder("openai",
                                                      error="OPENAI_API_KEY not set"),
                                _available_embedder("sentence_transformer",
                                                     type_="sentence_transformer")]), \
             patch("routes.health_routes._check_persist_dir",
                   return_value=_ok_persist_dir()), \
             patch("routes.health_routes._check_vector_store",
                   return_value=_ok_vector_store()):
            r = client.get("/api/admin/health/rag")
        assert r.status_code == 200
        body = r.get_json()
        assert body["status"] == "degraded"
        assert body["embedder"]["default"]["available"] is False
        assert body["embedder"]["fallback"]["available"] is True

    def test_one_collection_error_is_degraded(self, client):
        """One collection erroring with the others healthy →
        degraded, with the failing name surfaced under
        `collection_errors`."""
        bad_vs = {
            "instantiated": True,
            "collections": {
                "news":       {"count": 100},
                "alerts":     {"count": 50},
                "regulatory": {"error": "CorruptSegment: index file unreadable"},
            },
        }
        with patch("routes.health_routes._check_module",
                   return_value=_ok_chromadb()), \
             patch("routes.health_routes._read_embedder_config",
                   return_value=_embedder_cfg()), \
             patch("routes.health_routes._check_embedder",
                   side_effect=[_available_embedder("openai"),
                                _available_embedder("sentence_transformer")]), \
             patch("routes.health_routes._check_persist_dir",
                   return_value=_ok_persist_dir()), \
             patch("routes.health_routes._check_vector_store",
                   return_value=bad_vs):
            r = client.get("/api/admin/health/rag")
        assert r.status_code == 200
        body = r.get_json()
        assert body["status"] == "degraded"
        assert "regulatory" in body.get("collection_errors", [])


# ──────────────────────────────────────────────────────────────────
# Endpoint contract
# ──────────────────────────────────────────────────────────────────

class TestContract:
    def test_endpoint_does_not_crash_when_everything_broken(self, client):
        """Top-level try/except → structured JSON 503, never HTML 500.
        Critical because the whole point of a health probe is to
        provide a parseable answer even when things are very wrong."""
        with patch("routes.health_routes._check_module",
                   side_effect=Exception("boom in checker")):
            r = client.get("/api/admin/health/rag")
        assert r.status_code == 503
        assert r.is_json, "endpoint returned non-JSON (HTML 500 leaked)"
        body = r.get_json()
        assert body["status"] == "down"
        assert "probe_internal_error" in body["checks_failed"]

    def test_response_payload_always_has_status_and_checks_failed(self, client):
        with patch("routes.health_routes._check_module",
                   return_value=_ok_chromadb()), \
             patch("routes.health_routes._read_embedder_config",
                   return_value=_embedder_cfg()), \
             patch("routes.health_routes._check_embedder",
                   side_effect=[_available_embedder("openai"),
                                _available_embedder("sentence_transformer")]), \
             patch("routes.health_routes._check_persist_dir",
                   return_value=_ok_persist_dir()), \
             patch("routes.health_routes._check_vector_store",
                   return_value=_ok_vector_store()):
            r = client.get("/api/admin/health/rag")
        body = r.get_json()
        assert "status" in body
        assert "checks_failed" in body

    def test_endpoint_is_get_only(self, client):
        r = client.post("/api/admin/health/rag")
        assert r.status_code == 405


# ──────────────────────────────────────────────────────────────────
# Probe helpers — light smoke tests
# ──────────────────────────────────────────────────────────────────

class TestProbeHelpers:
    def test_check_module_handles_nonexistent_cleanly(self):
        from routes.health_routes import _check_module
        result = _check_module("definitely_not_a_real_module_12345")
        assert result["installed"] is False
        assert "ModuleNotFoundError" in result.get("error", "")

    def test_check_module_returns_version_for_present_module(self):
        from routes.health_routes import _check_module
        result = _check_module("os")
        assert result["installed"] is True

    def test_check_persist_dir_returns_path_field(self):
        from routes.health_routes import _check_persist_dir
        result = _check_persist_dir()
        assert "path" in result

    def test_check_embedder_openai_missing_key_unavailable(self, monkeypatch):
        """The exact deploy mistake we want this to catch."""
        from routes.health_routes import _check_embedder
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        out = _check_embedder("openai", {"openai": {
            "type": "openai", "model": "text-embedding-3-small",
            "api_key_env": "OPENAI_API_KEY",
        }})
        assert out["available"] is False
        assert "OPENAI_API_KEY" in out["error"]

    def test_check_embedder_openai_placeholder_treated_as_unset(self, monkeypatch):
        """The .env.example placeholder shouldn't fool the probe into
        reporting available — it would 401 on first real call."""
        from routes.health_routes import _check_embedder
        monkeypatch.setenv("OPENAI_API_KEY", "your_api_key_here")
        out = _check_embedder("openai", {"openai": {
            "type": "openai", "model": "text-embedding-3-small",
            "api_key_env": "OPENAI_API_KEY",
        }})
        assert out["available"] is False
        assert "placeholder" in out["error"]

    def test_check_embedder_openai_real_key_available(self, monkeypatch):
        from routes.health_routes import _check_embedder
        monkeypatch.setenv("OPENAI_API_KEY", "sk-real-looking-key")
        out = _check_embedder("openai", {"openai": {
            "type": "openai", "model": "text-embedding-3-small",
            "api_key_env": "OPENAI_API_KEY",
        }})
        assert out["available"] is True
