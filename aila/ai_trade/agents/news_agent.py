"""NEWS AGENT - monitors news and market sentiment."""

import json
import logging
from typing import Any, Optional

import aiohttp

from ...utils.common import TTLCache, retry_async
from ..api_keys import API_KEYS
from .base_agent import BaseAgent

logger = logging.getLogger("ai_trade")

URGENT_KEYWORDS = [
    "hack", "exploit", "breach", "stolen",
    "SEC", "lawsuit", "regulation", "ban",
    "listing", "delist", "partnership", "acquisition",
    "ETF", "approval", "rejected",
    "bankruptcy", "insolvency",
    "airdrop", "fork", "upgrade",
]

FNG_API = "https://api.alternative.me/fng/"


class NewsAgent(BaseAgent):
    """Monitors crypto news, analyzes impact, and tracks sentiment."""

    def __init__(self, claude_client: Any, knowledge_base: Any) -> None:
        super().__init__(
            name="NEWS",
            claude_client=claude_client,
            knowledge_base=knowledge_base,
            log_path="/opt/aila/logs/ai_trade/news.log",
        )
        self._cache = TTLCache(default_ttl=60.0)
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create shared aiohttp session."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=10)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def think(self, context: dict[str, Any]) -> dict[str, Any]:
        """Process news context."""
        action = context.get("action", "sentiment")
        if action == "breaking":
            return await self.detect_breaking_news()
        if action == "pair_sentiment":
            return await self.get_pair_sentiment(context.get("pair", "BTCUSDT"))
        return await self.get_market_sentiment()

    @retry_async(max_attempts=2, base_delay=2.0)
    async def get_latest_news(self, limit: int = 20) -> list[dict[str, Any]]:
        """Get latest crypto news from CryptoPanic or NewsAPI."""
        cached = self._cache.get("latest_news", ttl=120.0)
        if cached is not None:
            return cached

        news = await self._fetch_cryptopanic(limit)
        if not news:
            news = await self._fetch_newsapi(limit)

        self._cache.set("latest_news", news)
        self.log(f"Fetched {len(news)} news items")
        return news

    async def analyze_news_impact(self, news: dict[str, Any]) -> dict[str, Any]:
        """Evaluate the market impact of a news item."""
        prompt = f"""Evaluate this crypto news impact:
Title: {news.get('title', '')}
Source: {news.get('source', '')}
Currencies: {news.get('currencies', [])}

Respond in JSON:
{{"sentiment": "bullish"|"bearish"|"neutral", "impact_score": 1-10,
"affected_pairs": ["BTCUSDT"], "reaction_time": "immediate"|"hours"|"days",
"recommended_action": "buy"|"sell"|"wait"|"close_positions"}}"""
        return await self.claude_client.analyze(prompt)

    async def get_market_sentiment(self) -> dict[str, Any]:
        """Overall market sentiment from Fear & Greed + AI."""
        cached = self._cache.get("market_sentiment", ttl=300.0)
        if cached is not None:
            return cached

        fng = await self._get_fear_greed_index()
        prompt = f"""Evaluate crypto market sentiment:
Fear & Greed: {fng.get('value')}/100 ({fng.get('classification')})
Yesterday: {fng.get('yesterday')}, Last week: {fng.get('last_week')}

Respond in JSON:
{{"overall_sentiment": "extreme_fear"|"fear"|"neutral"|"greed"|"extreme_greed",
"sentiment_score": 0-100, "risk_level": "low"|"medium"|"high"|"extreme"}}"""

        result = await self.claude_client.analyze(prompt)
        if "error" not in result:
            self._cache.set("market_sentiment", result)
            self.log(f"Market sentiment: {result.get('overall_sentiment')}")
        return result

    async def detect_breaking_news(self) -> dict[str, Any]:
        """Detect breaking news requiring immediate reaction."""
        news = await self.get_latest_news(limit=10)

        for item in news:
            title_lower = item.get("title", "").lower()
            content_lower = item.get("content", "").lower()

            for keyword in URGENT_KEYWORDS:
                if keyword.lower() in title_lower or keyword.lower() in content_lower:
                    impact = await self.analyze_news_impact(item)
                    if impact.get("impact_score", 0) >= 7:
                        self.log(f"BREAKING: {item.get('title', '')[:80]}", "warning")
                        return {
                            "is_breaking": True,
                            "news": item,
                            "impact": impact,
                        }
                    break
        return {"is_breaking": False}

    async def get_pair_sentiment(self, pair: str) -> dict[str, Any]:
        """Sentiment analysis for a specific pair."""
        cache_key = f"pair_sentiment:{pair}"
        cached = self._cache.get(cache_key, ttl=120.0)
        if cached is not None:
            return cached

        base_asset = pair.replace("USDT", "").replace("/USDT", "").replace(":USDT", "")
        news = await self.get_latest_news(limit=10)
        pair_news = [
            n for n in news
            if base_asset in str(n.get("currencies", [])) or base_asset.lower() in n.get("title", "").lower()
        ]

        news_context = "\n".join(f"- {n.get('title', '')}" for n in pair_news[:3]) if pair_news else "No recent news"

        prompt = f"""Sentiment for {base_asset}:
News: {news_context}

Respond in JSON:
{{"pair": "{pair}", "sentiment": "very_bearish"|"bearish"|"neutral"|"bullish"|"very_bullish",
"sentiment_score": -100 to +100, "news_impact": "positive"|"negative"|"neutral"|"no_news"}}"""

        result = await self.claude_client.analyze(prompt)
        if "error" not in result:
            self._cache.set(cache_key, result)
            self.log(f"{pair} sentiment: {result.get('sentiment')}")
        return result

    async def _fetch_cryptopanic(self, limit: int) -> list[dict[str, Any]]:
        """Fetch news from CryptoPanic API."""
        api_key = API_KEYS.get("cryptopanic", "")
        if not api_key:
            return []
        try:
            session = await self._get_session()
            async with session.get(
                "https://cryptopanic.com/api/v1/posts/",
                params={"auth_token": api_key, "kind": "news", "filter": "important", "limit": limit},
            ) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
            return [
                {
                    "title": p.get("title", ""),
                    "content": p.get("title", ""),
                    "source": p.get("source", {}).get("title", "unknown"),
                    "timestamp": p.get("published_at", ""),
                    "currencies": [c.get("code") for c in p.get("currencies", [])],
                }
                for p in data.get("results", [])
            ]
        except aiohttp.ClientError as e:
            self.log(f"CryptoPanic API error: {e}", "error")
            return []

    async def _fetch_newsapi(self, limit: int) -> list[dict[str, Any]]:
        """Fetch news from NewsAPI."""
        api_key = API_KEYS.get("newsapi", "")
        if not api_key:
            return []
        try:
            session = await self._get_session()
            async with session.get(
                "https://newsapi.org/v2/everything",
                params={
                    "apiKey": api_key, "q": "cryptocurrency OR bitcoin",
                    "language": "en", "sortBy": "publishedAt", "pageSize": limit,
                },
            ) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
            return [
                {
                    "title": a.get("title", ""),
                    "content": a.get("description", ""),
                    "source": a.get("source", {}).get("name", "unknown"),
                    "timestamp": a.get("publishedAt", ""),
                    "currencies": [],
                }
                for a in data.get("articles", [])
            ]
        except aiohttp.ClientError as e:
            self.log(f"NewsAPI error: {e}", "error")
            return []

    async def _get_fear_greed_index(self) -> dict[str, Any]:
        """Fetch Fear & Greed Index."""
        cached = self._cache.get("fng", ttl=600.0)
        if cached is not None:
            return cached
        try:
            session = await self._get_session()
            async with session.get(FNG_API, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    entries = data.get("data", [])
                    if entries:
                        result = {
                            "value": int(entries[0].get("value", 50)),
                            "classification": entries[0].get("value_classification", "Neutral"),
                            "yesterday": int(entries[1].get("value", 50)) if len(entries) > 1 else 50,
                            "last_week": int(entries[6].get("value", 50)) if len(entries) > 6 else 50,
                        }
                        self._cache.set("fng", result)
                        return result
        except Exception as e:
            self.log(f"Fear & Greed API error: {e}", "error")
        return {"value": 50, "classification": "Neutral", "yesterday": 50, "last_week": 50}

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
