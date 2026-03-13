#!/usr/bin/env python3
"""
ARGUS Web Frontend
Flask-based web interface for the multi-agent system with real-time demonstrations
"""

import asyncio
import csv
import json
from flask import Flask, render_template, jsonify, request
from flask_cors import CORS
from datetime import datetime
import threading
import os
from pathlib import Path
import re
import subprocess
from typing import Any
from io import StringIO
from collections import Counter
from functools import lru_cache
import unicodedata
from json import JSONEncoder
try:
    from flask_talisman import Talisman
except Exception:
    Talisman = None

from agents_system import AgentOrchestrator, RiskClassifierAgent
from core.agents import (
    AnalysisAgent,
    PERSONA_REGISTRY,
    FX_RELATED_CATEGORIES,
    build_alert_list,
    build_country_board,
    load_monitored_countries,
    build_fx_summary,
    build_cash_manager_dashboard,
    build_generic_persona_dashboard,
)
from data_dashboard import data_dashboard_bp
from config import API_PORT


class SafeJSONEncoder(JSONEncoder):
    """Custom JSON encoder that safely handles None values and nested structures"""
    def default(self, obj):
        if obj is None:
            return None
        if isinstance(obj, dict):
            return {str(k): self.default(v) for k, v in obj.items() if v is not None}
        if isinstance(obj, (list, tuple)):
            return [self.default(item) for item in obj if item is not None]
        return super().default(obj)


def sanitize_for_json(obj: Any) -> Any:
    """
    Recursively sanitize data structure to remove None values and ensure JSON compatibility.
    Prevents TypeError when jsonify tries to sort keys containing None values.
    """
    if obj is None:
        return None

    if isinstance(obj, dict):
        # Filter out None values and recursively sanitize nested structures
        sanitized = {}
        for key, value in obj.items():
            if value is not None:
                sanitized_key = str(key) if not isinstance(key, str) else key
                sanitized[sanitized_key] = sanitize_for_json(value)
        return sanitized

    if isinstance(obj, (list, tuple)):
        # Recursively sanitize list items, filtering out None
        return [sanitize_for_json(item) for item in obj if item is not None]

    if isinstance(obj, (str, int, float, bool)):
        return obj

    # For other types (datetime, etc), convert to string
    return str(obj)


app = Flask(__name__)
CORS(app)

# Configure Flask JSON encoder to handle None values safely
app.json_encoder = SafeJSONEncoder
# IMPORTANT: Disable sort_keys to prevent comparison errors with None values
app.config['JSON_SORT_KEYS'] = False

app.register_blueprint(data_dashboard_bp)

GUARDIAN_SOURCE_WHITELIST = {
    "gdelt",
    "reliefweb",
    "world_bank",
    "noaa_nws",
    "nasa_eonet",
    "usgs",
    "fred",
    "us_treasury_auction",
    "bank_of_canada",
    "ofac_sls",
}

DEFAULT_GUARDIAN_GCS_BUCKET = "your-gcs-bucket"
_BOOL_FALSE_SET = {"0", "false", "no", "off"}
_ALERTS_SNAPSHOT_RE = re.compile(r"alerts_\d{8}_\d{6}\.json$")
_RAW_DT_SEGMENT_RE = re.compile(r"^dt=\d{4}-\d{2}-\d{2}$")

# Global orchestrator and execution state
orchestrator = AgentOrchestrator()
execution_state = {
    "is_running": False,
    "current_stage": None,
    "result": None,
    "agents_status": {}
}


def _env_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return str(val).strip().lower() not in _BOOL_FALSE_SET


def _normalize_gcs_prefix(prefix: str) -> str:
    if not prefix:
        return ""
    return prefix.strip("/").strip()


def _join_gcs_prefix(prefix: str, suffix: str) -> str:
    p = _normalize_gcs_prefix(prefix)
    s = suffix.lstrip("/")
    return f"{p}/{s}" if p else s


def _init_gcs_client(storage_module) -> tuple[Any, str]:
    """
    Build GCS client with explicit fallback credentials.
    Returns (client, auth_source) for diagnostics.
    """
    project = os.getenv("GOOGLE_CLOUD_PROJECT", "").strip() or None
    adc_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if adc_path:
        return storage_module.Client(project=project), f"adc:{adc_path}"

    root = Path(__file__).resolve().parent
    key_candidates = [
        root / "keys" / "treasury-reader.json",
        root / "keys" / "service-account.json",
    ]
    for key_path in key_candidates:
        if key_path.exists():
            client = storage_module.Client.from_service_account_json(str(key_path), project=project)
            return client, f"service_account:{key_path}"

    return storage_module.Client(project=project), "adc:default"


def _parse_json_text(raw_text: str) -> dict | None:
    try:
        obj = json.loads(raw_text)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _contains_keyword(text: str, kw: str) -> bool:
    if not text or not kw:
        return False
    text_n = _norm_text(text)
    kw = _norm_text(kw)
    if not kw:
        return False
    # Use token boundary for short single-token keywords to reduce false positives.
    if " " not in kw and len(kw) <= 4 and kw.isalpha():
        return bool(re.search(rf"\b{re.escape(kw)}\b", text_n))
    return kw in text_n


def _norm_text(s: str) -> str:
    """
    Unicode-aware normalization for multilingual keyword matching.
    Keeps CJK scripts while normalizing accents/case variants.
    """
    t = unicodedata.normalize("NFKC", str(s or "")).casefold()
    # Strip combining marks so "México" and "Mexico" match.
    t = "".join(ch for ch in unicodedata.normalize("NFKD", t) if not unicodedata.combining(ch))
    return t


@lru_cache(maxsize=1)
def _load_country_keyword_map() -> dict[str, list[str]]:
    """
    Build 11-country keyword map from config with supplemental treasury terms.
    """
    defaults = {
        "USA": [
            "united states", "united states of america", "usa", "u.s.", "us",
            "美国", "美國", "ee.uu.", "etats-unis", "vereinigte staaten",
            "usd", "federal reserve", "treasury", "washington",
        ],
        "UK": [
            "united kingdom", "uk", "u.k.", "britain", "great britain", "england",
            "英国", "英國", "royaume-uni", "reino unido", "vereinigtes konigreich",
            "london", "boe", "sterling", "gbp",
        ],
        "Germany": [
            "germany", "deutschland", "allemagne", "alemania", "德国", "德國",
            "berlin", "bundesbank", "eur", "euro area",
        ],
        "Japan": [
            "japan", "nippon", "nihon", "日本", "japon", "japao",
            "tokyo", "boj", "bank of japan", "yen", "jpy",
        ],
        "France": [
            "france", "french republic", "francia", "frankreich", "法国", "法國",
            "paris", "banque de france", "eur",
        ],
        "Canada": [
            "canada", "kanada", "加拿大", "canadá", "ottawa", "bank of canada", "cad", "loonie",
        ],
        "Brazil": [
            "brazil", "brasil", "brasilia", "巴西", "brésil", "brasilien",
            "bcb", "real", "brl",
        ],
        "India": [
            "india", "bharat", "भारत", "印度", "inde", "indien",
            "new delhi", "rbi", "rupee", "inr",
        ],
        "Australia": [
            "australia", "australie", "australien", "澳大利亚", "澳洲",
            "canberra", "rba", "australian dollar", "aud",
        ],
        "Mexico": [
            "mexico", "méxico", "mexican", "墨西哥", "mexique",
            "banxico", "mxn", "peso",
        ],
        "China": [
            "china", "prc", "people's republic of china", "mainland china",
            "中国", "中國", "zhongguo", "chine", "china continental",
            "beijing", "pboc", "yuan", "renminbi", "rmb", "cny",
        ],
    }
    root = _resolve_alphabet_root()
    cfg_path = root / "guardian_poc" / "config" / "countries.yaml" if root else None
    if cfg_path and cfg_path.exists():
        try:
            import yaml
            cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            countries = cfg.get("countries") or {}
            for c, info in countries.items():
                aliases = info.get("aliases") or []
                base = defaults.get(c, [c.lower()])
                merged = list(dict.fromkeys([_norm_text(str(x)) for x in base + aliases + [c]]))
                defaults[c] = merged
        except Exception:
            pass
    # Normalize defaults for matching.
    for c in list(defaults.keys()):
        defaults[c] = list(dict.fromkeys([_norm_text(str(x)) for x in defaults[c]]))
    return defaults


