"""
settings/persona_loader.py
==========================
Central persona settings loader.

Priority (highest → lowest):
  1. settings/custom.json   — user overrides saved via /api/update_persona
  2. settings/defaults.yaml — AI-generated baseline weights

Public API
----------
    load_merged_persona(key)     -> dict  (single persona, weights merged)
    load_all_merged_personas()   -> dict  (all personas, weights merged)
    save_custom(data)            -> None  (write settings/custom.json)
    get_category_weight(persona_cfg, risk_category) -> float
"""

import json
from pathlib import Path
from typing import Any, Dict

try:
    import yaml
    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False

_SETTINGS_DIR = Path(__file__).parent
_DEFAULTS_PATH = _SETTINGS_DIR / "defaults.yaml"
_CUSTOM_PATH   = _SETTINGS_DIR / "custom.json"

# Maps the free-text risk_category labels used on news items to the snake_case
# weight keys defined in defaults.yaml / custom.json.
_CATEGORY_TO_WEIGHT_KEY: Dict[str, str] = {
    "market risk":        "market_risk",
    "credit risk":        "credit_risk",
    "liquidity risk":     "liquidity_risk",
    "operational risk":   "operational_risk",
    "political risk":     "political_risk",
    "regulatory risk":    "regulatory_risk",
    "reputation risk":    "reputation_risk",
    "systematic risk":    "systematic_risk",
    "systemic risk":      "systematic_risk",
    "disaster risk":      "disaster_risk",
    "supply risk":        "supply_risk",
    "demand risk":        "demand_risk",
    "process risk":       "process_risk",
    "business risk":      "business_risk",
}


# ── Public API ────────────────────────────────────────────────────────────────

def load_merged_persona(persona_key: str) -> Dict[str, Any]:
    """Return the merged config dict for *persona_key*.

    Custom values override defaults; missing custom keys fall back to defaults.
    Returns an empty dict if the persona key is not found in defaults.
    """
    defaults = _load_defaults()
    custom   = _load_custom()

    base     = defaults.get("personas", {}).get(persona_key, {})
    override = custom.get("personas", {}).get(persona_key, {})
    return _deep_merge(base, override)


def load_all_merged_personas() -> Dict[str, Any]:
    """Return all personas with custom overrides applied."""
    defaults = _load_defaults()
    custom   = _load_custom()

    result: Dict[str, Any] = {}
    for key, base in defaults.get("personas", {}).items():
        override      = custom.get("personas", {}).get(key, {})
        result[key]   = _deep_merge(base, override)
    return result


def save_custom(data: Dict[str, Any]) -> None:
    """Persist *data* to settings/custom.json (creates the file if absent)."""
    _CUSTOM_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_category_weight(persona_cfg: Dict[str, Any], risk_category: str) -> float:
    """Return the weight multiplier for *risk_category* from *persona_cfg*.

    Lookup is case-insensitive.  Returns 1.0 (neutral) when the category is
    not found in the weight table — so un-classified items are unaffected.
    """
    weights    = persona_cfg.get("risk_weights") or {}
    weight_key = _CATEGORY_TO_WEIGHT_KEY.get(str(risk_category).lower().strip(), "")
    return float(weights.get(weight_key, 1.0))


# ── Internal helpers ──────────────────────────────────────────────────────────

def _load_defaults() -> Dict[str, Any]:
    if not _DEFAULTS_PATH.exists():
        return {}
    if _YAML_AVAILABLE:
        with open(_DEFAULTS_PATH, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    # Fallback: minimal YAML parser good enough for the simple key:value structure
    return _parse_simple_yaml(_DEFAULTS_PATH.read_text(encoding="utf-8"))


def _load_custom() -> Dict[str, Any]:
    if not _CUSTOM_PATH.exists():
        return {}
    try:
        return json.loads(_CUSTOM_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _deep_merge(base: Dict, override: Dict) -> Dict:
    """Recursively merge *override* into *base*, returning a new dict."""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _parse_simple_yaml(text: str) -> Dict:
    """Minimal YAML parser for the flat persona config (no PyYAML dependency)."""
    result: Dict = {}
    stack: list  = [(result, -1)]  # (current_dict, indent_level)
    key_stack: list = []

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        stripped = line.strip()
        if ":" not in stripped:
            continue
        key, _, val = stripped.partition(":")
        key = key.strip()
        val = val.strip()

        # Pop stack to the right level
        while len(stack) > 1 and stack[-1][1] >= indent:
            stack.pop()
            if key_stack:
                key_stack.pop()

        parent, _ = stack[-1]
        if val == "" or val.startswith("#"):
            # New nested dict
            new_dict: Dict = {}
            parent[key] = new_dict
            stack.append((new_dict, indent))
            key_stack.append(key)
        else:
            # Scalar value
            raw_val = val.split("#")[0].strip()
            try:
                parent[key] = float(raw_val) if "." in raw_val else int(raw_val)
            except ValueError:
                parent[key] = raw_val

    return result
