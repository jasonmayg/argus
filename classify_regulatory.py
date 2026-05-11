#!/usr/bin/env python3
"""
classify_regulatory.py

Extracts regulatory changes from two sources:
  1. Federal Reserve news (argus_news.db)
  2. Google Macro Radar CSV (GCS: news_google_macro_radar.csv)

Uses OpenAI to identify regulatory changes and extract Before/After details.
Results are stored in regulatory_changes table in argus_news.db.

Usage:
    python3 classify_regulatory.py           # process new items only
    python3 classify_regulatory.py --force   # reprocess everything
    python3 classify_regulatory.py --limit 20  # process max 20 items
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import io
import json
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from core.llm_router import call_llm

# ── Config ────────────────────────────────────────────────────────────────
DB_PATH     = Path(os.getenv("ARGUS_NEWS_DB", "data/fed_news/argus_news.db"))
BATCH_DELAY = 0.5

GCS_BUCKET     = "your-gcs-bucket"
GCS_RADAR_PATH = "news_google_macro_radar.csv"

# ── Regulatory categories ─────────────────────────────────────────────────
REGULATORY_CATEGORIES = [
    "Capital Controls",
    "AML / KYC",
    "Reporting Requirements",
    "Monetary Policy",
    "Banking Regulation",
    "Cross-border Payments",
    "Sanctions",
    "Tax / FATCA",
    "Cryptocurrency",
    "Interest Rate Policy",
    "Liquidity Requirements",
    "General Regulatory",
]

COUNTRY_LIST = [
    "USA", "China", "UK", "Germany", "France", "Japan",
    "Canada", "Hong Kong", "Singapore", "Brazil", "India",
    "Mexico", "Australia", "EU", "Global",
]

# ── OpenAI system prompt ───────────────────────────────────────────────────
REGULATORY_SYSTEM = """You are a treasury compliance analyst specializing in regulatory changes.

Analyze the news item and return ONLY valid JSON — no prose, no markdown fences.

Determine if this news represents a REGULATORY CHANGE that affects treasury operations
(capital flows, payments, compliance requirements, banking rules, monetary policy changes).

JSON schema (ALL fields required):
{
  "is_regulatory": boolean,
  "country": string,           // from: USA, China, UK, Germany, France, Japan, Canada, Hong Kong, Singapore, Brazil, India, Mexico, Australia, EU, Global
  "category": string,          // from: Capital Controls, AML / KYC, Reporting Requirements, Monetary Policy, Banking Regulation, Cross-border Payments, Sanctions, Tax / FATCA, Cryptocurrency, Interest Rate Policy, Liquidity Requirements, General Regulatory
  "severity": string,          // High, Medium, Low
  "change_before": string,     // what the rule/requirement WAS before (empty string if unknown)
  "change_after": string,      // what the rule/requirement IS NOW (empty string if unknown/unclear)
  "effective_date": string,    // ISO date if mentioned, else empty string
  "treasury_impact": string,   // 1-2 sentences on operational impact for treasury
  "action_required": string,   // specific action treasury team should take, or empty string
  "confidence": number         // 0.0-1.0 how confident you are this is a real regulatory change
}

If is_regulatory is false, still fill all fields with appropriate defaults (empty strings, "Low" severity, 0.0 confidence)."""


# ═══════════════════════════════════════════════════════════════════════════
# Database setup
# ═══════════════════════════════════════════════════════════════════════════

def init_db(conn: sqlite3.Connection) -> None:
    """Create regulatory_changes table if it doesn't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS regulatory_changes (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type     TEXT NOT NULL,        -- 'fed_news' | 'google_radar'
            source_id       TEXT,                 -- news.id or radar row id
            title           TEXT NOT NULL,
            url             TEXT,
            published_at    TEXT,
            country         TEXT,
            category        TEXT,
            severity        TEXT,
            change_before   TEXT,
            change_after    TEXT,
            effective_date  TEXT,
            treasury_impact TEXT,
            action_required TEXT,
            confidence      REAL DEFAULT 0.0,
            is_regulatory   INTEGER DEFAULT 0,    -- 1=yes, 0=no
            raw_summary     TEXT,
            classified_at   TEXT,
            UNIQUE(source_type, source_id)
        )
    """)
    conn.commit()


# ═══════════════════════════════════════════════════════════════════════════
# Text cleaning
# ═══════════════════════════════════════════════════════════════════════════

def _clean_html(text: str) -> str:
    """Strip HTML tags and decode common entities."""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    text = text.replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&quot;", '"').replace("&#39;", "'")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:500]


# ═══════════════════════════════════════════════════════════════════════════
# LLM classification (routed via core.llm_router, task=regulatory_classify)
# ═══════════════════════════════════════════════════════════════════════════

def _classify_item(title: str, summary: str, published_at: str = "") -> Optional[Dict]:
    """Classify one news item via the LLM router.

    The router picks a provider from the regulatory_classify chain in
    config/llm_routing.yaml (Groq first, OpenAI mini fallback). Returns the
    parsed JSON dict on success, or None if every provider failed or the
    response was not valid JSON.
    """
    user_prompt = f"""Title: {title}
