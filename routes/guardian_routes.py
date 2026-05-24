"""
Guardian & Persona route blueprint.
Handles:
  GET  /api/guardian/dashboard
  GET  /api/personas
  GET  /api/persona/dashboard
  POST /api/save_profile
  GET  /api/list_profiles
  POST /api/load_profile
"""

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path

from flask import Blueprint, jsonify, request

# Persona defaults & profile helpers
from personas.persona_loader import (
    load_all_defaults,
    load_defaults,
    load_all_region_defaults,
    merge_weights,
)

from core.agents import (
    PERSONA_REGISTRY,
    build_alert_list,
    build_country_board,
    load_monitored_countries,
    build_fx_summary,
    build_cash_manager_dashboard,
    build_generic_persona_dashboard,
)
from core.newsalerts import build_rescored_newsalerts_payload, rescore_news_alerts
from core.guardian_loader import (
    GUARDIAN_SOURCE_WHITELIST,
    _load_guardian_snapshot_from_gcs,
    _resolve_guardian_snapshot_path,
    _filter_guardian_alerts,
    _build_daily_warnings,
    _get_google_downside_snapshot,
    _find_previous_snapshot_path,
    _snapshot_and_alert_rows,
)
from core.json_utils import sanitize_for_json

guardian_bp = Blueprint("guardian", __name__)


