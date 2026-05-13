"""
ARGUS LLM router — Phase 0 entry point.

Single public function `call_llm()` routes a task to a chain of providers and
falls back on rate-limits / transient errors. The chain order per task is
defined in `config/llm_routing.yaml`.

Design contract:
  - Every successful call returns a str (the model's text reply)
  - Every fully-failed call raises `AllProvidersFailedError`
  - Every attempt (success or failure) is recorded to `llm_calls` in SQLite
  - The router is stateless: config is loaded once at import time; reload via
    `reload_config()` if you mutate the yaml at runtime

Why not a class instance: stateless function avoids singleton lifecycle bugs
in Flask app contexts (debug mode reloader, gunicorn fork, etc.). Config is
process-local and cheap to re-parse.

Usage:
    from core.llm_router import call_llm

    reply = call_llm(
        task="risk_classify",
        system="You are a risk analyst.",
        user="Classify: <news text>",
        max_tokens=512,
        temperature=0.0,
        response_format="json",
    )
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Optional

import yaml

from core.llm_providers import (
    AnthropicProvider,
    LLMProvider,
    OpenAICompatibleProvider,
    ProviderError,
    ProviderResponse,
    build_provider,
)
from core.llm_telemetry import log_call_safe


# ─────────────────────────────────────────────────────────────────────────
# Errors
# ─────────────────────────────────────────────────────────────────────────
class AllProvidersFailedError(RuntimeError):
    """Raised when every provider in a task's chain has failed."""

    def __init__(self, task: str, attempts: list[tuple[str, str]]):
        # attempts: list of (provider_name, short_error)
        self.task = task
        self.attempts = attempts
        lines = [f"All providers failed for task={task!r}:"]
        for name, err in attempts:
            lines.append(f"  - {name}: {err}")
        super().__init__("\n".join(lines))


# ─────────────────────────────────────────────────────────────────────────
# Config loading
# ─────────────────────────────────────────────────────────────────────────
_CONFIG_PATH = Path(
    os.getenv("LLM_ROUTING_CONFIG", Path(__file__).resolve().parent.parent / "config" / "llm_routing.yaml")
)

_config: dict = {}
_providers: dict[str, LLMProvider] = {}


def _load() -> None:
    """(Re)load routing config and instantiate providers."""
    global _config, _providers
    if not _CONFIG_PATH.exists():
        raise FileNotFoundError(f"LLM routing config not found: {_CONFIG_PATH}")
    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        _config = yaml.safe_load(f) or {}
    _providers = {}
    for name, cfg in (_config.get("providers") or {}).items():
        try:
            _providers[name] = build_provider(name, cfg)
        except Exception as e:
            # Don't crash the whole app for a misconfigured provider; just skip it
            # and log to stderr. The task chains will treat it as unavailable.
            import sys
            print(f"[llm_router] failed to build provider {name!r}: {e}", file=sys.stderr)


def reload_config() -> None:
    """Re-read yaml from disk and rebuild provider instances. Used in tests."""
    _load()


# Load once at import time
_load()


# ─────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────
def call_llm(
    task: str,
    system: str = "",
    user: Optional[str] = None,
    messages: Optional[list[dict[str, str]]] = None,
    max_tokens: int = 1024,
    temperature: float = 0.0,
    response_format: str = "text",          # "text" | "json"
) -> str:
    """Send a prompt to the configured provider chain for `task`.

    Provide either `user=<str>` for single-turn, or `messages=[...]` for
    multi-turn conversations. `system` is always optional and prepended.

    Returns the model's text reply on success. Raises AllProvidersFailedError
    if every provider in the chain fails.

    `response_format="json"` switches OpenAI-compatible providers to JSON mode
    and adds a JSON nudge to the system prompt for providers that don't have
    a native mode. The caller is still responsible for json.loads().
    """
    if user is None and messages is None:
        raise ValueError("call_llm requires either `user` or `messages`")
    if user is not None and messages is not None:
        raise ValueError("pass either `user` or `messages`, not both")

    # Build the standard message list
    msgs: list[dict[str, str]] = []
    if system:
        msgs.append({"role": "system", "content": system})
    if user is not None:
        msgs.append({"role": "user", "content": user})
    else:
        msgs.extend(messages)   # type: ignore[arg-type]

    chain = _resolve_chain(task)
    if not chain:
        raise AllProvidersFailedError(task, [("<config>", f"no providers configured for task {task!r}")])

    request_id = uuid.uuid4().hex[:12]
    attempts: list[tuple[str, str]] = []

    for idx, provider_name in enumerate(chain):
        provider = _providers.get(provider_name)
        if provider is None:
            attempts.append((provider_name, "provider not configured"))
            log_call_safe(
                task=task, provider=provider_name, model="-", status="failed",
                latency_ms=0, error_short="provider not configured",
                fallback_index=idx, request_id=request_id,
            )
            continue

        if not provider.is_available():
            attempts.append((provider_name, f"{provider._api_key_env} not set"))
            log_call_safe(
                task=task, provider=provider_name, model=provider.model, status="failed",
                latency_ms=0, error_short="api key not set",
                fallback_index=idx, request_id=request_id,
            )
            continue

        try:
            resp: ProviderResponse = provider.call(
                messages=msgs,
                max_tokens=max_tokens,
                temperature=temperature,
                response_format=response_format,
            )
        except ProviderError as e:
            attempts.append((provider_name, str(e)))
            log_call_safe(
                task=task, provider=provider_name, model=provider.model, status="failed",
                latency_ms=0, error_short=str(e),
                fallback_index=idx, request_id=request_id,
            )
            # If the error is not retryable on this provider, fall through to next.
            # If it IS retryable but we've used our retries, also fall through.
            # (Phase 0 keeps retry logic minimal — one attempt per provider, then advance.)
            continue

        # Success
        log_call_safe(
            task=task, provider=provider_name, model=resp.model, status="ok",
            latency_ms=resp.latency_ms, tokens_in=resp.tokens_in, tokens_out=resp.tokens_out,
            fallback_index=idx, request_id=request_id,
        )
        return resp.text

    raise AllProvidersFailedError(task, attempts)


# ─────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────
def _resolve_chain(task: str) -> list[str]:
    """Return provider name list for the task, falling back to `default`."""
    tasks_cfg = _config.get("tasks") or {}
    task_cfg = tasks_cfg.get(task) or tasks_cfg.get("default") or {}
    return list(task_cfg.get("chain") or [])


def list_tasks() -> list[str]:
    return sorted((_config.get("tasks") or {}).keys())


def list_providers() -> list[str]:
    return sorted(_providers.keys())


def get_provider(name: str) -> Optional[LLMProvider]:
    """Useful for tests / debugging — returns the live provider instance."""
    return _providers.get(name)
