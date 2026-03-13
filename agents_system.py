#!/usr/bin/env python3
"""
ARGUS Multi-Agent System
Complete agent-based architecture for Federal Reserve news monitoring and risk analysis
"""

import asyncio
import json
import os
from datetime import datetime
from typing import List, Dict, Optional
from abc import ABC, abstractmethod

from fed_news_crawler import FedNewsCrawler
from risk_classifier import RiskClassifier
from fed_news_db import FedNewsDatabase
from risk_matrix import (
    calculate_risk_level,
    PROBABILITY_LABELS,
    IMPACT_LABELS,
    score_to_probability,
    score_to_impact,
)


class Agent(ABC):
    """Base Agent Class"""

    def __init__(self, name: str):
        """
        Initialize agent

        Args:
            name: Agent name
        """
        self.name = name
        self.status = "idle"
        self.logs = []

    def log(self, message: str, level: str = "INFO"):
        """Log agent activity"""
        timestamp = datetime.now().isoformat()
        log_entry = {
            "timestamp": timestamp,
            "agent": self.name,
            "level": level,
            "message": message
        }
        self.logs.append(log_entry)
        print(f"[{self.name}] {level}: {message}")

    @abstractmethod
    async def execute(self, **kwargs):
        """Execute agent task"""
        pass

    def get_logs(self):
        """Get agent logs"""
        return self.logs


