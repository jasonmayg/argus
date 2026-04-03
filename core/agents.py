#!/usr/bin/env python3
"""
ARGUS Core Intelligence Layer
==============================
Single import point for:
  - AnalysisAgent  (2-D risk matrix, LLM + heuristic scoring, validate_output)
  - Persona registry / card specs  (Cash Manager and peers)
  - Dashboard builders  (cash manager workflow, country board, alert list, FX summary)

Usage
-----
    from core.agents import AnalysisAgent
    from core.agents import (
        PERSONA_REGISTRY, PERSONA_CARD_SPECS,
        build_cash_manager_dashboard, build_generic_persona_dashboard,
        build_alert_list, build_country_board, load_monitored_countries,
        build_fx_summary,
    )
"""

import json
import os
from abc import ABC, abstractmethod
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from risk_matrix import (
    calculate_risk_level,
    PROBABILITY_LABELS,
    IMPACT_LABELS,
    score_to_probability,
    score_to_impact,
)

# ---------------------------------------------------------------------------
# Project-root helper (shared with frontend_app.py via this module)
# ---------------------------------------------------------------------------

def resolve_alphabet_root() -> Optional[Path]:
    """Locate the Alphabet project root folder (contains guardian_poc + Dataset)."""
    local_root = Path(__file__).resolve().parent.parent  # argus/
    desktop_root = local_root.parent
    candidates = [
        local_root,
        desktop_root / "Alphabet ",
        desktop_root / "Alphabet",
    ]
    for c in candidates:
        if (c / "guardian_poc").exists() and (c / "Dataset").exists():
            return c
    return None


# ---------------------------------------------------------------------------
# Agent base class
# ---------------------------------------------------------------------------

class Agent(ABC):
    """Minimal base class shared by all ARGUS agents."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.status = "idle"
        self.logs: List[Dict] = []

    def log(self, message: str, level: str = "INFO") -> None:
        entry = {
            "timestamp": datetime.now().isoformat(),
            "agent":     self.name,
            "level":     level,
            "message":   message,
        }
        self.logs.append(entry)
        print(f"[{self.name}] {level}: {message}")

    @abstractmethod
    async def execute(self, **kwargs):
        """Run the agent's primary task."""

    def get_logs(self) -> List[Dict]:
        return self.logs


# ---------------------------------------------------------------------------
# AnalysisAgent
# ---------------------------------------------------------------------------

