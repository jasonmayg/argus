#!/usr/bin/env python3
"""
ARGUS: Federal Reserve News Database Manager
使用 SQLite DatabaseStorage和Management历史News数据
"""

import sqlite3
import json
from datetime import datetime
from typing import List, Dict, Optional, Tuple


class FedNewsDatabase:
    """美联储NewsDatabaseManagement器"""

    def __init__(self, db_path: str = "data/fed_news/argus_news.db"):
        """
        初始化Database

        Args:
            db_path: DatabaseFilePath
        """
        self.db_path = db_path
        self.conn = None
        self.cursor = None
        self._connect()
        self._create_tables()

    def _connect(self):
        """ConnectionDatabase"""
        import os
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.cursor = self.conn.cursor()
        print(f"✓ DatabaseConnection: {self.db_path}")

    def _create_tables(self):
        """创建数据表"""
        # News表
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS news (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                title TEXT NOT NULL,
                link TEXT UNIQUE NOT NULL,
                content TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Keywords匹配表
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS keyword_matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                news_id INTEGER NOT NULL,
                keyword TEXT NOT NULL,
                FOREIGN KEY (news_id) REFERENCES news(id),
                UNIQUE(news_id, keyword)
            )
        ''')

        # Risk分析表
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS risk_analysis (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                news_id INTEGER NOT NULL,
                risk_category TEXT NOT NULL,
                risk_score INTEGER,
                analysis_text TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (news_id) REFERENCES news(id)
            )
        ''')

        # 元数据表
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        self.conn.commit()
        print("✓ 数据表已创建")

    def insert_news(self, news_items: List[Dict]) -> int:
        """
        InsertNews数据

        Args:
            news_items: News列表

        Returns:
            Insert的记录数
        """
        inserted = 0

        for item in news_items:
            try:
                self.cursor.execute(
                    '''
                    INSERT OR IGNORE INTO news (date, title, link)
                    VALUES (?, ?, ?)
                    ''',
                    (item['date'], item['title'], item['link'])
                )
                inserted += self.cursor.rowcount
            except sqlite3.IntegrityError:
                pass  # 链接已存在，跳过

        self.conn.commit()
        print(f"✓ Insert {inserted} 条News记录")
        return inserted

    def add_keyword_matches(self, keywords: List[str]):
        """
        为所有News添加Keywords匹配记录

        Args:
            keywords: Keywords列表
        """
        # 获取所有News
        self.cursor.execute('SELECT id, title FROM news')
        news = self.cursor.fetchall()

        matches_count = 0

        for row in news:
            news_id = row['id']
            title_lower = row['title'].lower()

            for keyword in keywords:
                if keyword.lower() in title_lower:
                    try:
                        self.cursor.execute(
                            '''
                            INSERT OR IGNORE INTO keyword_matches (news_id, keyword)
                            VALUES (?, ?)
                            ''',
                            (news_id, keyword)
                        )
                        matches_count += self.cursor.rowcount
                    except sqlite3.IntegrityError:
                        pass

        self.conn.commit()
        print(f"✓ 添加 {matches_count} 条Keywords匹配")

    def query_by_date_range(
        self,
        start_date: str,
        end_date: str
    ) -> List[Dict]:
        """
        按Date范围查询News

        Args:
            start_date: 开始Date (YYYY-MM-DD)
            end_date: 结束Date (YYYY-MM-DD)

        Returns:
            News列表
        """
        self.cursor.execute(
            '''
            SELECT * FROM news
            WHERE date >= ? AND date <= ?
            ORDER BY date DESC
            ''',
            (start_date, end_date)
        )
        return [dict(row) for row in self.cursor.fetchall()]

    def query_by_keyword(self, keyword: str) -> List[Dict]:
        """
        按Keywords查询News

        Args:
            keyword: Keywords

        Returns:
            匹配的News列表
        """
        self.cursor.execute(
            '''
            SELECT DISTINCT n.* FROM news n
            JOIN keyword_matches km ON n.id = km.news_id
            WHERE km.keyword = ?
            ORDER BY n.date DESC
            ''',
            (keyword,)
        )
        return [dict(row) for row in self.cursor.fetchall()]

    def query_by_keywords(self, keywords: List[str]) -> List[Dict]:
        """
        按多个Keywords查询News（OR 逻辑）

        Args:
            keywords: Keywords列表

        Returns:
            匹配的News列表
        """
        if not keywords:
            return []

        placeholders = ','.join('?' * len(keywords))
        self.cursor.execute(
            f'''
            SELECT DISTINCT n.* FROM news n
            JOIN keyword_matches km ON n.id = km.news_id
            WHERE km.keyword IN ({placeholders})
            ORDER BY n.date DESC
            ''',
            keywords
        )
        return [dict(row) for row in self.cursor.fetchall()]

    def save_analysis(
        self,
        news_id: int,
        risk_category: str,
        risk_score: int,
        analysis_text: str
    ):
        """
        保存Risk分析Results

        Args:
            news_id: News ID
            risk_category: Risk类别
            risk_score: Risk评分 (1-10)
            analysis_text: 分析文本
        """
        self.cursor.execute(
            '''
            INSERT INTO risk_analysis (news_id, risk_category, risk_score, analysis_text)
            VALUES (?, ?, ?, ?)
            ''',
            (news_id, risk_category, risk_score, analysis_text)
        )
        self.conn.commit()

    def get_statistics(self) -> Dict:
        """
        获取Database统计Information

        Returns:
            统计Information字典
        """
        stats = {}

        # 总News数
        self.cursor.execute('SELECT COUNT(*) as count FROM news')
        stats['total_news'] = self.cursor.fetchone()['count']

        # Date范围
        self.cursor.execute('SELECT MIN(date) as min_date, MAX(date) as max_date FROM news')
        row = self.cursor.fetchone()
        stats['date_range'] = {
            'min': row['min_date'],
            'max': row['max_date']
        }

        # 按年份统计
        self.cursor.execute(
            '''
            SELECT SUBSTR(date, 1, 4) as year, COUNT(*) as count
            FROM news
            GROUP BY SUBSTR(date, 1, 4)
            ORDER BY year DESC
            '''
        )
        stats['by_year'] = {row['year']: row['count'] for row in self.cursor.fetchall()}

        # Keywords分布
        self.cursor.execute(
            '''
            SELECT keyword, COUNT(*) as count
            FROM keyword_matches
            GROUP BY keyword
            ORDER BY count DESC
            LIMIT 10
            '''
        )
        stats['top_keywords'] = {row['keyword']: row['count'] for row in self.cursor.fetchall()}

        return stats

    def export_to_json(self, output_file: str, query_filter: Optional[Dict] = None):
        """
        导出数据为 JSON

        Args:
            output_file: 输出FilePath
            query_filter: 查询Filter条件 (可选)
        """
        if query_filter and 'date_range' in query_filter:
            news = self.query_by_date_range(
                query_filter['date_range']['start'],
                query_filter['date_range']['end']
            )
        elif query_filter and 'keywords' in query_filter:
            news = self.query_by_keywords(query_filter['keywords'])
        else:
            self.cursor.execute('SELECT * FROM news ORDER BY date DESC')
            news = [dict(row) for row in self.cursor.fetchall()]

        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(news, f, ensure_ascii=False, indent=2)

        print(f"✓ 已导出 {len(news)} 条News到: {output_file}")

    def close(self):
        """CloseDatabaseConnection"""
        if self.conn:
            self.conn.close()
            print("✓ DatabaseConnection已Close")


