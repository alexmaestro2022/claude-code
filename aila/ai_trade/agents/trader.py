"""TRADER - scans market and finds opportunities."""

import json
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
        # Collect all analysis results for detailed logging
        all_analysis_results: list[dict] = []

        skipped_no_data = 0
        skipped_api_error = 0

        for pair_info in pairs[:10]:  # Analyze top 10
            symbol = pair_info["symbol"]

            # Get detailed market data
            market_data = await self.scanner.get_market_data(symbol)
            if not market_data:
                skipped_no_data += 1
                continue

            # Get knowledge context for this pair
            knowledge = self.knowledge_base.get_context_for_analysis(symbol)

            # Ask Claude for analysis
            analysis = await self.claude_client.get_market_analysis(
                symbol, market_data, knowledge
            )

            if "error" in analysis:
                skipped_api_error += 1
                self.log(f"Analysis error for {symbol}: {analysis['error']}", "error")
                continue

            decision = analysis.get("decision", "WAIT")
            confidence = analysis.get("confidence", 0)
            trend = market_data.get("trend", "NEUTRAL")

            # PRE-FILTER: Skip LONG signals against BEARISH trend
            if decision == "LONG" and trend == "BEARISH":
                self.log(f"{symbol}: LONG vs BEARISH trend - skipping")
                decision = "WAIT"
                analysis["decision"] = "WAIT"

            # PRE-FILTER: Skip SHORT signals against BULLISH trend
            if decision == "SHORT" and trend == "BULLISH":
                self.log(f"{symbol}: SHORT vs BULLISH trend - skipping")
                decision = "WAIT"
                analysis["decision"] = "WAIT"

            # Validate and adjust R:R ratio
            if decision != "WAIT":
                analysis = self._validate_and_adjust_rr(
                    analysis, market_data, symbol
                )
                decision = analysis.get("decision", "WAIT")

            # Collect result for detailed logging
            all_analysis_results.append({
                "symbol": symbol,
                "decision": decision,
                "confidence": confidence,
                "market_data": market_data,
                "reasoning": analysis.get("reasoning", ""),
            })

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

        # Log statistics
        if skipped_no_data > 0 or skipped_api_error > 0:
            self.log(f"Skipped: {skipped_no_data} no data, {skipped_api_error} API errors")

        # Log detailed analysis for top 5 pairs by confidence
        self._log_top_pairs_analysis(all_analysis_results)

        if best_opportunity:
            # Enrich with whale and news context if orchestrator available
            if self.orchestrator:
                try:
                    pair = best_opportunity["pair"]
                    context = await self.orchestrator.get_market_context(pair)

                    # Check for breaking news — may override decision
                    if context.get("breaking_news", {}).get("is_breaking"):
                        impact = context["breaking_news"]["impact"]
                        if impact.get("impact_score", 0) >= 8:
                            recommended = impact.get("recommended_action", "wait")
                            if recommended == "close_positions":
                                self.log("Breaking news: closing positions recommended", "warning")
                                best_opportunity["breaking_news_override"] = True
                                best_opportunity["decision"] = "WAIT"
                                return best_opportunity

                    # Add context to opportunity
                    best_opportunity["whale_signal"] = context.get("whale", {})
                    best_opportunity["news_sentiment"] = context.get("news", {})
                    best_opportunity["market_sentiment"] = context.get("market", {})

                    # Adjust confidence based on whale/news alignment
                    whale_sig = context.get("whale", {}).get("whale_signal", "neutral")
                    news_sent = context.get("news", {}).get("sentiment", "neutral")
                    decision = best_opportunity["decision"]

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

                    best_opportunity["confidence"] = int(clamp(
                        best_opportunity["confidence"] + alignment_bonus, 0, 100
                    ))

                    if alignment_bonus != 0:
                        self.log(f"Confidence adjusted by {alignment_bonus:+d} "
                                 f"(whale={whale_sig}, news={news_sent})")

                except Exception as e:
                    self.log(f"Error getting market context: {e}", "error")

            self.log(
                f"Best opportunity: {best_opportunity['decision']} "
                f"{best_opportunity['pair']} @ confidence={best_opportunity['confidence']}%"
            )

            # Re-check confidence after adjustments
            if best_opportunity["confidence"] < self.min_confidence:
                self.log("Confidence dropped below threshold after context adjustment")
                return None

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

    def _log_top_pairs_analysis(self, results: list[dict]) -> None:
        """Log detailed analysis for top 5 pairs by confidence."""
        self.log(f"Analysis completed for {len(results)} pairs")
        if not results:
            self.log("No pairs passed analysis (check market_data or Claude API)")
            return

        # Sort by confidence descending
        sorted_results = sorted(results, key=lambda x: x["confidence"], reverse=True)
        top_5 = sorted_results[:5]

        self.log("=" * 60)
        self.log("TOP 5 PAIRS ANALYSIS:")

        for r in top_5:
            symbol = r["symbol"]
            md = r["market_data"]
            confidence = r["confidence"]
            decision = r["decision"]

            # Format indicator values
            ema50 = md.get("ema50")
            ema200 = md.get("ema200")
            rsi = md.get("rsi")
            atr = md.get("atr")
            trend = md.get("trend", "UNKNOWN")
            price = md.get("price")

            # Format values for logging
            ema50_str = f"{ema50:.4f}" if ema50 else "N/A"
            ema200_str = f"{ema200:.4f}" if ema200 else "N/A"
            rsi_str = f"{rsi:.1f}" if rsi else "N/A"
            atr_str = f"{atr:.6f}" if atr else "N/A"
            price_str = f"{price:.4f}" if price else "N/A"

            # Determine rejection reason
            status = "ACCEPTED" if confidence >= self.min_confidence and decision != "WAIT" else "rejected"
            if status == "rejected":
                if decision == "WAIT":
                    reason = "no clear signal"
                elif confidence < self.min_confidence:
                    reason = f"below threshold ({self.min_confidence}%)"
                else:
                    reason = "unknown"
                status_str = f"rejected: {reason}"
            else:
                status_str = f"ACCEPTED for {decision}"

            # Log detailed line
            self.log(
                f"{symbol}: price={price_str}, EMA50={ema50_str}, EMA200={ema200_str}, "
                f"RSI={rsi_str}, ATR={atr_str}, trend={trend}, "
                f"decision={decision}, confidence={confidence}% ({status_str})"
            )

        self.log("=" * 60)

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
