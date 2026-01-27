"""TRADER - scans market and finds opportunities using batch analysis."""

import asyncio
from typing import Any, Optional

from ...utils.common import clamp
from .base_agent import BaseAgent


class TraderAgent(BaseAgent):
    """Main trader agent. Scans market and proposes trades for review."""

    # Minimum Risk:Reward ratio for trades
    MIN_RR_RATIO: float = 1.5
    # Minimum stop loss distance (percentage)
    MIN_SL_DISTANCE_PCT: float = 2.0

    def __init__(
        self, claude_client: Any, knowledge_base: Any,
        market_scanner: Any, orchestrator: Any = None,
    ) -> None:
        super().__init__(
            name="TRADER",
            claude_client=claude_client,
            knowledge_base=knowledge_base,
            log_path="/opt/aila/logs/ai_trade/trader.log",
        )
        self.scanner = market_scanner
        self.orchestrator = orchestrator
        self.min_confidence: int = 70

    async def think(self, context: dict[str, Any]) -> dict[str, Any]:
        """Scan market and find trading opportunities."""
        return await self.find_opportunity()

    async def find_opportunity(self) -> Optional[dict]:
        """
        Scan all filtered pairs and find best trading opportunity.
        Uses batch analysis - single Claude API call for all pairs.
        Returns opportunity dict or None.
        """
        self.log("Starting market scan (batch mode)...")

        # Get all filtered pairs by volume/volatility
        pairs = await self.scanner.get_top_pairs()
        if not pairs:
            self.log("No pairs found matching criteria")
            return None

        self.log(f"Fetching market data for {len(pairs)} pairs...")

        # Fetch market data for all pairs in parallel
        pairs_data = await self._fetch_all_market_data(pairs)

        if not pairs_data:
            self.log("No market data available for any pair")
            return None

        self.log(f"Batch analyzing {len(pairs_data)} pairs in single API call...")

        # Get knowledge context
        knowledge = self.knowledge_base.get_context_for_analysis("")

        # Single Claude API call for all pairs
        analysis = await self.claude_client.batch_analyze_market(pairs_data, knowledge)

        if "error" in analysis:
            self.log(f"Batch analysis error: {analysis['error']}", "error")
            return None

        # Log analysis summary
        pairs_analyzed = analysis.get("pairs_analyzed", len(pairs_data))
        runner_up = analysis.get("runner_up", "none")
        self.log(f"Analyzed {pairs_analyzed} pairs, runner-up: {runner_up}")

        decision = analysis.get("decision", "WAIT")
        confidence = analysis.get("confidence", 0)
        pair = analysis.get("pair")

        if decision == "WAIT" or confidence < self.min_confidence:
            self.log(f"No opportunity: decision={decision}, confidence={confidence}%")
            return None

        # Get market data for the selected pair
        market_data = next(
            (p["market_data"] for p in pairs_data if p["symbol"] == pair),
            {}
        )

        # Validate trend alignment
        trend = market_data.get("trend", "NEUTRAL")
        if decision == "LONG" and trend == "BEARISH":
            self.log(f"{pair}: LONG vs BEARISH trend - skipping")
            return None
        if decision == "SHORT" and trend == "BULLISH":
            self.log(f"{pair}: SHORT vs BULLISH trend - skipping")
            return None

        # Build opportunity
        best_opportunity = {
            "pair": pair,
            "decision": decision,
            "confidence": confidence,
            "strategy": analysis.get("strategy", "batch_analysis"),
            "entry_price": analysis.get("entry_price"),
            "stop_loss": analysis.get("stop_loss"),
            "take_profit": analysis.get("take_profit"),
            "leverage": analysis.get("leverage", 2),
            "position_size_pct": analysis.get("position_size_pct", 2),
            "reasoning": analysis.get("reasoning", ""),
            "risks": analysis.get("risks", []),
            "expected_duration": analysis.get("expected_duration", "1h"),
            "market_data": market_data,
            "pairs_analyzed": pairs_analyzed,
        }

        # Validate and adjust R:R ratio
        best_opportunity = self._validate_and_adjust_rr(
            best_opportunity, market_data, pair
        )
        if best_opportunity.get("decision") == "WAIT":
            self.log(f"{pair}: R:R validation failed")
            return None

        # Enrich with whale and news context if orchestrator available
        if self.orchestrator:
            best_opportunity = await self._enrich_with_context(best_opportunity)
            if best_opportunity.get("decision") == "WAIT":
                return None

        self.log(
            f"Best opportunity: {best_opportunity['decision']} "
            f"{best_opportunity['pair']} @ confidence={best_opportunity['confidence']}%"
        )

        # Final confidence check after adjustments
        if best_opportunity["confidence"] < self.min_confidence:
            self.log("Confidence dropped below threshold after adjustments")
            return None

        return best_opportunity

    async def _fetch_all_market_data(
        self, pairs: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Fetch market data for all pairs in parallel."""
        async def fetch_one(pair_info: dict[str, Any]) -> Optional[dict[str, Any]]:
            symbol = pair_info["symbol"]
            market_data = await self.scanner.get_market_data(symbol)
            if market_data:
                return {"symbol": symbol, "market_data": market_data}
            return None

        # Fetch all in parallel with concurrency limit
        tasks = [fetch_one(p) for p in pairs]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Filter out None and exceptions
        valid_data = [
            r for r in results
            if r is not None and not isinstance(r, Exception)
        ]

        self.log(f"Got market data for {len(valid_data)}/{len(pairs)} pairs")
        return valid_data

    async def _enrich_with_context(
        self, opportunity: dict[str, Any]
    ) -> dict[str, Any]:
        """Enrich opportunity with whale and news context."""
        try:
            pair = opportunity["pair"]
            context = await self.orchestrator.get_market_context(pair)

            # Check for breaking news — may override decision
            if context.get("breaking_news", {}).get("is_breaking"):
                impact = context["breaking_news"]["impact"]
                if impact.get("impact_score", 0) >= 8:
                    recommended = impact.get("recommended_action", "wait")
                    if recommended == "close_positions":
                        self.log("Breaking news: closing positions recommended", "warning")
                        opportunity["breaking_news_override"] = True
                        opportunity["decision"] = "WAIT"
                        return opportunity

            # Add context to opportunity
            opportunity["whale_signal"] = context.get("whale", {})
            opportunity["news_sentiment"] = context.get("news", {})
            opportunity["market_sentiment"] = context.get("market", {})

            # Adjust confidence based on whale/news alignment
            whale_sig = context.get("whale", {}).get("whale_signal", "neutral")
            news_sent = context.get("news", {}).get("sentiment", "neutral")
            decision = opportunity["decision"]

            alignment_bonus = 0
            if decision == "LONG":
                if whale_sig in ("strong_buy", "buy"):
                    alignment_bonus += 5
                if news_sent in ("bullish", "very_bullish"):
                    alignment_bonus += 5
                if whale_sig in ("sell", "strong_sell"):
                    alignment_bonus -= 10
                if news_sent in ("bearish", "very_bearish"):
                    alignment_bonus -= 10
            elif decision == "SHORT":
                if whale_sig in ("sell", "strong_sell"):
                    alignment_bonus += 5
                if news_sent in ("bearish", "very_bearish"):
                    alignment_bonus += 5
                if whale_sig in ("strong_buy", "buy"):
                    alignment_bonus -= 10
                if news_sent in ("bullish", "very_bullish"):
                    alignment_bonus -= 10

            opportunity["confidence"] = int(clamp(
                opportunity["confidence"] + alignment_bonus, 0, 100
            ))

            if alignment_bonus != 0:
                self.log(f"Confidence adjusted by {alignment_bonus:+d} "
                         f"(whale={whale_sig}, news={news_sent})")

        except Exception as e:
            self.log(f"Error getting market context: {e}", "error")

        return opportunity

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

    def _validate_and_adjust_rr(
        self, analysis: dict[str, Any], market_data: dict[str, Any], symbol: str
    ) -> dict[str, Any]:
        """
        Validate and adjust Risk:Reward ratio.
        If R:R < 1.5:1, try to adjust TP. If impossible, set decision to WAIT.
        """
        entry = analysis.get("entry_price")
        sl = analysis.get("stop_loss")
        tp = analysis.get("take_profit")
        decision = analysis.get("decision", "WAIT")

        if not all([entry, sl, tp]) or entry <= 0:
            return analysis

        # Calculate current R:R
        if decision == "LONG":
            sl_distance = entry - sl
            tp_distance = tp - entry
        else:  # SHORT
            sl_distance = sl - entry
            tp_distance = entry - tp

        if sl_distance <= 0:
            self.log(f"{symbol}: Invalid SL distance ({sl_distance}), skipping")
            analysis["decision"] = "WAIT"
            return analysis

        # Calculate SL distance percentage
        sl_pct = abs(sl_distance / entry) * 100
        if sl_pct < self.MIN_SL_DISTANCE_PCT:
            # Adjust SL to minimum distance
            if decision == "LONG":
                new_sl = entry * (1 - self.MIN_SL_DISTANCE_PCT / 100)
                sl_distance = entry - new_sl
            else:
                new_sl = entry * (1 + self.MIN_SL_DISTANCE_PCT / 100)
                sl_distance = new_sl - entry
            self.log(
                f"{symbol}: SL too tight ({sl_pct:.1f}%), "
                f"adjusted to {self.MIN_SL_DISTANCE_PCT}%"
            )
            analysis["stop_loss"] = new_sl

        # Calculate R:R ratio
        rr_ratio = tp_distance / sl_distance if sl_distance > 0 else 0

        if rr_ratio < self.MIN_RR_RATIO:
            # Try to adjust TP to meet minimum R:R
            required_tp_distance = sl_distance * self.MIN_RR_RATIO

            if decision == "LONG":
                new_tp = entry + required_tp_distance
            else:  # SHORT
                new_tp = entry - required_tp_distance

            # Validate new TP is reasonable (not more than 10% from entry)
            tp_pct = abs(required_tp_distance / entry) * 100
            if tp_pct > 10:
                self.log(
                    f"{symbol}: R:R={rr_ratio:.2f} too low, required TP ({tp_pct:.1f}%) "
                    f"exceeds 10% limit - skipping"
                )
                analysis["decision"] = "WAIT"
                return analysis

            self.log(
                f"{symbol}: R:R adjusted from {rr_ratio:.2f}:1 to {self.MIN_RR_RATIO}:1 "
                f"(TP: {tp:.6f} -> {new_tp:.6f})"
            )
            analysis["take_profit"] = new_tp

        return analysis
