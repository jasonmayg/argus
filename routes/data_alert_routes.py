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
LOCAL_GCS_HISTORY_CANDIDATES = [
    Path("guardian_poc/models/google_downside_gcs_features_demo.csv"),
]


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


def _fetch_series_points(bucket, prefix: str, country: str, indicator: str) -> Optional[List[Tuple[str, float]]]:
    """
    Find and download CSV for (country, indicator), return [(date, value)] ordered oldest→newest.
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
    target_blob = None
    for b in blobs:
        stem = Path(b.name).stem.lower()
        blob_country = _infer_country_from_filename(stem)
        blob_indicator = _infer_indicator_from_filename(stem)
        if country.lower() == "global" and blob_indicator == indicator.lower():
            target_blob = b
            break
        if blob_country and blob_country.lower() == country.lower() and blob_indicator == indicator.lower():
            target_blob = b
            break

    if not target_blob:
        return None

    text = _read_gcs_csv_text(bucket, target_blob.name)
    meta = _build_series_from_text(Path(target_blob.name).name, text)
    dates = meta.get("dates") or []
    values = meta.get("values") or []
    points: List[Tuple[str, float]] = []
    for d, v in zip(dates, values):
        try:
            points.append((str(d), float(v)))
        except Exception:
            continue
    return points or None


def _load_local_history_frame():
    if not HAS_PANDAS:
        return None
    for path in LOCAL_GCS_HISTORY_CANDIDATES:
        if path.exists():
            try:
                df = pd.read_csv(path)
                if "date" in df.columns and not df.empty:
                    return df
            except Exception:
                continue
    return None


def _match_local_history_column(df, indicator: str) -> Optional[str]:
    base_prefixes = [
        f"gcs_macro_{indicator}",
        f"gcs_risk_{indicator}",
    ]
    best_col = None
    best_rank = None
    for col in df.columns:
        if col == "date" or col.endswith("_chg_1") or col.endswith("_roll_5"):
            continue
        rank = None
        for idx, prefix in enumerate(base_prefixes):
            if col == prefix:
                return col
            if col.startswith(prefix + "_"):
                # Prefer unsuffixed exact matches, then stable prefixed variants.
                rank = 10 + idx
                break
        if rank is None:
            continue
        if best_rank is None or rank < best_rank:
            best_rank = rank
            best_col = col
    return best_col


def _fetch_local_series_points(country: str, indicator: str) -> Optional[List[Tuple[str, float]]]:
    """
    Local fallback for historical threshold evaluation based on cached demo GCS features.
    The cache is already date-aligned and gives us a stable source even when live GCS
    credentials are unavailable in the local UI runtime.
    """
    df = _load_local_history_frame()
    if df is None:
        return None

    col = _match_local_history_column(df, indicator)
    if not col:
        return None

    out: List[Tuple[str, float]] = []
    for _, row in df[["date", col]].dropna().iterrows():
        try:
            out.append((str(row["date"]), float(row[col])))
        except Exception:
            continue
    return out or None


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

    # ── Deduplicate: keep only highest severity per (country, indicator) ──
    severity_order = {"High": 0, "Medium": 1, "Low": 2}
    best: Dict[str, Dict] = {}
    for a in alerts:
        if a.get("_error"):
            continue
        key = f"{a.get('country','')}__{a.get('indicator','')}"
        existing = best.get(key)
        if existing is None:
            best[key] = a
        else:
            # Keep the one with higher severity (lower order number)
            if severity_order.get(a.get("severity","Low"), 9) < severity_order.get(existing.get("severity","Low"), 9):
                best[key] = a

    # Rebuild alerts list: deduped real alerts + errors
    deduped = list(best.values())
    errors  = [a for a in alerts if a.get("_error")]
    alerts  = deduped + errors

    # Sort: High → Medium → Low
    alerts.sort(key=lambda a: severity_order.get(a.get("severity", "Low"), 9))
    return alerts


def _evaluate_rule_history(rule: Dict, bucket, prefix: str, limit: int = 10) -> List[Dict]:
    """
    Evaluate one rule across its available history and return most recent triggered points.
    """
    country = rule["country"]
    indicator = rule["indicator"]
    rule_type = rule["type"]
    operator = rule["operator"]
    threshold = float(rule["threshold"])

    points = _fetch_series_points(bucket, prefix, country, indicator)
    if not points:
        return []

    out: List[Dict] = []
    if rule_type == "absolute":
        for date_str, latest in points:
            if _compare(latest, operator, threshold):
                out.append({
                    "rule_id": rule["id"],
                    "name": rule["name"],
                    "country": country,
                    "indicator": indicator,
                    "category": rule.get("category", "General"),
                    "severity": rule.get("severity", "Medium"),
                    "type": rule_type,
                    "operator": operator,
                    "threshold": threshold,
                    "actual_value": round(latest, 4),
                    "display_value": f"{latest:.4f}",
                    "latest_raw": round(latest, 4),
                    "description": rule.get("description", ""),
                    "triggered_at": date_str,
                    "source": "GCS Macro Data",
                    "historical": True,
                })
    elif rule_type == "change":
        for idx in range(1, len(points)):
            prev = points[idx - 1][1]
            latest = points[idx][1]
            if prev == 0:
                continue
            pct_change = (latest - prev) / abs(prev) * 100
            if _compare(abs(pct_change), operator, threshold):
                out.append({
                    "rule_id": rule["id"],
                    "name": rule["name"],
                    "country": country,
                    "indicator": indicator,
                    "category": rule.get("category", "General"),
                    "severity": rule.get("severity", "Medium"),
                    "type": rule_type,
                    "operator": operator,
                    "threshold": threshold,
                    "actual_value": round(pct_change, 4),
                    "display_value": f"{pct_change:+.2f}%",
                    "latest_raw": round(latest, 4),
                    "description": rule.get("description", ""),
                    "triggered_at": points[idx][0],
                    "source": "GCS Macro Data",
                    "historical": True,
                })

    out.sort(key=lambda a: str(a.get("triggered_at", "")), reverse=True)
    return out[:limit]


def _evaluate_rule_history_from_points(rule: Dict, points: List[Tuple[str, float]], limit: int = 10, source: str = "Local GCS Demo Cache") -> List[Dict]:
    """
    Shared historical trigger builder for local fallback points.
    """
    rule_type = rule["type"]
    operator = rule["operator"]
    threshold = float(rule["threshold"])
    country = rule["country"]
    indicator = rule["indicator"]

    out: List[Dict] = []
    if rule_type == "absolute":
        for date_str, latest in points:
            if _compare(latest, operator, threshold):
                out.append({
                    "rule_id": rule["id"],
                    "name": rule["name"],
                    "country": country,
                    "indicator": indicator,
                    "category": rule.get("category", "General"),
                    "severity": rule.get("severity", "Medium"),
                    "type": rule_type,
                    "operator": operator,
                    "threshold": threshold,
                    "actual_value": round(latest, 4),
                    "display_value": f"{latest:.4f}",
                    "latest_raw": round(latest, 4),
                    "description": rule.get("description", ""),
                    "triggered_at": date_str,
                    "source": source,
                    "historical": True,
                })
    elif rule_type == "change":
        for idx in range(1, len(points)):
            prev = points[idx - 1][1]
            latest = points[idx][1]
            if prev == 0:
                continue
            pct_change = (latest - prev) / abs(prev) * 100
            if _compare(abs(pct_change), operator, threshold):
                out.append({
                    "rule_id": rule["id"],
                    "name": rule["name"],
                    "country": country,
                    "indicator": indicator,
                    "category": rule.get("category", "General"),
                    "severity": rule.get("severity", "Medium"),
                    "type": rule_type,
                    "operator": operator,
                    "threshold": threshold,
                    "actual_value": round(pct_change, 4),
                    "display_value": f"{pct_change:+.2f}%",
                    "latest_raw": round(latest, 4),
                    "description": rule.get("description", ""),
                    "triggered_at": points[idx][0],
                    "source": source,
                    "historical": True,
                })

    out.sort(key=lambda a: str(a.get("triggered_at", "")), reverse=True)
    return out[:limit]


def _load_recent_historical_triggers(limit: int = 10) -> List[Dict]:
    """Return the most recent historical rule triggers across all enabled rules."""
    rules = _load_rules()
    if not rules:
        return []
    bucket = _get_gcs_bucket()
    all_hits: List[Dict] = []
    if bucket is not None:
        prefix = _get_latest_prefix(bucket)
        for rule in rules:
            try:
                all_hits.extend(_evaluate_rule_history(rule, bucket, prefix, limit=limit))
            except Exception:
                continue

    if not all_hits:
        for rule in rules:
            try:
                points = _fetch_local_series_points(rule["country"], rule["indicator"])
                if not points:
                    continue
                all_hits.extend(
                    _evaluate_rule_history_from_points(
                        rule,
                        points,
                        limit=limit,
                        source="Local GCS Demo Cache",
                    )
                )
            except Exception:
                continue

    all_hits.sort(key=lambda a: str(a.get("triggered_at", "")), reverse=True)
    seen = set()
    out = []
    for item in all_hits:
        key = (item.get("rule_id"), item.get("triggered_at"))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= limit:
            break
    return out


def _build_recent_trigger_feed(current_alerts: List[Dict], limit: int = 10) -> List[Dict]:
    """
    Always return a recent trigger feed. Current triggers are included first when present,
    then older historical triggers fill the remainder up to the requested limit.
    """
    real_current = [a for a in current_alerts if not a.get("_error")]
    historical = _load_recent_historical_triggers(limit=max(limit * 3, 20))

    merged: List[Dict] = []
    seen = set()
    for item in sorted(real_current, key=lambda a: str(a.get("triggered_at", "")), reverse=True):
        key = (item.get("rule_id"), item.get("triggered_at"), item.get("name"))
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)

    for item in historical:
        key = (item.get("rule_id"), item.get("triggered_at"), item.get("name"))
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
        if len(merged) >= limit:
            break

    merged.sort(key=lambda a: str(a.get("triggered_at", "")), reverse=True)
    return merged[:limit]


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

    recent_alerts = _build_recent_trigger_feed(filtered, limit=10)
    has_current = bool([a for a in filtered if not a.get("_error")])
    recent_message = ""
    if recent_alerts:
        if has_current:
            recent_message = "Showing the 10 most recent triggered data alerts, including current triggers."
        else:
            recent_source = recent_alerts[0].get("source", "historical cache")
            recent_message = f"No current threshold breaches; showing the 10 most recent triggered data alerts from {recent_source}."

    return jsonify({
        "status":        "success",
        "generated_at":  datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "total_triggered": len([a for a in alerts if not a.get("_error")]),
        "historical_fallback": not has_current,
        "historical_message": recent_message,
        "summary": {
            "high":   high_count,
            "medium": medium_count,
            "low":    low_count,
        },
        "alerts": recent_alerts or filtered,
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
            ['Medium','Medium','High','High','High'],
            ['Medium','High','High','High','High'],
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
