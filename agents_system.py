#!/usr/bin/env python3
"""
ARGUS Multi-Agent System
Complete agent-based architecture for Federal Reserve news monitoring and risk analysis
"""

import asyncio
import json
from datetime import datetime
from typing import List, Dict, Optional
from abc import ABC, abstractmethod

from fed_news_crawler import FedNewsCrawler
from risk_classifier import RiskClassifier
from fed_news_db import FedNewsDatabase


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
    """Agent for gathering Federal Reserve news"""

    def __init__(self):
        super().__init__("NewsGatherer")
        self.crawler = FedNewsCrawler()
        self.news_data = []

    async def execute(self, url: Optional[str] = None, **kwargs):
        """Gather news from Federal Reserve"""
        self.status = "running"
        self.log("Starting news gathering...")

        try:
            if not url:
                from datetime import datetime
                year = datetime.now().year
                url = f"https://www.federalreserve.gov/newsevents/pressreleases/{year}-press.htm"

            self.log(f"Crawling: {url}")
            result = await self.crawler.crawl(url=url)

            if result['status'] == 'success':
                self.news_data = result['json_data']
                self.log(f"Successfully gathered {len(self.news_data)} news items")
                self.status = "success"
                return {
                    "status": "success",
                    "count": len(self.news_data),
                    "data": self.news_data
                }
            else:
                self.log(f"Crawling failed: {result.get('error')}", "ERROR")
                self.status = "failed"
                return {"status": "failed", "error": result.get('error')}

        except Exception as e:
            self.log(f"Exception occurred: {str(e)}", "ERROR")
            self.status = "failed"
            return {"status": "failed", "error": str(e)}


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
    """Agent for risk analysis"""

    def __init__(self):
        super().__init__("RiskAnalyzer")
        self.analysis_results = []

    async def execute(self, classified_news: List[Dict], **kwargs):
        """Analyze risks in classified news"""
        self.status = "running"
        self.log(f"Starting analysis of {len(classified_news)} items")

        try:
            self.analysis_results = []

            # Analyze by risk level
            critical_items = [n for n in classified_news if n['risk_level'] == 'Critical']
            high_items = [n for n in classified_news if n['risk_level'] == 'High']
            medium_items = [n for n in classified_news if n['risk_level'] == 'Medium']

            self.log(f"Critical: {len(critical_items)}, High: {len(high_items)}, Medium: {len(medium_items)}")

            # Analyze by category
            by_category = {}
            for item in classified_news:
                cat = item['risk_category']
                if cat not in by_category:
                    by_category[cat] = []
                by_category[cat].append(item)

            analysis = {
                "total_items": len(classified_news),
                "by_risk_level": {
                    "critical": critical_items,
                    "high": high_items,
                    "medium": medium_items
                },
                "by_category": {
                    cat: len(items) for cat, items in by_category.items()
                },
                "top_keywords": self._extract_top_keywords(classified_news)
            }

            self.analysis_results = analysis
            self.log("Analysis completed successfully")
            self.status = "success"

            return {"status": "success", "analysis": analysis}

        except Exception as e:
            self.log(f"Analysis failed: {str(e)}", "ERROR")
            self.status = "failed"
            return {"status": "failed", "error": str(e)}

    def _extract_top_keywords(self, news_items: List[Dict], top_n: int = 10) -> List[str]:
        """Extract top keywords from news items"""
        keyword_count = {}
        for item in news_items:
            for keyword in item.get('matched_keywords', []):
                keyword_count[keyword] = keyword_count.get(keyword, 0) + 1

        sorted_keywords = sorted(keyword_count.items(), key=lambda x: x[1], reverse=True)
        return [kw for kw, count in sorted_keywords[:top_n]]


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
            report = {
                "timestamp": datetime.now().isoformat(),
                "title": "Federal Reserve News Risk Analysis Report",
                "summary": {
                    "total_items": analysis_data['total_items'],
                    "critical_count": len(analysis_data['by_risk_level']['critical']),
                    "high_count": len(analysis_data['by_risk_level']['high']),
                    "medium_count": len(analysis_data['by_risk_level']['medium']),
                },
                "recommendations": self._generate_recommendations(analysis_data),
                "top_keywords": analysis_data['top_keywords'],
                "category_distribution": analysis_data['by_category']
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
        """Generate recommendations based on analysis"""
        recommendations = []

        critical_count = len(analysis['by_risk_level']['critical'])
        if critical_count > 0:
            recommendations.append(f"⚠️ URGENT: {critical_count} critical risk items detected. Immediate management review required.")

        high_count = len(analysis['by_risk_level']['high'])
        if high_count > 3:
            recommendations.append(f"📊 {high_count} high-risk items detected. Consider deep analysis.")

        for category, count in analysis['by_category'].items():
            if count > 5:
                recommendations.append(f"🔍 Monitor {category} closely: {count} items detected.")

        if not recommendations:
            recommendations.append("✅ Risk profile within normal parameters.")

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
