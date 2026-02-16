#!/usr/bin/env python3
"""
ARGUS: 美联储NewsCrawler - 使用Example

展示如何在实际项目中使用Crawler进行News监控和分析
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
    """Example 1: 基本Crawler用法 - 抓取当前月份News"""
    print("\n" + "="*70)
    print("Example 1: 基本Crawler用法 - 抓取当前月份的News")
    print("="*70)

    crawler = FedNewsCrawler()

    # 获取 2026 年的News
    result = await crawler.crawl(
        url="https://www.federalreserve.gov/newsevents/pressreleases/2026-press.htm"
    )

    if result['status'] == 'success':
        # 打印统计Information
        print(f"\n📊 统计Information:")
        print(f"  • 找到News总数: {result['total_items_found']}")
        print(f"  • Filter后数量: {result['filtered_items']}")
        print(f"  • 抓取时间: {result['timestamp']}")

        # 打印Filter后的News
        print(f"\n📰 Filter后的News (共 {len(result['json_data'])} 条):")
        for i, news in enumerate(result['json_data'], 1):
            print(f"\n  {i}. [{news['date']}] {news['title']}")
            print(f"     Link: {news['link']}")

        return result['json_data']
    else:
        print(f"❌ Crawler失败: {result.get('error')}")
        return []


async def example_2_custom_keywords():
    """Example 2: 使用自定义KeywordsFilter"""
    print("\n" + "="*70)
    print("Example 2: 使用自定义KeywordsFilter - 仅保留 FOMC 和利率相关News")
    print("="*70)

    # 定义自定义Keywords
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
        print(f"\n使用Keywords: {', '.join(fomc_keywords)}")
        print(f"Filter后News数: {result['filtered_items']}")

        if result['json_data']:
            print(f"\n📄 Results:")
            for news in result['json_data']:
                print(f"  • {news['title']}")
        else:
            print("⚠️ 没有找到匹配的News")

        return result['json_data']
    else:
        print(f"❌ Crawler失败: {result.get('error')}")
        return []


async def example_3_historical_data():
    """Example 3: 获取历史数据用于回测"""
    print("\n" + "="*70)
    print("Example 3: 获取历史数据用于回测 - 抓取 2024 和 2025 年数据")
    print("="*70)

    # 获取多年数据
    all_news = await crawl_multiple_years(
        years=[2024, 2025],
        output_file="data/fed_news/example_historical.json"
    )

    # 统计Information
    print(f"\n📊 历史数据统计:")
    print(f"  • 总News数: {len(all_news)}")

    # 按年份分组
    by_year = {}
    for item in all_news:
        year = item['date'][:4]
        if year not in by_year:
            by_year[year] = []
        by_year[year].append(item)

    for year in sorted(by_year.keys()):
        print(f"  • {year} 年: {len(by_year[year])} 条News")

    return all_news


async def example_4_date_filtering():
    """Example 4: 按Date范围Filter"""
    print("\n" + "="*70)
    print("Example 4: 按Date范围Filter - Filter 2025 年 Q4 的News")
    print("="*70)

    # 先获取数据
    all_news = await crawl_multiple_years(
        years=[2025],
        keywords=["regulation", "bank", "interest"]
    )

    # Filter Q4 (10月-12月)
    q4_news = filter_by_date_range(
        all_news,
        start_date="2025-10-01",
        end_date="2025-12-31"
    )

    print(f"\n📅 Date范围Filter:")
    print(f"  • 原始数据: {len(all_news)} 条")
    print(f"  • Q4 数据: {len(q4_news)} 条")

    if q4_news:
        print(f"\n2025 年 Q4 的News:")
        for news in q4_news[:5]:  # 只显示前 5 条
            print(f"  • [{news['date']}] {news['title'][:80]}...")


async def example_5_save_and_load():
    """Example 5: 保存和加载CrawlerResults"""
    print("\n" + "="*70)
    print("Example 5: 保存和加载CrawlerResults")
    print("="*70)

    # 1. Crawler并保存
    crawler = FedNewsCrawler(keywords=["bank", "regulation"])
    result = await crawler.crawl(
        url="https://www.federalreserve.gov/newsevents/pressreleases/2026-press.htm"
    )

    # 2. 保存为 JSON
    output_file = "data/fed_news/example_save.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(result['json_data'], f, ensure_ascii=False, indent=2)
    print(f"\n💾 已保存到: {output_file}")

    # 3. 从File加载
    with open(output_file, 'r', encoding='utf-8') as f:
        loaded_data = json.load(f)
    print(f"📂 已加载 {len(loaded_data)} 条News")

    # 4. 对加载的数据进行处理
    print(f"\n加载的News摘要:")
    for news in loaded_data[:3]:
        print(f"  • [{news['date']}] {news['title'][:70]}...")


async def example_6_risk_analysis():
    """Example 6: 用于Risk分析 - 统计Risk相关News"""
    print("\n" + "="*70)
    print("Example 6: 用于Risk分析 - 统计Risk相关News")
    print("="*70)

    # 定义Risk相关的Keywords
    risk_keywords = [
        "risk", "crisis", "instability",
        "default", "stress test", "capital",
        "leverage", "liquidity"
    ]

    all_news = await crawl_multiple_years(
        years=[2024, 2025],
        keywords=risk_keywords
    )

    print(f"\n⚠️ Risk相关News统计:")
    print(f"  • 2024-2025 年期间找到 {len(all_news)} 条Risk相关News")

    # 按月份统计
    by_month = {}
    for item in all_news:
        month = item['date'][:7]  # YYYY-MM
        if month not in by_month:
            by_month[month] = 0
        by_month[month] += 1

    print(f"\n按月份分布:")
    for month in sorted(by_month.keys()):
        print(f"  • {month}: {by_month[month]} 条")

    # 显示最新的RiskNews
    print(f"\n最新的Risk相关News:")
    for news in all_news[:5]:
        print(f"  • [{news['date']}] {news['title']}")


async def main():
    """运行所有Example"""
    print("\n" + "🤖 "*35)
    print("ARGUS 美联储NewsCrawler - 使用Example")
    print("🤖 "*35)

    try:
        # 运行各个Example
        # 注意: 取消注释以运行特定Example，或运行全部

        # example_1_basic_crawl()  # 基本用法
        # await example_2_custom_keywords()  # 自定义Keywords
        # await example_3_historical_data()  # 历史数据
        # await example_4_date_filtering()  # DateFilter
        # await example_5_save_and_load()  # 保存和加载
        # await example_6_risk_analysis()  # Risk分析

        # 运行Example 1: 基本Crawler
        news_data = await example_1_basic_crawl()

        # 如果想运行其他Example，取消下行注释
        # await example_2_custom_keywords()
        # await example_3_historical_data()

    except Exception as e:
        print(f"\n❌ 发生Error: {e}")
        import traceback
        traceback.print_exc()

    print("\n" + "="*70)
    print("✅ Example运行完成")
    print("="*70)


if __name__ == "__main__":
    asyncio.run(main())
