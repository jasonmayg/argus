#!/usr/bin/env python3
"""
generate_actions.py

Generates AI-powered treasury action recommendations by combining:
  1. Guardian News Alerts (score >= 40, excluding NOAA weather)
  2. Matrix Scored Nodes (from pipeline analysis)
  3. Data Alerts (GCS threshold triggers)

Results are cached to shared_storage/latest_actions.json

Usage:
    python3 generate_actions.py                    # generate with default weights
    python3 generate_actions.py --persona fx_manager

    # Called by pipeline Stage 9
    from generate_actions import generate_actions
    await generate_actions(persona="cash_manager", weights={...})
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from core.llm_router import call_llm

# ── Config ─────────────────────────────────────────────────────
ACTIONS_PATH    = Path("shared_storage/latest_actions.json")
GUARDIAN_PATH   = Path("guardian_poc/data/alerts_latest.json")
RESULTS_PATH    = Path("shared_storage/latest_results.json")
CASH_DB_PATH    = Path(os.getenv("ARGUS_CASH_XLSX", "data/cash_positions.xlsx"))

# Guardian score threshold and excluded sources
MIN_GUARDIAN_SCORE  = 40
EXCLUDED_SOURCES    = {"noaa_nws", "noaa", "nws"}
EXCLUDED_CATEGORIES = {"disaster risk"}  # when source is weather-related

# Risk Posture thresholds
POSTURE_CRITICAL  = 75
POSTURE_ELEVATED  = 55
POSTURE_MODERATE  = 40

# Country flag emoji map
COUNTRY_FLAGS = {
    "USA": "🇺🇸", "US": "🇺🇸",
    "Canada": "🇨🇦", "UK": "🇬🇧",
    "Germany": "🇩🇪", "France": "🇫🇷",
    "Japan": "🇯🇵", "China": "🇨🇳",
    "Brazil": "🇧🇷", "India": "🇮🇳",
    "Mexico": "🇲🇽", "Australia": "🇦🇺",
    "Hong Kong": "🇭🇰", "Singapore": "🇸🇬",
    "South Korea": "🇰🇷", "Global": "🌐",
}

# Persona-specific context for OpenAI
PERSONA_CONTEXT = {
    "cash_manager": (
        "You are advising a Cash Manager. Prioritize liquidity, "
        "payment continuity, short-term funding, and cash mobility risks. "
        "Focus on immediate operational treasury actions."
    ),
    "fx_manager": (
        "You are advising an FX Manager. Prioritize currency volatility, "
        "hedge ratio impacts, and cross-border FX exposure. "
        "Focus on hedging strategies and FX risk mitigation."
    ),
    "risk_manager": (
        "You are advising a Risk Manager. Prioritize systemic risk, "
        "concentration risk, and escalation readiness. "
        "Focus on risk governance and escalation actions."
    ),
    "treasury_ops": (
        "You are advising a Treasury Operations specialist. Prioritize "
        "settlement risk, operational continuity, and process controls. "
        "Focus on operational execution and controls."
    ),
}


# ═══════════════════════════════════════════════════════════════
# Data loading helpers
# ═══════════════════════════════════════════════════════════════

def _load_guardian_alerts(min_score: float = MIN_GUARDIAN_SCORE, exclude: List[str] = None) -> List[Dict]:
    """Load and filter Guardian alerts."""
    if not GUARDIAN_PATH.exists():
        return []
    exclude = exclude or []
    try:
        data   = json.loads(GUARDIAN_PATH.read_text(encoding="utf-8"))
        alerts = data.get("alerts", [])
        result = []
        for a in alerts:
            score  = float(a.get("score", 0))
            source = (a.get("source") or "").lower().strip()
            cat    = (a.get("category_label") or a.get("category") or "").lower().strip()
            title  = (a.get("title") or "").strip()

            # Skip below min score
            if score < min_score:
                continue
            # Skip NOAA weather if excluded
            if "weather" in exclude and source in EXCLUDED_SOURCES:
                continue
            if "weather" in exclude and any(kw in title.lower() for kw in
                   ["advisory", "watch issued", "warning issued", "forecast"]):
                continue
            # Skip disaster if excluded
            if "disaster" in exclude and "disaster" in cat:
                continue

            result.append({
                "title":    title,
                "country":  a.get("country", "Global"),
                "category": a.get("category_label") or a.get("category_key") or "General",
                "score":    round(score, 2),
                "source":   source,
                "url":      a.get("url", ""),
                "recommendation": a.get("recommendation", ""),
            })
        return result
    except Exception as e:
        print(f"[generate_actions] Guardian load error: {e}")
        return []


def _load_matrix_nodes() -> List[Dict]:
    """Load scored matrix nodes from latest pipeline results."""
    if not RESULTS_PATH.exists():
        return []
    try:
        data   = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
        scored = (data.get("stages", {})
                      .get("analysis", {})
                      .get("analysis", {})
                      .get("scored_items", []))
        result = []
        for item in scored:
            level = item.get("matrix_level", "Low")
            if level == "Low":
                continue  # skip low-level items
            result.append({
                "title":    item.get("title", ""),
                "country":  item.get("country", "USA"),
                "category": item.get("risk_category", "General"),
                "score":    float(item.get("matrix_score", 0)),
                "probability": item.get("probability", 1),
                "impact":      item.get("impact", 1),
                "matrix_level": level,
                "source":   "pipeline",
                "rationale": item.get("impact_reasoning", ""),
            })
        return result
    except Exception as e:
        print(f"[generate_actions] Matrix load error: {e}")
        return []


def _load_data_alerts() -> List[Dict]:
    """Load GCS threshold-triggered data alerts."""
    try:
        from routes.data_alert_routes import _get_cached_alerts
        alerts = _get_cached_alerts()
        result = []
        for a in alerts:
            if a.get("_error"):
                continue
            sev = a.get("severity", "Low")
            if sev == "Low":
                continue
            result.append({
                "title":    a.get("name", ""),
                "country":  a.get("country", "Global"),
                "category": a.get("category", "General"),
                "score":    75.0 if sev == "High" else 50.0,
                "severity": sev,
                "source":   "data_alert",
                "description": a.get("description", ""),
                "display_value": a.get("display_value", ""),
                "indicator": a.get("indicator", ""),
            })
        return result
    except Exception as e:
        print(f"[generate_actions] Data alerts load error: {e}")
        return []


def _load_cash_positions() -> Dict[str, float]:
    """Load cash positions keyed by country for enrichment."""
    positions = {}
    try:
        import openpyxl
        wb = openpyxl.load_workbook(str(CASH_DB_PATH), data_only=True)
        ws = wb["positions"]
        headers = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
        for row in ws.iter_rows(min_row=2, values_only=True):
            r = dict(zip(headers, row))
            country = str(r.get("country") or r.get("Country") or "").strip()
            balance = r.get("balance") or r.get("Balance") or 0
            fx_rate = float(r.get("fx_rate") or r.get("FX Rate") or 1)
            try:
                usd_equiv = float(balance) / fx_rate
            except Exception:
                usd_equiv = 0
            if country:
                positions[country] = round(usd_equiv / 1_000_000, 1)  # in $M USD
    except Exception as e:
        print(f"[generate_actions] Cash positions load error: {e}")
    return positions


# ═══════════════════════════════════════════════════════════════
# Effective score calculation
# ═══════════════════════════════════════════════════════════════

def _compute_effective_score(alert: Dict, weights: Dict) -> float:
    """Apply category weights to base score."""
    base     = float(alert.get("score", 0))
    cat      = (alert.get("category") or "").lower().strip()

    # Map category to weight key
    CAT_MAP = {
        "market risk":      "market_risk",
        "credit risk":      "credit_risk",
        "liquidity risk":   "liquidity_risk",
        "operational risk": "operational_risk",
        "political risk":   "political_risk",
        "regulatory risk":  "regulatory_risk",
        "reputation risk":  "reputation_risk",
        "systematic risk":  "systematic_risk",
        "systemic risk":    "systematic_risk",
        "disaster risk":    "disaster_risk",
        "supply risk":      "supply_risk",
        "demand risk":      "demand_risk",
        "process risk":     "process_risk",
        "business risk":    "business_risk",
        "funding":          "market_risk",
        "fx risk":          "market_risk",
        "inflation":        "market_risk",
        "monetary policy":  "regulatory_risk",
        "geopolitical":     "political_risk",
        "natural disaster": "disaster_risk",
    }
    wkey   = CAT_MAP.get(cat, "")
    weight = weights.get(wkey, 1.0) if wkey else 1.0
    return min(100.0, round(base * weight, 2))


# ═══════════════════════════════════════════════════════════════
# Risk posture calculation
# ═══════════════════════════════════════════════════════════════

def _compute_risk_posture(alerts: List[Dict]) -> str:
    if not alerts:
        return "CONTAINED"
    scores     = [a["effective_score"] for a in alerts]
    max_score  = max(scores)
    high_count = sum(1 for a in alerts if a["effective_score"] >= POSTURE_ELEVATED)

    if max_score >= POSTURE_CRITICAL or high_count >= 3:
        return "CRITICAL"
    if max_score >= POSTURE_ELEVATED or high_count >= 1:
        return "ELEVATED"
    if max_score >= POSTURE_MODERATE or len(alerts) >= 3:
        return "MODERATE"
    return "CONTAINED"


# ═══════════════════════════════════════════════════════════════
# LLM generation (routed via core.llm_router, task=actions_generation)
# ═══════════════════════════════════════════════════════════════

def _call_llm(
    system: str,
    user: str,
    max_tokens: int = 1500,
    response_format: str = "text",
) -> Optional[str]:
    """Route an actions_generation call. Returns None on failure so callers can
    fall back to deterministic defaults without aborting the pipeline."""
    try:
        return call_llm(
            task="actions_generation",
            system=system,
            user=user,
            max_tokens=max_tokens,
            temperature=0.3,
            response_format=response_format,
        ).strip()
    except Exception as e:
        print(f"[generate_actions] LLM exception: {e}")
        return None


def _generate_executive_summary(
    country_groups: Dict[str, List[Dict]],
    posture: str,
    persona: str,
    persona_ctx: str,
) -> str:
    """Generate the executive summary paragraph."""
    alert_lines = []
    for country, alerts in list(country_groups.items())[:5]:
        top = alerts[0]
        alert_lines.append(
            f"  - {country}: {top['title']} "
            f"(score {top['effective_score']:.0f}, {top['category']})"
        )

    system = f"""You are a senior treasury risk analyst. {persona_ctx}
