import logging
import asyncio
from typing import Optional
from datetime import datetime
from .config import SCANNER_CONFIG, TIMEFRAMES

logger = logging.getLogger("ai_trade")


class MarketScanner:
    """Scans market for trading opportunities."""

    def __init__(self, exchange):
        self.exchange = exchange
        self.config = SCANNER_CONFIG
        self.last_scan = None
        self.cached_pairs = []

    async def get_top_pairs(self) -> list:
        """Get top trading pairs by volume and volatility."""
        try:
            tickers = await self.exchange.fetch_tickers()

            pairs = []
            for symbol, ticker in tickers.items():
                if not symbol.endswith("/USDT"):
                    continue
                if ":USDT" not in symbol and "/USDT" not in symbol:
                    continue

                volume_24h = ticker.get("quoteVolume", 0) or 0
                if volume_24h < self.config["min_volume_24h"]:
                    continue

                change_pct = abs(ticker.get("percentage", 0) or 0)
                if change_pct < self.config["min_volatility_pct"]:
                    continue
                if change_pct > self.config["max_volatility_pct"]:
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

            # Sort by volume descending
            pairs.sort(key=lambda x: x["volume_24h"], reverse=True)
            self.cached_pairs = pairs[:self.config["top_pairs_count"]]
            self.last_scan = datetime.now()

            logger.info(f"Market scan complete: {len(self.cached_pairs)} pairs found")
            return self.cached_pairs

        except Exception as e:
            logger.error(f"Market scan error: {e}")
            return self.cached_pairs

    async def get_market_data(self, symbol: str, timeframe: str = "15m", limit: int = 100) -> dict:
        """Get detailed market data for a symbol."""
        try:
            ohlcv = await self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)

            if not ohlcv or len(ohlcv) < 20:
                return {}

            closes = [c[4] for c in ohlcv]
            highs = [c[2] for c in ohlcv]
            lows = [c[3] for c in ohlcv]
            volumes = [c[5] for c in ohlcv]

            current_price = closes[-1]

            # Calculate indicators
            rsi = self._calculate_rsi(closes, 14)
            ema50 = self._calculate_ema(closes, 50)
            ema200 = self._calculate_ema(closes, 200)
            atr = self._calculate_atr(highs, lows, closes, 14)

            # Determine trend
            trend = "NEUTRAL"
            if ema50 and ema200:
                if current_price > ema50 > ema200:
                    trend = "BULLISH"
                elif current_price < ema50 < ema200:
                    trend = "BEARISH"

            return {
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

        except Exception as e:
            logger.error(f"Error getting market data for {symbol}: {e}")
            return {}

    def _calculate_rsi(self, closes: list, period: int = 14) -> Optional[float]:
        """Calculate RSI indicator."""
        if len(closes) < period + 1:
            return None

        deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
        gains = [d if d > 0 else 0 for d in deltas[-period:]]
        losses = [-d if d < 0 else 0 for d in deltas[-period:]]

        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def _calculate_ema(self, data: list, period: int) -> Optional[float]:
        """Calculate EMA."""
        if len(data) < period:
            return None

        multiplier = 2 / (period + 1)
        ema = sum(data[:period]) / period

        for price in data[period:]:
            ema = (price - ema) * multiplier + ema

        return ema

    def _calculate_atr(self, highs: list, lows: list, closes: list, period: int = 14) -> Optional[float]:
        """Calculate ATR indicator."""
        if len(closes) < period + 1:
            return None

        true_ranges = []
        for i in range(1, len(closes)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i-1]),
                abs(lows[i] - closes[i-1])
            )
            true_ranges.append(tr)

        return sum(true_ranges[-period:]) / period
