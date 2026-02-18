#!/usr/bin/env python3
"""
ARGUS: Federal Reserve News Backtest Crawler
For fetching historical news data, supports multiple years and date range filtering
"""

import asyncio
import json
from datetime import datetime
from typing import List, Dict, Optional

from fed_news_crawler import FedNewsCrawler


async def crawl_multiple_years(
    years: List[int],
    keywords: Optional[List[str]] = None,
    output_file: Optional[str] = None
) -> List[Dict]:
    """
    Crawl news data from multiple years

    Args:
        years: List of years to crawl (e.g., [2024, 2025, 2026])
        keywords: List of filter keywords
        output_file: Output file path

    Returns:
        List of news data from all years
    """
    crawler = FedNewsCrawler(keywords=keywords)
    all_news = []

    print(f"Starting to crawl news data for {len(years)} years...")
    print(f"Years: {years}")
    print(f"Keywords: {', '.join(crawler.keywords)}")
    print("="*60)

    for year in years:
        url = f"https://www.federalreserve.gov/newsevents/pressreleases/{year}-press.htm"

        print(f"\nCrawling data for {year}...")
        result = await crawler.crawl(url=url)

        if result.get("status") == "success":
            year_news = result['json_data']
            all_news.extend(year_news)

            print(f"  ✓ {year}: Found {result.get('total_items_found')} news items, "
                  f"filtered to {result.get('filtered_items')} items")

            # Add random delay to avoid rate limiting
            import random
            await asyncio.sleep(random.uniform(1, 2))
        else:
            print(f"  ✗ {year}: Crawl failed - {result.get('error')}")

    # Sort by date in descending order
    all_news.sort(key=lambda x: x['date'], reverse=True)

    print("\n" + "="*60)
    print(f"✓ Crawl complete: Retrieved {len(all_news)} news items")

    # Save results
    if output_file:
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(all_news, f, ensure_ascii=False, indent=2)
        print(f"✓ Data saved to: {output_file}")

    return all_news


def filter_by_date_range(
    items: List[Dict],
    start_date: str,
    end_date: str
) -> List[Dict]:
    """
    Filter news by date range (format: YYYY-MM-DD)

    Args:
        items: List of news items
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD)

    Returns:
        Filtered list of news items
    """
    from datetime import datetime

    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()

    filtered = [
        item for item in items
        if start <= datetime.strptime(item['date'], "%Y-%m-%d").date() <= end
    ]

    return filtered


def filter_by_keywords(items: List[Dict], keywords: List[str]) -> List[Dict]:
    """
    Filter news by keywords

    Args:
        items: List of news items
        keywords: List of keywords

    Returns:
        Filtered list of news items
    """
    filtered = []

    for item in items:
        title_lower = item['title'].lower()

        # Check if title contains any keywords
        for keyword in keywords:
            if keyword.lower() in title_lower:
                filtered.append(item)
                break  # Avoid duplicate additions

    return filtered


async def main():
    """
    Example: Crawl news data from 2024 and 2025

    Notes:
    - To use different years, modify the years list
    - To use different keywords, modify the keywords list
    - For specific date range, use filter_by_date_range()
    """

    # ===== Example 1: Crawl full year data for 2024 and 2025 =====
    print("\n" + "="*60)
    print("Example 1: Crawl news data for 2024 and 2025")
    print("="*60)

    backtest_data = await crawl_multiple_years(
        years=[2024, 2025],
        output_file="data/fed_news/fed_news_backtest_2024_2025.json"
    )

    # ===== Example 2: Filter data for specific date range =====
    print("\n" + "="*60)
    print("Example 2: Filter 2024 Q4 (Oct-Dec) data")
    print("="*60)

    q4_data = filter_by_date_range(
        backtest_data,
        start_date="2024-10-01",
        end_date="2024-12-31"
    )

    print(f"✓ Q4 data: {len(q4_data)} records")
    if q4_data:
        print("\nFirst 5:")
        for item in q4_data[:5]:
            print(f"  - [{item['date']}] {item['title'][:80]}...")

    # ===== Example 3: Use custom keyword filter =====
    print("\n" + "="*60)
    print("Example 3: Filter news containing specific keywords")
    print("="*60)

    custom_keywords = ["FOMC", "interest", "monetary policy", "rate decision"]
    filtered_data = filter_by_keywords(backtest_data, custom_keywords)

    print(f"✓ Custom keyword filter: {len(filtered_data)} records")
    if filtered_data:
        print("\nFirst 3:")
        for item in filtered_data[:3]:
            print(f"  - [{item['date']}] {item['title']}")

    # ===== Example 4: Save filtered data =====
    output_path = "data/fed_news/fed_news_filtered_2024_2025.json"
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(filtered_data, f, ensure_ascii=False, indent=2)
    print(f"\n✓ Filtered data saved to: {output_path}")


if __name__ == "__main__":
    asyncio.run(main())
