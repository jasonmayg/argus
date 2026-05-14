#!/usr/bin/env python3
"""
classify_news.py

AI-powered news classification with GCS data enrichment.
- Classifies news using Claude (risk_category, scores, analysis_text)
- Enriches each news item with linked GCS indicators + current values
- Writes results to the extended risk_analysis table

Usage:
    # Classify all unclassified news
    python3 classify_news.py

    # Reclassify everything (force)
    python3 classify_news.py --force

    # Called automatically by pipeline (AgentOrchestrator)
    from classify_news import classify_all_news
    await classify_all_news()
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

# Load .env automatically when run as a script or imported
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ── Config ────────────────────────────────────────────────
DB_PATH          = Path(os.getenv("ARGUS_NEWS_DB",    "data/fed_news/argus_news.db"))
OPENAI_API_KEY   = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL     = os.getenv("OPENAI_MODEL",   "gpt-4o")
OPENAI_URL       = "https://api.openai.com/v1/chat/completions"
BATCH_DELAY_SEC  = 0.5   # polite delay between API calls


# ═══════════════════════════════════════════════════════════
# Country / Topic → Indicator mapping
# ═══════════════════════════════════════════════════════════

COUNTRY_KEYWORDS: Dict[str, List[str]] = {
    "USA": [
        "federal reserve", "fed", "u.s.", "united states", "america",
        "treasury", "sofr", "fomc", "powell", "washington",
    ],
    "Canada": ["canada", "bank of canada", "boc", "canadian", "ottawa"],
    "UK": ["bank of england", "boe", "united kingdom", "britain", "london"],
    "Germany": ["ecb", "european central bank", "germany", "deutsche", "berlin", "eurozone"],
    "France": ["france", "french", "paris", "ecb"],
    "Japan": ["bank of japan", "boj", "japan", "yen", "jpy", "tokyo"],
    "China": ["china", "pboc", "renminbi", "yuan", "cny", "beijing"],
    "Brazil": ["brazil", "bcb", "brl", "real", "brasilia"],
    "India": ["india", "rbi", "reserve bank of india", "rupee", "inr"],
    "Australia": ["australia", "rba", "aud", "sydney"],
    "Mexico": ["mexico", "banxico", "mxn", "peso"],
    "South Korea": ["korea", "bank of korea", "krw", "won", "seoul"],
    "Hong Kong": ["hong kong", "hkma", "hkd"],
    "Global": ["global", "world", "international", "gdelt", "earthquake", "conflict"],
}

TOPIC_TO_INDICATORS: Dict[str, List[str]] = {
    "interest rate":    ["policy_rate", "sofr"],
    "rate":             ["policy_rate", "sofr"],
    "inflation":        ["cpi"],
    "cpi":              ["cpi"],
    "price":            ["cpi"],
    "currency":         ["usdcad"],
    "fx":               ["usdcad"],
    "exchange":         ["usdcad"],
    "liquidity":        ["sofr"],
    "funding":          ["sofr", "treasury_auctions"],
    "auction":          ["treasury_auctions"],
    "bond":             ["treasury_auctions"],
    "treasury":         ["treasury_auctions"],
    "geopolit":         ["conflict_light"],
    "conflict":         ["conflict_light"],
    "war":              ["conflict_light"],
    "earthquake":       ["earthquakes_global"],
    "disaster":         ["earthquakes_global"],
    "enforcement":      ["policy_rate"],
    "regulation":       ["policy_rate"],
    "monetary":         ["policy_rate", "sofr"],
}


def _extract_country(text: str) -> List[str]:
    """Extract all matched countries from text."""
    text_lower = text.lower()
    matched = []
    for country, keywords in COUNTRY_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            matched.append(country)
    return matched or ["Global"]


def _extract_indicators(text: str) -> List[str]:
    """Extract relevant GCS indicators from text."""
    text_lower = text.lower()
    indicators = set()
    for topic, inds in TOPIC_TO_INDICATORS.items():
        if topic in text_lower:
            indicators.update(inds)
    return list(indicators)


# ═══════════════════════════════════════════════════════════
# GCS data fetching (via data_dashboard cache)
# ═══════════════════════════════════════════════════════════

def _get_gcs_series_cached(country: str, indicator: str) -> Optional[Dict]:
    """
    Get latest series values from data_dashboard cache.
    Returns dict with latest_value, prev_value, change_pct, trend.
    """
    try:
        from data_dashboard import (
            _get_gcs_bucket,
            _resolve_data_dashboard_prefix,
            _list_gcs_csv_blobs,
            _read_gcs_csv_text,
            _build_series_from_text,
            _infer_country_from_filename,
            _infer_indicator_from_filename,
        )

        bucket = _get_gcs_bucket()
        prefix = _resolve_data_dashboard_prefix(bucket)
        blobs  = _list_gcs_csv_blobs(bucket, prefix)

        for b in blobs:
            stem           = Path(b.name).stem
            blob_country   = _infer_country_from_filename(stem)
            blob_indicator = _infer_indicator_from_filename(stem)

            country_match = (
                (country == "Global" and not blob_country) or
                (blob_country and blob_country.lower() == country.lower())
            )

            if country_match and blob_indicator == indicator.lower():
                text   = _read_gcs_csv_text(bucket, b.name)
                meta   = _build_series_from_text(Path(b.name).name, text)
                values = meta.get("values", [])
                dates  = meta.get("dates",  [])

                if len(values) < 1:
                    return None

                latest = values[-1]
                prev   = values[-2] if len(values) >= 2 else None
                change_pct = None
                trend      = "stable"

                if prev is not None and prev != 0:
                    change_pct = round((latest - prev) / abs(prev) * 100, 3)
                    if change_pct > 0.1:
                        trend = "rising"
                    elif change_pct < -0.1:
                        trend = "falling"

                return {
                    "indicator":   indicator,
                    "country":     country,
                    "latest":      round(latest, 4),
                    "prev":        round(prev, 4) if prev is not None else None,
                    "change_pct":  change_pct,
                    "trend":       trend,
                    "latest_date": dates[-1] if dates else None,
                    "file":        Path(b.name).name,
                }

    except Exception as e:
        print(f"    [GCS] Could not fetch {country}/{indicator}: {e}")

    return None


def _fetch_indicator_data(countries: List[str], indicators: List[str]) -> List[Dict]:
    """Fetch all relevant indicator data for this news item."""
    results = []
    seen    = set()

    for country in countries:
        for indicator in indicators:
            key = f"{country}:{indicator}"
            if key in seen:
                continue
            seen.add(key)

            data = _get_gcs_series_cached(country, indicator)
            if data:
                results.append(data)

    return results


# ═══════════════════════════════════════════════════════════
# Threshold checking
# ═══════════════════════════════════════════════════════════

def _load_rules() -> List[Dict]:
    rules_file = Path(os.getenv("ARGUS_THRESHOLDS_FILE", "thresholds.json"))
    if not rules_file.exists():
        return []
    with open(rules_file) as f:
        data = json.load(f)
    return [r for r in data.get("rules", []) if r.get("enabled", True)]


def _check_thresholds(indicator_data: List[Dict]) -> List[str]:
    """Return list of triggered rule IDs."""
    rules     = _load_rules()
    triggered = []

    for ind in indicator_data:
        for rule in rules:
            if (rule.get("country",    "").lower() != ind["country"].lower() or
                rule.get("indicator",  "").lower() != ind["indicator"].lower()):
                continue

            op        = rule.get("operator", ">")
            threshold = float(rule.get("threshold", 0))
            rtype     = rule.get("type", "absolute")

            value = None
            if rtype == "absolute":
                value = ind.get("latest")
            elif rtype == "change":
                cp = ind.get("change_pct")
                if cp is not None:
                    value = abs(cp)

            if value is None:
                continue

            ops = {">": lambda a,b: a>b, "<": lambda a,b: a<b,
                   ">=": lambda a,b: a>=b, "<=": lambda a,b: a<=b}
            fn = ops.get(op)
            if fn and fn(value, threshold):
                triggered.append(rule["id"])

    return triggered


# ═══════════════════════════════════════════════════════════
# Scoring
# ═══════════════════════════════════════════════════════════

def _compute_data_score(indicator_data: List[Dict], triggered: List[str]) -> float:
    """
    GCS data score 0-10:
      +2 per triggered alert (cap at 6)
      +1 if any linked indicator is 'rising'
      +0.5 per indicator linked
    """
    score = 0.0
    score += min(len(triggered) * 2.0, 6.0)
    score += sum(1.0 for d in indicator_data if d.get("trend") == "rising")
    score += min(len(indicator_data) * 0.5, 2.0)
    return min(round(score, 1), 10.0)


# ═══════════════════════════════════════════════════════════
# Claude classification
# ═══════════════════════════════════════════════════════════

CLASSIFY_SYSTEM = """You are ARGUS, an institutional treasury risk analyst.
Classify a news item and return ONLY valid JSON — no prose, no markdown fences.