@guardian_bp.route("/api/guardian/dashboard")
def get_guardian_dashboard():
    """Get Guardian multi-country dashboard data for the ARGUS page."""
    gcs_result = _load_guardian_snapshot_from_gcs()
    gcs_diag = {
        "ok":           bool(gcs_result.get("ok")),
        "message":      str(gcs_result.get("message") or ""),
        "bucket":       str(gcs_result.get("bucket") or ""),
        "prefix":       str(gcs_result.get("prefix") or ""),
        "snapshot_ref": str(gcs_result.get("snapshot_ref") or ""),
    }
    snapshot          = None
    snapshot_ref      = ""
    prev_snapshot_obj = None
    data_source       = "local"
    # True when the request served alerts straight from the
    # precomputed score cache (i.e. paid 0 ML cost). Surfaced in the
    # response meta so operators can monitor cache hit rate.
    used_score_cache = False

    if gcs_result.get("ok"):
        snapshot          = gcs_result.get("snapshot")
        snapshot_ref      = str(gcs_result.get("snapshot_ref") or "")
        prev_snapshot_obj = gcs_result.get("previous_snapshot")
        data_source       = "gcs"

        # Quality check: if GCS snapshot has no scored alerts, prefer local data
        if snapshot:
            gcs_alerts    = snapshot.get("alerts") or []
            gcs_triggered = len([
                a for a in gcs_alerts if float(a.get("score", 0)) > 0
            ])
            if gcs_triggered == 0:
                snapshot          = None
                snapshot_ref      = ""
                prev_snapshot_obj = None
                data_source       = "local"

    if snapshot is None:
        snapshot_path = _resolve_guardian_snapshot_path()
        if not snapshot_path.exists():
            return jsonify({
                "status":        "missing",
                "message":       "Guardian snapshot not found in GCS or local file",
                "snapshot_path": str(snapshot_path),
                "gcs_status":    gcs_diag,
            }), 404

        # ── Precomputed score cache fast path ─────────────────────
        # The bridge (`scripts/refresh_local_snapshot_from_gcs.py`)
        # writes a sibling `*_scored.json` after each snapshot refresh
        # that contains the already-rescored alerts. When its
        # `_score_meta.source_mtime` matches the live snapshot's
        # mtime, we can skip the ~30s live rescore entirely.
        try:
            from core.newsalerts import load_scored_snapshot_if_fresh
            cached = load_scored_snapshot_if_fresh(snapshot_path)
        except Exception:
            cached = None    # cache is a perf hint — never block the
                             # request if the helper itself errors.

        if cached is not None:
            snapshot          = cached
            snapshot_ref      = str(snapshot_path)
            used_score_cache  = True
            print(f"[dashboard] Used precomputed score cache "
                  f"({len(cached.get('alerts') or [])} alerts)")
        else:
            try:
                snapshot     = json.loads(snapshot_path.read_text(encoding="utf-8"))
                snapshot_ref = str(snapshot_path)
            except Exception as e:
                return jsonify({
                    "status":        "error",
                    "message":       f"Failed to parse local snapshot: {str(e)}",
                    "snapshot_path": str(snapshot_path),
                    "gcs_status":    gcs_diag,
                }), 500

    data_mode         = snapshot.get("data_mode")
    enforce_whitelist = not str(data_mode or "").startswith("live_gcs_raw")
    alerts            = _filter_guardian_alerts(
        snapshot.get("alerts"), enforce_whitelist=enforce_whitelist
    )

    # ── Model A: rescore all alerts with ML News Scorer ───────────────────────
    # Uses default weights (no user tuning at dashboard load time).
    # When the user drags the Weights Tuner, /api/guardian/news-alerts/rescore
    # re-runs with their custom weights on top of this baseline.
    #
    # Skipped entirely when `used_score_cache=True` — the cached alerts
    # were already rescored at bridge-write time with the same default
    # weights, so re-running here would just burn ~30s on identical
    # output.
    if not used_score_cache:
        try:
            alerts = rescore_news_alerts(alerts, risk_weights={})
            print(f"[dashboard] Model A rescored {len(alerts)} alerts successfully.")
        except Exception as _ml_err:
            print(f"[dashboard] ML rescore failed, using snapshot scores: {_ml_err}")
    # ─────────────────────────────────────────────────────────────────────────

    monitored_countries = load_monitored_countries()
    monitored_set       = set(monitored_countries)

    alert_rows_all = build_alert_list(alerts, max_items=5000)
    scoped_alerts  = [a for a in alerts if str(a.get("country") or "") in monitored_set]
    alert_rows     = build_alert_list(
        scoped_alerts if scoped_alerts else alerts, max_items=5000
    )

    country_board = build_country_board(scoped_alerts if scoped_alerts else alerts)
    availability  = snapshot.get("data_avaliability") or snapshot.get("data_availability") or []
    availability  = [
        x for x in availability
        if str(x.get("source") or "").strip() in GUARDIAN_SOURCE_WHITELIST
    ]
    enabled_sources         = [x for x in availability if x.get("enabled")]
    healthy_enabled_sources = [x for x in enabled_sources if x.get("healthy")]
    threshold               = float(snapshot.get("threshold") or 40)
    daily_comparison        = _build_daily_warnings(
        alert_rows, snapshot.get("raw_dt_segments") or [], threshold=threshold
    )
    triggered_count = len([x for x in alert_rows if float(x.get("score", 0)) >= threshold])

    if not availability and str(data_mode or "").startswith("live_gcs_raw"):
        unique_sources = sorted({
            str(x.get("source") or "").strip()
            for x in alert_rows_all
            if str(x.get("source") or "").strip()
        })
        enabled_sources         = [{"source": s, "enabled": True, "healthy": True} for s in unique_sources]
        healthy_enabled_sources = enabled_sources[:]

    fx_input_rows = alert_rows
    latest_dt     = str(daily_comparison.get("latest_dt") or "").strip()
    if str(data_mode or "").startswith("live_gcs_raw") and latest_dt:
        latest_rows = [x for x in alert_rows if str(x.get("raw_dt") or "") == latest_dt]
        if latest_rows:
            fx_input_rows = latest_rows
    fx_summary           = build_fx_summary(fx_input_rows, monitored_countries)
    fx_summary["as_of_dt"] = latest_dt or None

    high_risk_country_count = len([x for x in country_board if float(x.get("max_score", 0)) >= 70])
    at_risk_country_count   = len([x for x in country_board if float(x.get("max_score", 0)) > 0])
    high_risk_alert_count   = len([x for x in alert_rows    if float(x.get("score", 0)) >= 70])
    total_risk_alert_count  = len([x for x in alert_rows    if float(x.get("score", 0)) > 0])

    prev_triggered               = None
    current_triggered_for_trend  = triggered_count
    is_raw_mode                  = str(data_mode or "").startswith("live_gcs_raw")

    if is_raw_mode:
        prev_triggered              = daily_comparison.get("previous_triggered")
        current_triggered_for_trend = int(daily_comparison.get("latest_triggered") or 0)

    if isinstance(prev_snapshot_obj, dict):
        try:
            prev_mode    = prev_snapshot_obj.get("data_mode")
            prev_enforce = not str(prev_mode or "").startswith("live_gcs_raw")
            prev_alerts  = _filter_guardian_alerts(
                prev_snapshot_obj.get("alerts"), enforce_whitelist=prev_enforce
            )
            prev_rows       = build_alert_list(prev_alerts, max_items=5000)
            prev_triggered  = len([x for x in prev_rows if float(x.get("score", 0)) >= threshold])
            current_triggered_for_trend = triggered_count
        except Exception:
            prev_triggered = None
    elif data_source == "local":
        prev_path = _find_previous_snapshot_path(Path(snapshot_ref))
        if prev_path and prev_path.exists():
            try:
                prev_snapshot = json.loads(prev_path.read_text(encoding="utf-8"))
                prev_mode     = prev_snapshot.get("data_mode")
                prev_enforce  = not str(prev_mode or "").startswith("live_gcs_raw")
                prev_alerts   = _filter_guardian_alerts(
                    prev_snapshot.get("alerts"), enforce_whitelist=prev_enforce
                )
                prev_rows      = build_alert_list(prev_alerts, max_items=5000)
                prev_triggered = len([x for x in prev_rows if float(x.get("score", 0)) >= threshold])
                current_triggered_for_trend = triggered_count
            except Exception:
                prev_triggered = None

    trend_basis    = "triggered"
    trend_current  = current_triggered_for_trend
    trend_previous = prev_triggered
    trend_delta    = None if trend_previous is None else (trend_current - trend_previous)

    if is_raw_mode:
        trend_basis    = "events"
        trend_current  = int(daily_comparison.get("latest_events") or 0)
        prev_val       = daily_comparison.get("previous_events")
        trend_previous = None if prev_val is None else int(prev_val)
        trend_delta    = None if trend_previous is None else (trend_current - trend_previous)

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
        as_of      = str(google_downside.get("as_of_date") or "").strip()
        stale_days = None
        if as_of:
            try:
                as_dt      = datetime.fromisoformat(as_of.replace("Z", "+00:00")).date()
                stale_days = (datetime.now().date() - as_dt).days
            except Exception:
                stale_days = None
        google_downside["staleness_days"] = stale_days
        google_downside["is_stale"]       = bool(stale_days is not None and stale_days > 7)

    dashboard_data = {
        "status":        "success",
        "snapshot_path": snapshot_ref,
        "meta": {
            "generated_at_local":        snapshot.get("generated_at_local"),
            "generated_at_utc":          snapshot.get("generated_at_utc"),
            "timezone":                  snapshot.get("timezone"),
            "persona":                   snapshot.get("persona"),
            "threshold":                 snapshot.get("threshold"),
            "data_mode":                 data_mode,
            "event_count":               snapshot.get("event_count", 0),
            "alert_count":               len(alert_rows),
            "triggered_count":           triggered_count,
            "enabled_source_count":      len(enabled_sources),
            "healthy_enabled_source_count": len(healthy_enabled_sources),
            "data_source":               data_source,
            "raw_dt_segments":           snapshot.get("raw_dt_segments") or [],
            "scope":                     "11-country",
            "global_event_count":        len(alert_rows_all),
            "ml_scoring":                True,   # indicates Model A is active
            # `used_score_cache=True` means the dashboard served alerts
            # from the precomputed `*_scored.json` sibling file rather
            # than running rescore_news_alerts live. Operators can grep
            # logs for the rate.
            "used_score_cache":          used_score_cache,
        },
        "gcs_status": gcs_diag,
        "executive_summary": {
            "high_risk_country_count": high_risk_country_count,
            "high_risk_alert_count":   high_risk_alert_count,
            "at_risk_country_count":   at_risk_country_count,
            "total_risk_alert_count":  total_risk_alert_count,
            "trend": {
                "basis":               trend_basis,
                "direction":           trend_direction,
                "delta":               trend_delta,
                "current_triggered":   trend_current,
                "previous_triggered":  trend_previous,
            },
        },
        "country_rank_top5":   country_board[:5],
        "critical_alerts_top5": alert_rows[:5],
        "fx_summary":          fx_summary,
        "daily_comparison":    daily_comparison,
        "google_downside":     google_downside,
        "country_board":       country_board,
        "alerts":              alert_rows,
    }
    return jsonify(sanitize_for_json(dashboard_data))


