"""
personas/persona_loader.py
==========================
Loads AI-baseline persona weights from personas/defaults.yaml.

Design principle
----------------
There is no floating custom.json.  Per-user overrides live exclusively in
named profile files under personas/profiles/.  Merging happens in-memory
at the call site (e.g. inside /api/load_profile) and is never persisted
as a side-effect of a UI action.

If the user is moving sliders without saving, that state is owned by the
frontend (psDraft) and is never written to disk.

Public API
----------
    load_defaults(persona_key)                       -> dict  single persona global baseline
    load_defaults_for_region(persona_key, region)    -> dict  region-merged baseline
    load_all_defaults()                              -> dict  all personas (global only)
    load_all_region_defaults(persona_key)            -> dict  {region: merged_cfg} for all regions
    merge_weights(base, override)                    -> dict  deep-merge helper
    get_category_weight(cfg, category)               -> float weight for one risk category
"""

from pathlib import Path
from typing import Any, Dict

try:
    import yaml
    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False

_PERSONAS_DIR  = Path(__file__).parent
_DEFAULTS_PATH = _PERSONAS_DIR / "defaults.yaml"

# Maps free-text risk_category labels used on news items to the
# snake_case weight keys defined in defaults.yaml.
_CATEGORY_TO_WEIGHT_KEY: Dict[str, str] = {
    "market risk":      "market_risk",
    "credit risk":      "credit_risk",
    "liquidity risk":   "liquidity_risk",
    "operational risk": "operational_risk",
    "political risk":   "political_risk",
    "regulatory risk":  "regulatory_risk",
    "reputation risk":  "reputation_risk",
    "systematic risk":  "systematic_risk",
    "systemic risk":    "systematic_risk",
    "disaster risk":    "disaster_risk",
    "supply risk":      "supply_risk",
    "demand risk":      "demand_risk",
    "process risk":     "process_risk",
    "business risk":    "business_risk",
}


# Known region keys — must match TUNE_REGIONS in tune-modal.js
_KNOWN_REGIONS = ("global", "us", "canada", "mexico", "china", "japan", "uk", "korea", "india")


# ── Public API ────────────────────────────────────────────────────────────────

def load_defaults(persona_key: str) -> Dict[str, Any]:
    """Return the AI-baseline config for *persona_key* from defaults.yaml.

    Returns an empty dict if the key is not found.
    """
    return _load_yaml().get("personas", {}).get(persona_key, {})


def load_defaults_for_region(persona_key: str, region_key: str) -> Dict[str, Any]:
    """Return the merged config for *persona_key* + *region_key*.

    Applies the persona's ``region_overrides.<region_key>`` shallow diff on top
    of the global ``risk_weights``.  ``alert_threshold`` is always taken from
    the global baseline (region does not change the threshold).

    Returns the global baseline when *region_key* is ``"global"`` or unknown.
    """
    base = load_defaults(persona_key)
    if not base:
        return {}
    if region_key == "global" or region_key not in _KNOWN_REGIONS:
        return base

    overrides = base.get("region_overrides", {}).get(region_key, {})
    if not overrides:
        return base

    merged_weights = dict(base.get("risk_weights", {}))
    merged_weights.update(overrides)
    return {
        "alert_threshold": base.get("alert_threshold", 40),
        "risk_weights": merged_weights,
    }


def load_all_defaults() -> Dict[str, Any]:
    """Return all persona configs from defaults.yaml (global baseline only)."""
    return _load_yaml().get("personas", {})


def load_all_region_defaults(persona_key: str) -> Dict[str, Dict[str, Any]]:
    """Return a mapping of region_key → merged config for every known region.

    Useful for embedding all region variants in a single API response so the
    frontend can switch regions without re-fetching.
    """
    return {
        region: load_defaults_for_region(persona_key, region)
        for region in _KNOWN_REGIONS
    }


def merge_weights(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Deep-merge *override* into *base*, returning a new dict.

    Typically used as::

        merged = merge_weights(load_defaults(key), profile["weights"])

    so a profile's weights shadow the AI defaults without mutating either.
    """
    return _deep_merge(base, override)


def get_category_weight(persona_cfg: Dict[str, Any], risk_category: str) -> float:
    """Return the weight multiplier for *risk_category* from *persona_cfg*.

    Lookup is case-insensitive.  Returns 1.0 (neutral) when the category
    is not in the weight table so un-classified items are unaffected.
    """
    weights    = persona_cfg.get("risk_weights") or {}
    weight_key = _CATEGORY_TO_WEIGHT_KEY.get(str(risk_category).lower().strip(), "")
    return float(weights.get(weight_key, 1.0))


# ── Internal helpers ──────────────────────────────────────────────────────────

def _load_yaml() -> Dict[str, Any]:
    if not _DEFAULTS_PATH.exists():
        return {}
    if _YAML_AVAILABLE:
        with open(_DEFAULTS_PATH, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    return _parse_simple_yaml(_DEFAULTS_PATH.read_text(encoding="utf-8"))


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
    """Minimal YAML parser for flat persona config (no PyYAML fallback)."""
    result: Dict = {}
    stack: list  = [(result, -1)]
    key_stack: list = []

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        indent   = len(line) - len(line.lstrip())
        stripped = line.strip()
        if ":" not in stripped:
            continue
        key, _, val = stripped.partition(":")
        key = key.strip()
        val = val.strip()

        while len(stack) > 1 and stack[-1][1] >= indent:
            stack.pop()
            if key_stack:
                key_stack.pop()

        parent, _ = stack[-1]
        if val == "" or val.startswith("#"):
            new_dict: Dict = {}
            parent[key]   = new_dict
            stack.append((new_dict, indent))
            key_stack.append(key)
        else:
            raw_val = val.split("#")[0].strip()
            try:
                parent[key] = float(raw_val) if "." in raw_val else int(raw_val)
            except ValueError:
                parent[key] = raw_val

    return result