def _infer_country_for_row(raw_country: str, text: str) -> str:
    rc = str(raw_country or "").strip()
    if rc and rc.lower() not in {"unknown", "na", "n/a", "none"}:
        return rc
    kw_map = _load_country_keyword_map()
    best_country = "Unknown"
    best_hits = 0
    for country, kws in kw_map.items():
        hits = 0
        for kw in kws:
            if _contains_keyword(text, kw):
                hits += 1
        if hits > best_hits:
            best_country = country
            best_hits = hits
    return best_country if best_hits > 0 else "Unknown"


@lru_cache(maxsize=1)
def _load_risk_keyword_rules() -> list[tuple[str, list[str], list[str]]]:
    """
    Returns [(label, primary_keywords, high_alert_keywords), ...]
    """
    rules: list[tuple[str, list[str], list[str]]] = []
    root = _resolve_alphabet_root()
    cfg_path = root / "guardian_poc" / "config" / "risk_keywords.yaml" if root else None
    if not cfg_path or not cfg_path.exists():
        return rules
    def _extra_terms_for_label(label: str) -> tuple[list[str], list[str]]:
        l = _norm_text(label)
        # primary, high-alert
        mapping = {
            "market risk": (
                ["市场风险", "mercado", "riesgo de mercado", "risque de marche", "marktrisiko", "volatilite", "波动", "волатильность", "تقلب"],
                ["汇率暴跌", "闪崩", "crash boursier", "flash crash", "panico cambial", "обвал", "انهيار"],
            ),
            "credit risk": (
                ["信用风险", "riesgo de credito", "risque de credit", "kreditrisiko", "default", "违约", "дефолт", "تعثر"],
                ["主权违约", "支付违约", "insolvencia", "insolvabilite", "банкрот", "إفلاس"],
            ),
            "liquidity risk": (
                ["流动性风险", "riesgo de liquidez", "risque de liquidite", "liquiditatsrisiko", "bank run", "ликвидность", "سيولة"],
                ["流动性枯竭", "挤兑", "liquidity freeze", "retrait limite", "заморозка ликвидности", "تجميد السيولة"],
            ),
            "political risk": (
                ["政治风险", "riesgo politico", "risque politique", "politisches risiko", "制裁", "tarifas", "санкц", "войн", "احتجاج", "عقوبات"],
                ["军事冲突", "state of emergency", "estado de emergencia", "冲突升级", "ядерн", "اشتباكات", "حرب", "конфликт"],
            ),
            "regulatory risk": (
                ["监管风险", "riesgo regulatorio", "risque reglementaire", "regulierungsrisiko", "capital controls", "资本管制", "регулирован", "تنظيمي"],
                ["监管执法", "licence revokee", "transfer ban", "转移禁令", "запрет перевода", "حظر التحويل"],
            ),
            "systematic risk": (
                ["系统性风险", "riesgo sistemico", "risque systemique", "systemisches risiko", "contagion", "连锁反应", "системный риск", "مخاطر نظامية"],
                ["熔断", "circuit breaker", "市场停牌", "suspension du marche", "приостановка торгов", "وقف التداول"],
            ),
            "disaster risk": (
                ["灾害风险", "riesgo de desastre", "risque de catastrophe", "katastrophenrisiko", "地震", "洪水", "台风", "hurricane", "землетрясение", "فيضانات"],
                ["基础设施受损", "port closure", "机场关闭", "power grid failure", "закрытие порта", "انقطاع الكهرباء"],
            ),
        }
        for key, val in mapping.items():
            if key in l:
                return val
        return ([], [])

    try:
        import yaml
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        tax = cfg.get("risk_taxonomy") or {}
        for _k, info in tax.items():
            label = str(info.get("label") or "Market Risk")
            primaries = [_norm_text(str(x)) for x in (info.get("primary") or []) if str(x).strip()]
            highs = [_norm_text(str(x)) for x in (info.get("high_alert") or []) if str(x).strip()]
            extra_p, extra_h = _extra_terms_for_label(label)
            primaries = list(dict.fromkeys(primaries + [_norm_text(x) for x in extra_p]))
            highs = list(dict.fromkeys(highs + [_norm_text(x) for x in extra_h]))
            rules.append((label, primaries, highs))
    except Exception:
        return []
    return rules


def _infer_category_and_score(text: str) -> tuple[str, float]:
    rules = _load_risk_keyword_rules()
    if not rules:
        return ("Unclassified", 0.0)
    best_label = "Unclassified"
    best_score = 0.0
    for label, primaries, highs in rules:
        p_hits = sum(1 for kw in primaries if _contains_keyword(text, kw))
        h_hits = sum(1 for kw in highs if _contains_keyword(text, kw))
        if p_hits == 0 and h_hits == 0:
            continue
        raw = 18 + p_hits * 10 + h_hits * 22
        score = min(95.0, float(raw))
        if score > best_score:
            best_score = score
            best_label = label
    return best_label, round(best_score, 2)


def _severity_text_to_score(text: str) -> float:
    t = _norm_text(text)
    if t in {"critical", "severe", "严重", "紧急", "critique"}:
        return 90.0
    if t in {"high", "major", "高", "alto", "eleve"}:
        return 78.0
    if t in {"medium", "moderate", "中", "medio", "moyen"}:
        return 52.0
    if t in {"low", "minor", "低", "bajo", "faible"}:
        return 25.0
    return 0.0


def _normalize_score_0_100(raw_value: Any) -> float:
    score = _to_float(raw_value, default=0.0)
    # Probability-like fields are often 0~1.
    if 0 < score <= 1:
        score = score * 100
    if score < 0:
        return 0.0
    if score > 100:
        return 100.0
    return round(score, 2)


def _score_from_raw_row(row: dict) -> float:
    for key in ("score", "risk_score", "severity_score", "probability", "risk_probability"):
        if row.get(key) not in (None, ""):
            s = _normalize_score_0_100(row.get(key))
            if s > 0:
                return s
    # Fallback to textual levels when numeric score is missing.
    for key in ("risk_level", "level", "severity"):
        if row.get(key):
            s = _severity_text_to_score(str(row.get(key)))
            if s > 0:
                return s
    return 0.0


def _normalize_row_keys(row: dict) -> dict:
    out: dict[str, Any] = {}
    for k, v in (row or {}).items():
        key = str(k or "").strip().lower()
        if not key:
            continue
        key = key.replace(" ", "_").replace("-", "_")
        out[key] = v
    return out


def _pick_first(row: dict, keys: tuple[str, ...], default: str = "") -> str:
    for k in keys:
        val = row.get(k)
        if val is None:
            continue
        txt = str(val).strip()
        if txt:
            return txt
    return default


def _build_csv_reader(text: str):
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        return csv.DictReader(StringIO(text), dialect=dialect)
    except Exception:
        return csv.DictReader(StringIO(text))