async def example_usage():
    """使用Example"""
    from fed_news_crawler import FedNewsCrawler

    print("\n" + "="*70)
    print("ARGUS: 美联储NewsDatabase - 使用Example")
    print("="*70)

    # 1. 爬取News
    print("\n[步骤 1] 爬取News...")
    crawler = FedNewsCrawler()
    result = await crawler.crawl(
        url="https://www.federalreserve.gov/newsevents/pressreleases/2026-press.htm"
    )

    if result['status'] != 'success':
        print(f"❌ Crawler失败")
        return

    # 2. 初始化Database
    print("\n[步骤 2] 初始化Database...")
    db = FedNewsDatabase()

    # 3. InsertNews
    print("\n[步骤 3] InsertNews数据...")
    news_items = result['json_data']
    db.insert_news(news_items)

    # 4. 添加Keywords匹配
    print("\n[步骤 4] 添加Keywords匹配...")
    keywords = [
        "Payment", "Regulation", "Bank", "Interest",
        "Rate", "Treasury", "Monetary", "Financial", "Risk"
    ]
    db.add_keyword_matches(keywords)

    # 5. 查询Example
    print("\n[步骤 5] 查询Example...")

    # 查询所有包含 "Bank" 的News
    bank_news = db.query_by_keyword("Bank")
    print(f"\n包含 'Bank' 的News: {len(bank_news)} 条")
    for item in bank_news[:3]:
        print(f"  • [{item['date']}] {item['title'][:80]}...")

    # 查询多个Keywords
    multi_news = db.query_by_keywords(["Interest", "Rate"])
    print(f"\n包含 'Interest' 或 'Rate' 的News: {len(multi_news)} 条")

    # 6. 获取统计Information
    print("\n[步骤 6] Database统计...")
    stats = db.get_statistics()
    print(f"总News数: {stats['total_news']}")
    print(f"Date范围: {stats['date_range']['min']} 至 {stats['date_range']['max']}")
    print(f"\n按年份分布:")
    for year, count in stats['by_year'].items():
        print(f"  • {year}: {count} 条")
    print(f"\n热门Keywords:")
    for keyword, count in list(stats['top_keywords'].items())[:5]:
        print(f"  • {keyword}: {count} 条")

    # 7. 导出数据
    print("\n[步骤 7] 导出数据...")
    db.export_to_json("data/fed_news/database_export.json")

    # 8. CloseDatabase
    print("\n[步骤 8] CloseDatabase...")
    db.close()

    print("\n✅ Example完成")


if __name__ == "__main__":
    import asyncio
    asyncio.run(example_usage())
