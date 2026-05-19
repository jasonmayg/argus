"""
Phase 2.7 — data loader for persona evaluation.

Loads alerts from the Guardian snapshot JSON within a window, normalizes
them into the shape generate_actions._compute_effective_score expects,
and re-applies that exact function to score each alert under each
persona's weights.

The whole point of evaluation is to measure *the real production
scoring path*, so we deliberately import `_compute_effective_score`
and `EXCLUDED_SOURCES` from generate_actions rather than re-implementing
the formula here — any drift between eval and prod would invalidate
the diagnostic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from generate_actions import (
    EXCLUDED_SOURCES,
    GUARDIAN_PATH,
    _compute_effective_score,
)
from personas import persona_loader


@dataclass
class NormalizedAlert:
    """Alert shape generate_actions._compute_effective_score consumes.

    Mirrors what `_load_guardian_alerts` builds — we don't subclass /
    reuse its private fixture because tests need to build fakes too.
    """
    alert_id: str                # stable id (url, or hashed title fallback)
    title: str
    country: str
    category: str                # human label (e.g. "Liquidity Risk")
    score: float                 # base Guardian score, 0-100
    published_at: str            # ISO date or empty
    source: str                  # gdelt / usgs / sec / ...
    severity: Optional[str] = None
    url: str = ""


@dataclass
class ScoredAlert:
    alert: NormalizedAlert
    effective_score: float


@dataclass
class WindowSummary:
    """Returned by load_alerts_window. Reports what we kept vs filtered
    so the CLI can warn when the evaluation window is too sparse to be
    statistically meaningful."""
    alerts: List[NormalizedAlert]
    window_start: str            # ISO date
    window_end: str              # ISO date
    raw_count: int               # before filtering
    kept_count: int              # after filtering


# ──────────────────────────────────────────────────────────────────
# Loading
# ──────────────────────────────────────────────────────────────────

def _alert_id(raw: Dict) -> str:
    """Stable identifier for de-duplication and citation. URL is the
    most stable when present; falls back to title prefix so two alerts
    with identical titles collide (which is the right behavior — same
    headline twice in a window is usually a republish, not new signal)."""
    url = (raw.get("url") or "").strip()
    if url:
        return url
    title = (raw.get("title") or "").strip()
    return f"title:{title[:120]}"


def _normalize(raw: Dict) -> Optional[NormalizedAlert]:
    """Translate a raw alerts_latest.json entry into NormalizedAlert.
    Returns None when fields are missing in a way that would crash
    downstream scoring (no title or no score)."""
    title = (raw.get("title") or "").strip()
    if not title:
        return None
    try:
        score = float(raw.get("score", 0))
    except (TypeError, ValueError):
        return None
    return NormalizedAlert(
        alert_id=_alert_id(raw),
        title=title,
        country=raw.get("country") or "Global",
        category=(raw.get("category_label") or raw.get("category") or "General").strip(),
        score=score,
        published_at=(raw.get("published_at") or raw.get("seendate") or "").strip(),
        source=(raw.get("source") or "").strip().lower(),
        severity=raw.get("severity"),
        url=(raw.get("url") or "").strip(),
    )


def _within_window(alert: NormalizedAlert, window_start: datetime, window_end: datetime) -> bool:
    """published_at parsing is permissive — if a row has no parseable
    date we KEEP it rather than drop. Better to include slightly stale
    rows than to throw out signal because of malformed dates."""
    if not alert.published_at:
        return True
    s = alert.published_at[:10]  # YYYY-MM-DD prefix is what we get from Guardian
    try:
        dt = datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    return window_start <= dt <= window_end


def load_alerts_window(
    days: int = 30,
    *,
    snapshot_path: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> WindowSummary:
    """Read the Guardian snapshot, return a WindowSummary capturing what
    survived filtering. `snapshot_path` overrideable for tests."""
    path = Path(snapshot_path) if snapshot_path else GUARDIAN_PATH
    now = now or datetime.now(timezone.utc)
    window_end = now
    window_start = window_end - timedelta(days=days)

    if not path.exists():
        return WindowSummary(
            alerts=[], window_start=window_start.date().isoformat(),
            window_end=window_end.date().isoformat(),
            raw_count=0, kept_count=0,
        )

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return WindowSummary(
            alerts=[], window_start=window_start.date().isoformat(),
            window_end=window_end.date().isoformat(),
            raw_count=0, kept_count=0,
        )

    raw_list = data.get("alerts") or []
    kept: List[NormalizedAlert] = []
    seen_ids: set[str] = set()
    for raw in raw_list:
        a = _normalize(raw)
        if a is None:
            continue
        if a.alert_id in seen_ids:
            continue
        if not _within_window(a, window_start, window_end):
            continue
        seen_ids.add(a.alert_id)
        kept.append(a)

    return WindowSummary(
        alerts=kept,
        window_start=window_start.date().isoformat(),
        window_end=window_end.date().isoformat(),
        raw_count=len(raw_list),
        kept_count=len(kept),
    )


# ──────────────────────────────────────────────────────────────────
# Scoring (per-persona)
# ──────────────────────────────────────────────────────────────────

def score_for_persona(
    alerts: Iterable[NormalizedAlert],
    persona_key: str,
) -> List[ScoredAlert]:
    """Apply the persona's weights to each alert's base score using
    the exact production formula. Returns a new list sorted by
    effective_score descending so the caller can immediately slice
    top-N off the front."""
    persona_cfg = persona_loader.load_defaults(persona_key) or {}
    weights = persona_cfg.get("risk_weights") or {}

    scored: List[ScoredAlert] = []
    for a in alerts:
        # _compute_effective_score reads alert["category"] and
        # alert["score"]. We pass a dict view of the dataclass.
        eff = _compute_effective_score(
            {"category": a.category, "score": a.score},
            weights,
        )
        scored.append(ScoredAlert(alert=a, effective_score=eff))
    scored.sort(key=lambda s: s.effective_score, reverse=True)
    return scored


def top_n_ids(scored: List[ScoredAlert], n: int = 20) -> List[str]:
    """Return the top-N alert_ids in score order, used as input to
    the Jaccard calculations."""
    return [s.alert.alert_id for s in scored[:n]]
