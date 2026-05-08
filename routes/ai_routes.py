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

DB_PATH = Path(os.getenv("ARGUS_NEWS_DB", "data/fed_news/argus_news.db"))

_news_cache: Dict[str, Any] = {"ts": 0, "items": []}
NEWS_CACHE_TTL = 60

PERSONA_LABELS = {
    "cash_manager":        "Cash Manager",
    "fx_manager":          "FX Manager",
    "treasury_ops":        "Treasury Operations",
    "treasury_technology": "Treasury Technology",
    "risk_manager":        "Risk Manager",
}


# ═══════════════════════════════════════════════════════════════
# OpenAI helpers
# ═══════════════════════════════════════════════════════════════

def _openai(system: str, user: str, max_tokens: int = 1024) -> str:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set in .env")
    resp = requests.post(
        OPENAI_URL,
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
        json={
            "model":    OPENAI_MODEL,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "max_tokens": max_tokens,
            "temperature": 0.4,
        },
        timeout=30,
    )
    if not resp.ok:
        raise RuntimeError(f"OpenAI error {resp.status_code}: {resp.text[:300]}")
    return resp.json()["choices"][0]["message"]["content"].strip()


def _openai_with_history(system: str, history: List[Dict], user_msg: str, max_tokens: int = 1024) -> str:
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
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
        json={"model": OPENAI_MODEL, "messages": messages, "max_tokens": max_tokens, "temperature": 0.4},
        timeout=30,
    )
    if not resp.ok:
        raise RuntimeError(f"OpenAI error {resp.status_code}: {resp.text[:300]}")
    return resp.json()["choices"][0]["message"]["content"].strip()


# ═══════════════════════════════════════════════════════════════
# Pipeline results helper
# ═══════════════════════════════════════════════════════════════

def _load_latest_results() -> Optional[Dict]:
    for path in ["shared_storage/latest_results.json", "shared_storage/results.json", "data/latest_results.json"]:
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

