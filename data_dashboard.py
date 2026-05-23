# data_dashboard.py
from __future__ import annotations

import os
import re
import csv
import time
import threading

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from io import StringIO

from google.cloud import storage

from flask import Blueprint, jsonify, request, render_template_string

try:
    import pandas as pd  # type: ignore
except Exception:
    pd = None

# -------------------------
# Blueprint
# -------------------------
data_dashboard_bp = Blueprint("data_dashboard", __name__)

# -------------------------
# Config / conventions
# -------------------------
COUNTRY_PREFIX_RE = re.compile(r"^(us|ca|uk|de|jp|fr|br|in|au|mx|cn|eu|kr)_", re.I)

DEFAULT_COUNTRY_NAME_MAP = {
    "us": "USA", "ca": "Canada", "uk": "UK",   "de": "Germany",
    "jp": "Japan", "fr": "France", "br": "Brazil", "in": "India",
    "au": "Australia", "mx": "Mexico", "cn": "China",
    "eu": "Eurozone", "kr": "Korea",
}

INDICATOR_DISPLAY = {
    "cpi": "CPI", "sofr": "SOFR", "policy_rate": "Policy Rate",
    "usdcad": "USD/CAD FX", "treasury_auctions": "US Treasury Auctions",
    "conflict_light": "GDELT Conflict (Light)",
    "earthquakes_global": "USGS Earthquakes (Global)",
}

DEFAULT_ARGUS_GCS_BUCKET = "your-gcs-bucket"

# -------------------------
# ★ In-memory cache
# -------------------------
_CACHE_TTL = int(os.getenv("DATA_DASHBOARD_CACHE_TTL", "300"))  # seconds, default 5 min

_cache: Dict[str, Any] = {
    "inventory": None,   # full inventory dict
    "matrix":    None,   # coverage matrix dict
    "ts":        0.0,    # last populated timestamp
}
_cache_lock = threading.Lock()


def _cache_valid() -> bool:
    return (
        _cache["inventory"] is not None
        and time.time() - _cache["ts"] < _CACHE_TTL
    )


def _invalidate_cache() -> None:
    with _cache_lock:
        _cache["inventory"] = None
        _cache["matrix"]    = None
        _cache["ts"]        = 0.0


# -------------------------
# GCS helpers
# -------------------------
def _get_gcs_prefix() -> str:
    return os.getenv("DATA_DASHBOARD_GCS_PREFIX", "").strip()


def _parse_gs_uri(gs_uri: str) -> Tuple[str, str]:
    if not gs_uri.startswith("gs://"):
        raise ValueError("DATA_DASHBOARD_GCS_PREFIX must start with gs://")
    rest  = gs_uri[5:]
    parts = rest.split("/", 1)
    bucket_name = parts[0]
    prefix = parts[1] if len(parts) > 1 else ""
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    return bucket_name, prefix


def _get_gcs_bucket():
    bucket_name = (
        os.getenv("ARGUS_GCS_BUCKET", "").strip()
        or os.getenv("GUARDIAN_GCS_BUCKET", "").strip()
        or DEFAULT_ARGUS_GCS_BUCKET
    )
    project  = os.getenv("GOOGLE_CLOUD_PROJECT", "").strip() or None
    adc_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()

    if adc_path:
        return storage.Client(project=project).bucket(bucket_name)

    here = Path(__file__).resolve().parent
    extra_key_paths = [
        os.getenv("ARGUS_GCS_KEY_PATH", "").strip(),
        os.getenv("GUARDIAN_GCS_KEY_PATH", "").strip(),
        str(Path.home() / "Desktop" / "argus-main" / "keys" / "treasury-reader.json"),
        str(Path.home() / "Desktop" / "argus-main" / "keys" / "service-account.json"),
    ]
    for key_path in [
        here / "keys" / "treasury-reader.json",
        here / "keys" / "service-account.json",
        *[Path(p) for p in extra_key_paths if p],
    ]:
        if key_path.exists():
            return storage.Client.from_service_account_json(
                str(key_path), project=project
            ).bucket(bucket_name)

    return storage.Client(project=project).bucket(bucket_name)


def _resolve_data_dashboard_prefix(bucket) -> str:
    explicit = os.getenv("ARGUS_GCS_PREFIX", "").strip()
    if explicit:
        return explicit

    base = os.getenv("ARGUS_GCS_BASE_PREFIX", "").strip()
    if not base:
        root = os.getenv("GUARDIAN_GCS_PREFIX", "").strip().strip("/")
        raw  = os.getenv("GUARDIAN_GCS_RAW_PREFIX", "raw").strip().strip("/")
        base = f"{root}/{raw}/" if root else f"{raw}/"

    return _gcs_latest_dt_prefix(bucket, base)


