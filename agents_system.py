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
from core.agents import AnalysisAgent


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