Write a concise 2-3 sentence executive summary for a treasury risk briefing.
Be direct and specific. Mention key countries and risk categories.
Do not use bullet points. Write in flowing prose."""

    user = f"""Risk posture: {posture}
Top risk signals:
{chr(10).join(alert_lines)}

Write the executive summary now."""

    result = _call_llm(system, user, max_tokens=200)
    return result or f"Risk posture is {posture}. {len(country_groups)} countries require attention."


def _generate_action_card(
    country: str,
    category: str,
    alerts: List[Dict],
    cash_usd_m: Optional[float],
    persona: str,
    persona_ctx: str,
) -> Dict:
    """Generate a single action card via OpenAI."""
    alert_summary = "\n".join([
        f"  - {a['title']} (score {a['effective_score']:.0f})"
        for a in alerts[:3]
    ])
    cash_line = f"Current cash exposure in {country}: USD {cash_usd_m:.1f}M" if cash_usd_m else ""

    system = f"""You are a senior treasury risk analyst. {persona_ctx}
Generate a specific treasury action recommendation.
Return ONLY valid JSON — no prose, no markdown fences.

JSON schema:
{{
  "recommended_action": "string — specific action to take",
  "why": "string — 2 sentences explaining why this action is needed now",
  "expected_outcome": "string — what happens if action is taken",
  "pros": ["string", "string"],
  "cons": ["string", "string"],
  "alternatives": ["string", "string"]
}}"""

    user = f"""Country: {country}