class NewsGathererAgent(Agent):
    """Multi-source news and events gatherer.

    Supported source keys for the `sources` parameter:
        'federal_reserve'  - Fed Reserve press releases (default, uses FedNewsCrawler)
        'gcs'              - Google Cloud Storage Guardian snapshot (alerts_latest.json)
        'gdelt'            - GDELT global conflict/macro news
        'usgs'             - USGS significant earthquake feed
        'noaa_nws'         - NOAA National Weather Service active alerts
        'nasa_eonet'       - NASA EONET natural hazard events
        'reliefweb'        - UN OCHA ReliefWeb humanitarian reports
        'world_bank'       - World Bank macro/CPI indicators
        'bank_of_canada'   - Bank of Canada daily FX rates
        'fiscal_data'      - US Treasury auction data
        'sec_edgar'        - SEC EDGAR corporate filings
        'fred'             - FRED economic indicators (requires FRED_API_KEY env var)
        '<https://...>'    - Any custom URL (auto-detected, crawled via FedNewsCrawler)
    """

    SOURCE_FEDERAL_RESERVE = "federal_reserve"
    SOURCE_GCS = "gcs"

    # Guardian POC adapter source keys (map to GuardianEngine._adapter_* methods)
    GUARDIAN_API_SOURCES = {
        "gdelt", "usgs", "reliefweb", "noaa_nws", "nasa_eonet",
        "world_bank", "bank_of_canada", "fiscal_data", "sec_edgar", "fred",
    }

    DEFAULT_SOURCES = [SOURCE_FEDERAL_RESERVE]

    def __init__(self):
        super().__init__("NewsGatherer")
        self.crawler = FedNewsCrawler()
        self.news_data = []
        self._guardian_engine = None  # lazy-loaded on first use

    def _get_guardian_engine(self):
        """Lazy-load GuardianEngine from guardian_poc/src."""
        if self._guardian_engine is not None:
            return self._guardian_engine
        import sys
        from pathlib import Path
        guardian_src = Path(__file__).parent / "guardian_poc" / "src"
        if str(guardian_src) not in sys.path:
            sys.path.insert(0, str(guardian_src))
        cfg_dir = Path(__file__).parent / "guardian_poc" / "config"
        from guardian import GuardianEngine  # noqa: PLC0415
        self._guardian_engine = GuardianEngine(
            countries_cfg=str(cfg_dir / "countries.yaml"),
            keywords_cfg=str(cfg_dir / "risk_keywords.yaml"),
            personas_cfg=str(cfg_dir / "personas.yaml"),
            sources_cfg=str(cfg_dir / "data_sources.yaml"),
        )
        return self._guardian_engine

    def _event_to_news_item(self, event) -> Dict:
        """Normalize a GuardianEngine Event to the standard news item dict."""
        date_str = (
            event.published_at.strftime("%Y-%m-%d")
            if event.published_at
            else datetime.now().strftime("%Y-%m-%d")
        )
        return {
            "date": date_str,
            "title": event.title,
            "link": event.url or "",
            "source": event.source,
            "summary": (event.summary or "")[:500],
        }

    def _load_gcs_snapshot(self) -> List[Dict]:
        """Load Guardian alerts from a GCS snapshot and normalize to news items."""
        import os
        import json as _json
        gcs_enabled = os.getenv("GUARDIAN_GCS_ENABLED", "true").strip().lower() not in {
            "0", "false", "no", "off"
        }
        if not gcs_enabled:
            self.log("GCS skipped: GUARDIAN_GCS_ENABLED is false", "WARN")
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
                    self.log(f"GCS: found snapshot at gs://{bucket_name}/{path}")
                    break
            if blob is None:
                self.log(f"GCS: no alerts_latest.json found in gs://{bucket_name}", "WARN")
                return []
            snapshot = _json.loads(blob.download_as_text(encoding="utf-8"))
            return self._snapshot_to_news_items(snapshot)
        except Exception as exc:
            self.log(f"GCS load failed: {exc}", "ERROR")
            return []

    def _snapshot_to_news_items(self, snapshot: Dict) -> List[Dict]:
        """Convert Guardian snapshot alert list to normalized news items."""
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

    async def _fetch_federal_reserve(self, url: Optional[str] = None) -> List[Dict]:
        """Fetch Fed Reserve press releases via FedNewsCrawler."""
        if not url:
            year = datetime.now().year
            url = f"https://www.federalreserve.gov/newsevents/pressreleases/{year}-press.htm"
        self.log(f"Crawling: {url}")
        result = await self.crawler.crawl(url=url)
        if result["status"] != "success":
            raise RuntimeError(result.get("error", "Crawl returned non-success status"))
        items = result["json_data"]
        for item in items:
            item.setdefault("source", self.SOURCE_FEDERAL_RESERVE)
        return items

    def _fetch_guardian_source(self, source_key: str) -> List[Dict]:
        """Fetch events from a Guardian POC API adapter and normalize to news items."""
        engine = self._get_guardian_engine()
        cfg = engine.sources.get(source_key, {})
        adapter = engine.source_adapters.get(source_key)
        if adapter is None:
            raise ValueError(f"No adapter registered in GuardianEngine for source '{source_key}'")
        events = adapter(source_key, cfg, 50)
        return [self._event_to_news_item(e) for e in events]

    async def execute(
        self,
        url: Optional[str] = None,
        sources: Optional[List[str]] = None,
        **kwargs,
    ):
        """Gather news and events from one or more data sources.

        Args:
            url:     Optional URL; used with 'federal_reserve' source, or as a
                     custom URL source (any https:// string is auto-detected).
            sources: List of source keys to activate. Defaults to ['federal_reserve'].
                     Pass multiple to merge results, e.g.
                     ['federal_reserve', 'gdelt', 'usgs', 'gcs']
        """
        self.status = "running"
        self.log("Starting multi-source news gathering...")

        # Resolve source list
        if not sources:
            sources = [self.SOURCE_FEDERAL_RESERVE] if not url else [self.SOURCE_FEDERAL_RESERVE]

        all_items: List[Dict] = []
        source_stats: Dict = {}

        for source_key in sources:
            try:
                if source_key == self.SOURCE_FEDERAL_RESERVE:
                    items = await self._fetch_federal_reserve(url)
                elif source_key == self.SOURCE_GCS:
                    items = self._load_gcs_snapshot()
                elif source_key in self.GUARDIAN_API_SOURCES:
                    items = self._fetch_guardian_source(source_key)
                elif source_key.startswith("http://") or source_key.startswith("https://"):
                    # Treat raw URL as a custom crawl target
                    items = await self._fetch_federal_reserve(source_key)
                else:
                    self.log(f"Unknown source '{source_key}' — skipping", "WARN")
                    source_stats[source_key] = {"count": 0, "status": "skipped"}
                    continue

                source_stats[source_key] = {"count": len(items), "status": "success"}
                self.log(f"Source '{source_key}': {len(items)} items")
                all_items.extend(items)

            except Exception as exc:
                self.log(f"Source '{source_key}' failed: {exc}", "ERROR")
                source_stats[source_key] = {"count": 0, "status": "failed", "error": str(exc)}

        self.news_data = all_items
        total = len(all_items)

        if total > 0:
            self.status = "success"
            self.log(f"Gathered {total} total items from {len(source_stats)} source(s)")
            return {
                "status": "success",
                "count": total,
                "data": all_items,
                "sources": source_stats,
            }

        self.status = "failed"
        self.log("No items gathered from any source", "ERROR")
        return {
            "status": "failed",
            "count": 0,
            "data": [],
            "sources": source_stats,
            "error": "No items gathered from any configured source",
        }


