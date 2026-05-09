"""
ARGUS — Actions Routes
routes/actions_routes.py

Auto-regeneration logic:
  - GET /api/actions checks if cached data uses old schema (alternatives as strings)
  - If old schema detected, automatically triggers _ai_generate() for fresh SWOT
  - This ensures Daily Jobs always produces unique per-option SWOT analysis
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


def _needs_regeneration(pipeline: dict, cached: dict | None) -> bool:
    """
    Return True if we should call _ai_generate() to produce fresh SWOT data.

    Checks:
    1. No cache at all → regenerate
    2. Cache has no actions → regenerate
    3. alternatives[0] is a string (old schema) → regenerate
    4. No recommendation_pros field (old schema) → regenerate
    5. Pipeline generated_at is newer than cache → regenerate
    """
    if not cached:
        return True

    actions = cached.get("actions", [])
    if not actions:
        return True

    first = actions[0]

    # Check schema version: alternatives should be list of dicts
    alts = first.get("alternatives", [])
    if not alts:
        return True
    if isinstance(alts[0], str):
        logger.info("Cache uses old schema (string alternatives) — regenerating")
        return True

    # Check for new per-option SWOT fields
    if "recommendation_pros" not in first:
        logger.info("Cache missing recommendation_pros — regenerating")
        return True

    # Check if pipeline is newer
    pipeline_time = pipeline.get("generated_at", "")
    cached_time   = cached.get("generated_at", "")
    if pipeline_time and cached_time and pipeline_time > cached_time:
        logger.info("Pipeline is newer than cache — regenerating")
        return True

    return False


def _normalise(raw: dict) -> dict:
    """Pipeline shape → frontend shape (fallback when AI fails)."""
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
        "status":         "success",
        "posture":        raw.get("posture", "CONTAINED"),
        "summary":        raw.get("summary", ""),
        "generated_at":   raw.get("generated_at", ""),
        "persona":        raw.get("persona", ""),
        "custom_weights": raw.get("weights_used", False),
        "signal_count":   raw.get("total_alerts", len(actions)),
        "actions":        actions,
    }


@actions_bp.route("/api/actions", methods=["GET"])
def get_actions():
    pipeline = _load_pipeline_actions()
    cached   = _load_cache()

    if pipeline:
        if _needs_regeneration(pipeline, cached):
            logger.info("Auto-triggering AI regeneration for Decision Support")
            try:
                persona_key = pipeline.get("persona", "cash_manager") or "cash_manager"
                result = _ai_generate(
                    persona_key  = persona_key,
                    min_score    = 40,
                    exclude      = ["Weather / NOAA", "Disaster Risk"],
                    sources_cfg  = {"guardian": True, "data_alerts": True, "matrix": True},
                    pipeline_raw = pipeline,
                )
                _save_cache(result)
                return jsonify(result)
            except Exception as e:
                logger.warning("Auto AI regeneration failed (%s) — normalising pipeline", e)
                normalised = _normalise(pipeline)
                _save_cache(normalised)
                return jsonify(normalised)

        # Cache is up to date and uses new schema
        return jsonify({"status": "success", **cached})

    # No pipeline — return cache if available
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
    """Re-generate via AI on demand (user clicks Re-generate button)."""
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
            "status":       "success",
            "posture":      "CONTAINED",
            "summary":      "No signals above the selected threshold.",
            "generated_at": _now(),
            "persona":      persona_key,
            "actions":      [],
        }

    signals_text = "\n".join(
        f"  - [{a['country']}] {a['category']} (score {a['score']:.0f}): \"{a['title']}\""
        for a in alerts
    )

    prompt = f"""You are a treasury risk advisor for a {persona_key.replace('_', ' ')}.

For each signal below, generate one action with a primary recommendation and two
alternative options. CRITICAL: each option must have its own unique SWOT analysis
that specifically addresses that option's approach — never reuse the same pros/cons
across options.

Respond ONLY with valid JSON in this exact schema:
{{
  "summary": "2-sentence executive summary of overall risk posture",
  "posture": "CRITICAL|ELEVATED|MODERATE|CONTAINED",
  "actions": [
    {{
      "country": "country name",
      "category": "risk category",
      "effective_score": 0,
      "top_title": "signal headline",
      "why_now": "1 sentence explaining why this specific signal requires action today — must reference the actual event or data point from the signal title",
      "recommendation": "primary recommended action text",
      "recommendation_pros": [
        "specific strength of THIS primary action tied to the signal",
        "another strength specific to this approach"
      ],
      "recommendation_cons": [
        "specific weakness or risk of THIS primary action",
        "another constraint specific to this approach"
      ],
      "recommendation_outcome": "concrete measurable outcome if primary action is taken (e.g. reduces CNY exposure by 20%, ensures compliance by Q3)",
      "alternatives": [
        {{
          "action": "first alternative — genuinely different strategy from the primary",
          "pros": [
            "specific strength of THIS alternative that differs from primary pros",
            "another strength unique to this alternative approach"
          ],
          "cons": [
            "specific weakness of THIS alternative that differs from primary cons",
            "another constraint unique to this alternative"
          ],
          "outcome": "concrete outcome specific to this alternative approach"
        }},
        {{
          "action": "second alternative — another genuinely different strategy",
          "pros": [
            "specific strength of THIS second alternative",
            "another strength unique to this approach"
          ],
          "cons": [
            "specific weakness of THIS second alternative",
            "another constraint unique to this approach"
          ],
          "outcome": "concrete outcome specific to this second alternative"
        }}
      ],
      "alerts": []
    }}
  ]
}}

Signals to analyse:
{signals_text}

Critical rules:
- PRIMARY and each ALTERNATIVE must have COMPLETELY DIFFERENT pros/cons — they are different strategies
- Example: if primary is "hedge with forward contracts", alternatives could be "increase cash buffer" and "reduce exposure limit" — each has a different risk/benefit profile
- why_now must quote or directly reference the actual signal title
- All outcomes must be measurable (percentages, deadlines, quantities)
- NEVER repeat the same pros/cons across options
- NEVER use generic text like "Proactive risk management", "Execution cost", "Mitigates identified risk"
- Generate exactly one action object per signal listed above
"""

    openai_key = os.environ.get("OPENAI_API_KEY", "")
    if openai_key:
        import openai
        resp = openai.OpenAI(api_key=openai_key).chat.completions.create(
            model="gpt-4o",
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4,
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