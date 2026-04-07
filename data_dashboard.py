# data_dashboard.py
from __future__ import annotations

import os
import re
import csv

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from io import StringIO

from google.cloud import storage  # 你们 Guardian 已经在用这个

from flask import Blueprint, jsonify, request, render_template_string

try:
    import pandas as pd  # type: ignore
except Exception:
    pd = None  # optional

# -------------------------
# Blueprint
# -------------------------
data_dashboard_bp = Blueprint("data_dashboard", __name__)

# -------------------------
# Config / conventions
# -------------------------
# 你截图里文件名类似：us_cpi.csv, ca_policy_rate.csv, gdel t_conflict_light.csv, usgs_earthquakes_global.csv, news_*.csv
COUNTRY_PREFIX_RE = re.compile(r"^(us|ca|uk|de|jp|fr|br|in|au|mx|cn)_", re.I)

DEFAULT_COUNTRY_NAME_MAP = {
    "us": "USA",
    "ca": "Canada",
    "uk": "UK",
    "de": "Germany",
    "jp": "Japan",
    "fr": "France",
    "br": "Brazil",
    "in": "India",
    "au": "Australia",
    "mx": "Mexico",
    "cn": "China",
}

# 指标 key -> 展示名（你后面加新 csv 只要更新这里即可）
INDICATOR_DISPLAY = {
    "cpi": "CPI",
    "sofr": "SOFR",
    "policy_rate": "Policy Rate",
    "usdcad": "USD/CAD FX",
    "treasury_auctions": "US Treasury Auctions",
    # risk / events
    "conflict_light": "GDELT Conflict (Light)",
    "earthquakes_global": "USGS Earthquakes (Global)",
}

DEFAULT_ARGUS_GCS_BUCKET = "your-gcs-bucket"

# 数据类型归类：用文件名前缀判断
def _classify_dataset(file_stem: str) -> str:
    s = file_stem.lower()
    if s.startswith("news_"):
        return "news"
    if s.startswith("usgs_"):
        return "risk"
    if s.startswith("gdelt_"):
        return "risk"
    if s.startswith("bis_"):
        return "flows"
    # country macro series
    if COUNTRY_PREFIX_RE.search(s):
        return "macro"
    return "other"

def _get_gcs_prefix() -> str:
    """
    例子：gs://your-gcs-bucket/raw/dt=2026-02-18/
    """
    return os.getenv("DATA_DASHBOARD_GCS_PREFIX", "").strip()

def _parse_gs_uri(gs_uri: str) -> Tuple[str, str]:
    if not gs_uri.startswith("gs://"):
        raise ValueError("DATA_DASHBOARD_GCS_PREFIX must start with gs://")
    rest = gs_uri[5:]
    parts = rest.split("/", 1)
    bucket_name = parts[0]
    prefix = parts[1] if len(parts) > 1 else ""
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    return bucket_name, prefix

def _get_bucket() -> storage.bucket.Bucket:
    gs = _get_gcs_prefix()
    bucket_name, _ = _parse_gs_uri(gs)
    client = storage.Client()
    return client.bucket(bucket_name)

def _resolve_data_dir() -> Path:
    """
    本地 fallback（只有当 DATA_DASHBOARD_GCS_PREFIX 没设置时才会用）
    """
    env_dir = os.getenv("DATA_DASHBOARD_DATA_DIR", "").strip()
    if env_dir:
        return Path(env_dir).expanduser().resolve()

    here = Path(__file__).resolve().parent
    candidates = [
        here / "Dataset",
        here / "guardian_poc" / "data",
        here,
    ]
    for c in candidates:
        if c.exists() and any(c.glob("*.csv")):
            return c
    return candidates[0]
def _get_gcs_bucket():
    bucket_name = (
        os.getenv("ARGUS_GCS_BUCKET", "").strip()
        or os.getenv("GUARDIAN_GCS_BUCKET", "").strip()
        or DEFAULT_ARGUS_GCS_BUCKET
    )
    project = os.getenv("GOOGLE_CLOUD_PROJECT", "").strip() or None
    adc_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if adc_path:
        client = storage.Client(project=project)
        return client.bucket(bucket_name)

    here = Path(__file__).resolve().parent
    key_candidates = [
        here / "keys" / "treasury-reader.json",
        here / "keys" / "service-account.json",
    ]
    for key_path in key_candidates:
        if key_path.exists():
            client = storage.Client.from_service_account_json(str(key_path), project=project)
            return client.bucket(bucket_name)

    client = storage.Client(project=project)
    return client.bucket(bucket_name)


