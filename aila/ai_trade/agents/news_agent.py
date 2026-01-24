"""
NEWS AGENT — monitors news and sentiment.
Crypto news can move prices 10-50% in minutes.
"""

import json
import logging
import aiohttp
from datetime import datetime
from .base_agent import BaseAgent
from ..api_keys import API_KEYS

logger = logging.getLogger("ai_trade")


class NewsAgent(BaseAgent):
    """
    News agent - monitors crypto news, analyzes impact,
    detects breaking news, tracks market sentiment.
    """

    def __init__(self, claude_client, knowledge_base):
        super().__init__(
            name="NEWS",
            claude_client=claude_client,
            knowledge_base=knowledge_base,
            log_path="/opt/aila/logs/ai_trade/news.log"
        )

        # Key accounts to watch
        self.key_accounts = [
            "CZ_Binance", "VitalikButerin", "elonmusk",
            "whale_alert", "SEC_Enforcement",
        ]

        # Urgent keywords that require immediate reaction
        self.urgent_keywords = [
            "hack", "exploit", "breach", "stolen",
            "SEC", "lawsuit", "regulation", "ban",
            "listing", "delist",
            "partnership", "acquisition",
            "ETF", "approval", "rejected",
            "bankruptcy", "insolvency",
            "airdrop", "fork", "upgrade",
        ]

        # Fear & Greed API
        self.fng_api = "https://api.alternative.me/fng/"

    async def think(self, context: dict) -> dict:
        """Process news context."""
        action = context.get("action", "sentiment")
        if action == "breaking":
            return await self.detect_breaking_news()
        elif action == "pair_sentiment":
            return await self.get_pair_sentiment(context.get("pair", "BTCUSDT"))
        return await self.get_market_sentiment()

    async def get_latest_news(self, limit: int = 20) -> list:
        """Get latest crypto news from available sources."""
        news = []

        # Try CryptoPanic API
        cryptopanic_key = API_KEYS.get("cryptopanic", "")
        if cryptopanic_key:
            try:
                async with aiohttp.ClientSession() as session:
                    url = "https://cryptopanic.com/api/v1/posts/"
                    params = {
                        "auth_token": cryptopanic_key,
                        "kind": "news",
                        "filter": "important",
                        "limit": limit,
                    }
                    async with session.get(url, params=params, timeout=10) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            for post in data.get("results", []):
                                news.append({
                                    "title": post.get("title", ""),
                                    "content": post.get("title", ""),  # CryptoPanic only gives titles
                                    "source": post.get("source", {}).get("title", "unknown"),
                                    "url": post.get("url", ""),
                                    "timestamp": post.get("published_at", ""),
                                    "currencies": [c.get("code") for c in post.get("currencies", [])],
                                    "votes": post.get("votes", {}),
                                })
            except Exception as e:
                self.log(f"CryptoPanic API error: {e}", "error")

        # Try NewsAPI as fallback
        newsapi_key = API_KEYS.get("newsapi", "")
        if newsapi_key and not news:
            try:
                async with aiohttp.ClientSession() as session:
                    url = "https://newsapi.org/v2/everything"
                    params = {
                        "apiKey": newsapi_key,
                        "q": "cryptocurrency OR bitcoin OR ethereum",
                        "language": "en",
                        "sortBy": "publishedAt",
                        "pageSize": limit,
                    }
                    async with session.get(url, params=params, timeout=10) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            for article in data.get("articles", []):
                                news.append({
                                    "title": article.get("title", ""),
                                    "content": article.get("description", ""),
                                    "source": article.get("source", {}).get("name", "unknown"),
                                    "url": article.get("url", ""),
                                    "timestamp": article.get("publishedAt", ""),
                                    "currencies": [],
                                })
            except Exception as e:
                self.log(f"NewsAPI error: {e}", "error")

        self.log(f"Fetched {len(news)} news items")
        return news

    async def analyze_news_impact(self, news: dict) -> dict:
        """Evaluate the market impact of a news item."""
        prompt = f"""You are a crypto market expert. Evaluate this news impact:

NEWS:
Title: {news.get('title', '')}
Content: {news.get('content', '')}
Source: {news.get('source', '')}
Time: {news.get('timestamp', '')}
Currencies: {news.get('currencies', [])}

EVALUATE:
1. Bullish or Bearish?
2. Impact strength (1-10)?
3. Which coins affected?
4. How fast will market react?
5. How long will the effect last?

Respond in JSON:
{{
    "sentiment": "bullish" | "bearish" | "neutral",
    "impact_score": 1-10,
    "affected_pairs": ["BTCUSDT", "ETHUSDT"],
    "reaction_time": "immediate" | "hours" | "days",
    "effect_duration": "minutes" | "hours" | "days" | "weeks",
    "recommended_action": "buy" | "sell" | "wait" | "close_positions",
    "reasoning": "explanation",
    "price_impact_estimate_pct": -50 to +50
}}"""
        result = await self.claude_client.analyze(prompt)

        if "error" not in result:
            self.log(f"News impact: {result.get('sentiment')} "
                     f"(score={result.get('impact_score')}): {news.get('title', '')[:60]}")

        return result

    async def get_market_sentiment(self) -> dict:
        """
        Overall market sentiment.
        Uses Fear & Greed Index + AI analysis.
        """
        fng_data = await self._get_fear_greed_index()

        prompt = f"""Evaluate the current crypto market sentiment:

FEAR & GREED INDEX:
Value: {fng_data.get('value', 'unknown')}
Classification: {fng_data.get('classification', 'unknown')}
Yesterday: {fng_data.get('yesterday', 'unknown')}
Last week: {fng_data.get('last_week', 'unknown')}

CONTRARIAN ANALYSIS:
- Extreme Fear (0-25) = potential buying opportunity
- Fear (25-45) = cautious buying
- Neutral (45-55) = wait
- Greed (55-75) = cautious selling
- Extreme Greed (75-100) = potential selling opportunity

Respond in JSON:
{{
    "overall_sentiment": "extreme_fear" | "fear" | "neutral" | "greed" | "extreme_greed",
    "sentiment_score": 0-100,
    "market_phase": "accumulation" | "markup" | "distribution" | "markdown",
    "recommendation": "explanation",
    "contrarian_signal": "what contrarian approach suggests",
    "risk_level": "low" | "medium" | "high" | "extreme"
}}"""
        result = await self.claude_client.analyze(prompt)

        if "error" not in result:
            self.log(f"Market sentiment: {result.get('overall_sentiment')} "
                     f"(score={result.get('sentiment_score')})")

        return result

    async def detect_breaking_news(self) -> dict:
        """
        Detect breaking news that requires immediate reaction.
        """
        news = await self.get_latest_news(limit=10)

        for item in news:
            title_lower = item.get("title", "").lower()
            content_lower = item.get("content", "").lower()

            # Check for urgent keywords
            for keyword in self.urgent_keywords:
                if keyword.lower() in title_lower or keyword.lower() in content_lower:
                    impact = await self.analyze_news_impact(item)

                    if impact.get("impact_score", 0) >= 7:
                        self.log(f"BREAKING NEWS detected: {item.get('title', '')[:80]}", "warning")

                        # Log event
                        event = self.create_event(
                            action="BREAKING_NEWS",
                            data={
                                "title": item.get("title"),
                                "impact_score": impact.get("impact_score"),
                                "sentiment": impact.get("sentiment"),
                                "affected_pairs": impact.get("affected_pairs", []),
                            },
                            message=f"BREAKING: {item.get('title', '')[:80]}"
                        )

                        return {
                            "is_breaking": True,
                            "news": item,
                            "impact": impact,
                            "event": event,
                        }
                    break  # Only check first matching keyword per news item

        return {"is_breaking": False}

    async def get_pair_sentiment(self, pair: str) -> dict:
        """Sentiment analysis for a specific pair."""
        base_asset = pair.replace("USDT", "").replace("/USDT", "").replace(":USDT", "")

        # Get any pair-specific news
        news = await self.get_latest_news(limit=10)
        pair_news = [
            n for n in news
            if base_asset in str(n.get("currencies", [])) or
               base_asset.lower() in n.get("title", "").lower()
        ]

        news_context = ""
        if pair_news:
            news_context = f"\nRecent {base_asset} news:\n"
            for n in pair_news[:3]:
                news_context += f"- {n.get('title', '')}\n"

        prompt = f"""Evaluate current sentiment for {base_asset}:
{news_context}
Consider:
- Recent news and announcements
- Social media activity and trends
- Technical updates and development activity
- Market structure and whale behavior
- Upcoming events (upgrades, halvings, unlocks)

Respond in JSON:
{{
    "pair": "{pair}",
    "sentiment": "very_bearish" | "bearish" | "neutral" | "bullish" | "very_bullish",
    "sentiment_score": -100 to +100,
    "key_factors": ["factor1", "factor2"],
    "upcoming_events": ["event1", "event2"],
    "risk_factors": ["risk1", "risk2"],
    "news_impact": "positive" | "negative" | "neutral" | "no_news",
    "social_buzz": "low" | "medium" | "high"
}}"""
        result = await self.claude_client.analyze(prompt)

        if "error" not in result:
            self.log(f"{pair} sentiment: {result.get('sentiment')} "
                     f"(score={result.get('sentiment_score')})")

        return result

    async def _get_fear_greed_index(self) -> dict:
        """Fetch Fear & Greed Index from API."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self.fng_api, timeout=5) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        entries = data.get("data", [])
                        if entries:
                            current = entries[0]
                            yesterday = entries[1] if len(entries) > 1 else {}
                            last_week = entries[6] if len(entries) > 6 else {}
                            return {
                                "value": int(current.get("value", 50)),
                                "classification": current.get("value_classification", "Neutral"),
                                "yesterday": int(yesterday.get("value", 50)),
                                "last_week": int(last_week.get("value", 50)),
                            }
        except Exception as e:
            self.log(f"Fear & Greed API error: {e}", "error")

        return {"value": 50, "classification": "Neutral", "yesterday": 50, "last_week": 50}