JSON schema (all fields required):
{
  "risk_category": string,   // one of: Regulatory, Monetary Policy, Liquidity,
                             //   Inflation, FX Risk, Funding, Geopolitical,
                             //   Credit Risk, Operational, General
  "news_score": number,      // 0-10 float, news severity alone
  "rationale": string        // 1-2 sentences explaining the risk
}
"""

def _call_openai(news_title: str, news_content: str,
                 indicator_data: List[Dict], triggered: List[str]) -> Optional[Dict]:
    """Call OpenAI to classify the news item with GCS context."""
    if not OPENAI_API_KEY:
        print("    [OpenAI] OPENAI_API_KEY not set — skipping AI classification")
        return None

    # Build GCS context block
    gcs_lines = []
    for d in indicator_data:
        trend_str = f"trend={d['trend']}"
        chg_str   = f"change={d['change_pct']:+.2f}%" if d.get('change_pct') is not None else ""
        gcs_lines.append(
            f"  - {d['country']} {d['indicator']}: {d['latest']} "
            f"({trend_str}{', ' + chg_str if chg_str else ''})"
        )

    alert_lines = [f"  - {r}" for r in triggered] if triggered else ["  - None"]

    user_prompt = f"""News title: {news_title}
News content (excerpt): {(news_content or '')[:300]}