class AnalysisAgent(Agent):
    """2-D Probability × Impact risk matrix analysis agent.

    For every classified news item the agent assigns:
        Probability  1=Unlikely … 5=Frequent       (likelihood of occurrence)
        Impact       1=Negligible … 5=Catastrophic (severity for Treasury)

    Scoring is LLM-powered when ``ANTHROPIC_API_KEY`` is set; otherwise a
    keyword-score heuristic is used as a transparent fallback.

    The final risk level — Low / Medium / High / Extreme — is derived from the
    5 × 5 matrix defined in ``risk_matrix.py``.

    Parameters
    ----------
    persona : str
        Active persona key (e.g. ``"cash_manager"``).  When set the LLM prompt
        receives role-specific context so P/I scores are calibrated accordingly.
    """

    _LLM_BATCH_LIMIT = 30

    # Persona-specific system context injected into the LLM prompt
    _PERSONA_CONTEXT: Dict[str, str] = {
        "cash_manager": (
            "You are evaluating risk from a Cash Manager perspective. "
            "Elevate Probability for events that may trigger bank runs, payment delays, "
            "or liquidity freeze. Elevate Impact for events that could disrupt same-day "
            "settlements, cash buffers, or short-term funding lines."
        ),
        "fx_manager": (
            "You are evaluating risk from an FX Manager perspective. "
            "Elevate Probability for currency-volatility events and cross-border flows. "
            "Elevate Impact for events that materially affect hedge ratios or FX exposure."
        ),
        "risk_manager": (
            "You are evaluating risk from a Risk Manager perspective. "
            "Focus on systemic contagion, concentration risk, and escalation signals."
        ),
    }

    def __init__(self, persona: str = "cash_manager") -> None:
        super().__init__("RiskAnalyzer")
        self.persona = persona
        self.analysis_results: Dict = {}
        self._api_key = os.getenv("ANTHROPIC_API_KEY")

    # ── Public API ────────────────────────────────────────────────────────────

    async def execute(self, classified_news: List[Dict], **kwargs) -> Dict:
        """Score every item on the P × I matrix and aggregate results."""
        self.status = "running"
        self.log(
            f"Starting 2D risk-matrix analysis of {len(classified_news)} items "
            f"(persona={self.persona})"
        )

        try:
            raw_scored = await self._score_items(classified_news)
            # Validate every item before storing — no NoneType values escape.
            scored_items = [self.validate_output(item) for item in raw_scored]

            extreme_items = [n for n in scored_items if n.get("matrix_level") == "Extreme"]
            high_items    = [n for n in scored_items if n.get("matrix_level") == "High"]
            medium_items  = [n for n in scored_items if n.get("matrix_level") == "Medium"]
            low_items     = [n for n in scored_items if n.get("matrix_level") == "Low"]

            llm_used = any(item.get("llm_scored") for item in scored_items)
            self.log(
                f"Matrix (LLM={'yes' if llm_used else 'heuristic'}) — "
                f"Extreme:{len(extreme_items)}  High:{len(high_items)}  "
                f"Medium:{len(medium_items)}  Low:{len(low_items)}"
            )

            by_category: Dict[str, list] = {}
            for item in scored_items:
                cat = item.get("risk_category") or "Unknown"
                by_category.setdefault(cat, []).append(item)

            analysis = {
                "total_items":    len(scored_items),
                "scored_items":   scored_items,
                "llm_scored":     llm_used,
                "persona":        self.persona,
                "by_risk_level": {
                    "extreme": extreme_items,
                    "high":    high_items,
                    "medium":  medium_items,
                    "low":     low_items,
                },
                "by_category":    {cat: len(items) for cat, items in by_category.items()},
                "top_keywords":   self._extract_top_keywords(classified_news),
                "matrix_summary": self._build_matrix_summary(scored_items),
            }

            self.analysis_results = analysis
            self.log("2D risk-matrix analysis completed successfully")
            self.status = "success"
            return {"status": "success", "analysis": analysis}

        except Exception as exc:
            self.log(f"Analysis failed: {exc}", "ERROR")
            self.status = "failed"
            return {"status": "failed", "error": str(exc)}

    # ── validate_output ───────────────────────────────────────────────────────

    def validate_output(self, item: Any) -> Any:
        """Recursively replace ``None`` with safe typed defaults.

        Guarantees that no ``NoneType`` values survive into the JSON response,
        preventing the ``TypeError`` that occurs when ``jsonify`` attempts to
        sort dict keys that include ``None``.

        For dict items (scored news rows) the method also ensures every
        required matrix field is present so downstream consumers never need to
        guard against missing keys.
        """
        if item is None:
            return {}

        if isinstance(item, dict):
            cleaned: Dict[str, Any] = {}
            for k, v in item.items():
                key = str(k) if k is not None else "unknown_key"
                cleaned[key] = self.validate_output(v)

            # Required matrix fields — fill with safe defaults if absent
            cleaned.setdefault("probability",           1)
            cleaned.setdefault("probability_label",     PROBABILITY_LABELS[1])
            cleaned.setdefault("probability_reasoning", "")
            cleaned.setdefault("impact",                1)
            cleaned.setdefault("impact_label",          IMPACT_LABELS[1])
            cleaned.setdefault("impact_reasoning",      "")
            cleaned.setdefault("matrix_level",          "Low")
            cleaned.setdefault("matrix_color",          "#28a745")
            cleaned.setdefault("matrix_text_color",     "#fff")
            cleaned.setdefault("matrix_score",          0)
            cleaned.setdefault("llm_scored",            False)
            cleaned.setdefault("risk_category",         "Unknown")
            cleaned.setdefault("risk_score",            0)
            cleaned.setdefault("matched_keywords",      [])
            return cleaned

        if isinstance(item, list):
            return [self.validate_output(i) for i in item]

        if isinstance(item, (str, int, float, bool)):
            return item

        # datetime and other types → string
        return str(item)

    # ── Scoring orchestration ─────────────────────────────────────────────────

    async def _score_items(self, news_items: List[Dict]) -> List[Dict]:
        """Try LLM scoring; fall back to heuristic on any failure."""
        if self._api_key and news_items:
            try:
                return await self._score_with_llm(news_items)
            except Exception as exc:
                self.log(f"LLM scoring failed ({exc}), using heuristic fallback", "WARN")
        return self._score_heuristic(news_items)

    # ── LLM-powered scoring ───────────────────────────────────────────────────

    async def _score_with_llm(self, news_items: List[Dict]) -> List[Dict]:
        """Call Claude to assign Probability and Impact for every item.

        Items beyond ``_LLM_BATCH_LIMIT`` are scored heuristically to cap cost.
        """
        import anthropic  # imported lazily so the module loads without the SDK

        client    = anthropic.Anthropic(api_key=self._api_key)
        llm_items = news_items[: self._LLM_BATCH_LIMIT]

        items_text = "\n".join(
            f"{i + 1}. [{item.get('date', '')}] {item.get('title', '')} "
            f"(keyword risk score: {item.get('risk_score', 0):.0f}/100)"
            for i, item in enumerate(llm_items)
        )

        persona_ctx = self._PERSONA_CONTEXT.get(self.persona, "")
        prompt = f"""You are a senior treasury risk analyst. {persona_ctx}

For each news item below, assess two independent dimensions:

**Probability** — likelihood this event materialises within the next 90 days:
  1=Unlikely  |  2=Seldom  |  3=Occasional  |  4=Likely  |  5=Frequent

**Impact** — severity on Treasury operations, liquidity, and fixed-income portfolio:
  1=Negligible  |  2=Minor  |  3=Moderate  |  4=Hazardous  |  5=Catastrophic

News items:
{items_text}

Return a JSON array — one object per item, in the same order:
[
  {{
    "index": 1,
    "probability": <int 1-5>,
    "probability_label": "<label>",
    "probability_reasoning": "<one concise sentence>",
    "impact": <int 1-5>,
    "impact_label": "<label>",
    "impact_reasoning": "<one concise sentence>"
  }},
  ...
]
Return ONLY the JSON array — no preamble, no markdown fences."""

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )

        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            parts = raw.split("```")
            raw = parts[1] if len(parts) > 1 else raw
            if raw.lower().startswith("json"):
                raw = raw[4:]
        scores: List[Dict] = json.loads(raw.strip())

        result: List[Dict] = []
        for i, item in enumerate(news_items):
            item = dict(item)
            if i < len(scores):
                s   = scores[i]
                p   = max(1, min(5, int(s.get("probability", 1))))
                imp = max(1, min(5, int(s.get("impact",      1))))
                mx  = calculate_risk_level(p, imp)
                item.update({
                    "probability":           p,
                    "probability_label":     PROBABILITY_LABELS[p],
                    "probability_reasoning": s.get("probability_reasoning", ""),
                    "impact":                imp,
                    "impact_label":          IMPACT_LABELS[imp],
                    "impact_reasoning":      s.get("impact_reasoning", ""),
                    "matrix_level":          mx["level"],
                    "matrix_color":          mx["color"],
                    "matrix_text_color":     mx["text_color"],
                    "matrix_score":          mx["matrix_score"],
                    "llm_scored":            True,
                })
            else:
                item = self._apply_heuristic(item)
            result.append(item)
        return result

    # ── Heuristic fallback ────────────────────────────────────────────────────

    def _score_heuristic(self, news_items: List[Dict]) -> List[Dict]:
        """Derive P and I from the existing keyword risk_score (0–100)."""
        return [self._apply_heuristic(dict(item)) for item in news_items]

    def _apply_heuristic(self, item: Dict) -> Dict:
        rs  = float(item.get("risk_score") or 0)
        p   = score_to_probability(rs)
        imp = score_to_impact(rs)
        mx  = calculate_risk_level(p, imp)
        item.update({
            "probability":           p,
            "probability_label":     PROBABILITY_LABELS[p],
            "probability_reasoning": f"Estimated from keyword risk score {rs:.0f}/100.",
            "impact":                imp,
            "impact_label":          IMPACT_LABELS[imp],
            "impact_reasoning":      f"Estimated from keyword risk score {rs:.0f}/100.",
            "matrix_level":          mx["level"],
            "matrix_color":          mx["color"],
            "matrix_text_color":     mx["text_color"],
            "matrix_score":          mx["matrix_score"],
            "llm_scored":            False,
        })
        return item

    # ── Aggregation helpers ───────────────────────────────────────────────────

    def _build_matrix_summary(self, scored_items: List[Dict]) -> List[List[int]]:
        """Return a 5 × 5 item-count grid (probability × impact) for the heatmap."""
        grid = [[0] * 5 for _ in range(5)]
        for item in scored_items:
            p   = max(1, min(5, int(item.get("probability", 1))))
            imp = max(1, min(5, int(item.get("impact",      1))))
            grid[p - 1][imp - 1] += 1
        return grid

    def _extract_top_keywords(
        self, news_items: List[Dict], top_n: int = 10
    ) -> List[str]:
        """Return the top-N most frequent matched keywords across all items."""
        counts: Dict[str, int] = {}
        for item in news_items:
            for kw in item.get("matched_keywords", []):
                counts[kw] = counts.get(kw, 0) + 1
        return [
            kw
            for kw, _ in sorted(counts.items(), key=lambda x: x[1], reverse=True)[
                :top_n
            ]
        ]


