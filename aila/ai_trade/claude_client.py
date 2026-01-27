"""Claude API client for AI Trade module."""

import json
import logging
from datetime import datetime
from typing import Any

import anthropic

from ..utils.common import retry_async
from .config import ANTHROPIC_API_KEY, CLAUDE_MODEL, CLAUDE_MODEL_HAIKU

logger = logging.getLogger("ai_trade")


class APIUsageCounter:
    """Tracks Claude API usage for cost monitoring."""

    __slots__ = ("_calls", "_input_tokens", "_output_tokens", "_start_time")

    def __init__(self) -> None:
        self._calls: dict[str, int] = {"sonnet": 0, "haiku": 0}
        self._input_tokens: dict[str, int] = {"sonnet": 0, "haiku": 0}
        self._output_tokens: dict[str, int] = {"sonnet": 0, "haiku": 0}
        self._start_time = datetime.utcnow()

    def record(self, model: str, input_tokens: int, output_tokens: int) -> None:
        """Record API call usage."""
        key = "haiku" if "haiku" in model.lower() else "sonnet"
        self._calls[key] += 1
        self._input_tokens[key] += input_tokens
        self._output_tokens[key] += output_tokens

    def get_stats(self) -> dict[str, Any]:
        """Get usage statistics and estimated cost."""
        # Pricing per 1M tokens (Jan 2025)
        sonnet_input = 3.0  # $3/1M
        sonnet_output = 15.0  # $15/1M
        haiku_input = 0.25  # $0.25/1M
        haiku_output = 1.25  # $1.25/1M

        sonnet_cost = (
            self._input_tokens["sonnet"] * sonnet_input / 1_000_000
            + self._output_tokens["sonnet"] * sonnet_output / 1_000_000
        )
        haiku_cost = (
            self._input_tokens["haiku"] * haiku_input / 1_000_000
            + self._output_tokens["haiku"] * haiku_output / 1_000_000
        )

        runtime = (datetime.utcnow() - self._start_time).total_seconds()
        return {
            "calls": self._calls.copy(),
            "total_calls": sum(self._calls.values()),
            "input_tokens": self._input_tokens.copy(),
            "output_tokens": self._output_tokens.copy(),
            "estimated_cost_usd": {
                "sonnet": round(sonnet_cost, 4),
                "haiku": round(haiku_cost, 4),
                "total": round(sonnet_cost + haiku_cost, 4),
            },
            "runtime_hours": round(runtime / 3600, 2),
            "cost_per_hour": round((sonnet_cost + haiku_cost) / max(runtime / 3600, 0.01), 4),
        }


# Global usage counter
api_usage = APIUsageCounter()