def _read_alerts_from_gcs_raw_csvs(bucket, prefix: str) -> list[dict]:
    """
    Read raw CSV files from GCS prefix and normalize into Guardian alert rows.
    Supports multiple upstream CSV schemas.
    """
    alerts: list[dict] = []
    dt_match = re.search(r"(dt=\d{4}-\d{2}-\d{2})", prefix)
    raw_dt = dt_match.group(1) if dt_match else ""
    blobs = list(bucket.list_blobs(prefix=prefix))
    csv_blobs = [b for b in blobs if str(b.name).lower().endswith(".csv")]
    for blob in sorted(csv_blobs, key=lambda x: x.name):
        try:
            text = blob.download_as_text()
        except Exception:
            continue
        try:
            reader = _build_csv_reader(text)
        except Exception:
            continue
        for row in reader:
            if not isinstance(row, dict):
                continue
            nrow = _normalize_row_keys(row)
            source = _pick_first(nrow, ("source", "provider", "api_source", "feed", "publisher"), "gcs_raw")
            raw_country = _pick_first(
                nrow,
                ("country", "country_code", "countrycode", "nation", "region", "location_country", "iso2", "iso3"),
                "Unknown",
            )
            raw_category = _pick_first(
                nrow,
                ("category_label", "category", "risk_category", "type", "risk_type", "topic"),
                "",
            )
            score = _score_from_raw_row(nrow)
            published_at = _pick_first(nrow, ("published_at", "published", "timestamp", "time", "datetime", "event_time"), "")
            title = _pick_first(nrow, ("title", "headline", "event", "summary", "description", "text"), "")
            summary = _pick_first(nrow, ("summary", "description", "text", "body"), "")
            url = _pick_first(nrow, ("url", "link", "source_url", "article_url", "source_link"), "")
            combined_text = " ".join([title, summary, raw_category, url]).strip().lower()

            country = _infer_country_for_row(raw_country, combined_text)
            inferred_category, inferred_score = _infer_category_and_score(combined_text)
            category = raw_category or inferred_category or "Unclassified"
            if score <= 0 and inferred_score > 0:
                score = inferred_score
            alerts.append({
                "country": country,
                "category_label": category,
                "score": score,
                "severity": _pick_first(nrow, ("severity", "risk_level", "level"), ""),
                "recency": _pick_first(nrow, ("recency",), ""),
                "source": source,
                "title": title,
                "url": url,
                "published_at": published_at,
                "recommendation": _pick_first(nrow, ("recommendation", "suggestion", "action"), ""),
                "recommendation_source": _pick_first(nrow, ("recommendation_source",), "gcs_raw"),
                "rationale": _pick_first(nrow, ("rationale", "reason"), ""),
                "raw_dt": raw_dt,
            })
    return alerts


def _safe_ratio(num: float, den: float) -> float:
    if den <= 0:
        return 0.0
    return num / den


def _build_daily_warnings(alert_rows: list[dict], raw_dt_segments: list[str], threshold: float = 40.0) -> dict:
    """
    Compare latest dt partition vs previous dt partition and generate operational warnings.
    """
    if not raw_dt_segments:
        return {
            "latest_dt": None,
            "previous_dt": None,
            "warnings": [{"level": "info", "title": "No partition metadata", "detail": "raw_dt segments unavailable."}],
            "latest_events": 0,
            "previous_events": 0,
            "delta_events": 0,
            "latest_triggered": 0,
            "previous_triggered": 0,
            "delta_triggered": 0,
        }

    ordered = sorted(raw_dt_segments)
    latest_dt = ordered[-1]
    previous_dt = ordered[-2] if len(ordered) >= 2 else None
    latest_rows = [r for r in alert_rows if str(r.get("raw_dt") or "") == latest_dt]
    previous_rows = [r for r in alert_rows if str(r.get("raw_dt") or "") == str(previous_dt or "")]

    latest_n = len(latest_rows)
    prev_n = len(previous_rows)
    delta_n = latest_n - prev_n
    growth = (delta_n / prev_n) if prev_n > 0 else None
    latest_triggered = len([r for r in latest_rows if float(r.get("score") or 0) >= threshold])
    prev_triggered = len([r for r in previous_rows if float(r.get("score") or 0) >= threshold])
    delta_triggered = latest_triggered - prev_triggered

    warnings: list[dict] = []
    if previous_dt is None:
        warnings.append({"level": "info", "title": "No previous partition", "detail": f"Only {latest_dt} is available."})
    else:
        if prev_n == 0 and latest_n > 0:
            warnings.append({"level": "warn", "title": "Baseline missing", "detail": f"{previous_dt} has 0 events, latest {latest_dt} has {latest_n}."})
        elif growth is not None and growth >= 0.5:
            warnings.append({"level": "warn", "title": "Event spike", "detail": f"Events increased {growth*100:.1f}% ({prev_n} -> {latest_n})."})
        elif growth is not None and growth <= -0.5:
            warnings.append({"level": "warn", "title": "Event drop", "detail": f"Events dropped {-growth*100:.1f}% ({prev_n} -> {latest_n})."})

    unknown_count = sum(1 for r in latest_rows if str(r.get("country") or "").strip().lower() in {"", "unknown", "na", "n/a", "none"})
    unknown_ratio = _safe_ratio(unknown_count, latest_n)
    if latest_n > 0 and unknown_ratio >= 0.3:
        warnings.append({"level": "high", "title": "Country mapping issue", "detail": f"{unknown_ratio*100:.1f}% rows are Unknown country in {latest_dt}."})

    zero_score = sum(1 for r in latest_rows if float(r.get("score") or 0) <= 0)
    zero_ratio = _safe_ratio(zero_score, latest_n)
    if latest_n > 0 and zero_ratio >= 0.8:
        warnings.append({"level": "high", "title": "Score mapping issue", "detail": f"{zero_ratio*100:.1f}% rows have score=0 in {latest_dt}."})

    latest_sources = Counter(str(r.get("source") or "unknown") for r in latest_rows)
    prev_sources = Counter(str(r.get("source") or "unknown") for r in previous_rows)
    new_sources = sorted([s for s in latest_sources if s not in prev_sources and s != ""])
    if new_sources:
        warnings.append({"level": "info", "title": "New sources detected", "detail": f"{len(new_sources)} new source(s): {', '.join(new_sources[:4])}"})

    if previous_dt is not None:
        latest_countries = Counter(str(r.get("country") or "Unknown") for r in latest_rows)
        prev_countries = Counter(str(r.get("country") or "Unknown") for r in previous_rows)
        if latest_countries:
            top_country, top_cnt = latest_countries.most_common(1)[0]
            prev_top = prev_countries.get(top_country, 0)
            if top_cnt >= 10 and prev_top > 0 and top_cnt >= prev_top * 2:
                warnings.append({"level": "warn", "title": "Country concentration spike", "detail": f"{top_country}: {prev_top} -> {top_cnt} events."})

    if not warnings:
        warnings.append({"level": "ok", "title": "No structural anomalies", "detail": "Latest day is stable vs previous day."})

    return {
        "latest_dt": latest_dt,
        "previous_dt": previous_dt,
        "latest_events": latest_n,
        "previous_events": prev_n,
        "delta_events": delta_n,
        "latest_triggered": latest_triggered,
        "previous_triggered": prev_triggered,
        "delta_triggered": delta_triggered,
        "warnings": warnings[:8],
    }


def _discover_raw_dt_segments(bucket, raw_base_prefix: str) -> list[str]:
    """
    Discover dt=YYYY-MM-DD folder segments under raw base prefix by inspecting blob paths.
    Returns sorted segment names: ["dt=2026-02-10", ...]
    """
    base = _normalize_gcs_prefix(raw_base_prefix)
    prefix = f"{base}/" if base else ""
    found = set()
    try:
        for blob in bucket.list_blobs(prefix=prefix):
            name = str(blob.name or "")
            parts = [p for p in name.split("/") if p]
            for idx, seg in enumerate(parts):
                if seg == base and idx + 1 < len(parts):
                    nxt = parts[idx + 1]
                    if _RAW_DT_SEGMENT_RE.fullmatch(nxt):
                        found.add(nxt)
                elif base == "" and _RAW_DT_SEGMENT_RE.fullmatch(seg):
                    found.add(seg)
    except Exception:
        return []
    return sorted(found)