# ===========================================================================
# Persona registry and card specifications
# ===========================================================================

FX_RELATED_CATEGORIES = {
    "market risk",
    "liquidity risk",
    "credit risk",
    "political risk",
    "regulatory risk",
    "systematic risk",
}

PERSONA_REGISTRY: Dict[str, Dict[str, str]] = {
    "cash_manager": {
        "label":   "Cash Manager",
        "summary": "Prioritizes liquidity, payments, funding pressure, and immediate treasury actions.",
    },
    "fx_manager": {
        "label":   "FX Manager",
        "summary": "Prioritizes currency volatility, hedge exposure, and FX-driven cross-border risk.",
    },
    "treasury_ops": {
        "label":   "Treasury Operations",
        "summary": "Prioritizes daily treasury execution, controls, and operational continuity.",
    },
    "treasury_tech": {
        "label":   "Treasury Technology",
        "summary": "Prioritizes systems resilience, process reliability, and operational technology risk.",
    },
    "risk_manager": {
        "label":   "Risk Manager",
        "summary": "Prioritizes concentration risk, systemic signals, and escalation readiness.",
    },
}

PERSONA_CARD_SPECS: Dict[str, List[Dict]] = {
    "cash_manager": [
        {
            "key":        "liquidity_risk",
            "title":      "Liquidity Risk",
            "categories": {"liquidity risk", "credit risk", "systematic risk"},
            "keywords":   {"liquidity", "cash", "buffer", "funding", "credit", "default"},
        },
        {
            "key":        "funding_pressure",
            "title":      "Funding Cost Pressure",
            "categories": {"market risk", "credit risk", "regulatory risk"},
            "keywords":   {"interest", "rate", "funding", "debt", "maturity", "refinanc"},
        },
        {
            "key":        "payments_ops",
            "title":      "Payment & Disbursement Risk",
            "categories": {"operational risk", "process risk", "regulatory risk"},
            "keywords":   {"payment", "settlement", "bank", "disbursement", "transfer", "outage"},
        },
        {
            "key":        "cash_mobility",
            "title":      "Cash Concentration & Sweeping Watch",
            "categories": {"regulatory risk", "political risk", "process risk"},
            "keywords":   {"capital", "sweep", "restriction", "mobility", "cross-border", "transfer"},
        },
    ],
}


