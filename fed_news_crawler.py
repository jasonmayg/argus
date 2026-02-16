#!/usr/bin/env python3
"""
ARGUS: Federal Reserve News Crawler
用于抓取美联储News公告，用于全球国库Risk监控
"""

import asyncio
import json
import re
from datetime import datetime
from typing import List, Dict, Optional
from urllib.parse import urljoin

from crawl4ai import AsyncWebCrawler


# ===== Configuration Section =====
# KeywordsFilter列表
KEYWORDS_FILTER = [
    "Payment", "Payments",
    "Regulation", "Regulatory",
    "Bank", "Banking", "Banks",
    "Interest", "Rate", "Rates",
    "Treasury", "Monetary",
    "Financial", "Risk",
    "Stability", "Crisis"
]

# 美联储News页面 URL
FED_NEWS_URL = "https://www.federalreserve.gov/newsevents/pressreleases.htm"

# 输出目录
OUTPUT_DIR = "data/fed_news"


class FedNewsCrawler:
    """美联储NewsCrawler类"""

    def __init__(self, keywords: Optional[List[str]] = None):
        """
        初始化Crawler

        Args:
            keywords: FilterKeywords列表，如果为 None 则使用默认列表
        """
        self.keywords = keywords or KEYWORDS_FILTER
        self.crawler = None
        self.news_items = []

    async def fetch_page(self, url: str, page_num: int = 1) -> str:
        """
        获取单个页面的 HTML 内容

        Args:
            url: 目标 URL
            page_num: 页码（用于处理翻页）

        Returns:
            页面的 HTML 和 Markdown 内容
        """
        print(f"正在抓取第 {page_num} 页: {url}")

        self.crawler = AsyncWebCrawler()
        result = await self.crawler.arun(
            url=url,
            bypass_cache=True,
            wait_for='body',  # 等待 body 加载完成
            wait_until='networkidle',  # 等待网络空闲
            delay_before_return_ms=2000  # 额外延迟以确保 JS 渲染完成
        )

        if result.success:
            print(f"✓ 成功获取页面内容 (Markdown 长度: {len(result.markdown)} 字符)")
            return result.html, result.markdown
        else:
            print(f"✗ 失败: {result.error_message}")
            return None, None

    def extract_news_items(self, markdown: str) -> List[Dict]:
        """
        从 Markdown 中ExtractNews条目

        美联储 Markdown 格式:
        Date (如 2/12/2026)
        [标题](链接)

        Args:
            markdown: 页面 Markdown 内容

        Returns:
            News条目列表
        """
        items = []

        # Markdown 格式: Date单独一行，然后是 [标题](链接) 格式
        # 例如:
        # 2/12/2026
        # [_Federal Reserve Board announces..._](https://www.federalreserve.gov/newsevents/...)

        lines = markdown.split('\n')

        for i, line in enumerate(lines):
            line = line.strip()

            # Check是否是Date行 (格式: M/D/YYYY 或 MM/DD/YYYY)
            if re.match(r'^\d{1,2}/\d{1,2}/\d{4}$', line):
                date_str = line

                # 查找下一个非空行中的标题和链接
                for j in range(i + 1, len(lines)):
                    next_line = lines[j].strip()

                    if not next_line:
                        continue

                    # 匹配 Markdown 链接格式: [标题](链接)
                    # 标题可能包含 _斜体_ 标记
                    link_pattern = r'\[_?([^]_]+)_?\]\(([^)]+)\)'
                    match = re.search(link_pattern, next_line)

                    if match:
                        title = match.group(1).strip()
                        link = match.group(2).strip()

                        # 确保链接是绝对 URL
                        if link.startswith('/'):
                            link = urljoin(FED_NEWS_URL, link)

                        items.append({
                            'date': date_str,
                            'title': title,
                            'link': link
                        })

                        break  # 找到对应的链接后停止查找
                    else:
                        # 如果下一行不是链接，可能是标题+链接在不同行
                        # 或者这不是News行，继续查找
                        break

        print(f"✓ Extract了 {len(items)} 条News记录")
        return items

    def filter_by_keywords(self, items: List[Dict]) -> List[Dict]:
        """
        按KeywordsFilterNews条目

        Args:
            items: News条目列表

        Returns:
            Filter后的News条目列表
        """
        filtered = []

        for item in items:
            title_lower = item['title'].lower()

            # Check标题是否包含任何Keywords
            for keyword in self.keywords:
                if keyword.lower() in title_lower:
                    filtered.append(item)
                    break  # 避免重复添加

        print(f"✓ KeywordsFilter后: {len(filtered)} 条记录 (Filter掉 {len(items) - len(filtered)} 条)")
        return filtered

    def normalize_date(self, date_str: str) -> str:
        """
        将Date字符串标准化为 YYYY-MM-DD 格式

        Args:
            date_str: 原始Date字符串

        Returns:
            标准化的Date字符串
        """
        # 尝试多种Date格式
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

        # 如果无法解析，Return原始字符串
        return date_str.strip()

    def to_json(self) -> str:
        """
        将News条目转换为 JSON 格式

        Returns:
            JSON 字符串
        """
        output = []

        for item in self.news_items:
            output.append({
                "date": self.normalize_date(item['date']),
                "title": item['title'],
                "link": item['link']
            })

        # 按Date倒序排列
        output.sort(key=lambda x: x['date'], reverse=True)

        return json.dumps(output, ensure_ascii=False, indent=2)

    def to_markdown(self, markdown_content: str) -> str:
        """
        将内容转换为 Markdown 格式，便于后续处理

        Args:
            markdown_content: Crawl4AI 提供的 Markdown 内容

        Returns:
            格式化的 Markdown 字符串
        """
        # 创建带有News列表的 Markdown 文档
        md_output = "# 美联储News公告\n\n"
        md_output += f"**抓取时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        md_output += f"**数据来源**: {FED_NEWS_URL}\n"
        md_output += f"**记录数量**: {len(self.news_items)}\n\n"

        md_output += "## 筛选条件\n"
        md_output += f"- **Keywords**: {', '.join(self.keywords)}\n\n"

        md_output += "## News列表\n\n"

        for item in self.news_items:
            normalized_date = self.normalize_date(item['date'])
            md_output += f"### [{item['title']}]({item['link']})\n"
            md_output += f"- **Date**: {normalized_date}\n"
            md_output += f"- **链接**: {item['link']}\n\n"

        return md_output

    async def crawl(self, url: str = FED_NEWS_URL, page_num: int = 1) -> Dict:
        """
        执行Crawler的主流程

        Args:
            url: 抓取的 URL (通常是某一年的News页面)
            page_num: 页码

        Returns:
            包含 JSON 和 Markdown 内容的字典
        """
        try:
            # 1. 获取页面内容
            html, markdown = await self.fetch_page(url, page_num)
            if not markdown:
                return {"error": "Failed to fetch page"}

            # 2. 从 Markdown 中ExtractNews条目
            items = self.extract_news_items(markdown)

            # 3. 按KeywordsFilter
            self.news_items = self.filter_by_keywords(items)

            # 4. 生成输出
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
    """主函数"""
    # 创建Crawler实例
    crawler = FedNewsCrawler()

    # 执行抓取 - 获取 2026 年的News页面
    # 注: 默认抓取当年的News，可通过修改 URL 来改为其他年份
    current_year = datetime.now().year
    news_url = f"https://www.federalreserve.gov/newsevents/pressreleases/{current_year}-press.htm"

    print(f"CrawlerConfiguration:")
    print(f"  目标 URL: {news_url}")
    print(f"  FilterKeywords: {', '.join(crawler.keywords)}")
    print("="*60)

    result = await crawler.crawl(url=news_url)

    if result.get("status") == "success":
        # 保存 JSON Results
        import os
        os.makedirs(OUTPUT_DIR, exist_ok=True)

        json_output_path = f"{OUTPUT_DIR}/fed_news_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(json_output_path, 'w', encoding='utf-8') as f:
            json.dump(result['json_data'], f, ensure_ascii=False, indent=2)
        print(f"\n✓ JSON 数据已保存到: {json_output_path}")

        # 保存 Markdown Results
        md_output_path = f"{OUTPUT_DIR}/fed_news_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        with open(md_output_path, 'w', encoding='utf-8') as f:
            f.write(result['markdown'])
        print(f"✓ Markdown 数据已保存到: {md_output_path}")

        # 打印统计Information
        print("\n" + "="*60)
        print("Crawler统计:")
        print("="*60)
        print(f"找到总记录数: {result.get('total_items_found', 0)}")
        print(f"Filter后记录数: {result.get('filtered_items', 0)}")
        print(f"抓取 URL: {result.get('url')}")
        print(f"抓取时间: {result.get('timestamp')}")

        # 打印到控制台 (前 5 条)
        if result['json_data']:
            print("\n" + "="*60)
            print("JSON 输出Example (前 5 条):")
            print("="*60)
            print(json.dumps(result['json_data'][:5], ensure_ascii=False, indent=2))
        else:
            print("\n⚠️ Warning: 没有找到与Keywords匹配的News记录")
    else:
        print(f"❌ 抓取失败: {result.get('error')}")


