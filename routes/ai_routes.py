"""
routes/ai_routes.py

AI-powered endpoints using OpenAI:
  POST /api/brief  — Generate an AI intelligence brief
  POST /api/chat   — Guardian chatbot
  GET  /api/news   — Reads from data/fed_news/argus_news.db (SQLite)
"""

from __future__ import annotations

import os
import json
import sqlite3
import time
import requests
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import Blueprint, jsonify, request

# ── Blueprint ─────────────────────────────────────────────────
ai_bp = Blueprint("ai_routes", __name__)

# ── Config ────────────────────────────────────────────────────
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL   = os.getenv("OPENAI_MODEL", "gpt-4o")
OPENAI_URL     = "https://api.openai.com/v1/chat/completions"

# Path to the news SQLite database
DB_PATH = Path(os.getenv("ARGUS_NEWS_DB", "data/fed_news/argus_news.db"))

# News cache
_news_cache: Dict[str, Any] = {"ts": 0, "items": []}
NEWS_CACHE_TTL = 60  # seconds


# ═══════════════════════════════════════════════════════════════
# OpenAI helper
# ═══════════════════════════════════════════════════════════════

def _openai(system: str, user: str, max_tokens: int = 1024) -> str:
    """Call OpenAI and return assistant text. Raises RuntimeError on failure."""
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set in .env")

    resp = requests.post(
        OPENAI_URL,
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type":  "application/json",
        },
        json={
            "model":       OPENAI_MODEL,
            "messages":    [
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
            "max_tokens":  max_tokens,
            "temperature": 0.4,
        },
        timeout=30,
    )
    if not resp.ok:
        raise RuntimeError(f"OpenAI error {resp.status_code}: {resp.text[:300]}")
    return resp.json()["choices"][0]["message"]["content"].strip()


def _openai_with_history(
    system: str,
    history: List[Dict],
    user_msg: str,
    max_tokens: int = 1024,
) -> str:
    """Call OpenAI with multi-turn conversation history."""
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set in .env")

    messages = [{"role": "system", "content": system}]
    for turn in history[-10:]:
        role    = "assistant" if turn.get("role") == "ai" else "user"
        content = (turn.get("content") or "").strip()
        if content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_msg})

    resp = requests.post(
        OPENAI_URL,
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type":  "application/json",
        },
        json={
            "model":       OPENAI_MODEL,
            "messages":    messages,
            "max_tokens":  max_tokens,
            "temperature": 0.4,
        },
        timeout=30,
    )
    if not resp.ok:
        raise RuntimeError(f"OpenAI error {resp.status_code}: {resp.text[:300]}")
    return resp.json()["choices"][0]["message"]["content"].strip()


# ═══════════════════════════════════════════════════════════════
# Pipeline results helper
# ═══════════════════════════════════════════════════════════════

def _load_latest_results() -> Optional[Dict]:
    candidates = [
        "shared_storage/latest_results.json",
        "shared_storage/results.json",
        "data/latest_results.json",
    ]
    for path in candidates:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            continue
    return None


def _results_summary_text(results: Dict) -> str:
    lines: List[str] = []

    ng = results.get("stages", {}).get("news_gathering", {})
    if ng.get("count"):
        lines.append(f"- Signals ingested: {ng['count']}")

    rc = results.get("stages", {}).get("risk_classification", {})
    if rc:
        lines.append(f"- Risk classification: {json.dumps(rc)[:400]}")

    analysis = results.get("stages", {}).get("analysis", {}).get("analysis", {})
    scored   = analysis.get("scored_items", [])
    if scored:
        top = sorted(scored, key=lambda x: x.get("total_score", 0), reverse=True)[:5]
        lines.append("- Top scored risk items:")
        for item in top:
            lines.append(
                f"  • [{item.get('matrix_level','?')}] {item.get('title','?')} "
                f"(prob={item.get('probability','?')}, impact={item.get('impact','?')})"
            )

    return "\n".join(lines) if lines else "No structured pipeline results available yet."


