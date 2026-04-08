"""
routes/cash_routes.py

Cash position endpoints:
  GET /api/cash/positions  — Current positions from cash_positions.xlsx (Sheet: positions)
  GET /api/cash/history    — Historical monthly data (Sheet: history)
  GET /api/cash/fx         — FX rates reference (Sheet: fx_rates)
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import Blueprint, jsonify, request

try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False

cash_bp = Blueprint("cash_routes", __name__)

# ── Config ────────────────────────────────────────────────
CASH_FILE = Path(os.getenv("ARGUS_CASH_FILE", "data/cash_positions.xlsx"))

# Simple cache
_cache: Dict[str, Any] = {"ts": 0, "positions": None, "history": None, "fx": None}
CACHE_TTL = int(os.getenv("CASH_CACHE_TTL", "300"))  # 5 min default


def _cache_valid() -> bool:
    return time.time() - _cache["ts"] < CACHE_TTL and _cache["positions"] is not None


def _load_excel() -> None:
    """Load all sheets from Excel into cache."""
    if not HAS_PANDAS:
        raise RuntimeError("pandas is required: pip install pandas openpyxl")
    if not CASH_FILE.exists():
        raise FileNotFoundError(f"Cash file not found: {CASH_FILE}")

    sheets = pd.read_excel(str(CASH_FILE), sheet_name=None, engine="openpyxl")

    # ── positions sheet ──────────────────────────────────
    pos_df = sheets.get("positions", pd.DataFrame())
    # Skip title/subtitle rows — find the real header row
    # Header is the row containing "Country"
    header_row = None
    for i, row in pos_df.iterrows():
        if "Country" in str(row.values):
            header_row = i
            break

    if header_row is not None:
        pos_df.columns = pos_df.iloc[header_row]
        pos_df = pos_df.iloc[header_row + 1:].reset_index(drop=True)

    # Clean up: drop rows where Country is NaN or "TOTAL"
    if "Country" in pos_df.columns:
        pos_df = pos_df[
            pos_df["Country"].notna() &
            ~pos_df["Country"].astype(str).str.contains("TOTAL", case=False)
        ].reset_index(drop=True)

    positions = []
    for _, row in pos_df.iterrows():
        country = str(row.get("Country", "")).strip()
        if not country or country == "nan":
            continue

        def safe_float(val, default=0.0):
            try:
                return float(val)
            except (TypeError, ValueError):
                return default

        def safe_str(val, default="—"):
            s = str(val).strip()
            return s if s and s != "nan" else default

        usd_equiv = safe_float(row.get("USD Equivalent", row.get("G", 0)))
        local_bal = safe_float(row.get("Local Balance", row.get("F", 0)))
        fx_rate   = safe_float(row.get("FX Rate\n(to USD)", row.get("E", 1)), 1.0)
        change_30d = safe_float(row.get("30-Day Change", row.get("I", 0)))
        risk      = safe_str(row.get("Risk Level", row.get("K", "Low")), "Low")
        currency  = safe_str(row.get("Currency", row.get("D", "")))
        entity    = safe_str(row.get("Legal Entity", row.get("B", "")))
        region    = safe_str(row.get("Region", row.get("C", "")))
        ytd       = safe_float(row.get("YTD Change", row.get("L", 0)))
        avg_90d   = safe_float(row.get("Avg 90-Day USD", row.get("M", 0)))
        notes     = safe_str(row.get("Notes", row.get("N", "")))

        positions.append({
            "country":      country,
            "entity":       entity,
            "region":       region,
            "currency":     currency,
            "fx_rate":      round(fx_rate, 5),
            "local_balance": local_bal,
            "usd_equivalent": usd_equiv,
            "change_30d_pct": round(change_30d * 100, 2) if abs(change_30d) < 1 else round(change_30d, 2),
            "risk_level":   risk,
            "ytd_change_pct": round(ytd * 100, 2) if abs(ytd) < 1 else round(ytd, 2),
            "avg_90d_usd":  avg_90d,
            "notes":        notes,
        })

    # ── history sheet ────────────────────────────────────
    hist_df = sheets.get("history", pd.DataFrame())
    # Find header row with "Snapshot Date"
    header_row = None
    for i, row in hist_df.iterrows():
        if any("Snapshot" in str(v) or "Country" in str(v) for v in row.values):
            header_row = i
            break

    if header_row is not None:
        hist_df.columns = hist_df.iloc[header_row]
        hist_df = hist_df.iloc[header_row + 1:].reset_index(drop=True)

    history = []
    for _, row in hist_df.iterrows():
        country = str(row.get("Country", "")).strip()
        if not country or country == "nan":
            continue

        snap_date = row.get("Snapshot Date", "")
        if hasattr(snap_date, "strftime"):
            snap_date = snap_date.strftime("%Y-%m-%d")
        else:
            snap_date = str(snap_date)[:10]

        def safe_float(val, default=0.0):
            try: return float(val)
            except: return default

        mom = safe_float(row.get("MoM Change %", row.get("H", 0)))

        history.append({
            "date":          snap_date,
            "country":       country,
            "entity":        str(row.get("Legal Entity", row.get("C", ""))).strip(),
            "currency":      str(row.get("Currency", row.get("D", ""))).strip(),
            "fx_rate":       round(safe_float(row.get("FX Rate", row.get("E", 1)), 1), 5),
            "local_balance": safe_float(row.get("Local Balance", row.get("F", 0))),
            "usd_equivalent":safe_float(row.get("USD Equivalent", row.get("G", 0))),
            "mom_change_pct":round(mom * 100, 2) if abs(mom) < 1 else round(mom, 2),
            "risk_level":    str(row.get("Risk Level", row.get("I", "Low"))).strip(),
            "region":        str(row.get("Region", row.get("J", ""))).strip(),
        })

    # ── fx_rates sheet ───────────────────────────────────
    fx_df = sheets.get("fx_rates", pd.DataFrame())
    header_row = None
    for i, row in fx_df.iterrows():
        if any("Currency" in str(v) or "Rate" in str(v) for v in row.values):
            header_row = i
            break

    fx_rates = []
    if header_row is not None:
        fx_df.columns = fx_df.iloc[header_row]
        fx_df = fx_df.iloc[header_row + 1:].reset_index(drop=True)
        for _, row in fx_df.iterrows():
            pair = str(row.get("Currency Pair", row.get("A", ""))).strip()
            if not pair or pair == "nan":
                continue
            try:
                rate = float(row.get("Rate (to USD)", row.get("B", 0)))
            except:
                rate = 0.0
            fx_rates.append({
                "pair":    pair,
                "rate":    round(rate, 5),
                "updated": str(row.get("Last Updated", row.get("C", ""))).strip(),
                "source":  str(row.get("Source", row.get("D", ""))).strip(),
            })

    _cache["positions"] = positions
    _cache["history"]   = history
    _cache["fx"]        = fx_rates
    _cache["ts"]        = time.time()


def _get_positions() -> List[Dict]:
    if not _cache_valid():
        _load_excel()
    return _cache["positions"] or []


def _get_history() -> List[Dict]:
    if not _cache_valid():
        _load_excel()
    return _cache["history"] or []


def _get_fx() -> List[Dict]:
    if not _cache_valid():
        _load_excel()
    return _cache["fx"] or []


# ── Routes ────────────────────────────────────────────────

@cash_bp.get("/api/cash/positions")
def api_cash_positions():
    try:
        positions = _get_positions()

        total_usd = sum(p["usd_equivalent"] for p in positions)

        # Compute % of total
        for p in positions:
            p["pct_of_total"] = round(p["usd_equivalent"] / total_usd * 100, 2) if total_usd else 0

        # Regional subtotals
        regions: Dict[str, float] = {}
        for p in positions:
            regions[p["region"]] = regions.get(p["region"], 0) + p["usd_equivalent"]

        high_risk_usd = sum(
            p["usd_equivalent"] for p in positions if p["risk_level"] == "High"
        )

        return jsonify({
            "status":        "success",
            "as_of":         datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
            "source":        str(CASH_FILE),
            "total_usd":     total_usd,
            "entity_count":  len(positions),
            "high_risk_usd": high_risk_usd,
            "regional_totals": regions,
            "positions":     positions,
        })

    except FileNotFoundError as e:
        return jsonify({"status": "error", "message": str(e)}), 404
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cash_bp.get("/api/cash/history")
def api_cash_history():
    """
    Optional filters:
      ?country=Japan
      ?from=2026-01-01&to=2026-04-01
    """
    try:
        history = _get_history()

        country_filter = (request.args.get("country") or "").strip()
        from_date = (request.args.get("from") or "").strip()
        to_date   = (request.args.get("to")   or "").strip()

        if country_filter:
            history = [h for h in history if h["country"].lower() == country_filter.lower()]
        if from_date:
            history = [h for h in history if h["date"] >= from_date]
        if to_date:
            history = [h for h in history if h["date"] <= to_date]

        # Sort by date asc
        history = sorted(history, key=lambda x: x["date"])

        return jsonify({
            "status":  "success",
            "count":   len(history),
            "filters": {
                "country": country_filter or None,
                "from":    from_date or None,
                "to":      to_date   or None,
            },
            "history": history,
        })

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cash_bp.get("/api/cash/fx")
def api_cash_fx():
    try:
        return jsonify({"status": "success", "fx_rates": _get_fx()})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cash_bp.post("/api/cash/cache/invalidate")
def api_cash_cache_invalidate():
    """Call this after updating the Excel file."""
    _cache["ts"] = 0
    return jsonify({"status": "ok", "message": "Cash cache invalidated."})