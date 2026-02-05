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
            macd = self._calculate_macd(closes)
            bb = self._calculate_bollinger(closes)
            vol_profile = self._calculate_volume_profile(volumes)
            sr = self._calculate_support_resistance(highs, lows, closes)
            stoch_rsi = self._calculate_stochastic_rsi(closes)

            trend = self._determine_trend(current_price, ema50, ema200)

            # Get funding rate and open interest
            funding_info = await self._exchange.get_funding_info(symbol)
            oi_info = await self._exchange.get_open_interest(symbol)

            # Determine funding signal
            funding_pct = funding_info.get("funding_rate_pct", 0)
            if funding_pct > 0.05:
                funding_signal = "SHORT_BIAS"  # Market overleveraged long
            elif funding_pct < -0.05:
                funding_signal = "LONG_BIAS"  # Market overleveraged short
            else:
                funding_signal = "NEUTRAL"

            result = {
                "price": current_price,
                "rsi": round(rsi, 2) if rsi else None,
                "ema50": round(ema50, 6) if ema50 else None,
                "ema200": round(ema200, 6) if ema200 else None,
                "atr": round(atr, 6) if atr else None,
                "trend": trend,
                "volume_24h": sum(volumes[-24:]) if len(volumes) >= 24 else sum(volumes),
                "change_24h": ((current_price - closes[0]) / closes[0] * 100) if closes[0] else 0,
                "support": sr["support"] if sr else min(lows[-20:]),
                "resistance": sr["resistance"] if sr else max(highs[-20:]),
                "macd": macd,
                "bollinger": bb,
                "volume_profile": vol_profile,
                "stoch_rsi": stoch_rsi,
                "funding_rate": funding_pct,
                "funding_signal": funding_signal,
                "open_interest": oi_info.get("open_interest", 0),
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

    @staticmethod
    def _calculate_macd(
        closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9
    ) -> Optional[dict[str, float]]:
        """Calculate MACD (12, 26, 9) — line, signal, histogram."""
        if len(closes) < slow + signal:
            return None

        def _ema(data: list[float], period: int) -> list[float]:
            mult = 2 / (period + 1)
            result = [sum(data[:period]) / period]
            for price in data[period:]:
                result.append((price - result[-1]) * mult + result[-1])
            return result

        ema_fast = _ema(closes, fast)
        ema_slow = _ema(closes, slow)

        # Align lengths: ema_fast starts at index fast, ema_slow at index slow
        offset = slow - fast
        macd_line = [
            ema_fast[offset + i] - ema_slow[i]
            for i in range(len(ema_slow))
        ]

        if len(macd_line) < signal:
            return None

        signal_line = _ema(macd_line, signal)
        # Align: signal_line starts signal periods into macd_line
        histogram = macd_line[-1] - signal_line[-1]

        return {
            "macd": round(macd_line[-1], 6),
            "signal": round(signal_line[-1], 6),
            "histogram": round(histogram, 6),
        }

    @staticmethod
    def _calculate_bollinger(
        closes: list[float], period: int = 20, std_dev: float = 2.0
    ) -> Optional[dict[str, float]]:
        """Calculate Bollinger Bands (20, 2) — upper, lower, %B."""
        if len(closes) < period:
            return None

        window = closes[-period:]
        sma = sum(window) / period
        variance = sum((x - sma) ** 2 for x in window) / period
        std = variance ** 0.5

        upper = sma + std_dev * std
        lower = sma - std_dev * std
        current = closes[-1]

        band_width = upper - lower
        pct_b = (current - lower) / band_width if band_width > 0 else 0.5

        return {
            "upper": round(upper, 6),
            "lower": round(lower, 6),
            "middle": round(sma, 6),
            "pct_b": round(pct_b, 4),
            "width_pct": round(band_width / sma * 100, 2) if sma else 0,
        }

    @staticmethod
    def _calculate_volume_profile(
        volumes: list[float], period: int = 20
    ) -> Optional[dict[str, float]]:
        """Calculate volume ratio: current vs average over N candles."""
        if len(volumes) < period + 1:
            return None

        avg_vol = sum(volumes[-(period + 1):-1]) / period
        current_vol = volumes[-1]
        ratio = current_vol / avg_vol if avg_vol > 0 else 1.0

        return {
            "current": round(current_vol, 2),
            "avg_20": round(avg_vol, 2),
            "ratio": round(ratio, 2),
        }

    @staticmethod
    def _calculate_support_resistance(
        highs: list[float], lows: list[float], closes: list[float],
        lookback: int = 50,
    ) -> Optional[dict[str, float]]:
        """Find nearest support/resistance from local min/max over N candles."""
        if len(closes) < lookback:
            return None

        h = highs[-lookback:]
        l = lows[-lookback:]
        price = closes[-1]

        # Find local maxima and minima (swing points)
        resistance_levels = []
        support_levels = []
        for i in range(2, len(h) - 2):
            if h[i] > h[i - 1] and h[i] > h[i - 2] and h[i] > h[i + 1] and h[i] > h[i + 2]:
                resistance_levels.append(h[i])
            if l[i] < l[i - 1] and l[i] < l[i - 2] and l[i] < l[i + 1] and l[i] < l[i + 2]:
                support_levels.append(l[i])

        # Nearest support below price
        supports_below = [s for s in support_levels if s < price]
        support = max(supports_below) if supports_below else min(l)

        # Nearest resistance above price
        resistances_above = [r for r in resistance_levels if r > price]
        resistance = min(resistances_above) if resistances_above else max(h)

        return {
            "support": round(support, 6),
            "resistance": round(resistance, 6),
            "distance_to_support_pct": round((price - support) / price * 100, 2) if support else 0,
            "distance_to_resistance_pct": round((resistance - price) / price * 100, 2) if resistance else 0,
        }

    @staticmethod
    def _calculate_stochastic_rsi(
        closes: list[float],
        rsi_period: int = 14, stoch_period: int = 14,
        k_smooth: int = 3, d_smooth: int = 3,
    ) -> Optional[dict[str, float]]:
        """Calculate Stochastic RSI (14, 14, 3, 3)."""
        needed = rsi_period + stoch_period + d_smooth + 5
        if len(closes) < needed:
            return None

        # Step 1: Calculate RSI series
        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        rsi_values = []
        avg_gain = sum(max(d, 0) for d in deltas[:rsi_period]) / rsi_period
        avg_loss = sum(max(-d, 0) for d in deltas[:rsi_period]) / rsi_period

        for i in range(rsi_period, len(deltas)):
            gain = max(deltas[i], 0)
            loss = max(-deltas[i], 0)
            avg_gain = (avg_gain * (rsi_period - 1) + gain) / rsi_period
            avg_loss = (avg_loss * (rsi_period - 1) + loss) / rsi_period
            rs = avg_gain / avg_loss if avg_loss > 0 else 100
            rsi_values.append(100 - (100 / (1 + rs)))

        if len(rsi_values) < stoch_period:
            return None

        # Step 2: Stochastic of RSI
        stoch_k_raw = []
        for i in range(stoch_period - 1, len(rsi_values)):
            window = rsi_values[i - stoch_period + 1:i + 1]
            low_rsi = min(window)
            high_rsi = max(window)
            diff = high_rsi - low_rsi
            k = ((rsi_values[i] - low_rsi) / diff * 100) if diff > 0 else 50
            stoch_k_raw.append(k)

        if len(stoch_k_raw) < k_smooth:
            return None

        # Step 3: Smooth %K
        stoch_k = [
            sum(stoch_k_raw[i - k_smooth + 1:i + 1]) / k_smooth
            for i in range(k_smooth - 1, len(stoch_k_raw))
        ]

        if len(stoch_k) < d_smooth:
            return None

        # Step 4: %D = SMA of %K
        stoch_d = [
            sum(stoch_k[i - d_smooth + 1:i + 1]) / d_smooth
            for i in range(d_smooth - 1, len(stoch_k))
        ]

        return {
            "k": round(stoch_k[-1], 2),
            "d": round(stoch_d[-1], 2),
        }

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
