#!/usr/bin/env python3
"""
ARGUS: Daily Federal Reserve News Monitoring
每日自动监控美联储News，Storage到Database并进行Risk分析
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
    """每日监控器"""

    def __init__(self):
        """初始化监控器"""
        self.crawler = FedNewsCrawler()
        self.db = FedNewsDatabase()
        self.current_year = datetime.now().year

    async def run(self, verbose: bool = True) -> Dict:
        """
        执行每日监控

        Args:
            verbose: 是否打印详细Information

        Returns:
            监控Results
        """
        if verbose:
            print("\n" + "="*70)
            print(f"ARGUS 每日监控 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            print("="*70)

        result = {
            "timestamp": datetime.now().isoformat(),
            "status": "running"
        }

        try:
            # 1. 爬取News
            if verbose:
                print("\n[1/4] 爬取News...")

            news_url = f"https://www.federalreserve.gov/newsevents/pressreleases/{self.current_year}-press.htm"
            crawl_result = await self.crawler.crawl(url=news_url)

            if crawl_result['status'] != 'success':
                result['status'] = 'error'
                result['error'] = crawl_result.get('error')
                return result

            news_items = crawl_result['json_data']
            if verbose:
                print(f"✓ 找到 {len(news_items)} 条News")

            # 2. Storage到Database
            if verbose:
                print("\n[2/4] Storage到Database...")

            inserted = self.db.insert_news(news_items)
            if verbose:
                print(f"✓ Insert {inserted} 条新记录")

            # 3. 添加Keywords匹配
            if verbose:
                print("\n[3/4] 添加Keywords匹配...")

            keywords = [
                "Payment", "Payments", "Regulation", "Regulatory",
                "Bank", "Banking", "Banks", "Interest", "Rate", "Rates",
                "Treasury", "Monetary", "Financial", "Risk", "Stability", "Crisis"
            ]
            self.db.add_keyword_matches(keywords)

            # 4. 统计和报告
            if verbose:
                print("\n[4/4] 生成报告...")

            stats = self.db.get_statistics()

            result['status'] = 'success'
            result['news_count'] = len(news_items)
            result['inserted'] = inserted
            result['total_in_db'] = stats['total_news']
            result['statistics'] = stats

            if verbose:
                self._print_summary(result)

            # 保存监控Logging
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
        """打印监控摘要"""
        print("\n" + "="*70)
        print("监控摘要")
        print("="*70)
        print(f"状态: {result['status']}")
        print(f"本次爬取: {result['news_count']} 条")
        print(f"新增记录: {result['inserted']} 条")
        print(f"Database总数: {result['total_in_db']} 条")

        stats = result.get('statistics', {})
        if stats.get('by_year'):
            print(f"\n按年份分布:")
            for year in sorted(stats['by_year'].keys(), reverse=True):
                print(f"  • {year}: {stats['by_year'][year]} 条")

        if stats.get('top_keywords'):
            print(f"\n热门Keywords (Top 5):")
            for keyword in list(stats['top_keywords'].items())[:5]:
                print(f"  • {keyword[0]}: {keyword[1]} 条")

        print("="*70)

    def _save_log(self, result: Dict):
        """保存监控Logging"""
        log_dir = Path("data/fed_news/logs")
        log_dir.mkdir(parents=True, exist_ok=True)

        log_file = log_dir / f"daily_monitor_{datetime.now().strftime('%Y%m%d')}.json"

        with open(log_file, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        print(f"\n✓ Logging已保存: {log_file}")


async def main():
    """主函数"""
    monitor = DailyMonitor()
    result = await monitor.run(verbose=True)

    # Return适当的退出码
    sys.exit(0 if result['status'] == 'success' else 1)


if __name__ == "__main__":
    asyncio.run(main())
