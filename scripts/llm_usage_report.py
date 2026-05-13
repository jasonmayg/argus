"""
ARGUS LLM usage report.

Reads the `llm_calls` table populated by core/llm_router.py and produces a
readable report covering:

  - Headline totals (calls, success rate, estimated cost, savings vs. an
    all-GPT-4o baseline)
  - Per-task breakdown (count, ok/fail, token volume, latency p50/p95, cost)
  - Per-provider breakdown (same dimensions)
  - Fallback events (which provider's failure caused which provider to take
    over) — useful for spotting flaky upstreams
  - Top failures (grouped by task + provider + truncated error)

Pricing constants in PROVIDER_PRICING reflect public list prices as of
2026-05. Edit there if rates change. Free providers (Groq) are explicitly
listed with zero cost so the totals are accurate.

Usage:
    python scripts/llm_usage_report.py                   # last 24h, text
    python scripts/llm_usage_report.py --since 7d        # last 7 days
    python scripts/llm_usage_report.py --since all
    python scripts/llm_usage_report.py --format json     # machine-readable
    python scripts/llm_usage_report.py --top-failures 10
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = Path(os.getenv("ARGUS_NEWS_DB", ROOT / "data" / "fed_news" / "argus_news.db"))

# ─────────────────────────────────────────────────────────────────────────
# Pricing (USD per 1M tokens). Free providers are zero.
# Update when public rates change. Used for cost estimates only — actual
# bills should be cross-checked with provider invoices.
# ─────────────────────────────────────────────────────────────────────────
PROVIDER_PRICING: dict[str, dict[str, float]] = {
    "groq_llama_70b":    {"input": 0.0,   "output": 0.0},     # free tier
    "openai_4o":         {"input": 2.50,  "output": 10.00},
    "openai_4o_mini":    {"input": 0.15,  "output": 0.60},
    "anthropic_haiku":   {"input": 1.00,  "output": 5.00},    # Haiku 4.5
    "anthropic_sonnet":  {"input": 3.00,  "output": 15.00},   # Sonnet 4.6
}

# Baseline used for "savings vs." calculation. We compare today's actual
# spend against the cost we would have paid if every call had been routed
# to GPT-4o (the pre-router default in most call sites).
BASELINE_PROVIDER = "openai_4o"


# ─────────────────────────────────────────────────────────────────────────
# Time window
# ─────────────────────────────────────────────────────────────────────────
_SINCE_RE = re.compile(r"^(\d+)\s*([hdwm])$", re.IGNORECASE)


def parse_since(spec: str) -> datetime | None:
    """Parse a relative window like '24h', '7d', '2w', '1m' or 'all'."""
    spec = (spec or "").strip().lower()
    if spec in {"all", "0", ""}:
        return None
    m = _SINCE_RE.match(spec)
    if not m:
        raise SystemExit(f"--since: cannot parse {spec!r}. Use e.g. 24h, 7d, 2w, or 'all'.")
    n, unit = int(m.group(1)), m.group(2)
    delta = {
        "h": timedelta(hours=n),
        "d": timedelta(days=n),
        "w": timedelta(weeks=n),
        "m": timedelta(days=30 * n),
    }[unit]
    return datetime.now(timezone.utc) - delta


# ─────────────────────────────────────────────────────────────────────────
# Cost
# ─────────────────────────────────────────────────────────────────────────
def call_cost(provider: str, tokens_in: int, tokens_out: int) -> float:
    rate = PROVIDER_PRICING.get(provider)
    if rate is None:
        return 0.0   # unknown provider — assume free rather than guess
    return (tokens_in / 1_000_000.0) * rate["input"] + (tokens_out / 1_000_000.0) * rate["output"]


def baseline_cost(tokens_in: int, tokens_out: int) -> float:
    return call_cost(BASELINE_PROVIDER, tokens_in, tokens_out)


# ─────────────────────────────────────────────────────────────────────────
# Data loading
# ─────────────────────────────────────────────────────────────────────────
def load_rows(db_path: Path, since: datetime | None) -> list[dict[str, Any]]:
    if not db_path.exists():
        raise SystemExit(f"DB not found: {db_path}")

    sql = (
        "SELECT id, timestamp, task, provider, model, status, latency_ms, "
        "       tokens_in, tokens_out, error_short, fallback_index, request_id "
        "FROM llm_calls"
    )
    params: tuple = ()
    if since is not None:
        sql += " WHERE timestamp >= ?"
        params = (since.isoformat(),)
    sql += " ORDER BY id"

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


# ─────────────────────────────────────────────────────────────────────────
# Aggregation
# ─────────────────────────────────────────────────────────────────────────
def _percentile(values: list[int], p: float) -> int:
    """Crude percentile — no numpy. p in [0,100]."""
    if not values:
        return 0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1)))))
    return s[k]


def aggregate(rows: list[dict]) -> dict[str, Any]:
    by_task: dict[str, dict] = defaultdict(lambda: {
        "calls": 0, "ok": 0, "fail": 0, "tokens_in": 0, "tokens_out": 0,
        "latencies": [], "cost": 0.0, "baseline_cost": 0.0,
    })
    by_provider: dict[str, dict] = defaultdict(lambda: {
        "calls": 0, "ok": 0, "fail": 0, "tokens_in": 0, "tokens_out": 0,
        "latencies": [], "cost": 0.0,
    })
    by_request: dict[str, list[dict]] = defaultdict(list)

    for r in rows:
        task = r["task"]
        provider = r["provider"]
        ok = r["status"] == "ok"
        ti, to = int(r["tokens_in"] or 0), int(r["tokens_out"] or 0)
        latency = int(r["latency_ms"] or 0)
        cost = call_cost(provider, ti, to)

        t = by_task[task]
        t["calls"] += 1
        t["ok" if ok else "fail"] += 1
        t["tokens_in"] += ti
        t["tokens_out"] += to
        if latency > 0:
            t["latencies"].append(latency)
        t["cost"] += cost
        if ok:
            # Baseline cost is only meaningful for successful calls — failed
            # attempts wouldn't have produced output tokens we'd be charged for.
            t["baseline_cost"] += baseline_cost(ti, to)

        p = by_provider[provider]
        p["calls"] += 1
        p["ok" if ok else "fail"] += 1
        p["tokens_in"] += ti
        p["tokens_out"] += to
        if latency > 0:
            p["latencies"].append(latency)
        p["cost"] += cost

        rid = r["request_id"]
        if rid:
            by_request[rid].append(r)

    # Fallback edges: for each request_id with > 1 attempt, count transitions
    # from a failed provider to the *next* provider that was tried.
    fallback_edges: dict[tuple[str, str], int] = defaultdict(int)
    for rid, attempts in by_request.items():
        if len(attempts) < 2:
            continue
        attempts.sort(key=lambda x: x["fallback_index"])
        for i in range(len(attempts) - 1):
            if attempts[i]["status"] == "failed":
                fallback_edges[(attempts[i]["provider"], attempts[i + 1]["provider"])] += 1

    # Failure clustering
    failure_clusters: dict[tuple[str, str, str], int] = defaultdict(int)
    for r in rows:
        if r["status"] != "failed":
            continue
        err_key = (r["error_short"] or "")[:80]
        failure_clusters[(r["task"], r["provider"], err_key)] += 1

    return {
        "by_task": dict(by_task),
        "by_provider": dict(by_provider),
        "fallback_edges": dict(fallback_edges),
        "failure_clusters": dict(failure_clusters),
        "total_calls": len(rows),
        "total_ok": sum(1 for r in rows if r["status"] == "ok"),
        "total_fail": sum(1 for r in rows if r["status"] == "failed"),
        "total_request_groups": len(by_request),
    }


# ─────────────────────────────────────────────────────────────────────────
# Output: text
# ─────────────────────────────────────────────────────────────────────────
def _fmt_int(n: int) -> str:
    return f"{n:,}"


def _fmt_cost(c: float) -> str:
    if c == 0.0:
        return "$0.0000"
    if c < 0.01:
        return f"${c:.4f}"
    return f"${c:.2f}"


def render_text(agg: dict, since: datetime | None, top_failures: int) -> str:
    lines: list[str] = []
    bar = "═" * 78
    rule = "─" * 78

    # Header
    since_str = since.strftime("%Y-%m-%d %H:%M UTC") if since else "beginning of time"
    lines.append(bar)
    lines.append(f"ARGUS LLM Usage Report — since {since_str}")
    lines.append(bar)

    total_calls = agg["total_calls"]
    total_ok = agg["total_ok"]
    total_fail = agg["total_fail"]
    success_pct = (100.0 * total_ok / total_calls) if total_calls else 0.0

    total_cost = sum(t["cost"] for t in agg["by_task"].values())
    total_baseline = sum(t["baseline_cost"] for t in agg["by_task"].values())
    saved = total_baseline - total_cost

    lines.append("")
    if total_calls == 0:
        lines.append("No calls recorded in this window.")
        lines.append(bar)
        return "\n".join(lines)

    lines.append(f"Calls       : {_fmt_int(total_calls)}   ({total_ok} ok, {total_fail} failed — {success_pct:.1f}% success)")
    lines.append(f"Request groups : {_fmt_int(agg['total_request_groups'])}   "
                 f"({_fmt_int(total_calls - agg['total_request_groups'])} fallback retries)")
    lines.append(f"Actual cost : {_fmt_cost(total_cost)}")
    lines.append(f"Baseline    : {_fmt_cost(total_baseline)}   "
                 f"(if every successful call had hit {BASELINE_PROVIDER})")
    lines.append(f"Saved       : {_fmt_cost(saved)}   "
                 f"({(100.0 * saved / total_baseline if total_baseline else 0):.1f}% vs baseline)")

    # By task
    lines.append("")
    lines.append("By Task")
    lines.append(rule)
    lines.append(f"{'task':<22} {'calls':>6} {'ok':>4} {'fail':>5} {'in/out tokens':>18} {'p50':>7} {'p95':>7} {'cost':>10}")
    for task in sorted(agg["by_task"].keys(), key=lambda t: -agg["by_task"][t]["calls"]):
        t = agg["by_task"][task]
        tokens = f"{_fmt_int(t['tokens_in'])} / {_fmt_int(t['tokens_out'])}"
        p50 = _percentile(t["latencies"], 50)
        p95 = _percentile(t["latencies"], 95)
        lines.append(
            f"{task[:22]:<22} {t['calls']:>6} {t['ok']:>4} {t['fail']:>5} "
            f"{tokens:>18} {p50:>5}ms {p95:>5}ms {_fmt_cost(t['cost']):>10}"
        )

    # By provider
    lines.append("")
    lines.append("By Provider")
    lines.append(rule)
    lines.append(f"{'provider':<22} {'calls':>6} {'ok':>4} {'fail':>5} {'success':>8} {'in/out tokens':>18} {'p50':>7} {'cost':>10}")
    for prov in sorted(agg["by_provider"].keys(), key=lambda p: -agg["by_provider"][p]["calls"]):
        p = agg["by_provider"][prov]
        sr = (100.0 * p["ok"] / p["calls"]) if p["calls"] else 0.0
        tokens = f"{_fmt_int(p['tokens_in'])} / {_fmt_int(p['tokens_out'])}"
        p50 = _percentile(p["latencies"], 50)
        suffix = " (FREE)" if PROVIDER_PRICING.get(prov, {}).get("input", 1) == 0 else ""
        lines.append(
            f"{prov[:22]:<22} {p['calls']:>6} {p['ok']:>4} {p['fail']:>5} "
            f"{sr:>7.1f}% {tokens:>18} {p50:>5}ms {(_fmt_cost(p['cost']) + suffix):>10}"
        )

    # Fallback edges
    if agg["fallback_edges"]:
        lines.append("")
        lines.append(f"Fallback Events ({sum(agg['fallback_edges'].values())})")
        lines.append(rule)
        edges = sorted(agg["fallback_edges"].items(), key=lambda kv: -kv[1])
        for (src, dst), n in edges:
            lines.append(f"  {src:<22} → {dst:<22} {n:>4} times")

    # Top failures
    if agg["failure_clusters"]:
        lines.append("")
        lines.append(f"Top Failures (showing up to {top_failures})")
        lines.append(rule)
        clusters = sorted(agg["failure_clusters"].items(), key=lambda kv: -kv[1])[:top_failures]
        for (task, provider, err), n in clusters:
            err_short = err if len(err) <= 70 else err[:67] + "..."
            lines.append(f"  [{n:>3}x] task={task} provider={provider}")
            lines.append(f"         {err_short}")

    lines.append("")
    lines.append(bar)
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────
# Output: JSON
# ─────────────────────────────────────────────────────────────────────────
def render_json(agg: dict, since: datetime | None) -> str:
    by_task_out = {}
    for task, t in agg["by_task"].items():
        by_task_out[task] = {
            "calls": t["calls"], "ok": t["ok"], "fail": t["fail"],
            "tokens_in": t["tokens_in"], "tokens_out": t["tokens_out"],
            "latency_p50_ms": _percentile(t["latencies"], 50),
            "latency_p95_ms": _percentile(t["latencies"], 95),
            "cost_usd": round(t["cost"], 6),
            "baseline_cost_usd": round(t["baseline_cost"], 6),
        }
    by_provider_out = {}
    for prov, p in agg["by_provider"].items():
        by_provider_out[prov] = {
            "calls": p["calls"], "ok": p["ok"], "fail": p["fail"],
            "success_rate": round(100.0 * p["ok"] / p["calls"], 2) if p["calls"] else 0,
            "tokens_in": p["tokens_in"], "tokens_out": p["tokens_out"],
            "latency_p50_ms": _percentile(p["latencies"], 50),
            "latency_p95_ms": _percentile(p["latencies"], 95),
            "cost_usd": round(p["cost"], 6),
            "is_free": PROVIDER_PRICING.get(prov, {}).get("input", 1) == 0,
        }
    fallback_out = {f"{src}->{dst}": n for (src, dst), n in agg["fallback_edges"].items()}
    failures_out = [
        {"task": task, "provider": provider, "error": err, "count": n}
        for (task, provider, err), n in sorted(agg["failure_clusters"].items(), key=lambda kv: -kv[1])
    ]

    return json.dumps({
        "since": since.isoformat() if since else None,
        "totals": {
            "calls": agg["total_calls"],
            "ok": agg["total_ok"],
            "fail": agg["total_fail"],
            "request_groups": agg["total_request_groups"],
            "actual_cost_usd": round(sum(t["cost"] for t in agg["by_task"].values()), 6),
            "baseline_cost_usd": round(sum(t["baseline_cost"] for t in agg["by_task"].values()), 6),
        },
        "by_task": by_task_out,
        "by_provider": by_provider_out,
        "fallback_edges": fallback_out,
        "failures": failures_out,
    }, indent=2, ensure_ascii=False)


# ─────────────────────────────────────────────────────────────────────────
# RAG (Phase 2): read rag_queries and join on request_id
# ─────────────────────────────────────────────────────────────────────────
def load_rag_rows(db_path: Path, since: Optional[datetime]) -> list[dict]:
    """Return rag_queries rows in the window, empty list if table missing."""
    if not db_path.exists():
        return []
    with sqlite3.connect(db_path) as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        if "rag_queries" not in tables:
            return []
        sql = (
            "SELECT id, timestamp, endpoint, collection, query_text, n_results, "
            "       top_score, latency_ms, embedder_model, embedder_version, request_id "
            "FROM rag_queries"
        )
        params: tuple = ()
        if since is not None:
            sql += " WHERE timestamp >= ?"
            params = (since.isoformat(),)
        sql += " ORDER BY id"
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def aggregate_rag(rows: list[dict]) -> dict:
    by_endpoint: dict[str, dict] = defaultdict(lambda: {
        "queries": 0, "collections": defaultdict(int), "latencies": [], "top_scores": [],
    })
    by_collection: dict[str, dict] = defaultdict(lambda: {
        "queries": 0, "latencies": [], "top_scores": [], "request_ids": set(),
    })
    for r in rows:
        endpoint = r.get("endpoint") or "<unspecified>"
        coll = r.get("collection") or "<unknown>"
        latency = int(r.get("latency_ms") or 0)
        top = r.get("top_score")
        e = by_endpoint[endpoint]
        e["queries"] += 1
        e["collections"][coll] += 1
        if latency > 0:
            e["latencies"].append(latency)
        if top is not None:
            e["top_scores"].append(top)
        c = by_collection[coll]
        c["queries"] += 1
        if latency > 0:
            c["latencies"].append(latency)
        if top is not None:
            c["top_scores"].append(top)
        if r.get("request_id"):
            c["request_ids"].add(r["request_id"])
    return {
        "total": len(rows),
        "by_endpoint": dict(by_endpoint),
        "by_collection": dict(by_collection),
    }


def render_rag_text(rag_agg: dict, llm_request_ids: set[str]) -> str:
    if rag_agg["total"] == 0:
        return ""
    rule = "─" * 78
    lines = ["", "RAG Queries", rule]
    lines.append(f"Total RAG queries : {rag_agg['total']}")
    # Join: how many RAG queries fed an LLM call we recorded?
    joined = 0
    for c in rag_agg["by_collection"].values():
        joined += sum(1 for rid in c["request_ids"] if rid in llm_request_ids)
    lines.append(f"Joined to LLM call: {joined}  (RAG queries inside an /api/* request that also called an LLM)")
    lines.append("")
    lines.append(f"{'collection':<18} {'queries':>8} {'p50 ms':>8} {'avg top_score':>14}")
    for coll, c in sorted(rag_agg["by_collection"].items(), key=lambda kv: -kv[1]["queries"]):
        p50 = _percentile(c["latencies"], 50)
        avg_top = (sum(c["top_scores"]) / len(c["top_scores"])) if c["top_scores"] else 0.0
        lines.append(f"{coll:<18} {c['queries']:>8} {p50:>6}ms  {avg_top:>14.3f}")
    lines.append("")
    lines.append(f"{'endpoint':<28} {'queries':>8} {'p50 ms':>8}  collections")
    for ep, e in sorted(rag_agg["by_endpoint"].items(), key=lambda kv: -kv[1]["queries"]):
        p50 = _percentile(e["latencies"], 50)
        colls = ", ".join(f"{c}={n}" for c, n in sorted(e["collections"].items(), key=lambda kv: -kv[1]))[:40]
        lines.append(f"{ep[:28]:<28} {e['queries']:>8} {p50:>6}ms  {colls}")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────
def main() -> int:
    parser = argparse.ArgumentParser(description="ARGUS LLM usage report")
    parser.add_argument("--since", default="24h",
                        help="Time window — e.g. 24h, 7d, 2w, 1m, or 'all'. Default: 24h")
    parser.add_argument("--format", choices=["text", "json"], default="text",
                        help="Output format. Default: text")
    parser.add_argument("--top-failures", type=int, default=5,
                        help="How many top failure clusters to show. Default: 5")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB,
                        help=f"SQLite DB path. Default: {DEFAULT_DB}")
    parser.add_argument("--include-rag", action="store_true",
                        help="Also report Phase 2 rag_queries (joined on request_id)")
    args = parser.parse_args()

    since = parse_since(args.since)
    rows = load_rows(args.db, since)
    agg = aggregate(rows)

    rag_rows: list[dict] = []
    rag_agg: dict = {"total": 0}
    if args.include_rag:
        rag_rows = load_rag_rows(args.db, since)
        rag_agg = aggregate_rag(rag_rows)

    if args.format == "json":
        payload = json.loads(render_json(agg, since))
        if args.include_rag:
            payload["rag"] = {
                "total": rag_agg["total"],
                "by_endpoint": {
                    ep: {"queries": e["queries"],
                         "p50_ms": _percentile(e["latencies"], 50),
                         "collections": dict(e["collections"])}
                    for ep, e in rag_agg.get("by_endpoint", {}).items()
                },
                "by_collection": {
                    c: {"queries": v["queries"],
                        "p50_ms": _percentile(v["latencies"], 50),
                        "avg_top_score": (sum(v["top_scores"]) / len(v["top_scores"])) if v["top_scores"] else 0.0}
                    for c, v in rag_agg.get("by_collection", {}).items()
                },
            }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(render_text(agg, since, args.top_failures))
        if args.include_rag:
            llm_rids: set[str] = {r["request_id"] for r in rows if r.get("request_id")}
            extra = render_rag_text(rag_agg, llm_rids)
            if extra:
                print(extra)

    return 0


if __name__ == "__main__":
    sys.exit(main())
