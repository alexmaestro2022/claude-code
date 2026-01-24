"""Market scanner for AI Trade module."""

import logging
from datetime import datetime
from typing import Any, Optional

from ..utils.common import TTLCache
from .config import SCANNER_CONFIG

logger = logging.getLogger("ai_trade")


class MarketScanner:
    """Scans market for trading opportunities with caching."""

    __slots__ = ("_exchange", "_config", "_cache", "_last_scan")

    def __init__(self, exchange: Any) -> None:
        self._exchange = exchange
        self._config = SCANNER_CONFIG
        self._cache = TTLCache(default_ttl=30.0)
        self._last_scan: Optional[datetime] = None

    async def get_top_pairs(self) -> list[dict[str, Any]]:
        """Get top trading pairs by volume and volatility."""
        cached = self._cache.get("top_pairs", ttl=30.0)
        if cached is not None:
            return cached

        try:
            tickers = await self._exchange.fetch_tickers()
            pairs = self._filter_pairs(tickers)
            pairs.sort(key=lambda x: x["volume_24h"], reverse=True)
            result = pairs[:self._config["top_pairs_count"]]

            self._cache.set("top_pairs", result)
            self._last_scan = datetime.now()
            logger.info(f"Market scan: {len(result)} pairs found")
            return result

        except Exception as e:
            logger.error(f"Market scan error: {e}")
            return self._cache.get("top_pairs") or []

    async def get_market_data(
        self, symbol: str, timeframe: str = "15m", limit: int = 100
    ) -> dict[str, Any]:
        """Get detailed market data with indicators for a symbol."""
        cache_key = f"market:{symbol}:{timeframe}"
        cached = self._cache.get(cache_key, ttl=5.0)
        if cached is not None:
            return cached

        try:
            ohlcv = await self._exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            if not ohlcv or len(ohlcv) < 20:
                return {}

            closes = [c[4] for c in ohlcv]
            highs = [c[2] for c in ohlcv]
            lows = [c[3] for c in ohlcv]
            volumes = [c[5] for c in ohlcv]
            current_price = closes[-1]

            rsi = self._calculate_rsi(closes)
            ema50 = self._calculate_ema(closes, 50)
            ema200 = self._calculate_ema(closes, 200)
            atr = self._calculate_atr(highs, lows, closes)

            trend = self._determine_trend(current_price, ema50, ema200)

            result = {
                "price": current_price,
                "rsi": round(rsi, 2) if rsi else None,
                "ema50": round(ema50, 6) if ema50 else None,
                "ema200": round(ema200, 6) if ema200 else None,
                "atr": round(atr, 6) if atr else None,
                "trend": trend,
                "volume_24h": sum(volumes[-24:]) if len(volumes) >= 24 else sum(volumes),
                "change_24h": ((current_price - closes[0]) / closes[0] * 100) if closes[0] else 0,
                "support": min(lows[-20:]),
                "resistance": max(highs[-20:]),
            }
            self._cache.set(cache_key, result)
            return result

        except Exception as e:
            logger.error(f"Error getting market data for {symbol}: {e}")
            return {}

    def _filter_pairs(self, tickers: dict[str, Any]) -> list[dict[str, Any]]:
        """Filter pairs by volume and volatility criteria."""
        pairs = []
        for symbol, ticker in tickers.items():
            if "/USDT" not in symbol:
                continue

            volume_24h = ticker.get("quoteVolume", 0) or 0
            if volume_24h < self._config["min_volume_24h"]:
                continue

            change_pct = abs(ticker.get("percentage", 0) or 0)
            if not (self._config["min_volatility_pct"] <= change_pct <= self._config["max_volatility_pct"]):
                continue

            pairs.append({
                "symbol": symbol,
                "price": ticker.get("last", 0),
                "volume_24h": volume_24h,
                "change_24h": ticker.get("percentage", 0),
                "high_24h": ticker.get("high", 0),
                "low_24h": ticker.get("low", 0),
                "volatility": change_pct,
            })
        return pairs

    @staticmethod
    def _determine_trend(
        price: float, ema50: Optional[float], ema200: Optional[float]
    ) -> str:
        """Determine market trend from EMAs."""
        if ema50 and ema200:
            if price > ema50 > ema200:
                return "BULLISH"
            if price < ema50 < ema200:
                return "BEARISH"
        return "NEUTRAL"

    @staticmethod
    def _calculate_rsi(closes: list[float], period: int = 14) -> Optional[float]:
        """Calculate RSI indicator."""
        if len(closes) < period + 1:
            return None

        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        gains = [max(d, 0) for d in deltas[-period:]]
        losses = [max(-d, 0) for d in deltas[-period:]]

        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period

        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    @staticmethod
    def _calculate_ema(data: list[float], period: int) -> Optional[float]:
        """Calculate EMA."""
        if len(data) < period:
            return None

        multiplier = 2 / (period + 1)
        ema = sum(data[:period]) / period
        for price in data[period:]:
            ema = (price - ema) * multiplier + ema
        return ema

    @staticmethod
    def _calculate_atr(
        highs: list[float], lows: list[float], closes: list[float], period: int = 14
    ) -> Optional[float]:
        """Calculate ATR indicator."""
        if len(closes) < period + 1:
            return None

        true_ranges = []
        for i in range(1, len(closes)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
            true_ranges.append(tr)
        return sum(true_ranges[-period:]) / period
