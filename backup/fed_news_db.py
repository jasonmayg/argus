#!/usr/bin/env python3
"""
ARGUS: Federal Reserve News Database Manager
Uses SQLite Database for storing and managing historical news data
"""

import sqlite3
import json
from datetime import datetime
from typing import List, Dict, Optional, Tuple


class FedNewsDatabase:
    """Federal Reserve News Database Manager"""

    def __init__(self, db_path: str = "data/fed_news/argus_news.db"):
        """
        Initialize the database

        Args:
            db_path: Database file path
        """
        self.db_path = db_path
        self.conn = None
        self.cursor = None
        self._connect()
        self._create_tables()

    def _connect(self):
        """Connect to the database"""
        import os
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.cursor = self.conn.cursor()
        print(f"✓ Database connection established: {self.db_path}")

    def _create_tables(self):
        """Create database tables"""
        # News table
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

        # Keyword matches table
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS keyword_matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                news_id INTEGER NOT NULL,
                keyword TEXT NOT NULL,
                FOREIGN KEY (news_id) REFERENCES news(id),
                UNIQUE(news_id, keyword)
            )
        ''')

        # Risk analysis table
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

        # Metadata table
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        self.conn.commit()
        print("✓ Database tables created")

    def insert_news(self, news_items: List[Dict]) -> int:
        """
        Insert news data

        Args:
            news_items: List of news items

        Returns:
            Number of records inserted
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
                pass  # Link already exists, skip

        self.conn.commit()
        print(f"✓ Inserted {inserted} news records")
        return inserted

    def add_keyword_matches(self, keywords: List[str]):
        """
        Add keyword match records for all news

        Args:
            keywords: List of keywords
        """
        # Fetch all news
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
        print(f"✓ Added {matches_count} keyword matches")

    def query_by_date_range(
        self,
        start_date: str,
        end_date: str
    ) -> List[Dict]:
        """
        Query news by date range

        Args:
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)

        Returns:
            List of news items
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
        Query news by keyword

        Args:
            keyword: Keyword

        Returns:
            List of matching news items
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
        Query news by multiple keywords (OR logic)

        Args:
            keywords: List of keywords

        Returns:
            List of matching news items
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
        Save risk analysis results

        Args:
            news_id: News ID
            risk_category: Risk category
            risk_score: Risk score (1-10)
            analysis_text: Analysis text
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
        Get database statistics

        Returns:
            Dictionary of statistics
        """
        stats = {}

        # Total news count
        self.cursor.execute('SELECT COUNT(*) as count FROM news')
        stats['total_news'] = self.cursor.fetchone()['count']

        # Date range
        self.cursor.execute('SELECT MIN(date) as min_date, MAX(date) as max_date FROM news')
        row = self.cursor.fetchone()
        stats['date_range'] = {
            'min': row['min_date'],
            'max': row['max_date']
        }

        # Group by year
        self.cursor.execute(
            '''
            SELECT SUBSTR(date, 1, 4) as year, COUNT(*) as count
            FROM news
            GROUP BY SUBSTR(date, 1, 4)
            ORDER BY year DESC
            '''
        )
        stats['by_year'] = {row['year']: row['count'] for row in self.cursor.fetchall()}

        # Keyword distribution
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
        Export data as JSON

        Args:
            output_file: Output file path
            query_filter: Query filter conditions (optional)
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

        print(f"✓ Exported {len(news)} news items to: {output_file}")

    def close(self):
        """Close database connection"""
        if self.conn:
            self.conn.close()
            print("✓ Database connection closed")


async def example_usage():
    """Usage example"""
    from fed_news_crawler import FedNewsCrawler

    print("\n" + "="*70)
    print("ARGUS: Federal Reserve News Database - Usage Example")
    print("="*70)

    # 1. Crawl news
    print("\n[Step 1] Crawl news...")
    crawler = FedNewsCrawler()
    result = await crawler.crawl(
        url="https://www.federalreserve.gov/newsevents/pressreleases/2026-press.htm"
    )

    if result['status'] != 'success':
        print(f"❌ Crawler failed")
        return

    # 2. Initialize database
    print("\n[Step 2] Initialize database...")
    db = FedNewsDatabase()

    # 3. Insert news
    print("\n[Step 3] Insert news data...")
    news_items = result['json_data']
    db.insert_news(news_items)

    # 4. Add keyword matches
    print("\n[Step 4] Add keyword matches...")
    keywords = [
        "Payment", "Regulation", "Bank", "Interest",
        "Rate", "Treasury", "Monetary", "Financial", "Risk"
    ]
    db.add_keyword_matches(keywords)

    # 5. Query examples
    print("\n[Step 5] Query examples...")

    # Query all news containing "Bank"
    bank_news = db.query_by_keyword("Bank")
    print(f"\nNews containing 'Bank': {len(bank_news)} items")
    for item in bank_news[:3]:
        print(f"  • [{item['date']}] {item['title'][:80]}...")

    # Query multiple keywords
    multi_news = db.query_by_keywords(["Interest", "Rate"])
    print(f"\nNews containing 'Interest' or 'Rate': {len(multi_news)} items")

    # 6. Get statistics
    print("\n[Step 6] Database statistics...")
    stats = db.get_statistics()
    print(f"Total news: {stats['total_news']}")
    print(f"Date range: {stats['date_range']['min']} to {stats['date_range']['max']}")
    print(f"\nDistribution by year:")
    for year, count in stats['by_year'].items():
        print(f"  • {year}: {count} items")
    print(f"\nTop keywords:")
    for keyword, count in list(stats['top_keywords'].items())[:5]:
        print(f"  • {keyword}: {count} items")

    # 7. Export data
    print("\n[Step 7] Export data...")
    db.export_to_json("data/fed_news/database_export.json")

    # 8. Close database
    print("\n[Step 8] Close database...")
    db.close()

    print("\n✅ Example completed")


if __name__ == "__main__":
    import asyncio
    asyncio.run(example_usage())
