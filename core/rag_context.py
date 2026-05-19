"""
Phase 2 RAG context builder — shared retrieval helpers for chat/brief/actions.

Each endpoint formats hits differently (citations vs. inline prose vs. few-shot
examples), so this module keeps the *retrieval* part — querying Chroma,
filtering, and serializing hits into a stable structure — separated from the
endpoint-specific prompting.

Public surface:
    build_chat_context(query, request_id, ...) -> ChatContext
    build_brief_context(persona, pipeline_results, request_id, ...) -> ChatContext
    build_actions_context(alerts, persona, request_id, ...) -> ChatContext
    load_endpoint_config(endpoint) -> dict

A `ChatContext` carries both the LLM-facing prompt string and a structured
list of citations the frontend can render alongside the reply.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from core.vector_store import SearchHit, get_vector_store

ROOT = Path(__file__).resolve().parent.parent
_CONFIG_PATH = Path(os.getenv("RAG_CONFIG", ROOT / "config" / "rag_config.yaml"))


@dataclass
class Citation:
    collection: str
    id: str
    title: str
    url: Optional[str]
    date: Optional[str]
    score: Optional[float]
    distance: float


@dataclass
class ChatContext:
    prompt_text: str
    citations: list[Citation] = field(default_factory=list)
    used: bool = False           # True if RAG actually contributed text
    news_hits: int = 0
    alerts_hits: int = 0
    regulatory_hits: int = 0     # ECB / BoE / OFAC etc. — they live in
                                 # the regulatory collection, not news


# ─────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────
_CONFIG_CACHE: Optional[dict] = None


def _load_config() -> dict:
    global _CONFIG_CACHE
    if _CONFIG_CACHE is None:
        if _CONFIG_PATH.exists():
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                _CONFIG_CACHE = yaml.safe_load(f) or {}
        else:
            _CONFIG_CACHE = {}
    return _CONFIG_CACHE


def load_endpoint_config(endpoint: str) -> dict:
    """Return the {endpoints.<name>} block, or an empty dict if absent."""
    return (_load_config().get("endpoints") or {}).get(endpoint) or {}


# ─────────────────────────────────────────────────────────────────────────
# Formatting helpers
# ─────────────────────────────────────────────────────────────────────────
def _first_line(text: str, max_chars: int = 140) -> str:
    line = (text or "").splitlines()[0] if text else ""
    return line[: max_chars - 1].rstrip() + "…" if len(line) > max_chars else line


def _news_line(i: int, hit: SearchHit) -> str:
    m = hit.metadata or {}
    title = _first_line(hit.document, 160)
    parts = [f"[N{i}] {title}"]
    bits = []
    if m.get("date"):          bits.append(str(m["date"]))
    if m.get("risk_category"): bits.append(str(m["risk_category"]))
    if m.get("risk_score") is not None: bits.append(f"score={m['risk_score']}")
    if bits:
        parts.append("  (" + " | ".join(bits) + ")")
    return "".join(parts)


def _alert_line(i: int, hit: SearchHit) -> str:
    m = hit.metadata or {}
    title = _first_line(hit.document, 160)
    parts = [f"[A{i}] {title}"]
    bits = []
    if m.get("snapshot_date"):  bits.append(str(m["snapshot_date"]))
    if m.get("country"):        bits.append(str(m["country"]))
    if m.get("category_label"): bits.append(str(m["category_label"]))
    if m.get("score") is not None: bits.append(f"score={m['score']}")
    if bits:
        parts.append("  (" + " | ".join(bits) + ")")
    return "".join(parts)


def _regulatory_line(i: int, hit: SearchHit) -> str:
    """Render a regulatory_changes hit. Surfacing country/category/severity
    is more useful than the date here — regulatory items are rule-changes
    or binding actions, and operators scan for jurisdiction + severity
    first, not recency."""
    m = hit.metadata or {}
    title = _first_line(hit.document, 160)
    parts = [f"[R{i}] {title}"]
    bits = []
    if m.get("country"):        bits.append(str(m["country"]))
    if m.get("category"):       bits.append(str(m["category"]))
    if m.get("severity"):       bits.append(str(m["severity"]))
    if m.get("effective_date"): bits.append(f"eff={m['effective_date']}")
    if bits:
        parts.append("  (" + " | ".join(bits) + ")")
    return "".join(parts)


def _to_citation(coll: str, hit: SearchHit, label: str) -> Citation:
    m = hit.metadata or {}
    return Citation(
        collection=coll,
        id=hit.id,
        title=_first_line(hit.document, 200),
        url=m.get("url"),
        date=m.get("date") or m.get("snapshot_date")
             or m.get("seendate") or m.get("effective_date"),
        score=m.get("risk_score") if m.get("risk_score") is not None else m.get("score"),
        distance=hit.distance,
    )


# ─────────────────────────────────────────────────────────────────────────
# Public builder
# ─────────────────────────────────────────────────────────────────────────
def build_chat_context(
    query: str,
    request_id: Optional[str] = None,
    endpoint: str = "api.chat",
) -> ChatContext:
    """Retrieve from `news`, `alerts`, and `regulatory` for a chat query.

    Regulatory is included because the bulk of Argus's structured signal
    (ECB / BoE / BoJ / OFAC etc.) lives there — the `news` collection
    currently only ingests Fed news, so without this any non-US monetary
    or sanctions question would retrieve nothing relevant.

    Returns a ChatContext. If RAG is disabled in config, returns an empty
    ChatContext with `used=False` so callers can fall back to the legacy
    static context. Failures inside Chroma are swallowed (best-effort).
    """
    cfg = load_endpoint_config("chat")
    if not cfg.get("enabled", True):
        return ChatContext(prompt_text="", used=False)

    news_k = int(cfg.get("news_top_k", 5))
    alerts_k = int(cfg.get("alerts_top_k", 3))
    regulatory_k = int(cfg.get("regulatory_top_k", 3))
    alerts_min = int(cfg.get("alerts_min_count", 5))

    try:
        store = get_vector_store()
    except Exception:
        return ChatContext(prompt_text="", used=False)

    news_hits: list[SearchHit] = []
    alerts_hits: list[SearchHit] = []
    regulatory_hits: list[SearchHit] = []
    try:
        if news_k > 0:
            news_hits = store.search_news(
                query, n_results=news_k,
                request_id=request_id, endpoint=endpoint,
            )
    except Exception:
        news_hits = []

    try:
        if alerts_k > 0 and store.count("alerts") >= alerts_min:
            alerts_hits = store.search_alerts(
                query, n_results=alerts_k,
                request_id=request_id, endpoint=endpoint,
            )
    except Exception:
        alerts_hits = []

    try:
        if regulatory_k > 0:
            regulatory_hits = store.search_regulatory(
                query, n_results=regulatory_k,
                request_id=request_id, endpoint=endpoint,
            )
    except Exception:
        regulatory_hits = []

    if not news_hits and not alerts_hits and not regulatory_hits:
        return ChatContext(prompt_text="", used=False,
                           news_hits=0, alerts_hits=0, regulatory_hits=0)

    blocks: list[str] = []
    citations: list[Citation] = []

    if news_hits:
        lines = [_news_line(i + 1, h) for i, h in enumerate(news_hits)]
        blocks.append("Retrieved news (most relevant first):\n" + "\n".join(lines))
        citations.extend(_to_citation("news", h, f"N{i+1}") for i, h in enumerate(news_hits))

    if alerts_hits:
        lines = [_alert_line(i + 1, h) for i, h in enumerate(alerts_hits)]
        blocks.append("Retrieved alerts:\n" + "\n".join(lines))
        citations.extend(_to_citation("alerts", h, f"A{i+1}") for i, h in enumerate(alerts_hits))

    if regulatory_hits:
        lines = [_regulatory_line(i + 1, h) for i, h in enumerate(regulatory_hits)]
        blocks.append("Retrieved regulatory changes:\n" + "\n".join(lines))
        citations.extend(_to_citation("regulatory", h, f"R{i+1}") for i, h in enumerate(regulatory_hits))

    tag_examples: list[str] = []
    if news_hits:        tag_examples.append("[N1]")
    if alerts_hits:      tag_examples.append("[A1]")
    if regulatory_hits:  tag_examples.append("[R1]")
    available_tags = " / ".join(tag_examples) or "[N#]"
    blocks.append(
        f"Cite items by tag ({available_tags}) when their content informs your answer. "
        "Do not invent tags that are not in the retrieval block above. "
        "If retrieval is empty or irrelevant, answer from general knowledge and say so."
    )
    return ChatContext(
        prompt_text="\n\n".join(blocks),
        citations=citations,
        used=True,
        news_hits=len(news_hits),
        alerts_hits=len(alerts_hits),
        regulatory_hits=len(regulatory_hits),
    )


# ─────────────────────────────────────────────────────────────────────────
# Brief builder
# ─────────────────────────────────────────────────────────────────────────
_RISK_KEYWORDS: dict[str, str] = {
    "market_risk":      "market volatility equity",
    "credit_risk":      "credit default counterparty",
    "liquidity_risk":   "liquidity funding cash flow",
    "operational_risk": "operations system outage",
    "political_risk":   "political geopolitical sanctions",
    "regulatory_risk":  "regulatory compliance enforcement",
    "reputation_risk":  "reputation scandal",
    "systematic_risk":  "systemic macroeconomic recession",
    "disaster_risk":    "disaster natural catastrophe",
    "supply_risk":      "supply chain shortage",
    "demand_risk":      "demand consumer spending",
    "process_risk":     "process control failure",
    "business_risk":    "business continuity",
}

_PERSONA_WEIGHTS_CACHE: Optional[dict] = None


def _persona_query_terms(persona: str, top_n: int = 4) -> list[str]:
    """Map persona → top-N risk category keyword strings, sorted by weight.

    Reads only personas/defaults.yaml (not merged with user profiles —
    the brief query just needs a stable signal of what this persona cares
    about; profile-level tuning is the alert layer's concern, not RAG's).
    """
    global _PERSONA_WEIGHTS_CACHE
    if _PERSONA_WEIGHTS_CACHE is None:
        path = ROOT / "personas" / "defaults.yaml"
        try:
            with open(path, "r", encoding="utf-8") as f:
                _PERSONA_WEIGHTS_CACHE = (yaml.safe_load(f) or {}).get("personas") or {}
        except Exception:
            _PERSONA_WEIGHTS_CACHE = {}

    weights = (_PERSONA_WEIGHTS_CACHE.get(persona) or {}).get("risk_weights") or {}
    if not weights:
        return []
    ranked = sorted(weights.items(), key=lambda kv: kv[1], reverse=True)
    terms: list[str] = []
    for cat, _w in ranked[:top_n]:
        if cat in _RISK_KEYWORDS:
            terms.append(_RISK_KEYWORDS[cat])
    return terms


def _pipeline_top_titles(pipeline_results: Optional[dict], max_n: int) -> list[str]:
    """Extract the top-scored item titles from the pipeline so the brief
    retrieval query is anchored on what the system itself flagged today."""
    if not pipeline_results or max_n <= 0:
        return []
    try:
        scored = (
            (pipeline_results.get("stages") or {})
            .get("analysis", {})
            .get("analysis", {})
            .get("scored_items") or []
        )
    except AttributeError:
        return []
    if not scored:
        return []
    ordered = sorted(scored, key=lambda x: x.get("total_score", 0), reverse=True)
    titles: list[str] = []
    for item in ordered[:max_n]:
        t = (item.get("title") or "").strip()
        if t:
            titles.append(t)
    return titles


def build_brief_context(
    persona: str,
    pipeline_results: Optional[dict] = None,
    request_id: Optional[str] = None,
    endpoint: str = "api.brief",
) -> ChatContext:
    """Retrieve news + alerts relevant to today's pipeline state and persona.

    The retrieval query is composed from:
      1. Up to N pipeline top-scored item titles (most signal-rich source).
      2. Persona top risk-category keywords (always present, even with no
         pipeline data — keeps the brief useful on cold starts).

    If both sources are empty (no pipeline, unknown persona), returns a
    context with `used=False` so /api/brief falls back to its legacy
    static news summary.
    """
    cfg = load_endpoint_config("brief")
    if not cfg.get("enabled", True):
        return ChatContext(prompt_text="", used=False)

    news_k = int(cfg.get("news_top_k", 8))
    alerts_k = int(cfg.get("alerts_top_k", 5))
    regulatory_k = int(cfg.get("regulatory_top_k", 4))
    alerts_min = int(cfg.get("alerts_min_count", 5))
    pipeline_max = int(cfg.get("pipeline_terms_max", 3))

    pipeline_terms = _pipeline_top_titles(pipeline_results, pipeline_max)
    persona_terms = _persona_query_terms(persona, top_n=4)

    query_parts: list[str] = []
    query_parts.extend(pipeline_terms)
    query_parts.extend(persona_terms)
    query = " | ".join(query_parts).strip()
    if not query:
        return ChatContext(prompt_text="", used=False)

    try:
        store = get_vector_store()
    except Exception:
        return ChatContext(prompt_text="", used=False)

    news_hits: list[SearchHit] = []
    alerts_hits: list[SearchHit] = []
    regulatory_hits: list[SearchHit] = []
    try:
        if news_k > 0:
            news_hits = store.search_news(
                query, n_results=news_k,
                request_id=request_id, endpoint=endpoint,
            )
    except Exception:
        news_hits = []

    try:
        if alerts_k > 0 and store.count("alerts") >= alerts_min:
            alerts_hits = store.search_alerts(
                query, n_results=alerts_k,
                request_id=request_id, endpoint=endpoint,
            )
    except Exception:
        alerts_hits = []

    try:
        if regulatory_k > 0:
            regulatory_hits = store.search_regulatory(
                query, n_results=regulatory_k,
                request_id=request_id, endpoint=endpoint,
            )
    except Exception:
        regulatory_hits = []

    if not news_hits and not alerts_hits and not regulatory_hits:
        return ChatContext(prompt_text="", used=False)

    blocks: list[str] = []
    citations: list[Citation] = []

    if news_hits:
        lines = [_news_line(i + 1, h) for i, h in enumerate(news_hits)]
        blocks.append("Recent relevant news:\n" + "\n".join(lines))
        citations.extend(_to_citation("news", h, f"N{i+1}") for i, h in enumerate(news_hits))

    if alerts_hits:
        lines = [_alert_line(i + 1, h) for i, h in enumerate(alerts_hits)]
        blocks.append("Active alerts of interest:\n" + "\n".join(lines))
        citations.extend(_to_citation("alerts", h, f"A{i+1}") for i, h in enumerate(alerts_hits))

    if regulatory_hits:
        lines = [_regulatory_line(i + 1, h) for i, h in enumerate(regulatory_hits)]
        blocks.append("Relevant regulatory changes:\n" + "\n".join(lines))
        citations.extend(_to_citation("regulatory", h, f"R{i+1}") for i, h in enumerate(regulatory_hits))

    blocks.append(
        "Ground the brief in the items above where relevant. Do not list them —"
        " synthesize them into the 3-5 sentence narrative. Skip any item that"
        " doesn't materially affect this persona's posture."
    )
    return ChatContext(
        prompt_text="\n\n".join(blocks),
        citations=citations,
        used=True,
        news_hits=len(news_hits),
        alerts_hits=len(alerts_hits),
        regulatory_hits=len(regulatory_hits),
    )


# ─────────────────────────────────────────────────────────────────────────
# Actions builder
# ─────────────────────────────────────────────────────────────────────────
@dataclass
class ActionsContext:
    """Distinct from ChatContext: actions has two prompt blocks (few-shot
    + regulatory) so the caller can splice them into different parts of
    its prompt template, plus a regulatory-hit count for the response."""
    few_shot_text: str = ""
    regulatory_text: str = ""
    citations: list[Citation] = field(default_factory=list)
    used: bool = False
    history_hits: int = 0
    regulatory_hits: int = 0


def _format_few_shot(hit: SearchHit, idx: int) -> str:
    """Render an actions_history hit as a compact few-shot example. The
    history docs already encode WHY/ACTION/PROS/CONS in a structured way,
    so we mostly just label them."""
    m = hit.metadata or {}
    header_bits = []
    if m.get("country"):  header_bits.append(str(m["country"]))
    if m.get("category"): header_bits.append(str(m["category"]))
    if m.get("generated_at"): header_bits.append(str(m["generated_at"])[:10])
    header = " | ".join(header_bits) or "previous run"
    body = (hit.document or "").strip()
    return f"Example {idx} ({header}):\n{body}"


def _format_regulatory(hit: SearchHit, idx: int) -> str:
    m = hit.metadata or {}
    header_bits = []
    if m.get("country"):  header_bits.append(str(m["country"]))
    if m.get("category"): header_bits.append(str(m["category"]))
    if m.get("severity"): header_bits.append(f"severity={m['severity']}")
    if m.get("effective_date"): header_bits.append(str(m["effective_date"]))
    header = " | ".join(header_bits) or "regulatory"
    body = _first_line(hit.document, 220)
    return f"[R{idx}] ({header}) {body}"


def build_actions_context(
    alerts: list[dict],
    persona: str,
    request_id: Optional[str] = None,
    endpoint: str = "api.actions",
) -> ActionsContext:
    """Pull few-shot history + regulatory evidence for an actions generation.

    Composes a single retrieval query from the alert titles plus the
    persona's risk-keyword expansion. Runs two queries:

      * actions_history → top-K become few-shot examples in the prompt.
        Skipped if the collection is too small to yield meaningful
        nearest neighbours.
      * regulatory → top-K become "supporting evidence" the LLM can
        cite to anchor its why_now sentences.

    Returns ActionsContext. Callers should treat `used=False` as a
    signal to fall through to the legacy actions prompt; behaviour
    parity with pre-RAG /api/actions/generate is preserved.
    """
    cfg = load_endpoint_config("actions")
    if not cfg.get("enabled", True):
        return ActionsContext()
    if not alerts:
        return ActionsContext()

    few_shot_k = int(cfg.get("few_shot_top_k", 3))
    regulatory_k = int(cfg.get("regulatory_top_k", 3))
    history_min = int(cfg.get("history_min_count", 5))

    # Compose query: alert titles carry the most signal; persona keywords
    # bias retrieval toward this user's risk priorities.
    alert_titles: list[str] = []
    for a in alerts[:6]:                          # cap to avoid an oversized query
        t = (a.get("title") or "").strip()
        if t:
            alert_titles.append(t)
    persona_terms = _persona_query_terms(persona, top_n=3)
    query = " | ".join(alert_titles + persona_terms).strip()
    if not query:
        return ActionsContext()

    try:
        store = get_vector_store()
    except Exception:
        return ActionsContext()

    history_hits: list[SearchHit] = []
    regulatory_hits: list[SearchHit] = []
    try:
        if few_shot_k > 0 and store.count("actions_history") >= history_min:
            history_hits = store.search_actions_history(
                query, n_results=few_shot_k,
                request_id=request_id, endpoint=endpoint,
            )
    except Exception:
        history_hits = []

    try:
        if regulatory_k > 0:
            regulatory_hits = store.search_regulatory(
                query, n_results=regulatory_k,
                request_id=request_id, endpoint=endpoint,
            )
    except Exception:
        regulatory_hits = []

    if not history_hits and not regulatory_hits:
        return ActionsContext()

    citations: list[Citation] = []
    few_shot_text = ""
    if history_hits:
        examples = [_format_few_shot(h, i + 1) for i, h in enumerate(history_hits)]
        few_shot_text = (
            "Past similar action cards from this system — match their tone, "
            "specificity, and the why_now / recommendation / pros / cons "
            "structure. Do NOT copy their content; treat them as style guides.\n\n"
            + "\n\n".join(examples)
        )
        citations.extend(_to_citation("actions_history", h, f"E{i+1}")
                         for i, h in enumerate(history_hits))

    regulatory_text = ""
    if regulatory_hits:
        lines = [_format_regulatory(h, i + 1) for i, h in enumerate(regulatory_hits)]
        regulatory_text = (
            "Recent regulatory developments relevant to these signals — quote a "
            "[R#] tag in the why_now field when one of these directly explains "
            "the timing or scope of a recommendation:\n"
            + "\n".join(lines)
        )
        citations.extend(_to_citation("regulatory", h, f"R{i+1}")
                         for i, h in enumerate(regulatory_hits))

    return ActionsContext(
        few_shot_text=few_shot_text,
        regulatory_text=regulatory_text,
        citations=citations,
        used=True,
        history_hits=len(history_hits),
        regulatory_hits=len(regulatory_hits),
    )
