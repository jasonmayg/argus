"""
Phase 0 smoke test — verify all 4 LLM provider keys work end-to-end.

Hits each provider with a minimal "say hello" request, reports PASS/FAIL,
latency, and a snippet of the reply. Uses HTTP directly (only `requests` +
`python-dotenv`) so no extra deps are required.

Usage:
    python scripts/smoke_test_llm.py

Exit code: 0 if all configured providers pass; 1 if any configured provider
fails. Missing keys are skipped, not failed.
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import requests
from dotenv import load_dotenv

# Load .env from project root regardless of where this is invoked from
ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

PROMPT_SYSTEM = "You are a smoke test. Reply with exactly: pong"
PROMPT_USER = "ping"
TIMEOUT_SEC = 30


@dataclass
class Result:
    provider: str
    model: str
    ok: bool
    latency_ms: int
    reply: str = ""
    error: str = ""
    tokens_in: int = 0
    tokens_out: int = 0


def _short(s: str, n: int = 80) -> str:
    s = (s or "").strip().replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


def test_groq() -> Result:
    key = os.getenv("GROQ_API_KEY", "").strip()
    model = "llama-3.3-70b-versatile"
    if not key:
        return Result("Groq", model, False, 0, error="GROQ_API_KEY not set")

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": PROMPT_SYSTEM},
            {"role": "user", "content": PROMPT_USER},
        ],
        "max_tokens": 16,
        "temperature": 0,
    }
    t0 = time.perf_counter()
    try:
        r = requests.post(url, headers=headers, json=body, timeout=TIMEOUT_SEC)
        latency = int((time.perf_counter() - t0) * 1000)
        if r.status_code != 200:
            return Result("Groq", model, False, latency, error=f"HTTP {r.status_code}: {_short(r.text, 200)}")
        data = r.json()
        reply = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        return Result(
            "Groq", model, True, latency, reply=_short(reply),
            tokens_in=usage.get("prompt_tokens", 0),
            tokens_out=usage.get("completion_tokens", 0),
        )
    except Exception as e:
        return Result("Groq", model, False, int((time.perf_counter() - t0) * 1000), error=str(e))


def test_gemini() -> Result:
    key = os.getenv("GEMINI_API_KEY", "").strip()
    model = "gemini-2.0-flash-lite"
    if not key:
        return Result("Gemini", model, False, 0, error="GEMINI_API_KEY not set")

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    headers = {"Content-Type": "application/json", "x-goog-api-key": key}
    body = {
        "system_instruction": {"parts": [{"text": PROMPT_SYSTEM}]},
        "contents": [{"role": "user", "parts": [{"text": PROMPT_USER}]}],
        "generationConfig": {"maxOutputTokens": 16, "temperature": 0},
    }
    t0 = time.perf_counter()
    try:
        r = requests.post(url, headers=headers, json=body, timeout=TIMEOUT_SEC)
        latency = int((time.perf_counter() - t0) * 1000)
        if r.status_code != 200:
            return Result("Gemini", model, False, latency, error=f"HTTP {r.status_code}: {_short(r.text, 200)}")
        data = r.json()
        candidates = data.get("candidates") or []
        if not candidates:
            return Result("Gemini", model, False, latency, error=f"no candidates: {_short(str(data), 200)}")
        parts = candidates[0].get("content", {}).get("parts", [])
        reply = "".join(p.get("text", "") for p in parts)
        usage = data.get("usageMetadata", {})
        return Result(
            "Gemini", model, True, latency, reply=_short(reply),
            tokens_in=usage.get("promptTokenCount", 0),
            tokens_out=usage.get("candidatesTokenCount", 0),
        )
    except Exception as e:
        return Result("Gemini", model, False, int((time.perf_counter() - t0) * 1000), error=str(e))


def test_openai() -> Result:
    key = os.getenv("OPENAI_API_KEY", "").strip()
    model = "gpt-4o-mini"
    if not key:
        return Result("OpenAI", model, False, 0, error="OPENAI_API_KEY not set")

    url = "https://api.openai.com/v1/chat/completions"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": PROMPT_SYSTEM},
            {"role": "user", "content": PROMPT_USER},
        ],
        "max_tokens": 16,
        "temperature": 0,
    }
    t0 = time.perf_counter()
    try:
        r = requests.post(url, headers=headers, json=body, timeout=TIMEOUT_SEC)
        latency = int((time.perf_counter() - t0) * 1000)
        if r.status_code != 200:
            return Result("OpenAI", model, False, latency, error=f"HTTP {r.status_code}: {_short(r.text, 200)}")
        data = r.json()
        reply = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        return Result(
            "OpenAI", model, True, latency, reply=_short(reply),
            tokens_in=usage.get("prompt_tokens", 0),
            tokens_out=usage.get("completion_tokens", 0),
        )
    except Exception as e:
        return Result("OpenAI", model, False, int((time.perf_counter() - t0) * 1000), error=str(e))


def test_anthropic() -> Result:
    key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    model = "claude-haiku-4-5-20251001"
    if not key:
        return Result("Anthropic", model, False, 0, error="ANTHROPIC_API_KEY not set")

    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    body = {
        "model": model,
        "max_tokens": 16,
        "system": PROMPT_SYSTEM,
        "messages": [{"role": "user", "content": PROMPT_USER}],
    }
    t0 = time.perf_counter()
    try:
        r = requests.post(url, headers=headers, json=body, timeout=TIMEOUT_SEC)
        latency = int((time.perf_counter() - t0) * 1000)
        if r.status_code != 200:
            return Result("Anthropic", model, False, latency, error=f"HTTP {r.status_code}: {_short(r.text, 200)}")
        data = r.json()
        content = data.get("content") or []
        reply = "".join(b.get("text", "") for b in content if b.get("type") == "text")
        usage = data.get("usage", {})
        return Result(
            "Anthropic", model, True, latency, reply=_short(reply),
            tokens_in=usage.get("input_tokens", 0),
            tokens_out=usage.get("output_tokens", 0),
        )
    except Exception as e:
        return Result("Anthropic", model, False, int((time.perf_counter() - t0) * 1000), error=str(e))


def main() -> int:
    print("=" * 78)
    print("ARGUS Phase 0 — LLM provider smoke test")
    print("=" * 78)

    results = [test_groq(), test_gemini(), test_openai(), test_anthropic()]

    # Pretty-print table
    header = f"{'PROVIDER':<12}{'MODEL':<32}{'STATUS':<10}{'LATENCY':<12}{'TOKENS (in/out)':<18}"
    print("\n" + header)
    print("-" * len(header))
    for r in results:
        if not os.getenv(f"{r.provider.upper()}_API_KEY", "").strip() and not (r.provider == "Anthropic" and os.getenv("ANTHROPIC_API_KEY")):
            status = "SKIP"
        else:
            status = "PASS" if r.ok else "FAIL"
        latency = f"{r.latency_ms} ms" if r.latency_ms else "-"
        tokens = f"{r.tokens_in}/{r.tokens_out}" if (r.tokens_in or r.tokens_out) else "-"
        print(f"{r.provider:<12}{r.model:<32}{status:<10}{latency:<12}{tokens:<18}")

    # Details for each
    print("\n" + "-" * 78)
    print("Details")
    print("-" * 78)
    for r in results:
        print(f"\n[{r.provider}]")
        if r.ok:
            print(f"  reply: {r.reply!r}")
        elif r.error:
            print(f"  error: {r.error}")
        else:
            print("  skipped (no key)")

    # Summary
    configured = [r for r in results if r.error != f"{r.provider.upper()}_API_KEY not set"]
    passed = [r for r in configured if r.ok]
    failed = [r for r in configured if not r.ok]

    print("\n" + "=" * 78)
    print(f"Summary: {len(passed)}/{len(configured)} configured providers passed")
    if failed:
        print(f"Failed:  {', '.join(r.provider for r in failed)}")
    print("=" * 78)

    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
