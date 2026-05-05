#!/usr/bin/env python3
"""
ARGUS: Unified Data Agent
Crawls all configured data sources and writes raw payloads to shared_storage/raw/
so the bridge pipeline and AnalysisAgent process every source identically.

Supported source keys
---------------------
federal_reserve  - Fed Reserve press releases (crawl4ai, markdown link extraction)
gcs              - Google Cloud Storage Guardian snapshot (alerts_latest.json)
gdelt            - GDELT global conflict/macro news  (GuardianEngine adapter)
usgs             - USGS significant earthquake feed  (GuardianEngine adapter)
noaa_nws         - NOAA National Weather Service alerts (GuardianEngine adapter)
nasa_eonet       - NASA EONET natural hazard events  (GuardianEngine adapter)
reliefweb        - UN OCHA ReliefWeb humanitarian reports (GuardianEngine adapter)
world_bank       - World Bank macro/CPI indicators   (GuardianEngine adapter)
bank_of_canada   - Bank of Canada daily FX rates     (GuardianEngine adapter)
fiscal_data      - US Treasury auction data          (GuardianEngine adapter)
sec_edgar        - SEC EDGAR corporate filings       (GuardianEngine adapter)
fred             - FRED economic indicators          (GuardianEngine adapter, requires FRED_API_KEY)
<https://...>    - Any custom URL (auto-crawled via crawl4ai)
"""

import asyncio
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urljoin

from config import RAW_DIR

# ── Federal Reserve crawler constants ────────────────────────────────────────
_FED_BASE_URL = "https://www.federalreserve.gov/newsevents/pressreleases.htm"
_FED_LINK_RE = re.compile(r'\[_?([^]_]+)_?\]\(([^)]+)\)')

_FED_KEYWORDS = [
    "Payment", "Payments", "Regulation", "Regulatory",
    "Bank", "Banking", "Banks", "Interest", "Rate", "Rates",
    "Treasury", "Monetary", "Financial", "Risk", "Stability", "Crisis",
]

# ── Date normalisation ────────────────────────────────────────────────────────
_DATE_FORMATS = ["%Y-%m-%d", "%B %d, %Y", "%b %d, %Y", "%m/%d/%Y"]