def _gcs_latest_dt_prefix(bucket, base_prefix: str = "raw/") -> str:
    base_prefix = base_prefix.rstrip("/") + "/"
    dt_values: set = set()
    for b in bucket.list_blobs(prefix=base_prefix):
        m = re.search(rf"{re.escape(base_prefix)}dt=(\d{{4}}-\d{{2}}-\d{{2}})/", b.name)
        if m:
            dt_values.add(m.group(1))
    if not dt_values:
        return base_prefix
    return f"{base_prefix}dt={sorted(dt_values)[-1]}/"


def _list_gcs_csv_blobs(bucket, prefix: str) -> List[Any]:
    prefix = prefix.rstrip("/") + "/"
    return [b for b in bucket.list_blobs(prefix=prefix)
            if b.name.lower().endswith(".csv")]


def _read_gcs_csv_text(bucket, blob_name: str) -> str:
    return bucket.blob(blob_name).download_as_text()


# -------------------------
# Local fallback helpers
# -------------------------
def _resolve_data_dir() -> Path:
    env_dir = os.getenv("DATA_DASHBOARD_DATA_DIR", "").strip()
    if env_dir:
        return Path(env_dir).expanduser().resolve()
    here = Path(__file__).resolve().parent
    for c in [here / "Dataset", here / "guardian_poc" / "data", here]:
        if c.exists() and any(c.glob("*.csv")):
            return c
    return here / "Dataset"


# -------------------------
# CSV / series helpers
# -------------------------
def _classify_dataset(file_stem: str) -> str:
    s = file_stem.lower()
    if s.startswith("news_"):   return "news"
    if s.startswith("usgs_"):   return "risk"
    if s.startswith("gdelt_"):  return "risk"
    if s.startswith("bis_"):    return "flows"
    if COUNTRY_PREFIX_RE.search(s): return "macro"
    return "other"


def _infer_country_from_filename(stem: str) -> Optional[str]:
    m = COUNTRY_PREFIX_RE.match(stem.lower())
    if not m:
        return None
    return DEFAULT_COUNTRY_NAME_MAP.get(m.group(1).lower(), m.group(1).upper())


def _infer_indicator_from_filename(stem: str) -> str:
    s = stem.lower()
    s = COUNTRY_PREFIX_RE.sub("", s)
    s = re.sub(r"^(news|usgs|gdelt|bis)_", "", s)
    return s


def _safe_float(x: Any) -> Optional[float]:
    try:
        if x is None or (isinstance(x, str) and not x.strip()):
            return None
        return float(x)
    except Exception:
        return None


def _parse_date_any(x: Any) -> Optional[str]:
    if x is None:
        return None
    if isinstance(x, datetime):
        return x.date().isoformat()
    s = str(x).strip()
    if not s:
        return None
    if re.fullmatch(r"\d{10}", s):
        try:
            return datetime.utcfromtimestamp(int(s)).date().isoformat()
        except Exception:
            pass
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s
    if re.fullmatch(r"\d{4}-\d{2}", s):
        return f"{s}-01"
    if pd is not None:
        try:
            dt = pd.to_datetime(s, errors="coerce")
            return None if pd.isna(dt) else dt.date().isoformat()
        except Exception:
            return None
    return None


def _read_csv_from_text(text: str) -> List[Dict[str, Any]]:
    if pd is not None:
        return pd.read_csv(StringIO(text)).to_dict(orient="records")
    rows: List[Dict[str, Any]] = []
    for r in csv.DictReader(StringIO(text)):
        rows.append(dict(r))
    return rows


