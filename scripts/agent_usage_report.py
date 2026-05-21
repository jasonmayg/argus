#!/usr/bin/env python3
"""
Phase 3E — CLI agent usage report.

Same telemetry data as `/api/admin/agents` but rendered for terminal
/ cron log consumption. Use this when:
  * No Flask server is reachable (e.g. on a deploy that's down).
  * You want a quick daily snapshot piped into a cron-mailed digest.
  * Debugging agent cost / latency without leaving the shell.

Usage:
    python scripts/agent_usage_report.py
    python scripts/agent_usage_report.py --hours 168          # last week
    python scripts/agent_usage_report.py --agent intelligence # just one agent
    python scripts/agent_usage_report.py --recent 50          # last 50 runs

Exit codes:
    0  success (report printed, even if empty)
    1  telemetry db missing entirely
    2  query / unexpected failure
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Mirror admin_routes / agent_telemetry path resolution. Hard-coded
# default + env override so this works without importing the
# telemetry module (which would also work but adds startup cost).
DB_PATH = Path(os.getenv("ARGUS_NEWS_DB", "data/fed_news/argus_news.db"))


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--hours", type=int, default=24,
                   help="Window size in hours (default: 24, max: 168)")
    p.add_argument("--agent", type=str, default=None,
                   help="Filter to a single agent name (intelligence / response / actions)")
    p.add_argument("--recent", type=int, default=10,
                   help="Show this many most-recent runs in detail (default: 10, 0 to skip)")
    return p.parse_args()


def _connect_ro() -> Optional[sqlite3.Connection]:
    if not DB_PATH.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as e:
        print(f"  WARN: cannot open telemetry db: {e}", file=sys.stderr)
        return None


def _percentile(samples: List[int], p: float) -> int:
    if not samples:
        return 0
    sorted_s = sorted(samples)
    if p <= 0: return sorted_s[0]
    if p >= 1: return sorted_s[-1]
    n = len(sorted_s)
    idx = max(0, min(n - 1, int(round(p * n - 0.5))))
    return sorted_s[idx]


def _aggregate(conn: sqlite3.Connection, cutoff_iso: str,
                agent_filter: Optional[str]) -> Dict[str, dict]:
    """Per-agent rollup from `agent_executions`. Returns dict keyed
    by agent_name."""
    where = "WHERE started_at >= ?"
    params: tuple = (cutoff_iso,)
    if agent_filter:
        where += " AND agent_name = ?"
        params = (cutoff_iso, agent_filter)

    cur = conn.cursor()
    cur.execute(f"""
        SELECT agent_name, status, turns_used, tokens_in, tokens_out,
               cost_usd, error_class
          FROM agent_executions
          {where}
    """, params)

    out: Dict[str, dict] = {}
    for r in cur.fetchall():
        name = r["agent_name"]
        slot = out.setdefault(name, {
            "total_runs": 0, "status_counts": {},
            "tokens_in_total": 0, "tokens_out_total": 0,
            "cost_usd_total": 0.0, "turns": [], "errors": {},
        })
        slot["total_runs"] += 1
        slot["status_counts"][r["status"]] = slot["status_counts"].get(r["status"], 0) + 1
        slot["tokens_in_total"]  += int(r["tokens_in"]  or 0)
        slot["tokens_out_total"] += int(r["tokens_out"] or 0)
        slot["cost_usd_total"]   += float(r["cost_usd"] or 0)
        slot["turns"].append(int(r["turns_used"] or 0))
        if r["error_class"]:
            slot["errors"][r["error_class"]] = slot["errors"].get(r["error_class"], 0) + 1
    return out


def _print_summary(by_agent: Dict[str, dict], hours: int) -> None:
    print(f"\n=== Argus agent usage — last {hours}h ===")
    if not by_agent:
        print("  (no agent runs in window)")
        return
    for name, s in sorted(by_agent.items()):
        ok      = s["status_counts"].get("ok", 0)
        success = round(ok / s["total_runs"], 3) if s["total_runs"] else 0
        print(f"\n[{name}]")
        print(f"  runs: {s['total_runs']:>6}   success: {ok}/{s['total_runs']} = {success}")
        # Status breakdown (excluding ok which we already showed)
        non_ok = {k: v for k, v in s["status_counts"].items() if k != "ok"}
        if non_ok:
            parts = "  ".join(f"{k}={v}" for k, v in sorted(non_ok.items()))
            print(f"  status:  {parts}")
        print(f"  cost:    total ${s['cost_usd_total']:.4f}    "
              f"avg/run ${s['cost_usd_total']/max(s['total_runs'],1):.5f}")
        print(f"  tokens:  in {s['tokens_in_total']:>7}    "
              f"out {s['tokens_out_total']:>7}")
        print(f"  turns:   p50 {_percentile(s['turns'], 0.5):>2}    "
              f"p95 {_percentile(s['turns'], 0.95):>2}    "
              f"max {max(s['turns']) if s['turns'] else 0:>2}")
        if s["errors"]:
            print("  top errors:")
            for cls, n in sorted(s["errors"].items(), key=lambda x: -x[1])[:5]:
                print(f"    {cls}: {n}")


def _print_recent(conn: sqlite3.Connection, limit: int,
                   agent_filter: Optional[str]) -> None:
    if limit <= 0:
        return
    where = ""
    params: tuple = ()
    if agent_filter:
        where = "WHERE agent_name = ?"
        params = (agent_filter,)
    cur = conn.cursor()
    cur.execute(f"""
        SELECT agent_name, started_at, status, turns_used, tokens_in,
               tokens_out, cost_usd, error_class, request_id
          FROM agent_executions
          {where}
      ORDER BY started_at DESC
         LIMIT ?
    """, (*params, limit))
    rows = cur.fetchall()
    if not rows:
        return
    print(f"\n=== Recent {len(rows)} run(s) ===")
    print(f"  {'started_at':<25} {'agent':<14} {'status':<16} "
          f"{'turns':>5}  {'cost':>9}  {'req_id':<18}")
    for r in rows:
        cost = float(r["cost_usd"] or 0)
        err = f"  err: {r['error_class']}" if r["error_class"] else ""
        print(f"  {r['started_at']:<25} {r['agent_name']:<14} "
              f"{r['status']:<16} {r['turns_used']:>5}  "
              f"${cost:>7.5f}  {(r['request_id'] or '')[:16]:<18}{err}")


def main() -> int:
    args = _parse_args()
    hours = max(1, min(168, args.hours))
    cutoff_iso = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

    conn = _connect_ro()
    if conn is None:
        print(f"  ERROR: telemetry db not found at {DB_PATH}", file=sys.stderr)
        print(f"  This means no agent has run yet, or ARGUS_NEWS_DB points elsewhere.",
              file=sys.stderr)
        return 1

    try:
        with conn:
            by_agent = _aggregate(conn, cutoff_iso, args.agent)
            _print_summary(by_agent, hours)
            _print_recent(conn, args.recent, args.agent)
        return 0
    except sqlite3.OperationalError as e:
        # Most likely cause: agent_executions table doesn't exist yet
        # (db was created for news but no agent has run). Treat as
        # "no data" not "error".
        if "no such table" in str(e).lower():
            print(f"  (no agent_executions table — no agent has run yet)")
            return 0
        print(f"  ERROR: sqlite query failed: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"  ERROR: unexpected — {type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
