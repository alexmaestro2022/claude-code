"""Claude API client for AI Trade module."""

import json
import logging
from typing import Any, Optional

import anthropic

from ..utils.common import retry_async
from .config import ANTHROPIC_API_KEY, CLAUDE_MODEL

logger = logging.getLogger("ai_trade")


class ClaudeClient:
    """Async client for Claude API interactions with retry logic."""

    __slots__ = ("_client", "_model")

    def __init__(self) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
        self._model = CLAUDE_MODEL

    @retry_async(max_attempts=3, base_delay=2.0, exceptions=(anthropic.APIError,))
    async def analyze(self, prompt: str, max_tokens: int = 4096) -> dict[str, Any]:
        """Send prompt to Claude and return parsed JSON response."""
        try:
            message = await self._client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            response_text = message.content[0].text
            return self._extract_json(response_text)
        except anthropic.APIError:
            raise
        except Exception as e:
            logger.error(f"Claude API error: {e}")
            return {"error": str(e)}

    async def get_market_analysis(
        self, pair: str, market_data: dict[str, Any], knowledge: dict[str, Any]
    ) -> dict[str, Any]:
        """Analyze market and make entry decision."""
        prompt = self._build_market_prompt(pair, market_data, knowledge)
        return await self.analyze(prompt)

    async def analyze_trade_result(self, trade: dict[str, Any]) -> dict[str, Any]:
        """Analyze completed trade for learning."""
        prompt = self._build_trade_analysis_prompt(trade)
        return await self.analyze(prompt)

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any]:
        """Extract JSON from response text."""
        start = text.find("{")
        end = text.rfind("}") + 1
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                pass
        return {"raw_response": text}

    @staticmethod
    def _build_market_prompt(
        pair: str, market_data: dict[str, Any], knowledge: dict[str, Any]
    ) -> str:
        """Build market analysis prompt."""
        pair_perf = knowledge.get("pair_performance", {}).get(pair, {})
        setups = knowledge.get("successful_setups", [])[-5:]
        mistakes = knowledge.get("mistakes_to_avoid", [])[-5:]

        return f"""You are an expert cryptocurrency trader. Analyze the market.

## CURRENT SITUATION
Pair: {pair}
Price: {market_data.get('price')}
24h Change: {market_data.get('change_24h')}%
24h Volume: ${market_data.get('volume_24h', 0):,.0f}
Trend: {market_data.get('trend')}

Indicators:
- RSI(14): {market_data.get('rsi')}
- EMA50: {market_data.get('ema50')}
- EMA200: {market_data.get('ema200')}
- ATR: {market_data.get('atr')}

## PAIR HISTORY
{json.dumps(pair_perf, indent=2)}

## RECENT SUCCESSFUL TRADES
{json.dumps(setups, indent=2)}

## MISTAKES TO AVOID
{json.dumps(mistakes, indent=2)}

## TASK
Respond STRICTLY in JSON:
{{
    "decision": "LONG" | "SHORT" | "WAIT",
    "confidence": 0-100,
    "strategy": "strategy name",
    "entry_price": number or null,
    "stop_loss": number or null,
    "take_profit": number or null,
    "leverage": 1-20,
    "position_size_pct": 1-10,
    "reasoning": "detailed reasoning",
    "risks": ["risk1", "risk2"],
    "expected_duration": "5m" | "1h" | "4h" | "1d"
}}

If unsure - choose WAIT. Better to miss a trade than lose money."""

    @staticmethod
    def _build_trade_analysis_prompt(trade: dict[str, Any]) -> str:
        """Build trade analysis prompt."""
        return f"""Analyze the completed trade and extract lessons.

## TRADE
{json.dumps(trade, indent=2)}

## TASK
Respond STRICTLY in JSON:
{{
    "grade": "A" | "B" | "C" | "D" | "F",
    "what_went_right": ["point1", "point2"],
    "what_went_wrong": ["point1", "point2"],
    "lesson_learned": "main lesson",
    "improvement_suggestion": "how to improve",
    "add_to_mistakes_to_avoid": "if there was an error, what to add"
}}"""
