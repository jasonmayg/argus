"""
routes/data_alert_routes.py

Threshold-based data alerts from GCS macro/risk indicators.

  GET /api/data/alerts          — Run all enabled rules, return triggered alerts
  GET /api/data/alerts/rules    — List all configured rules (for UI display)
  POST /api/data/alerts/refresh — Force cache refresh
"""

from __future__ import annotations

import json
import os
import time
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from flask import Blueprint, jsonify, request

try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False

data_alert_bp = Blueprint("data_alert_routes", __name__)

# ── Config ────────────────────────────────────────────────
THRESHOLDS_FILE = Path(os.getenv("ARGUS_THRESHOLDS_FILE", "thresholds.json"))
CACHE_TTL       = int(os.getenv("DATA_ALERT_CACHE_TTL", "300"))  # 5 min

_cache: Dict[str, Any] = {"ts": 0.0, "alerts": None, "rules": None}
_cache_lock = threading.Lock()


# ═══════════════════════════════════════════════════════════
# Threshold loading
# ═══════════════════════════════════════════════════════════

def _load_rules() -> List[Dict]:
    if not THRESHOLDS_FILE.exists():
        return []
    with open(THRESHOLDS_FILE, "r") as f:
        data = json.load(f)
    return [r for r in data.get("rules", []) if r.get("enabled", True)]


# ═══════════════════════════════════════════════════════════
# GCS data fetching (reuses data_dashboard helpers)
# ═══════════════════════════════════════════════════════════

def _get_gcs_bucket():
    """Import and reuse bucket helper from data_dashboard."""
    try:
        from data_dashboard import _get_gcs_bucket as _dgb
        return _dgb()
    except Exception:
        return None


def _get_latest_prefix(bucket) -> str:
    """Import and reuse prefix helper from data_dashboard."""
    try:
        from data_dashboard import _resolve_data_dashboard_prefix
        return _resolve_data_dashboard_prefix(bucket)
    except Exception:
        return "raw/"


def _fetch_series(bucket, prefix: str, country: str, indicator: str) -> Optional[List[float]]:
    """
    Find and download CSV for (country, indicator), return values list (newest last).
    Returns None if not found.
    """
    try:
        from data_dashboard import (
            _list_gcs_csv_blobs,
            _read_gcs_csv_text,
            _build_series_from_text,
            _infer_country_from_filename,
            _infer_indicator_from_filename,
            DEFAULT_COUNTRY_NAME_MAP,
        )
    except ImportError:
        return None

    blobs = _list_gcs_csv_blobs(bucket, prefix)

    # Reverse-lookup: country display name → prefix key
    country_key = None
    for k, v in DEFAULT_COUNTRY_NAME_MAP.items():
        if v.lower() == country.lower():
            country_key = k.lower()
            break

    target_blob = None
    for b in blobs:
        stem = Path(b.name).stem.lower()
        blob_country  = _infer_country_from_filename(stem)
        blob_indicator = _infer_indicator_from_filename(stem)

        # Global indicators (no country prefix)
        if country.lower() == "global" and blob_indicator == indicator.lower():
            target_blob = b
            break

        if (blob_country and blob_country.lower() == country.lower()
                and blob_indicator == indicator.lower()):
            target_blob = b
            break

    if not target_blob:
        return None

    text = _read_gcs_csv_text(bucket, target_blob.name)
    meta = _build_series_from_text(Path(target_blob.name).name, text)
    return meta.get("values") or []


# ═══════════════════════════════════════════════════════════
# Alert evaluation
# ═══════════════════════════════════════════════════════════

def _compare(value: float, operator: str, threshold: float) -> bool:
    ops = {
        ">":  lambda a, b: a > b,
        "<":  lambda a, b: a < b,
        ">=": lambda a, b: a >= b,
        "<=": lambda a, b: a <= b,
        "==": lambda a, b: abs(a - b) < 1e-9,
    }
    fn = ops.get(operator)
    return fn(value, threshold) if fn else False


