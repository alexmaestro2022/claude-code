"""REVIEWER - validates trade proposals from TRADER."""

import json
from typing import Any

from .base_agent import BaseAgent


class ReviewerAgent(BaseAgent):
    """Validates trade proposals. Can APPROVE, REJECT, or MODIFY."""

    def __init__(self, claude_client: Any, knowledge_base: Any) -> None:
        super().__init__(
            name="REVIEWER",
            claude_client=claude_client,
            knowledge_base=knowledge_base,
            log_path="/opt/aila/logs/ai_trade/reviewer.log",
        )

    async def think(self, context: dict[str, Any]) -> dict[str, Any]:
        """Review a trade proposal."""
        opportunity = context.get("opportunity")
        if not opportunity:
            return {"decision": "REJECT", "reason": "No opportunity provided"}
        return await self.review(opportunity)

    async def review(self, opportunity: dict[str, Any]) -> dict[str, Any]:
        """Review trade proposal and return decision."""
        self.log(f"Reviewing: {opportunity['decision']} {opportunity['pair']}")

        # Get knowledge about past mistakes
        mistakes = self.knowledge_base.data.get("mistakes_to_avoid", [])
        pair_stats = self.knowledge_base.get_pair_stats(opportunity["pair"])

        prompt = f"""You are a senior trade reviewer. Your job is to find flaws in trade proposals.
Be critical but fair. Only reject if there are clear problems.

## TRADE PROPOSAL
Pair: {opportunity['pair']}
Direction: {opportunity['decision']}
Confidence: {opportunity['confidence']}%
Strategy: {opportunity.get('strategy')}
Entry: {opportunity.get('entry_price')}
Stop Loss: {opportunity.get('stop_loss')}
Take Profit: {opportunity.get('take_profit')}
Leverage: {opportunity.get('leverage')}x
Position Size: {opportunity.get('position_size_pct')}%
Reasoning: {opportunity.get('reasoning')}
Risks identified: {json.dumps(opportunity.get('risks', []))}

## MARKET DATA
Price: {opportunity.get('market_data', {}).get('price')}
Trend: {opportunity.get('market_data', {}).get('trend')}
RSI: {opportunity.get('market_data', {}).get('rsi')}
MACD: {'bullish' if (opportunity.get('market_data', {}).get('macd', {}).get('histogram') or 0) > 0 else 'bearish'}
Bollinger %B: {opportunity.get('market_data', {}).get('bollinger', {}).get('pct_b', 'N/A') if opportunity.get('market_data', {}).get('bollinger') else 'N/A'}
Volume ratio: {opportunity.get('market_data', {}).get('volume_profile', {}).get('ratio', 'N/A') if opportunity.get('market_data', {}).get('volume_profile') else 'N/A'}
StochRSI K: {opportunity.get('market_data', {}).get('stoch_rsi', {}).get('k', 'N/A') if opportunity.get('market_data', {}).get('stoch_rsi') else 'N/A'}
Support: {opportunity.get('market_data', {}).get('support', 'N/A')}
Resistance: {opportunity.get('market_data', {}).get('resistance', 'N/A')}
Funding Rate: {opportunity.get('market_data', {}).get('funding_rate', 0):.4f}% ({opportunity.get('market_data', {}).get('funding_signal', 'NEUTRAL')})
Open Interest: {opportunity.get('market_data', {}).get('open_interest', 0):,.0f}
Liquidation Pressure: {opportunity.get('market_data', {}).get('liquidation_pressure', 'LOW')} (OI change 25m: {opportunity.get('market_data', {}).get('oi_change_pct', 0)}%)
Fear & Greed: {opportunity.get('market_data', {}).get('fear_greed', 50)} ({opportunity.get('market_data', {}).get('fear_greed_label', 'Neutral')})

## HISTORICAL PERFORMANCE ON THIS PAIR
{json.dumps(pair_stats, indent=2)}

## MISTAKES TO AVOID
{json.dumps(mistakes[-5:], indent=2)}

## DIRECTION RULES (CRITICAL!)
- LONG in BEARISH trend → REJECT (counter-trend, unless RSI < 15 + volume spike)
- SHORT in BEARISH trend → APPROVE (this IS the trend direction!)
- SHORT in BULLISH trend → REJECT (counter-trend, unless RSI > 85 + reversal pattern)
- LONG in BULLISH trend → APPROVE (this IS the trend direction!)
- NEUTRAL trend → either direction OK if other criteria met

## OVERSOLD/OVERBOUGHT (context, not veto!)
For SHORT in BEARISH trend:
- RSI 20-35 → OK (trend continuation)
- RSI 10-20 → OK with caution (suggest tighter SL)
- RSI < 10 → REJECT (extreme oversold, squeeze risk)
For LONG in BULLISH trend:
- RSI 65-80 → OK (trend continuation)
- RSI 80-90 → OK with caution
- RSI > 90 → REJECT (extreme overbought)

## FEAR & GREED (context, NOT a veto!)
- F&G is market sentiment indicator, not a trade blocker
- F&G < 20 + BEARISH trend + SHORT = NORMAL, don't reject
- F&G > 80 + BULLISH trend + LONG = NORMAL, don't reject
- Only reject at extremes: F&G < 5 for LONG, F&G > 95 for SHORT

## FUNDING RATE
- Negative funding in bearish trend = NORMAL (market is short-biased)
- Only reject SHORT if funding < -0.15% AND RSI < 10 (extreme conditions)
- Positive funding in bullish trend = NORMAL
- Reject LONG if funding > 0.15%

## R:R RATIO (context-dependent!)
- Trend-following (SHORT in bearish, LONG in bullish): R:R >= 1.3 is OK
- Counter-trend trades: R:R >= 2.0 required
- Never reject solely on R:R if >= 1.3 for trend-following

## OTHER CHECKS
1. Stop loss: min 3% for meme/volatile, min 2% for majors
2. Leverage: max 2x for volatile, max 3x for majors
3. Position size within safe limits?
4. Known mistakes being repeated?
5. Confidence justified by data?
6. High liquidation pressure = wait for cascade to complete

## APPROVAL BIAS
When in doubt for TREND-FOLLOWING trades (SHORT in bearish, LONG in bullish):
- Prefer MODIFY over REJECT
- Prefer APPROVE over MODIFY if only minor issues

## TASK
Respond STRICTLY in JSON:

{{
    "decision": "APPROVE" | "REJECT" | "MODIFY",
    "reason": "detailed explanation",
    "risk_reward_ratio": number,
    "issues_found": ["issue1", "issue2"],
    "modifications": {{
        "leverage": number (REQUIRED if MODIFY),
        "position_size_pct": number (REQUIRED if MODIFY),
        "stop_loss": number (REQUIRED if MODIFY),
        "take_profit": number (REQUIRED if MODIFY)
    }}
}}

CRITICAL: If decision is MODIFY, you MUST provide ALL 4 modification values (not null).
Calculate proper values that meet the thresholds above.
"""
        result = await self.claude_client.analyze(
            prompt, agent="REVIEWER", action="validate", context=f"pair={opportunity['pair']}"
        )

        if "error" in result:
            self.log(f"Review error: {result['error']}", "error")
            return {"decision": "REJECT", "reason": f"Review failed: {result['error']}"}

        decision = result.get("decision", "REJECT")
        reason = result.get("reason", "Unknown")

        self.log(f"Decision: {decision} — {reason}")

        # Apply modifications if MODIFY
        if decision == "MODIFY" and result.get("modifications"):
            modified = opportunity.copy()
            mods = result["modifications"]

            if mods.get("leverage"):
                modified["leverage"] = mods["leverage"]
            if mods.get("position_size_pct"):
                modified["position_size_pct"] = mods["position_size_pct"]
            if mods.get("stop_loss"):
                modified["stop_loss"] = mods["stop_loss"]
            if mods.get("take_profit"):
                modified["take_profit"] = mods["take_profit"]

            result["modified_opportunity"] = modified

        return result