Published: {published_at}
Summary: {_clean_html(summary)[:400]}

Classify this news item for regulatory changes affecting treasury operations."""

    try:
        raw = call_llm(
            task="regulatory_classify",
            system=REGULATORY_SYSTEM,
            user=user_prompt,
            max_tokens=400,
            temperature=0.1,
            response_format="json",
        ).strip()
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
        return json.loads(raw)

    except Exception as e:
        print(f"    [LLM] Exception: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════════
# Source 1: Federal Reserve news DB
# ═══════════════════════════════════════════════════════════════════════════

def _get_unprocessed_fed_news(conn: sqlite3.Connection, force: bool) -> List[Dict]:
    """Get Fed news items not yet in regulatory_changes."""
    cur = conn.cursor()
    if force:
        cur.execute("SELECT id, title, date, content FROM news ORDER BY date DESC")
    else:
        cur.execute("""
            SELECT n.id, n.title, n.date, n.content
            FROM news n
            LEFT JOIN regulatory_changes rc
                ON rc.source_type = 'fed_news' AND rc.source_id = CAST(n.id AS TEXT)
            WHERE rc.id IS NULL
            ORDER BY n.date DESC
        """)
    return [
        {"id": str(r[0]), "title": r[1] or "", "url": "",
         "published_at": r[2] or "", "summary": r[3] or ""}
        for r in cur.fetchall()
    ]


# ═══════════════════════════════════════════════════════════════════════════
# Source 2: Google Macro Radar (GCS)
# ═══════════════════════════════════════════════════════════════════════════

def _fetch_google_radar_news() -> List[Dict]:
    """Download and parse Google Macro Radar CSV from GCS."""
    try:
        from data_dashboard import (
            _get_gcs_bucket,
            _resolve_data_dashboard_prefix,
        )
        bucket = _get_gcs_bucket()
        prefix = _resolve_data_dashboard_prefix(bucket)

        # Find the radar CSV
        blob_path = f"{prefix}{GCS_RADAR_PATH}"
        blob = bucket.blob(blob_path)
        if not blob.exists():
            # Try without prefix
            blob = bucket.blob(GCS_RADAR_PATH)
            if not blob.exists():
                print(f"  [Radar] news_google_macro_radar.csv not found in GCS")
                return []

        text = blob.download_as_text(encoding="utf-8")
        reader = csv.DictReader(io.StringIO(text))
        items = []
        for row in reader:
            title = (row.get("title") or "").strip()
            if not title:
                continue
            items.append({
                "id":           row.get("id", "") or title[:50],
                "title":        title,
                "url":          row.get("link", "") or "",
                "published_at": row.get("published_at", "") or "",
                "summary":      row.get("summary", "") or "",
                "source":       row.get("source", "Google News") or "",
            })
        print(f"  [Radar] Loaded {len(items)} items from GCS")
        return items

    except Exception as e:
        print(f"  [Radar] GCS fetch error: {e}")
        return []


def _get_unprocessed_radar(conn: sqlite3.Connection, force: bool) -> List[Dict]:
    """Filter radar items not yet processed."""
    all_items = _fetch_google_radar_news()
    if not all_items or force:
        return all_items

    cur = conn.cursor()
    cur.execute(
        "SELECT source_id FROM regulatory_changes WHERE source_type = 'google_radar'"
    )
    existing = {r[0] for r in cur.fetchall()}
    return [item for item in all_items if item["id"] not in existing]


# ═══════════════════════════════════════════════════════════════════════════
# DB write
# ═══════════════════════════════════════════════════════════════════════════

def _upsert_regulatory(
    conn: sqlite3.Connection,
    source_type: str,
    item: Dict,
    result: Dict,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("""
        INSERT OR REPLACE INTO regulatory_changes
            (source_type, source_id, title, url, published_at,
             country, category, severity,
             change_before, change_after, effective_date,
             treasury_impact, action_required, confidence,
             is_regulatory, raw_summary, classified_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        source_type,
        item["id"],
        item["title"],
        item.get("url", ""),
        item.get("published_at", ""),
        result.get("country", "Global"),
        result.get("category", "General Regulatory"),
        result.get("severity", "Low"),
        result.get("change_before", ""),
        result.get("change_after", ""),
        result.get("effective_date", ""),
        result.get("treasury_impact", ""),
        result.get("action_required", ""),
        float(result.get("confidence", 0.0)),
        1 if result.get("is_regulatory") else 0,
        _clean_html(item.get("summary", ""))[:500],
        now,
    ))
    conn.commit()


