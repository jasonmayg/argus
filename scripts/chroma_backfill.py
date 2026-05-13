"""
Phase 2 Chroma backfill — populate the vector store from existing sources.

Reads from:
  - SQLite news + risk_analysis  →  news collection
  - SQLite regulatory_changes    →  regulatory collection
  - GCS alerts_latest.json       →  alerts collection
  - shared_storage/latest_actions.json → actions_history collection (if present)

Idempotent: re-running upserts the same ids. Use --reset <collection> to
drop and re-create a single collection from scratch (e.g. after switching
embedder model).

Usage:
    python scripts/chroma_backfill.py                       # all collections
    python scripts/chroma_backfill.py --collections news regulatory
    python scripts/chroma_backfill.py --limit 100           # debug small batch
    python scripts/chroma_backfill.py --reset news          # nuke + re-embed
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from core.vector_store import VectorStore, get_vector_store, COLLECTION_NAMES   # noqa: E402

DB_PATH = Path(os.getenv("ARGUS_NEWS_DB", ROOT / "data/fed_news/argus_news.db"))
GUARDIAN_SNAPSHOT = ROOT / "guardian_poc" / "data" / "alerts_latest.json"
ACTIONS_SNAPSHOT = ROOT / "shared_storage" / "latest_actions.json"

BATCH_SIZE = 200


# ─────────────────────────────────────────────────────────────────────────
# News
# ─────────────────────────────────────────────────────────────────────────
def backfill_news(store: VectorStore, limit: Optional[int] = None) -> int:
    if not DB_PATH.exists():
        print(f"  [news] DB not found at {DB_PATH} — skipping")
        return 0
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        sql = """
            SELECT n.id, n.date, n.title, n.link, n.content,
                   ra.risk_category, ra.risk_score, ra.analysis_text
            FROM news n
            LEFT JOIN risk_analysis ra ON ra.news_id = n.id
            ORDER BY n.id
        """
        if limit:
            sql += f" LIMIT {int(limit)}"
        rows = [dict(r) for r in conn.execute(sql).fetchall()]
    if not rows:
        print("  [news] table is empty")
        return 0

    print(f"  [news] processing {len(rows)} rows in batches of {BATCH_SIZE}")
    added = 0
    for start in range(0, len(rows), BATCH_SIZE):
        batch = rows[start:start + BATCH_SIZE]
        for item in batch:
            ok = store.add_news(item["id"], item)
            if ok:
                added += 1
        _progress(start + len(batch), len(rows))
    print()
    return added


# ─────────────────────────────────────────────────────────────────────────
# Regulatory
# ─────────────────────────────────────────────────────────────────────────
def backfill_regulatory(store: VectorStore, limit: Optional[int] = None) -> int:
    if not DB_PATH.exists():
        return 0
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        # Tolerate missing table — early-stage repos may not have run the
        # regulatory classifier yet.
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        if "regulatory_changes" not in tables:
            print("  [regulatory] regulatory_changes table missing — skipping")
            return 0
        sql = "SELECT * FROM regulatory_changes ORDER BY id"
        if limit:
            sql += f" LIMIT {int(limit)}"
        rows = [dict(r) for r in conn.execute(sql).fetchall()]
    if not rows:
        print("  [regulatory] no rows yet — skipping")
        return 0
    print(f"  [regulatory] processing {len(rows)} rows")
    added = 0
    for start in range(0, len(rows), BATCH_SIZE):
        batch = rows[start:start + BATCH_SIZE]
        for row in batch:
            ok = store.add_regulatory(row["id"], row)
            if ok:
                added += 1
        _progress(start + len(batch), len(rows))
    print()
    return added


# ─────────────────────────────────────────────────────────────────────────
# Alerts
# ─────────────────────────────────────────────────────────────────────────
def backfill_alerts(store: VectorStore) -> int:
    if not GUARDIAN_SNAPSHOT.exists():
        print(f"  [alerts] snapshot not found at {GUARDIAN_SNAPSHOT} — skipping")
        return 0
    try:
        snap = json.loads(GUARDIAN_SNAPSHOT.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  [alerts] snapshot is unreadable: {e}")
        return 0

    alerts = snap.get("alerts") or snap.get("scored_alerts") or []
    snapshot_date = snap.get("generated_at", "")[:10] or snap.get("date") or "unknown"
    if not alerts:
        print(f"  [alerts] snapshot {GUARDIAN_SNAPSHOT.name} contains 0 alerts")
        return 0
    print(f"  [alerts] importing {len(alerts)} alerts (snapshot_date={snapshot_date})")
    n = store.add_alerts(snapshot_date, alerts)
    return n


# ─────────────────────────────────────────────────────────────────────────
# Actions history
# ─────────────────────────────────────────────────────────────────────────
def backfill_actions_history(store: VectorStore) -> int:
    if not ACTIONS_SNAPSHOT.exists():
        return 0
    try:
        snap = json.loads(ACTIONS_SNAPSHOT.read_text(encoding="utf-8"))
    except Exception:
        return 0
    actions = snap.get("actions") or []
    if not actions:
        return 0
    added = 0
    for a in actions:
        # Ensure each action has persona/country/category for the embedder
        a.setdefault("persona", snap.get("persona", "unknown"))
        a.setdefault("generated_at", snap.get("generated_at", ""))
        if store.add_action_history(a):
            added += 1
    return added


# ─────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────
def _progress(done: int, total: int) -> None:
    pct = (100 * done / total) if total else 100.0
    bar_len = 30
    filled = int(bar_len * done / total) if total else bar_len
    bar = "█" * filled + "─" * (bar_len - filled)
    sys.stdout.write(f"\r    [{bar}] {done}/{total} ({pct:.0f}%)")
    sys.stdout.flush()


def reset_collection(store: VectorStore, name: str) -> None:
    print(f"  [reset] dropping collection {name!r} ...", end="")
    try:
        store.client.delete_collection(name)
        print(" ok")
    except Exception as e:
        print(f" ({e})")


# ─────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────
def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill Chroma collections from SQLite + snapshots")
    parser.add_argument("--collections", nargs="+", default=list(COLLECTION_NAMES),
                        choices=list(COLLECTION_NAMES) + ["all"],
                        help=f"Collections to fill. Default: all ({', '.join(COLLECTION_NAMES)})")
    parser.add_argument("--limit", type=int, default=None,
                        help="Max rows per collection (debug)")
    parser.add_argument("--reset", action="append", default=[],
                        choices=list(COLLECTION_NAMES),
                        help="Drop + re-create a collection before backfilling. Repeatable.")
    args = parser.parse_args()

    cols = args.collections
    if "all" in cols:
        cols = list(COLLECTION_NAMES)

    store = get_vector_store()
    print(f"Chroma persist dir: {store.persist_dir}")
    print(f"Embedder: {store.embedder.embedder_version}")
    print(f"Collections to fill: {cols}")
    if args.reset:
        print(f"Resetting first: {args.reset}")
        for n in args.reset:
            reset_collection(store, n)

    before = store.collection_counts()
    t0 = time.perf_counter()
    totals: dict[str, int] = {}

    if "news" in cols:
        print("\n→ news")
        totals["news"] = backfill_news(store, args.limit)
    if "regulatory" in cols:
        print("\n→ regulatory")
        totals["regulatory"] = backfill_regulatory(store, args.limit)
    if "alerts" in cols:
        print("\n→ alerts")
        totals["alerts"] = backfill_alerts(store)
    if "actions_history" in cols:
        print("\n→ actions_history")
        totals["actions_history"] = backfill_actions_history(store)

    after = store.collection_counts()
    elapsed = time.perf_counter() - t0

    print("\n" + "=" * 70)
    print("Backfill summary")
    print("=" * 70)
    for n in COLLECTION_NAMES:
        delta = after.get(n, 0) - before.get(n, 0)
        marker = "✓" if delta >= 0 else "✗"
        print(f"  {marker} {n:18s}  before={before.get(n, 0):>6}  after={after.get(n, 0):>6}  delta={delta:+}")
    print(f"\n  Elapsed: {elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
