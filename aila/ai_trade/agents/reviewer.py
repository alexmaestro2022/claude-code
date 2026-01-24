import json
from .base_agent import BaseAgent


class ReviewerAgent(BaseAgent):
    """
    Reviewer agent - validates trade proposals from TRADER.
    Can APPROVE, REJECT, or MODIFY proposals.
    """

    def __init__(self, claude_client, knowledge_base):
        super().__init__(
            name="REVIEWER",
            claude_client=claude_client,
            knowledge_base=knowledge_base,
            log_path="/opt/aila/logs/ai_trade/reviewer.log"
        )

    async def think(self, context: dict) -> dict:
        """Review a trade proposal."""
        opportunity = context.get("opportunity")
        if not opportunity:
            return {"decision": "REJECT", "reason": "No opportunity provided"}
        return await self.review(opportunity)

    async def review(self, opportunity: dict) -> dict:
        """
        Review trade proposal and return decision.
        Returns: {decision: APPROVE|REJECT|MODIFY, reason, modified_opportunity?}
        """
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
RSI: {opportunity.get('market_data', {}).get('rsi')}
Trend: {opportunity.get('market_data', {}).get('trend')}

## HISTORICAL PERFORMANCE ON THIS PAIR
{json.dumps(pair_stats, indent=2)}

## MISTAKES TO AVOID
{json.dumps(mistakes[-5:], indent=2)}

## REVIEW CHECKLIST
1. Is the direction consistent with the trend?
2. Is the risk/reward ratio acceptable (min 1.5:1)?
3. Is the stop loss reasonable (not too tight, not too wide)?
4. Is the leverage appropriate for the volatility?
5. Is the position size within safe limits?
6. Are there any of the known mistakes being repeated?
7. Is the confidence level justified by the data?

## TASK
Respond STRICTLY in JSON:

{{
    "decision": "APPROVE" | "REJECT" | "MODIFY",
    "reason": "detailed explanation",
    "risk_reward_ratio": number,
    "issues_found": ["issue1", "issue2"],
    "modifications": {{
        "leverage": number or null,
        "position_size_pct": number or null,
        "stop_loss": number or null,
        "take_profit": number or null
    }}
}}

If MODIFY — fill in modifications with suggested values.
If APPROVE or REJECT — modifications can be empty.
"""
        result = await self.claude_client.analyze(prompt)

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
