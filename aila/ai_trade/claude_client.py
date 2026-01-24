import anthropic
import json
import logging
from .config import ANTHROPIC_API_KEY, CLAUDE_MODEL

logger = logging.getLogger("ai_trade")


class ClaudeClient:
    """Client for Claude API interactions."""

    def __init__(self):
        self.client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        self.model = CLAUDE_MODEL

    async def analyze(self, prompt: str, max_tokens: int = 4096) -> dict:
        """Send prompt to Claude and return JSON response."""
        try:
            message = self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )

            response_text = message.content[0].text

            # Try to parse JSON from response
            try:
                start = response_text.find("{")
                end = response_text.rfind("}") + 1
                if start != -1 and end > start:
                    json_str = response_text[start:end]
                    return json.loads(json_str)
            except json.JSONDecodeError:
                pass

            return {"raw_response": response_text}

        except Exception as e:
            logger.error(f"Claude API error: {e}")
            return {"error": str(e)}

    async def get_market_analysis(self, pair: str, market_data: dict, knowledge: dict) -> dict:
        """Analyze market and make entry decision."""
        prompt = f"""You are an expert cryptocurrency trader with 20 years of experience. Analyze the market and make decisions.

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

## MY EXPERIENCE WITH THIS PAIR
{json.dumps(knowledge.get('pair_performance', {}).get(pair, {}), indent=2)}

## RECENT SUCCESSFUL TRADES
{json.dumps(knowledge.get('successful_setups', [])[-5:], indent=2)}

## MISTAKES TO AVOID
{json.dumps(knowledge.get('mistakes_to_avoid', [])[-5:], indent=2)}

## TASK

Analyze the situation and respond STRICTLY in JSON format:

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

If unsure - choose WAIT. Better to miss a trade than lose money.
"""
        return await self.analyze(prompt)

    async def analyze_trade_result(self, trade: dict) -> dict:
        """Analyze completed trade for learning."""
        prompt = f"""Analyze the completed trade and extract lessons.

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
    "add_to_mistakes_to_avoid": "if there was an error, what to add to the list"
}}
"""
        return await self.analyze(prompt)