# ===========================================================================
# Shared alert / board helpers  (used by persona dashboards AND guardian route)
# ===========================================================================

def _risk_level(score: float) -> str:
    if score >= 70:
        return "High"
    if score >= 40:
        return "Medium"
    if score > 0:
        return "Low"
    return "None"


def build_alert_list(alerts: List[Dict], max_items: int = 60) -> List[Dict]:
    """Normalise and sort alert rows; return the top *max_items* by score."""
    shaped = []
    for alert in alerts:
        try:
            score = float(alert.get("score", 0))
        except (TypeError, ValueError):
            score = 0.0
        shaped.append({
            "alert_id":             (
                f"{alert.get('country','Unknown')}|{alert.get('source','-')}"
                f"|{alert.get('published_at','')}|{round(score, 2)}"
            ),
            "country":              alert.get("country")              or "Unknown",
            "category":             alert.get("category_label")       or alert.get("category_key") or "-",
            "score":                round(score, 2),
            "risk_level":           _risk_level(score),
            "severity":             alert.get("severity")             or "-",
            "recency":              alert.get("recency")              or "-",
            "source":               alert.get("source")               or "-",
            "title":                alert.get("title")                or "",
            "url":                  alert.get("url")                  or "",
            "published_at":         alert.get("published_at")         or "",
            "recommendation":       alert.get("recommendation")       or "",
            "recommendation_source":alert.get("recommendation_source") or "rule",
            "rationale":            alert.get("rationale")            or "",
            "raw_dt":               alert.get("raw_dt")               or "",
        })
    shaped.sort(key=lambda x: x["score"], reverse=True)
    return shaped[:max_items]


