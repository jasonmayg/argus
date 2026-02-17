#!/usr/bin/env python3
"""
ARGUS: Daily Federal Reserve News Monitoring
Daily automated monitoring of Federal Reserve news, storing to database and performing risk analysis
"""

import asyncio
import json
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
            # 1. Crawl news
            if verbose:
                print("\n[1/4] Crawling Federal Reserve news...")

            news_url = f"https://www.federalreserve.gov/newsevents/pressreleases/{self.current_year}-press.htm"
            crawl_result = await self.crawler.crawl(url=news_url)

            if crawl_result['status'] != 'success':
                result['status'] = 'error'
                result['error'] = crawl_result.get('error')
                return result

            news_items = crawl_result['json_data']
            if verbose:
                print(f"✓ Found {len(news_items)} news items")

            # 2. Store to database
            if verbose:
                print("\n[2/4] Storing news to database...")

            inserted = self.db.insert_news(news_items)
            if verbose:
                print(f"✓ Inserted {inserted} new records")

            # 3. Add keyword matching
            if verbose:
                print("\n[3/4] Adding keyword matching...")

            keywords = [
                "Payment", "Payments", "Regulation", "Regulatory",
                "Bank", "Banking", "Banks", "Interest", "Rate", "Rates",
                "Treasury", "Monetary", "Financial", "Risk", "Stability", "Crisis"
            ]
            self.db.add_keyword_matches(keywords)

            # 4. Statistics and reporting
            if verbose:
                print("\n[4/4] Generating report...")

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
        print(f"News items crawled: {result['news_count']}")
        print(f"New records inserted: {result['inserted']}")
        print(f"Total in database: {result['total_in_db']}")

        stats = result.get('statistics', {})
        if stats.get('by_year'):
            print(f"\nDistribution by year:")
            for year in sorted(stats['by_year'].keys(), reverse=True):
                print(f"  • {year}: {stats['by_year'][year]} items")

        if stats.get('top_keywords'):
            print(f"\nPopular Keywords (Top 5):")
            for keyword in list(stats['top_keywords'].items())[:5]:
                print(f"  • {keyword[0]}: {keyword[1]} occurrences")

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