class RiskClassifierAgent(Agent):
    """Agent for classifying risks in news"""

    def __init__(self):
        super().__init__("RiskClassifier")
        self.classifier = RiskClassifier()
        self.classified_news = []

    async def execute(self, news_items: List[Dict], **kwargs):
        """Classify news items by risk"""
        self.status = "running"
        self.log(f"Starting classification of {len(news_items)} items")

        try:
            self.classified_news = []

            for news in news_items:
                classification = self.classifier.classify_news(news['title'])
                risk_level = self.classifier.get_risk_level(classification['risk_score'])

                classified_item = {
                    **news,
                    'risk_category': classification['primary_category'],
                    'risk_score': classification['risk_score'],
                    'risk_level': risk_level,
                    'matched_keywords': list(classification['matched_keywords'].keys())
                }
                self.classified_news.append(classified_item)

            self.log(f"Successfully classified {len(self.classified_news)} items")
            self.status = "success"

            # Count by risk level
            by_level = {}
            for item in self.classified_news:
                level = item['risk_level']
                by_level[level] = by_level.get(level, 0) + 1

            self.log(f"Distribution: {by_level}")

            return {
                "status": "success",
                "count": len(self.classified_news),
                "data": self.classified_news,
                "distribution": by_level
            }

        except Exception as e:
            self.log(f"Classification failed: {str(e)}", "ERROR")
            self.status = "failed"
            return {"status": "failed", "error": str(e)}


class DatabaseAgent(Agent):
    """Agent for database operations"""

    def __init__(self):
        super().__init__("DatabaseManager")
        self.records_stored = 0

    async def execute(self, action: str, data: Optional[Dict] = None, **kwargs):
        """Execute database operations"""
        self.status = "running"
        self.log(f"Executing database action: {action}")

        # Create fresh database connection for this thread (SQLite threading fix)
        db = FedNewsDatabase()

        try:
            if action == "insert":
                inserted = db.insert_news(data['news_items'])
                self.records_stored += inserted
                self.log(f"Inserted {inserted} records")
                return {"status": "success", "inserted": inserted}

            elif action == "classify":
                keywords = data.get('keywords', [])
                db.add_keyword_matches(keywords)
                self.log(f"Added keyword matches")
                return {"status": "success"}

            elif action == "query":
                query_type = data.get('type')
                if query_type == 'statistics':
                    stats = db.get_statistics()
                    self.log(f"Retrieved statistics: {stats['total_news']} total records")
                    return {"status": "success", "data": stats}

            self.status = "success"
            return {"status": "success"}

        except Exception as e:
            self.log(f"Database operation failed: {str(e)}", "ERROR")
            self.status = "failed"
            return {"status": "failed", "error": str(e)}

        finally:
            db.close()


