"""
LLM provider abstraction for the Phase 0 router.

Concrete providers implement a small interface and raise ProviderError on
failure so the router can decide whether to fall back. We deliberately use
`requests` directly instead of the openai/anthropic SDKs so:
  - Adding new providers (Groq, OpenRouter, Fireworks, etc.) is uniform
  - Network/timeout semantics are consistent across providers
  - No SDK version drift between providers

A "message" follows the standard chat format: {"role": "system"|"user"|"assistant", "content": "..."}
The Anthropic provider hoists the leading system message into Anthropic's
dedicated `system` field — callers do not need to think about this.
"""

from __future__ import annotations

import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import requests


# ─────────────────────────────────────────────────────────────────────────
# Errors
# ─────────────────────────────────────────────────────────────────────────
class ProviderError(Exception):
    """Raised when a single provider call fails.

    `retryable` is False if the error is structural (auth, missing key) and
    retrying the same provider will not help. The router uses this to decide
    whether to advance to the next provider immediately.
    """

    def __init__(self, provider: str, message: str, status_code: int | None = None, retryable: bool = True):
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code
        self.retryable = retryable


# ─────────────────────────────────────────────────────────────────────────
# Response container
# ─────────────────────────────────────────────────────────────────────────
@dataclass
class ProviderResponse:
    text: str
    tokens_in: int
    tokens_out: int
    latency_ms: int
    model: str


# ─────────────────────────────────────────────────────────────────────────
# Base
# ─────────────────────────────────────────────────────────────────────────
class LLMProvider(ABC):
    def __init__(self, name: str, cfg: dict[str, Any]):
        self.name = name
        self.model: str = cfg["model"]
        self.timeout_sec: int = int(cfg.get("timeout_sec", 30))
        self.max_retries: int = int(cfg.get("max_retries", 1))
        self.supports_json_mode: bool = bool(cfg.get("supports_json_mode", False))
        self._api_key_env = cfg["api_key_env"]

    @property
    def api_key(self) -> str:
        return os.getenv(self._api_key_env, "").strip()

    def is_available(self) -> bool:
        return bool(self.api_key)

    @abstractmethod
    def call(
        self,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
        response_format: str,    # "text" or "json"
    ) -> ProviderResponse: ...


# ─────────────────────────────────────────────────────────────────────────
# OpenAI-compatible: covers OpenAI, Groq, and any other endpoint that
# implements POST {base_url}/chat/completions with the standard schema.
# ─────────────────────────────────────────────────────────────────────────
class OpenAICompatibleProvider(LLMProvider):
    def __init__(self, name: str, cfg: dict[str, Any]):
        super().__init__(name, cfg)
        self.base_url: str = cfg["base_url"].rstrip("/")

    def call(
        self,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
        response_format: str,
    ) -> ProviderResponse:
        if not self.api_key:
            raise ProviderError(self.name, f"{self._api_key_env} not set", retryable=False)

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if response_format == "json" and self.supports_json_mode:
            body["response_format"] = {"type": "json_object"}

        t0 = time.perf_counter()
        try:
            r = requests.post(url, headers=headers, json=body, timeout=self.timeout_sec)
        except requests.RequestException as e:
            raise ProviderError(self.name, f"network error: {e}", retryable=True) from e
        latency = int((time.perf_counter() - t0) * 1000)

        if r.status_code != 200:
            retryable = r.status_code in (429, 500, 502, 503, 504)
            raise ProviderError(
                self.name,
                f"HTTP {r.status_code}: {(r.text or '')[:200]}",
                status_code=r.status_code,
                retryable=retryable,
            )

        try:
            data = r.json()
            text = data["choices"][0]["message"]["content"] or ""
            usage = data.get("usage") or {}
        except (KeyError, ValueError, IndexError) as e:
            raise ProviderError(self.name, f"unexpected response shape: {e}", retryable=True) from e

        return ProviderResponse(
            text=text,
            tokens_in=int(usage.get("prompt_tokens", 0)),
            tokens_out=int(usage.get("completion_tokens", 0)),
            latency_ms=latency,
            model=self.model,
        )


# ─────────────────────────────────────────────────────────────────────────
# Anthropic Messages API
# ─────────────────────────────────────────────────────────────────────────
class AnthropicProvider(LLMProvider):
    BASE_URL = "https://api.anthropic.com/v1/messages"

    def call(
        self,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
        response_format: str,
    ) -> ProviderResponse:
        if not self.api_key:
            raise ProviderError(self.name, f"{self._api_key_env} not set", retryable=False)

        # Hoist a leading system message into Anthropic's dedicated `system` field.
        # Anthropic does not accept a {"role": "system"} entry inside `messages`.
        system_text = ""
        chat_msgs: list[dict[str, str]] = []
        for m in messages:
            if m["role"] == "system":
                system_text = (system_text + "\n\n" + m["content"]).strip() if system_text else m["content"]
            else:
                chat_msgs.append({"role": m["role"], "content": m["content"]})

        # JSON-mode for Anthropic requires tool-use trickery; not implemented in Phase 0.
        # If the caller asked for JSON we just nudge via system prompt — caller-side
        # parsing already tolerates this (existing code does the same).
        if response_format == "json" and not self.supports_json_mode:
            json_hint = "Respond with a single JSON object only. Do not include any text outside the JSON."
            system_text = f"{system_text}\n\n{json_hint}".strip() if system_text else json_hint

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": chat_msgs,
        }
        if system_text:
            body["system"] = system_text

        t0 = time.perf_counter()
        try:
            r = requests.post(self.BASE_URL, headers=headers, json=body, timeout=self.timeout_sec)
        except requests.RequestException as e:
            raise ProviderError(self.name, f"network error: {e}", retryable=True) from e
        latency = int((time.perf_counter() - t0) * 1000)

        if r.status_code != 200:
            retryable = r.status_code in (429, 500, 502, 503, 504, 529)   # 529 = overloaded
            raise ProviderError(
                self.name,
                f"HTTP {r.status_code}: {(r.text or '')[:200]}",
                status_code=r.status_code,
                retryable=retryable,
            )

        try:
            data = r.json()
            content_blocks = data.get("content") or []
            text = "".join(b.get("text", "") for b in content_blocks if b.get("type") == "text")
            usage = data.get("usage") or {}
        except (KeyError, ValueError) as e:
            raise ProviderError(self.name, f"unexpected response shape: {e}", retryable=True) from e

        return ProviderResponse(
            text=text,
            tokens_in=int(usage.get("input_tokens", 0)),
            tokens_out=int(usage.get("output_tokens", 0)),
            latency_ms=latency,
            model=self.model,
        )


# ─────────────────────────────────────────────────────────────────────────
# Factory
# ─────────────────────────────────────────────────────────────────────────
_PROVIDER_TYPES = {
    "openai_compatible": OpenAICompatibleProvider,
    "anthropic": AnthropicProvider,
}


def build_provider(name: str, cfg: dict[str, Any]) -> LLMProvider:
    ptype = cfg.get("type")
    if ptype not in _PROVIDER_TYPES:
        raise ValueError(f"unknown provider type {ptype!r} for {name!r}; valid: {list(_PROVIDER_TYPES)}")
    return _PROVIDER_TYPES[ptype](name, cfg)