def load_monitored_countries() -> List[str]:
    """Load the 11-country watchlist from Alphabet config; fallback to defaults."""
    fallback = [
        "USA", "UK", "Germany", "Japan", "France", "Canada",
        "Brazil", "India", "Australia", "Mexico", "China",
    ]
    alphabet_root = resolve_alphabet_root()
    if not alphabet_root:
        return fallback
    cfg_path = alphabet_root / "guardian_poc" / "config" / "countries.yaml"
    if not cfg_path.exists():
        return fallback
    try:
        import yaml
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        countries = list((cfg.get("countries") or {}).keys())
        return countries if countries else fallback
    except Exception:
        return fallback


def build_country_board(
    alerts: List[Dict],
    monitored_set: Optional[set] = None,
) -> List[Dict]:
    """Aggregate alert rows into a per-country risk board, sorted by max score."""
    if monitored_set is None:
        monitored_set = set(load_monitored_countries())

    by_country: Dict[str, Dict] = {}
    for alert in alerts:
        country = alert.get("country") or "Unknown"
        if monitored_set and country not in monitored_set:
            continue
        try:
            score = float(alert.get("score", 0))
        except (TypeError, ValueError):
            score = 0.0

        row = by_country.setdefault(country, {
            "country":      country,
            "alert_count":  0,
            "score_sum":    0.0,
            "max_score":    0.0,
            "top_category": "Unclassified",
        })
        row["alert_count"] += 1
        row["score_sum"]   += score
        if score > row["max_score"]:
            row["max_score"]    = score
            row["top_category"] = (
                alert.get("category_label") or alert.get("category_key") or "-"
            )

    board = []
    for country, row in by_country.items():
        avg = row["score_sum"] / row["alert_count"] if row["alert_count"] else 0.0
        board.append({
            "country":      country,
            "alert_count":  row["alert_count"],
            "avg_score":    round(avg, 2),
            "max_score":    round(row["max_score"], 2),
            "top_category": row["top_category"],
            "risk_level":   _risk_level(row["max_score"]),
        })

    board.sort(key=lambda x: (x["max_score"], x["alert_count"]), reverse=True)
    return board


def build_fx_summary(
    alert_rows: List[Dict], monitored_countries: List[str]
) -> Dict:
    """Build FX-oriented risk board for monitored countries."""
    board_map: Dict[str, Dict] = {
        c: {
            "country":       c,
            "fx_score":      0.0,
            "fx_level":      "None",
            "fx_alert_count":0,
            "top_driver":    "-",
        }
        for c in monitored_countries
    }

    for row in alert_rows:
        country  = row.get("country")
        if country not in board_map:
            continue
        category = str(row.get("category") or "").strip().lower()
        if category not in FX_RELATED_CATEGORIES:
            continue
        try:
            score = float(row.get("score") or 0)
        except (TypeError, ValueError):
            score = 0.0
        slot = board_map[country]
        slot["fx_alert_count"] += 1
        if score >= slot["fx_score"]:
            slot["fx_score"]   = round(score, 2)
            slot["fx_level"]   = _risk_level(score)
            slot["top_driver"] = row.get("category") or "-"

    board = list(board_map.values())
    board.sort(key=lambda x: (x["fx_score"], x["fx_alert_count"]), reverse=True)
    high_count = len([x for x in board if x["fx_score"] >= 70])
    med_above  = len([x for x in board if x["fx_score"] >= 40])
    return {
        "monitored_count":             len(monitored_countries),
        "high_risk_country_count":     high_count,
        "medium_or_above_country_count":med_above,
        "top5":                        board[:5],
        "countries":                   board,
    }


# ===========================================================================
# Cash Manager persona — decision logic
# ===========================================================================

def _status_from_score(score: float) -> str:
    if score >= 70:
        return "high"
    if score >= 40:
        return "medium"
    if score > 0:
        return "low"
    return "stable"