class AnalysisAgent(Agent):
    """Agent for 2D Probability × Impact risk matrix analysis.

    For every classified news item the agent assigns:
      - Probability  (1=Unlikely … 5=Frequent)  — likelihood of occurrence
      - Impact       (1=Negligible … 5=Catastrophic) — severity for Treasury

    Scores are obtained by calling the Claude API when an ANTHROPIC_API_KEY is
    present; otherwise a keyword-score heuristic is used as a fallback.
    The final risk level (Low / Medium / High / Extreme) is derived from the
    5×5 matrix defined in risk_matrix.py.
    """

    # Maximum items sent to the LLM in one batch (keeps token cost predictable)
    _LLM_BATCH_LIMIT = 30

    def __init__(self):
        super().__init__("RiskAnalyzer")
        self.analysis_results = []
        self._api_key = os.getenv("ANTHROPIC_API_KEY")

    # ── Public entry point ────────────────────────────────────────────────────

    async def execute(self, classified_news: List[Dict], **kwargs):
        """Score every item on the P×I matrix and aggregate results."""
        self.status = "running"
        self.log(f"Starting 2D risk matrix analysis of {len(classified_news)} items")

        try:
            scored_items = await self._score_items(classified_news)

            extreme_items = [n for n in scored_items if n.get("matrix_level") == "Extreme"]
            high_items    = [n for n in scored_items if n.get("matrix_level") == "High"]
            medium_items  = [n for n in scored_items if n.get("matrix_level") == "Medium"]
            low_items     = [n for n in scored_items if n.get("matrix_level") == "Low"]

            llm_used = any(item.get("llm_scored") for item in scored_items)
            self.log(
                f"Matrix results (LLM={'yes' if llm_used else 'heuristic'}) — "
                f"Extreme: {len(extreme_items)}, High: {len(high_items)}, "
                f"Medium: {len(medium_items)}, Low: {len(low_items)}"
            )

            by_category: Dict[str, list] = {}
            for item in scored_items:
                cat = item.get("risk_category", "Unknown")
                by_category.setdefault(cat, []).append(item)

            analysis = {
                "total_items":   len(scored_items),
                "scored_items":  scored_items,
                "llm_scored":    llm_used,
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
            self.log("2D risk matrix analysis completed successfully")
            self.status = "success"
            return {"status": "success", "analysis": analysis}

        except Exception as e:
            self.log(f"Analysis failed: {str(e)}", "ERROR")
            self.status = "failed"
            return {"status": "failed", "error": str(e)}

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
        """Call Claude to assign Probability and Impact for all items.

        Items beyond _LLM_BATCH_LIMIT are scored heuristically to cap cost.
        """
        import anthropic  # imported here so the module loads without the SDK installed

        client = anthropic.Anthropic(api_key=self._api_key)
        llm_items = news_items[: self._LLM_BATCH_LIMIT]

        items_text = "\n".join(
            f"{i + 1}. [{item.get('date', '')}] {item.get('title', '')} "
            f"(keyword risk score: {item.get('risk_score', 0):.0f}/100)"
            for i, item in enumerate(llm_items)
        )

        prompt = f"""You are a senior treasury risk analyst.

For each news item below, assess two independent dimensions:

**Probability** — likelihood this event materialises or escalates within the next 90 days:
  1 = Unlikely  |  2 = Seldom  |  3 = Occasional  |  4 = Likely  |  5 = Frequent

**Impact** — severity on Treasury operations, liquidity, and fixed-income portfolio if it occurs:
  1 = Negligible  |  2 = Minor  |  3 = Moderate  |  4 = Hazardous  |  5 = Catastrophic

News items:
{items_text}

Return a JSON array — one object per item, in the same order:
[
  {{
    "index": 1,
    "probability": <int 1-5>,
    "probability_label": "<label>",
    "probability_reasoning": "<one concise sentence explaining your probability rating>",
    "impact": <int 1-5>,
    "impact_label": "<label>",
    "impact_reasoning": "<one concise sentence explaining your impact rating>"
  }},
  ...
]

Return ONLY the JSON array — no preamble, no explanation, no markdown fences."""

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )

        raw = response.content[0].text.strip()
        # Strip optional markdown code fences
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
                s = scores[i]
                p   = max(1, min(5, int(s.get("probability", 1))))
                imp = max(1, min(5, int(s.get("impact", 1))))
                matrix = calculate_risk_level(p, imp)
                item.update({
                    "probability":             p,
                    "probability_label":       PROBABILITY_LABELS[p],
                    "probability_reasoning":   s.get("probability_reasoning", ""),
                    "impact":                  imp,
                    "impact_label":            IMPACT_LABELS[imp],
                    "impact_reasoning":        s.get("impact_reasoning", ""),
                    "matrix_level":            matrix["level"],
                    "matrix_color":            matrix["color"],
                    "matrix_text_color":       matrix["text_color"],
                    "matrix_score":            matrix["matrix_score"],
                    "llm_scored":              True,
                })
            else:
                # Item beyond LLM batch limit — use heuristic
                item = self._apply_heuristic(item)
            result.append(item)
        return result

    # ── Heuristic fallback ────────────────────────────────────────────────────

    def _score_heuristic(self, news_items: List[Dict]) -> List[Dict]:
        """Derive P and I from the existing keyword risk_score (0–100)."""
        return [self._apply_heuristic(dict(item)) for item in news_items]

    def _apply_heuristic(self, item: Dict) -> Dict:
        rs     = float(item.get("risk_score") or 0)
        p      = score_to_probability(rs)
        imp    = score_to_impact(rs)
        matrix = calculate_risk_level(p, imp)
        item.update({
            "probability":           p,
            "probability_label":     PROBABILITY_LABELS[p],
            "probability_reasoning": f"Estimated from keyword risk score {rs:.0f}/100.",
            "impact":                imp,
            "impact_label":          IMPACT_LABELS[imp],
            "impact_reasoning":      f"Estimated from keyword risk score {rs:.0f}/100.",
            "matrix_level":          matrix["level"],
            "matrix_color":          matrix["color"],
            "matrix_text_color":     matrix["text_color"],
            "matrix_score":          matrix["matrix_score"],
            "llm_scored":            False,
        })
        return item

    # ── Aggregation helpers ───────────────────────────────────────────────────

    def _build_matrix_summary(self, scored_items: List[Dict]) -> List[List[int]]:
        """Return a 5×5 count grid (probability × impact) for the heatmap."""
        grid = [[0] * 5 for _ in range(5)]
        for item in scored_items:
            p   = max(1, min(5, int(item.get("probability", 1))))
            imp = max(1, min(5, int(item.get("impact",      1))))
            grid[p - 1][imp - 1] += 1
        return grid

    def _extract_top_keywords(self, news_items: List[Dict], top_n: int = 10) -> List[str]:
        """Return the top-N most frequent matched keywords."""
        counts: Dict[str, int] = {}
        for item in news_items:
            for kw in item.get("matched_keywords", []):
                counts[kw] = counts.get(kw, 0) + 1
        return [kw for kw, _ in sorted(counts.items(), key=lambda x: x[1], reverse=True)[:top_n]]


