from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from guardian import Event, GuardianEngine, ScoredAlert, demo_events, parse_country_weight_overrides

APP_TZ = ZoneInfo("America/Los_Angeles")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run daily Guardian snapshot and push alerts.")
    parser.add_argument("--persona", default="treasury_ops", choices=["fx_trader", "treasury_ops", "treasury_tech"])
    parser.add_argument("--country-weights", default="", help="e.g. China=1.5,India=1.2")
    parser.add_argument("--max-records", type=int, default=100)
    parser.add_argument("--config-dir", default="guardian_poc/config")
    parser.add_argument("--sources-config", default="guardian_poc/config/data_sources.yaml")
    parser.add_argument("--output-dir", default="guardian_poc/data/snapshots")
    parser.add_argument("--latest-path", default="guardian_poc/data/alerts_latest.json")
    parser.add_argument("--reports-dir", default="guardian_poc/reports")
    parser.add_argument("--webhook-url", default="")
    parser.add_argument("--webhook-timeout-sec", type=int, default=15)
    parser.add_argument("--llm-enable", action="store_true")
    parser.add_argument("--llm-model", default="gpt-4o-mini")
    parser.add_argument("--llm-max-output-tokens", type=int, default=220)
    parser.add_argument("--llm-max-input-chars", type=int, default=2800)
    parser.add_argument("--llm-max-alerts", type=int, default=8)
    parser.add_argument("--llm-timeout-sec", type=int, default=20)
    parser.add_argument("--fallback-demo-if-empty", action="store_true", help="Use demo events when live fetch returns 0.")
    return parser.parse_args()


def alert_to_dict(a: ScoredAlert) -> dict[str, Any]:
    row = asdict(a)
    row["published_at"] = a.published_at.isoformat()
    return row


def event_to_dict(e: Event, engine: GuardianEngine) -> dict[str, Any]:
    text = f"{e.title}\n{e.summary}".lower()
    category_keys = [k for k, _ in engine._detect_categories(text)]  # noqa: SLF001
    categories = [engine.risk_taxonomy[k]["label"] for k in category_keys]
    if not categories:
        inferred = [k for k, _ in engine._infer_categories(text=text, source=e.source)]  # noqa: SLF001
        category_keys = inferred
        categories = [engine.risk_taxonomy[k]["label"] for k in inferred]
    countries = engine._detect_countries(text)  # noqa: SLF001
    is_demo = str(e.event_id).startswith("demo-")
    suggestion_templates = {
        "market_risk": [
            "Tighten FX/rate intraday limits",
            "Run 5-day stress test on liquidity",
            "Review hedge coverage on key currencies",
        ],
        "credit_risk": [
            "Recheck counterparty limits and ratings",
            "Cut unsecured exposure in affected country",
            "Prepare fallback banking counterparties",
        ],
        "liquidity_risk": [
            "Increase local cash buffer",
            "Pre-fund critical outgoing payments",
            "Activate treasury liquidity watchlist",
        ],
        "regulatory_risk": [
            "Review cross-border payment routing",
            "Validate compliance impact with legal",
            "Prepare alternative repatriation path",
        ],
        "political_risk": [
            "Raise sanctions-screening frequency",
            "Trigger country-risk escalation call",
            "Review restricted-party exposure",
        ],
        "disaster_risk": [
            "Trigger BCP checklist for local ops",
            "Verify payment rail redundancy",
            "Confirm bank branch contingency coverage",
        ],
    }
    default_suggestions = [
        "Escalate to treasury risk review",
        "Monitor for 24h and reassess score",
        "Prepare contingency payment workflow",
    ]
    suggestion_options = default_suggestions
    for k in category_keys:
        if k in suggestion_templates:
            suggestion_options = suggestion_templates[k]
            break
    news_title = ""
    news_summary = ""
    news_url = ""
    if is_demo:
        first_country = countries[0] if countries else "Monitored country"
        cat_text = categories[0] if categories else "Risk Update"
        news_title = f"[Demo News] {first_country} {cat_text} Brief"
        news_summary = (
            f"Synthetic briefing generated for demo mode. "
            f"Event signals suggest elevated {cat_text.lower()} conditions for {first_country}."
        )
        news_url = e.url or f"https://example.com/demo-news/{first_country.lower()}"
    return {
        "source": e.source,
        "event_id": e.event_id,
        "title": e.title,
        "summary": e.summary,
        "url": e.url,
        "published_at": e.published_at.isoformat(),
        "countries": countries,
        "categories": categories,
        "is_demo_generated": is_demo,
        "news_title": news_title,
        "news_summary": news_summary,
        "news_url": news_url,
        "suggestion_options": suggestion_options,
    }


