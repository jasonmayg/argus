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

from risk_classifier import RiskClassifier
from core.agents import AnalysisAgent
from core.data_agent import DataAgent, GUARDIAN_API_SOURCES


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
    """Multi-source news and events gatherer backed by the unified DataAgent.

    Supported source keys for the `sources` parameter:
        'federal_reserve'  - Fed Reserve press releases (crawl4ai markdown extraction)
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
        '<https://...>'    - Any custom URL (auto-detected, crawled via crawl4ai)
    """

    SOURCE_FEDERAL_RESERVE = "federal_reserve"
    SOURCE_GCS = "gcs"
    DEFAULT_SOURCES = [SOURCE_FEDERAL_RESERVE]

    def __init__(self):
        super().__init__("NewsGatherer")
        self._data_agent = DataAgent()
        self.news_data = []

    async def execute(
        self,
        url: Optional[str] = None,
        sources: Optional[List[str]] = None,
        **kwargs,
    ):
        """Gather news and events from one or more data sources.

        Delegates to DataAgent which writes each source payload to
        shared_storage/raw/ so the bridge pipeline can process them.

        Args:
            url:     Optional URL override for 'federal_reserve' or a custom
                     https:// crawl target.
            sources: List of source keys. Defaults to ['federal_reserve'].
        """
        self.status = "running"
        self.log("Starting multi-source news gathering...")

        fetch_result = await self._data_agent.fetch(sources=sources, url=url, write_raw=True)

        self.news_data = fetch_result["items"]
        total = fetch_result["total"]

        for source_key, stat in fetch_result["sources"].items():
            self.log(f"Source '{source_key}': {stat['count']} items ({stat['status']})")
            if stat.get("path"):
                self.log(f"  → wrote {stat['path']}")

        if total > 0:
            self.status = "success"
            self.log(f"Gathered {total} total items from {len(fetch_result['sources'])} source(s)")
            return {
                "status": "success",
                "count": total,
                "data": self.news_data,
                "sources": fetch_result["sources"],
            }

        self.status = "failed"
        self.log("No items gathered from any source", "ERROR")
        return {
            "status": "failed",
            "count": 0,
            "data": [],
            "sources": fetch_result["sources"],
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
    """Agent for persisting processed items to shared_storage/analyzed/.

    Replaces the legacy SQLite-backed FedNewsDatabase.  All storage now goes
    through the shared_storage pipeline so every source uses the same path.
    """

    def __init__(self):
        super().__init__("DatabaseManager")
        self.records_stored = 0

    async def execute(self, action: str, data: Optional[Dict] = None, **kwargs):
        """Persist or query items via shared_storage.

        Supported actions:
            insert  — write news_items to shared_storage/raw/ as a timestamped JSON file
            query   — return a lightweight in-memory count summary (no SQLite)
        """
        self.status = "running"
        self.log(f"Executing storage action: {action}")

        try:
            if action == "insert":
                from config import RAW_DIR  # noqa: PLC0415
                from core.data_agent import _write_to_raw  # noqa: PLC0415

                items = (data or {}).get("news_items", [])
                if items:
                    path = _write_to_raw("pipeline_db_insert", items)
                    self.records_stored += len(items)
                    self.log(f"Wrote {len(items)} records to {path}")
                    return {"status": "success", "inserted": len(items), "path": str(path)}
                return {"status": "success", "inserted": 0}

            elif action == "query":
                return {
                    "status": "success",
                    "data": {"total_news": self.records_stored},
                }

            self.status = "success"
            return {"status": "success"}

        except Exception as e:
            self.log(f"Storage operation failed: {str(e)}", "ERROR")
            self.status = "failed"
            return {"status": "failed", "error": str(e)}


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