Linked GCS macro indicators:
{chr(10).join(gcs_lines) if gcs_lines else '  - None available'}

Triggered threshold alerts:
{chr(10).join(alert_lines)}

Classify this news item."""

    resp = requests.post(
        OPENAI_URL,
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type":  "application/json",
        },
        json={
            "model":       OPENAI_MODEL,
            "messages":    [
                {"role": "system", "content": CLASSIFY_SYSTEM},
                {"role": "user",   "content": user_prompt},
            ],
            "max_tokens":  300,
            "temperature": 0.2,
        },
        timeout=30,
    )
    if not resp.ok:
        raise RuntimeError(f"OpenAI error {resp.status_code}: {resp.text[:200]}")

    raw = resp.json()["choices"][0]["message"]["content"].strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
    return json.loads(raw)


# ═══════════════════════════════════════════════════════════
# DB helpers
# ═══════════════════════════════════════════════════════════

def _init_risk_analysis_schema(conn: sqlite3.Connection) -> None:
    """Idempotently bring the risk_analysis table up to the columns this
    module writes.

    The table predates this script and was created out-of-band (no
    CREATE TABLE statement existed anywhere in the repo). The live schema
    had only six columns, while _upsert_analysis writes twelve — every
    run of classify_news.py raised ``OperationalError: table has no
    column named news_score`` and the pipeline silently never produced a
    single risk_analysis row.

    Resolution: CREATE TABLE IF NOT EXISTS with the full target schema
    so fresh DBs come up correct, plus PRAGMA-driven ALTER TABLE for
    pre-existing deployments that already have the partial table. Safe
    to call on every invocation.

    Logging:
      * INFO line per column added — so operators see *what* was
        backfilled the first time the migration runs against a stale
        DB. Subsequent runs are silent at INFO and only emit DEBUG.
      * DEBUG line with timing + the column set before/after.
      * ERROR line + re-raise on any ``sqlite3.OperationalError``: a
        broken migration must be loud, never silent.
    """
    t0 = time.perf_counter()
    added: List[str] = []
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS risk_analysis (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                news_id           INTEGER NOT NULL,
                risk_category     TEXT,
                risk_score        INTEGER,
                analysis_text     TEXT,
                news_score        REAL,
                data_score        REAL,
                linked_indicators TEXT,
                indicator_values  TEXT,
                triggered_alerts  TEXT,
                scored_at         TEXT,
                created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        existing = {row[1] for row in conn.execute("PRAGMA table_info(risk_analysis)")}
        logger.debug("risk_analysis existing columns: %s", sorted(existing))

        additions = [
            ("news_score",        "REAL"),
            ("data_score",        "REAL"),
            ("linked_indicators", "TEXT"),
            ("indicator_values",  "TEXT"),
            ("triggered_alerts",  "TEXT"),
            ("scored_at",         "TEXT"),
        ]
        for col, sql_type in additions:
            if col not in existing:
                logger.info("risk_analysis: adding missing column %s %s", col, sql_type)
                conn.execute(f"ALTER TABLE risk_analysis ADD COLUMN {col} {sql_type}")
                added.append(col)
        conn.commit()
    except sqlite3.OperationalError as exc:
        logger.error("risk_analysis schema init failed: %s", exc)
        raise

    elapsed_ms = (time.perf_counter() - t0) * 1000
    logger.debug(
        "risk_analysis schema init took %.1fms (%d column(s) added)",
        elapsed_ms, len(added),
    )


def _get_unclassified(conn: sqlite3.Connection, force: bool = False) -> List[Dict]:
    """Return news items that need classification.

    Includes ``date`` and ``link`` so the downstream Chroma dual-write
    (Phase 2E) can stamp the vector doc with proper publication metadata
    instead of empty strings.
    """
    cur = conn.cursor()
    if force:
        cur.execute("SELECT id, title, content, date, link FROM news ORDER BY date DESC")
    else:
        cur.execute("""
            SELECT n.id, n.title, n.content, n.date, n.link
            FROM news n
            LEFT JOIN risk_analysis ra ON ra.news_id = n.id
            WHERE ra.news_id IS NULL
               OR ra.risk_category IS NULL
               OR ra.risk_category = ''
            ORDER BY n.date DESC
        """)
    return [
        {"id": r[0], "title": r[1], "content": r[2], "date": r[3] or "", "link": r[4] or ""}
        for r in cur.fetchall()
    ]


def _upsert_analysis(conn: sqlite3.Connection, news_id: int, result: Dict) -> None:
    """Insert or update risk_analysis row."""
    cur = conn.cursor()

    # Check if row exists
    cur.execute("SELECT id FROM risk_analysis WHERE news_id = ?", (news_id,))
    existing = cur.fetchone()

    now = datetime.now(timezone.utc).isoformat()

    if existing:
        cur.execute("""
            UPDATE risk_analysis SET
                risk_category     = ?,
                risk_score        = ?,
                analysis_text     = ?,
                news_score        = ?,
                data_score        = ?,
                linked_indicators = ?,
                indicator_values  = ?,
                triggered_alerts  = ?,
                scored_at         = ?
            WHERE news_id = ?
        """, (
            result["risk_category"],
            result["risk_score"],
            result["analysis_text"],
            result["news_score"],
            result["data_score"],
            json.dumps(result["linked_indicators"]),
            json.dumps(result["indicator_values"]),
            json.dumps(result["triggered_alerts"]),
            now,
            news_id,
        ))
    else:
        cur.execute("""
            INSERT INTO risk_analysis
                (news_id, risk_category, risk_score, analysis_text,
                 news_score, data_score, linked_indicators,
                 indicator_values, triggered_alerts, scored_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            news_id,
            result["risk_category"],
            result["risk_score"],
            result["analysis_text"],
            result["news_score"],
            result["data_score"],
            json.dumps(result["linked_indicators"]),
            json.dumps(result["indicator_values"]),
            json.dumps(result["triggered_alerts"]),
            now,
        ))

    conn.commit()