class ReportingAgent(Agent):
    """Agent for generating reports"""

    def __init__(self):
        super().__init__("Reporter")
        self.reports = []

    async def execute(self, analysis_data: Dict, **kwargs):
        """Generate report from analysis"""
        self.status = "running"
        self.log("Generating report...")

        try:
            by_level = analysis_data.get("by_risk_level", {})
            report = {
                "timestamp": datetime.now().isoformat(),
                "title": "Federal Reserve News Risk Analysis Report",
                "summary": {
                    "total_items":   analysis_data.get("total_items", 0),
                    "extreme_count": len(by_level.get("extreme", [])),
                    "high_count":    len(by_level.get("high",    [])),
                    "medium_count":  len(by_level.get("medium",  [])),
                    "low_count":     len(by_level.get("low",     [])),
                    "llm_scored":    analysis_data.get("llm_scored", False),
                },
                "recommendations": self._generate_recommendations(analysis_data),
                "top_keywords": analysis_data.get("top_keywords", []),
                "category_distribution": analysis_data.get("by_category", {}),
            }

            self.reports.append(report)
            self.log("Report generated successfully")
            self.status = "success"

            return {"status": "success", "report": report}

        except Exception as e:
            self.log(f"Report generation failed: {str(e)}", "ERROR")
            self.status = "failed"
            return {"status": "failed", "error": str(e)}

    def _generate_recommendations(self, analysis: Dict) -> List[str]:
        """Generate recommendations based on 2D matrix analysis."""
        recommendations = []
        by_level = analysis.get("by_risk_level", {})

        extreme_count = len(by_level.get("extreme", []))
        if extreme_count > 0:
            recommendations.append(
                f"URGENT: {extreme_count} Extreme-risk item(s) detected "
                f"(Frequent/Likely × Hazardous/Catastrophic). Immediate management review required."
            )

        high_count = len(by_level.get("high", []))
        if high_count > 3:
            recommendations.append(
                f"{high_count} High-risk items on the matrix. Escalate to treasury risk committee."
            )

        for category, count in analysis.get("by_category", {}).items():
            if count > 5:
                recommendations.append(f"Monitor {category} closely: {count} items flagged.")

        scored = analysis.get("scored_items", [])
        high_impact = [s for s in scored if s.get("impact", 0) >= 4]
        if high_impact:
            recommendations.append(
                f"{len(high_impact)} item(s) rated Hazardous or Catastrophic impact — "
                f"review treasury exposure regardless of probability."
            )

        if not recommendations:
            recommendations.append("Risk profile within normal parameters. Continue routine monitoring.")

        return recommendations