def _load_guardian_snapshot_from_gcs() -> dict[str, Any]:
    """
    Load latest Guardian snapshot from GCS.
    Returns a status dict and snapshot payload when available.
    """
    if not _env_bool("GUARDIAN_GCS_ENABLED", True):
        return {
            "ok": False,
            "snapshot": None,
            "snapshot_ref": "",
            "previous_snapshot": None,
            "message": "GCS disabled by GUARDIAN_GCS_ENABLED",
            "bucket": "",
            "prefix": "",
        }

    bucket_name = os.getenv("GUARDIAN_GCS_BUCKET", DEFAULT_GUARDIAN_GCS_BUCKET).strip()
    prefix = _normalize_gcs_prefix(os.getenv("GUARDIAN_GCS_PREFIX", ""))
    explicit_latest = os.getenv("GUARDIAN_GCS_LATEST_OBJECT", "").strip().lstrip("/")
    if not bucket_name:
        return {
            "ok": False,
            "snapshot": None,
            "snapshot_ref": "",
            "previous_snapshot": None,
            "message": "missing GUARDIAN_GCS_BUCKET",
            "bucket": "",
            "prefix": prefix,
        }

    try:
        from google.cloud import storage  # type: ignore
    except Exception as e:
        return {
            "ok": False,
            "snapshot": None,
            "snapshot_ref": "",
            "previous_snapshot": None,
            "message": f"google-cloud-storage unavailable: {str(e)}",
            "bucket": bucket_name,
            "prefix": prefix,
        }

    try:
        client, auth_source = _init_gcs_client(storage)
        bucket = client.bucket(bucket_name)
    except Exception as e:
        return {
            "ok": False,
            "snapshot": None,
            "snapshot_ref": "",
            "previous_snapshot": None,
            "message": f"GCS client init failed: {str(e)}",
            "bucket": bucket_name,
            "prefix": prefix,
        }

    def _download_blob_json(blob_name: str) -> dict | None:
        try:
            blob = bucket.blob(blob_name)
            txt = blob.download_as_text()
            return _parse_json_text(txt)
        except Exception:
            return None

    latest_candidates = []
    if explicit_latest:
        latest_candidates.append(explicit_latest)
    latest_candidates.extend([
        _join_gcs_prefix(prefix, "guardian_poc/data/alerts_latest.json"),
        _join_gcs_prefix(prefix, "alerts_latest.json"),
    ])

    loaded_snapshot = None
    loaded_object = ""
    for name in latest_candidates:
        loaded_snapshot = _download_blob_json(name)
        if loaded_snapshot is not None:
            loaded_object = name
            break

    snapshot_candidates = []
    try:
        for blob in client.list_blobs(bucket_name, prefix=prefix or None):
            nm = blob.name
            base = nm.split("/")[-1]
            if _ALERTS_SNAPSHOT_RE.fullmatch(base):
                snapshot_candidates.append(nm)
    except Exception as e:
        if loaded_snapshot is None:
            return {
                "ok": False,
                "snapshot": None,
                "snapshot_ref": "",
                "previous_snapshot": None,
                "message": f"GCS list failed: {str(e)}",
                "bucket": bucket_name,
                "prefix": prefix,
            }

    snapshot_candidates = sorted(set(snapshot_candidates), reverse=True)

    if loaded_snapshot is None and snapshot_candidates:
        loaded_object = snapshot_candidates[0]
        loaded_snapshot = _download_blob_json(loaded_object)

    # Fallback: build snapshot from raw CSV partition
    if loaded_snapshot is None:
        raw_prefix_base = _normalize_gcs_prefix(os.getenv("GUARDIAN_GCS_RAW_PREFIX", "raw"))
        raw_mode = str(os.getenv("GUARDIAN_GCS_RAW_MODE", "latest")).strip().lower()
        configured_raw_date = str(os.getenv("GUARDIAN_GCS_RAW_DATE", "")).strip()
        discovered_dt = _discover_raw_dt_segments(bucket, _join_gcs_prefix(prefix, raw_prefix_base))

        target_dt_segments: list[str] = []
        if raw_mode == "all":
            target_dt_segments = discovered_dt
        elif raw_mode == "date":
            if configured_raw_date:
                target_dt_segments = [f"dt={configured_raw_date}"]
            elif discovered_dt:
                target_dt_segments = [discovered_dt[-1]]
        else:
            # latest mode
            if configured_raw_date:
                target_dt_segments = [f"dt={configured_raw_date}"]
            elif discovered_dt:
                target_dt_segments = [discovered_dt[-1]]
            else:
                target_dt_segments = [f"dt={datetime.utcnow().strftime('%Y-%m-%d')}"]

        raw_alerts: list[dict] = []
        for dt_seg in target_dt_segments:
            raw_prefix = _join_gcs_prefix(prefix, f"{raw_prefix_base}/{dt_seg}")
            raw_alerts.extend(_read_alerts_from_gcs_raw_csvs(bucket, raw_prefix))

        if raw_alerts:
            now_utc = datetime.utcnow().isoformat() + "+00:00"
            loaded_snapshot = {
                "generated_at_utc": now_utc,
                "generated_at_local": datetime.now().astimezone().isoformat(),
                "timezone": str(datetime.now().astimezone().tzinfo or "local"),
                "persona": "treasury_ops",
                "threshold": 40,
                "data_mode": f"live_gcs_raw_{raw_mode}",
                "event_count": len(raw_alerts),
                "alerts": raw_alerts,
                "data_availability": [],
                "raw_dt_segments": target_dt_segments,
            }
            loaded_object = ",".join(
                _join_gcs_prefix(prefix, f"{raw_prefix_base}/{d}/*.csv")
                for d in target_dt_segments
            )
        else:
            return {
                "ok": False,
                "snapshot": None,
                "snapshot_ref": "",
                "previous_snapshot": None,
                "message": f"no valid Guardian snapshot JSON or raw CSV under gs://{bucket_name}/{_join_gcs_prefix(prefix, raw_prefix_base)}",
                "bucket": bucket_name,
                "prefix": prefix,
            }

    previous_snapshot = None
    if snapshot_candidates:
        if loaded_object in snapshot_candidates:
            idx = snapshot_candidates.index(loaded_object)
            if idx + 1 < len(snapshot_candidates):
                previous_snapshot = _download_blob_json(snapshot_candidates[idx + 1])
        elif len(snapshot_candidates) >= 2:
            previous_snapshot = _download_blob_json(snapshot_candidates[1])

    return {
        "ok": True,
        "snapshot": loaded_snapshot,
        "snapshot_ref": f"gs://{bucket_name}/{loaded_object}",
        "previous_snapshot": previous_snapshot,
        "message": f"auth={auth_source}",
        "bucket": bucket_name,
        "prefix": prefix,
    }


def _resolve_guardian_snapshot_path() -> Path:
    """Resolve Guardian snapshot path with local-project-first strategy."""
    candidates = []
    env_path = os.getenv("GUARDIAN_SNAPSHOT_PATH")
    if env_path:
        candidates.append(Path(env_path).expanduser())

    local_root = Path(__file__).resolve().parent
    desktop_root = local_root.parent
    candidates.extend([
        local_root / "guardian_poc" / "data" / "alerts_latest.json",
        desktop_root / "Alphabet " / "guardian_poc" / "data" / "alerts_latest.json",
        desktop_root / "Alphabet" / "guardian_poc" / "data" / "alerts_latest.json",
    ])

    for candidate in candidates:
        if candidate.exists():
            return candidate

    # Fallback to latest non-demo snapshot when latest file is missing.
    snapshot_dirs = [
        local_root / "guardian_poc" / "data" / "snapshots",
        desktop_root / "Alphabet " / "guardian_poc" / "data" / "snapshots",
        desktop_root / "Alphabet" / "guardian_poc" / "data" / "snapshots",
    ]
    for snap_dir in snapshot_dirs:
        if not snap_dir.exists():
            continue
        for fp in sorted(snap_dir.glob("alerts_*.json"), reverse=True):
            try:
                obj = json.loads(fp.read_text(encoding="utf-8"))
            except Exception:
                continue
            mode = str(obj.get("data_mode") or "").strip().lower()
            if mode == "demo_fallback":
                continue
            return fp

    # Return primary candidate for diagnostics even when missing
    return candidates[0] if candidates else Path("alerts_latest.json")


