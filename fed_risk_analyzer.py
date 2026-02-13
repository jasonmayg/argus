#!/usr/bin/env python3
"""
ARGUS: Federal Reserve News Risk Analyzer
使用 Claude API 自动分析美联储News中的Risk信号
"""

import asyncio
import json
import os
from datetime import datetime
from typing import Optional, Dict, List

import anthropic

from fed_news_crawler import FedNewsCrawler


class FedRiskAnalyzer:
    """美联储NewsRisk分析器"""

    def __init__(self, api_key: Optional[str] = None, model: str = "claude-opus-4-6"):
        """
        初始化分析器

        Args:
            api_key: Anthropic API 密钥（如果为 None，将从环境变量Read）
            model: 使用的 Claude 模型
        """
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        self.model = model
        self.client = anthropic.Anthropic(api_key=self.api_key)

    async def analyze_news_batch(
        self,
        news_items: List[Dict],
        risk_categories: Optional[List[str]] = None
    ) -> Dict:
        """
        批量分析News项目的Risk

        Args:
            news_items: News项目列表
            risk_categories: 要分析的Risk类别

        Returns:
            Risk分析Results
        """
        if not risk_categories:
            risk_categories = [
                "Interest Rate Risk",
                "Monetary Policy Risk",
                "Banking Stability Risk",
                "Regulatory Change Risk",
                "Systemic Risk"
            ]

        if not news_items:
            return {"error": "No news items to analyze"}

        # 格式化News为 Markdown
        news_markdown = self._format_news_markdown(news_items)

        # 构建分析提示
        prompt = self._build_analysis_prompt(news_markdown, risk_categories)

        print(f"正在分析 {len(news_items)} 条News...")
        print(f"Risk类别: {', '.join(risk_categories)}")
        print("="*60)

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=2000,
                messages=[
                    {
                        "role": "user",
                        "content": prompt
                    }
                ]
            )

            analysis_text = response.content[0].text

            result = {
                "status": "success",
                "timestamp": datetime.now().isoformat(),
                "news_count": len(news_items),
                "model": self.model,
                "risk_categories": risk_categories,
                "analysis": analysis_text,
                "tokens_used": {
                    "input": response.usage.input_tokens,
                    "output": response.usage.output_tokens
                }
            }

            return result

        except Exception as e:
            return {
                "status": "error",
                "error": str(e)
            }

    def _format_news_markdown(self, news_items: List[Dict]) -> str:
        """格式化News为 Markdown"""
        markdown = "# 美联储News汇总\n\n"
        markdown += f"**汇总时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        markdown += f"**News条数**: {len(news_items)}\n\n"

        for i, item in enumerate(news_items, 1):
            markdown += f"## {i}. {item['title']}\n"
            markdown += f"- **Date**: {item['date']}\n"
            markdown += f"- **链接**: {item['link']}\n\n"

        return markdown

    def _build_analysis_prompt(self, news_markdown: str, categories: List[str]) -> str:
        """构建 Claude 的分析提示"""
        categories_str = "\n".join([f"- {cat}" for cat in categories])

        prompt = f"""作为一名金融Risk分析专家，请分析以下美联储News中的Risk信号。

## News内容

{news_markdown}

## 分析要求

请分析这些News对以下Risk类别的影响：

{categories_str}

## 分析框架

对于每个Risk类别，请提供：
1. **Risk信号**: 从News中识别的具体Risk信号
2. **严重程度**: 低/中/高（1-10 分）
3. **影响范围**: News对 Alphabet 国库Management的潜在影响
4. **建议行动**: 针对识别的Risk的建议对策

## 总体评估

最后，请提供：
- **总体Risk等级**: 低/中/高
- **关键Risk汇总**: 最重要的 3-5 个Risk
- **监控建议**: 建议继续关注的领域

请以结构化且易于操作的方式呈现分析Results。"""

        return prompt

    def save_analysis(self, analysis: Dict, output_file: Optional[str] = None) -> str:
        """
        保存分析Results

        Args:
            analysis: 分析Results字典
            output_file: 输出FilePath

        Returns:
            保存的FilePath
        """
        if not output_file:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_file = f"data/fed_news/risk_analysis_{timestamp}.json"

        os.makedirs(os.path.dirname(output_file), exist_ok=True)

        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(analysis, f, ensure_ascii=False, indent=2)

        print(f"\n✓ 分析Results已保存到: {output_file}")
        return output_file


async def main():
    """主函数"""
    print("\n" + "="*70)
    print("ARGUS: 美联储NewsRisk分析")
    print("="*70)

    # 1. 爬取News
    print("\n[步骤 1/3] 爬取美联储News...")
    crawler = FedNewsCrawler()
    result = await crawler.crawl(
        url="https://www.federalreserve.gov/newsevents/pressreleases/2026-press.htm"
    )

    if result['status'] != 'success':
        print(f"❌ Crawler失败: {result.get('error')}")
        return

    news_items = result['json_data']
    print(f"✓ 成功获取 {len(news_items)} 条News")

    # 2. 初始化分析器
    print("\n[步骤 2/3] 初始化 Claude API 分析器...")
    analyzer = FedRiskAnalyzer()

    # 3. 执行Risk分析
    print("\n[步骤 3/3] 执行Risk分析...")
    analysis = await analyzer.analyze_news_batch(
        news_items=news_items,
        risk_categories=[
            "Interest Rate Risk",
            "Monetary Policy Risk",
            "Banking Stability Risk",
            "Regulatory Change Risk",
            "Treasury Operations Risk"
        ]
    )

    # 4. 显示Results
    if analysis['status'] == 'success':
        print("\n" + "="*70)
        print("Risk分析Results")
        print("="*70)
        print(analysis['analysis'])

        # 5. 保存Results
        print("\n" + "="*70)
        print("保存Results...")
        print("="*70)
        analyzer.save_analysis(analysis)

        # 6. 打印统计Information
        print("\n" + "="*70)
        print("统计Information")
        print("="*70)
        print(f"分析时间: {analysis['timestamp']}")
        print(f"News数量: {analysis['news_count']}")
        print(f"使用模型: {analysis['model']}")
        print(f"Token 使用:")
        print(f"  - 输入: {analysis['tokens_used']['input']}")
        print(f"  - 输出: {analysis['tokens_used']['output']}")
    else:
        print(f"❌ 分析失败: {analysis['error']}")


if __name__ == "__main__":
    asyncio.run(main())