def _matches_persona_spec(row: Dict, spec: Dict) -> bool:
    category = str(row.get("category") or "").strip().lower()
    if category in spec["categories"]:
        return True
    text = " ".join([
        str(row.get("title")          or ""),
        str(row.get("recommendation") or ""),
        str(row.get("rationale")      or ""),
    ]).lower()
    return any(kw in text for kw in spec["keywords"])


def _default_actions_for_cash_manager(rows: List[Dict]) -> List[str]:
    actions: List[str] = []
    for row in rows:
        rec = str(row.get("recommendation") or "").strip()
        if rec and rec not in actions:
            actions.append(rec)
        if len(actions) >= 3:
            break
    return actions or [
        "Review local cash buffers in the highest-risk markets.",
        "Validate payment routing and same-day settlement capacity.",
        "Re-check short-term funding and cash concentration plans.",
    ]


def _top_alert_excerpt(rows: List[Dict], limit: int = 3) -> List[str]:
    return [
        f"{str(r.get('country') or '-')}: {str(r.get('title') or r.get('category') or '-')}"
        for r in rows[:limit]
    ]


def _build_cash_manager_workflow(
    alert_rows: List[Dict], actions: List[str]
) -> List[Dict]:
    signal_rows = alert_rows[:6]
    high_rows   = [r for r in alert_rows if float(r.get("score") or 0) >= 70]
    liquidity_rows = [
        r for r in alert_rows
        if str(r.get("category") or "").strip().lower()
        in {"liquidity risk", "credit risk", "systematic risk"}
    ]
    payment_rows = [
        r for r in alert_rows
        if any(
            kw in str(r.get("title") or "").lower()
            for kw in ("payment", "bank", "settlement")
        )
    ]
    top_cat = Counter(str(r.get("category") or "-") for r in alert_rows).most_common(1)
    dominant = top_cat[0][0] if top_cat else "-"
    impacted = sorted(
        {str(r.get("country") or "-") for r in liquidity_rows + payment_rows}
    )[:4]

    def _max_score(rows: List[Dict]) -> float:
        return max((float(r.get("score") or 0) for r in rows), default=0.0)

    return [
        {
            "key":     "signal_detection",
            "step":    "01",
            "title":   "Signal Detection",
            "status":  _status_from_score(_max_score(signal_rows)),
            "summary": (
                "The system continuously monitors market, geopolitical, banking, "
                "and operational signals relevant to daily cash management."
            ),
            "metric":  f"{len(signal_rows)} priority signals",
            "details": [
                f"{len(alert_rows)} total events available in the latest cycle.",
                f"{len(high_rows)} high-risk signals surfaced for immediate review.",
                "Signal sources include global news, GCS snapshots, and treasury adapters.",
            ],
            "evidence": _top_alert_excerpt(signal_rows),
        },
        {
            "key":     "risk_assessment",
            "step":    "02",
            "title":   "Risk Assessment",
            "status":  _status_from_score(_max_score(high_rows or alert_rows)),
            "summary": (
                "Detected events are classified into treasury-relevant risk categories "
                "so the cash manager can understand the nature of the threat quickly."
            ),
            "metric":  f"Top risk: {dominant}",
            "details": [
                f"{len(liquidity_rows)} alerts map to liquidity / counterparty-sensitive categories.",
                f"Dominant category: {dominant}.",
                "Classification emphasises liquidity, operational continuity, and funding pressure.",
            ],
            "evidence": _top_alert_excerpt(high_rows or alert_rows),
        },
        {
            "key":     "impact_estimation",
            "step":    "03",
            "title":   "Impact Estimation",
            "status":  _status_from_score(_max_score(liquidity_rows + payment_rows)),
            "summary": (
                "The system estimates how detected events could affect global liquidity, "
                "trapped cash, payment execution, and regional treasury exposure."
            ),
            "metric":  f"{len(impacted)} countries under watch",
            "details": [
                f"{len(payment_rows)} signals suggest possible payment/settlement disruption.",
                f"{len(liquidity_rows)} signals could affect near-term liquidity.",
                "Scope covers cash mobility, payment continuity, and short-term funding pressure.",
            ],
            "evidence": impacted or ["No country concentration signal detected."],
        },
        {
            "key":     "decision_support",
            "step":    "04",
            "title":   "Decision Support",
            "status":  _status_from_score(_max_score(high_rows or alert_rows)),
            "summary": (
                "ARGUS translates the risk picture into treasury action options "
                "while keeping the final decision with the cash manager."
            ),
            "metric":  f"{len(actions)} recommended actions",
            "details": [
                "Suggested actions focus on liquidity, payment routing, and funding plans.",
                "Actions support decision speed, not replace treasury judgement.",
                "Escalate to management when exposure becomes material.",
            ],
            "evidence": actions[:3],
        },
    ]