class ClaudeClient:
    """Async client for Claude API interactions with retry logic."""

    __slots__ = ("_client", "_model", "_model_haiku")

    def __init__(self) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
        self._model = CLAUDE_MODEL
        self._model_haiku = CLAUDE_MODEL_HAIKU

    @retry_async(max_attempts=3, base_delay=2.0, exceptions=(anthropic.APIError,))
    async def analyze(
        self, prompt: str, max_tokens: int = 4096, use_haiku: bool = False
    ) -> dict[str, Any]:
        """Send prompt to Claude and return parsed JSON response."""
        model = self._model_haiku if use_haiku else self._model
        try:
            message = await self._client.messages.create(
                model=model,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            response_text = message.content[0].text

            # Record usage
            api_usage.record(
                model,
                message.usage.input_tokens,
                message.usage.output_tokens,
            )
            logger.debug(
                f"API call: {model} | in={message.usage.input_tokens} out={message.usage.output_tokens}"
            )

            return self._extract_json(response_text)
        except anthropic.APIError:
            raise
        except Exception as e:
            logger.error(f"Claude API error: {e}")
            return {"error": str(e)}

    async def batch_analyze_market(
        self, pairs_data: list[dict[str, Any]], knowledge: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Batch analyze all pairs in a single API call.
        Returns the best opportunity or WAIT decision.
        """
        if not pairs_data:
            return {"decision": "WAIT", "reason": "No pairs to analyze"}

        prompt = self._build_batch_market_prompt(pairs_data, knowledge)
        return await self.analyze(prompt, max_tokens=4096, use_haiku=False)

    async def get_market_analysis(
        self, pair: str, market_data: dict[str, Any], knowledge: dict[str, Any]
    ) -> dict[str, Any]:
        """Analyze market and make entry decision (legacy single-pair method)."""
        prompt = self._build_market_prompt(pair, market_data, knowledge)
        return await self.analyze(prompt)

    async def analyze_trade_result(self, trade: dict[str, Any]) -> dict[str, Any]:
        """Analyze completed trade for learning (uses Haiku - non-critical)."""
        prompt = self._build_trade_analysis_prompt(trade)
        return await self.analyze(prompt, use_haiku=True)

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
    def _build_batch_market_prompt(
        pairs_data: list[dict[str, Any]], knowledge: dict[str, Any]
    ) -> str:
        """Build batch market analysis prompt for all pairs."""
        setups = knowledge.get("successful_setups", [])[-5:]
        mistakes = knowledge.get("mistakes_to_avoid", [])[-5:]

        # Format pairs data compactly
        pairs_summary = []
        for p in pairs_data:
            md = p.get("market_data", {})
            pairs_summary.append({
                "symbol": p["symbol"],
                "price": md.get("price"),
                "change_24h": md.get("change_24h"),
                "volume_24h": md.get("volume_24h"),
                "rsi": md.get("rsi"),
                "trend": md.get("trend"),
                "ema50": md.get("ema50"),
                "ema200": md.get("ema200"),
                "atr": md.get("atr"),
            })

        return f"""You are an expert cryptocurrency trader. Analyze ALL {len(pairs_data)} pairs and select the SINGLE BEST trading opportunity.

## ALL PAIRS DATA
{json.dumps(pairs_summary, indent=1)}

## RECENT SUCCESSFUL TRADES
{json.dumps(setups, indent=2)}

## MISTAKES TO AVOID
{json.dumps(mistakes, indent=2)}

## RISK MANAGEMENT RULES (MANDATORY)
1. Risk/Reward ratio MUST be >= 1.5:1
2. Stop loss: min 3% for volatile coins, 2% for stable (BTC, ETH)
3. Leverage: max 2x for meme/volatile, max 3x for majors
4. Position size: 2-4% of capital
5. NEVER go LONG in BEARISH trend, NEVER go SHORT in BULLISH trend

## ANALYSIS CRITERIA
- Look for strong trends with RSI confirmation
- Prefer pairs with high volume (>$10M daily)
- Check for trend alignment (price vs EMA50 vs EMA200)
- Consider volatility (ATR) for stop loss calculation

## TASK
Analyze all pairs and respond STRICTLY in JSON:
{{
    "decision": "LONG" | "SHORT" | "WAIT",
    "pair": "SYMBOL/USDT or null if WAIT",
    "confidence": 0-100,
    "strategy": "strategy name",
    "entry_price": number or null,
    "stop_loss": number or null,
    "take_profit": number or null,
    "leverage": 1-3,
    "position_size_pct": 2-4,
    "reasoning": "why this pair is the best choice",
    "risks": ["risk1", "risk2"],
    "expected_duration": "5m" | "1h" | "4h" | "1d",
    "pairs_analyzed": {len(pairs_data)},
    "runner_up": "second best pair or null"
}}

CRITICAL:
- If NO pair has a good setup, choose "WAIT"
- Better to miss a trade than lose money
- Only choose LONG/SHORT if confidence >= 70%"""

    @staticmethod
    def _build_market_prompt(
        pair: str, market_data: dict[str, Any], knowledge: dict[str, Any]
    ) -> str:
        """Build market analysis prompt (legacy single-pair)."""
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

## RISK MANAGEMENT RULES (MANDATORY)
1. Risk/Reward ratio MUST be >= 1.5:1 (take_profit distance / stop_loss distance)
2. Stop loss distance: minimum 3% from entry for volatile coins, 2% for stable
3. Leverage: max 2x for meme/volatile coins, max 3x for major coins (BTC, ETH, BNB)
4. Position size: 2-4% of capital

## TASK
Respond STRICTLY in JSON:
{{
    "decision": "LONG" | "SHORT" | "WAIT",
    "confidence": 0-100,
    "strategy": "strategy name",
    "entry_price": number or null,
    "stop_loss": number or null (min 3% from entry),
    "take_profit": number or null (must give R/R >= 1.5),
    "leverage": 1-3 (2 for volatile, 3 for majors),
    "position_size_pct": 2-4,
    "reasoning": "detailed reasoning",
    "risks": ["risk1", "risk2"],
    "expected_duration": "5m" | "1h" | "4h" | "1d"
}}

CRITICAL: If R/R < 1.5 - choose WAIT. Better to miss a trade than lose money."""

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


def get_api_usage() -> dict[str, Any]:
    """Get current API usage statistics."""
    return api_usage.get_stats()