def _snapshot_and_alert_rows() -> tuple[dict | None, list[dict], str]:
    """Load the current Guardian snapshot and normalize alert rows for downstream views."""
    gcs_result = _load_guardian_snapshot_from_gcs()
    snapshot = None
    snapshot_ref = ""
    if gcs_result.get("ok"):
        snapshot = gcs_result.get("snapshot")
        snapshot_ref = str(gcs_result.get("snapshot_ref") or "")

    if snapshot is None:
        snapshot_path = _resolve_guardian_snapshot_path()
        if not snapshot_path.exists():
            return None, [], ""
        try:
            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
            snapshot_ref = str(snapshot_path)
        except Exception:
            return None, [], ""

    data_mode = snapshot.get("data_mode")
    enforce_whitelist = not str(data_mode or "").startswith("live_gcs_raw")
    alerts = _filter_guardian_alerts(snapshot.get("alerts"), enforce_whitelist=enforce_whitelist)
    alert_rows = build_alert_list(alerts, max_items=5000)
    return snapshot, alert_rows, snapshot_ref


def _filter_guardian_alerts(raw_alerts, enforce_whitelist: bool = True):
    alerts = []
    for item in raw_alerts or []:
        source = str(item.get("source") or "").strip()
        url = str(item.get("url") or "").strip().lower()
        if enforce_whitelist and source not in GUARDIAN_SOURCE_WHITELIST:
            continue
        if "example.com/demo" in url:
            continue
        alerts.append(item)
    return alerts


def _find_previous_snapshot_path(current_path: Path) -> Path | None:
    """Find previous non-demo snapshot file in the same snapshots directory."""
    if not current_path.exists():
        return None
    parent = current_path.parent
    if parent.name != "snapshots":
        return None

    snapshots = sorted(parent.glob("alerts_*.json"), reverse=True)
    seen_current = False
    for fp in snapshots:
        if fp == current_path:
            seen_current = True
            continue
        if not seen_current:
            continue
        try:
            obj = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            continue
        mode = str(obj.get("data_mode") or "").strip().lower()
        if mode == "demo_fallback":
            continue
        return fp
    return None


def _resolve_alphabet_root() -> Path | None:
    local_root = Path(__file__).resolve().parent
    desktop_root = local_root.parent
    candidates = [
        local_root,
        desktop_root / "Alphabet ",
        desktop_root / "Alphabet",
    ]
    for c in candidates:
        if (c / "guardian_poc").exists() and (c / "Dataset").exists():
            return c
    return None


def _num_series(df, col):
    try:
        import pandas as pd  # noqa: F401
    except Exception:
        return None
    if col not in df.columns:
        return None
    import pandas as pd
    return pd.to_numeric(df[col], errors="coerce")