# ═══════════════════════════════════════════════════════════════════════════
# Main classification loop
# ═══════════════════════════════════════════════════════════════════════════

async def classify_regulatory(force: bool = False, limit: int = 100) -> Dict:
    """
    Main entry point. Processes Fed news + Google Radar news.
    Called by pipeline (AgentOrchestrator Stage 8) or CLI.
    """
    if not DB_PATH.exists():
        return {"status": "error", "message": f"DB not found: {DB_PATH}"}

    conn = sqlite3.connect(str(DB_PATH))
    init_db(conn)

    # Collect items from both sources
    print("\n[RegulatoryClassifier] Collecting news items...")
    fed_items    = _get_unprocessed_fed_news(conn, force)
    radar_items  = _get_unprocessed_radar(conn, force)

    print(f"  Fed news: {len(fed_items)} items to process")
    print(f"  Google Radar: {len(radar_items)} items to process")

    all_items = (
        [("fed_news", i) for i in fed_items] +
        [("google_radar", i) for i in radar_items]
    )[:limit]

    if not all_items:
        conn.close()
        return {"status": "success", "processed": 0, "regulatory": 0,
                "message": "All items already classified"}

    print(f"\n[RegulatoryClassifier] Processing {len(all_items)} items...")
    processed = 0
    regulatory_count = 0
    errors = 0

    for i, (source_type, item) in enumerate(all_items, 1):
        title = item["title"][:70]
        print(f"\n  [{i}/{len(all_items)}] [{source_type}] {title}")

        try:
            result = _classify_item(
                item["title"],
                item.get("summary", ""),
                item.get("published_at", ""),
            )

            if result is None:
                # Store as non-regulatory with zero confidence
                result = {
                    "is_regulatory": False, "country": "Global",
                    "category": "General Regulatory", "severity": "Low",
                    "change_before": "", "change_after": "",
                    "effective_date": "", "treasury_impact": "",
                    "action_required": "", "confidence": 0.0,
                }

            _upsert_regulatory(conn, source_type, item, result)
            processed += 1

            if result.get("is_regulatory") and result.get("confidence", 0) >= 0.5:
                regulatory_count += 1
                print(f"    ✓ REGULATORY: {result.get('country')} | "
                      f"{result.get('category')} | {result.get('severity')} | "
                      f"conf={result.get('confidence', 0):.2f}")
            else:
                print(f"    - Not regulatory (conf={result.get('confidence', 0):.2f})")

        except Exception as e:
            print(f"    ERROR: {e}")
            errors += 1

        if i < len(all_items):
            time.sleep(BATCH_DELAY)

    conn.close()
    print(f"\n[RegulatoryClassifier] Done: {processed} processed, "
          f"{regulatory_count} regulatory, {errors} errors")

    return {
        "status":     "success",
        "processed":  processed,
        "regulatory": regulatory_count,
        "errors":     errors,
        "total":      len(all_items),
    }


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Classify regulatory changes from news")
    parser.add_argument("--force", action="store_true",
                        help="Reprocess all items, even already classified")
    parser.add_argument("--limit", type=int, default=100,
                        help="Max items to process (default: 100)")
    args = parser.parse_args()

    result = asyncio.run(classify_regulatory(force=args.force, limit=args.limit))
    print(f"\nResult: {result}")
