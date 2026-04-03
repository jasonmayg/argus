#!/usr/bin/env python3
"""
ARGUS: Federal Reserve News Crawler
Fetches Federal Reserve news announcements for global treasury risk monitoring
"""

import asyncio
import json
import re
from datetime import datetime
from typing import List, Dict, Optional
from urllib.parse import urljoin

from crawl4ai import AsyncWebCrawler


# ===== Configuration Section =====
# Keyword filter list
KEYWORDS_FILTER = [
    "Payment", "Payments",
    "Regulation", "Regulatory",
    "Bank", "Banking", "Banks",
    "Interest", "Rate", "Rates",
    "Treasury", "Monetary",
    "Financial", "Risk",
    "Stability", "Crisis"
]

# Federal Reserve news page URL
FED_NEWS_URL = "https://www.federalreserve.gov/newsevents/pressreleases.htm"

# Output directory
OUTPUT_DIR = "data/fed_news"


class FedNewsCrawler:
    """Federal Reserve news crawler class"""

    def __init__(self, keywords: Optional[List[str]] = None):
        """
        Initialize the crawler

        Args:
            keywords: List of filter keywords, uses default list if None
        """
        self.keywords = keywords or KEYWORDS_FILTER
        self.crawler = None
        self.news_items = []

    async def fetch_page(self, url: str, page_num: int = 1) -> str:
        """
        Fetch HTML content of a single page

        Args:
            url: Target URL
            page_num: Page number (for pagination)

        Returns:
            HTML and Markdown content of the page
        """
        print(f"Fetching page {page_num}: {url}")

        self.crawler = AsyncWebCrawler()
        result = await self.crawler.arun(
            url=url,
            bypass_cache=True,
            wait_for='body',  # Wait for body to load
            wait_until='networkidle',  # Wait for network idle
            delay_before_return_ms=2000  # Extra delay to ensure JS rendering completes
        )

        if result.success:
            print(f"✓ Successfully fetched page content (Markdown length: {len(result.markdown)} characters)")
            return result.html, result.markdown
        else:
            print(f"✗ Failed: {result.error_message}")
            return None, None

    def extract_news_items(self, markdown: str) -> List[Dict]:
        """
        Extract news items from Markdown

        Federal Reserve Markdown format:
        Date (e.g., 2/12/2026)
        [Title](Link)

        Args:
            markdown: Page Markdown content

        Returns:
            List of news items
        """
        items = []

        # Markdown format: Date on separate line, then [Title](Link) format
        # Example:
        # 2/12/2026
        # [_Federal Reserve Board announces..._](https://www.federalreserve.gov/newsevents/...)

        lines = markdown.split('\n')

        for i, line in enumerate(lines):
            line = line.strip()

            # Check if this is a date line (format: M/D/YYYY or MM/DD/YYYY)
            if re.match(r'^\d{1,2}/\d{1,2}/\d{4}$', line):
                date_str = line

                # Find the next non-empty line containing title and link
                for j in range(i + 1, len(lines)):
                    next_line = lines[j].strip()

                    if not next_line:
                        continue

                    # Match Markdown link format: [title](link)
                    # Title may contain _italic_ markers
                    link_pattern = r'\[_?([^]_]+)_?\]\(([^)]+)\)'
                    match = re.search(link_pattern, next_line)

                    if match:
                        title = match.group(1).strip()
                        link = match.group(2).strip()

                        # Ensure link is absolute URL
                        if link.startswith('/'):
                            link = urljoin(FED_NEWS_URL, link)

                        items.append({
                            'date': date_str,
                            'title': title,
                            'link': link
                        })

                        break  # Stop after finding corresponding link
                    else:
                        # If next line is not a link, title and link may be on different lines
                        # Or this may not be a news line, continue searching
                        break

        print(f"✓ Extracted {len(items)} news records")
        return items

    def filter_by_keywords(self, items: List[Dict]) -> List[Dict]:
        """
        Filter news items by keywords

        Args:
            items: List of news items

        Returns:
            Filtered list of news items
        """
        filtered = []

        for item in items:
            title_lower = item['title'].lower()

            # Check if title contains any keywords
            for keyword in self.keywords:
                if keyword.lower() in title_lower:
                    filtered.append(item)
                    break  # Avoid duplicate additions

        print(f"✓ Keyword filter: {len(filtered)} records (filtered out {len(items) - len(filtered)} records)")
        return filtered

    def normalize_date(self, date_str: str) -> str:
        """
        Normalize date string to YYYY-MM-DD format

        Args:
            date_str: Original date string

        Returns:
            Normalized date string
        """
        # Try multiple date formats
        formats = [
            "%Y-%m-%d",
            "%B %d, %Y",
            "%b %d, %Y",
            "%m/%d/%Y"
        ]

        for fmt in formats:
            try:
                dt = datetime.strptime(date_str.strip(), fmt)
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                continue

        # If unable to parse, return original string
        return date_str.strip()

    def to_json(self) -> str:
        """
        Convert news items to JSON format

        Returns:
            JSON string
        """
        output = []

        for item in self.news_items:
            output.append({
                "date": self.normalize_date(item['date']),
                "title": item['title'],
                "link": item['link']
            })

        # Sort by date in descending order
        output.sort(key=lambda x: x['date'], reverse=True)

        return json.dumps(output, ensure_ascii=False, indent=2)

    def to_markdown(self, markdown_content: str) -> str:
        """
        Convert content to Markdown format for further processing

        Args:
            markdown_content: Markdown content provided by Crawl4AI

        Returns:
            Formatted Markdown string
        """
        # Create Markdown document with news list
        md_output = "# Federal Reserve News Announcements\n\n"
        md_output += f"**Crawl Time**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        md_output += f"**Data Source**: {FED_NEWS_URL}\n"
        md_output += f"**Record Count**: {len(self.news_items)}\n\n"

        md_output += "## Filter Criteria\n"
        md_output += f"- **Keywords**: {', '.join(self.keywords)}\n\n"

        md_output += "## News List\n\n"

        for item in self.news_items:
            normalized_date = self.normalize_date(item['date'])
            md_output += f"### [{item['title']}]({item['link']})\n"
            md_output += f"- **Date**: {normalized_date}\n"
            md_output += f"- **Link**: {item['link']}\n\n"

        return md_output

    async def crawl(self, url: str = FED_NEWS_URL, page_num: int = 1) -> Dict:
        """
        Execute the main crawler process

        Args:
            url: URL to crawl (typically a news page for a specific year)
            page_num: Page number

        Returns:
            Dictionary containing JSON and Markdown content
        """
        try:
            # 1. Fetch page content
            html, markdown = await self.fetch_page(url, page_num)
            if not markdown:
                return {"error": "Failed to fetch page"}

            # 2. Extract news items from Markdown
            items = self.extract_news_items(markdown)

            # 3. Filter by keywords
            self.news_items = self.filter_by_keywords(items)

            # 4. Generate output
            result = {
                "status": "success",
                "timestamp": datetime.now().isoformat(),
                "url": url,
                "total_items_found": len(items),
                "filtered_items": len(self.news_items),
                "json_data": json.loads(self.to_json()),
                "markdown": self.to_markdown(markdown)
            }

            return result

        finally:
            if self.crawler:
                await self.crawler.close()


