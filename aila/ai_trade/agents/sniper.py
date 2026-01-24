"""SNIPER - instant entries on key events (breakouts, listings, news)."""

import asyncio
import logging
from datetime import datetime
from typing import Any, Callable, Optional

from ...utils.common import TTLCache
from .base_agent import BaseAgent

logger = logging.getLogger("ai_trade")


class SniperAgent(BaseAgent):
    """Sniper entries on key market events."""

    def __init__(
        self, claude_client: Any, knowledge_base: Any, scanner: Any = None
    ) -> None:
        super().__init__(
            name="SNIPER",
            claude_client=claude_client,
            knowledge_base=knowledge_base,
            log_path="/opt/aila/logs/ai_trade/sniper.log",
        )
        self.scanner = scanner
        self._cache = TTLCache(default_ttl=10.0)
        self._pending_snipes: list[dict[str, Any]] = []
        self._triggers: dict[str, Callable] = {
            "breakout": self._check_breakout,
            "breakdown": self._check_breakdown,
            "liquidation_cascade": self._check_liquidations,
            "funding_flip": self._check_funding_flip,
        }

    async def think(self, context: dict[str, Any]) -> dict[str, Any]:
        """Process sniper context."""
        pairs = context.get("pairs", [])
        if not pairs:
            return {"snipes": []}
        snipes = await self.scan_for_snipes(pairs)
        return {"snipes": snipes}

    async def scan_for_snipes(self, pairs: list[str]) -> list[dict[str, Any]]:
        """Scan pairs for sniper opportunities in parallel."""
        tasks = [self._analyze_pair(pair) for pair in pairs]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        snipes: list[dict[str, Any]] = []
        for pair, result in zip(pairs, results):
            if isinstance(result, Exception):
                self.log(f"Snipe scan failed for {pair}: {result}", "error")
                continue
            if result and result.get("snipe_ready"):
                snipes.append(result)
                self.log(f"Snipe opportunity: {pair} - {result.get('trigger_type')}", "warning")

        return snipes

    async def prepare_snipe(self, snipe: dict[str, Any]) -> dict[str, Any]:
        """Calculate precise entry parameters for a snipe."""
        prompt = f"""Prepare sniper entry:

TRIGGER: {snipe.get('trigger_type')}
PAIR: {snipe.get('pair')}
DIRECTION: {snipe.get('direction')}
ENTRY PRICE: {snipe.get('entry_price')}

Calculate optimal parameters.

Respond in JSON only:
{{"entry_type": "market"|"limit", "entry_price": number,
"stop_loss": number, "take_profit_1": number, "take_profit_2": number,
"position_size_pct": 1-3, "leverage": 5-15,
"max_slippage_pct": number, "time_limit_seconds": 30-300,
"abort_conditions": ["condition1", "condition2"]}}"""

        result = await self.claude_client.analyze(prompt)
        self.log(f"Snipe prepared: {snipe.get('pair')} {snipe.get('direction')}")
        return result

    async def get_pending_snipes(self) -> list[dict[str, Any]]:
        """Return pending snipes."""
        return self._pending_snipes

    async def cancel_snipe(self, pair: str) -> bool:
        """Cancel a pending snipe by pair."""
        before = len(self._pending_snipes)
        self._pending_snipes = [
            s for s in self._pending_snipes if s.get("pair") != pair
        ]
        cancelled = len(self._pending_snipes) < before
        if cancelled:
            self.log(f"Snipe cancelled: {pair}")
        return cancelled

    async def _analyze_pair(self, pair: str) -> dict[str, Any]:
        """Analyze a pair for snipe opportunity."""
        cache_key = f"snipe:{pair}"
        cached = self._cache.get(cache_key, ttl=10.0)
        if cached is not None:
            return cached

        for trigger_name, trigger_func in self._triggers.items():
            try:
                result = await trigger_func(pair)
                if result and result.get("triggered"):
                    snipe = {
                        "pair": pair,
                        "snipe_ready": True,
                        "trigger_type": trigger_name,
                        "direction": result.get("direction", "LONG"),
                        "entry_price": result.get("entry_price"),
                        "stop_loss": result.get("stop_loss"),
                        "take_profit": result.get("take_profit"),
                        "confidence": result.get("confidence", 70),
                        "urgency": result.get("urgency", "medium"),
                        "reasoning": result.get("reasoning", ""),
                        "timestamp": datetime.utcnow().isoformat(),
                    }
                    self._cache.set(cache_key, snipe)
                    return snipe
            except Exception as e:
                self.log(f"Trigger {trigger_name} error for {pair}: {e}", "error")

        return {"snipe_ready": False}

    async def _check_breakout(self, pair: str) -> dict[str, Any]:
        """Check for resistance breakout."""
        if not self.scanner:
            return {"triggered": False}
        try:
            data = await self.scanner.get_market_data(pair, "5m")
            if not data:
                return {"triggered": False}

            price = data.get("price", 0)
            high_24h = data.get("high_24h", 0)
            atr = data.get("atr", 0)

            # Breakout: price within 0.1% of 24h high with momentum
            if high_24h and price > high_24h * 0.999 and atr > 0:
                rsi = data.get("rsi", 50)
                if 55 < rsi < 80:  # Not overbought
                    return {
                        "triggered": True,
                        "direction": "LONG",
                        "entry_price": price,
                        "stop_loss": price - atr * 1.5,
                        "take_profit": price + atr * 3,
                        "confidence": min(85, 50 + int(rsi - 50)),
                        "urgency": "high",
                        "reasoning": f"Breakout: price at 24h high, RSI={rsi}",
                    }
        except Exception as e:
            self.log(f"Breakout check error {pair}: {e}", "error")
        return {"triggered": False}

    async def _check_breakdown(self, pair: str) -> dict[str, Any]:
        """Check for support breakdown."""
        if not self.scanner:
            return {"triggered": False}
        try:
            data = await self.scanner.get_market_data(pair, "5m")
            if not data:
                return {"triggered": False}

            price = data.get("price", 0)
            low_24h = data.get("low_24h", 0)
            atr = data.get("atr", 0)

            # Breakdown: price within 0.1% of 24h low
            if low_24h and price < low_24h * 1.001 and atr > 0:
                rsi = data.get("rsi", 50)
                if 20 < rsi < 45:  # Not oversold
                    return {
                        "triggered": True,
                        "direction": "SHORT",
                        "entry_price": price,
                        "stop_loss": price + atr * 1.5,
                        "take_profit": price - atr * 3,
                        "confidence": min(85, 50 + int(50 - rsi)),
                        "urgency": "high",
                        "reasoning": f"Breakdown: price at 24h low, RSI={rsi}",
                    }
        except Exception as e:
            self.log(f"Breakdown check error {pair}: {e}", "error")
        return {"triggered": False}

    async def _check_liquidations(self, pair: str) -> dict[str, Any]:
        """Check for liquidation cascades (potential bounce after)."""
        if not self.scanner:
            return {"triggered": False}
        try:
            data = await self.scanner.get_market_data(pair, "5m")
            if not data:
                return {"triggered": False}

            change = abs(data.get("change_24h", 0))
            rsi = data.get("rsi", 50)
            atr = data.get("atr", 0)
            price = data.get("price", 0)

            # Sharp move (>5%) + extreme RSI = potential liquidation cascade
            if change > 5 and (rsi < 20 or rsi > 80) and atr > 0:
                direction = "LONG" if rsi < 20 else "SHORT"
                sl_mult = 2.0 if change > 8 else 1.5
                return {
                    "triggered": True,
                    "direction": direction,
                    "entry_price": price,
                    "stop_loss": price + atr * sl_mult * (-1 if direction == "LONG" else 1),
                    "take_profit": price + atr * 2 * (1 if direction == "LONG" else -1),
                    "confidence": 60,
                    "urgency": "high",
                    "reasoning": f"Liquidation cascade: {change:.1f}% move, RSI={rsi}",
                }
        except Exception as e:
            self.log(f"Liquidation check error {pair}: {e}", "error")
        return {"triggered": False}

    async def _check_funding_flip(self, pair: str) -> dict[str, Any]:
        """Check for extreme funding rate flip (contrarian signal)."""
        # Funding rate data requires exchange-specific API
        # Placeholder for future integration
        return {"triggered": False}
