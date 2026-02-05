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

        # Get knowledge context enriched with BTC and trades history
        knowledge = self._build_enriched_knowledge(pairs_data)

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

    async def analyze_pairs(
        self, pairs_data: list[dict[str, Any]]
    ) -> Optional[dict]:
        """
        Analyze a pre-filtered list of pairs with market data.
        Used by cascade analysis to analyze priority groups.

        Args:
            pairs_data: List of dicts with 'symbol' and 'market_data' keys

        Returns:
            Best opportunity dict or None
        """
        if not pairs_data:
            return None

        self.log(f"Cascade: Analyzing {len(pairs_data)} pairs...")

        # Get knowledge context enriched with BTC and trades history
        knowledge = self._build_enriched_knowledge(pairs_data)

        # Single Claude API call for all pairs
        analysis = await self.claude_client.batch_analyze_market(pairs_data, knowledge)

        if "error" in analysis:
            self.log(f"Cascade analysis error: {analysis['error']}", "error")
            return None

        decision = analysis.get("decision", "WAIT")
        confidence = analysis.get("confidence", 0)
        pair = analysis.get("pair")

        if decision == "WAIT" or confidence < self.min_confidence:
            self.log(f"Cascade: No opportunity in batch (decision={decision}, conf={confidence}%)")
            return None

        # Get market data for the selected pair
        market_data = next(
            (p["market_data"] for p in pairs_data if p["symbol"] == pair),
            {}
        )

        # Validate trend alignment
        trend = market_data.get("trend", "NEUTRAL")
        if decision == "LONG" and trend == "BEARISH":
            self.log(f"Cascade: {pair} LONG vs BEARISH trend - skipping")
            return None
        if decision == "SHORT" and trend == "BULLISH":
            self.log(f"Cascade: {pair} SHORT vs BULLISH trend - skipping")
            return None

        # Build opportunity
        opportunity = {
            "pair": pair,
            "decision": decision,
            "confidence": confidence,
            "strategy": analysis.get("strategy", "cascade_analysis"),
            "entry_price": analysis.get("entry_price"),
            "stop_loss": analysis.get("stop_loss"),
            "take_profit": analysis.get("take_profit"),
            "leverage": analysis.get("leverage", 2),
            "position_size_pct": analysis.get("position_size_pct", 2),
            "reasoning": analysis.get("reasoning", ""),
            "risks": analysis.get("risks", []),
            "expected_duration": analysis.get("expected_duration", "1h"),
            "market_data": market_data,
            "pairs_analyzed": len(pairs_data),
        }

        # Validate and adjust R:R ratio
        opportunity = self._validate_and_adjust_rr(opportunity, market_data, pair)
        if opportunity.get("decision") == "WAIT":
            self.log(f"Cascade: {pair} R:R validation failed")
            return None

        # Enrich with whale and news context
        if self.orchestrator:
            opportunity = await self._enrich_with_context(opportunity)
            if opportunity.get("decision") == "WAIT":
                return None

        self.log(
            f"Cascade opportunity: {opportunity['decision']} "
            f"{opportunity['pair']} @ confidence={opportunity['confidence']}%"
        )

        # Final confidence check after adjustments
        if opportunity["confidence"] < self.min_confidence:
            self.log("Cascade: Confidence dropped below threshold after adjustments")
            return None

        return opportunity

    def _build_enriched_knowledge(
        self, pairs_data: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Build knowledge dict enriched with BTC context and recent trades."""
        knowledge = self.knowledge_base.get_context_for_analysis("", agent="TRADER")

        # Extract BTC data from pairs_data if available
        btc_entry = next(
            (p for p in pairs_data if p["symbol"] in ("BTC/USDT", "BTCUSDT")), None
        )
        if btc_entry:
            md = btc_entry.get("market_data", {})
            macd = md.get("macd", {})
            knowledge["btc_context"] = {
                "price": md.get("price"),
                "trend": md.get("trend"),
                "change_24h": md.get("change_24h"),
                "rsi": md.get("rsi"),
                "macd_signal": "bullish" if (macd.get("histogram") or 0) > 0 else "bearish",
            }

        # Add market sentiment from scanner if available
        if self.orchestrator and hasattr(self.orchestrator, "scanner"):
            try:
                overview = self.scanner._cache.get("market_overview")
                if overview:
                    knowledge["market_sentiment"] = overview
            except Exception:
                pass

        # Add recent trades from agent_stats
        if self.orchestrator and hasattr(self.orchestrator, "autopilot"):
            try:
                stats = self.orchestrator.autopilot._agent_stats.get_stats("TRADER")
                history = stats.get("trades_history", [])
                knowledge["recent_trades"] = history[:3]
            except Exception:
                pass

        return knowledge

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

            # Add context to opportunity (also used by Stage 4 to avoid re-fetching)
            opportunity["whale_signal"] = context.get("whale", {})
            opportunity["news_sentiment"] = context.get("news", {})
            opportunity["market_sentiment"] = context.get("market", {})
            opportunity["_market_context"] = context  # Full context for Stage 4 reuse

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

    def _get_management_rules_text(self) -> str:
        """Get position management rules from knowledge base for prompt."""
        try:
            trader_learning = self.knowledge_base.data.get("trader_learning", {})
            mgmt_rules = trader_learning.get("management_rules", [])
            if not mgmt_rules:
                return "No management rules learned yet."
            # Get last 5 rules
            rules_text = []
            for r in mgmt_rules[-5:]:
                rule = r.get("rule", str(r)) if isinstance(r, dict) else str(r)
                rules_text.append(f"- {rule[:80]}")
            return "\n".join(rules_text) if rules_text else "No management rules learned yet."
        except Exception:
            return "No management rules learned yet."

    async def evaluate_exit(self, position: dict, market_data: dict) -> dict:
        """Evaluate whether to exit or modify an existing position.

        Args:
            position: Current position data (symbol, direction, entry_price,
                      current_price, unrealized_pnl_pct, duration, leverage,
                      stop_loss, take_profit, peak_pnl_pct).
            market_data: Current market data (price, rsi, trend, atr, news).

        Returns:
            Dict with action, new_sl, new_tp, close_pct, reason, urgency.
        """
        symbol = position.get("symbol", "unknown")
        pnl_pct = position.get("unrealized_pnl_pct", 0)
        peak_pnl = position.get("peak_pnl_pct", 0)
        direction = position.get("direction", "LONG")
        leverage = position.get("leverage", 1)
        entry_price = position.get("entry_price", 0)
        current_price = position.get("current_price", market_data.get("price", 0))

        # Derive trend/momentum signals from indicators
        macd = market_data.get("macd", {})
        bb = market_data.get("bollinger", {})
        vp = market_data.get("volume_profile", {})
        srsi = market_data.get("stoch_rsi", {})

        macd_signal = "bullish" if (macd.get("histogram") or 0) > 0 else "bearish"
        vol_ratio = vp.get("ratio", 1.0) if vp else "N/A"

        # Determine if momentum supports or opposes position
        momentum_status = "neutral"
        if direction == "LONG":
            if macd_signal == "bearish" and (srsi.get("k", 50) if srsi else 50) > 70:
                momentum_status = "fading"
            elif macd_signal == "bullish" and (srsi.get("k", 50) if srsi else 50) < 80:
                momentum_status = "supporting"
        elif direction == "SHORT":
            if macd_signal == "bullish" and (srsi.get("k", 50) if srsi else 50) < 30:
                momentum_status = "fading"
            elif macd_signal == "bearish" and (srsi.get("k", 50) if srsi else 50) > 20:
                momentum_status = "supporting"

        prompt = f"""## ROLE
You are a position manager. Evaluate this open position and decide the best action.

## POSITION
- Pair: {symbol} | Direction: {direction} | Leverage: {leverage}x
- Entry: {entry_price} | Current: {current_price}
- PnL: {pnl_pct:+.2f}% | Peak PnL: {peak_pnl:+.2f}% | Drawdown from peak: {peak_pnl - pnl_pct:.2f}%
- Duration: {position.get('duration', 'unknown')}
- Stop Loss: {position.get('stop_loss', 'none')} | Take Profit: {position.get('take_profit', 'none')}

## CURRENT INDICATORS
- Price: {current_price} | Trend: {market_data.get('trend', 'N/A')}
- RSI: {market_data.get('rsi', 'N/A')} | StochRSI K: {market_data.get('stoch_rsi_k', srsi.get('k', 'N/A') if srsi else 'N/A')}
- MACD: {macd_signal} (hist={market_data.get('macd_histogram', macd.get('histogram', 'N/A'))})
- Bollinger %B: {market_data.get('bollinger_pct_b', bb.get('pct_b', 'N/A') if bb else 'N/A')} (>1=overbought, <0=oversold)
- Volume ratio: {market_data.get('volume_ratio', vol_ratio)} (>1.5=high, <0.5=low)
- ATR: {market_data.get('atr', 'N/A')}
- EMA50: {market_data.get('ema50', 'N/A')} | EMA200: {market_data.get('ema200', 'N/A')}
- Support: {market_data.get('support', 'N/A')} | Resistance: {market_data.get('resistance', 'N/A')}
- Momentum: {momentum_status}

## ORDERBOOK
- Imbalance: {market_data.get('orderbook_imbalance', 0):.3f} ({market_data.get('orderbook_signal', 'BALANCED')})
- Big bid walls: {market_data.get('big_bid_walls', 0)} | Big ask walls: {market_data.get('big_ask_walls', 0)}
- Spread: {market_data.get('spread_pct', 0):.4f}%

## MARKET SENTIMENT
- Funding rate: {market_data.get('funding_rate', 0):.4f}%
- OI change 25m: {market_data.get('oi_change_pct', 0):.2f}%
- Liquidation pressure: {market_data.get('liquidation_pressure', 'LOW')}
- Fear & Greed: {market_data.get('fear_greed', 50)} ({market_data.get('fear_greed_label', 'Neutral')})

## LEARNED MANAGEMENT RULES (from past trades)
{self._get_management_rules_text()}

## DECISION RULES
1. PnL dropping from peak by >50% of peak → tighten SL or partial close
2. Trend reversed against position → CLOSE (urgency=high)
3. PnL > +5% and momentum=fading → PARTIAL_CLOSE 50%
4. PnL > +3% → move SL to breakeven+0.5%
5. NEVER move SL further from entry (only tighten)
6. If near S/R level that opposes position → consider closing
7. Orderbook imbalance against position (SELL_PRESSURE for LONG) → tighten SL
8. Funding extreme (>0.1% for LONG, <-0.1% for SHORT) → extra caution
9. Fear&Greed <20 favors LONG, >80 favors SHORT — consider if aligned
10. High liquidation pressure → volatile, tighten SL
11. Partial close: 25-75% of position

## TASK
Respond STRICTLY in JSON:
{{
    "action": "HOLD" | "CLOSE" | "MOVE_SL" | "MOVE_TP" | "PARTIAL_CLOSE",
    "new_sl": null or exact price (for MOVE_SL only),
    "new_tp": null or exact price (for MOVE_TP only),
    "close_pct": null or 25-75 (for PARTIAL_CLOSE only),
    "reason": "1 sentence explaining why",
    "urgency": "low" | "medium" | "high"
}}"""
        result = await self.claude_client.analyze(
            prompt,
            use_haiku=True,
            agent="TRADER",
            action="evaluate_exit",
            context=f"pair={symbol},pnl={pnl_pct:.1f}%",
        )

        if "error" not in result:
            action = result.get("action", "HOLD")
            self.log(
                f"Exit eval: {symbol} → {action} "
                f"(urgency={result.get('urgency', '?')}) "
                f"pnl={pnl_pct:.1f}%"
            )

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