def _normalize_date(date_str: str) -> str:
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(date_str.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return date_str.strip()


# ── Fed press-release crawler (embedded, no external module dependency) ───────

async def _crawl_fed_url(url: str) -> List[Dict]:
    """Fetch a Fed press-release page and return normalised news items."""
    from crawl4ai import AsyncWebCrawler  # noqa: PLC0415 — optional heavy dep

    async with AsyncWebCrawler() as crawler:
        result = await crawler.arun(
            url=url,
            bypass_cache=True,
            wait_for="body",
            wait_until="networkidle",
            delay_before_return_ms=2000,
        )

    if not result.success:
        raise RuntimeError(f"crawl4ai failed for {url}: {result.error_message}")

    markdown = result.markdown or ""
    items: List[Dict] = []
    lines = markdown.split("\n")

    for i, line in enumerate(lines):
        line = line.strip()
        if not re.match(r"^\d{1,2}/\d{1,2}/\d{4}$", line):
            continue
        date_str = line
        for j in range(i + 1, len(lines)):
            next_line = lines[j].strip()
            if not next_line:
                continue
            m = _FED_LINK_RE.search(next_line)
            if m:
                title = m.group(1).strip()
                link = m.group(2).strip()
                if link.startswith("/"):
                    link = urljoin(_FED_BASE_URL, link)
                # keyword filter
                title_lower = title.lower()
                if any(kw.lower() in title_lower for kw in _FED_KEYWORDS):
                    items.append({
                        "date": _normalize_date(date_str),
                        "title": title,
                        "link": link,
                        "source": "federal_reserve",
                    })
            break  # only examine the next non-empty line per date entry

    return items


# ── GCS snapshot loader ────────────────────────────────────────────────────────

def _load_gcs_snapshot() -> List[Dict]:
    """Load Guardian alerts from a GCS snapshot and normalise to news items."""
    gcs_enabled = os.getenv("GUARDIAN_GCS_ENABLED", "true").strip().lower() not in {
        "0", "false", "no", "off"
    }
    if not gcs_enabled:
        return []

    bucket_name = os.getenv("GUARDIAN_GCS_BUCKET", "your-gcs-bucket").strip()
    prefix = os.getenv("GUARDIAN_GCS_PREFIX", "").strip().strip("/")
    try:
        from google.cloud import storage  # noqa: PLC0415

        client = storage.Client()
        bucket = client.bucket(bucket_name)
        candidates = [
            f"{prefix}/guardian_poc/data/alerts_latest.json".lstrip("/"),
            f"{prefix}/alerts_latest.json".lstrip("/"),
            "guardian_poc/data/alerts_latest.json",
        ]
        blob = None
        for path in candidates:
            b = bucket.blob(path)
            if b.exists():
                blob = b
                break
        if blob is None:
            return []
        snapshot = json.loads(blob.download_as_text(encoding="utf-8"))
        return _snapshot_to_items(snapshot)
    except Exception:
        return []


def _snapshot_to_items(snapshot: Dict) -> List[Dict]:
    items = []
    for alert in snapshot.get("alerts", []):
        title = str(alert.get("title") or "").strip()
        if not title:
            continue
        pub = str(alert.get("published_at") or "")
        date_str = pub[:10] if len(pub) >= 10 else datetime.now().strftime("%Y-%m-%d")
        items.append({
            "date": date_str,
            "title": title,
            "link": alert.get("url") or "",
            "source": alert.get("source") or "gcs",
            "summary": (alert.get("rationale") or alert.get("recommendation") or "")[:500],
        })
    return items


# ── Guardian POC adapter loader ────────────────────────────────────────────────
_GUARDIAN_ENGINE = None


def _get_guardian_engine():
    global _GUARDIAN_ENGINE
    if _GUARDIAN_ENGINE is not None:
        return _GUARDIAN_ENGINE
    import sys  # noqa: PLC0415

    guardian_src = Path(__file__).resolve().parent.parent / "guardian_poc" / "src"
    if str(guardian_src) not in sys.path:
        sys.path.insert(0, str(guardian_src))
    cfg_dir = Path(__file__).resolve().parent.parent / "guardian_poc" / "config"
    from guardian import GuardianEngine  # noqa: PLC0415

    _GUARDIAN_ENGINE = GuardianEngine(
        countries_cfg=str(cfg_dir / "countries.yaml"),
        keywords_cfg=str(cfg_dir / "risk_keywords.yaml"),
        personas_cfg=str(cfg_dir / "personas.yaml"),
        sources_cfg=str(cfg_dir / "data_sources.yaml"),
    )
    return _GUARDIAN_ENGINE


def _fetch_guardian_source(source_key: str) -> List[Dict]:
    engine = _get_guardian_engine()
    cfg = engine.sources.get(source_key, {})
    adapter = engine.source_adapters.get(source_key)
    if adapter is None:
        raise ValueError(f"No GuardianEngine adapter for '{source_key}'")
    events = adapter(source_key, cfg, 50)

    items = []
    for e in events:
        date_str = (
            e.published_at.strftime("%Y-%m-%d")
            if e.published_at
            else datetime.now().strftime("%Y-%m-%d")
        )
        items.append({
            "date": date_str,
            "title": e.title,
            "link": e.url or "",
            "source": e.source,
            "summary": (e.summary or "")[:500],
        })
    return items


# ── Bridge-contract writer ─────────────────────────────────────────────────────

def _write_to_raw(source_key: str, items: List[Dict]) -> Path:
    """Write items to shared_storage/raw/ using the bridge input contract."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = RAW_DIR / f"{source_key}_{ts}.json"
    payload = {
        "source": source_key,
        "crawled_at": datetime.now().isoformat(),
        "news_items": items,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


# ── Public DataAgent class ─────────────────────────────────────────────────────

GUARDIAN_API_SOURCES = {
    "gdelt", "usgs", "reliefweb", "noaa_nws", "nasa_eonet",
    "world_bank", "bank_of_canada", "fiscal_data", "sec_edgar", "fred",
}


class DataAgent:
    """Unified data agent: fetches from all sources, writes to shared_storage/raw/."""

    async def fetch(
        self,
        sources: Optional[List[str]] = None,
        url: Optional[str] = None,
        write_raw: bool = True,
    ) -> Dict:
        if not sources:
            if url and (url.startswith("http://") or url.startswith("https://")):
                sources = [url]
            else:
                sources = ["federal_reserve"]

        all_items: List[Dict] = []
        source_stats: Dict = {}
        paths: List[Path] = []

        for source_key in sources:
            try:
                if source_key == "federal_reserve":
                    # ★ Restored: only crawl current year for daily pipeline speed
                    fed_url = url or (
                        f"https://www.federalreserve.gov/newsevents/pressreleases/"
                        f"{datetime.now().year}-press.htm"
                    )
                    items = await _crawl_fed_url(fed_url)

                elif source_key == "gcs":
                    items = _load_gcs_snapshot()

                elif source_key in GUARDIAN_API_SOURCES:
                    items = _fetch_guardian_source(source_key)

                elif source_key.startswith("http://") or source_key.startswith("https://"):
                    items = await _crawl_fed_url(source_key)
                    for item in items:
                        item["source"] = source_key

                else:
                    source_stats[source_key] = {"count": 0, "status": "skipped",
                                                "error": "unknown source key"}
                    continue

                stat: Dict = {"count": len(items), "status": "success"}
                if write_raw and items:
                    key_slug = re.sub(r"[^a-z0-9_]", "_", source_key.lower())[:40]
                    p = _write_to_raw(key_slug, items)
                    stat["path"] = str(p)
                    paths.append(p)

                source_stats[source_key] = stat
                all_items.extend(items)

            except Exception as exc:
                source_stats[source_key] = {"count": 0, "status": "failed", "error": str(exc)}

        success_count = sum(1 for s in source_stats.values() if s["status"] == "success")
        if success_count == len(sources):
            status = "success"
        elif success_count > 0:
            status = "partial"
        else:
            status = "failed"

        return {
            "status": status,
            "total": len(all_items),
            "sources": source_stats,
            "paths": paths,
            "items": all_items,
        }