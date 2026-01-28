"""Market scanner for AI Trade module."""

import logging
from datetime import datetime
from typing import Any, Optional

from ..utils.common import TTLCache
from .config import SCANNER_CONFIG

logger = logging.getLogger("ai_trade")


class MarketScanner:
    """Scans market for trading opportunities with caching."""

    __slots__ = (
        "_exchange", "_config", "_cache", "_last_scan",
        "_instruments_cache", "_instruments_updated", "_known_symbols"
    )

    def __init__(self, exchange: Any) -> None:
        self._exchange = exchange
        self._config = SCANNER_CONFIG
        self._cache = TTLCache(default_ttl=30.0)
        self._last_scan: Optional[datetime] = None
        self._instruments_cache: list[str] = []
        self._instruments_updated: Optional[datetime] = None
        self._known_symbols: set[str] = set()

    async def _refresh_instruments(self) -> list[str]:
        """
        Refresh list of USDT perpetual instruments from exchange.
        Caches for pairs_cache_ttl seconds (default 1 hour).
        Logs new and delisted pairs.
        """
        ttl = self._config.get("pairs_cache_ttl", 3600)

        # Check if cache is still valid
        if (
            self._instruments_cache
            and self._instruments_updated
            and (datetime.now() - self._instruments_updated).total_seconds() < ttl
        ):
            return self._instruments_cache

        try:
            # Fetch fresh instruments list
            symbols = await self._exchange.get_usdt_perpetual_symbols()
            new_set = set(symbols)

            # Log changes on update (not first load)
            if self._known_symbols:
                added = new_set - self._known_symbols
                removed = self._known_symbols - new_set
                if added or removed:
                    logger.info(
                        f"Pairs updated: +{len(added)} new, -{len(removed)} delisted"
                    )
                    if added:
                        logger.debug(f"New pairs: {sorted(added)}")
                    if removed:
                        logger.debug(f"Delisted pairs: {sorted(removed)}")
            else:
                logger.info(f"Loaded {len(symbols)} USDT perpetual pairs from Bybit")

            self._instruments_cache = symbols
            self._instruments_updated = datetime.now()
            self._known_symbols = new_set
            return symbols

        except Exception as e:
            logger.error(f"Error fetching instruments: {e}")
            return self._instruments_cache or []

    async def get_top_pairs(self, limit: int = None) -> list[dict[str, Any]]:
        """Get top trading pairs by volume and volatility."""
        scan_all = self._config.get("scan_all_pairs", False)

        if limit is None:
            limit = None if scan_all else self._config["top_pairs_count"]

        cached = self._cache.get("top_pairs", ttl=30.0)
        if cached is not None:
            return cached if limit is None else cached[:limit]

        try:
            # Get allowed symbols if scanning all pairs
            allowed_symbols: Optional[set[str]] = None
            if scan_all:
                instruments = await self._refresh_instruments()
                allowed_symbols = set(instruments)

            tickers = await self._exchange.fetch_tickers()
            pairs = self._filter_pairs(tickers, allowed_symbols)
            pairs.sort(key=lambda x: x["volume_24h"], reverse=True)

            # No limit if scanning all pairs
            if limit is None:
                result = pairs
            else:
                result = pairs[:max(limit, self._config["top_pairs_count"])]

            self._cache.set("top_pairs", result)
            self._last_scan = datetime.now()
            logger.info(f"Market scan: {len(result)} pairs found")
            return result if limit is None else result[:limit]

        except Exception as e:
            logger.error(f"Market scan error: {e}")
            cached_result = self._cache.get("top_pairs") or []
            if limit is None:
                return cached_result
            return cached_result[:limit] if cached_result else []

    async def get_market_overview(self) -> dict[str, Any]:
        """Get overall market health overview."""
        try:
            pairs = await self.get_top_pairs(limit=50)
            if not pairs:
                return {"status": "no_data", "health": "unknown"}

            bullish = 0
            bearish = 0
            total_volume = 0

            for pair in pairs:
                change = pair.get("change_24h", 0)
                if change > 0:
                    bullish += 1
                elif change < 0:
                    bearish += 1
                total_volume += pair.get("volume_24h", 0)

            total = len(pairs)
            bullish_pct = (bullish / total * 100) if total > 0 else 50

            if bullish_pct > 65:
                health = "bullish"
            elif bullish_pct < 35:
                health = "bearish"
            else:
                health = "neutral"

            return {
                "status": "ok",
                "health": health,
                "bullish_count": bullish,
                "bearish_count": bearish,
                "neutral_count": total - bullish - bearish,
                "bullish_pct": round(bullish_pct, 1),
                "total_volume_24h": total_volume,
                "pairs_analyzed": total,
                "timestamp": datetime.now().isoformat(),
            }

        except Exception as e:
            logger.error(f"Market overview error: {e}")
            return {"status": "error", "health": "unknown", "error": str(e)}

    async def get_market_data(
        self, symbol: str, timeframe: str = "15m", limit: int = 250
    ) -> dict[str, Any]:
        """Get detailed market data with indicators for a symbol."""
        cache_key = f"market:{symbol}:{timeframe}"
        cached = self._cache.get(cache_key, ttl=5.0)
        if cached is not None:
            return cached

        try:
            ohlcv = await self._exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            if not ohlcv or len(ohlcv) < 20:
                logger.warning(f"Insufficient data for {symbol}: got {len(ohlcv) if ohlcv else 0} candles")
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
            logger.error(f"Error getting market data for {symbol}: {type(e).__name__}: {e}")
            return {}

    def _filter_pairs(
        self, tickers: dict[str, Any], allowed_symbols: Optional[set[str]] = None
    ) -> list[dict[str, Any]]:
        """Filter pairs by volume and volatility criteria."""
        pairs = []
        for symbol, ticker in tickers.items():
            if "/USDT" not in symbol:
                continue

            # Filter by allowed symbols if provided
            if allowed_symbols and symbol not in allowed_symbols:
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

    async def get_pairs_by_priority(
        self,
        min_change: float = 3.0,
        max_change: float = 50.0,
        p1_max: int = 10,
        p2_max: int = 10,
        p3_max: int = 10,
    ) -> dict[str, list[dict[str, Any]]]:
        """
        Get pairs categorized by priority based on 24h change.

        VIP: BTC, ETH, SOL (always included regardless of change)
        Priority 1: +5% to +20% (start of trend) - best opportunities
        Priority 2: +20% to +35% (middle of trend) - moderate risk
        Priority 3: +35% to +50% (late trend) - higher risk

        Excludes:
        - Pairs with <min_change% (flat/no momentum)
        - Pairs with >max_change% (too risky, except VIP)
        """
        VIP_PAIRS = {"BTC/USDT", "ETH/USDT", "SOL/USDT"}

        cached = self._cache.get("pairs_by_priority", ttl=30.0)
        if cached is not None:
            return cached

        try:
            tickers = await self._exchange.fetch_tickers()

            vip = []
            priority_1 = []  # 5-20%
            priority_2 = []  # 20-35%
            priority_3 = []  # 35-50%

            for symbol, ticker in tickers.items():
                if "/USDT" not in symbol:
                    continue

                volume_24h = ticker.get("quoteVolume", 0) or 0
                if volume_24h < self._config.get("min_volume_24h", 1_000_000):
                    continue

                change_24h = ticker.get("percentage", 0) or 0
                abs_change = abs(change_24h)

                pair_data = {
                    "symbol": symbol,
                    "price": ticker.get("last", 0),
                    "volume_24h": volume_24h,
                    "change_24h": change_24h,
                    "high_24h": ticker.get("high", 0),
                    "low_24h": ticker.get("low", 0),
                }

                # VIP pairs always included
                if symbol in VIP_PAIRS:
                    vip.append(pair_data)
                    continue

                # Filter by change range
                if abs_change < min_change:
                    continue  # Too flat
                if abs_change > max_change:
                    continue  # Too risky

                # Categorize by priority (use absolute change for direction-agnostic)
                if 5.0 <= abs_change < 20.0:
                    priority_1.append(pair_data)
                elif 20.0 <= abs_change < 35.0:
                    priority_2.append(pair_data)
                elif 35.0 <= abs_change <= max_change:
                    priority_3.append(pair_data)

            # Sort each priority by volume (higher volume = more liquid)
            priority_1.sort(key=lambda x: x["volume_24h"], reverse=True)
            priority_2.sort(key=lambda x: x["volume_24h"], reverse=True)
            priority_3.sort(key=lambda x: x["volume_24h"], reverse=True)

            result = {
                "vip": vip,
                "priority_1": priority_1[:p1_max],
                "priority_2": priority_2[:p2_max],
                "priority_3": priority_3[:p3_max],
                "stats": {
                    "vip_count": len(vip),
                    "p1_total": len(priority_1),
                    "p1_selected": min(len(priority_1), p1_max),
                    "p2_total": len(priority_2),
                    "p2_selected": min(len(priority_2), p2_max),
                    "p3_total": len(priority_3),
                    "p3_selected": min(len(priority_3), p3_max),
                },
            }

            self._cache.set("pairs_by_priority", result)
            logger.info(
                f"[CASCADE] Pairs by priority: VIP={len(vip)}, "
                f"P1={result['stats']['p1_selected']}/{result['stats']['p1_total']}, "
                f"P2={result['stats']['p2_selected']}/{result['stats']['p2_total']}, "
                f"P3={result['stats']['p3_selected']}/{result['stats']['p3_total']}"
            )
            return result

        except Exception as e:
            logger.error(f"[CASCADE] Error getting pairs by priority: {e}")
            return {"vip": [], "priority_1": [], "priority_2": [], "priority_3": [], "stats": {}}
