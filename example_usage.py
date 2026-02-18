#!/usr/bin/env python3
"""
ARGUS: Federal Reserve News Crawler - Usage Examples

Demonstrates how to use the Crawler for news monitoring and analysis in real projects
"""

import asyncio
import json
from datetime import datetime
from fed_news_crawler import FedNewsCrawler
from fed_news_backtest import (
    crawl_multiple_years,
    filter_by_date_range,
    filter_by_keywords
)


async def example_1_basic_crawl():
    """Example 1: Basic crawler usage - crawl current month news"""
    print("\n" + "="*70)
    print("Example 1: Basic Crawler usage - crawl current month news")
    print("="*70)

    crawler = FedNewsCrawler()

    # Get news from 2026
    result = await crawler.crawl(
        url="https://www.federalreserve.gov/newsevents/pressreleases/2026-press.htm"
    )

    if result['status'] == 'success':
        # Print statistics
        print(f"\n📊 Statistics:")
        print(f"  • Total news found: {result['total_items_found']}")
        print(f"  • After filter: {result['filtered_items']}")
        print(f"  • Crawl time: {result['timestamp']}")

        # Print filtered news
        print(f"\n📰 Filtered news (total {len(result['json_data'])} items):")
        for i, news in enumerate(result['json_data'], 1):
            print(f"\n  {i}. [{news['date']}] {news['title']}")
            print(f"     Link: {news['link']}")

        return result['json_data']
    else:
        print(f"❌ Crawler failed: {result.get('error')}")
        return []


async def example_2_custom_keywords():
    """Example 2: Use custom keyword filter"""
    print("\n" + "="*70)
    print("Example 2: Use custom keyword filter - FOMC and interest rate related news")
    print("="*70)

    # Define custom keywords
    fomc_keywords = [
        "FOMC", "Federal Open Market Committee",
        "interest rate", "rate decision",
        "monetary policy", "policy rate"
    ]

    crawler = FedNewsCrawler(keywords=fomc_keywords)

    result = await crawler.crawl(
        url="https://www.federalreserve.gov/newsevents/pressreleases/2026-press.htm"
    )

    if result['status'] == 'success':
        print(f"\nUsing keywords: {', '.join(fomc_keywords)}")
        print(f"News count after filter: {result['filtered_items']}")

        if result['json_data']:
            print(f"\n📄 Results:")
            for news in result['json_data']:
                print(f"  • {news['title']}")
        else:
            print("⚠️ No matching news found")

        return result['json_data']
    else:
        print(f"❌ Crawler failed: {result.get('error')}")
        return []


async def example_3_historical_data():
    """Example 3: Get historical data for backtesting"""
    print("\n" + "="*70)
    print("Example 3: Get historical data for backtesting - crawl 2024 and 2025 data")
    print("="*70)

    # Get multi-year data
    all_news = await crawl_multiple_years(
        years=[2024, 2025],
        output_file="data/fed_news/example_historical.json"
    )

    # Statistics
    print(f"\n📊 Historical data statistics:")
    print(f"  • Total news: {len(all_news)}")

    # Group by year
    by_year = {}
    for item in all_news:
        year = item['date'][:4]
        if year not in by_year:
            by_year[year] = []
        by_year[year].append(item)

    for year in sorted(by_year.keys()):
        print(f"  • Year {year}: {len(by_year[year])} news items")

    return all_news


async def example_4_date_filtering():
    """Example 4: Filter by date range"""
    print("\n" + "="*70)
    print("Example 4: Filter by date range - filter 2025 Q4 news")
    print("="*70)

    # First fetch data
    all_news = await crawl_multiple_years(
        years=[2025],
        keywords=["regulation", "bank", "interest"]
    )

    # Filter Q4 (Oct-Dec)
    q4_news = filter_by_date_range(
        all_news,
        start_date="2025-10-01",
        end_date="2025-12-31"
    )

    print(f"\n📅 Date range filter:")
    print(f"  • Original data: {len(all_news)} items")
    print(f"  • Q4 data: {len(q4_news)} items")

    if q4_news:
        print(f"\nNews from 2025 Q4:")
        for news in q4_news[:5]:  # Show first 5 items only
            print(f"  • [{news['date']}] {news['title'][:80]}...")


async def example_5_save_and_load():
    """Example 5: Save and load crawler results"""
    print("\n" + "="*70)
    print("Example 5: Save and load crawler results")
    print("="*70)

    # 1. Crawl and save
    crawler = FedNewsCrawler(keywords=["bank", "regulation"])
    result = await crawler.crawl(
        url="https://www.federalreserve.gov/newsevents/pressreleases/2026-press.htm"
    )

    # 2. Save as JSON
    output_file = "data/fed_news/example_save.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(result['json_data'], f, ensure_ascii=False, indent=2)
    print(f"\n💾 Saved to: {output_file}")

    # 3. Load from file
    with open(output_file, 'r', encoding='utf-8') as f:
        loaded_data = json.load(f)
    print(f"📂 Loaded {len(loaded_data)} news items")

    # 4. Process loaded data
    print(f"\nSummary of loaded news:")
    for news in loaded_data[:3]:
        print(f"  • [{news['date']}] {news['title'][:70]}...")


async def example_6_risk_analysis():
    """Example 6: For risk analysis - count risk-related news"""
    print("\n" + "="*70)
    print("Example 6: For risk analysis - count risk-related news")
    print("="*70)

    # Define risk-related keywords
    risk_keywords = [
        "risk", "crisis", "instability",
        "default", "stress test", "capital",
        "leverage", "liquidity"
    ]

    all_news = await crawl_multiple_years(
        years=[2024, 2025],
        keywords=risk_keywords
    )

    print(f"\n⚠️ Risk-related news statistics:")
    print(f"  • Found {len(all_news)} risk-related news items during 2024-2025")

    # Group by month
    by_month = {}
    for item in all_news:
        month = item['date'][:7]  # YYYY-MM
        if month not in by_month:
            by_month[month] = 0
        by_month[month] += 1

    print(f"\nDistribution by month:")
    for month in sorted(by_month.keys()):
        print(f"  • {month}: {by_month[month]} items")

    # Show latest risk news
    print(f"\nLatest risk-related news:")
    for news in all_news[:5]:
        print(f"  • [{news['date']}] {news['title']}")


async def main():
    """Run all examples"""
    print("\n" + "🤖 "*35)
    print("ARGUS Federal Reserve News Crawler - Usage Examples")
    print("🤖 "*35)

    try:
        # Run various examples
        # Note: Uncomment to run specific examples, or run all

        # example_1_basic_crawl()  # Basic usage
        # await example_2_custom_keywords()  # Custom keywords
        # await example_3_historical_data()  # Historical data
        # await example_4_date_filtering()  # Date filter
        # await example_5_save_and_load()  # Save and load
        # await example_6_risk_analysis()  # Risk analysis

        # Run Example 1: Basic crawler
        news_data = await example_1_basic_crawl()

        # To run other examples, uncomment the line below
        # await example_2_custom_keywords()
        # await example_3_historical_data()

    except Exception as e:
        print(f"\n❌ Error occurred: {e}")
        import traceback
        traceback.print_exc()

    print("\n" + "="*70)
    print("✅ Example execution completed")
    print("="*70)


if __name__ == "__main__":
    asyncio.run(main())
