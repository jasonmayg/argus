"""
Integration test for the Phase 0 LLM router.

Exercises:
  1. Plain call via `user=...`
  2. Multi-turn via `messages=[...]`
  3. JSON-mode (Groq + OpenAI native, Anthropic via system nudge)
  4. Fallback chain — point a task at a bogus provider first and assert it
     falls through to the next one
  5. Telemetry — read back the llm_calls table and confirm rows landed

Usage:
    python scripts/test_llm_router.py

Exit 0 on all pass, 1 on any failure.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

# Make `core/` importable when run from the project root or anywhere
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from core.llm_router import (                            # noqa: E402
    AllProvidersFailedError,
    call_llm,
    list_providers,
    list_tasks,
    reload_config,
    _config,
    _providers,
)
from core.llm_telemetry import DB_PATH                   # noqa: E402


# ─── tiny test framework ──────────────────────────────────────────────────
_results: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    _results.append((name, condition, detail))
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {name}" + (f"  — {detail}" if detail else ""))


# ─── test 1: basic single-turn call ───────────────────────────────────────
def test_basic_call() -> None:
    print("\n[test 1] basic single-turn call")
    try:
        reply = call_llm(
            task="risk_classify",
            system="You are a smoke test. Reply with exactly: pong",
            user="ping",
            max_tokens=16,
        )
        check("returns non-empty string", isinstance(reply, str) and len(reply) > 0, f"reply={reply!r}")
        check("contains 'pong'", "pong" in reply.lower(), f"reply={reply!r}")
    except Exception as e:
        check("did not raise", False, f"raised {type(e).__name__}: {e}")


# ─── test 2: multi-turn via messages=[...] ────────────────────────────────
def test_multi_turn() -> None:
    print("\n[test 2] multi-turn via messages=[...]")
    try:
        reply = call_llm(
            task="guardian_chat",
            system="You are a concise assistant. Answer in <= 5 words.",
            messages=[
                {"role": "user", "content": "Capital of France?"},
                {"role": "assistant", "content": "Paris."},
                {"role": "user", "content": "And of Japan?"},
            ],
            max_tokens=32,
        )
        check("returns non-empty string", isinstance(reply, str) and len(reply) > 0, f"reply={reply!r}")
        check("references Tokyo", "tokyo" in reply.lower(), f"reply={reply!r}")
    except Exception as e:
        check("did not raise", False, f"raised {type(e).__name__}: {e}")


# ─── test 3: JSON mode ────────────────────────────────────────────────────
def test_json_mode() -> None:
    print("\n[test 3] JSON mode")
    try:
        reply = call_llm(
            task="risk_classify",
            system="Return ONLY a JSON object with a single key 'risk' set to one of: low, medium, high.",
            user="The Fed announced an emergency rate cut due to systemic banking failure.",
            max_tokens=64,
            response_format="json",
        )
        check("returns non-empty string", isinstance(reply, str) and len(reply) > 0, f"reply={reply!r}")
        try:
            parsed = json.loads(reply)
            check("parseable as JSON", True, f"parsed={parsed}")
            check("has 'risk' key", "risk" in parsed, f"keys={list(parsed.keys())}")
        except json.JSONDecodeError as e:
            check("parseable as JSON", False, f"parse error: {e}; reply={reply!r}")
    except Exception as e:
        check("did not raise", False, f"raised {type(e).__name__}: {e}")


# ─── test 4: fallback chain ───────────────────────────────────────────────
def test_fallback() -> None:
    """Monkey-patch the config so the first provider in `default` is a bogus
    one, then call_llm() with task='default'. The first attempt should fail
    (provider not configured), and the call should still succeed via the
    second entry. Telemetry should show two rows with the same request_id.
    """
    print("\n[test 4] fallback chain")

    # Snapshot original chain so we can restore
    original_chain = list(_config.get("tasks", {}).get("default", {}).get("chain", []))
    if not original_chain:
        check("default chain configured", False, "no default chain in config")
        return

    # Mutate in-memory config to put a bogus provider first
    _config["tasks"]["default"]["chain"] = ["bogus_provider"] + original_chain

    try:
        reply = call_llm(
            task="default",
            system="You are a smoke test. Reply with exactly: pong",
            user="ping",
            max_tokens=16,
        )
        check("call succeeded after fallback", isinstance(reply, str) and len(reply) > 0, f"reply={reply!r}")
    except AllProvidersFailedError as e:
        check("call succeeded after fallback", False, f"all providers failed: {e}")
    finally:
        # Restore
        _config["tasks"]["default"]["chain"] = original_chain


# ─── test 5: telemetry rows ───────────────────────────────────────────────
def test_telemetry() -> None:
    print("\n[test 5] telemetry rows in llm_calls table")
    if not DB_PATH.exists():
        check("DB file exists", False, f"missing {DB_PATH}")
        return

    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT request_id) FROM llm_calls "
            "WHERE timestamp >= datetime('now', '-5 minutes')"
        )
        n_rows, n_requests = cur.fetchone()

    check("at least one row written", n_rows > 0, f"rows={n_rows}")
    check("rows grouped by request_id", n_requests > 0, f"distinct request_ids={n_requests}")

    # Verify fallback test produced 2+ rows for one request_id
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "SELECT request_id, COUNT(*) AS n, "
            "       SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed, "
            "       SUM(CASE WHEN status='ok' THEN 1 ELSE 0 END) AS ok "
            "FROM llm_calls WHERE timestamp >= datetime('now', '-5 minutes') "
            "GROUP BY request_id HAVING n > 1"
        )
        multi = cur.fetchall()

    check(
        "fallback path recorded (one request with failed→ok pattern)",
        any(failed >= 1 and ok >= 1 for _, _, failed, ok in multi),
        f"multi-attempt request groups: {multi}",
    )


# ─── test 6: introspection helpers ────────────────────────────────────────
def test_introspection() -> None:
    print("\n[test 6] introspection helpers")
    tasks = list_tasks()
    providers = list_providers()
    check("list_tasks() non-empty", len(tasks) > 0, f"count={len(tasks)}, sample={tasks[:3]}")
    check("list_providers() non-empty", len(providers) > 0, f"count={len(providers)}, sample={providers[:3]}")
    check("risk_classify task present", "risk_classify" in tasks)
    check("groq_llama_70b provider present", "groq_llama_70b" in providers)


# ─── runner ───────────────────────────────────────────────────────────────
def main() -> int:
    print("=" * 78)
    print("ARGUS LLM router — integration test")
    print("=" * 78)
    print(f"Config: {sorted((_config.get('tasks') or {}).keys())}")
    print(f"Providers: {list_providers()}")
    print()

    test_introspection()
    test_basic_call()
    test_multi_turn()
    test_json_mode()
    test_fallback()
    # Give telemetry a moment to flush in case of any buffered writes (sync today, but harmless)
    time.sleep(0.1)
    test_telemetry()

    passed = sum(1 for _, ok, _ in _results if ok)
    failed = sum(1 for _, ok, _ in _results if not ok)

    print("\n" + "=" * 78)
    print(f"Summary: {passed} passed, {failed} failed")
    if failed:
        print("\nFailures:")
        for name, ok, detail in _results:
            if not ok:
                print(f"  - {name}: {detail}")
    print("=" * 78)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
