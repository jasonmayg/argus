"""
ARGUS — Actions Routes
routes/actions_routes.py

Reads from shared_storage/latest_results.json → stages.actions_generation
which is already populated by the pipeline.
"""

import json
import logging
from pathlib import Path

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

actions_bp = Blueprint("actions", __name__)

SHARED_DIR    = Path(__file__).parent.parent / "shared_storage"
RESULTS_FILE  = SHARED_DIR / "latest_results.json"
ACTIONS_CACHE = SHARED_DIR / "latest_actions.json"


def _load_pipeline_actions() -> dict | None:
    try:
        if RESULTS_FILE.exists():
            with open(RESULTS_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            ag = d.get("stages", {}).get("actions_generation", {})
            if ag.get("status") == "success":
                return ag
    except Exception as e:
        logger.warning("Could not load pipeline actions: %s", e)
    return None


def _load_cache() -> dict | None:
    try:
        if ACTIONS_CACHE.exists():
            with open(ACTIONS_CACHE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.warning("Could not load actions cache: %s", e)
    return None


def _save_cache(data: dict) -> None:
    SHARED_DIR.mkdir(parents=True, exist_ok=True)
    with open(ACTIONS_CACHE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _normalise(raw: dict) -> dict:
    """
    Pipeline shape  →  frontend shape

    Pipeline:  country_groups[].category_cards[].action.{why, recommended_action, ...}
    Frontend:  actions[].{why_now, recommendation, ...}
    """
    actions = []
    for cg in raw.get("country_groups", []):
        country = cg.get("country", "Global")
        for card in cg.get("category_cards", []):
            act = card.get("action", {})
            actions.append({
                "country":          country,
                "category":         card.get("category", ""),
                "effective_score":  card.get("effective_score", 0),
                "severity":         card.get("severity", "WATCH"),
                "top_title":        card.get("top_alert_title", ""),
                "why_now":          act.get("why", ""),
                "recommendation":   act.get("recommended_action", ""),
                "expected_outcome": act.get("expected_outcome", ""),
                "pros":             act.get("pros", []),
                "cons":             act.get("cons", []),
                "alternatives":     act.get("alternatives", []),
                "alerts": [
                    {
                        "title":      a.get("title", ""),
                        "base_score": a.get("score", a.get("effective_score", 0)),
                        "weight":     a.get("weight_applied", 1.0),
                    }
                    for a in card.get("alerts", [])
                ],
            })

    return {
        "status":        "success",
        "posture":       raw.get("posture", "CONTAINED"),
        "summary":       raw.get("summary", ""),
        "generated_at":  raw.get("generated_at", ""),
        "persona":       raw.get("persona", ""),
        "custom_weights": raw.get("weights_used", False),
        "signal_count":  raw.get("total_alerts", len(actions)),
        "actions":       actions,
    }


@actions_bp.route("/api/actions", methods=["GET"])
def get_actions():
    # 1. Pipeline result (freshest — always prefer)
    pipeline = _load_pipeline_actions()
    if pipeline:
        return jsonify(_normalise(pipeline))

    # 2. Manual cache fallback
    cached = _load_cache()
    if cached:
        return jsonify({"status": "success", **cached})

    return jsonify({
        "status":  "empty",
        "actions": [],
        "summary": "",
        "message": "No recommendations yet. Run Daily Jobs first.",
    })


@actions_bp.route("/api/actions/generate", methods=["POST"])
def generate_actions():
    """Re-generate via AI, or fall back to pipeline data."""
    body        = request.get_json(silent=True) or {}
    persona_key = body.get("persona", "cash_manager")
    min_score   = float(body.get("min_score", 40))
    exclude     = body.get("exclude_categories", ["Weather / NOAA", "Disaster Risk"])
    sources_cfg = body.get("sources", {"guardian": True, "data_alerts": True, "matrix": True})

    pipeline = _load_pipeline_actions()

    try:
        result = _ai_generate(persona_key, min_score, exclude, sources_cfg, pipeline)
        _save_cache(result)
        return jsonify(result)
    except Exception as e:
        logger.warning("AI generation failed (%s) — returning pipeline data", e)
        if pipeline:
            normalised = _normalise(pipeline)
            _save_cache(normalised)
            return jsonify(normalised)
        return jsonify({"status": "error", "message": str(e), "actions": []}), 500


def _ai_generate(persona_key, min_score, exclude, sources_cfg, pipeline_raw):
    import os

    alerts = []
    if pipeline_raw:
        for cg in pipeline_raw.get("country_groups", []):
            for card in cg.get("category_cards", []):
                if card.get("effective_score", 0) < min_score:
                    continue
                cat = card.get("category", "")
                if any(ex.lower() in cat.lower() for ex in exclude):
                    continue
                alerts.append({
                    "country":  cg.get("country", "Global"),
                    "category": cat,
                    "score":    card.get("effective_score", 0),
                    "title":    card.get("top_alert_title", ""),
                })

    if not alerts:
        return {
            "status": "success", "posture": "CONTAINED",
            "summary": "No signals above the selected threshold.",
            "generated_at": _now(), "persona": persona_key, "actions": [],
        }

    prompt = (
        f"You are a treasury risk AI for a {persona_key.replace('_',' ')}.\n"
        "Generate recommendations. Respond ONLY with JSON:\n"
        '{"summary":"...","posture":"CRITICAL|ELEVATED|MODERATE|CONTAINED","actions":[{'
        '"country","category","effective_score","top_title","why_now","recommendation",'
        '"expected_outcome","pros":[],"cons":[],"alternatives":[],"alerts":[]}]}\n\n'
        "Signals:\n" +
        "\n".join(f"  {a['country']} / {a['category']} ({a['score']:.0f}): {a['title']}"
                  for a in alerts)
    )

    openai_key = os.environ.get("OPENAI_API_KEY", "")
    if openai_key:
        import openai
        resp = openai.OpenAI(api_key=openai_key).chat.completions.create(
            model="gpt-4o",
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        d = json.loads(resp.choices[0].message.content or "{}")
        return {**d, "status": "success", "generated_at": _now(), "persona": persona_key}

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if anthropic_key:
        import anthropic
        msg = anthropic.Anthropic(api_key=anthropic_key).messages.create(
            model="claude-opus-4-6", max_tokens=4096,
            messages=[{"role": "user", "content": prompt + "\n\nRespond ONLY with JSON."}],
        )
        raw = msg.content[0].text.strip().lstrip("```json").rstrip("```").strip()
        d = json.loads(raw)
        return {**d, "status": "success", "generated_at": _now(), "persona": persona_key}

    raise RuntimeError("No AI API keys (OPENAI_API_KEY / ANTHROPIC_API_KEY)")


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")