def _get_google_downside_snapshot():
    """
    Infer Google downside-risk probability from trained artifact + latest dataset row.
    Returns a dict suitable for dashboard rendering; never raises.
    """
    alphabet_root = _resolve_alphabet_root()
    if not alphabet_root:
        return {"status": "unavailable", "reason": "Alphabet project folder not found"}

    try:
        import joblib
        import numpy as np
        import pandas as pd
        import yaml
    except Exception:
        # Fallback: try multiple known virtualenv interpreters for inference.
        local_root = Path(__file__).resolve().parent
        desktop_root = local_root.parent
        py_candidates = [
            alphabet_root / ".venv" / "bin" / "python",
            desktop_root / "Alphabet " / ".venv" / "bin" / "python",
            desktop_root / "Alphabet" / ".venv" / "bin" / "python",
        ]
        py_candidates = [p for p in py_candidates if p.exists()]
        if not py_candidates:
            return {"status": "unavailable", "reason": "missing dependency and no fallback venv python found"}
        script = r'''
import json
import importlib.util
import sys
from pathlib import Path
import joblib

alphabet_root = Path(sys.argv[1])
model_path = alphabet_root / "guardian_poc" / "models" / "google_downside_risk_model.joblib"
metrics_path = alphabet_root / "guardian_poc" / "models" / "google_downside_risk_metrics.json"
trainer_path = alphabet_root / "guardian_poc" / "src" / "train_google_risk_model.py"
prices_path = alphabet_root / "Dataset" / "googl_daily_prices.csv"
news_path = alphabet_root / "Dataset" / "Google_Daily_News.csv"
balance_path = alphabet_root / "Dataset" / "googl_balance_sheet.csv"
cashflow_path = alphabet_root / "Dataset" / "googl_cash_flow_statement.csv"
income_path = alphabet_root / "Dataset" / "googl_income_statement.csv"
keywords_path = alphabet_root / "guardian_poc" / "config" / "risk_keywords.yaml"

required = [model_path, trainer_path, prices_path, news_path, balance_path, cashflow_path, income_path, keywords_path]
missing = [str(p) for p in required if not p.exists()]
if missing:
    print(json.dumps({"status": "unavailable", "reason": f"missing files: {', '.join(missing[:2])}"}))
    raise SystemExit(0)

spec = importlib.util.spec_from_file_location("train_google_risk_model", str(trainer_path))
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)

artifact = joblib.load(model_path)
model = artifact["model"] if isinstance(artifact, dict) else artifact
feature_columns = artifact.get("feature_columns", []) if isinstance(artifact, dict) else []
threshold = float(artifact.get("threshold", 0.5)) if isinstance(artifact, dict) else 0.5
model_name = artifact.get("model_name", "unknown") if isinstance(artifact, dict) else "unknown"

frame = mod.build_training_frame(
    prices_path=str(prices_path),
    news_path=str(news_path),
    balance_path=str(balance_path),
    cashflow_path=str(cashflow_path),
    income_path=str(income_path),
    keywords_cfg_path=str(keywords_path),
)
if frame.empty:
    print(json.dumps({"status":"unavailable","reason":"no feature rows"}))
    raise SystemExit(0)

latest = frame.iloc[-1:].copy()
if feature_columns:
    for col in feature_columns:
        if col not in latest.columns:
            latest[col] = 0.0
    X = latest[feature_columns]
else:
    X = latest[[c for c in latest.columns if c not in {"date", "target_ret_next_1d", "target_downside_risk"}]]

prob = float(model.predict_proba(X)[0][1]) if hasattr(model, "predict_proba") else float(model.predict(X)[0])
level = "High" if prob >= 0.7 else ("Medium" if prob >= 0.4 else "Low")

metrics = {}
if metrics_path.exists():
    try:
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    except Exception:
        pass

top_signals = []
for col in ["vol_20d", "ret_5d", "news_count", "fin_leverage", "fin_ocf_margin"]:
    if col in latest.columns:
        try:
            v = float(latest[col].iloc[0])
            top_signals.append({"name": col, "value": round(v, 4)})
        except Exception:
            pass

print(json.dumps({
    "status": "success",
    "probability": round(prob, 4),
    "percent": round(prob * 100, 2),
    "level": level,
    "model_name": model_name,
    "threshold": threshold,
    "as_of_date": str(latest["date"].iloc[0]) if "date" in latest.columns else None,
    "test_auc": metrics.get("test_auc"),
    "top_signals": top_signals
}))
'''
        last_err = ""
        for ext_py in py_candidates:
            try:
                out = subprocess.run(
                    [str(ext_py), "-c", script, str(alphabet_root)],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=45,
                )
                payload = json.loads((out.stdout or "").strip() or "{}")
                if isinstance(payload, dict):
                    return payload
                last_err = "external inference parse failed"
            except Exception as e:
                err = ""
                if hasattr(e, "stderr") and e.stderr:
                    err = str(e.stderr).strip().splitlines()[-1]
                last_err = err or str(e)
                continue
        return {"status": "unavailable", "reason": f"missing dependency and external inference failed: {last_err}"}

    model_path = alphabet_root / "guardian_poc" / "models" / "google_downside_risk_model.joblib"
    metrics_path = alphabet_root / "guardian_poc" / "models" / "google_downside_risk_metrics.json"
    prices_path = alphabet_root / "Dataset" / "googl_daily_prices.csv"
    news_path = alphabet_root / "Dataset" / "Google_Daily_News.csv"
    balance_path = alphabet_root / "Dataset" / "googl_balance_sheet.csv"
    cashflow_path = alphabet_root / "Dataset" / "googl_cash_flow_statement.csv"
    income_path = alphabet_root / "Dataset" / "googl_income_statement.csv"
    keywords_path = alphabet_root / "guardian_poc" / "config" / "risk_keywords.yaml"

    required = [model_path, prices_path, news_path, balance_path, cashflow_path, income_path, keywords_path]
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        return {"status": "unavailable", "reason": f"missing files: {', '.join(missing[:2])}"}

    try:
        artifact = joblib.load(model_path)
        model = artifact.get("model") if isinstance(artifact, dict) else artifact
        feature_columns = artifact.get("feature_columns", []) if isinstance(artifact, dict) else []
        threshold = float(artifact.get("threshold", 0.5)) if isinstance(artifact, dict) else 0.5
        model_name = artifact.get("model_name", "unknown") if isinstance(artifact, dict) else "unknown"
    except Exception as e:
        return {"status": "unavailable", "reason": f"model load failed: {str(e)}"}

    try:
        prices = pd.read_csv(prices_path).rename(columns={
            "1. open": "open",
            "2. high": "high",
            "3. low": "low",
            "4. close": "close",
            "5. volume": "volume",
        })
        prices["date"] = pd.to_datetime(prices["date"], utc=False)
        for c in ["open", "high", "low", "close", "volume"]:
            prices[c] = pd.to_numeric(prices[c], errors="coerce")
        prices = prices.sort_values("date").dropna(subset=["close"])
        prices["ret_1d"] = prices["close"].pct_change()
        prices["ret_5d"] = prices["close"].pct_change(5)
        prices["vol_5d"] = prices["ret_1d"].rolling(5).std()
        prices["vol_20d"] = prices["ret_1d"].rolling(20).std()
        prices["range_pct"] = (prices["high"] - prices["low"]) / prices["close"]
        vol_mean_20 = prices["volume"].rolling(20).mean()
        vol_std_20 = prices["volume"].rolling(20).std().replace(0, np.nan)
        prices["volume_z_20d"] = (prices["volume"] - vol_mean_20) / vol_std_20

        news = pd.read_csv(news_path)
        news["date"] = pd.to_datetime(news["datetime"], unit="s", utc=True).dt.tz_convert(None).dt.floor("D")
        news["text"] = (news["headline"].fillna("") + " " + news["summary"].fillna("")).str.lower()
        per_day = pd.DataFrame(index=sorted(news["date"].dropna().unique()))
        per_day.index.name = "date"
        per_day["news_count"] = news.groupby("date").size()

        keywords = yaml.safe_load(keywords_path.read_text(encoding="utf-8")).get("risk_taxonomy", {})
        for key, cfg in keywords.items():
            primaries = [str(x).lower() for x in cfg.get("primary", [])]
            highs = [str(x).lower() for x in cfg.get("high_alert", [])]
            p_col = f"news_{key}_primary"
            h_col = f"news_{key}_high"
            news[p_col] = 0
            news[h_col] = 0
            for term in primaries:
                if term:
                    news[p_col] = news[p_col] + news["text"].str.contains(re.escape(term), regex=True, na=False).astype(int)
            for term in highs:
                if term:
                    news[h_col] = news[h_col] + news["text"].str.contains(re.escape(term), regex=True, na=False).astype(int)
            per_day[p_col] = news.groupby("date")[p_col].sum()
            per_day[h_col] = news.groupby("date")[h_col].sum()
        per_day = per_day.fillna(0).reset_index()

        b = pd.read_csv(balance_path)
        c = pd.read_csv(cashflow_path)
        i = pd.read_csv(income_path)
        for df in (b, c, i):
            df["fiscalDateEnding"] = pd.to_datetime(df["fiscalDateEnding"], utc=False)
        b = b.sort_values("fiscalDateEnding")
        c = c.sort_values("fiscalDateEnding")
        i = i.sort_values("fiscalDateEnding")
        fin = pd.DataFrame({"date": sorted(set(b["fiscalDateEnding"]).intersection(c["fiscalDateEnding"]).intersection(i["fiscalDateEnding"]))})
        fin = fin.rename(columns={"date": "fiscalDateEnding"})
        fin = fin.merge(b, on="fiscalDateEnding", how="left").merge(c, on="fiscalDateEnding", how="left", suffixes=("", "_cf"))
        fin = fin.merge(i, on="fiscalDateEnding", how="left", suffixes=("", "_is"))

        cash = _num_series(fin, "cashAndCashEquivalentsAtCarryingValue")
        cur_assets = _num_series(fin, "totalCurrentAssets")
        cur_liab = _num_series(fin, "totalCurrentLiabilities")
        total_liab = _num_series(fin, "totalLiabilities")
        equity = _num_series(fin, "totalShareholderEquity")
        revenue = _num_series(fin, "totalRevenue")
        op_income = _num_series(fin, "operatingIncome")
        op_cf = _num_series(fin, "operatingCashflow")
        capex = _num_series(fin, "capitalExpenditures")
        net_income = _num_series(fin, "netIncome")

        fin_out = pd.DataFrame({"date": fin["fiscalDateEnding"]})
        fin_out["fin_current_ratio"] = cur_assets / cur_liab.replace(0, np.nan)
        fin_out["fin_cash_to_current_liab"] = cash / cur_liab.replace(0, np.nan)
        fin_out["fin_leverage"] = total_liab / equity.replace(0, np.nan)
        fin_out["fin_operating_margin"] = op_income / revenue.replace(0, np.nan)
        fin_out["fin_ocf_margin"] = op_cf / revenue.replace(0, np.nan)
        fin_out["fin_fcf_proxy"] = (op_cf - capex) / revenue.replace(0, np.nan)
        fin_out["fin_net_margin"] = net_income / revenue.replace(0, np.nan)

        frame = prices.merge(per_day, on="date", how="left").sort_values("date")
        frame = pd.merge_asof(frame, fin_out.sort_values("date"), on="date", direction="backward")
        frame = frame.fillna(0)

        if frame.empty:
            return {"status": "unavailable", "reason": "no feature rows"}

        latest = frame.iloc[-1:].copy()
        if feature_columns:
            for col in feature_columns:
                if col not in latest.columns:
                    latest[col] = 0.0
            X = latest[feature_columns]
        else:
            ignore_cols = {"date", "target_ret_next_1d", "target_downside_risk"}
            use_cols = [c for c in latest.columns if c not in ignore_cols]
            X = latest[use_cols]

        prob = float(model.predict_proba(X)[0][1]) if hasattr(model, "predict_proba") else float(model.predict(X)[0])
        level = "High" if prob >= 0.7 else ("Medium" if prob >= 0.4 else "Low")

        top_signals = []
        for col in ["vol_20d", "ret_5d", "news_count", "fin_leverage", "fin_ocf_margin"]:
            if col in latest.columns:
                val = latest[col].iloc[0]
                if pd.notna(val):
                    top_signals.append({"name": col, "value": round(float(val), 4)})

        metrics = {}
        if metrics_path.exists():
            try:
                metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
            except Exception:
                metrics = {}

        return {
            "status": "success",
            "probability": round(prob, 4),
            "percent": round(prob * 100, 2),
            "level": level,
            "model_name": model_name,
            "threshold": threshold,
            "as_of_date": str(latest["date"].iloc[0]) if "date" in latest.columns else None,
            "test_auc": metrics.get("test_auc"),
            "top_signals": top_signals,
            "is_stale": False,
            "staleness_days": None,
        }
    except Exception as e:
        return {"status": "unavailable", "reason": f"inference failed: {str(e)}"}