def post_webhook(url: str, payload: dict[str, Any], timeout_sec: int) -> bool:
    if not url:
        return False
    try:
        resp = requests.post(url, json=payload, timeout=timeout_sec)
        resp.raise_for_status()
        return True
    except Exception:
        return False


def build_data_avaliability(engine: GuardianEngine, events: list[Event]) -> list[dict[str, Any]]:
    status_rows = engine.get_source_status()
    counts = Counter(e.source for e in events)
    out: list[dict[str, Any]] = []
    for row in status_rows:
        source = str(row.get("source"))
        out.append(
            {
                "source": source,
                "tier": row.get("tier"),
                "enabled": bool(row.get("enabled")),
                "implemented": bool(row.get("implemented")),
                "events": int(counts.get(source, 0)),
                "healthy": bool(row.get("enabled")) and bool(row.get("implemented")) and int(counts.get(source, 0)) > 0,
            }
        )
    return out


def _risk_level(score: float) -> str:
    if score >= 70:
        return "HIGH"
    if score >= 40:
        return "MEDIUM"
    if score > 0:
        return "LOW"
    return "NONE"


def export_country_reports(snapshot: dict[str, Any], countries: list[str], reports_dir: str) -> list[str]:
    ts = pd.to_datetime(snapshot.get("generated_at_local") or snapshot.get("generated_at_utc"), errors="coerce")
    day_str = ts.strftime("%Y-%m-%d") if not pd.isna(ts) else datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    base = Path(reports_dir) / day_str
    base.mkdir(parents=True, exist_ok=True)

    alerts_df = pd.DataFrame(snapshot.get("alerts", []))
    events_df = pd.DataFrame(snapshot.get("events", []))
    if not alerts_df.empty and "published_at" in alerts_df.columns:
        alerts_df["published_at"] = pd.to_datetime(alerts_df["published_at"], errors="coerce")
    if not events_df.empty and "published_at" in events_df.columns:
        events_df["published_at"] = pd.to_datetime(events_df["published_at"], errors="coerce")
        if "countries" in events_df.columns:
            events_df["countries"] = events_df["countries"].apply(lambda x: x if isinstance(x, list) else [])

    written: list[str] = []
    for country in countries:
        c_alerts = alerts_df[alerts_df["country"] == country].copy() if not alerts_df.empty else pd.DataFrame()
        c_events = (
            events_df[events_df["countries"].apply(lambda xs: country in xs)].copy()
            if not events_df.empty and "countries" in events_df.columns
            else pd.DataFrame()
        )
        max_score = float(c_alerts["score"].max()) if not c_alerts.empty else 0.0
        avg_score = float(c_alerts["score"].mean()) if not c_alerts.empty else 0.0
        risk_level = _risk_level(max_score)
        high_count = int((c_alerts["score"] >= 70).sum()) if not c_alerts.empty else 0
        med_count = int(((c_alerts["score"] >= 40) & (c_alerts["score"] < 70)).sum()) if not c_alerts.empty else 0

        csv_path = base / f"{country}_daily_alerts.csv"
        md_path = base / f"{country}_daily_report.md"
        if c_alerts.empty:
            pd.DataFrame(columns=["country", "score", "category_label", "source", "published_at", "title", "recommendation", "url"]).to_csv(csv_path, index=False)
        else:
            cols = ["country", "score", "category_label", "source", "published_at", "title", "recommendation", "rationale", "url"]
            cols = [c for c in cols if c in c_alerts.columns]
            c_alerts.sort_values("score", ascending=False)[cols].to_csv(csv_path, index=False)

        lines = [
            f"# {country} Treasury Risk Daily Report",
            "",
            f"- Date (UTC): {day_str}",
            f"- Persona: {snapshot.get('persona')}",
            f"- Data mode: {snapshot.get('data_mode')}",
            f"- Risk level: {risk_level}",
            f"- Max score: {max_score:.2f}",
            f"- Avg score: {avg_score:.2f}",
            f"- Warnings: {0 if c_alerts.empty else len(c_alerts)} (High: {high_count}, Medium: {med_count})",
            f"- Related events: {0 if c_events.empty else len(c_events)}",
            "",
            "## Top Warnings",
        ]
        if c_alerts.empty:
            lines.append("No warnings for this country in current snapshot.")
        else:
            top = c_alerts.sort_values("score", ascending=False).head(10)
            for _, row in top.iterrows():
                pub = row.get("published_at")
                pub_s = pd.to_datetime(pub, errors="coerce")
                pub_text = pub_s.strftime("%Y-%m-%d %H:%M") if not pd.isna(pub_s) else str(pub)
                lines.append(
                    f"- [{row.get('score', 0):.2f}] {row.get('category_label', '-')}: {row.get('title', '-')}"
                    f" | {row.get('source', '-')}"
                    f" | {pub_text}"
                )
                lines.append(f"  - Recommendation: {row.get('recommendation', '-')}")
                url = row.get("url", "")
                if isinstance(url, str) and url:
                    lines.append(f"  - Link: {url}")
        lines.append("")
        lines.append("## Event Highlights")
        if c_events.empty:
            lines.append("No related raw events in current snapshot.")
        else:
            top_e = c_events.sort_values("published_at", ascending=False).head(10)
            for _, row in top_e.iterrows():
                pub = row.get("published_at")
                pub_s = pd.to_datetime(pub, errors="coerce")
                pub_text = pub_s.strftime("%Y-%m-%d %H:%M") if not pd.isna(pub_s) else str(pub)
                lines.append(f"- {row.get('title', '-')}")
                lines.append(f"  - Source: {row.get('source', '-')}, Time: {pub_text}")
                cats = row.get("categories", [])
                if isinstance(cats, list) and cats:
                    lines.append(f"  - Categories: {', '.join(cats)}")
                url = row.get("url", "")
                if isinstance(url, str) and url:
                    lines.append(f"  - Link: {url}")
        md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        written.extend([str(csv_path), str(md_path)])
    return written