# ═══════════════════════════════════════════════════════════════
# SQLite news helpers
# ═══════════════════════════════════════════════════════════════

def _query_news_db(
    limit: int = 30,
    min_score: Optional[int] = None,
    category: Optional[str] = None,
) -> List[Dict]:
    if not DB_PATH.exists():
        return []

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    sql = """
        SELECT
            n.id, n.date, n.title, n.link, n.content, n.created_at,
            ra.risk_category, ra.risk_score, ra.analysis_text,
            GROUP_CONCAT(km.keyword, ', ') AS keywords
        FROM news n
        LEFT JOIN risk_analysis ra ON ra.news_id = n.id
        LEFT JOIN keyword_matches km ON km.news_id = n.id
        WHERE 1=1
    """
    params: List[Any] = []

    if min_score is not None:
        sql += " AND (ra.risk_score IS NULL OR ra.risk_score >= ?)"
        params.append(min_score)

    if category:
        sql += " AND ra.risk_category = ?"
        params.append(category)

    sql += " GROUP BY n.id ORDER BY n.date DESC, ra.risk_score DESC LIMIT ?"
    params.append(limit)

    cur.execute(sql, params)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def _risk_score_to_level(score: Optional[int]) -> str:
    if score is None: return "low"
    if score >= 7:    return "high"
    if score >= 4:    return "medium"
    return "low"


def _news_summary_text(limit: int = 10) -> str:
    """Recent high-risk news as text context for Claude."""
    try:
        rows = _query_news_db(limit=limit, min_score=3)
        if not rows:
            return ""
        lines = ["- Recent high-risk news (from ARGUS DB):"]
        for row in rows:
            cat   = row.get("risk_category") or "General"
            score = row.get("risk_score") or "—"
            lines.append(f"  • [{cat}] {row['title']} (score={score})")
        return "\n".join(lines)
    except Exception:
        return ""


# ═══════════════════════════════════════════════════════════════
# POST /api/brief
# ═══════════════════════════════════════════════════════════════

BRIEF_SYSTEM = """You are ARGUS, an institutional-grade treasury risk intelligence system.
Produce a concise, authoritative intelligence brief for a treasury professional.

Rules:
- 3-5 sentences maximum. Direct and data-driven.
- Focus on: macroeconomic risk, geopolitical risk, FX volatility, liquidity.
- Tone: professional, measured, never alarmist.
- Write flowing prose — no bullet points in the brief itself.
- End with overall risk posture: ELEVATED, MODERATE, or CONTAINED.

After the prose, append a JSON block in this exact format:
```json
{
  "signals": [
    {"label": "Short signal label", "level": "high"},
    {"label": "Short signal label", "level": "medium"},
    {"label": "Short signal label", "level": "low"}
  ]
}
```
"""

@ai_bp.post("/api/brief")
def api_brief():
    try:
        persona = (request.json or {}).get("persona", "cash_manager")
    except Exception:
        persona = "cash_manager"
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    results      = _load_latest_results()
    pipeline_ctx = _results_summary_text(results) if results else "No pipeline data available yet."
    news_ctx     = _news_summary_text(limit=10)

    user_prompt = f"""Current timestamp: {now_utc}
Active persona: {persona}

Pipeline context:
{pipeline_ctx}

{news_ctx}

Generate the treasury intelligence brief now."""

    try:
        raw        = _openai(BRIEF_SYSTEM, user_prompt, max_tokens=800)
        brief_text = raw
        signals: List[Dict] = []

        if "```json" in raw:
            parts      = raw.split("```json", 1)
            brief_text = parts[0].strip()
            try:
                json_str = parts[1].split("```")[0].strip()
                signals  = json.loads(json_str).get("signals", [])
            except Exception:
                pass

        return jsonify({
            "status":       "success",
            "brief":        brief_text,
            "signals":      signals,
            "generated_at": now_utc,
        })

    except RuntimeError as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ═══════════════════════════════════════════════════════════════
# POST /api/chat
# ═══════════════════════════════════════════════════════════════

