"""
Phase 2 Chroma audit — verify SQLite vs Chroma sync.

For each collection that has a SQLite source-of-truth, compare row counts
and report mismatches. Useful as a cron / CI check to catch silent drift
when the double-write hook fails.

Exit codes:
  0  in sync (drift <= --tolerance)
  1  drift > --tolerance for at least one collection
  2  hard error (DB missing, Chroma unreachable, etc.)

Usage:
    python scripts/chroma_audit.py
    python scripts/chroma_audit.py --tolerance 0.05      # allow 5% drift
    python scripts/chroma_audit.py --json
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from core.vector_store import get_vector_store    # noqa: E402

DB_PATH = Path(os.getenv("ARGUS_NEWS_DB", ROOT / "data/fed_news/argus_news.db"))

# Maps Chroma collection name → (sqlite table, count query). Collections that
# have no SQLite SoT (e.g. alerts, actions_history) are excluded.
COLL_TO_SQL = {
    "news": ("news", "SELECT COUNT(*) FROM news"),
    "regulatory": ("regulatory_changes", "SELECT COUNT(*) FROM regulatory_changes"),
}


def _sqlite_count(query: str) -> int:
    if not DB_PATH.exists():
        return 0
    with sqlite3.connect(DB_PATH) as conn:
        return conn.execute(query).fetchone()[0]


def _has_table(name: str) -> bool:
    if not DB_PATH.exists():
        return False
    with sqlite3.connect(DB_PATH) as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    return name in tables


def main() -> int:
    p = argparse.ArgumentParser(description="SQLite ↔ Chroma sync audit")
    p.add_argument("--tolerance", type=float, default=0.01,
                   help="Acceptable drift as a fraction (default 0.01 = 1%%)")
    p.add_argument("--json", action="store_true",
                   help="Output JSON instead of text (for CI/cron parsing)")
    args = p.parse_args()

    try:
        store = get_vector_store()
    except Exception as e:
        if args.json:
            print(json.dumps({"status": "error", "message": str(e)}))
        else:
            print(f"Failed to load vector store: {e}", file=sys.stderr)
        return 2

    report: dict[str, dict] = {}
    over_tolerance = False
    for coll, (table, query) in COLL_TO_SQL.items():
        if not _has_table(table):
            report[coll] = {"status": "no_sqlite_table", "sqlite": None, "chroma": store.count(coll)}
            continue
        sqlite_n = _sqlite_count(query)
        chroma_n = store.count(coll)
        if sqlite_n == 0:
            drift = 0.0 if chroma_n == 0 else 1.0
        else:
            drift = abs(sqlite_n - chroma_n) / float(sqlite_n)
        ok = drift <= args.tolerance
        if not ok:
            over_tolerance = True
        report[coll] = {
            "status":    "ok" if ok else "drift",
            "sqlite":    sqlite_n,
            "chroma":    chroma_n,
            "drift_pct": round(drift * 100, 2),
            "tolerance_pct": round(args.tolerance * 100, 2),
        }

    # Audit-only collections: alerts and actions_history have no SoT count,
    # but we still report current Chroma count for visibility.
    for coll in ("alerts", "actions_history"):
        report[coll] = {"status": "audit_only", "sqlite": None, "chroma": store.count(coll)}

    if args.json:
        print(json.dumps({"status": "ok" if not over_tolerance else "drift",
                          "collections": report}, indent=2))
    else:
        print(f"Audit (tolerance: ±{args.tolerance * 100:.1f}%)")
        for coll, info in report.items():
            mark = {"ok": "✓", "drift": "✗", "audit_only": "·", "no_sqlite_table": "?"}.get(info["status"], "?")
            sqlite = info["sqlite"] if info["sqlite"] is not None else "  -  "
            chroma = info["chroma"]
            extra = ""
            if info["status"] == "drift":
                extra = f"   drift={info['drift_pct']}%"
            elif info["status"] == "ok":
                extra = f"   drift={info['drift_pct']}%"
            print(f"  {mark} {coll:18s}  sqlite={sqlite}  chroma={chroma}{extra}")
    return 1 if over_tolerance else 0


if __name__ == "__main__":
    sys.exit(main())
