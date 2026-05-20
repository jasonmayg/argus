"""
Phase 3C — chat complexity router.

`/api/chat` has two backends:

  * **Legacy** (single-shot RAG + LLM call). Fast (~1-2 s), cheap
    (~$0.001/query), but can't iterate or follow up.
  * **ResponseAgent** (Phase 3A AgentRunner with tool use). Slower
    (~5-15 s, multiple turns), more expensive (~$0.05-0.20/query),
    but can plan retrievals, cross-reference, and reason.

The router decides which one runs. We use a heuristic score (no
extra LLM call — adding one would defeat the latency goal of
keeping simple queries on the cheap path):

  * **Length** — long questions usually deserve more thought.
  * **Analytical verbs** — "compare", "analyze", "summarize"
    signal multi-step reasoning.
  * **Connectives** — "across", "between", "versus" signal
    cross-source synthesis.
  * **Multi-entity** — mentioning 2+ countries/agencies signals
    the same.

Score >= COMPLEX_THRESHOLD → ResponseAgent.

Conservative by design — if we're unsure, we go simple, because
mis-routing a simple query to the agent costs money + latency while
mis-routing a complex one to legacy "merely" gives a less rich
answer (still useful). Tuneable via the threshold constant.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List

# Score threshold. Empirically chosen: a query needs at least two
# independent signals of complexity before we spend the agent budget.
# Bump up if cost overruns; bump down if too many complex queries
# slip through to legacy.
COMPLEX_THRESHOLD = 2

# Verbs that imply multi-step reasoning. Each match adds 1 to score.
# Kept short on purpose — generic words like "what" would tag almost
# everything. These trigger when the user explicitly asks for synthesis.
_ANALYTICAL_VERBS = (
    "compare",
    "contrast",
    "analyze",
    "analyse",
    "summarize",
    "summarise",
    "explain",
    "what's the impact",
    "what is the impact",
    "how does",
    "how do",
    "why did",
    "implications",
    "breakdown",
    "break down",
    "walk me through",
)

# Connectives that signal cross-source / cross-entity reasoning.
_CROSS_CONNECTIVES = (
    "across",
    "between",
    "versus",
    " vs ",
    "compared to",
    "compared with",
    "as well as",
    "alongside",
)

# Entity name fragments — when 2+ appear in the query the user is
# probably asking for a synthesis spanning them. The list is the
# canonical Argus universe (matches `countries.yaml` aliases +
# known regulator + central bank short names). Kept lowercase for
# substring match.
#
# We deliberately list ONLY the non-punctuated variants ("us", "uk")
# rather than dotted forms ("u.s.", "u.k.") — the dotted forms can't
# clean-match through `\b...\b` regex because `.` isn't a word
# character, so a `\bu.s.\b` pattern never matches as written. The
# non-punctuated forms cover the same usage in practice.
_KNOWN_ENTITIES = (
    "fed", "federal reserve",
    "ecb", "european central bank",
    "boe", "bank of england",
    "boj", "bank of japan",
    "boc", "bank of canada",
    "fca",
    "treasury",
    "imf",
    "world bank",
    "bis",
    "ofac",
    "us", "united states", "usa",
    "uk", "britain", "united kingdom",
    "eu", "europe", "eurozone", "germany", "france",
    "japan",
    "china",
    "canada",
    "mexico",
    "india",
    "brazil",
    "korea",
    "australia",
)

# Length thresholds (characters). Two-tier scoring:
#   medium (>= 80 chars)  → +1 (needs a SECOND signal to escalate)
#   long   (>= 180 chars) → +2 (escalates on length alone — at that
#                              point the user almost certainly wants
#                              a substantive multi-step answer).
# A short 79-char question pays no length cost regardless of how
# elaborate it sounds, so simple lookups stay on legacy.
_MEDIUM_LEN = 80
_LONG_LEN   = 180


@dataclass
class RoutingDecision:
    """What the router decided and why. The route layer logs this
    so we can later A/B the routing accuracy without re-running."""
    route:   str             # "simple" or "complex"
    score:   int             # raw heuristic score
    reasons: List[str]       # human-readable list ("length>80", "verb:compare", ...)


def classify(query: str) -> RoutingDecision:
    """Return RoutingDecision for `query`. Pure function — no LLM,
    no I/O. Empty / very short queries trivially route to simple."""
    q = (query or "").strip().lower()
    if not q:
        return RoutingDecision(route="simple", score=0, reasons=["empty"])

    score: int = 0
    reasons: List[str] = []

    # Length
    if len(q) >= _LONG_LEN:
        score += 2
        reasons.append(f"length>={_LONG_LEN}")
    elif len(q) >= _MEDIUM_LEN:
        score += 1
        reasons.append(f"length>={_MEDIUM_LEN}")

    # Analytical verbs — each conceptual verb contributes at most 1.
    # We walk the verb list longest-first and CONSUME the matched
    # span from a scratch copy of the query, so "how does" matching
    # doesn't leave "how do" behind to match again on the next
    # iteration. Without this, "how does X compare" used to score
    # twice on verbs ("how do" + "how does") and could mis-route
    # short questions into the expensive agent path.
    verb_scratch = q
    for v in sorted(_ANALYTICAL_VERBS, key=len, reverse=True):
        if v in verb_scratch:
            score += 1
            reasons.append(f"verb:{v}")
            verb_scratch = verb_scratch.replace(v, " ")

    # Cross-source connectives — at most 1 point even if multiple
    # are present (the signal is that the question spans things,
    # which is binary). We match the connective AS DEFINED (e.g.
    # " vs " with spaces, not "vs") so the substring check isn't
    # tripped by words like "verses" / "diverse". The label in
    # `reasons` keeps the trimmed form for readability.
    for c in _CROSS_CONNECTIVES:
        if c in q:
            score += 1
            reasons.append(f"connective:{c.strip()}")
            break

    # Multi-entity — count DISTINCT entity matches, then award
    # +1 if 2+ are present. Entities that fully contain each other
    # ("us" vs "united states") are deduped via the longer-first
    # iteration: once we match "united states", we strip it from
    # the scanning text so the inner "us" doesn't double-count.
    scratch = q
    hits: set[str] = set()
    for ent in sorted(_KNOWN_ENTITIES, key=len, reverse=True):
        # `\b` boundary means "us" doesn't catch "use" / "user", but
        # it ALSO means "treasury" doesn't catch "treasuries" — a
        # known limitation we live with; the false-negative rate on
        # plurals is acceptable vs the false-positive risk of
        # substring matching.
        pattern = r"\b" + re.escape(ent) + r"\b"
        if re.search(pattern, scratch):
            hits.add(ent)
            scratch = re.sub(pattern, " ", scratch)
    if len(hits) >= 2:
        score += 1
        reasons.append(f"entities:{len(hits)}")

    route = "complex" if score >= COMPLEX_THRESHOLD else "simple"
    return RoutingDecision(route=route, score=score, reasons=reasons)
