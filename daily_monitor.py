#!/usr/bin/env python3
"""
ARGUS: Daily Federal Reserve News Monitoring
Daily automated monitoring of Federal Reserve news, Guardian POC event classification, storing to database and performing risk analysis
"""

import asyncio
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict

from fed_news_crawler import FedNewsCrawler
from fed_news_db import FedNewsDatabase


class DailyMonitor:
    """Daily monitoring system for Federal Reserve news"""

    def __init__(self):
        """Initialize the daily monitor"""
        self.crawler = FedNewsCrawler()
        self.db = FedNewsDatabase()
        self.current_year = datetime.now().year
        self.guardian_poc_path = Path(__file__).parent / "guardian_poc" / "src" / "daily_job.py"

    def _run_guardian_job(self, verbose: bool = True) -> Dict:
        """
        Run Guardian POC daily job to classify global events into risk alerts

        Returns:
            Dictionary with Guardian job results
        """
        guardian_result = {
            "status": "skipped",
            "message": "Guardian POC job not found",
            "events_count": 0,
            "alerts_count": 0,
            "triggered_count": 0
        }

        if not self.guardian_poc_path.exists():
            if verbose:
                print(f"⚠ Guardian POC daily job not found at {self.guardian_poc_path}")
            return guardian_result

        try:
            if verbose:
                print("\n[2/5] Running Guardian POC daily job...")

            cmd = [
                sys.executable,
                str(self.guardian_poc_path),
                "--persona", "treasury_ops",
                "--fallback-demo-if-empty"
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
                cwd=Path(__file__).parent
            )

            if result.returncode != 0:
                guardian_result["status"] = "error"
                guardian_result["message"] = f"Guardian job failed: {result.stderr}"
                if verbose:
                    print(f"❌ Guardian job error: {result.stderr[:200]}")
                return guardian_result

            output = result.stdout.strip()
            if verbose:
                print(f"✓ {output}")

            # Parse output for stats
            # Expected output: "mode=live events=177 alerts=30 triggered=28 webhook_pushed=False snapshot=... latest=..."
            for part in output.split():
                if part.startswith("events="):
                    guardian_result["events_count"] = int(part.split("=")[1])
                elif part.startswith("alerts="):
                    guardian_result["alerts_count"] = int(part.split("=")[1])
                elif part.startswith("triggered="):
                    guardian_result["triggered_count"] = int(part.split("=")[1])

            guardian_result["status"] = "success"
            guardian_result["message"] = output
            return guardian_result

        except subprocess.TimeoutExpired:
            guardian_result["status"] = "error"
            guardian_result["message"] = "Guardian job timed out (>120s)"
            if verbose:
                print("❌ Guardian job timed out")
            return guardian_result
        except Exception as e:
            guardian_result["status"] = "error"
            guardian_result["message"] = f"Guardian job exception: {str(e)}"
            if verbose:
                print(f"❌ Guardian job error: {e}")
            return guardian_result

    async def run(self, verbose: bool = True) -> Dict:
        """
        Execute daily monitoring

        Args:
            verbose: Whether to print detailed information

        Returns:
            Monitoring results
        """
        if verbose:
            print("\n" + "="*70)
            print(f"ARGUS Daily Monitoring - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            print("="*70)

        result = {
            "timestamp": datetime.now().isoformat(),
            "status": "running"
        }

        try:
            # 1. Crawl Federal Reserve news
            if verbose:
                print("\n[1/5] Crawling Federal Reserve news...")

            news_url = f"https://www.federalreserve.gov/newsevents/pressreleases/{self.current_year}-press.htm"
            crawl_result = await self.crawler.crawl(url=news_url)

            if crawl_result['status'] != 'success':
                result['status'] = 'error'
                result['error'] = crawl_result.get('error')
                return result

            news_items = crawl_result['json_data']
            if verbose:
                print(f"✓ Found {len(news_items)} news items")

            # 2. Run Guardian POC daily job (classify global events)
            guardian_result = self._run_guardian_job(verbose=verbose)
            result['guardian'] = guardian_result

            # 3. Store Federal Reserve news to database
            if verbose:
                print("\n[3/5] Storing Federal Reserve news to database...")

            inserted = self.db.insert_news(news_items)
            if verbose:
                print(f"✓ Inserted {inserted} new records")

            # 4. Add keyword matching
            if verbose:
                print("\n[4/5] Adding keyword matching...")

            keywords = [
                "Payment", "Payments", "Regulation", "Regulatory",
                "Bank", "Banking", "Banks", "Interest", "Rate", "Rates",
                "Treasury", "Monetary", "Financial", "Risk", "Stability", "Crisis"
            ]
            self.db.add_keyword_matches(keywords)

            # 5. Statistics and reporting
            if verbose:
                print("\n[5/5] Generating report...")

            stats = self.db.get_statistics()

            result['status'] = 'success'
            result['news_count'] = len(news_items)
            result['inserted'] = inserted
            result['total_in_db'] = stats['total_news']
            result['statistics'] = stats

            if verbose:
                self._print_summary(result)

            # Save monitoring logs
            self._save_log(result)

            return result

        except Exception as e:
            result['status'] = 'error'
            result['error'] = str(e)
            if verbose:
                print(f"\n❌ Error: {e}")
            return result

        finally:
            self.db.close()

    def _print_summary(self, result: Dict):
        """Print monitoring summary"""
        print("\n" + "="*70)
        print("Monitoring Summary")
        print("="*70)
        print(f"Status: {result['status']}")

        # Federal Reserve section
        print(f"\nFederal Reserve News:")
        print(f"  • News items crawled: {result['news_count']}")
        print(f"  • New records inserted: {result['inserted']}")
        print(f"  • Total in database: {result['total_in_db']}")

        stats = result.get('statistics', {})
        if stats.get('by_year'):
            print(f"\n  Distribution by year:")
            for year in sorted(stats['by_year'].keys(), reverse=True)[:3]:
                print(f"    - {year}: {stats['by_year'][year]} items")

        if stats.get('top_keywords'):
            print(f"\n  Popular Keywords (Top 5):")
            for keyword in list(stats['top_keywords'].items())[:5]:
                print(f"    - {keyword[0]}: {keyword[1]} occurrences")

        # Guardian POC section
        guardian = result.get('guardian', {})
        if guardian:
            print(f"\nGuardian POC Global Event Classification:")
            print(f"  • Status: {guardian.get('status', 'unknown')}")
            if guardian.get('events_count'):
                print(f"  • Raw events fetched: {guardian['events_count']}")
            if guardian.get('alerts_count'):
                print(f"  • Classified alerts: {guardian['alerts_count']}")
            if guardian.get('triggered_count'):
                print(f"  • High-risk triggered: {guardian['triggered_count']}")
            if guardian.get('status') in ['error', 'skipped'] and guardian.get('message'):
                print(f"  • Message: {guardian['message']}")

        print("="*70)

    def _save_log(self, result: Dict):
        """Save monitoring logs"""
        log_dir = Path("data/fed_news/logs")
        log_dir.mkdir(parents=True, exist_ok=True)

        log_file = log_dir / f"daily_monitor_{datetime.now().strftime('%Y%m%d')}.json"

        with open(log_file, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        print(f"\n✓ Logs saved: {log_file}")


async def main():
    """Main function"""
    monitor = DailyMonitor()
    result = await monitor.run(verbose=True)

    # Return appropriate exit code
    sys.exit(0 if result['status'] == 'success' else 1)


if __name__ == "__main__":
    asyncio.run(main())