# ═══════════════════════════════════════════════════════════
# Core classification logic
# ═══════════════════════════════════════════════════════════

def _classify_single(news: Dict) -> Dict:
    """
    Full pipeline for one news item:
    1. Extract countries + indicators
    2. Fetch GCS data
    3. Check thresholds
    4. Call Claude
    5. Compute composite score
    6. Return result dict
    """
    title   = news.get("title", "")
    content = news.get("content", "")
    text    = f"{title} {content}"

    # Step 1: link GCS data
    countries  = _extract_country(text)
    indicators = _extract_indicators(text)
    print(f"    Countries: {countries}  Indicators: {indicators}")

    # Step 2: fetch indicator data
    indicator_data = _fetch_indicator_data(countries, indicators) if indicators else []

    # Step 3: check thresholds
    triggered = _check_thresholds(indicator_data)
    if triggered:
        print(f"    Triggered rules: {triggered}")

    # Step 4: Claude classification
    claude_result = None
    try:
        claude_result = _call_openai(title, content, indicator_data, triggered)
    except Exception as e:
        print(f"    [OpenAI] Error: {e}")

    # Step 5: compute scores
    news_score = float(claude_result.get("news_score", 3.0)) if claude_result else 3.0
    data_score = _compute_data_score(indicator_data, triggered)

    # Composite: 40% news + 35% data triggers + 25% trend
    trend_bonus = sum(
        0.5 if d.get("trend") == "rising" else 0
        for d in indicator_data
    )
    composite = round(
        news_score * 0.40 +
        data_score * 0.35 +
        min(trend_bonus, 2.5) * 0.25,
        1
    )
    composite = min(composite, 10.0)

    risk_category = (
        claude_result.get("risk_category", "General")
        if claude_result else "General"
    )
    rationale = (
        claude_result.get("rationale", "")
        if claude_result else ""
    )

    # Build analysis_text combining AI rationale + data context
    data_lines = []
    for d in indicator_data:
        chg = f"{d['change_pct']:+.2f}%" if d.get("change_pct") is not None else "N/A"
        data_lines.append(
            f"{d['country']} {d['indicator'].upper()}: {d['latest']} ({d['trend']}, {chg})"
        )

    analysis_parts = [rationale] if rationale else []
    if data_lines:
        analysis_parts.append("Data context: " + " | ".join(data_lines))
    if triggered:
        analysis_parts.append(f"Triggered alerts: {', '.join(triggered)}")

    analysis_text = " — ".join(analysis_parts) or "No analysis available."

    return {
        "risk_category":    risk_category,
        "risk_score":       int(round(composite)),
        "analysis_text":    analysis_text[:1000],
        "news_score":       news_score,
        "data_score":       data_score,
        "linked_indicators": [
            f"{d['country']}:{d['indicator']}" for d in indicator_data
        ],
        "indicator_values": {
            f"{d['country']}:{d['indicator']}": {
                "latest":     d["latest"],
                "trend":      d["trend"],
                "change_pct": d.get("change_pct"),
            }
            for d in indicator_data
        },
        "triggered_alerts": triggered,
    }


