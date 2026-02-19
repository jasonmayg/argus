# data_dashboard.py
from __future__ import annotations

import os
import re
import csv
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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


def _resolve_data_dir() -> Path:
    """
    优先用环境变量 DATA_DASHBOARD_DATA_DIR
    否则尝试项目内常见目录：Dataset/ 或 guardian_poc/data/ 或当前目录
    """
    env_dir = os.getenv("DATA_DASHBOARD_DATA_DIR", "").strip()
    if env_dir:
        return Path(env_dir).expanduser().resolve()

    here = Path(__file__).resolve().parent
    candidates = [
        here / "Dataset",
        here / "guardian_poc" / "data",
        here,  # fallback
    ]
    for c in candidates:
        if c.exists() and any(c.glob("*.csv")):
            return c
    return candidates[0]


def _list_csv_files(data_dir: Path) -> List[Path]:
    if not data_dir.exists():
        return []
    return sorted([p for p in data_dir.glob("*.csv") if p.is_file()])


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


def _read_csv_any(path: Path) -> List[Dict[str, Any]]:
    """
    返回 row dict 列表（尽量兼容各种 schema）
    """
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


def _build_series(path: Path) -> Dict[str, Any]:
    rows = _read_csv_any(path)
    date_col, value_col = _infer_xy(rows)

    points: List[Tuple[str, float]] = []
    if date_col and value_col:
        for r in rows:
            d = _parse_date_any(r.get(date_col))
            v = _safe_float(r.get(value_col))
            if d and v is not None:
                points.append((d, v))

    # sort by date string
    points.sort(key=lambda x: x[0])
    dates = [p[0] for p in points]
    values = [p[1] for p in points]

    return {
        "file": path.name,
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
    files = _list_csv_files(data_dir)
    items = []
    countries = set()
    indicators = set()

    min_date = None
    max_date = None

    for fp in files:
        stem = fp.stem
        ds_type = _classify_dataset(stem)
        country = _infer_country_from_filename(stem)
        indicator = _infer_indicator_from_filename(stem)
        display = INDICATOR_DISPLAY.get(indicator, indicator)

        if country:
            countries.add(country)
        if indicator:
            indicators.add(indicator)

        # try lightweight peek for date range (macro/risk mostly)
        series_meta = {}
        if ds_type in {"macro", "risk"}:
            try:
                series_meta = _build_series(fp)
            except Exception:
                series_meta = {}

        d0 = series_meta.get("min_date")
        d1 = series_meta.get("max_date")
        if d0:
            min_date = d0 if min_date is None else min(min_date, d0)
        if d1:
            max_date = d1 if max_date is None else max(max_date, d1)

        items.append({
            "file": fp.name,
            "stem": stem,
            "type": ds_type,
            "country": country,
            "indicator": indicator,
            "indicator_display": display,
            "size_bytes": fp.stat().st_size,
            "min_date": d0,
            "max_date": d1,
            "points": series_meta.get("points"),
        })

    return {
        "data_dir": str(data_dir),
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
    countries = sorted({s["country"] for s in sources if s.get("type") == "macro" and s.get("country")})
    indicators = sorted({s["indicator"] for s in sources if s.get("type") == "macro" and s.get("indicator")})

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


def _find_file_for(country: str, indicator: str, inv: Dict[str, Any]) -> Optional[str]:
    # match macro first, then risk (some are non-country)
    country_norm = country.strip()
    ind_norm = indicator.strip().lower()
    for s in inv.get("sources", []):
        if s.get("indicator") == ind_norm and s.get("country") == country_norm:
            return s.get("file")
    # allow non-country indicators (risk)
    for s in inv.get("sources", []):
        if s.get("indicator") == ind_norm and not s.get("country"):
            return s.get("file")
    return None


# -------------------------
# Routes / APIs
# -------------------------
@data_dashboard_bp.get("/data")
def data_page():
    # 单文件：用 render_template_string，避免你去改 templates
    return render_template_string(_DATA_DASHBOARD_HTML)


@data_dashboard_bp.get("/api/data/inventory")
def api_inventory():
    data_dir = _resolve_data_dir()
    inv = _inventory(data_dir)
    return jsonify(inv)


@data_dashboard_bp.get("/api/data/matrix")
def api_matrix():
    data_dir = _resolve_data_dir()
    inv = _inventory(data_dir)
    return jsonify(_coverage_matrix(inv))


@data_dashboard_bp.get("/api/data/series")
def api_series():
    """
    /api/data/series?country=USA&indicator=cpi
    /api/data/series?indicator=conflict_light  (risk dataset without country)
    """
    country = (request.args.get("country") or "").strip()
    indicator = (request.args.get("indicator") or "").strip().lower()

    data_dir = _resolve_data_dir()
    inv = _inventory(data_dir)
    fn = _find_file_for(country, indicator, inv)
    if not fn:
        return jsonify({"ok": False, "message": "dataset not found", "country": country, "indicator": indicator}), 404

    fp = data_dir / fn
    meta = _build_series(fp)

    return jsonify({
        "ok": True,
        "country": country or None,
        "indicator": indicator,
        "indicator_display": INDICATOR_DISPLAY.get(indicator, indicator),
        "file": fn,
        "date_col": meta["date_col"],
        "value_col": meta["value_col"],
        "min_date": meta["min_date"],
        "max_date": meta["max_date"],
        "dates": meta["dates"],
        "values": meta["values"],
    })


@data_dashboard_bp.get("/api/data/compare")
def api_compare():
    """
    /api/data/compare?countries=USA,Canada&indicator=cpi&normalize=1
    """
    countries_raw = (request.args.get("countries") or "").strip()
    indicator = (request.args.get("indicator") or "").strip().lower()
    normalize = (request.args.get("normalize") or "").strip().lower() in {"1", "true", "yes", "on"}

    countries = [c.strip() for c in countries_raw.split(",") if c.strip()]
    if len(countries) != 2:
        return jsonify({"ok": False, "message": "countries must have exactly 2 items"}), 400

    data_dir = _resolve_data_dir()
    inv = _inventory(data_dir)

    series_payload = []
    for c in countries:
        fn = _find_file_for(c, indicator, inv)
        if not fn:
            series_payload.append({"country": c, "ok": False, "message": "dataset not found"})
            continue
        meta = _build_series(data_dir / fn)
        vals = meta["values"]
        if normalize:
            vals = _normalize_100(vals)
        series_payload.append({
            "country": c,
            "ok": True,
            "file": fn,
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
  <title>ARGUS Data Dashboard</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial; margin: 0; background: #f6f7fb; }
    header { background: #4f46e5; color: white; padding: 18px 20px; }
    header h1 { margin: 0; font-size: 20px; }
    header p { margin: 6px 0 0; opacity: .9; font-size: 13px; }
    .wrap { max-width: 1200px; margin: 16px auto; padding: 0 16px; }
    .grid { display: grid; gap: 12px; }
    .grid.cards { grid-template-columns: repeat(4, minmax(0, 1fr)); }
    .card { background: white; border-radius: 12px; box-shadow: 0 6px 18px rgba(0,0,0,.06); padding: 14px; }
    .card h3 { margin: 0 0 8px; font-size: 13px; color: #111827; }
    .kpi { font-size: 22px; font-weight: 700; color: #111827; }
    .sub { font-size: 12px; color: #6b7280; margin-top: 6px; }
    .grid.main { grid-template-columns: 420px 1fr; align-items: start; }
    .row { display: flex; gap: 10px; flex-wrap: wrap; }
    select, input { padding: 8px 10px; border-radius: 10px; border: 1px solid #e5e7eb; background: white; }
    button { padding: 9px 12px; border-radius: 10px; border: 0; background: #111827; color: white; cursor: pointer; }
    button.secondary { background: #4f46e5; }
    table { width: 100%; border-collapse: collapse; font-size: 12px; }
    th, td { padding: 8px 8px; border-bottom: 1px solid #eef2f7; text-align: left; }
    th { color: #374151; font-weight: 600; background: #f9fafb; position: sticky; top: 0; }
    .pill { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; background: #eef2ff; color: #3730a3; }
    .muted { color: #6b7280; }
    .ok { color: #065f46; font-weight: 600; }
    .no { color: #9ca3af; }
    @media (max-width: 980px) {
      .grid.cards { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .grid.main { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
<header>
  <h1>ARGUS · Treasury / Macro Risk Monitoring Console</h1>
  <p>Inventory · Coverage · Single-series chart · Cross-country comparison</p>
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
      <div style="height: 8px;"></div>
      <canvas id="singleChart" height="130"></canvas>

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
      <canvas id="compareChart" height="130"></canvas>

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