CHAT_SYSTEM = """You are the ARGUS Guardian — an AI assistant embedded in an institutional treasury risk platform.

You have deep expertise in:
- Macroeconomic risk: central bank policy, interest rates, CPI, SOFR
- Geopolitical risk: sovereign debt, trade tensions, regional conflicts
- FX & liquidity risk: currency volatility, cross-border flows
- Treasury operations: cash management, hedging, funding gaps

Use the pipeline and news context provided to answer questions accurately.
If data is unavailable, say so and answer from general knowledge.
Tone: precise, professional, concise. No disclaimers or filler."""

@ai_bp.post("/api/chat")
def api_chat():
    if not request.is_json:
        return jsonify({"status": "error", "message": "JSON required"}), 400

    body     = request.json
    user_msg = (body.get("message") or "").strip()
    persona  = body.get("persona", "cash_manager")
    history  = body.get("history", [])

    if not user_msg:
        return jsonify({"status": "error", "message": "message is required"}), 400

    # Build system prompt with live context
    results      = _load_latest_results()
    pipeline_ctx = _results_summary_text(results) if results else ""
    news_ctx     = _news_summary_text(limit=8)

    system = CHAT_SYSTEM
    if pipeline_ctx:
        system += f"\n\nLatest pipeline context:\n{pipeline_ctx}"
    if news_ctx:
        system += f"\n\nRecent news (ARGUS DB):\n{news_ctx}"

    try:
        reply = _openai_with_history(system, history, user_msg, max_tokens=1024)
        return jsonify({"status": "success", "reply": reply})
    except RuntimeError as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ═══════════════════════════════════════════════════════════════
# GET /api/news
# ═══════════════════════════════════════════════════════════════

@ai_bp.get("/api/news")
def api_news():
    global _news_cache

    if time.time() - _news_cache["ts"] < NEWS_CACHE_TTL and _news_cache["items"]:
        return jsonify({"status": "success", "cached": True, "items": _news_cache["items"]})

    limit     = int(request.args.get("limit", 30))
    min_score = request.args.get("min_score")
    category  = request.args.get("category")
    min_score = int(min_score) if min_score else None

    try:
        rows = _query_news_db(limit=limit, min_score=min_score, category=category)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

    if not rows:
        return jsonify({
            "status":  "success",
            "cached":  False,
            "items":   [],
            "message": f"No news found in {DB_PATH}. Ensure the pipeline has run.",
        })

    items = []
    for row in rows:
        items.append({
            "id":            row["id"],
            "date":          row["date"],
            "title":         row["title"],
            "url":           row["link"],
            "source":        "ARGUS News DB",
            "summary":       row["analysis_text"] or (row["content"] or "")[:200],
            "risk_level":    _risk_score_to_level(row["risk_score"]),
            "risk_score":    row["risk_score"],
            "risk_category": row["risk_category"] or "Uncategorised",
            "keywords":      row["keywords"] or "",
        })

    level_order = {"high": 0, "medium": 1, "low": 2}
    items.sort(key=lambda x: (level_order.get(x["risk_level"], 9), x["date"]))
    items.reverse()

    _news_cache = {"ts": time.time(), "items": items}

    return jsonify({
        "status": "success",
        "cached": False,
        "total":  len(items),
        "items":  items,
    })


# ═══════════════════════════════════════════════════════════════
# GET /api/home/risk-categories
# ═══════════════════════════════════════════════════════════════

