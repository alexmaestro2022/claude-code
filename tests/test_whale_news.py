"""Tests for Whale Tracker and News agents."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aila.ai_trade.agents.whale_tracker import WhaleTrackerAgent
from aila.ai_trade.agents.news_agent import NewsAgent


@pytest.fixture
def mock_claude():
    """Mock Claude client."""
    client = MagicMock()
    client.analyze = AsyncMock(return_value={
        "whale_signal": "neutral",
        "confidence": 50,
        "whale_activity_level": "low",
    })
    return client


@pytest.fixture
def mock_knowledge_base():
    """Mock knowledge base."""
    kb = MagicMock()
    kb.data = {"total_trades": 0, "win_rate": 0, "total_pnl": 0}
    return kb


@pytest.fixture
def mock_exchange():
    """Mock exchange."""
    exchange = MagicMock()
    exchange.fetch_order_book = AsyncMock(return_value={
        "asks": [[100.0, 1.0], [101.0, 2.0], [102.0, 5.0]],
        "bids": [[99.0, 1.0], [98.0, 3.0], [97.0, 6.0]],
    })
    return exchange


@pytest.fixture
def whale_tracker(mock_claude, mock_knowledge_base, mock_exchange):
    """Create WhaleTrackerAgent instance."""
    return WhaleTrackerAgent(mock_claude, mock_knowledge_base, mock_exchange)


@pytest.fixture
def news_agent(mock_claude, mock_knowledge_base):
    """Create NewsAgent instance."""
    return NewsAgent(mock_claude, mock_knowledge_base)


class TestWhaleTracker:
    """Tests for WhaleTrackerAgent."""

    @pytest.mark.asyncio
    async def test_get_whale_signal(self, whale_tracker):
        """Test getting whale signal for a pair."""
        result = await whale_tracker.get_whale_signal("BTCUSDT")
        assert isinstance(result, dict)
        assert "whale_signal" in result or "error" in result

    @pytest.mark.asyncio
    async def test_whale_signal_caching(self, whale_tracker):
        """Test that whale signal is cached."""
        await whale_tracker.get_whale_signal("BTCUSDT")
        await whale_tracker.get_whale_signal("BTCUSDT")
        # Second call should use cache, so Claude is called only once for the signal
        # (but subcalls may also invoke Claude)
        assert whale_tracker._cache.get("whale_signal:BTCUSDT", ttl=60.0) is not None

    @pytest.mark.asyncio
    async def test_analyze_orderbook(self, whale_tracker):
        """Test orderbook whale analysis."""
        result = await whale_tracker.analyze_orderbook_whales("BTCUSDT")
        assert "imbalance" in result
        assert result["imbalance"] in ("neutral", "buyers", "sellers")

    @pytest.mark.asyncio
    async def test_analyze_orderbook_no_exchange(self, mock_claude, mock_knowledge_base):
        """Test orderbook analysis without exchange returns defaults."""
        tracker = WhaleTrackerAgent(mock_claude, mock_knowledge_base, exchange=None)
        result = await tracker.analyze_orderbook_whales("BTCUSDT")
        assert result["imbalance"] == "neutral"
        assert result["resistance_walls"] == []
        assert result["support_walls"] == []

    @pytest.mark.asyncio
    async def test_exchange_flows(self, whale_tracker):
        """Test exchange flow analysis."""
        result = await whale_tracker.analyze_exchange_flows("BTCUSDT")
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_close_session(self, whale_tracker):
        """Test session cleanup."""
        await whale_tracker._get_session()
        assert whale_tracker._session is not None
        await whale_tracker.close()

    @pytest.mark.asyncio
    async def test_parallel_data_collection(self, whale_tracker):
        """Test that get_whale_signal calls subcollectors in parallel."""
        whale_tracker.analyze_exchange_flows = AsyncMock(return_value={"signal": "neutral"})
        whale_tracker.detect_accumulation = AsyncMock(return_value={"phase": "neutral"})
        whale_tracker.analyze_orderbook_whales = AsyncMock(return_value={"imbalance": "neutral"})

        await whale_tracker.get_whale_signal("ETHUSDT")

        whale_tracker.analyze_exchange_flows.assert_called_once_with("ETHUSDT")
        whale_tracker.detect_accumulation.assert_called_once()
        whale_tracker.analyze_orderbook_whales.assert_called_once_with("ETHUSDT")


class TestNewsAgent:
    """Tests for NewsAgent."""

    @pytest.mark.asyncio
    async def test_get_market_sentiment(self, news_agent):
        """Test getting market sentiment."""
        result = await news_agent.get_market_sentiment()
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_market_sentiment_caching(self, news_agent):
        """Test market sentiment is cached."""
        await news_agent.get_market_sentiment()
        cached = news_agent._cache.get("market_sentiment", ttl=300.0)
        assert cached is not None

    @pytest.mark.asyncio
    async def test_get_pair_sentiment(self, news_agent):
        """Test getting pair-specific sentiment."""
        result = await news_agent.get_pair_sentiment("BTCUSDT")
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_detect_breaking_news_none(self, news_agent):
        """Test breaking news detection when no news available."""
        result = await news_agent.detect_breaking_news()
        assert result["is_breaking"] is False

    @pytest.mark.asyncio
    async def test_detect_breaking_news_found(self, news_agent):
        """Test breaking news detection when urgent news exists."""
        news_agent.get_latest_news = AsyncMock(return_value=[
            {"title": "Major hack: $500M stolen from exchange", "content": ""},
        ])
        news_agent.analyze_news_impact = AsyncMock(return_value={
            "impact_score": 9,
            "sentiment": "bearish",
            "affected_pairs": ["BTCUSDT"],
            "reaction_time": "immediate",
            "recommended_action": "close_positions",
        })

        result = await news_agent.detect_breaking_news()
        assert result["is_breaking"] is True
        assert result["impact"]["impact_score"] == 9

    @pytest.mark.asyncio
    async def test_fear_greed_index_fallback(self, news_agent):
        """Test Fear & Greed Index returns default on failure."""
        news_agent._session = MagicMock()
        news_agent._session.closed = True  # Force new session creation failure

        result = await news_agent._get_fear_greed_index()
        assert result["value"] == 50  # Default neutral

    @pytest.mark.asyncio
    async def test_close_session(self, news_agent):
        """Test session cleanup."""
        await news_agent._get_session()
        assert news_agent._session is not None
        await news_agent.close()

    @pytest.mark.asyncio
    async def test_think_dispatch(self, news_agent):
        """Test think() dispatches to correct method."""
        news_agent.get_market_sentiment = AsyncMock(return_value={"sentiment": "neutral"})
        news_agent.detect_breaking_news = AsyncMock(return_value={"is_breaking": False})
        news_agent.get_pair_sentiment = AsyncMock(return_value={"sentiment": "bullish"})

        await news_agent.think({"action": "sentiment"})
        news_agent.get_market_sentiment.assert_called_once()

        await news_agent.think({"action": "breaking"})
        news_agent.detect_breaking_news.assert_called_once()

        await news_agent.think({"action": "pair_sentiment", "pair": "ETHUSDT"})
        news_agent.get_pair_sentiment.assert_called_once_with("ETHUSDT")