def _infer_xy(rows: List[Dict[str, Any]]) -> Tuple[Optional[str], Optional[str]]:
    if not rows:
        return None, None
    cols = list(rows[0].keys())

    date_candidates  = {"date","datetime","time","timestamp","published_at","fiscaldateending"}
    value_candidates = {"value","close","rate","cpi","amount","auction_amount","count","score"}

    date_col = next(
        (c for c in cols if str(c).strip().lower() in date_candidates), None
    ) or next(
        (c for c in cols if "date" in str(c).lower() or "time" in str(c).lower()), None
    )

    value_col = next(
        (c for c in cols if str(c).strip().lower() in value_candidates), None
    )
    if value_col is None:
        for c in cols:
            if c == date_col:
                continue
            sample = [_safe_float(r.get(c)) for r in rows[:10]]
            if sum(v is not None for v in sample) >= max(2, len(sample) // 2):
                value_col = c
                break

    return date_col, value_col


def _build_series_from_text(file_name: str, text: str) -> Dict[str, Any]:
    rows = _read_csv_from_text(text)
    date_col, value_col = _infer_xy(rows)

    points: List[Tuple[str, float]] = []
    if date_col and value_col:
        for r in rows:
            d = _parse_date_any(r.get(date_col))
            v = _safe_float(r.get(value_col))
            if d is not None and v is not None:
                points.append((d, v))

    points.sort(key=lambda x: x[0])
    dates  = [p[0] for p in points]
    values = [p[1] for p in points]

    return {
        "file": file_name, "date_col": date_col, "value_col": value_col,
        "points": len(points), "dates": dates, "values": values,
        "min_date": dates[0] if dates else None,
        "max_date": dates[-1] if dates else None,
    }


def _normalize_100(values: List[float]) -> List[float]:
    if not values or values[0] == 0:
        return values
    base = values[0]
    return [round(v / base * 100.0, 4) for v in values]


# -------------------------
# ★ Core inventory (with cache)
# -------------------------
def _get_cached_inventory() -> Dict[str, Any]:
    """
    Return inventory from cache if valid, otherwise rebuild.
    Only ONE GCS scan per cache window (thread-safe).
    """
    with _cache_lock:
        if _cache_valid():
            return _cache["inventory"]  # type: ignore

    # Build outside the lock so other requests don't block the whole app
    bucket = _get_gcs_bucket()
    prefix = _resolve_data_dashboard_prefix(bucket)
    inv    = _inventory_from_gcs(bucket, prefix)
    mat    = _coverage_matrix(inv)

    with _cache_lock:
        _cache["inventory"] = inv
        _cache["matrix"]    = mat
        _cache["ts"]        = time.time()

    return inv


def _get_cached_matrix() -> Dict[str, Any]:
    _get_cached_inventory()          # ensures cache is warm
    with _cache_lock:
        return _cache["matrix"]      # type: ignore


def _inventory_from_gcs(bucket, prefix: str) -> Dict[str, Any]:
    """
    ★ Only downloads CSV content for macro/risk files (needed for date ranges).
      News/flows/other files are listed by metadata only — no download.
    """
    blobs = _list_gcs_csv_blobs(bucket, prefix)

    items: List[Dict] = []
    countries:  set = set()
    indicators: set = set()
    min_date: Optional[str] = None
    max_date: Optional[str] = None

    for b in sorted(blobs, key=lambda x: x.name):
        base      = Path(b.name).name
        stem      = Path(b.name).stem
        ds_type   = _classify_dataset(stem)
        country   = _infer_country_from_filename(stem)
        indicator = _infer_indicator_from_filename(stem)
        display   = INDICATOR_DISPLAY.get(indicator, indicator)

        if country:   countries.add(country)
        if indicator: indicators.add(indicator)

        # ★ Only read content for series we'll actually chart
        series_meta: Dict = {}
        if ds_type in {"macro", "risk"}:
            try:
                text        = _read_gcs_csv_text(bucket, b.name)
                series_meta = _build_series_from_text(base, text)
            except Exception:
                series_meta = {}

        d0 = series_meta.get("min_date")
        d1 = series_meta.get("max_date")
        if d0: min_date = d0 if min_date is None else min(min_date, d0)
        if d1: max_date = d1 if max_date is None else max(max_date, d1)

        items.append({
            "file": base, "blob": b.name, "stem": stem,
            "type": ds_type, "country": country,
            "indicator": indicator, "indicator_display": display,
            "size_bytes": getattr(b, "size", None),
            "min_date": d0, "max_date": d1,
            "points": series_meta.get("points"),
        })

    return {
        "gcs_prefix":     prefix,
        "sources":        items,
        "country_count":  len(countries),
        "indicator_count":len(indicators),
        "source_count":   len(items),
        "min_date":       min_date,
        "max_date":       max_date,
    }


def _coverage_matrix(inv: Dict[str, Any]) -> Dict[str, Any]:
    sources    = inv.get("sources", [])
    countries  = sorted({
        s["country"] for s in sources
        if s.get("type") == "macro" and isinstance(s.get("country"), str)
    })
    indicators = sorted({
        s["indicator"] for s in sources
        if s.get("type") == "macro" and isinstance(s.get("indicator"), str)
    })

    matrix = []
    for c in countries:
        row = {"country": c}
        for ind in indicators:
            row[ind] = any(
                s for s in sources
                if s.get("type") == "macro"
                and s.get("country") == c
                and s.get("indicator") == ind
            )
        matrix.append(row)

    return {
        "countries":  countries,
        "indicators": [{"key": i, "label": INDICATOR_DISPLAY.get(i, i)} for i in indicators],
        "matrix":     matrix,
    }


def _find_blob_for(country: str, indicator: str, inv: Dict[str, Any]) -> Optional[str]:
    ind_norm = indicator.strip().lower()
    c_norm   = country.strip()

    # macro match first
    for s in inv.get("sources", []):
        if s.get("indicator") == ind_norm and s.get("country") == c_norm:
            return s.get("blob")
    # non-country (risk/global)
    for s in inv.get("sources", []):
        if s.get("indicator") == ind_norm and not s.get("country"):
            return s.get("blob")
    return None


# -------------------------
# Routes
# -------------------------
@data_dashboard_bp.get("/data")
def data_page():
    return render_template_string(_DATA_DASHBOARD_HTML)


@data_dashboard_bp.get("/api/data/inventory")
def api_inventory():
    try:
        inv = _get_cached_inventory()
        return jsonify(inv)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@data_dashboard_bp.get("/api/data/matrix")
def api_matrix():
    """
    ★ Now just reads from cache — no extra GCS scan.
    """
    try:
        mat = _get_cached_matrix()
        return jsonify(mat)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@data_dashboard_bp.post("/api/data/cache/invalidate")
def api_cache_invalidate():
    """Manual cache bust — call this after uploading new data."""
    _invalidate_cache()
    return jsonify({"status": "ok", "message": "Cache invalidated."})


@data_dashboard_bp.get("/api/data/series")
def api_series():
    country   = (request.args.get("country") or "").strip()
    indicator = (request.args.get("indicator") or "").strip().lower()

    if not indicator:
        return jsonify({"ok": False, "message": "indicator param is required"}), 400

    try:
        inv = _get_cached_inventory()           # ★ uses cache, no extra GCS scan
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500

    bucket    = _get_gcs_bucket()
    blob_name = _find_blob_for(country, indicator, inv)

    if not blob_name:
        return jsonify({
            "ok": False,
            "message": f"No dataset found for country='{country}' indicator='{indicator}'",
            "available_indicators": list({
                s["indicator"] for s in inv.get("sources", [])
                if s.get("country") == country or not country
            }),
        }), 404

    try:
        text = _read_gcs_csv_text(bucket, blob_name)
        meta = _build_series_from_text(Path(blob_name).name, text)
    except Exception as e:
        return jsonify({"ok": False, "message": f"Failed to read dataset: {e}"}), 500

    return jsonify({
        "ok": True,
        "country":           country or None,
        "indicator":         indicator,
        "indicator_display": INDICATOR_DISPLAY.get(indicator, indicator),
        "file":              Path(blob_name).name,
        "blob":              blob_name,
        "date_col":          meta["date_col"],
        "value_col":         meta["value_col"],
        "min_date":          meta["min_date"],
        "max_date":          meta["max_date"],
        "dates":             meta["dates"],
        "values":            meta["values"],
    })


@data_dashboard_bp.get("/api/data/compare")
def api_compare():
    countries_raw = (request.args.get("countries") or "").strip()
    indicator     = (request.args.get("indicator") or "").strip().lower()
    normalize     = request.args.get("normalize", "").lower() in {"1", "true", "yes", "on"}

    countries = [c.strip() for c in countries_raw.split(",") if c.strip()]
    if len(countries) != 2:
        return jsonify({"ok": False, "message": "Provide exactly 2 comma-separated countries"}), 400
    if not indicator:
        return jsonify({"ok": False, "message": "indicator param is required"}), 400

    try:
        inv = _get_cached_inventory()           # ★ uses cache
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500

    bucket = _get_gcs_bucket()
    series_payload = []

    for c in countries:
        blob_name = _find_blob_for(c, indicator, inv)
        if not blob_name:
            series_payload.append({"country": c, "ok": False, "message": "dataset not found"})
            continue
        try:
            text = _read_gcs_csv_text(bucket, blob_name)
            meta = _build_series_from_text(Path(blob_name).name, text)
            vals = _normalize_100(meta["values"]) if normalize else meta["values"]
            series_payload.append({
                "country": c, "ok": True,
                "file": Path(blob_name).name, "blob": blob_name,
                "dates": meta["dates"], "values": vals,
                "min_date": meta["min_date"], "max_date": meta["max_date"],
            })
        except Exception as e:
            series_payload.append({"country": c, "ok": False, "message": str(e)})

    return jsonify({
        "ok":                True,
        "indicator":         indicator,
        "indicator_display": INDICATOR_DISPLAY.get(indicator, indicator),
        "normalize":         normalize,
        "series":            series_payload,
    })


# ─────────────────────────────────────────────────────────────
# Data Dashboard HTML  (embedded, style matches new design system)
# ─────────────────────────────────────────────────────────────
_DATA_DASHBOARD_HTML = r"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>ARGUS — Data Infrastructure</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <style>
    :root {
      --bg:       #F0F2F5;
      --card:     #FFFFFF;
      --border:   #E2E8F0;
      --text:     #0F172A;
      --muted:    #94A3B8;
      --secondary:#475569;
      --primary:  #2563EB;
      --success:  #166534;
      --success-bg:#DCFCE7;
      --mono:     'IBM Plex Mono', monospace;
      --sans:     'IBM Plex Sans', sans-serif;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: var(--sans);
      background: var(--bg);
      color: var(--text);
      font-size: 13px;
      -webkit-font-smoothing: antialiased;
    }

    /* Header */
    .hdr {
      background: #0A0E1A;
      padding: 14px 24px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      border-bottom: 1px solid #1E2535;
    }
    .hdr h1 {
      font-family: var(--mono);
      font-size: 13px;
      font-weight: 600;
      color: #fff;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }
    .hdr p { font-size: 11px; color: #4A5568; margin-top: 2px; font-family: var(--mono); }
    .hdr-right { display: flex; gap: 8px; align-items: center; }

    /* Layout */
    .wrap { max-width: 1400px; margin: 0 auto; padding: 20px 24px; }

    /* KPI strip */
    .kpi-strip {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 12px;
      margin-bottom: 20px;
    }
    .kpi-card {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 16px 20px;
    }
    .kpi-label {
      font-family: var(--mono);
      font-size: 9px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.1em;
      color: var(--muted);
      margin-bottom: 8px;
    }
    .kpi-value {
      font-family: var(--mono);
      font-size: 26px;
      font-weight: 700;
      color: var(--text);
      line-height: 1;
    }
    .kpi-sub { font-size: 10px; color: var(--muted); margin-top: 4px; }

    /* Main grid — row 1: Inventory (narrow) | Signal Charts (wider).
       Coverage Matrix lives on its own full-width row below so the
       country × indicator table doesn't squeeze the chart column. */
    .main-grid {
      display: grid;
      grid-template-columns: minmax(0, 0.85fr) minmax(0, 1.15fr);
      gap: 16px;
      margin-bottom: 16px;
    }
    .matrix-row { margin-bottom: 20px; }

    /* Cards */
    .card {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 20px;
    }
    .card-title {
      font-family: var(--mono);
      font-size: 10px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.1em;
      color: var(--muted);
      padding-bottom: 10px;
      border-bottom: 1px solid var(--border);
      margin-bottom: 14px;
    }
    .card-sub { font-size: 11px; color: var(--muted); margin-bottom: 12px; }

    /* Tables */
    .tbl { width: 100%; border-collapse: collapse; font-size: 12px; }
    .tbl thead th {
      padding: 8px 10px;
      text-align: left;
      font-size: 9px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--muted);
      background: #F8FAFC;
      border-bottom: 1px solid var(--border);
      font-family: var(--mono);
    }
    .tbl tbody td {
      padding: 8px 10px;
      border-bottom: 1px solid var(--border);
      color: var(--text);
      vertical-align: top;
    }
    .tbl tbody tr:last-child td { border-bottom: none; }
    .tbl tbody tr:hover td { background: #FAFAFA; }

    .pill {
      display: inline-block;
      padding: 2px 8px;
      border-radius: 3px;
      background: #F1F5F9;
      border: 1px solid var(--border);
      font-family: var(--mono);
      font-size: 10px;
      color: var(--secondary);
    }
    .pill.ok   { background: var(--success-bg); color: var(--success); border-color: #BBF7D0; }
    .pill.high { background: #FEE2E2; color: #991B1B; border-color: #FECACA; }
    .pill.med  { background: #FEF3C7; color: #92400E; border-color: #FDE68A; }

    /* Scrollable table wrapper */
    .tbl-wrap { max-height: 340px; overflow-y: auto; border: 1px solid var(--border); border-radius: 4px; }

    /* Coverage Matrix — full width, country×indicator grid can have
       15+ columns. Compact cells + horizontal scroll + sticky first
       column keep it readable without forcing the whole card wide. */
    .matrix-wrap {
      max-height: 380px;
      overflow: auto;
      border: 1px solid var(--border);
      border-radius: 4px;
    }
    .matrix-wrap table.tbl { font-size: 11px; }
    .matrix-wrap thead th,
    .matrix-wrap tbody td {
      padding: 4px 8px;
      white-space: nowrap;
    }
    .matrix-wrap thead th {
      position: sticky;
      top: 0;
      z-index: 2;
    }
    .matrix-wrap thead th:first-child,
    .matrix-wrap tbody td:first-child {
      position: sticky;
      left: 0;
      background: #F8FAFC;
      z-index: 1;
      border-right: 1px solid var(--border);
    }
    .matrix-wrap thead th:first-child { z-index: 3; }
    .matrix-wrap .pill {
      padding: 1px 5px;
      font-size: 9px;
    }

    /* Charts section */
    .chart-section { margin-bottom: 24px; }
    .chart-section + .chart-section { border-top: 1px solid var(--border); padding-top: 20px; }

    .ctrl-row {
      display: flex;
      gap: 8px;
      align-items: center;
      flex-wrap: wrap;
      margin-bottom: 12px;
    }
    select, .btn {
      padding: 7px 12px;
      border: 1px solid var(--border);
      border-radius: 4px;
      font-size: 12px;
      font-family: var(--sans);
      background: white;
      color: var(--text);
      cursor: pointer;
      outline: none;
    }
    select:focus { border-color: var(--primary); }
    .btn {
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      transition: all 0.15s;
    }
    .btn:hover { background: #F8FAFC; border-color: #CBD5E1; }
    .btn-primary {
      background: var(--primary);
      color: white;
      border: none;
    }
    .btn-primary:hover { background: #1D4ED8; }
    .btn-sm { padding: 5px 10px; font-size: 11px; }

    .chart-wrap { height: 240px; position: relative; }
    .chart-note {
      font-size: 10px;
      color: var(--muted);
      font-family: var(--mono);
      margin-top: 6px;
      min-height: 16px;
    }
    .chart-error {
      font-size: 11px;
      color: #991B1B;
      background: #FEE2E2;
      padding: 8px 12px;
      border-radius: 4px;
      margin-top: 6px;
      font-family: var(--mono);
    }

    /* Status bar */
    .status-bar {
      display: flex;
      align-items: center;
      gap: 8px;
      font-family: var(--mono);
      font-size: 10px;
      color: var(--muted);
    }
    .status-dot { width:6px; height:6px; border-radius:50%; background:#22C55E; }
    .status-dot.loading { background:#F59E0B; animation: pulse 1s infinite; }
    @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }

    /* Loading overlay inside card */
    .loading-row td { color: var(--muted); font-style: italic; text-align: center; padding: 24px; }

    /* Responsive */
    @media(max-width: 900px) {
      .main-grid { grid-template-columns: 1fr; }
      .kpi-strip { grid-template-columns: repeat(2,1fr); }
    }
  </style>
</head>
<body>

<div class="hdr">
  <div>
    <h1>ARGUS // Data Infrastructure Console</h1>
    <p>Inventory · Coverage Matrix · Signal Charts</p>
  </div>
  <div class="hdr-right">
    <div class="status-bar">
      <div class="status-dot loading" id="statusDot"></div>
      <span id="statusText">Loading…</span>
    </div>
    <button class="btn btn-sm" onclick="bustCache()" title="Force reload from GCS">↻ Refresh</button>
  </div>
</div>

<div class="wrap">

  <!-- KPI Strip -->
  <div class="kpi-strip">
    <div class="kpi-card">
      <div class="kpi-label">Data Sources</div>
      <div class="kpi-value" id="kpi_sources">—</div>
      <div class="kpi-sub" id="kpi_dir">—</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-label">Countries</div>
      <div class="kpi-value" id="kpi_countries">—</div>
      <div class="kpi-sub">Macro coverage</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-label">Indicators</div>
      <div class="kpi-value" id="kpi_indicators">—</div>
      <div class="kpi-sub">Unique series types</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-label">Time Range</div>
      <div class="kpi-value" style="font-size:16px; padding-top:4px;" id="kpi_range">—</div>
      <div class="kpi-sub">Across all series</div>
    </div>
  </div>

  <!-- Main Grid -->
  <div class="main-grid">

    <!-- Left: Dataset Inventory (narrow, ~5 cols) -->
    <div class="card">
      <div class="card-title">Dataset Inventory</div>
      <div class="card-sub">All CSV files detected in current GCS prefix.</div>
      <div class="tbl-wrap">
        <table class="tbl" id="inv_table">
          <thead>
            <tr>
              <th>File</th><th>Type</th><th>Country</th>
              <th>Indicator</th><th>Range</th>
            </tr>
          </thead>
          <tbody>
            <tr class="loading-row"><td colspan="5">Loading inventory…</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Right: Signal Charts (single + compare stacked) -->
    <div class="card">
      <div class="card-title">Signal Charts</div>

      <!-- Single series -->
      <div class="chart-section">
        <div class="ctrl-row">
          <select id="single_country"></select>
          <select id="single_indicator"></select>
          <button class="btn btn-primary" onclick="loadSingle()">Load Series</button>
        </div>
        <div class="chart-wrap"><canvas id="singleChart"></canvas></div>
        <div class="chart-note" id="single_note"></div>
        <div class="chart-error" id="single_error" style="display:none;"></div>
      </div>

      <!-- Compare two countries -->
      <div class="chart-section">
        <div class="ctrl-row">
          <select id="c1"></select>
          <select id="c2"></select>
          <select id="compare_indicator"></select>
          <label style="display:flex;align-items:center;gap:5px;font-size:11px;color:var(--muted);cursor:pointer;">
            <input type="checkbox" id="normalize"/> Normalize
          </label>
          <button class="btn btn-primary" onclick="loadCompare()">Compare</button>
        </div>
        <div class="chart-wrap"><canvas id="compareChart"></canvas></div>
        <div class="chart-note" id="compare_note"></div>
        <div class="chart-error" id="compare_error" style="display:none;"></div>
      </div>

    </div>
  </div><!-- /main-grid -->

  <!-- Row 2: Coverage Matrix at full width.
       Country × indicator grid scrolls horizontally; first column is
       sticky so the country label stays visible as users scan across. -->
  <div class="card matrix-row">
    <div class="card-title">Coverage Matrix — Macro</div>
    <div class="card-sub">Country × indicator availability. Scroll horizontally for additional indicators.</div>
    <div class="matrix-wrap">
      <table class="tbl" id="matrix_table">
        <thead id="matrix_head"></thead>
        <tbody id="matrix_body">
          <!-- colspan picks a value safely larger than the real
               indicator count (15+ today, ~30 ceiling); HTML spec
               accepts up to 1000. Hardcoding a smaller number (10)
               would leave bare cells to the right of the loading
               message until JS replaces the body. -->
          <tr class="loading-row"><td colspan="999">Loading matrix…</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</div><!-- /wrap -->

<script>
// ── State ──────────────────────────────────────────
let inv = null;
let singleChart = null;
let compareChart = null;

const CHART_COLORS = ["#2563EB", "#F59E0B"];

function bytes(n) {
  if (!n) return "—";
  if (n < 1024) return n + " B";
  if (n < 1048576) return (n/1024).toFixed(1) + " KB";
  return (n/1048576).toFixed(1) + " MB";
}
function el(id) { return document.getElementById(id); }

function setStatus(text, loading = false) {
  el("statusText").textContent = text;
  el("statusDot").className = "status-dot" + (loading ? " loading" : "");
}

function showError(sectionId, msg) {
  const errEl = el(sectionId + "_error");
  if (errEl) { errEl.textContent = msg; errEl.style.display = "block"; }
}
function clearError(sectionId) {
  const errEl = el(sectionId + "_error");
  if (errEl) { errEl.style.display = "none"; errEl.textContent = ""; }
}

// ── Fetch helpers ──────────────────────────────────
async function fetchJSON(url) {
  const r = await fetch(url);
  const body = await r.json();
  if (!r.ok) throw new Error(body.error || body.message || `HTTP ${r.status}`);
  return body;
}

// ── Select helpers ─────────────────────────────────
function setOptions(selectEl, options, selectedVal) {
  selectEl.innerHTML = "";
  for (const opt of options) {
    const o = document.createElement("option");
    o.value = opt.value;
    o.textContent = opt.label;
    if (selectedVal && selectedVal === opt.value) o.selected = true;
    selectEl.appendChild(o);
  }
}

function populateSelects(matrix) {
  const countries = matrix.countries.map(c => ({ value: c, label: c }));
  const inds      = matrix.indicators.map(x => ({ value: x.key, label: x.label }));

  setOptions(el("single_country"),    countries, countries[0]?.value);
  setOptions(el("single_indicator"),  inds,      inds[0]?.value);
  setOptions(el("c1"),                countries, countries[0]?.value);
  setOptions(el("c2"),                countries, countries[1]?.value ?? countries[0]?.value);
  setOptions(el("compare_indicator"), inds,      inds[0]?.value);
}

// ── Inventory table ────────────────────────────────
function buildInventoryTable() {
  const tbody = el("inv_table").querySelector("tbody");
  tbody.innerHTML = "";
  for (const s of inv.sources) {
    const typeClass = s.type === "macro" ? "" : s.type === "risk" ? "high" : "med";
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>
        <span class="pill">${s.file}</span>
        <div style="font-size:10px;color:var(--muted);margin-top:2px;">${bytes(s.size_bytes)}</div>
      </td>
      <td><span class="pill ${typeClass}">${s.type}</span></td>
      <td>${s.country || "<span style='color:var(--muted)'>—</span>"}</td>
      <td>${s.indicator_display || s.indicator}</td>
      <td style="font-family:var(--mono);font-size:10px;color:var(--muted);">
        ${s.min_date || "—"} → ${s.max_date || "—"}
      </td>`;
    tbody.appendChild(tr);
  }
}

// ── Coverage matrix ────────────────────────────────
function buildMatrix(matrix) {
  const head = el("matrix_head");
  const body = el("matrix_body");
  head.innerHTML = "";
  body.innerHTML = "";

  const trh = document.createElement("tr");
  trh.innerHTML = "<th>Country</th>" +
    matrix.indicators.map(x => `<th>${x.label}</th>`).join("");
  head.appendChild(trh);

  for (const row of matrix.matrix) {
    const tr = document.createElement("tr");
    let tds = `<td><strong>${row.country}</strong></td>`;
    for (const ind of matrix.indicators) {
      tds += row[ind.key]
        ? `<td style="text-align:center;"><span class="pill ok">✓</span></td>`
        : `<td style="text-align:center;color:var(--muted);">—</td>`;
    }
    tr.innerHTML = tds;
    body.appendChild(tr);
  }
}

// ── Chart builder ──────────────────────────────────
function makeChart(chartRef, canvasId, labels, datasets) {
  const ctx = el(canvasId).getContext("2d");
  if (chartRef) chartRef.destroy();
  return new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: datasets.map((ds, i) => ({
        ...ds,
        borderColor:     CHART_COLORS[i % CHART_COLORS.length],
        backgroundColor: CHART_COLORS[i % CHART_COLORS.length] + "18",
        borderWidth: 1.5,
        pointRadius: 0,
        tension: 0.3,
        fill: i === 0,
      }))
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: datasets.length > 1, labels: { font: { size: 11 } } },
        tooltip: { enabled: true }
      },
      scales: {
        x: { ticks: { maxTicksLimit: 8, font: { size: 10 } }, grid: { color: "#F1F5F9" } },
        y: { ticks: { font: { size: 10 } }, grid: { color: "#F1F5F9" } }
      }
    }
  });
}

// ── Load single series ─────────────────────────────
async function loadSingle() {
  clearError("single");
  el("single_note").textContent = "Loading…";

  const country   = el("single_country").value;
  const indicator = el("single_indicator").value;

  try {
    const s = await fetchJSON(
      `/api/data/series?country=${encodeURIComponent(country)}&indicator=${encodeURIComponent(indicator)}`
    );
    if (!s.ok) throw new Error(s.message);

    singleChart = makeChart(
      singleChart, "singleChart",
      s.dates,
      [{ label: `${s.indicator_display} (${country})`, data: s.values }]
    );
    el("single_note").textContent =
      `${s.file} · ${s.date_col} → ${s.value_col} · ${s.min_date || "—"} to ${s.max_date || "—"} · ${s.dates.length} pts`;
  } catch (e) {
    el("single_note").textContent = "";
    showError("single", "Error: " + (e.message || e));
  }
}

// ── Load compare ───────────────────────────────────
async function loadCompare() {
  clearError("compare");
  el("compare_note").textContent = "Loading…";

  const c1        = el("c1").value;
  const c2        = el("c2").value;
  const indicator = el("compare_indicator").value;
  const normalize = el("normalize").checked ? "1" : "0";

  try {
    const payload = await fetchJSON(
      `/api/data/compare?countries=${encodeURIComponent(c1+","+c2)}&indicator=${encodeURIComponent(indicator)}&normalize=${normalize}`
    );
    if (!payload.ok) throw new Error(payload.message);

    const [s1, s2] = payload.series;
    if (!s1?.ok) throw new Error(`${c1}: ${s1?.message || "not found"}`);
    if (!s2?.ok) throw new Error(`${c2}: ${s2?.message || "not found"}`);

    // Date-align on the front-end
    const map1 = new Map((s1.dates || []).map((d, i) => [d, s1.values[i]]));
    const map2 = new Map((s2.dates || []).map((d, i) => [d, s2.values[i]]));
    const common = [...map1.keys()].filter(d => map2.has(d)).slice(-1200);
    const v1 = common.map(d => map1.get(d));
    const v2 = common.map(d => map2.get(d));

    compareChart = makeChart(
      compareChart, "compareChart", common,
      [
        { label: `${payload.indicator_display} (${c1})`, data: v1 },
        { label: `${payload.indicator_display} (${c2})`, data: v2 },
      ]
    );
    el("compare_note").textContent =
      `${payload.indicator_display} · normalize=${payload.normalize} · ${common.length} common pts`;
  } catch (e) {
    el("compare_note").textContent = "";
    showError("compare", "Error: " + (e.message || e));
  }
}

// ── Cache bust ─────────────────────────────────────
async function bustCache() {
  setStatus("Refreshing from GCS…", true);
  try {
    await fetch("/api/data/cache/invalidate", { method: "POST" });
    await boot();
  } catch (e) {
    setStatus("Refresh failed: " + e.message, false);
  }
}

// ── Boot ───────────────────────────────────────────
async function boot() {
  setStatus("Loading inventory…", true);
  try {
    // ★ Fetch inventory and matrix in parallel — one GCS scan (cached)
    const [invData, matData] = await Promise.all([
      fetchJSON("/api/data/inventory"),
      fetchJSON("/api/data/matrix"),
    ]);

    inv = invData;

    // KPIs
    el("kpi_sources").textContent   = inv.source_count   ?? "—";
    el("kpi_countries").textContent = inv.country_count  ?? "—";
    el("kpi_indicators").textContent= inv.indicator_count?? "—";
    el("kpi_range").textContent     = `${inv.min_date || "—"} → ${inv.max_date || "—"}`;
    el("kpi_dir").textContent       = inv.gcs_prefix || "local";

    buildInventoryTable();
    buildMatrix(matData);
    populateSelects(matData);

    setStatus("Ready", false);
  } catch (e) {
    setStatus("Load failed: " + e.message, false);
    console.error(e);
  }
}

boot();
</script>
</body>
</html>
"""