def build_cash_manager_dashboard(
    alert_rows: List[Dict], snapshot: Optional[Dict]
) -> Dict:
    """Build the full Cash Manager persona dashboard payload."""
    matched_cards = []
    for spec in PERSONA_CARD_SPECS["cash_manager"]:
        matched    = [r for r in alert_rows if _matches_persona_spec(r, spec)]
        top        = matched[:3]
        top_score  = max((float(r.get("score") or 0) for r in matched), default=0.0)
        countries  = sorted(
            {str(r.get("country") or "-") for r in top if str(r.get("country") or "").strip()}
        )
        detail = "No material signals detected in the latest snapshot."
        if top:
            detail = " | ".join(
                f"{r.get('country') or '-'}: {r.get('title') or r.get('category') or '-'}"
                for r in top[:2]
            )
        matched_cards.append({
            "key":       spec["key"],
            "title":     spec["title"],
            "status":    _status_from_score(top_score),
            "metric":    f"{len(matched)} matched alerts",
            "detail":    detail,
            "top_score": round(top_score, 2),
            "countries": countries[:3],
        })

    high_risk  = len([r for r in alert_rows if float(r.get("score") or 0) >= 70])
    triggered  = len([r for r in alert_rows if float(r.get("score") or 0) >= 40])
    top_countries = build_country_board(alert_rows)[:3]
    actions    = _default_actions_for_cash_manager(alert_rows)
    generated_at = "-"
    if isinstance(snapshot, dict):
        generated_at = (
            snapshot.get("generated_at_local") or snapshot.get("generated_at_utc") or "-"
        )

    return {
        "persona": "cash_manager",
        "title":   "Cash Manager Dashboard",
        "summary": {
            "headline": (
                f"{triggered} triggered alerts and {high_risk} high-risk signals "
                "currently affect near-term cash decisions."
            ),
            "subheadline": (
                "Focus on liquidity, payment execution, short-term funding, and cash mobility."
            ),
            "generated_at": generated_at,
            "event_count":  len(alert_rows),
        },
        "cards":         matched_cards,
        "actions":       actions,
        "top_countries": top_countries,
        "workflow":      _build_cash_manager_workflow(alert_rows, actions),
    }


def build_generic_persona_dashboard(
    persona: str, alert_rows: List[Dict], snapshot: Optional[Dict]
) -> Dict:
    """Build a placeholder dashboard for personas without a deep implementation."""
    cfg      = PERSONA_REGISTRY.get(persona, PERSONA_REGISTRY["cash_manager"])
    top_rows = alert_rows[:3]
    generated_at = "-"
    if isinstance(snapshot, dict):
        generated_at = (
            snapshot.get("generated_at_local") or snapshot.get("generated_at_utc") or "-"
        )
    return {
        "persona": persona,
        "title":   f"{cfg['label']} Dashboard",
        "summary": {
            "headline":    cfg["summary"],
            "subheadline": "Role-based placeholder; Cash Manager has the deepest implementation.",
            "generated_at":generated_at,
            "event_count": len(alert_rows),
        },
        "cards": [
            {
                "key":       "priority_signals",
                "title":     "Priority Signals",
                "status":    _status_from_score(
                    max((float(r.get("score") or 0) for r in top_rows), default=0.0)
                ),
                "metric":    f"{len(top_rows)} top alerts",
                "detail":    " | ".join(
                    f"{r.get('country') or '-'}: {r.get('category') or '-'}"
                    for r in top_rows
                ) or "No alerts available.",
                "top_score": round(
                    max((float(r.get("score") or 0) for r in top_rows), default=0.0), 2
                ),
                "countries": [str(r.get("country") or "-") for r in top_rows[:3]],
            }
        ],
        "actions": [
            "Review top alerts from the global Guardian board.",
            "Switch to Cash Manager persona for the full role-specific demo.",
        ],
        "top_countries": build_country_board(alert_rows)[:3],
        "workflow": [],
    }