async def main():
    """Main function"""
    # Create crawler instance
    crawler = FedNewsCrawler()

    # Execute crawl - fetch current year news page
    # Note: By default crawls the current year, can be changed by modifying URL for other years
    current_year = datetime.now().year
    news_url = f"https://www.federalreserve.gov/newsevents/pressreleases/{current_year}-press.htm"

    print(f"Crawler Configuration:")
    print(f"  Target URL: {news_url}")
    print(f"  Filter Keywords: {', '.join(crawler.keywords)}")
    print("="*60)

    result = await crawler.crawl(url=news_url)

    if result.get("status") == "success":
        # Save JSON results
        import os
        os.makedirs(OUTPUT_DIR, exist_ok=True)

        json_output_path = f"{OUTPUT_DIR}/fed_news_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(json_output_path, 'w', encoding='utf-8') as f:
            json.dump(result['json_data'], f, ensure_ascii=False, indent=2)
        print(f"\n✓ JSON data saved to: {json_output_path}")

        # Save Markdown results
        md_output_path = f"{OUTPUT_DIR}/fed_news_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        with open(md_output_path, 'w', encoding='utf-8') as f:
            f.write(result['markdown'])
        print(f"✓ Markdown data saved to: {md_output_path}")

        # Print statistics
        print("\n" + "="*60)
        print("Crawler Statistics:")
        print("="*60)
        print(f"Total records found: {result.get('total_items_found', 0)}")
        print(f"Records after filter: {result.get('filtered_items', 0)}")
        print(f"Crawled URL: {result.get('url')}")
        print(f"Crawl time: {result.get('timestamp')}")

        # Print to console (first 5 items)
        if result['json_data']:
            print("\n" + "="*60)
            print("JSON Output Example (first 5 items):")
            print("="*60)
            print(json.dumps(result['json_data'][:5], ensure_ascii=False, indent=2))
        else:
            print("\n⚠️ Warning: No news records matching keywords found")
    else:
        print(f"❌ Crawl failed: {result.get('error')}")


# ===== Multi-page crawling notes =====
"""
To crawl historical data from 2024 (for backtesting), you can modify the code as follows:

1. The Federal Reserve website's pagination typically uses query parameters, for example:
   - Page 1: https://www.federalreserve.gov/newsevents/pressreleases.htm
   - Page 2: https://www.federalreserve.gov/newsevents/pressreleases.htm?Page=2
   - Page N: https://www.federalreserve.gov/newsevents/pressreleases.htm?Page=N

2. Method to implement multi-page crawling:

   async def crawl_multiple_pages(self, max_pages: int = 10):
       '''Crawl data from multiple pages'''
       all_news = []

       for page_num in range(1, max_pages + 1):
           url = f"{FED_NEWS_URL}?Page={page_num}"
           result = await self.crawl(url=url, page_num=page_num)

           if result.get("status") == "success":
               all_news.extend(result['json_data'])
               print(f"Page {page_num}: Retrieved {len(result['json_data'])} news items")

               # Add random delay to avoid being rate-limited
               import random
               await asyncio.sleep(random.uniform(1, 3))
           else:
               print(f"Page {page_num}: Crawl failed, stopping")
               break

       return all_news

3. Filter data by date range:

   def filter_by_date_range(self, items: List[Dict], start_date: str, end_date: str) -> List[Dict]:
       '''Filter news by date range (e.g., entire year 2024)'''
       from datetime import datetime

       start = datetime.strptime(start_date, "%Y-%m-%d")
       end = datetime.strptime(end_date, "%Y-%m-%d")

       filtered = [
           item for item in items
           if start <= datetime.strptime(item['date'], "%Y-%m-%d") <= end
       ]

       return filtered

4. Usage:

   async def main():
       crawler = FedNewsCrawler()

       # Crawl first 5 pages
       all_news = await crawler.crawl_multiple_pages(max_pages=5)

       # Filter 2024 data
       news_2024 = crawler.filter_by_date_range(all_news, "2024-01-01", "2024-12-31")

       # Save results
       with open("fed_news_2024.json", 'w') as f:
           json.dump(news_2024, f, indent=2)
"""


if __name__ == "__main__":
    asyncio.run(main())
