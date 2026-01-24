import json
from typing import Optional
from .base_agent import BaseAgent


class TraderAgent(BaseAgent):
    """
    Main trader agent - scans market and finds opportunities.
    Does NOT execute trades — sends proposals for review.
    """

    def __init__(self, claude_client, knowledge_base, market_scanner):
        super().__init__(
            name="TRADER",
            claude_client=claude_client,
            knowledge_base=knowledge_base,
            log_path="/opt/aila/logs/ai_trade/trader.log"
        )
        self.scanner = market_scanner
        self.min_confidence = 70

    async def think(self, context: dict) -> dict:
        """Scan market and find trading opportunities."""
        return await self.find_opportunity()

    async def find_opportunity(self) -> Optional[dict]:
        """
        Scan top pairs and find best trading opportunity.
        Returns opportunity dict or None.
        """
        self.log("Starting market scan...")

        # Get top pairs by volume/volatility
        pairs = await self.scanner.get_top_pairs()
        if not pairs:
            self.log("No pairs found matching criteria")
            return None

        self.log(f"Scanning {len(pairs)} pairs for opportunities")

        best_opportunity = None
        best_confidence = 0

        for pair_info in pairs[:10]:  # Analyze top 10
            symbol = pair_info["symbol"]

            # Get detailed market data
            market_data = await self.scanner.get_market_data(symbol)
            if not market_data:
                continue

            # Get knowledge context for this pair
            knowledge = self.knowledge_base.get_context_for_analysis(symbol)

            # Ask Claude for analysis
            analysis = await self.claude_client.get_market_analysis(
                symbol, market_data, knowledge
            )

            if "error" in analysis:
                self.log(f"Analysis error for {symbol}: {analysis['error']}", "error")
                continue

            decision = analysis.get("decision", "WAIT")
            confidence = analysis.get("confidence", 0)

            self.log(f"{symbol}: {decision} (confidence={confidence}%)")

            if decision != "WAIT" and confidence >= self.min_confidence:
                if confidence > best_confidence:
                    best_confidence = confidence
                    best_opportunity = {
                        "pair": symbol,
                        "decision": decision,
                        "confidence": confidence,
                        "strategy": analysis.get("strategy", "ai_analysis"),
                        "entry_price": analysis.get("entry_price"),
                        "stop_loss": analysis.get("stop_loss"),
                        "take_profit": analysis.get("take_profit"),
                        "leverage": analysis.get("leverage", 1),
                        "position_size_pct": analysis.get("position_size_pct", 2),
                        "reasoning": analysis.get("reasoning", ""),
                        "risks": analysis.get("risks", []),
                        "expected_duration": analysis.get("expected_duration", "1h"),
                        "market_data": market_data,
                    }

        if best_opportunity:
            self.log(
                f"Best opportunity: {best_opportunity['decision']} "
                f"{best_opportunity['pair']} @ confidence={best_confidence}%"
            )
            return best_opportunity

        self.log("No opportunities found above confidence threshold")
        return None

    async def evaluate_exit(self, position: dict, market_data: dict) -> dict:
        """Evaluate whether to exit an existing position."""
        prompt = f"""You are monitoring an open position. Decide if it should be closed.

## POSITION
Pair: {position['symbol']}
Direction: {position['direction']}
Entry: {position['entry_price']}
Current: {market_data.get('price')}
Unrealized PnL: {position.get('unrealized_pnl_pct', 0):.2f}%
Duration: {position.get('duration', 'unknown')}

## CURRENT MARKET
RSI: {market_data.get('rsi')}
Trend: {market_data.get('trend')}
ATR: {market_data.get('atr')}

## TASK
Should this position be closed? Respond in JSON:

{{
    "action": "HOLD" | "CLOSE",
    "reason": "explanation",
    "urgency": "low" | "medium" | "high"
}}
"""
        result = await self.claude_client.analyze(prompt)
        return result
