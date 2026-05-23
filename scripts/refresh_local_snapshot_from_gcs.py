#!/usr/bin/env python3
"""
Bridge script — refresh `guardian_poc/data/alerts_latest.json` by
pulling today's raw alert rows from the GCS bucket
`your-gcs-bucket`, running them through Guardian's scoring
+ risk-classification pipeline, and writing the resulting snapshot
locally.

WHY: AI_Treasury_Project writes daily raw CSVs to GCS but doesn't
score / classify them. `generate_actions.py` and the persona_eval
tooling both consume *scored* Guardian alerts, and the local
guardian_poc has stopped running (its file has been stale since
2026-05-14). This script bridges the gap until the upstream pipeline
takes over (see `docs/GUARDIAN_SCORED_SNAPSHOT_HANDOFF.md`).

Usage:
    python scripts/refresh_local_snapshot_from_gcs.py
    python scripts/refresh_local_snapshot_from_gcs.py --dry-run
    python scripts/refresh_local_snapshot_from_gcs.py --persona fx_manager

Env required:
    GOOGLE_APPLICATION_CREDENTIALS  service account JSON path
    GUARDIAN_GCS_BUCKET             defaults to your-gcs-bucket

Exit codes:
    0  success (snapshot written, or dry-run completed)
    1  GCS unreachable / auth failure / no data
    2  scoring produced 0 alerts (likely a config / classification regression)
    3  unrecoverable error
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

LOG = logging.getLogger("refresh_snapshot")

# Map the human-readable `source` strings the GCS path emits onto the
# short keys Guardian's adapters use internally. Important because:
#   * `GuardianEngine._base_severity` only matches the short form
#     (e.g. `"ecb"` → 0.3 from PR #47; `"USGS"` would fall through to
#     the 0.6 default and we'd reintroduce noise).
#   * `_infer_categories` has source-default category fallbacks keyed
#     on the short form too.
# Anything not in this map is lowercased + underscored as a best-guess
# normalization (e.g. "Bank of England" → "bank_of_england").
_SOURCE_NORMALIZATION = {
    "European Central Bank":  "ecb",
    "Federal Reserve":        "fed",
    "U.S. Treasury":          "treasury",
    "Bank of England":        "boe",
    "Bank of Japan":          "boj",
    "Bank of Canada":         "bank_of_canada",
    "FCA UK":                 "fca",
    "USGS":                   "usgs",
}


# Synthetic summary hints — country only. We paste the GCS-tagged
# country into the Event's summary so `_detect_countries` picks it
# up (press-release titles rarely mention the country by name).
#
# Category hints were removed on 2026-05-20 because they hit
# `_detect_categories` primary keywords ("interest rate", etc.)
# which short-circuits the engine's category resolution chain and
# bypasses `_infer_categories` source_defaults. Without category
# hints the chain runs:
#   1. `_detect_categories(title only)` — typically returns []
#      for short press-release headlines
#   2. `_infer_categories(title, source)` — fires source_defaults
#      (e.g. fed → ["market_risk", "regulatory_risk"]) so each
#      press release fans out into multiple categories. That
#      fan-out is what gives persona weights room to discriminate.
#
# Phrasing here MUST match `guardian_poc/config/countries.yaml`
# country names / aliases.

# Output paths.
LATEST_PATH    = ROOT / "guardian_poc" / "data" / "alerts_latest.json"
SNAPSHOTS_DIR  = ROOT / "guardian_poc" / "data" / "snapshots"

# Guardian config (re-used as-is — these define the country list,
# risk taxonomy, persona weights).
CONFIG_DIR = ROOT / "guardian_poc" / "config"


def _normalize_source(raw: str) -> str:
    """Map GCS-emitted source string to Guardian short key."""
    if not raw:
        return ""
    if raw in _SOURCE_NORMALIZATION:
        return _SOURCE_NORMALIZATION[raw]
    # Best-effort fallback. Sources we don't know about get a stable
    # snake_case form so they're still groupable in downstream stats.
    return re.sub(r"[^a-z0-9]+", "_", raw.strip().lower()).strip("_")


def _parse_published_at(value: str) -> Optional[datetime]:
    """Parse the published_at field from a GCS alert. The raw CSVs
    use a mix of RFC 2822 (`Mon, 18 May 2026 09:06:58 +0100`) and
    ISO 8601. Returns None when neither parses — caller decides
    whether to drop or stamp with now().
    """
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
        if dt is not None:
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        pass
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _convert_to_events(raw_alerts):
    """Turn the alert-dict list from `_load_guardian_snapshot_from_gcs`
    into the `Event` shape `GuardianEngine.score_events` consumes.

    Drops rows that don't have a usable title (the bulk of GCS rows are
    `source=gcs_raw` econ-series rows with empty title — keeping them
    would just inflate the input without producing any scored alerts).
    """
    from guardian_poc.src.guardian import Event
    kept = []
    dropped_empty_title = 0
    dropped_bad_date = 0
    for raw in raw_alerts:
        title = (raw.get("title") or "").strip()
        if not title:
            dropped_empty_title += 1
            continue
        published = _parse_published_at(raw.get("published_at") or "")
        if published is None:
            # Without a date we'd default recency to "very old", which
            # is wrong — better to stamp with now() so the alert still
            # surfaces in today's eval window.
            published = datetime.now(timezone.utc)
            dropped_bad_date += 1
        norm_source = _normalize_source(raw.get("source") or "")
        # Country hint into summary so _detect_countries fires on
        # press-release titles that don't mention the country.
        # See the _SOURCE_CATEGORY_HINTS removal comment above for
        # why we deliberately don't inject category hints.
        country_hint = (raw.get("country") or "").strip()
        if country_hint and country_hint.lower() == "unknown":
            country_hint = ""
        summary = country_hint
        kept.append(Event(
            source=norm_source,
            event_id=(raw.get("url") or f"title:{title[:200]}"),
            title=title,
            summary=summary,
            url=(raw.get("url") or ""),
            published_at=published,
        ))
    LOG.info(
        "convert: kept=%d dropped_empty=%d unparseable_date=%d (stamped now())",
        len(kept), dropped_empty_title, dropped_bad_date,
    )
    return kept


def _build_snapshot(
    scored,
    *,
    event_count: int,
    persona: str,
    threshold: float,
    source_ref: str,
) -> dict:
    """Serialize the ScoredAlert list into the JSON shape
    `guardian_poc/data/alerts_latest.json` has historically used —
    the shape `core/persona_eval/data_loader._normalize` consumes.

    `event_count` is the count of input Guardian Events (pre-scoring).
    `alert_count` is the count of ScoredAlerts (post-scoring). These
    diverge whenever a single Event fans out to multiple alerts —
    e.g. a press release that mentions both "USA" and "UK", or one
    that matches both market_risk and regulatory_risk keywords.
    Dashboards historically chart event_count as "how much raw signal
    came in" and alert_count as "how many scored items downstream
    consumers see," so treating them as the same number was masking
    the fan-out behavior.
    """
    now_utc = datetime.now(timezone.utc)
    now_local = now_utc.astimezone()

    def _serialize(sa) -> dict:
        d = asdict(sa)
        # asdict gives published_at as a datetime — JSON can't serialize.
        if isinstance(d.get("published_at"), datetime):
            d["published_at"] = d["published_at"].isoformat()
        return d

    alerts = [_serialize(sa) for sa in scored]
    triggered = sum(1 for a in alerts if a["score"] >= threshold)
    return {
        "generated_at_utc":   now_utc.isoformat(),
        "generated_at_local": now_local.isoformat(),
        "timezone":           str(now_local.tzinfo or "local"),
        "persona":            persona,
        "threshold":          threshold,
        "data_mode":          "bridge_gcs_raw_to_scored",
        "source_ref":         source_ref,
        "event_count":        event_count,
        "alert_count":        len(scored),
        "triggered_count":    triggered,
        "llm_status":         None,
        "data_avaliability":  [],   # intentionally matches the original typo
        "events":             [],   # legacy field — left empty since we don't keep raw events
        "alerts":             alerts,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--persona", default="neutral",
                        help=("Persona key for scoring (default: neutral, all "
                              "weights = 1.0). The bridge stores baseline scores "
                              "so `_compute_effective_score` downstream can apply "
                              "the actual user persona's weights cleanly. Override "
                              "only if you know you want pre-weighted output."))
    parser.add_argument("--threshold", type=float, default=40.0,
                        help="Alert trigger threshold (informational only — written into snapshot)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print stats but don't write any files")
    parser.add_argument("--verbose", action="store_true",
                        help="Debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    try:
        from core.guardian_loader import _load_guardian_snapshot_from_gcs
    except Exception as e:
        LOG.error("import core.guardian_loader failed: %s", e)
        return 3

    LOG.info("loading from GCS …")
    gcs_result = _load_guardian_snapshot_from_gcs()
    if not gcs_result.get("ok"):
        LOG.error("GCS load failed: %s", gcs_result.get("message"))
        return 1
    snapshot = gcs_result.get("snapshot") or {}
    raw_alerts = snapshot.get("alerts") or []
    LOG.info("GCS returned %d raw alerts (ref=%s)",
             len(raw_alerts), gcs_result.get("snapshot_ref"))

    events = _convert_to_events(raw_alerts)
    if not events:
        LOG.error("no usable events after filtering — refusing to overwrite snapshot")
        return 1

    LOG.info("initializing GuardianEngine for scoring …")
    try:
        from guardian_poc.src.guardian import GuardianEngine
        engine = GuardianEngine(
            countries_cfg=str(CONFIG_DIR / "countries.yaml"),
            keywords_cfg=str(CONFIG_DIR / "risk_keywords.yaml"),
            personas_cfg=str(CONFIG_DIR / "personas.yaml"),
        )
    except Exception as e:
        LOG.exception("GuardianEngine init failed: %s", e)
        return 3

    # Validate persona up-front. `engine.score_events` does
    # `self.personas[persona]` which KeyErrors on unknown keys —
    # crashing with a traceback isn't useful. Listing the valid keys
    # also lets a user catch a typo without grepping the YAML.
    if args.persona not in engine.personas:
        LOG.error(
            "unknown --persona %r. Valid personas: %s",
            args.persona, ", ".join(sorted(engine.personas.keys())),
        )
        return 3

    scored = engine.score_events(events, persona=args.persona)
    LOG.info("scored %d alerts (from %d events)", len(scored), len(events))
    if not scored:
        # 0-scored is a real signal — either the GCS data has no
        # keyword-matching content today, or the classification
        # config regressed. Surface as exit 2 so callers can detect
        # and not silently overwrite a previously-good snapshot.
        LOG.error("scoring produced 0 alerts — refusing to overwrite latest")
        return 2

    out = _build_snapshot(
        scored,
        event_count=len(events),
        persona=args.persona,
        threshold=args.threshold,
        source_ref=gcs_result.get("snapshot_ref") or "",
    )

    by_source = {}
    by_category = {}
    for a in out["alerts"]:
        by_source[a["source"]] = by_source.get(a["source"], 0) + 1
        by_category[a["category_label"]] = by_category.get(a["category_label"], 0) + 1
    LOG.info("source distribution: %s",
             ", ".join(f"{k}={v}" for k, v in sorted(by_source.items(), key=lambda x: -x[1])))
    LOG.info("category distribution: %s",
             ", ".join(f"{k}={v}" for k, v in sorted(by_category.items(), key=lambda x: -x[1])))

    if args.dry_run:
        LOG.info("--dry-run: not writing any files. Would have written:")
        LOG.info("  %s (%d alerts)", LATEST_PATH, len(out["alerts"]))
        return 0

    LATEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    LATEST_PATH.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    LOG.info("wrote %s", LATEST_PATH)

    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    snap_path = SNAPSHOTS_DIR / f"alerts_{stamp}.json"
    snap_path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    LOG.info("wrote %s", snap_path)

    # Precompute the rescored snapshot so the dashboard doesn't pay the
    # ~30s ML cost on every request. Import is inside main() because
    # `core.newsalerts` pulls in the ML scorer on import, and we want
    # the bridge to keep working in environments without ML deps
    # (it just won't generate the cache file there — the dashboard's
    # fallback path handles that).
    try:
        from core.newsalerts import write_scored_snapshot
        scored_path = write_scored_snapshot(LATEST_PATH)
        if scored_path is not None:
            LOG.info("wrote %s (precomputed dashboard scores)", scored_path)
    except Exception as exc:
        # A failure here is non-fatal — the dashboard will fall back
        # to live rescoring (slow but correct). Log loudly so
        # operators notice the precompute regressed.
        LOG.warning("precompute scored snapshot failed: %s", exc)

    return 0


if __name__ == "__main__":
    sys.exit(main())