# ===== 多页面Crawler注释 =====
"""
如果需要抓取 2024 年的历史数据（回测需求），可以按以下方式修改:

1. 美联储网站的翻页方式通常是通过查询Parameters实现，例如:
   - 第 1 页: https://www.federalreserve.gov/newsevents/pressreleases.htm
   - 第 2 页: https://www.federalreserve.gov/newsevents/pressreleases.htm?Page=2
   - 第 N 页: https://www.federalreserve.gov/newsevents/pressreleases.htm?Page=N

2. 实现多页面抓取的方法:

   async def crawl_multiple_pages(self, max_pages: int = 10):
       '''抓取多个页面的数据'''
       all_news = []

       for page_num in range(1, max_pages + 1):
           url = f"{FED_NEWS_URL}?Page={page_num}"
           result = await self.crawl(url=url, page_num=page_num)

           if result.get("status") == "success":
               all_news.extend(result['json_data'])
               print(f"Page {page_num}: 已获取 {len(result['json_data'])} 条News")

               # 添加随机延迟避免被限制
               import random
               await asyncio.sleep(random.uniform(1, 3))
           else:
               print(f"Page {page_num}: 抓取失败，停止")
               break

       return all_news

3. 按Date范围Filter数据:

   def filter_by_date_range(self, items: List[Dict], start_date: str, end_date: str) -> List[Dict]:
       '''按Date范围FilterNews（例如: 2024 年全年）'''
       from datetime import datetime

       start = datetime.strptime(start_date, "%Y-%m-%d")
       end = datetime.strptime(end_date, "%Y-%m-%d")

       filtered = [
           item for item in items
           if start <= datetime.strptime(item['date'], "%Y-%m-%d") <= end
       ]

       return filtered

4. 调用方式:

   async def main():
       crawler = FedNewsCrawler()

       # 抓取前 5 页
       all_news = await crawler.crawl_multiple_pages(max_pages=5)

       # Filter 2024 年的数据
       news_2024 = crawler.filter_by_date_range(all_news, "2024-01-01", "2024-12-31")

       # 保存Results
       with open("fed_news_2024.json", 'w') as f:
           json.dump(news_2024, f, indent=2)
"""


if __name__ == "__main__":
    asyncio.run(main())