def main() -> None:
    args = parse_args()
    now_local = datetime.now(tz=APP_TZ)
    now_utc = now_local.astimezone(timezone.utc)
    engine = GuardianEngine(
        countries_cfg=f"{args.config_dir}/countries.yaml",
        keywords_cfg=f"{args.config_dir}/risk_keywords.yaml",
        personas_cfg=f"{args.config_dir}/personas.yaml",
        sources_cfg=args.sources_config,
    )
    overrides = parse_country_weight_overrides(args.country_weights)
    events = engine.fetch_events(max_records=args.max_records)
    data_mode = "live"
    if not events and args.fallback_demo_if_empty:
        events = demo_events(list(engine.countries.keys()))
        data_mode = "demo_fallback"
    alerts = engine.score_events(events, persona=args.persona, country_overrides=overrides)
    threshold = float(engine.personas[args.persona]["alert_threshold"])

    llm_status: dict[str, Any] | None = None
    if args.llm_enable:
        llm_status = engine.enhance_alerts_with_llm(
            alerts=[a for a in alerts if a.score >= threshold],
            persona=args.persona,
            model=args.llm_model,
            max_output_tokens=args.llm_max_output_tokens,
            max_input_chars=args.llm_max_input_chars,
            max_alerts=args.llm_max_alerts,
            timeout_sec=args.llm_timeout_sec,
        )

    triggered = [a for a in alerts if a.score >= threshold]
    snapshot = {
        "generated_at_local": now_local.isoformat(),
        "generated_at_utc": now_utc.isoformat(),
        "timezone": "America/Los_Angeles",
        "persona": args.persona,
        "threshold": threshold,
        "data_mode": data_mode,
        "event_count": len(events),
        "alert_count": len(alerts),
        "triggered_count": len(triggered),
        "llm_status": llm_status,
        "data_avaliability": build_data_avaliability(engine, events),
        "events": [event_to_dict(e, engine) for e in events],
        "alerts": [alert_to_dict(a) for a in alerts],
    }

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    dated = out_dir / f"alerts_{now_local.strftime('%Y%m%d_%H%M%S')}.json"
    latest = Path(args.latest_path)
    latest.parent.mkdir(parents=True, exist_ok=True)
    dated.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    latest.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")

    webhook_url = args.webhook_url or os.getenv("GUARDIAN_WEBHOOK_URL", "").strip()
    webhook_payload = {
        "title": "Alphabet Treasury Guardian Alert",
        "generated_at_utc": snapshot["generated_at_utc"],
        "persona": args.persona,
        "triggered_count": len(triggered),
        "top_alerts": [alert_to_dict(a) for a in triggered[:5]],
    }
    pushed = post_webhook(webhook_url, webhook_payload, timeout_sec=args.webhook_timeout_sec)
    report_files = export_country_reports(snapshot, list(engine.countries.keys()), args.reports_dir)

    print(f"mode={data_mode} events={len(events)} alerts={len(alerts)} triggered={len(triggered)} webhook_pushed={pushed}")
    print(f"snapshot={dated}")
    print(f"latest={latest}")
    print(f"country_reports={len(report_files)} files at {Path(args.reports_dir).resolve()}")


if __name__ == "__main__":
    main()