@guardian_bp.route("/api/guardian/news-alerts/rescore", methods=["POST"])
def rescore_guardian_newsalerts():
    """Server-side rescore of Guardian News Alerts using current tuner weights."""
    body         = request.get_json(force=True, silent=True) or {}
    payload      = build_rescored_newsalerts_payload(body.get("weights") or {})
    status_code  = (
        200 if payload.get("status") == "success"
        else 404 if payload.get("status") == "missing"
        else 500
    )
    return jsonify(sanitize_for_json(payload)), status_code


@guardian_bp.route("/api/personas")
def get_personas():
    """Return AI-baseline persona configs with per-region defaults."""
    defaults     = load_all_defaults()
    personas_out = []
    for key, cfg in PERSONA_REGISTRY.items():
        entry = {"key": key, "label": cfg["label"], "summary": cfg["summary"]}
        d     = defaults.get(key, {})
        entry["alert_threshold"] = d.get("alert_threshold")
        entry["risk_weights"]    = d.get("risk_weights", {})
        entry["region_defaults"] = load_all_region_defaults(key)
        personas_out.append(entry)
    return jsonify({
        "status":          "success",
        "personas":        personas_out,
        "default_persona": "cash_manager",
    })


@guardian_bp.route("/api/persona/dashboard")
def get_persona_dashboard():
    """Return role-based dashboard content for the selected persona."""
    persona = str(request.args.get("persona") or "cash_manager").strip().lower()
    if persona not in PERSONA_REGISTRY:
        persona = "cash_manager"

    snapshot, alert_rows, snapshot_ref = _snapshot_and_alert_rows()
    if snapshot is None:
        return jsonify({
            "status":  "missing",
            "message": "Persona dashboard unavailable because Guardian snapshot could not be loaded.",
            "persona": persona,
        }), 404

    if persona == "cash_manager":
        payload = build_cash_manager_dashboard(alert_rows, snapshot)
    else:
        payload = build_generic_persona_dashboard(persona, alert_rows, snapshot)

    payload["status"]        = "success"
    payload["snapshot_path"] = snapshot_ref
    return jsonify(sanitize_for_json(payload))