class AgentOrchestrator:
    """Master orchestrator for coordinating all agents"""

    def __init__(self):
        self.news_gatherer = NewsGathererAgent()
        self.risk_classifier = RiskClassifierAgent()
        self.database_manager = DatabaseAgent()
        self.analyzer = AnalysisAgent()
        self.reporter = ReportingAgent()
        self.execution_log = []

    async def execute_pipeline(self, **kwargs) -> Dict:
        """Execute complete analysis pipeline"""
        print("\n" + "="*70)
        print("ARGUS MULTI-AGENT SYSTEM - EXECUTION PIPELINE")
        print("="*70)

        result = {
            "status": "running",
            "timestamp": datetime.now().isoformat(),
            "stages": {}
        }

        try:
            # Stage 1: Gather News
            print("\n[STAGE 1] News Gathering...")
            news_result = await self.news_gatherer.execute(**kwargs)
            result['stages']['news_gathering'] = news_result
            if news_result['status'] != 'success':
                return self._finalize_result(result, 'failed')

            # Stage 2: Classify Risks
            print("\n[STAGE 2] Risk Classification...")
            classify_result = await self.risk_classifier.execute(
                news_items=news_result['data']
            )
            result['stages']['risk_classification'] = classify_result
            if classify_result['status'] != 'success':
                return self._finalize_result(result, 'failed')

            # Stage 3: Store in Database
            print("\n[STAGE 3] Database Management...")
            db_result = await self.database_manager.execute(
                action="insert",
                data={"news_items": news_result['data']}
            )
            result['stages']['database'] = db_result

            # Stage 4: Analyze
            print("\n[STAGE 4] Risk Analysis...")
            analysis_result = await self.analyzer.execute(
                classified_news=classify_result['data']
            )
            result['stages']['analysis'] = analysis_result
            if analysis_result['status'] != 'success':
                return self._finalize_result(result, 'failed')

            # Stage 5: Generate Report
            print("\n[STAGE 5] Report Generation...")
            report_result = await self.reporter.execute(
                analysis_data=analysis_result['analysis']
            )
            result['stages']['reporting'] = report_result

            return self._finalize_result(result, 'success')

        except Exception as e:
            print(f"\n❌ Pipeline failed: {str(e)}")
            return self._finalize_result(result, 'failed')

    def _finalize_result(self, result: Dict, status: str) -> Dict:
        """Finalize execution result"""
        result['status'] = status
        result['completion_time'] = datetime.now().isoformat()
        print("\n" + "="*70)
        print(f"PIPELINE STATUS: {status.upper()}")
        print("="*70)
        return result

    def get_agent_summary(self) -> Dict:
        """Get summary of all agents"""
        return {
            "news_gatherer": {
                "status": self.news_gatherer.status,
                "logs": len(self.news_gatherer.logs)
            },
            "risk_classifier": {
                "status": self.risk_classifier.status,
                "classified_count": len(self.risk_classifier.classified_news)
            },
            "database_manager": {
                "status": self.database_manager.status,
                "records_stored": self.database_manager.records_stored
            },
            "analyzer": {
                "status": self.analyzer.status,
                "analysis_ready": bool(self.analyzer.analysis_results)
            },
            "reporter": {
                "status": self.reporter.status,
                "reports_generated": len(self.reporter.reports)
            }
        }


async def demo():
    """Demo the multi-agent system"""
    orchestrator = AgentOrchestrator()

    # Execute the pipeline
    result = await orchestrator.execute_pipeline()

    # Print results
    print("\n" + "="*70)
    print("FINAL RESULTS")
    print("="*70)

    print("\nAgent Summary:")
    summary = orchestrator.get_agent_summary()
    for agent, stats in summary.items():
        print(f"  {agent}: {stats}")

    # Get the final report
    if orchestrator.reporter.reports:
        report = orchestrator.reporter.reports[-1]
        print("\nGenerated Report:")
        print(json.dumps(report, indent=2, ensure_ascii=False))

    return result


if __name__ == "__main__":
    import sys
    asyncio.run(demo())