@app.route('/api/guardian/dashboard')
def get_guardian_dashboard():
    """Get Guardian multi-country dashboard data for ARGUS page."""
    gcs_result = _load_guardian_snapshot_from_gcs()
    gcs_diag = {
        "ok": bool(gcs_result.get("ok")),
        "message": str(gcs_result.get("message") or ""),
        "bucket": str(gcs_result.get("bucket") or ""),
        "prefix": str(gcs_result.get("prefix") or ""),
        "snapshot_ref": str(gcs_result.get("snapshot_ref") or ""),
    }
    snapshot = None
    snapshot_ref = ""
    prev_snapshot_obj = None
    data_source = "local"

    if gcs_result.get("ok"):
        snapshot = gcs_result.get("snapshot")
        snapshot_ref = str(gcs_result.get("snapshot_ref") or "")
        prev_snapshot_obj = gcs_result.get("previous_snapshot")
        data_source = "gcs"

    if snapshot is None:
        snapshot_path = _resolve_guardian_snapshot_path()
        if not snapshot_path.exists():
            return jsonify({
                "status": "missing",
                "message": "Guardian snapshot not found in GCS or local file",
                "snapshot_path": str(snapshot_path),
                "gcs_status": gcs_diag,
            }), 404

        try:
            snapshot = json.loads(snapshot_path.read_text(encoding='utf-8'))
            snapshot_ref = str(snapshot_path)
        except Exception as e:
            return jsonify({
                "status": "error",
                "message": f"Failed to parse local snapshot: {str(e)}",
                "snapshot_path": str(snapshot_path),
                "gcs_status": gcs_diag,
            }), 500

    data_mode = snapshot.get("data_mode")
    enforce_whitelist = not str(data_mode or "").startswith("live_gcs_raw")
    alerts = _filter_guardian_alerts(snapshot.get("alerts"), enforce_whitelist=enforce_whitelist)
    monitored_countries = load_monitored_countries()
    monitored_set = set(monitored_countries)

    alert_rows_all = build_alert_list(alerts, max_items=5000)
    scoped_alerts = [a for a in alerts if str(a.get("country") or "") in monitored_set]
    alert_rows = build_alert_list(scoped_alerts if scoped_alerts else alerts, max_items=5000)

    country_board = build_country_board(scoped_alerts if scoped_alerts else alerts)
    availability = snapshot.get("data_avaliability") or snapshot.get("data_availability") or []
    availability = [
        x for x in availability
        if str(x.get("source") or "").strip() in GUARDIAN_SOURCE_WHITELIST
    ]
    enabled_sources = [x for x in availability if x.get("enabled")]
    healthy_enabled_sources = [x for x in enabled_sources if x.get("healthy")]
    threshold = float(snapshot.get("threshold") or 40)
    daily_comparison = _build_daily_warnings(alert_rows, snapshot.get("raw_dt_segments") or [], threshold=threshold)
    triggered_count = len([x for x in alert_rows if float(x.get("score", 0)) >= threshold])

    # For raw GCS mode, availability metadata is often absent; derive basic source health from observed sources.
    if not availability and str(data_mode or "").startswith("live_gcs_raw"):
        unique_sources = sorted({str(x.get("source") or "").strip() for x in alert_rows_all if str(x.get("source") or "").strip()})
        enabled_sources = [{"source": s, "enabled": True, "healthy": True} for s in unique_sources]
        healthy_enabled_sources = enabled_sources[:]

    # FX card should reflect current operational view; for raw-all mode use latest dt only.
    fx_input_rows = alert_rows
    latest_dt = str(daily_comparison.get("latest_dt") or "").strip()
    if str(data_mode or "").startswith("live_gcs_raw") and latest_dt:
        latest_rows = [x for x in alert_rows if str(x.get("raw_dt") or "") == latest_dt]
        if latest_rows:
            fx_input_rows = latest_rows
    fx_summary = build_fx_summary(fx_input_rows, monitored_countries)
    fx_summary["as_of_dt"] = latest_dt or None

    high_risk_country_count = len([x for x in country_board if float(x.get("max_score", 0)) >= 70])
    at_risk_country_count = len([x for x in country_board if float(x.get("max_score", 0)) > 0])
    high_risk_alert_count = len([x for x in alert_rows if float(x.get("score", 0)) >= 70])
    total_risk_alert_count = len([x for x in alert_rows if float(x.get("score", 0)) > 0])

    prev_triggered = None
    current_triggered_for_trend = triggered_count
    is_raw_mode = str(data_mode or "").startswith("live_gcs_raw")

    if is_raw_mode:
        prev_triggered = daily_comparison.get("previous_triggered")
        current_triggered_for_trend = int(daily_comparison.get("latest_triggered") or 0)

    if isinstance(prev_snapshot_obj, dict):
        try:
            prev_mode = prev_snapshot_obj.get("data_mode")
            prev_enforce = not str(prev_mode or "").startswith("live_gcs_raw")
            prev_alerts = _filter_guardian_alerts(prev_snapshot_obj.get("alerts"), enforce_whitelist=prev_enforce)
            prev_rows = build_alert_list(prev_alerts, max_items=5000)
            prev_triggered = len([x for x in prev_rows if float(x.get("score", 0)) >= threshold])
            current_triggered_for_trend = triggered_count
        except Exception:
            prev_triggered = None
    elif data_source == "local":
        prev_path = _find_previous_snapshot_path(Path(snapshot_ref))
        if prev_path and prev_path.exists():
            try:
                prev_snapshot = json.loads(prev_path.read_text(encoding='utf-8'))
                prev_mode = prev_snapshot.get("data_mode")
                prev_enforce = not str(prev_mode or "").startswith("live_gcs_raw")
                prev_alerts = _filter_guardian_alerts(prev_snapshot.get("alerts"), enforce_whitelist=prev_enforce)
                prev_rows = build_alert_list(prev_alerts, max_items=5000)
                prev_triggered = len([x for x in prev_rows if float(x.get("score", 0)) >= threshold])
                current_triggered_for_trend = triggered_count
            except Exception:
                prev_triggered = None

    trend_basis = "triggered"
    trend_current = current_triggered_for_trend
    trend_previous = prev_triggered
    trend_delta = None if trend_previous is None else (trend_current - trend_previous)

    # In raw multi-day mode, triggered counts can be sparse; use day-over-day events as trend basis.
    if is_raw_mode:
        trend_basis = "events"
        trend_current = int(daily_comparison.get("latest_events") or 0)
        prev_val = daily_comparison.get("previous_events")
        trend_previous = None if prev_val is None else int(prev_val)
        trend_delta = None if trend_previous is None else (trend_current - trend_previous)

    if trend_delta is None:
        trend_direction = "NA"
    elif trend_delta > 0:
        trend_direction = "UP"
    elif trend_delta < 0:
        trend_direction = "DOWN"
    else:
        trend_direction = "FLAT"

    google_downside = _get_google_downside_snapshot()
    if isinstance(google_downside, dict) and google_downside.get("status") == "success":
        as_of = str(google_downside.get("as_of_date") or "").strip()
        stale_days = None
        if as_of:
            try:
                as_dt = datetime.fromisoformat(as_of.replace("Z", "+00:00")).date()
                stale_days = (datetime.now().date() - as_dt).days
            except Exception:
                stale_days = None
        google_downside["staleness_days"] = stale_days
        google_downside["is_stale"] = bool(stale_days is not None and stale_days > 7)

    dashboard_data = {
        "status": "success",
        "snapshot_path": snapshot_ref,
        "meta": {
            "generated_at_local": snapshot.get("generated_at_local"),
            "generated_at_utc": snapshot.get("generated_at_utc"),
            "timezone": snapshot.get("timezone"),
            "persona": snapshot.get("persona"),
            "threshold": snapshot.get("threshold"),
            "data_mode": data_mode,
            "event_count": snapshot.get("event_count", 0),
            "alert_count": len(alert_rows),
            "triggered_count": triggered_count,
            "enabled_source_count": len(enabled_sources),
            "healthy_enabled_source_count": len(healthy_enabled_sources),
            "data_source": data_source,
            "raw_dt_segments": snapshot.get("raw_dt_segments") or [],
            "scope": "11-country",
            "global_event_count": len(alert_rows_all),
        },
        "gcs_status": gcs_diag,
        "executive_summary": {
            "high_risk_country_count": high_risk_country_count,
            "high_risk_alert_count": high_risk_alert_count,
            "at_risk_country_count": at_risk_country_count,
            "total_risk_alert_count": total_risk_alert_count,
            "trend": {
                "basis": trend_basis,
                "direction": trend_direction,
                "delta": trend_delta,
                "current_triggered": trend_current,
                "previous_triggered": trend_previous,
            },
        },
        "country_rank_top5": country_board[:5],
        "critical_alerts_top5": alert_rows[:5],
        "fx_summary": fx_summary,
        "daily_comparison": daily_comparison,
        "google_downside": google_downside,
        "country_board": country_board,
        "alerts": alert_rows,
    }
    # Sanitize to remove None values before jsonify
    return jsonify(sanitize_for_json(dashboard_data))