Risk category: {category}
{cash_line}
Alerts:
{alert_summary}

Generate the action recommendation."""

    raw = _call_llm(system, user, max_tokens=500, response_format="json")
    if raw:
        try:
            raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
            return json.loads(raw)
        except Exception:
            pass

    # Fallback if OpenAI fails
    return {
        "recommended_action": f"Review {category} exposure in {country} and assess mitigation options.",
        "why": f"Multiple {category} signals detected in {country} with elevated effective scores.",
        "expected_outcome": "Reduced exposure and improved risk visibility.",
        "pros": ["Proactive risk management", "Early intervention opportunity"],
        "cons": ["Execution cost", "May require management approval"],
        "alternatives": ["Monitor and reassess in 24 hours", "Partial hedge as interim measure"],
    }


# ═══════════════════════════════════════════════════════════════
# Main orchestration
# ═══════════════════════════════════════════════════════════════

async def generate_actions(
    persona: str = "cash_manager",
    weights: Optional[Dict] = None,
    settings: Optional[Dict] = None,
) -> Dict:
    """
    Main entry point. Collects all alerts, applies weights,
    groups by country, generates AI recommendations, caches result.
    """
    print(f"\n[generate_actions] Starting for persona={persona}")
    weights  = weights  or {}
    settings = settings or {}

    # Extract filter settings
    min_score = float(settings.get("min_score", MIN_GUARDIAN_SCORE))
    exclude   = settings.get("exclude", ["weather", "disaster"])
    sources   = settings.get("sources", ["guardian", "data", "matrix"])

    # ── 1. Collect all alerts ──────────────────────────────────
    guardian_alerts = _load_guardian_alerts(min_score=min_score, exclude=exclude) if "guardian" in sources else []
    matrix_nodes    = _load_matrix_nodes()   if "matrix"   in sources else []
    data_alerts     = _load_data_alerts()    if "data"     in sources else []
    cash_positions  = _load_cash_positions()

    print(f"  Guardian alerts: {len(guardian_alerts)}")
    print(f"  Matrix nodes:    {len(matrix_nodes)}")
    print(f"  Data alerts:     {len(data_alerts)}")

    # Apply min_score to matrix and data alerts too
    matrix_nodes = [a for a in matrix_nodes if float(a.get("score", 0)) >= min_score]
    data_alerts  = [a for a in data_alerts  if float(a.get("score", 0)) >= min_score]

    all_alerts = guardian_alerts + matrix_nodes + data_alerts

    if not all_alerts:
        result = {
            "status":       "success",
            "posture":      "CONTAINED",
            "summary":      "No significant alerts detected. Risk profile within normal parameters.",
            "country_groups": [],
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "persona":      persona,
        }
        _save_actions(result)
        return result

    # ── 2. Apply weights → effective score ─────────────────────
    for a in all_alerts:
        a["effective_score"] = _compute_effective_score(a, weights)

    # Sort by effective score descending
    all_alerts.sort(key=lambda a: a["effective_score"], reverse=True)

    # ── 3. Compute risk posture ────────────────────────────────
    posture = _compute_risk_posture(all_alerts)
    print(f"  Risk posture: {posture}")

    # ── 4. Group by country → then by category ─────────────────
    by_country: Dict[str, Dict[str, List[Dict]]] = {}
    for a in all_alerts:
        country = a.get("country", "Global")
        cat     = a.get("category", "General")
        by_country.setdefault(country, {}).setdefault(cat, []).append(a)

    # Sort countries by max effective score
    country_order = sorted(
        by_country.keys(),
        key=lambda c: max(a["effective_score"] for alerts in by_country[c].values() for a in alerts),
        reverse=True,
    )

    # ── 5. Generate executive summary ─────────────────────────
    persona_ctx = PERSONA_CONTEXT.get(persona, PERSONA_CONTEXT["cash_manager"])
    flat_country_groups = {c: [a for cats in by_country[c].values() for a in cats] for c in country_order}
    summary = _generate_executive_summary(flat_country_groups, posture, persona, persona_ctx)

    # ── 6. Generate action cards per country/category ──────────
    country_groups_out = []
    for country in country_order:
        cat_groups  = by_country[country]
        cash_usd_m  = cash_positions.get(country)
        flag        = COUNTRY_FLAGS.get(country, "🌐")

        # Sort categories by max score
        sorted_cats = sorted(
            cat_groups.keys(),
            key=lambda c: max(a["effective_score"] for a in cat_groups[c]),
            reverse=True,
        )

        category_cards = []
        for cat in sorted_cats:
            cat_alerts  = sorted(cat_groups[cat], key=lambda a: a["effective_score"], reverse=True)
            top_score   = cat_alerts[0]["effective_score"]

            print(f"  Generating action: {country} / {cat} (score {top_score:.0f})")
            action_data = _generate_action_card(
                country, cat, cat_alerts, cash_usd_m, persona, persona_ctx
            )

            # Severity label
            severity = "HIGH" if top_score >= 65 else "MEDIUM" if top_score >= 40 else "WATCH"

            category_cards.append({
                "category":          cat,
                "severity":          severity,
                "effective_score":   round(top_score, 1),
                "alert_count":       len(cat_alerts),
                "top_alert_title":   cat_alerts[0].get("title", ""),
                "action":            action_data,
                "alerts":            [
                    {
                        "title":           a.get("title", ""),
                        "effective_score": round(a["effective_score"], 1),
                        "source":          a.get("source", ""),
                        "score":           a.get("score", 0),
                        "weight_applied":  round(a["effective_score"] / max(a.get("score", 1), 0.01), 2),
                    }
                    for a in cat_alerts[:5]
                ],
            })

        country_max_score = max(
            card["effective_score"] for card in category_cards
        )
        country_groups_out.append({
            "country":        country,
            "flag":           flag,
            "max_score":      round(country_max_score, 1),
            "cash_usd_m":     cash_usd_m,
            "category_cards": category_cards,
        })

    # ── 7. Build and cache result ──────────────────────────────
    result = {
        "status":         "success",
        "settings_used":  {"min_score": min_score, "exclude": exclude, "sources": sources},
        "posture":        posture,
        "summary":        summary,
        "country_groups": country_groups_out,
        "total_alerts":   len(all_alerts),
        "generated_at":   datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "persona":        persona,
        "weights_used":   bool(weights),
    }

    _save_actions(result)
    print(f"[generate_actions] Done. {len(country_groups_out)} countries, posture={posture}")
    return result


def _save_actions(result: Dict) -> None:
    try:
        ACTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
        ACTIONS_PATH.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        print(f"[generate_actions] Save error: {e}")


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Generate ARGUS treasury action recommendations")
    parser.add_argument("--persona", default="cash_manager",
                        choices=["cash_manager", "fx_manager", "risk_manager", "treasury_ops"])
    args = parser.parse_args()

    result = asyncio.run(generate_actions(persona=args.persona))
    print(f"\nResult: posture={result['posture']}, "
          f"countries={len(result.get('country_groups', []))}, "
          f"alerts={result.get('total_alerts', 0)}")