def _evaluate_rule(
    rule: Dict,
    bucket,
    prefix: str,
) -> Optional[Dict]:
    """
    Evaluate one rule against live GCS data.
    Returns alert dict if triggered, None otherwise.
    """
    country   = rule["country"]
    indicator = rule["indicator"]
    rule_type = rule["type"]       # "absolute" | "change"
    operator  = rule["operator"]
    threshold = float(rule["threshold"])

    values = _fetch_series(bucket, prefix, country, indicator)
    if not values or len(values) < 1:
        return None

    latest = values[-1]

    if rule_type == "absolute":
        triggered = _compare(latest, operator, threshold)
        actual_value = latest
        display_value = f"{latest:.4f}"

    elif rule_type == "change":
        if len(values) < 2:
            return None
        prev = values[-2]
        if prev == 0:
            return None
        pct_change = (latest - prev) / abs(prev) * 100
        triggered = _compare(abs(pct_change), operator, threshold)
        actual_value = pct_change
        display_value = f"{pct_change:+.2f}%"
    else:
        return None

    if not triggered:
        return None

    return {
        "rule_id":      rule["id"],
        "name":         rule["name"],
        "country":      country,
        "indicator":    indicator,
        "category":     rule.get("category", "General"),
        "severity":     rule.get("severity", "Medium"),
        "type":         rule_type,
        "operator":     operator,
        "threshold":    threshold,
        "actual_value": round(actual_value, 4),
        "display_value":display_value,
        "latest_raw":   round(latest, 4),
        "description":  rule.get("description", ""),
        "triggered_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "source":       "GCS Macro Data",
        # Matrix fields
        "probability":  rule.get("probability", 3),
        "impact":       rule.get("impact", 3),
        "title":        rule.get("name", ""),
    }


def _run_all_rules() -> List[Dict]:
    """Run all enabled rules against current GCS data. Returns triggered alerts."""
    rules = _load_rules()
    if not rules:
        return []

    bucket = _get_gcs_bucket()
    if bucket is None:
        return [_make_config_error("GCS bucket unavailable")]

    prefix = _get_latest_prefix(bucket)
    alerts: List[Dict] = []

    for rule in rules:
        try:
            alert = _evaluate_rule(rule, bucket, prefix)
            if alert:
                alerts.append(alert)
        except Exception as e:
            # Don't let one bad rule break all others
            alerts.append({
                "rule_id":      rule.get("id", "unknown"),
                "name":         rule.get("name", "Unknown Rule"),
                "country":      rule.get("country", "—"),
                "indicator":    rule.get("indicator", "—"),
                "category":     "System",
                "severity":     "Low",
                "description":  f"Rule evaluation error: {str(e)[:100]}",
                "display_value":"—",
                "triggered_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                "source":       "GCS Macro Data",
                "_error":       True,
            })

    # Sort: High → Medium → Low
    severity_order = {"High": 0, "Medium": 1, "Low": 2}
    alerts.sort(key=lambda a: severity_order.get(a.get("severity", "Low"), 9))
    return alerts


def _make_config_error(msg: str) -> Dict:
    return {
        "rule_id":      "config_error",
        "name":         "Configuration Error",
        "country":      "—",
        "indicator":    "—",
        "category":     "System",
        "severity":     "Low",
        "description":  msg,
        "display_value":"—",
        "triggered_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "source":       "System",
        "_error":       True,
    }


def _get_cached_alerts() -> List[Dict]:
    with _cache_lock:
        if time.time() - _cache["ts"] < CACHE_TTL and _cache["alerts"] is not None:
            return _cache["alerts"]

    alerts = _run_all_rules()

    with _cache_lock:
        _cache["alerts"] = alerts
        _cache["ts"] = time.time()

    return alerts


# ═══════════════════════════════════════════════════════════
# Routes
# ═══════════════════════════════════════════════════════════

