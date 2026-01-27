"""PREDICTOR - predicts price movements using technical analysis + AI."""

import asyncio
import json
import logging
from typing import Any, Optional

from ...utils.common import TTLCache, retry_async
from .base_agent import BaseAgent

logger = logging.getLogger("ai_trade")


class PredictorAgent(BaseAgent):
    """Predicts price movements based on technical analysis and AI interpretation."""

    def __init__(
        self, claude_client: Any, knowledge_base: Any, scanner: Any = None
    ) -> None:
        super().__init__(
            name="PREDICTOR",
            claude_client=claude_client,
            knowledge_base=knowledge_base,
            log_path="/opt/aila/logs/ai_trade/predictor.log",
        )
        self.scanner = scanner
        self._cache = TTLCache(default_ttl=180.0)  # Increased from 30s to 3min

    async def think(self, context: dict[str, Any]) -> dict[str, Any]:
        """Process prediction context."""
        pair = context.get("pair")
        if not pair:
            return {"error": "No pair provided"}
        action = context.get("action", "predict")
        if action == "reversal":
            return await self.detect_reversal(pair)
        return await self.predict_movement(pair)

    async def predict_movement(
        self, pair: str, timeframe: str = "15m"
    ) -> dict[str, Any]:
        """Predict price movement for a pair."""
        cache_key = f"predict:{pair}:{timeframe}"
        cached = self._cache.get(cache_key, ttl=180.0)
        if cached is not None:
            return cached

        self.log(f"Predicting movement for {pair} ({timeframe})")

        # Gather data in parallel
        indicators, sr_levels = await asyncio.gather(
            self._get_indicators(pair, timeframe),
            self._find_sr_levels(pair),
            return_exceptions=True,
        )
        if isinstance(indicators, Exception):
            indicators = {}
        if isinstance(sr_levels, Exception):
            sr_levels = {}

        prompt = f"""You are an expert technical analyst with 20 years of experience.

DATA FOR {pair} ({timeframe}):

INDICATORS:
{json.dumps(indicators, indent=2)}

SUPPORT/RESISTANCE:
{json.dumps(sr_levels, indent=2)}

TASK: Predict price movement for the next 1-4 hours.

Respond in JSON only:
{{"direction": "up"|"down"|"sideways", "probability": 0-100,
"target_price": number, "stop_price": number, "expected_move_pct": number,
"timeframe_hours": 1-4, "confidence": 0-100,
"key_levels": {{"strong_support": number, "weak_support": number,
"weak_resistance": number, "strong_resistance": number}},
"signals": ["signal1", "signal2"], "risks": ["risk1", "risk2"],
"reasoning": "explanation"}}"""

        result = await self.claude_client.analyze(
            prompt, agent="PREDICTOR", action="movement", context=f"pair={pair}"
        )
        if "error" not in result:
            self._cache.set(cache_key, result)
            self.log(f"{pair}: {result.get('direction')} ({result.get('probability')}%)")
        return result

    async def detect_reversal(self, pair: str) -> dict[str, Any]:
        """Detect trend reversal probability."""
        cache_key = f"reversal:{pair}"
        cached = self._cache.get(cache_key, ttl=180.0)
        if cached is not None:
            return cached

        indicators = await self._get_indicators(pair, "1h")

        prompt = f"""Determine trend reversal probability for {pair}:

INDICATORS: {json.dumps(indicators, indent=2)}

REVERSAL SIGNS:
- RSI/MACD divergence
- Extreme RSI (<20 or >80)
- Pin bars, hammers, dojis
- Volume declining during move
- Key level tests

Respond in JSON only:
{{"reversal_probability": 0-100, "current_trend": "up"|"down"|"sideways",
"reversal_type": "bullish"|"bearish"|"none",
"signals": ["signal1"], "confirmation_needed": ["what is needed"],
"reasoning": "explanation"}}"""

        result = await self.claude_client.analyze(
            prompt, agent="PREDICTOR", action="reversal", context=f"pair={pair}"
        )
        if "error" not in result:
            self._cache.set(cache_key, result)
            self.log(f"{pair} reversal: {result.get('reversal_type')} "
                     f"({result.get('reversal_probability')}%)")
        return result

    async def find_similar_patterns(self, pair: str) -> dict[str, Any]:
        """Find similar historical patterns."""
        prompt = f"""Find similar historical patterns for {pair}.

Describe:
1. What pattern is forming now
2. When similar occurred in the past
3. How it ended
4. Probability of repetition

Respond in JSON only:
{{"current_pattern": "pattern name",
"similar_cases": [{{"date": "approx date", "outcome": "what happened", "move_pct": number}}],
"average_outcome": number, "win_rate": number, "recommendation": "text"}}"""
        return await self.claude_client.analyze(
            prompt, agent="PREDICTOR", action="patterns", context=f"pair={pair}"
        )

    async def _get_indicators(
        self, pair: str, timeframe: str
    ) -> dict[str, Any]:
        """Get technical indicators from market scanner."""
        if not self.scanner:
            return {}
        try:
            data = await self.scanner.get_market_data(pair, timeframe)
            return {
                "price": data.get("price", 0),
                "rsi": data.get("rsi", 50),
                "ema_20": data.get("ema_20", 0),
                "ema_50": data.get("ema_50", 0),
                "ema_200": data.get("ema_200", 0),
                "atr": data.get("atr", 0),
                "volume_24h": data.get("volume", 0),
                "change_24h": data.get("change_24h", 0),
                "trend": data.get("trend", "neutral"),
            }
        except Exception as e:
            self.log(f"Error getting indicators for {pair}: {e}", "error")
            return {}

    async def _find_sr_levels(self, pair: str) -> dict[str, Any]:
        """Find support/resistance levels from market data."""
        if not self.scanner:
            return {"support_levels": [], "resistance_levels": []}
        try:
            data = await self.scanner.get_market_data(pair, "1h")
            price = data.get("price", 0)
            atr = data.get("atr", 0)
            if not price or not atr:
                return {"support_levels": [], "resistance_levels": []}
            return {
                "support_levels": [price - atr, price - atr * 2],
                "resistance_levels": [price + atr, price + atr * 2],
            }
        except Exception as e:
            self.log(f"Error finding S/R for {pair}: {e}", "error")
            return {"support_levels": [], "resistance_levels": []}