@app.route('/api/personas')
def get_personas():
    """Return available persona options for the role-based dashboard."""
    return jsonify({
        "status": "success",
        "personas": [
            {"key": key, "label": cfg["label"], "summary": cfg["summary"]}
            for key, cfg in PERSONA_REGISTRY.items()
        ],
        "default_persona": "cash_manager",
    })


@app.route('/api/persona/dashboard')
def get_persona_dashboard():
    """Return role-based dashboard content for the selected persona."""
    persona = str(request.args.get("persona") or "cash_manager").strip().lower()
    if persona not in PERSONA_REGISTRY:
        persona = "cash_manager"

    snapshot, alert_rows, snapshot_ref = _snapshot_and_alert_rows()
    if snapshot is None:
        return jsonify({
            "status": "missing",
            "message": "Persona dashboard unavailable because Guardian snapshot could not be loaded.",
            "persona": persona,
        }), 404

    if persona == "cash_manager":
        payload = build_cash_manager_dashboard(alert_rows, snapshot)
    else:
        payload = build_generic_persona_dashboard(persona, alert_rows, snapshot)

    payload["status"] = "success"
    payload["snapshot_path"] = snapshot_ref
    return jsonify(sanitize_for_json(payload))


@app.route('/')
def index():
    """Main dashboard page"""
    return render_template('dashboard.html')


@app.route('/api/status')
def get_status():
    """Get current system status"""
    return jsonify({
        "is_running": execution_state["is_running"],
        "current_stage": execution_state["current_stage"],
        "agents_status": orchestrator.get_agent_summary(),
        "timestamp": datetime.now().isoformat()
    })


@app.route('/api/execute', methods=['POST'])
def execute_pipeline():
    """Execute the analysis pipeline"""
    if execution_state["is_running"]:
        return jsonify({"error": "Pipeline already running"}), 400

    def run_pipeline():
        """Run pipeline in background thread"""
        execution_state["is_running"] = True
        try:
            # Run the async pipeline
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(orchestrator.execute_pipeline())
            loop.close()

            execution_state["result"] = result
            execution_state["current_stage"] = "completed"
        except Exception as e:
            execution_state["result"] = {"error": str(e)}
            execution_state["current_stage"] = "failed"
        finally:
            execution_state["is_running"] = False

    # Start pipeline in background thread
    thread = threading.Thread(target=run_pipeline)
    thread.daemon = True
    thread.start()

    return jsonify({"status": "started", "message": "Pipeline execution started"})


@app.route('/api/analyze', methods=['POST'])
def analyze_items():
    """Synchronous analysis endpoint for the Bridge Manager.

    Accepts:
        POST {"news_items": [...], "source_file": "optional_label"}

    Returns:
        Enriched JSON with 2D risk-matrix scores for every item, plus an
        aggregate summary.  Runs the RiskClassifier and AnalysisAgent inline
        (blocking); use /api/execute for the full async pipeline instead.
    """
    body = request.get_json(silent=True) or {}
    news_items = body.get("news_items")

    if not news_items or not isinstance(news_items, list):
        return jsonify({"error": "Request body must include a non-empty 'news_items' list"}), 400

    source_file = str(body.get("source_file", "bridge_input"))

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        # Stage 1: keyword classification
        classifier = RiskClassifierAgent()
        classify_result = loop.run_until_complete(
            classifier.execute(news_items=news_items)
        )
        if classify_result["status"] != "success":
            loop.close()
            return jsonify({"error": "Classification failed", "detail": classify_result}), 500

        # Stage 2: 2D risk-matrix analysis (LLM or heuristic)
        analyzer = AnalysisAgent()
        analysis_result = loop.run_until_complete(
            analyzer.execute(classified_news=classify_result["data"])
        )
        loop.close()

        if analysis_result["status"] != "success":
            return jsonify({"error": "Analysis failed", "detail": analysis_result}), 500

        analysis = analysis_result["analysis"]
        by_level = analysis["by_risk_level"]

        return jsonify(sanitize_for_json({
            "status":       "success",
            "source_file":  source_file,
            "item_count":   len(news_items),
            "scored_items": analysis["scored_items"],
            "summary": {
                "extreme":       len(by_level.get("extreme", [])),
                "high":          len(by_level.get("high",    [])),
                "medium":        len(by_level.get("medium",  [])),
                "low":           len(by_level.get("low",     [])),
                "llm_scored":    analysis.get("llm_scored", False),
                "top_keywords":  analysis.get("top_keywords", []),
                "matrix_summary": analysis.get("matrix_summary", []),
            },
            "analyzed_at": datetime.now().isoformat(),
        }))

    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route('/api/results')
def get_results():
    """Get latest execution results"""
    if execution_state["result"]:
        # Sanitize result to remove None values before jsonify
        # This prevents TypeError when JSON encoder tries to sort keys with None values
        sanitized_result = sanitize_for_json(execution_state["result"])
        return jsonify(sanitized_result)
    return jsonify({"status": "no_results"})


@app.route('/api/agents/logs/<agent_name>')
def get_agent_logs(agent_name):
    """Get logs for specific agent"""
    agent_map = {
        "news_gatherer": orchestrator.news_gatherer,
        "risk_classifier": orchestrator.risk_classifier,
        "database": orchestrator.database_manager,
        "analyzer": orchestrator.analyzer,
        "reporter": orchestrator.reporter
    }

    agent = agent_map.get(agent_name)
    if not agent:
        return jsonify({"error": "Agent not found"}), 404

    return jsonify({
        "agent": agent_name,
        "logs": agent.get_logs(),
        "status": agent.status
    })


@app.route('/api/agents/summary')
def get_agents_summary():
    """Get summary of all agents"""
    return jsonify(orchestrator.get_agent_summary())


@app.route("/api/demo/report")
def get_demo_report():
    if not orchestrator.reporter.reports:
        return jsonify({
            "recommendations": [],
            "top_keywords": []
        })
    return jsonify(sanitize_for_json(orchestrator.reporter.reports[-1]))


@app.route('/api/reset', methods=['POST'])
def reset_system():
    """Reset the system"""
    global orchestrator
    orchestrator = AgentOrchestrator()
    execution_state["is_running"] = False
    execution_state["current_stage"] = None
    execution_state["result"] = None
    return jsonify({"status": "reset_complete"})


if __name__ == '__main__':
    port = API_PORT
    print("""
    ╔════════════════════════════════════════════════════════════════╗
    ║                                                                ║
    ║         ARGUS Multi-Agent System - Web Frontend                ║
    ║                                                                ║
    ║  Starting Flask server on http://localhost:5050/              ║
    ║  Open your browser and navigate to the URL above               ║
    ║                                                                ║
    ╚════════════════════════════════════════════════════════════════╝
    """)
    print(f"    Listening on http://127.0.0.1:{port}/")
    app.run(debug=True, port=port)