def _resolve_data_dashboard_prefix(bucket) -> str:
    explicit_prefix = os.getenv("ARGUS_GCS_PREFIX", "").strip()
    if explicit_prefix:
        return explicit_prefix

    base_prefix = os.getenv("ARGUS_GCS_BASE_PREFIX", "").strip()
    if not base_prefix:
        guardian_root = os.getenv("GUARDIAN_GCS_PREFIX", "").strip().strip("/")
        guardian_raw = os.getenv("GUARDIAN_GCS_RAW_PREFIX", "raw").strip().strip("/")
        if guardian_root:
            base_prefix = f"{guardian_root}/{guardian_raw}/"
        else:
            base_prefix = f"{guardian_raw}/"
    return _gcs_latest_dt_prefix(bucket, base_prefix)


def _gcs_latest_dt_prefix(bucket, base_prefix: str = "raw/") -> str:
    """
    Auto-detect latest dt=YYYY-MM-DD under base_prefix.
    Returns like: raw/dt=2026-02-18/
    """
    base_prefix = base_prefix.rstrip("/") + "/"
    dt_values = set()

    # list_blobs is flat; we just parse dt=... from blob.name
    for b in bucket.list_blobs(prefix=base_prefix):
        m = re.search(rf"{re.escape(base_prefix)}dt=(\d{{4}}-\d{{2}}-\d{{2}})/", b.name)
        if m:
            dt_values.add(m.group(1))

    if not dt_values:
        return base_prefix  # fallback: raw/

    latest = sorted(dt_values)[-1]
    return f"{base_prefix}dt={latest}/"


def _list_gcs_csv_blobs(bucket, prefix: str) -> List[Any]:
    prefix = prefix.rstrip("/") + "/"
    blobs = list(bucket.list_blobs(prefix=prefix))
    return [b for b in blobs if b.name.lower().endswith(".csv")]


def _read_gcs_csv_text(bucket, blob_name: str) -> str:
    return bucket.blob(blob_name).download_as_text()


def _read_csv_from_text(text: str) -> List[Dict[str, Any]]:
    if pd is not None:
        df = pd.read_csv(StringIO(text))
        return df.to_dict(orient="records")

    # fallback: csv.DictReader
    rows: List[Dict[str, Any]] = []
    reader = csv.DictReader(StringIO(text))
    for r in reader:
        rows.append(dict(r))
    return rows


def _build_series_from_text(file_name: str, text: str) -> Dict[str, Any]:
    rows = _read_csv_from_text(text)
    date_col, value_col = _infer_xy(rows)

    points: List[Tuple[str, float]] = []
    if date_col and value_col:
        for r in rows:
            d = _parse_date_any(r.get(date_col))
            v = _safe_float(r.get(value_col))
            if d and v is not None:
                points.append((d, v))

    points.sort(key=lambda x: x[0])
    dates = [p[0] for p in points]
    values = [p[1] for p in points]

    return {
        "file": file_name,
        "date_col": date_col,
        "value_col": value_col,
        "points": len(points),
        "dates": dates,
        "values": values,
        "min_date": dates[0] if dates else None,
        "max_date": dates[-1] if dates else None,
    }