def _query_news_db(limit: int = 30, min_score: Optional[int] = None, category: Optional[str] = None) -> List[Dict]:
    if not DB_PATH.exists():
        return []
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur  = conn.cursor()
    sql  = """
        SELECT n.id, n.date, n.title, n.link, n.content, n.created_at,
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
    try:
        rows = _query_news_db(limit=limit, min_score=3)
        if not rows:
            return ""
        lines = ["- Recent high-risk news (from ARGUS DB):"]
        for row in rows:
            lines.append(f"  • [{row.get('risk_category') or 'General'}] {row['title']} (score={row.get('risk_score') or '—'})")
        return "\n".join(lines)
    except Exception:
        return ""


def _get_top_alerts_for_recommendations(limit: int = 5) -> List[Dict]:
    try:
        rows = _query_news_db(limit=limit, min_score=2)
        return [{"title": r.get("title", ""), "category": r.get("risk_category", "General"), "score": r.get("risk_score", 0)} for r in rows]
    except Exception:
        return []


def _get_data_alerts_summary() -> str:
    try:
        from routes.data_alert_routes import _get_cached_alerts
        alerts    = _get_cached_alerts()
        triggered = [a for a in alerts if not a.get("_error")]
        if not triggered:
            return "No data threshold breaches currently triggered."
        lines = []
        for a in triggered[:5]:
            lines.append(f"  • {a.get('name','')} — {a.get('country','')} ({a.get('severity','')}) Actual: {a.get('display_value','')}")
        return "\n".join(lines)
    except Exception:
        return "Data alerts unavailable."


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
    now_utc      = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
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
        return jsonify({"status": "success", "brief": brief_text, "signals": signals, "generated_at": now_utc})
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
    results      = _load_latest_results()
    pipeline_ctx = _results_summary_text(results) if results else ""
    news_ctx     = _news_summary_text(limit=8)
    system       = CHAT_SYSTEM
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
        return jsonify({"status": "success", "cached": False, "items": [], "message": f"No news found in {DB_PATH}."})
    items = []
    for row in rows:
        items.append({
            "id": row["id"], "date": row["date"], "title": row["title"], "url": row["link"],
            "source": "ARGUS News DB",
            "summary": row["analysis_text"] or (row["content"] or "")[:200],
            "risk_level": _risk_score_to_level(row["risk_score"]),
            "risk_score": row["risk_score"],
            "risk_category": row["risk_category"] or "Uncategorised",
            "keywords": row["keywords"] or "",
        })
    level_order = {"high": 0, "medium": 1, "low": 2}
    items.sort(key=lambda x: (level_order.get(x["risk_level"], 9), x["date"]))
    items.reverse()
    _news_cache = {"ts": time.time(), "items": items}
    return jsonify({"status": "success", "cached": False, "total": len(items), "items": items})


# ═══════════════════════════════════════════════════════════════
# GET /api/home/risk-categories
# ═══════════════════════════════════════════════════════════════

@ai_bp.get("/api/home/risk-categories")
def api_home_risk_categories():
    news_cats: dict = {}
    if DB_PATH.exists():
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        cur  = conn.cursor()
        try:
            cur.execute("""
                SELECT ra.risk_category, COUNT(*) as count, n.title as top_title
                FROM risk_analysis ra
                JOIN news n ON n.id = ra.news_id
                WHERE ra.risk_category IS NOT NULL AND ra.risk_category != ''
                  AND ra.risk_category != 'General'
                  AND ra.risk_score >= 2
                  AND n.date >= date('now', '-90 days')
                GROUP BY ra.risk_category
                ORDER BY count DESC
                LIMIT 8
            """)
            rows = [dict(r) for r in cur.fetchall()]
            if not rows:
                cur.execute("""
                    SELECT km.keyword as risk_category, COUNT(DISTINCT km.news_id) as count,
                           (SELECT n2.title FROM news n2 JOIN keyword_matches km2 ON km2.news_id = n2.id
                            WHERE km2.keyword = km.keyword ORDER BY n2.date DESC LIMIT 1) as top_title
                    FROM keyword_matches km
                    GROUP BY km.keyword ORDER BY count DESC LIMIT 8
                """)
                rows = [dict(r) for r in cur.fetchall()]
            if not rows:
                cur.execute("""
                    SELECT CASE
                        WHEN title LIKE '%rate%' OR title LIKE '%interest%' THEN 'Interest Rate'
                        WHEN title LIKE '%inflation%' OR title LIKE '%CPI%' THEN 'Inflation'
                        WHEN title LIKE '%enforcement%' OR title LIKE '%regulation%' THEN 'Regulatory'
                        WHEN title LIKE '%bank%' THEN 'Banking'
                        ELSE 'General' END as risk_category,
                        COUNT(*) as count, MAX(title) as top_title
                    FROM news GROUP BY risk_category ORDER BY count DESC LIMIT 6
                """)
                rows = [dict(r) for r in cur.fetchall()]
            for r in rows:
                cat = r['risk_category']
                news_cats[cat] = {"risk_category": cat, "count": r['count'], "top_title": r['top_title'], "source": "news", "data_count": 0}
        except Exception:
            pass
        finally:
            conn.close()

    try:
        rules_path = Path(os.getenv("ARGUS_THRESHOLDS_FILE", "thresholds.json"))
        if rules_path.exists():
            with open(rules_path) as f:
                threshold_data = json.load(f)
            try:
                from routes.data_alert_routes import _get_cached_alerts
                for a in _get_cached_alerts():
                    if a.get("_error"): continue
                    cat = a.get("category", "General")
                    if cat in news_cats:
                        news_cats[cat]["data_count"] += 1
                        news_cats[cat]["source"]      = "both"
                    else:
                        news_cats[cat] = {"risk_category": cat, "count": 0, "top_title": a.get("name", ""), "source": "data", "data_count": 1}
            except Exception:
                for rule in threshold_data.get("rules", []):
                    if not rule.get("enabled", True): continue
                    cat = rule.get("category", "General")
                    if cat not in news_cats:
                        news_cats[cat] = {"risk_category": cat, "count": 0, "top_title": rule.get("description", ""), "source": "data", "data_count": 0}
    except Exception:
        pass

    categories = sorted(news_cats.values(), key=lambda c: -(c["count"] + c["data_count"] * 2))[:8]
    return jsonify({"status": "success", "categories": categories, "total": len(categories), "sources": ["news_db", "gcs_data_alerts"]})


# ═══════════════════════════════════════════════════════════════
# POST /api/recommendations
# ═══════════════════════════════════════════════════════════════

RECOMMENDATIONS_SYSTEM = """You are ARGUS, an institutional treasury risk intelligence system.
Generate a concise, actionable daily recommendation guide for a treasury professional.