# ═══════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════

async def classify_all_news(force: bool = False) -> Dict:
    """
    Classify all unclassified news items.
    Called by AgentOrchestrator after pipeline completes.

    Returns summary dict with counts.
    """
    if not DB_PATH.exists():
        return {"status": "error", "message": f"DB not found: {DB_PATH}"}

    conn  = sqlite3.connect(str(DB_PATH))
    _init_risk_analysis_schema(conn)
    items = _get_unclassified(conn, force=force)

    if not items:
        conn.close()
        return {"status": "success", "classified": 0, "message": "All news already classified"}

    print(f"\n[ClassifyNews] Processing {len(items)} news items...")
    classified = 0
    errors     = 0

    for i, news in enumerate(items, 1):
        print(f"\n  [{i}/{len(items)}] {news['title'][:70]}")
        try:
            result = _classify_single(news)
            _upsert_analysis(conn, news["id"], result)
            print(f"    → {result['risk_category']} | score={result['risk_score']} "
                  f"(news={result['news_score']}, data={result['data_score']})")
            classified += 1
        except Exception as e:
            print(f"    ERROR: {e}")
            errors += 1

        # Polite delay between Claude calls
        if i < len(items):
            time.sleep(BATCH_DELAY_SEC)

    conn.close()
    print(f"\n[ClassifyNews] Done: {classified} classified, {errors} errors")

    return {
        "status":     "success",
        "classified": classified,
        "errors":     errors,
        "total":      len(items),
    }


# ═══════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Classify ARGUS news with AI + GCS data")
    parser.add_argument("--force", action="store_true",
                        help="Reclassify all news, even already classified")
    args = parser.parse_args()

    result = asyncio.run(classify_all_news(force=args.force))
    print(f"\nResult: {result}")