def _inventory_from_gcs(bucket, prefix: str) -> Dict[str, Any]:
    blobs = _list_gcs_csv_blobs(bucket, prefix)

    items = []
    countries = set()
    indicators = set()
    min_date = None
    max_date = None

    for b in sorted(blobs, key=lambda x: x.name):
        base = Path(b.name).name          # us_cpi.csv
        stem = Path(b.name).stem          # us_cpi
        ds_type = _classify_dataset(stem)
        country = _infer_country_from_filename(stem)
        indicator = _infer_indicator_from_filename(stem)
        display = INDICATOR_DISPLAY.get(indicator, indicator)

        if country:
            countries.add(country)
        if indicator:
            indicators.add(indicator)

        series_meta = {}
        if ds_type in {"macro", "risk"}:
            try:
                text = _read_gcs_csv_text(bucket, b.name)
                series_meta = _build_series_from_text(base, text)
            except Exception:
                series_meta = {}

        d0 = series_meta.get("min_date")
        d1 = series_meta.get("max_date")
        if d0:
            min_date = d0 if min_date is None else min(min_date, d0)
        if d1:
            max_date = d1 if max_date is None else max(max_date, d1)

        items.append({
            "file": base,           # 给前端展示+选择用
            "blob": b.name,         # 关键：后端读数据用
            "stem": stem,
            "type": ds_type,
            "country": country,
            "indicator": indicator,
            "indicator_display": display,
            "size_bytes": getattr(b, "size", None),
            "min_date": d0,
            "max_date": d1,
            "points": series_meta.get("points"),
        })

    return {
        "gcs_prefix": prefix,
        "sources": items,
        "country_count": len(countries),
        "indicator_count": len(indicators),
        "source_count": len(items),
        "min_date": min_date,
        "max_date": max_date,
    }


def _list_csv_sources() -> List[Dict[str, Any]]:
    """
    统一返回 source 列表：
    - GCS:  {"kind":"gcs","file":"us_cpi.csv","stem":"us_cpi","blob":"raw/dt=.../us_cpi.csv","size_bytes":123}
    - Local:{"kind":"local","file":"us_cpi.csv","stem":"us_cpi","path":Path(...),"size_bytes":123}
    """
    gs = _get_gcs_prefix()
    if gs:
        bucket = _get_bucket()
        _, prefix = _parse_gs_uri(gs)
        blobs = list(bucket.list_blobs(prefix=prefix))
        out: List[Dict[str, Any]] = []
        for b in blobs:
            name = str(b.name)
            if not name.lower().endswith(".csv"):
                continue
            file_name = Path(name).name
            out.append({
                "kind": "gcs",
                "file": file_name,
                "stem": Path(file_name).stem,
                "blob": name,
                "size_bytes": getattr(b, "size", None),
            })
        return sorted(out, key=lambda x: x["file"])

    # fallback local
    data_dir = _resolve_data_dir()
    out: List[Dict[str, Any]] = []
    if data_dir.exists():
        for p in sorted([p for p in data_dir.glob("*.csv") if p.is_file()]):
            out.append({
                "kind": "local",
                "file": p.name,
                "stem": p.stem,
                "path": p,
                "size_bytes": p.stat().st_size,
            })
    return out


def _infer_country_from_filename(stem: str) -> Optional[str]:
    m = COUNTRY_PREFIX_RE.match(stem.lower())
    if not m:
        return None
    return DEFAULT_COUNTRY_NAME_MAP.get(m.group(1).lower(), m.group(1).upper())


def _infer_indicator_from_filename(stem: str) -> str:
    s = stem.lower()
    # strip country prefix (us_, ca_, etc.)
    s = COUNTRY_PREFIX_RE.sub("", s)
    # strip known prefixes like news_, usgs_, gdelt_
    s = re.sub(r"^(news|usgs|gdelt|bis)_", "", s)
    return s


