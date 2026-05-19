#!/usr/bin/env python3
"""
Phase 2.7 — persona weight evaluation CLI.

Usage:
    python scripts/persona_eval.py
    python scripts/persona_eval.py --days 60
    python scripts/persona_eval.py --since 2026-04-01 --until 2026-04-30
    python scripts/persona_eval.py --persona fx_manager
    python scripts/persona_eval.py --no-llm
    python scripts/persona_eval.py --dry-run

Exit codes:
    0  success
    1  insufficient data (< 50 alerts in window)
    2  all LLM judge calls failed
    3  unrecoverable error
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("persona_eval")

# Default personas to evaluate — keep in sync with personas/defaults.yaml
DEFAULT_PERSONAS = ["cash_manager", "fx_manager", "risk_manager", "treasury_ops"]
MIN_ALERTS_FOR_EVAL = 50

OUT_DIR = Path("data/persona_eval")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Evaluate persona weight effectiveness")
    p.add_argument("--days", type=int, default=30,
                   help="Window size in days (default: 30, ignored if --since given)")
    p.add_argument("--since", type=str, default=None,
                   help="ISO date — start of window (overrides --days)")
    p.add_argument("--until", type=str, default=None,
                   help="ISO date — end of window (defaults to today)")
    p.add_argument("--persona", action="append", default=None,
                   help="Single persona to evaluate; repeat for multiple")
    p.add_argument("--top-n", type=int, default=20,
                   help="Top-N alerts per persona (default: 20)")
    p.add_argument("--no-llm", action="store_true",
                   help="Skip Method B (LLM judge) — only run Method A")
    p.add_argument("--include-all", action="store_true",
                   help=("Skip production parity filtering (MIN_GUARDIAN_SCORE + "
                         "weather/disaster excludes). Use for raw / diagnostic "
                         "evaluation of the upstream feed, not for measuring "
                         "what users actually see."))
    p.add_argument("--dry-run", action="store_true",
                   help="Print report to stdout, do not write files or DB rows")
    p.add_argument("--out-dir", type=str, default=str(OUT_DIR),
                   help=f"Output dir for reports (default: {OUT_DIR})")
    return p.parse_args()


def main() -> int:
    args = _parse_args()

    try:
        from core.persona_eval import data_loader, differentiation, llm_judge, report, run_log
        from personas import persona_loader
    except Exception as e:
        log.error("import failed: %s", e)
        return 3

    # ── Window ─────────────────────────────────────────────────
    apply_filters = not args.include_all
    try:
        if args.since:
            since_dt = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)
            until_dt = (
                datetime.fromisoformat(args.until).replace(tzinfo=timezone.utc)
                if args.until else datetime.now(timezone.utc)
            )
            days = max(1, (until_dt - since_dt).days)
            window = data_loader.load_alerts_window(
                days=days, now=until_dt, apply_prod_filters=apply_filters,
            )
        else:
            window = data_loader.load_alerts_window(
                days=args.days, apply_prod_filters=apply_filters,
            )
    except Exception as e:
        log.error("failed to load window: %s", e)
        return 3

    log.info("Window: %s → %s", window.window_start, window.window_end)
    log.info("Alerts: %d kept / %d raw", window.kept_count, window.raw_count)
    if window.kept_count < MIN_ALERTS_FOR_EVAL:
        log.error(
            "insufficient data: %d alerts < %d threshold. "
            "Widen --days or load a backfill of historical Guardian snapshots.",
            window.kept_count, MIN_ALERTS_FOR_EVAL,
        )
        return 1

    # ── Per-persona scoring ───────────────────────────────────
    personas = args.persona or DEFAULT_PERSONAS
    scored_by_persona = {}
    top_ids_by_persona = {}
    for p in personas:
        scored = data_loader.score_for_persona(window.alerts, p)
        scored_by_persona[p] = scored
        top_ids_by_persona[p] = data_loader.top_n_ids(scored, n=args.top_n)
        log.info("  %s: top score=%.1f (n=%d)", p,
                 scored[0].effective_score if scored else 0, len(scored))

    # ── Method A ──────────────────────────────────────────────
    diff_report = differentiation.jaccard_matrix(top_ids_by_persona)
    log.info("Differentiation: avg jaccard=%.2f, max=%.2f",
             diff_report.avg_jaccard, diff_report.max_jaccard)
    flags = differentiation.flag_outliers(diff_report)
    for f in flags:
        log.warning("  %s", f)

    # ── Method B ──────────────────────────────────────────────
    judge_results = {}
    failed_personas: set[str] = set()
    if not args.no_llm:
        log.info("Running LLM judge (2 runs per persona)...")
        for p in personas:
            cfg = persona_loader.load_defaults(p) or {}
            weights = cfg.get("risk_weights") or {}
            scored = scored_by_persona[p]
            try:
                jr = llm_judge.evaluate_persona(p, scored, weights, runs=2)
            except Exception as e:
                log.warning("  %s: judge exception %s", p, e)
                failed_personas.add(p)
                continue
            # `jr` is non-None here; treat "all runs unparseable" as
            # the same failure mode as the exception branch above.
            if all(r.parse_error for r in jr.runs):
                log.warning("  %s: all runs failed to parse", p)
                failed_personas.add(p)
                continue
            judge_results[p] = jr
            log.info("  %s: %d consensus, %d disagreement",
                     p, len(jr.consensus_adjustments),
                     len(jr.disagreement_adjustments))

    if not args.no_llm and len(failed_personas) == len(personas):
        log.error("All LLM judge calls failed; aborting (run with --no-llm to skip Method B)")
        return 2

    # ── Build EvalRun ─────────────────────────────────────────
    run = report.EvalRun(
        run_at=datetime.now(timezone.utc).isoformat(),
        window=window,
        top_n=args.top_n,
        scored_by_persona=scored_by_persona,
        differentiation=diff_report,
        judge_results=judge_results,
        llm_model="claude-sonnet-4-6" if judge_results else None,
        notes=None,
    )

    md = report.render_markdown(run)
    js = report.render_json(run)

    if args.dry_run:
        log.info("\n========== MARKDOWN ==========\n%s", md)
        return 0

    # ── Write files ────────────────────────────────────────────
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    date_str = window.window_end
    md_path = out_dir / f"{date_str}.md"
    js_path = out_dir / f"{date_str}.json"
    md_path.write_text(md, encoding="utf-8")
    js_path.write_text(json.dumps(js, indent=2, default=str), encoding="utf-8")
    log.info("Report: %s", md_path)
    log.info("JSON:   %s", js_path)

    # ── Telemetry row ─────────────────────────────────────────
    run_log.log_run_safe(
        window_start=window.window_start,
        window_end=window.window_end,
        alerts_count=window.kept_count,
        avg_jaccard=diff_report.avg_jaccard,
        max_jaccard=diff_report.max_jaccard,
        llm_model=run.llm_model,
        llm_cost_usd=run.llm_cost_usd,
        report_md_path=str(md_path),
        report_json_path=str(js_path),
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
