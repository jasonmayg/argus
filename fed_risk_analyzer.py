#!/usr/bin/env python3
"""
ARGUS: Federal Reserve News Risk Analyzer
Uses Claude API to automatically analyze risk signals in Federal Reserve news
"""

import asyncio
import json
import os
from datetime import datetime
from typing import Optional, Dict, List

import anthropic

from fed_news_crawler import FedNewsCrawler


class FedRiskAnalyzer:
    """Federal Reserve News Risk Analyzer"""

    def __init__(self, api_key: Optional[str] = None, model: str = "claude-opus-4-6"):
        """
        Initialize the analyzer

        Args:
            api_key: Anthropic API key (if None, will be read from environment variables)
            model: Claude model to use
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
        Analyze risk for a batch of news items

        Args:
            news_items: List of news items
            risk_categories: Risk categories to analyze

        Returns:
            Risk analysis results
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

        # Format news as Markdown
        news_markdown = self._format_news_markdown(news_items)

        # Build analysis prompt
        prompt = self._build_analysis_prompt(news_markdown, risk_categories)

        print(f"Analyzing {len(news_items)} news items...")
        print(f"Risk categories: {', '.join(risk_categories)}")
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
        """Format news as Markdown"""
        markdown = "# Federal Reserve News Summary\n\n"
        markdown += f"**Summary Time**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        markdown += f"**News Count**: {len(news_items)}\n\n"

        for i, item in enumerate(news_items, 1):
            markdown += f"## {i}. {item['title']}\n"
            markdown += f"- **Date**: {item['date']}\n"
            markdown += f"- **Link**: {item['link']}\n\n"

        return markdown

    def _build_analysis_prompt(self, news_markdown: str, categories: List[str]) -> str:
        """Build Claude's analysis prompt"""
        categories_str = "\n".join([f"- {cat}" for cat in categories])

        prompt = f"""As a financial risk analysis expert, please analyze the risk signals in the following Federal Reserve news.

## News Content

{news_markdown}

## Analysis Requirements

Please analyze the impact of these news items on the following risk categories:

{categories_str}

## Analysis Framework

For each risk category, please provide:
1. **Risk Signals**: Specific risk signals identified from the news
2. **Severity Level**: Low/Medium/High (1-10 scale)
3. **Impact Scope**: Potential impact of the news on treasury management operations
4. **Recommended Actions**: Suggested measures to address identified risks

## Overall Assessment

Finally, please provide:
- **Overall Risk Level**: Low/Medium/High
- **Key Risk Summary**: Most important 3-5 risks
- **Monitoring Recommendations**: Suggested areas to continue monitoring

Please present the analysis results in a structured and actionable format."""

        return prompt

    def save_analysis(self, analysis: Dict, output_file: Optional[str] = None) -> str:
        """
        Save analysis results

        Args:
            analysis: Analysis results dictionary
            output_file: Output file path

        Returns:
            Path to saved file
        """
        if not output_file:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_file = f"data/fed_news/risk_analysis_{timestamp}.json"

        os.makedirs(os.path.dirname(output_file), exist_ok=True)

        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(analysis, f, ensure_ascii=False, indent=2)

        print(f"\n✓ Analysis results saved to: {output_file}")
        return output_file


async def main():
    """Main function"""
    print("\n" + "="*70)
    print("ARGUS: Federal Reserve News Risk Analysis")
    print("="*70)

    # 1. Crawl news
    print("\n[Step 1/3] Crawl Federal Reserve news...")
    crawler = FedNewsCrawler()
    result = await crawler.crawl(
        url="https://www.federalreserve.gov/newsevents/pressreleases/2026-press.htm"
    )

    if result['status'] != 'success':
        print(f"❌ Crawler failed: {result.get('error')}")
        return

    news_items = result['json_data']
    print(f"✓ Successfully retrieved {len(news_items)} news items")

    # 2. Initialize analyzer
    print("\n[Step 2/3] Initialize Claude API analyzer...")
    analyzer = FedRiskAnalyzer()

    # 3. Execute risk analysis
    print("\n[Step 3/3] Execute risk analysis...")
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

    # 4. Display results
    if analysis['status'] == 'success':
        print("\n" + "="*70)
        print("Risk Analysis Results")
        print("="*70)
        print(analysis['analysis'])

        # 5. Save results
        print("\n" + "="*70)
        print("Saving Results...")
        print("="*70)
        analyzer.save_analysis(analysis)

        # 6. Print statistics
        print("\n" + "="*70)
        print("Statistics")
        print("="*70)
        print(f"Analysis time: {analysis['timestamp']}")
        print(f"News count: {analysis['news_count']}")
        print(f"Model used: {analysis['model']}")
        print(f"Token usage:")
        print(f"  - Input: {analysis['tokens_used']['input']}")
        print(f"  - Output: {analysis['tokens_used']['output']}")
    else:
        print(f"❌ Analysis failed: {analysis['error']}")


if __name__ == "__main__":
    asyncio.run(main())