# ── Profiles ──────────────────────────────────────────────────────────────────

_PROFILES_DIR = Path("personas/profiles")
_RESULTS_PATH = Path("shared_storage/latest_results.json")
_SAFE_RE      = re.compile(r"[^a-zA-Z0-9_\-]")


@guardian_bp.route("/api/save_profile", methods=["POST"])
def save_profile():
    """Save a named weight profile to personas/profiles/<safe_name>.json."""
    body = request.get_json(force=True, silent=True) or {}
    name = str(body.get("name") or "").strip()
    if not name:
        return jsonify({"status": "error", "message": "Profile name is required"}), 400

    safe = _SAFE_RE.sub("_", name)[:64] or "profile"
    _PROFILES_DIR.mkdir(exist_ok=True)

    profile = {
        "name":        name,
        "persona_key": str(body.get("persona_key") or "cash_manager"),
        "saved_at":    datetime.now().isoformat(timespec="seconds"),
        "weights":     body.get("weights") or {},
    }

    dest = _PROFILES_DIR / f"{safe}.json"
    dest.write_text(json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")

    return jsonify({"status": "success", "filename": dest.name, "profile": profile})


@guardian_bp.route("/api/list_profiles")
def list_profiles():
    """Return metadata for every saved profile, newest first."""
    _PROFILES_DIR.mkdir(exist_ok=True)
    profiles = []
    for f in sorted(_PROFILES_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        profiles.append({
            "filename":    f.name,
            "name":        data.get("name", f.stem),
            "persona_key": data.get("persona_key", ""),
            "saved_at":    data.get("saved_at", ""),
        })
    return jsonify({"status": "success", "profiles": profiles})


@guardian_bp.route("/api/load_profile", methods=["POST"])
def load_profile():
    """Load a saved profile, apply its weights, then re-run AnalysisAgent."""
    body     = request.get_json(force=True, silent=True) or {}
    filename = str(body.get("filename") or "").strip()

    if not filename or "/" in filename or "\\" in filename or not filename.endswith(".json"):
        return jsonify({"status": "error", "message": "Invalid filename"}), 400

    path = _PROFILES_DIR / filename
    if not path.exists():
        return jsonify({"status": "error", "message": "Profile not found"}), 404

    try:
        profile = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return jsonify({"status": "error", "message": f"Could not read profile: {exc}"}), 500

    persona_key     = str(profile.get("persona_key") or "cash_manager")
    profile_weights = profile.get("weights") or {}
    merged_cfg      = merge_weights(load_defaults(persona_key), profile_weights)

    rescored = None
    if _RESULTS_PATH.exists():
        try:
            results    = json.loads(_RESULTS_PATH.read_text(encoding="utf-8"))
            classified = (
                results.get("stages", {})
                       .get("risk_classification", {})
                       .get("data") or []
            )
            if classified:
                from core.agents import AnalysisAgent
                analyzer = AnalysisAgent(persona=persona_key, weights=merged_cfg)
                loop     = asyncio.new_event_loop()
                try:
                    result = loop.run_until_complete(
                        analyzer.execute(classified_news=classified)
                    )
                finally:
                    loop.close()
                if result.get("status") == "success":
                    rescored = result["analysis"]
        except Exception as exc:
            print(f"[load_profile] Re-score warning: {exc}")

    return jsonify(sanitize_for_json({
        "status":   "success",
        "profile":  profile,
        "rescored": rescored,
    }))