Output ONLY a valid JSON object in this exact format, no other text:
{
  "focus": "One or two sentences on what to focus on today and why, specific to the role.",
  "where": [
    "Specific ARGUS module to check — e.g. Risk Monitor → Regulatory alerts",
    "Another module — e.g. Data Alerts → SOFR threshold breach"
  ],
  "actions": [
    "Concrete action tied to today's signals — e.g. Review liquidity buffer vs 30-day forecast",
    "Another action — e.g. Check FX hedge coverage given USD strength",
    "Third action — e.g. Escalate enforcement action review to compliance team"
  ]
}

Rules:
- focus: persona-specific, reference actual signals present, max 2 sentences
- where: 2-3 items, always name the specific ARGUS module
- actions: 2-3 concrete actions, directly tied to today's signals
- Tone: direct, professional, actionable
- Output JSON only — no markdown, no code fences, no extra text"""


@ai_bp.post("/api/recommendations")
def api_recommendations():
    """
    Generate today's action recommendations for the active persona.
    Called on page load and on persona switch.
    """
    try:
        body    = request.get_json(force=True, silent=True) or {}
        persona = str(body.get("persona") or "cash_manager").strip()
    except Exception:
        persona = "cash_manager"

    persona_label = PERSONA_LABELS.get(persona, persona.replace("_", " ").title())
    now_utc       = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    top_alerts   = _get_top_alerts_for_recommendations(limit=5)
    data_summary = _get_data_alerts_summary()

    alert_ctx = "No significant news alerts currently."
    if top_alerts:
        alert_lines = "\n".join(
            f"  • [{a['category']}] score={a['score']} — {a['title'][:80]}"
            for a in top_alerts
        )
        alert_ctx = f"Top news alerts:\n{alert_lines}"

    user_prompt = f"""Timestamp: {now_utc}
Role: {persona_label}

{alert_ctx}

Triggered data alerts (macro indicators):
{data_summary}

Generate the daily recommendation JSON for this {persona_label}."""

    try:
        raw = _openai(RECOMMENDATIONS_SYSTEM, user_prompt, max_tokens=600)

        # Strip markdown fences if model added them anyway
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        rec     = json.loads(raw)
        focus   = str(rec.get("focus", ""))
        where   = rec.get("where", [])
        actions = rec.get("actions", [])
        if not isinstance(where,   list): where   = [str(where)]
        if not isinstance(actions, list): actions = [str(actions)]

        return jsonify({
            "status":        "success",
            "persona":       persona,
            "persona_label": persona_label,
            "generated_at":  now_utc,
            "focus":         focus,
            "where":         where[:3],
            "actions":       actions[:3],
        })

    except json.JSONDecodeError:
        return jsonify({
            "status":        "success",
            "persona":       persona,
            "persona_label": persona_label,
            "generated_at":  now_utc,
            "focus":         "Review today's active risk signals and monitor threshold breaches.",
            "where":         ["Risk Monitor → News Alerts", "Data Alerts → triggered thresholds"],
            "actions":       ["Review top-scored alerts", "Check data threshold breaches", "Update risk register if needed"],
        })

    except RuntimeError as e:
        return jsonify({"status": "error", "message": str(e)}), 500