@ai_bp.get("/api/home/risk-categories")
def api_home_risk_categories():
    """
    Returns risk categories merged from two sources:
    1. News DB (risk_analysis / keyword_matches / title keywords)
    2. GCS Data Alerts (from thresholds.json triggered rules)
    """
    # ── Part 1: News DB categories ──────────────────────────
    news_cats: dict = {}   # category → {count, top_title, source}

    if DB_PATH.exists():
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        cur  = conn.cursor()
        try:
            # Try risk_analysis first
            cur.execute("""
                SELECT ra.risk_category, COUNT(*) as count,
                       n.title as top_title
                FROM risk_analysis ra
                JOIN news n ON n.id = ra.news_id
                WHERE ra.risk_category IS NOT NULL AND ra.risk_category != ''
                  AND ra.risk_category != 'General'
                GROUP BY ra.risk_category
                ORDER BY count DESC
                LIMIT 8
            """)
            rows = [dict(r) for r in cur.fetchall()]

            # Fallback: keyword_matches
            if not rows:
                cur.execute("""
                    SELECT km.keyword as risk_category,
                           COUNT(DISTINCT km.news_id) as count,
                           (SELECT n2.title FROM news n2
                            JOIN keyword_matches km2 ON km2.news_id = n2.id
                            WHERE km2.keyword = km.keyword
                            ORDER BY n2.date DESC LIMIT 1) as top_title
                    FROM keyword_matches km
                    GROUP BY km.keyword
                    ORDER BY count DESC
                    LIMIT 8
                """)
                rows = [dict(r) for r in cur.fetchall()]

            # Final fallback: title keyword grouping
            if not rows:
                cur.execute("""
                    SELECT
                        CASE
                            WHEN title LIKE '%rate%' OR title LIKE '%interest%' THEN 'Interest Rate'
                            WHEN title LIKE '%inflation%' OR title LIKE '%CPI%' THEN 'Inflation'
                            WHEN title LIKE '%enforcement%' OR title LIKE '%regulation%' THEN 'Regulatory'
                            WHEN title LIKE '%bank%' THEN 'Banking'
                            ELSE 'General'
                        END as risk_category,
                        COUNT(*) as count,
                        MAX(title) as top_title
                    FROM news
                    GROUP BY risk_category
                    ORDER BY count DESC
                    LIMIT 6
                """)
                rows = [dict(r) for r in cur.fetchall()]

            for r in rows:
                cat = r['risk_category']
                news_cats[cat] = {
                    "risk_category": cat,
                    "count":         r['count'],
                    "top_title":     r['top_title'],
                    "source":        "news",
                    "data_count":    0,
                }
        except Exception:
            pass
        finally:
            conn.close()

    # ── Part 2: GCS Data Alert categories ──────────────────
    try:
        rules_path = Path(os.getenv("ARGUS_THRESHOLDS_FILE", "thresholds.json"))
        if rules_path.exists():
            with open(rules_path) as f:
                threshold_data = json.load(f)

            # Import the alert evaluation (reuse existing cache)
            try:
                from routes.data_alert_routes import _get_cached_alerts
                alerts = _get_cached_alerts()
                for a in alerts:
                    if a.get("_error"):
                        continue
                    cat = a.get("category", "General")
                    if cat in news_cats:
                        # Merge: increment data_count
                        news_cats[cat]["data_count"] += 1
                        news_cats[cat]["source"]      = "both"
                    else:
                        # New category from GCS
                        news_cats[cat] = {
                            "risk_category": cat,
                            "count":         0,
                            "top_title":     a.get("name", ""),
                            "source":        "data",
                            "data_count":    1,
                        }
            except Exception:
                # If alerts unavailable, still show rules as potential categories
                for rule in threshold_data.get("rules", []):
                    if not rule.get("enabled", True):
                        continue
                    cat = rule.get("category", "General")
                    if cat not in news_cats:
                        news_cats[cat] = {
                            "risk_category": cat,
                            "count":         0,
                            "top_title":     rule.get("description", ""),
                            "source":        "data",
                            "data_count":    0,
                        }
    except Exception:
        pass

    # ── Merge and sort ──────────────────────────────────────
    categories = list(news_cats.values())

    # Sort: most signals first, then data alerts, then news only
    def sort_key(c):
        total = c["count"] + c["data_count"] * 2  # data alerts weighted 2x
        return -total

    categories.sort(key=sort_key)
    categories = categories[:8]   # top 8

    return jsonify({
        "status":     "success",
        "categories": categories,
        "total":      len(categories),
        "sources":    ["news_db", "gcs_data_alerts"],
    })