def _safe_float(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        if isinstance(x, str) and not x.strip():
            return None
        return float(x)
    except Exception:
        return None


def _parse_date_any(x: Any) -> Optional[str]:
    """
    返回 ISO yyyy-mm-dd 字符串（尽量）
    支持：YYYY-MM-DD / YYYY-MM / epoch seconds / datetime
    """
    if x is None:
        return None
    if isinstance(x, datetime):
        return x.date().isoformat()
    s = str(x).strip()
    if not s:
        return None

    # epoch seconds
    if re.fullmatch(r"\d{10}", s):
        try:
            dt = datetime.utcfromtimestamp(int(s))
            return dt.date().isoformat()
        except Exception:
            pass

    # YYYY-MM-DD
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s
    # YYYY-MM -> normalize to first day
    if re.fullmatch(r"\d{4}-\d{2}", s):
        return f"{s}-01"

    # try pandas date parse if available
    if pd is not None:
        try:
            dt = pd.to_datetime(s, errors="coerce")
            if pd.isna(dt):
                return None
            return dt.date().isoformat()
        except Exception:
            return None

    return None


def _read_csv_any(source: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    source: {"kind":"gcs"...} or {"kind":"local"...}
    """
    if source.get("kind") == "gcs":
        bucket = _get_bucket()
        blob = bucket.blob(source["blob"])
        text = blob.download_as_text()

        if pd is not None:
            try:
                df = pd.read_csv(StringIO(text))
                return df.to_dict(orient="records")
            except Exception:
                pass

        rows: List[Dict[str, Any]] = []
        reader = csv.DictReader(StringIO(text))
        for r in reader:
            rows.append(dict(r))
        return rows

    # local
    path: Path = source["path"]

    if pd is not None:
        try:
            df = pd.read_csv(path)
            return df.to_dict(orient="records")
        except Exception:
            pass

    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="ignore", newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(dict(r))
    return rows


def _infer_xy(rows: List[Dict[str, Any]]) -> Tuple[Optional[str], Optional[str]]:
    """
    尝试从列名里找 date/time + value
    """
    if not rows:
        return (None, None)
    cols = list(rows[0].keys())

    # date candidates
    date_candidates = ["date", "datetime", "time", "timestamp", "published_at", "fiscalDateEnding"]
    value_candidates = ["value", "close", "rate", "cpi", "amount", "auction_amount", "count", "score"]

    date_col = None
    for c in cols:
        if c is None:
            continue
        lc = str(c).strip().lower()
        if lc in [d.lower() for d in date_candidates]:
            date_col = c
            break
    if date_col is None:
        # heuristic: any col containing "date" or "time"
        for c in cols:
            lc = str(c).strip().lower()
            if "date" in lc or "time" in lc:
                date_col = c
                break

    value_col = None
    for c in cols:
        lc = str(c).strip().lower()
        if lc in [v.lower() for v in value_candidates]:
            value_col = c
            break
    if value_col is None:
        # heuristic: first numeric-ish column excluding date
        for c in cols:
            if c == date_col:
                continue
            # sample few rows
            sample = []
            for r in rows[:10]:
                sample.append(_safe_float(r.get(c)))
            numeric_hits = sum(1 for v in sample if v is not None)
            if numeric_hits >= max(2, len(sample) // 2):
                value_col = c
                break

    return (date_col, value_col)


def _build_series(source: Dict[str, Any]) -> Dict[str, Any]:
    rows = _read_csv_any(source)
    date_col, value_col = _infer_xy(rows)

    points: List[Tuple[str, float]] = []
    if date_col and value_col:
        for r in rows:
            d = _parse_date_any(r.get(date_col))
            v = _safe_float(r.get(value_col))
            if d and v is not None:
                points.append((d, v))

    points.sort(key=lambda x: x[0])
    dates = [p[0] for p in points]
    values = [p[1] for p in points]

    return {
        "file": source["file"],
        "date_col": date_col,
        "value_col": value_col,
        "points": len(points),
        "dates": dates,
        "values": values,
        "min_date": dates[0] if dates else None,
        "max_date": dates[-1] if dates else None,
    }


def _normalize_100(values: List[float]) -> List[float]:
    if not values:
        return values
    base = values[0]
    if base == 0:
        return values
    return [round(v / base * 100.0, 4) for v in values]


def _inventory(data_dir: Path) -> Dict[str, Any]:
    sources = _list_csv_sources()
    items = []
    countries = set()
    indicators = set()

    min_date = None
    max_date = None

    for src in sources:
        stem = src["stem"]
        ds_type = _classify_dataset(stem)
        country = _infer_country_from_filename(stem)
        indicator = _infer_indicator_from_filename(stem)
        display = INDICATOR_DISPLAY.get(indicator, indicator)

        if country:
            countries.add(country)
        if indicator:
            indicators.add(indicator)

        series_meta = {}
        if ds_type in {"macro", "risk"}:
            try:
                series_meta = _build_series(src)
            except Exception:
                series_meta = {}

        d0 = series_meta.get("min_date")
        d1 = series_meta.get("max_date")
        if d0:
            min_date = d0 if min_date is None else min(min_date, d0)
        if d1:
            max_date = d1 if max_date is None else max(max_date, d1)

        items.append({
            "file": src["file"],
            "stem": stem,
            "type": ds_type,
            "country": country,
            "indicator": indicator,
            "indicator_display": display,
            "size_bytes": src.get("size_bytes"),
            "min_date": d0,
            "max_date": d1,
            "points": series_meta.get("points"),
            # 关键：把 blob/path 存进去，后面画图要用
            "kind": src.get("kind"),
            "blob": src.get("blob"),
            "path": str(src.get("path")) if src.get("path") else None,
        })

    # data_dir 这里改成“当前数据源”
    gs = _get_gcs_prefix()
    data_loc = gs if gs else str(data_dir)

    return {
        "data_dir": data_loc,
        "sources": items,
        "country_count": len(countries),
        "indicator_count": len(indicators),
        "source_count": len(items),
        "min_date": min_date,
        "max_date": max_date,
    }


def _coverage_matrix(inv: Dict[str, Any]) -> Dict[str, Any]:
    # only macro series considered for matrix
    sources = inv.get("sources", [])
    countries = sorted({
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
            hit = any(s for s in sources if s.get("type") == "macro" and s.get("country") == c and s.get("indicator") == ind)
            row[ind] = bool(hit)
        matrix.append(row)

    return {
        "countries": countries,
        "indicators": [{"key": i, "label": INDICATOR_DISPLAY.get(i, i)} for i in indicators],
        "matrix": matrix,
    }


def _find_source_for(country: str, indicator: str, inv: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    country_norm = country.strip()
    ind_norm = indicator.strip().lower()

    # macro first
    for s in inv.get("sources", []):
        if s.get("indicator") == ind_norm and s.get("country") == country_norm:
            return s

    # non-country (risk)
    for s in inv.get("sources", []):
        if s.get("indicator") == ind_norm and not s.get("country"):
            return s

    return None

def _to_source_dict(item: Dict[str, Any]) -> Dict[str, Any]:
    """
    inv['sources'] 里的一条 item -> _build_series() 需要的 source dict
    """
    src = {
        "kind": item.get("kind"),
        "file": item["file"],
        "stem": item.get("stem") or Path(item["file"]).stem,
    }
    if item.get("kind") == "gcs":
        src["blob"] = item["blob"]
    else:
        # item["path"] 在 inventory 里我让你存的是 str
        src["path"] = Path(item["path"])
    return src

# -------------------------
# Routes / APIs
# -------------------------
@data_dashboard_bp.get("/data")
def data_page():
    # 单文件：用 render_template_string，避免你去改 templates
    return render_template_string(_DATA_DASHBOARD_HTML)


@data_dashboard_bp.get("/api/data/inventory")
def api_inventory():
    bucket = _get_gcs_bucket()
    prefix = _resolve_data_dashboard_prefix(bucket)

    inv = _inventory_from_gcs(bucket, prefix)
    return jsonify(inv)


@data_dashboard_bp.get("/api/data/matrix")
def api_matrix():
    bucket = _get_gcs_bucket()
    prefix = _resolve_data_dashboard_prefix(bucket)

    inv = _inventory_from_gcs(bucket, prefix)
    return jsonify(_coverage_matrix(inv))

def _find_blob_for(country: str, indicator: str, inv: Dict[str, Any]) -> Optional[str]:
    country_norm = country.strip()
    ind_norm = indicator.strip().lower()

    for s in inv.get("sources", []):
        if s.get("indicator") == ind_norm and s.get("country") == country_norm:
            return s.get("blob")

    for s in inv.get("sources", []):
        if s.get("indicator") == ind_norm and not s.get("country"):
            return s.get("blob")

    return None

@data_dashboard_bp.get("/api/data/series")
def api_series():
    country = (request.args.get("country") or "").strip()
    indicator = (request.args.get("indicator") or "").strip().lower()

    bucket = _get_gcs_bucket()
    prefix = _resolve_data_dashboard_prefix(bucket)

    inv = _inventory_from_gcs(bucket, prefix)
    blob_name = _find_blob_for(country, indicator, inv)
    if not blob_name:
        return jsonify({"ok": False, "message": "dataset not found", "country": country, "indicator": indicator}), 404

    text = _read_gcs_csv_text(bucket, blob_name)
    meta = _build_series_from_text(Path(blob_name).name, text)

    return jsonify({
        "ok": True,
        "country": country or None,
        "indicator": indicator,
        "indicator_display": INDICATOR_DISPLAY.get(indicator, indicator),
        "file": Path(blob_name).name,
        "blob": blob_name,
        "date_col": meta["date_col"],
        "value_col": meta["value_col"],
        "min_date": meta["min_date"],
        "max_date": meta["max_date"],
        "dates": meta["dates"],
        "values": meta["values"],
    })


@data_dashboard_bp.get("/api/data/compare")
def api_compare():
    countries_raw = (request.args.get("countries") or "").strip()
    indicator = (request.args.get("indicator") or "").strip().lower()
    normalize = (request.args.get("normalize") or "").strip().lower() in {"1","true","yes","on"}

    countries = [c.strip() for c in countries_raw.split(",") if c.strip()]
    if len(countries) != 2:
        return jsonify({"ok": False, "message": "countries must have exactly 2 items"}), 400

    bucket = _get_gcs_bucket()
    prefix = _resolve_data_dashboard_prefix(bucket)

    inv = _inventory_from_gcs(bucket, prefix)

    series_payload = []
    for c in countries:
        blob_name = _find_blob_for(c, indicator, inv)
        if not blob_name:
            series_payload.append({"country": c, "ok": False, "message": "dataset not found"})
            continue

        text = _read_gcs_csv_text(bucket, blob_name)
        meta = _build_series_from_text(Path(blob_name).name, text)
        vals = meta["values"]
        if normalize:
            vals = _normalize_100(vals)

        series_payload.append({
            "country": c,
            "ok": True,
            "file": Path(blob_name).name,
            "blob": blob_name,
            "dates": meta["dates"],
            "values": vals,
            "min_date": meta["min_date"],
            "max_date": meta["max_date"],
        })

    return jsonify({
        "ok": True,
        "indicator": indicator,
        "indicator_display": INDICATOR_DISPLAY.get(indicator, indicator),
        "normalize": normalize,
        "series": series_payload,
    })


# -------------------------
# Single-file HTML (Chart.js)
# -------------------------
_DATA_DASHBOARD_HTML = r"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>ARGUS Data Infrastructure</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <link rel="stylesheet" href="/static/css/data_console.css">
</head>
<body>
<header>
  <div>
    <h1>ARGUS | DATA INFRASTRUCTURE CONSOLE</h1>
    <p>Inventory · Coverage · Signal Analysis</p>
  </div>
  <div class="pill" id="kpi_dir">-</div>
</header>

<div class="wrap">
  <div class="grid cards" id="kpis">
    <div class="card"><h3>Data Sources</h3><div class="kpi" id="kpi_sources">-</div><div class="sub" id="kpi_dir">-</div></div>
    <div class="card"><h3>Countries</h3><div class="kpi" id="kpi_countries">-</div><div class="sub">Macro coverage countries</div></div>
    <div class="card"><h3>Indicators</h3><div class="kpi" id="kpi_indicators">-</div><div class="sub">Unique file indicators</div></div>
    <div class="card"><h3>Time Range</h3><div class="kpi" id="kpi_range">-</div><div class="sub">From parsed series metadata</div></div>
  </div>

  <div style="height: 12px;"></div>

  <div class="grid main">
    <div class="card">
      <h3>Dataset Inventory</h3>
      <div class="sub muted">Shows what the app can read right now (based on your CSV directory).</div>
      <div style="height: 10px;"></div>
      <div style="max-height: 420px; overflow: auto;">
        <table id="inv_table">
          <thead>
            <tr>
              <th>File</th>
              <th>Type</th>
              <th>Country</th>
              <th>Indicator</th>
              <th>Range</th>
            </tr>
          </thead>
          <tbody></tbody>
        </table>
      </div>

      <div style="height: 14px;"></div>

      <h3>Coverage Matrix (Macro)</h3>
      <div class="sub muted">Country × Indicator availability table (macro only).</div>
      <div style="height: 10px;"></div>
      <div style="max-height: 320px; overflow: auto;">
        <table id="matrix_table">
          <thead id="matrix_head"></thead>
          <tbody id="matrix_body"></tbody>
        </table>
      </div>
    </div>

    <div class="card">
      <h3>Charts</h3>
      <div class="row">
        <select id="single_country"></select>
        <select id="single_indicator"></select>
        <button class="secondary" onclick="loadSingle()">Load Single Series</button>
      </div>
      <div style="height:280px;">
        <canvas id="singleChart"></canvas>
        </div>

      <div style="height: 18px;"></div>

      <div class="row">
        <select id="c1"></select>
        <select id="c2"></select>
        <select id="compare_indicator"></select>
        <label class="muted" style="display:flex;align-items:center;gap:6px;">
          <input type="checkbox" id="normalize" /> Normalize(100)
        </label>
        <button onclick="loadCompare()">Compare</button>
      </div>
      <div style="height: 8px;"></div>
      <div style="height:280px;">
        <canvas id="compareChart"></canvas>
        </div>
        
      <div style="height: 12px;"></div>
      <div class="sub muted" id="chart_note"></div>
    </div>
  </div>
</div>

<script>
let inv = null;
let singleChart = null;
let compareChart = null;

function bytes(n){ if(!n) return "-"; if(n<1024) return n+" B"; if(n<1024*1024) return (n/1024).toFixed(1)+" KB"; return (n/1024/1024).toFixed(1)+" MB"; }
function el(id){ return document.getElementById(id); }

async function fetchJSON(url){
  const r = await fetch(url);
  if(!r.ok) throw new Error(await r.text());
  return await r.json();
}

function setOptions(selectEl, options, selected){
  selectEl.innerHTML = "";
  for(const opt of options){
    const o = document.createElement("option");
    o.value = opt.value;
    o.textContent = opt.label;
    if(selected && selected === opt.value) o.selected = true;
    selectEl.appendChild(o);
  }
}

function buildInventoryTable(){
  const tbody = el("inv_table").querySelector("tbody");
  tbody.innerHTML = "";
  for(const s of inv.sources){
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><span class="pill">${s.file}</span><div class="muted">${bytes(s.size_bytes)}</div></td>
      <td>${s.type}</td>
      <td>${s.country || "<span class='muted'>—</span>"}</td>
      <td>${s.indicator_display || s.indicator}</td>
      <td class="muted">${(s.min_date||"-")} → ${(s.max_date||"-")}</td>
    `;
    tbody.appendChild(tr);
  }
}

async function buildMatrix(){
  const m = await fetchJSON("/api/data/matrix");
  const head = el("matrix_head");
  const body = el("matrix_body");
  head.innerHTML = "";
  body.innerHTML = "";

  const trh = document.createElement("tr");
  trh.innerHTML = `<th>Country</th>` + m.indicators.map(x => `<th>${x.label}</th>`).join("");
  head.appendChild(trh);

  for(const row of m.matrix){
    const tr = document.createElement("tr");
    let tds = `<td><b>${row.country}</b></td>`;
    for(const ind of m.indicators){
      const ok = !!row[ind.key];
      tds += `<td>${ok ? "<span class='ok'>✅</span>" : "<span class='no'>—</span>"}</td>`;
    }
    tr.innerHTML = tds;
    body.appendChild(tr);
  }

  // countries for selectors
  const countries = m.countries.map(c => ({value:c, label:c}));
  setOptions(el("single_country"), countries, countries[0]?.value);
  setOptions(el("c1"), countries, countries[0]?.value);
  setOptions(el("c2"), countries, countries[1]?.value || countries[0]?.value);

  const inds = m.indicators.map(x => ({value:x.key, label:x.label}));
  setOptions(el("single_indicator"), inds, inds[0]?.value);
  setOptions(el("compare_indicator"), inds, inds[0]?.value);
}

function ensureChart(chartRef, canvasId, labels, datasets){
  const ctx = el(canvasId).getContext("2d");
  if(chartRef) chartRef.destroy();
  return new Chart(ctx, {
        type: "line",
    data: {
      labels: labels,
      datasets: datasets
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: true },
        tooltip: { enabled: true }
      },
      scales: {
        x: {
          ticks: { maxTicksLimit: 10 }
        }
      }
    }
  });
}

async function loadSingle(){
  const country = el("single_country").value;
  const indicator = el("single_indicator").value;

  try{
    const s = await fetchJSON(`/api/data/series?country=${encodeURIComponent(country)}&indicator=${encodeURIComponent(indicator)}`);
    if(!s.ok){
      el("chart_note").textContent = s.message || "Failed to load series.";
      return;
    }

    const labels = s.dates || [];
    const values = s.values || [];

    singleChart = ensureChart(
      singleChart,
      "singleChart",
      labels,
      [{
        label: `${s.indicator_display} (${country})`,
        data: values
      }]
    );

    el("chart_note").textContent =
      `Single: ${s.file} | ${s.date_col} → ${s.value_col} | ${s.min_date || "-"} → ${s.max_date || "-"}`;

  }catch(e){
    el("chart_note").textContent = "Single series error: " + (e.message || e);
  }
}

async function loadCompare(){
  const c1 = el("c1").value;
  const c2 = el("c2").value;
  const indicator = el("compare_indicator").value;
  const normalize = el("normalize").checked ? "1" : "0";

  try{
    const payload = await fetchJSON(`/api/data/compare?countries=${encodeURIComponent(c1 + "," + c2)}&indicator=${encodeURIComponent(indicator)}&normalize=${normalize}`);
    if(!payload.ok){
      el("chart_note").textContent = payload.message || "Compare failed.";
      return;
    }

    const s1 = payload.series?.[0];
    const s2 = payload.series?.[1];
    if(!s1?.ok || !s2?.ok){
      el("chart_note").textContent = "One of the series is missing for selected countries.";
      return;
    }

    // Align by date intersection (front-end)
    const map1 = new Map((s1.dates || []).map((d, i) => [d, s1.values[i]]));
    const map2 = new Map((s2.dates || []).map((d, i) => [d, s2.values[i]]));

    const commonDates = [];
    const v1 = [];
    const v2 = [];

    for(const d of map1.keys()){
      if(map2.has(d)){
        commonDates.push(d);
        v1.push(map1.get(d));
        v2.push(map2.get(d));
      }
    }

    // keep last N points (avoid too heavy)
    const N = 1200;
    const start = Math.max(0, commonDates.length - N);
    const labels = commonDates.slice(start);
    const a = v1.slice(start);
    const b = v2.slice(start);

    compareChart = ensureChart(
      compareChart,
      "compareChart",
      labels,
      [
        { label: `${payload.indicator_display} (${c1})`, data: a },
        { label: `${payload.indicator_display} (${c2})`, data: b },
      ]
    );

    el("chart_note").textContent =
      `Compare: ${payload.indicator_display} | normalize=${payload.normalize} | ${c1}: ${s1.file} | ${c2}: ${s2.file} | common points: ${labels.length}`;

  }catch(e){
    el("chart_note").textContent = "Compare error: " + (e.message || e);
  }
}

async function boot(){
  try{
    inv = await fetchJSON("/api/data/inventory");

    el("kpi_sources").textContent = inv.source_count ?? "-";
    el("kpi_countries").textContent = inv.country_count ?? "-";
    el("kpi_indicators").textContent = inv.indicator_count ?? "-";
    el("kpi_range").textContent = `${inv.min_date || "-"} → ${inv.max_date || "-"}`;
    el("kpi_dir").textContent = inv.data_dir || "-";

    buildInventoryTable();
    await buildMatrix();

    // auto initial charts
    await loadSingle();
    await loadCompare();
  }catch(e){
    el("chart_note").textContent = "Boot error: " + (e.message || e);
  }
}

boot();
</script>
</body>
</html>
"""
# （上面 HTML 字符串结束后，这里什么都不用加）
