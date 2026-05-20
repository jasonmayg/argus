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
    MIN_GUARDIAN_SCORE,
    _compute_effective_score,
)
from personas import persona_loader

# Guardian POC writes one timestamped JSON per fetch to this directory
# alongside overwriting alerts_latest.json. The directory is the
# authoritative history — alerts_latest.json holds only the most
# recent snapshot. For multi-day evaluation we walk the directory.
GUARDIAN_SNAPSHOTS_DIR = Path("guardian_poc/data/snapshots")


# Mirror the production exclude defaults so the evaluation measures the
# alert pool users actually see in the /api/actions UI rather than the
# raw Guardian feed (which is what _load_guardian_alerts in
# generate_actions filters BEFORE applying persona weights). Without
# matching these filters Phase 2.7 was measuring noise that prod users
# would never encounter — Copilot review on PR #46 caught this.
DEFAULT_EXCLUDE_GROUPS = ("weather", "disaster")


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
    """Stable identifier for de-duplication and citation.

    Uses (url + published_at) as a composite key rather than url alone:
    Guardian re-emits the same source URL on every fetch (e.g. an ECB
    FX-reference-rate endpoint shows up daily with the same URL), and
    each fetch is a distinct event we want to keep. (url, published_at)
    correctly collapses re-fetches of the same event while keeping
    daily reports of the "same indicator" distinct.

    Falls back to title prefix when URL is missing so headlineless
    duplicates still collapse."""
    url = (raw.get("url") or "").strip()
    pub_date = (raw.get("published_at") or raw.get("seendate") or "")[:10]
    if url:
        return f"{url}#{pub_date}" if pub_date else url
    title = (raw.get("title") or "").strip()
    return f"title:{title[:120]}#{pub_date}"


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


def _passes_prod_filters(
    alert: NormalizedAlert,
    *,
    min_score: float,
    exclude_groups: tuple[str, ...],
) -> bool:
    """Mirror the filter logic generate_actions._load_guardian_alerts
    applies before persona scoring runs in production. If the eval
    doesn't match these, persona_eval measures a different population
    than the production /api/actions path does, defeating the point.

    Logic transcribed from generate_actions._load_guardian_alerts:
      1. Drop below min_score
      2. If "weather" excluded: drop NOAA sources AND alerts whose
         title looks like a routine forecast/advisory
      3. If "disaster" excluded: drop alerts whose category contains
         "disaster"
    """
    if alert.score < min_score:
        return False
    if "weather" in exclude_groups:
        if alert.source in EXCLUDED_SOURCES:
            return False
        title_lower = alert.title.lower()
        if any(kw in title_lower for kw in
               ("advisory", "watch issued", "warning issued", "forecast")):
            return False
    if "disaster" in exclude_groups:
        if "disaster" in alert.category.lower():
            return False
    return True


def _read_snapshot_file(path: Path) -> List[Dict]:
    """Best-effort read of one Guardian snapshot JSON. Returns the
    `alerts` list, or [] on any I/O / parse error — a single corrupt
    file should never abort a multi-snapshot walk."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("alerts") or []
    except (OSError, json.JSONDecodeError):
        return []


def load_alerts_window(
    days: int = 30,
    *,
    snapshot_path: Optional[Path] = None,
    snapshots_dir: Optional[Path] = None,
    now: Optional[datetime] = None,
    min_score: Optional[float] = None,
    exclude_groups: Optional[tuple[str, ...]] = None,
    apply_prod_filters: bool = True,
) -> WindowSummary:
    """Read Guardian alerts and return everything within the window.

    Source-of-truth precedence:
      1. `snapshot_path`     — single file (used by tests + ad-hoc evals)
      2. `snapshots_dir`     — walk every JSON in this directory
      3. `GUARDIAN_SNAPSHOTS_DIR` (default) if it exists with files
      4. `GUARDIAN_PATH` (alerts_latest.json) as last resort

    The default path (#3) is what production callers want: each
    Guardian fetch writes a timestamped JSON to that dir, so multi-day
    evaluation needs ALL of them, not just the most recent overwrite.

    Production-parity filtering (`apply_prod_filters=True` by default)
    mirrors what generate_actions._load_guardian_alerts strips before
    persona scoring runs: alerts below MIN_GUARDIAN_SCORE plus weather
    + disaster groups. Pass `apply_prod_filters=False` (or `--include-
    all` on the CLI) for raw / diagnostic evaluation that ignores prod
    filtering — useful when you want to see what the LLM judge would
    say about the full upstream feed."""
    now = now or datetime.now(timezone.utc)
    window_end = now
    window_start = window_end - timedelta(days=days)
    effective_min_score = min_score if min_score is not None else MIN_GUARDIAN_SCORE
    effective_excludes = exclude_groups if exclude_groups is not None else DEFAULT_EXCLUDE_GROUPS

    # ── Collect raw alerts from chosen source ───────────────────
    raw_list: List[Dict] = []
    if snapshot_path is not None:
        raw_list = _read_snapshot_file(Path(snapshot_path))
    else:
        dir_to_scan: Optional[Path] = None
        if snapshots_dir is not None:
            dir_to_scan = Path(snapshots_dir)
        elif GUARDIAN_SNAPSHOTS_DIR.exists() and any(
            GUARDIAN_SNAPSHOTS_DIR.glob("alerts_*.json")
        ):
            dir_to_scan = GUARDIAN_SNAPSHOTS_DIR

        if dir_to_scan is not None and dir_to_scan.exists():
            # Walk newest-first so the dedup pass below keeps the
            # most recent occurrence of each (url, date). Without this,
            # older snapshots from before a scoring change (e.g. the
            # bridge's switch to a neutral persona on 2026-05-20) keep
            # masking newer ones with the same alert_id, silently
            # leaving persona_eval on stale data.
            for snap in sorted(dir_to_scan.glob("alerts_*.json"), reverse=True):
                raw_list.extend(_read_snapshot_file(snap))
        elif GUARDIAN_PATH.exists():
            raw_list = _read_snapshot_file(GUARDIAN_PATH)

    # ── Normalize, dedupe, window-filter, prod-parity filter ────
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
        if apply_prod_filters and not _passes_prod_filters(
            a, min_score=effective_min_score, exclude_groups=effective_excludes,
        ):
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
