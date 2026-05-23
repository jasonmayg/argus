from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from core.agents import build_alert_list, build_country_board
from core.guardian_loader import (
    _filter_guardian_alerts,
    _load_guardian_snapshot_from_gcs,
    _resolve_guardian_snapshot_path,
)

# ── ML News Scorer ────────────────────────────────────────────────────────────
_ML_DIR = Path(__file__).resolve().parent.parent / "ml"
sys.path.insert(0, str(_ML_DIR))

try:
    from predict import NewsScorer
    _scorer = NewsScorer(
        model_path=str(_ML_DIR / "models" / "news_scorer_model.pkl"),
        tfidf_path=str(_ML_DIR / "models" / "news_scorer_tfidf.pkl"),
    )
    _ML_AVAILABLE = True
    print("[newsalerts] ML scorer loaded successfully.")
except Exception as _e:
    print(f"[newsalerts] ML scorer not available, using rules fallback: {_e}")
    _scorer       = None
    _ML_AVAILABLE = False

# Maps frontend weight keys to ML model category strings
_WEIGHT_KEY_TO_CATEGORY: dict[str, str] = {
    "liquidity_risk":   "Liquidity",
    "regulatory_risk":  "Regulatory",
    "market_risk":      "Market Risk",
    "credit_risk":      "Credit Risk",
    "operational_risk": "Operational",
    "systematic_risk":  "Systematic Risk",
    "political_risk":   "Political Risk",
    "reputation_risk":  "Reputation Risk",
    "disaster_risk":    "Disaster Risk",
    "supply_risk":      "Supply Risk",
    "demand_risk":      "Demand Risk",
    "process_risk":     "Process Risk",
    "business_risk":    "Business Risk",
}

# ── Low-quality filter ────────────────────────────────────────────────────────
_LOW_QUALITY_SCORE_THRESHOLD = 5.0
_LOW_QUALITY_MIN_BODY_LEN    = 50

# ── Score amplification ───────────────────────────────────────────────────────
_AMPLIFY_CENTER = 5.0
_AMPLIFY_FACTOR = 2.5

# ── Keyword bonus — capped at ±1.0 to provide bounded score differentiation ──
# Purpose: produce score variation for title-only articles.
# The total keyword adjustment is capped at ±1.0 on the 0-10 scale to keep the
# heuristic bounded, but it still affects downstream 0-100 scores and can alter
# the final alert level near classification thresholds after scaling/modifiers.
_KEYWORD_ADJUSTMENTS: list[tuple[list[str], float]] = [
    # High-risk signals → small upward nudge
    (["terrorist", "terrorism", "terror"],  +0.4),
    (["cartel", "trafficking", "narco"],    +0.4),
    (["sanctions", "sanctioned"],           +0.3),
    (["hamas", "hezbollah", "hizballah"],   +0.4),
    (["fraud", "fraudulent"],               +0.3),
    (["ban", "banned", "bans"],             +0.2),
    (["fine", "fined", "fines"],            +0.2),
    (["misconduct", "malpractice"],         +0.3),
    (["disrupts", "dismantles", "seizes"],  +0.3),
    (["enforcement action"],                +0.2),
    (["criminal", "convicted", "prison"],   +0.3),
    (["money laundering", "laundering"],    +0.3),
    (["crisis", "collapse", "failure"],     +0.3),
    (["breach", "violation", "penalty"],    +0.2),
    # Low-risk signals → small downward nudge
    (["remarks", "commencement", "speech"],       -0.3),
    (["annual report", "quarterly report"],       -0.3),
    (["survey", "consultation", "questionnaire"], -0.2),
    (["holiday", "christmas", "thanksgiving"],    -0.5),
    (["memorial", "remembrance"],                 -0.3),
    (["routine", "scheduled"],                    -0.3),
    (["vision", "shared vision", "joint statement",
      "statement on", "frontier"],                -0.3),
    (["publication", "publishes", "published"],   -0.2),
]
# Hard cap: keyword adjustment cannot exceed ±1.0 on 0-10 scale
_MAX_KEYWORD_ADJUSTMENT = 1.0
# ─────────────────────────────────────────────────────────────────────────────