@data_alert_bp.get("/api/data/alerts")
def api_data_alerts():
    """
    Returns triggered alerts based on current GCS data vs thresholds.
    Query params:
      ?severity=High        — filter by severity
      ?category=Inflation   — filter by category
      ?country=USA          — filter by country
      ?fresh=1              — bypass cache
    """
    if request.args.get("fresh"):
        with _cache_lock:
            _cache["ts"] = 0.0

    try:
        alerts = _get_cached_alerts()
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

    # Apply filters
    severity = (request.args.get("severity") or "").strip()
    category = (request.args.get("category") or "").strip()
    country  = (request.args.get("country")  or "").strip()

    filtered = alerts
    if severity:
        filtered = [a for a in filtered if a.get("severity", "").lower() == severity.lower()]
    if category:
        filtered = [a for a in filtered if a.get("category", "").lower() == category.lower()]
    if country:
        filtered = [a for a in filtered if a.get("country", "").lower() == country.lower()]

    # Summary counts
    high_count   = sum(1 for a in alerts if a.get("severity") == "High" and not a.get("_error"))
    medium_count = sum(1 for a in alerts if a.get("severity") == "Medium" and not a.get("_error"))
    low_count    = sum(1 for a in alerts if a.get("severity") == "Low" and not a.get("_error"))

    return jsonify({
        "status":        "success",
        "generated_at":  datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "total_triggered": len([a for a in alerts if not a.get("_error")]),
        "summary": {
            "high":   high_count,
            "medium": medium_count,
            "low":    low_count,
        },
        "alerts": filtered,
    })


@data_alert_bp.get("/api/data/alerts/rules")
def api_data_alert_rules():
    """List all configured rules (enabled + disabled) for UI display."""
    try:
        with open(THRESHOLDS_FILE, "r") as f:
            data = json.load(f)
        rules = data.get("rules", [])
        return jsonify({
            "status": "success",
            "total":  len(rules),
            "enabled": sum(1 for r in rules if r.get("enabled", True)),
            "rules":  rules,
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@data_alert_bp.post("/api/data/alerts/refresh")
def api_data_alerts_refresh():
    """Force cache invalidation and re-run all rules."""
    with _cache_lock:
        _cache["ts"] = 0.0
        _cache["alerts"] = None
    return jsonify({"status": "ok", "message": "Alert cache cleared. Next request will re-evaluate."})


# ═══════════════════════════════════════════════════════════
# GET /api/data/alerts/matrix-items
# ═══════════════════════════════════════════════════════════

@data_alert_bp.get("/api/data/alerts/matrix-items")
def api_data_alerts_matrix_items():
    """
    Returns triggered GCS alerts formatted as Matrix scored_items,
    ready to be merged with pipeline analysis results on the frontend.
    """
    try:
        alerts = _get_cached_alerts()
        items  = []

        PROB_LABELS   = ['Unlikely','Seldom','Occasional','Likely','Frequent']
        IMPACT_LABELS = ['Negligible','Minor','Moderate','Hazardous','Catastrophic']
        MATRIX_LEVEL  = [
            ['Low','Low','Low','Medium','Medium'],
            ['Low','Low','Medium','Medium','High'],
            ['Low','Medium','Medium','High','High'],
            ['Medium','Medium','High','High','Extreme'],
            ['Medium','High','High','Extreme','Extreme'],
        ]

        for a in alerts:
            if a.get("_error"):
                continue
            prob   = int(a.get("probability", 3))
            impact = int(a.get("impact",      3))
            # clamp 1-5
            prob   = max(1, min(5, prob))
            impact = max(1, min(5, impact))
            level  = MATRIX_LEVEL[prob-1][impact-1]

            items.append({
                "title":           a["name"] + f" — {a['country']} {a['indicator'].upper()}",
                "probability":     prob,
                "impact":          impact,
                "matrix_level":    level,
                "impact_reasoning": a["description"] + f" (Actual: {a['display_value']})",
                "source":          "GCS Data Alert",
                "rule_id":         a["rule_id"],
                "category":        a.get("category", ""),
                "severity":        a.get("severity", ""),
                "triggered_at":    a.get("triggered_at", ""),
            })

        return jsonify({
            "status": "success",
            "count":  len(items),
            "items":  items,
        })

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500