"""
Phase 2 Chroma inspector — debug / explore the vector store.

Three subcommands:

  stats              Show count + sample doc + metadata schema per collection.
  search <name> <q>  Run a semantic search against a collection.
  versions           List distinct embedder_version values per collection
                     (useful after switching embedder model to find stale rows).

Usage:
    python scripts/chroma_inspect.py stats
    python scripts/chroma_inspect.py search news "UK banking crisis" --top 5
    python scripts/chroma_inspect.py search regulatory "sanctions"
    python scripts/chroma_inspect.py versions
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from core.vector_store import COLLECTION_NAMES, get_vector_store     # noqa: E402


def cmd_stats() -> int:
    store = get_vector_store()
    print(f"Chroma persist dir: {store.persist_dir}")
    print(f"Embedder:           {store.embedder.embedder_version}")
    print()
    for name in COLLECTION_NAMES:
        coll = store._get(name)
        n = coll.count()
        print(f"── {name:18s}  count={n}")
        if n == 0:
            continue
        sample = coll.peek(limit=2)
        for i, doc in enumerate(sample.get("documents") or []):
            meta = (sample.get("metadatas") or [{}])[i] if i < len(sample.get("metadatas") or []) else {}
            doc_short = (doc or "").replace("\n", " / ")[:120]
            print(f"    [{i}]  {doc_short}…")
            keys = ", ".join(sorted(meta.keys()))[:120]
            print(f"         meta keys: {keys}")
        print()
    return 0


def cmd_search(collection: str, query: str, top: int) -> int:
    store = get_vector_store()
    search_fn = {
        "news":            store.search_news,
        "regulatory":      store.search_regulatory,
        "alerts":          store.search_alerts,
        "actions_history": store.search_actions_history,
    }[collection]
    hits = search_fn(query, n_results=top, endpoint="cli.inspect")
    print(f"Query: {query!r}  →  {len(hits)} hit(s)")
    if not hits:
        return 0
    for i, h in enumerate(hits):
        doc_short = (h.document or "").replace("\n", " / ")[:200]
        ms = {k: v for k, v in h.metadata.items() if k not in ("url",)}
        print(f"\n  [{i+1}] distance={h.distance:.3f}  id={h.id}")
        print(f"      {doc_short}")
        print(f"      {json.dumps(ms, ensure_ascii=False, default=str)[:200]}")
    return 0


def cmd_versions() -> int:
    store = get_vector_store()
    print(f"Current embedder: {store.embedder.embedder_version}\n")
    for name in COLLECTION_NAMES:
        coll = store._get(name)
        n = coll.count()
        if n == 0:
            print(f"  {name:18s}  (empty)")
            continue
        # Sample more aggressively if collection is large
        sample = coll.get(limit=min(n, 1000), include=["metadatas"])
        seen: dict[str, int] = {}
        for m in sample.get("metadatas") or []:
            v = (m or {}).get("embedder_version", "<missing>")
            seen[v] = seen.get(v, 0) + 1
        print(f"  {name:18s}  count={n}  sample={sum(seen.values())}")
        for v, c in sorted(seen.items(), key=lambda kv: -kv[1]):
            mark = " ←current" if v == store.embedder.embedder_version else ""
            print(f"    {c:>5}  {v}{mark}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Chroma collection inspector")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("stats", help="Show counts + sample docs per collection")

    s_search = sub.add_parser("search", help="Semantic search inside a collection")
    s_search.add_argument("collection", choices=list(COLLECTION_NAMES))
    s_search.add_argument("query")
    s_search.add_argument("--top", type=int, default=5)

    sub.add_parser("versions", help="List distinct embedder_version values per collection")

    args = p.parse_args()
    if args.cmd == "stats":
        return cmd_stats()
    if args.cmd == "search":
        return cmd_search(args.collection, args.query, args.top)
    if args.cmd == "versions":
        return cmd_versions()
    return 1


if __name__ == "__main__":
    sys.exit(main())