def _apply_keyword_bonus(news_score: float, title: str, body: str) -> float:
    """
    Apply small keyword-based score adjustments for title-only articles.

    Only applies when body text has no meaningful content beyond the title.
    Total adjustment is capped at ±1.0 to prevent risk level changes —
    the goal is score differentiation, not risk reclassification.
    """
    effective_body = body.strip()
    if len(effective_body) > len(title.strip()) + 20:
        return news_score  # body has real content, trust the model

    text = (title + " " + body).lower()
    adjustment = 0.0
    for keywords, delta in _KEYWORD_ADJUSTMENTS:
        if any(re.search(r'\b' + re.escape(kw) + r'\b', text) for kw in keywords):
            adjustment += delta

    # Hard cap to prevent risk level changes
    adjustment = max(-_MAX_KEYWORD_ADJUSTMENT, min(_MAX_KEYWORD_ADJUSTMENT, adjustment))
    return max(0.0, min(10.0, news_score + adjustment))


def _amplify_score(news_score: float) -> float:
    """
    Amplify score differences around the midpoint for better visual discrimination.
    factor=2.5 means a 0.4 raw difference becomes ~1.0 amplified difference.
    """
    amplified = _AMPLIFY_CENTER + (news_score - _AMPLIFY_CENTER) * _AMPLIFY_FACTOR
    return max(0.0, min(10.0, amplified))


def load_guardian_snapshot_for_newsalerts() -> tuple[dict[str, Any] | None, str]:
    """Load the active Guardian snapshot, preferring GCS when it contains scored alerts."""
    gcs_result = _load_guardian_snapshot_from_gcs()
    snapshot = None
    snapshot_ref = ""
    if gcs_result.get("ok"):
        snapshot = gcs_result.get("snapshot")
        snapshot_ref = str(gcs_result.get("snapshot_ref") or "")
        if snapshot:
            gcs_alerts = snapshot.get("alerts") or []
            gcs_triggered = len([a for a in gcs_alerts if float(a.get("score", 0)) > 0])
            if gcs_triggered == 0:
                snapshot = None
                snapshot_ref = ""

    if snapshot is None:
        snapshot_path = _resolve_guardian_snapshot_path()
        if not snapshot_path.exists():
            return None, ""
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        snapshot_ref = str(snapshot_path)
    return snapshot, snapshot_ref


def filter_news_alerts(alerts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply the same News Alerts filtering/dedup rules used by the dashboard."""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for alert in alerts:
        src   = str(alert.get("source") or "").lower()
        title = str(alert.get("title") or "").strip()
        if src in {"gcs_raw", "gcs_data"}:
            continue
        if title.startswith("US ") and " update:" in title.lower():
            continue
        key = title.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(alert)
    return out


def _is_low_quality(row: dict[str, Any], news_score: float) -> bool:
    """
    Return True if this alert is low-quality and should be filtered out.
    Low-quality = score below threshold AND body text too short to be meaningful.
    """
    if news_score >= _LOW_QUALITY_SCORE_THRESHOLD:
        return False
    body = str(
        row.get("body") or
        row.get("analysis_text") or
        row.get("summary") or
        ""
    ).strip()
    return len(body) < _LOW_QUALITY_MIN_BODY_LEN


def rescore_news_alerts(
    alerts: list[dict[str, Any]],
    risk_weights: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    """
    Recompute Guardian News Alert scores.

    Scoring pipeline:
      1. ML model predicts raw news_score (0-10)
      2. Keyword bonus applied for title-only articles (capped at ±1.0)
      3. Score amplified around midpoint for better visual discrimination
      4. Recency and country_weight applied to produce final 0-100 score

    Filtering:
      - Low-quality alerts (news_score < 5.0 AND body < 50 chars) are removed
    """
    risk_weights   = risk_weights or {}
    rescored: list[dict[str, Any]] = []
    filtered_count = 0

    for alert in alerts:
        row          = dict(alert)
        category_key = str(row.get("category_key") or "").strip()
        country      = str(row.get("country") or "").strip()

        try:
            recency = float(row.get("recency", 1.0) or 1.0)
        except Exception:
            recency = 1.0

        country_weight = 1.2 if country == "China" else 1.0
        persona_weight = float(risk_weights.get(category_key, 1.0))

        if _ML_AVAILABLE and _scorer is not None:
            ml_category = _WEIGHT_KEY_TO_CATEGORY.get(category_key, "General")
            title = str(row.get("title") or row.get("headline") or "")
            body  = str(
                row.get("body") or
                row.get("analysis_text") or
                row.get("summary") or
                title
            )
            try:
                # Step 1: ML prediction
                news_score = _scorer.predict_one(
                    title         = title,
                    analysis_text = body,
                    risk_category = ml_category,
                    user_weight   = persona_weight,
                )

                # Step 2: Keyword bonus (capped at ±1.0, differentiation only)
                news_score = _apply_keyword_bonus(news_score, title, body)

                # Step 3: Filter low-quality
                if _is_low_quality(row, news_score):
                    filtered_count += 1
                    continue

                # Step 4: Amplify + final score
                amplified = _amplify_score(news_score)
                score = max(0.0, min(100.0,
                    amplified * 10.0 * recency * country_weight
                ))

            except Exception as _predict_err:
                print(f"[newsalerts] ML predict failed for '{title[:40]}': {_predict_err}")
                try:
                    severity = float(row.get("severity", 0) or 0)
                except Exception:
                    severity = 0.0
                score = max(0.0, min(100.0,
                    100.0 * severity * recency * country_weight * persona_weight
                ))
        else:
            try:
                severity = float(row.get("severity", 0) or 0)
            except Exception:
                severity = 0.0
            score = max(0.0, min(100.0,
                100.0 * severity * recency * country_weight * persona_weight
            ))

        row["score"] = round(score, 2)
        rescored.append(row)

    if filtered_count:
        print(f"[newsalerts] Filtered {filtered_count} low-quality alerts")

    rescored.sort(key=lambda a: float(a.get("score", 0) or 0), reverse=True)
    return rescored


def build_rescored_newsalerts_payload(
    weights_cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build backend-computed News Alerts payload for the tuner."""
    weights_cfg  = weights_cfg or {}
    risk_weights = weights_cfg.get("risk_weights") or {}
    threshold    = float(weights_cfg.get("alert_threshold") or 40.0)

    snapshot, snapshot_ref = load_guardian_snapshot_for_newsalerts()
    if snapshot is None:
        return {"status": "missing", "message": "Guardian snapshot not found"}

    data_mode         = snapshot.get("data_mode")
    enforce_whitelist = not str(data_mode or "").startswith("live_gcs_raw")
    alerts            = _filter_guardian_alerts(
        snapshot.get("alerts"), enforce_whitelist=enforce_whitelist
    )
    rescored      = rescore_news_alerts(alerts, risk_weights=risk_weights)
    filtered      = filter_news_alerts(rescored)
    alert_rows    = build_alert_list(filtered, max_items=5000)
    country_board = build_country_board(filtered)

    high_risk_country_count = len([x for x in country_board if float(x.get("max_score", 0)) >= 70])
    at_risk_country_count   = len([x for x in country_board if float(x.get("max_score", 0)) > 0])
    high_risk_alert_count   = len([x for x in alert_rows    if float(x.get("score", 0)) >= 70])
    total_risk_alert_count  = len([x for x in alert_rows    if float(x.get("score", 0)) > 0])
    triggered_count         = len([x for x in alert_rows    if float(x.get("score", 0)) >= threshold])

    return {
        "status":        "success",
        "snapshot_path": snapshot_ref,
        "source":        "newsalerts_rescored",
        "meta": {
            "threshold":       threshold,
            "data_mode":       data_mode,
            "alert_count":     len(alert_rows),
            "triggered_count": triggered_count,
        },
        "executive_summary": {
            "high_risk_country_count": high_risk_country_count,
            "high_risk_alert_count":   high_risk_alert_count,
            "at_risk_country_count":   at_risk_country_count,
            "total_risk_alert_count":  total_risk_alert_count,
        },
        "country_rank_top5":    country_board[:5],
        "critical_alerts_top5": alert_rows[:5],
        "alerts":               alert_rows,
    }