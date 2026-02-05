"""SNIPER - instant entries on key events (breakouts, listings, news)."""

import asyncio
import logging
from datetime import datetime
from typing import Any, Callable, Optional

from ...utils.common import TTLCache
from .base_agent import BaseAgent

logger = logging.getLogger("ai_trade")

MIN_SL_PERCENT = 2.0
MIN_RR_RATIO = 2.0


class SniperAgent(BaseAgent):
    """Sniper entries on key market events."""

    __slots__ = (
        "scanner",
        "_cache",
        "_pending_snipes",
        "_triggers",
        "_agent_stats",
        "_snipe_cooldowns",
        "_duplicate_counter",
        "_last_opportunity_log",
    )

    def __init__(
        self,
        claude_client: Any,
        knowledge_base: Any,
        scanner: Any = None,
        agent_stats: Any = None,
    ) -> None:
        super().__init__(
            name="SNIPER",
            claude_client=claude_client,
            knowledge_base=knowledge_base,
            log_path="/opt/aila/logs/ai_trade/sniper.log",
        )
        self.scanner = scanner
        self._agent_stats = agent_stats
        self._cache = TTLCache(default_ttl=10.0)
        self._pending_snipes: list[dict[str, Any]] = []
        self._triggers: dict[str, Callable] = {
            "breakout": self._check_breakout,
            "breakdown": self._check_breakdown,
            "liquidation_cascade": self._check_liquidations,
            "trend_continuation": self._check_trend_continuation,
        }
        # Cooldown tracking: pair -> last_trigger_time
        self._snipe_cooldowns: dict[str, datetime] = {}
        # Count duplicates for monitoring
        self._duplicate_counter: int = 0
        # Log dedup: pair -> last_log_time (prevent log spam)
        self._last_opportunity_log: dict[str, datetime] = {}

    def _get_max_leverage(self) -> int:
        """Get max leverage from agent level config."""
        if self._agent_stats:
            limits = self._agent_stats.get_level_limits("SNIPER")
            return limits.get("max_leverage", 2)
        return 2  # Default conservative

    def _calc_sl_tp(
        self, price: float, atr: float, direction: str
    ) -> tuple[float, float]:
        """Calculate SL/TP with min SL 2% and R:R >= 2.0."""
        atr_percent = (atr / price) * 100 if price > 0 else 2.0
        sl_percent = max(1.5 * atr_percent, MIN_SL_PERCENT)
        tp_percent = sl_percent * MIN_RR_RATIO

        sl_offset = price * sl_percent / 100
        tp_offset = price * tp_percent / 100

        if direction == "LONG":
            return price - sl_offset, price + tp_offset
        return price + sl_offset, price - tp_offset

    def _calc_breakout_confidence(self, data: dict[str, Any]) -> int:
        """Calculate dynamic confidence for breakout/breakdown.

        Base 65% + bonuses for volume, RSI strength, proximity to level.
        Max 90%.
        """
        confidence = 65

        # Volume bonus: higher volume = higher confidence
        vol_ratio = data.get("volume_ratio", 1.0)
        if vol_ratio > 2.0:
            confidence += 10
        elif vol_ratio > 1.5:
            confidence += 5

        # RSI strength bonus: stronger RSI = higher confidence
        rsi = data.get("rsi", 50)
        rsi_distance = abs(rsi - 50)
        if rsi_distance > 15:
            confidence += 5
        if rsi_distance > 25:
            confidence += 5

        # Proximity to level bonus
        price = data.get("price", 0)
        high_24h = data.get("high_24h", 0)
        low_24h = data.get("low_24h", 0)

        if high_24h and price > 0:
            proximity_high = abs(price - high_24h) / price * 100
            if proximity_high < 0.05:
                confidence += 5

        if low_24h and price > 0:
            proximity_low = abs(price - low_24h) / price * 100
            if proximity_low < 0.05:
                confidence += 5

        return min(confidence, 90)

    def _calc_liquidation_confidence(self, change: float, rsi: float) -> int:
        """Calculate dynamic confidence for liquidation cascade.

        Base 70% + bonuses for move size and RSI extremum.
        Max 95%.
        """
        confidence = 70

        # Move size bonus
        if change > 8:
            confidence += 10
        elif change > 6:
            confidence += 5

        # RSI extremum bonus
        if rsi < 15 or rsi > 85:
            confidence += 10
        elif rsi < 20 or rsi > 80:
            confidence += 5

        return min(confidence, 95)

    async def think(self, context: dict[str, Any]) -> dict[str, Any]:
        """Process sniper context."""
        pairs = context.get("pairs", [])
        if not pairs:
            return {"snipes": []}
        snipes = await self.scan_for_snipes(pairs)
        return {"snipes": snipes}

    async def scan_for_snipes(self, pairs: list[str]) -> list[dict[str, Any]]:
        """Scan pairs for sniper opportunities in parallel."""
        tasks = [self._analyze_pair(pair) for pair in pairs]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        snipes: list[dict[str, Any]] = []
        now = datetime.utcnow()
        for pair, result in zip(pairs, results):
            if isinstance(result, Exception):
                self.log(f"Snipe scan failed for {pair}: {result}", "error")
                continue
            if result and result.get("snipe_ready"):
                snipes.append(result)
                # Log max once per 5 min per pair to prevent spam
                last_log = self._last_opportunity_log.get(pair)
                if not last_log or (now - last_log).total_seconds() >= 300:
                    trigger = result.get("trigger_type", "unknown")
                    conf = result.get("confidence", 0)
                    self.log(
                        f"Snipe opportunity: {pair} - {trigger} (conf={conf}%)",
                        "warning",
                    )
                    self._last_opportunity_log[pair] = now

        return snipes

    async def prepare_snipe(self, snipe: dict[str, Any]) -> dict[str, Any]:
        """Calculate precise entry parameters for a snipe."""
        symbol = snipe.get("pair", "")
        trigger_type = snipe.get("trigger_type", "")
        max_lev = self._get_max_leverage()

        # Get funding, OI, liquidation and Fear & Greed data
        market_context = ""
        if self.scanner:
            try:
                md = await self.scanner.get_market_data(symbol, "5m")
                if md:
                    fr = md.get("funding_rate", 0)
                    fs = md.get("funding_signal", "NEUTRAL")
                    oi = md.get("open_interest", 0)
                    liq = md.get("liquidation_pressure", "LOW")
                    oi_chg = md.get("oi_change_pct", 0)
                    fng = md.get("fear_greed", 50)
                    fng_label = md.get("fear_greed_label", "Neutral")
                    market_context = (
                        f"FUNDING: {fr:.4f}% ({fs}), OI: {oi:,.0f}\n"
                        f"Liquidation: {liq}, OI change 25m: {oi_chg}%\n"
                        f"Fear&Greed: {fng} ({fng_label})\n"
                    )
            except Exception:
                pass

        # Get experience from knowledge base
        sniper_kb = self.knowledge_base.data.get("sniper_learning", {})
        mistakes = sniper_kb.get("mistakes_to_avoid", [])[-3:]
        rules = sniper_kb.get("learned_rules", [])[-3:]

        # Build experience section
        exp_lines = []
        if mistakes:
            mistake_strs = [
                m.get("lesson", str(m))[:60] if isinstance(m, dict) else str(m)[:60]
                for m in mistakes
            ]
            exp_lines.append("AVOID: " + "; ".join(mistake_strs))
        if rules:
            rule_strs = [
                r.get("rule", str(r))[:50] if isinstance(r, dict) else str(r)[:50]
                for r in rules
            ]
            exp_lines.append("RULES: " + "; ".join(rule_strs))
        experience_section = "\n".join(exp_lines) + "\n" if exp_lines else ""

        prompt = f"""Prepare sniper entry:

TRIGGER: {trigger_type}
PAIR: {symbol}
DIRECTION: {snipe.get('direction')}
ENTRY PRICE: {snipe.get('entry_price')}
MAX LEVERAGE: {max_lev}
{market_context}{experience_section}
Calculate optimal parameters. Funding >0.05% = overleveraged long (SHORT bias), <-0.05% = LONG bias.
Fear&Greed <20 = best LONG opportunity, >80 = best SHORT. High liq pressure after drop = reversal potential.

Respond in JSON only:
{{"entry_type": "market"|"limit", "entry_price": number,
"stop_loss": number, "take_profit_1": number, "take_profit_2": number,
"position_size_pct": 1-3, "leverage": 1-{max_lev},
"max_slippage_pct": number, "time_limit_seconds": 30-300,
"abort_conditions": ["condition1", "condition2"]}}"""

        result = await self.claude_client.analyze(
            prompt,
            agent="SNIPER",
            action="prepare_snipe",
            context=f"pair={symbol},trigger={trigger_type}",
        )
        self.log(f"Snipe prepared: {symbol} {snipe.get('direction')}")
        return result

    async def get_pending_snipes(self) -> list[dict[str, Any]]:
        """Return pending snipes."""
        return self._pending_snipes

    async def cancel_snipe(self, pair: str) -> bool:
        """Cancel a pending snipe by pair."""
        before = len(self._pending_snipes)
        self._pending_snipes = [
            s for s in self._pending_snipes if s.get("pair") != pair
        ]
        cancelled = len(self._pending_snipes) < before
        if cancelled:
            self.log(f"Snipe cancelled: {pair}")
        return cancelled

    async def evaluate_exit(self, position: dict, market_data: dict) -> dict:
        """Evaluate SNIPER position exit — aggressive rules for quick trades.

        SNIPER positions target 5-30 min holding, quick profit capture.
        """
        symbol = position.get("symbol", "unknown")
        pnl_pct = position.get("unrealized_pnl_pct", 0)
        peak_pnl = position.get("peak_pnl_pct", 0)
        direction = position.get("direction", "LONG")
        leverage = position.get("leverage", 1)
        entry_price = position.get("entry_price", 0)
        current_price = position.get("current_price", market_data.get("price", 0))
        duration = position.get("duration", "0h 0m")

        # Parse duration to minutes
        try:
            parts = duration.replace("h", "").replace("m", "").split()
            hours = int(parts[0]) if parts else 0
            mins = int(parts[1]) if len(parts) > 1 else 0
            duration_mins = hours * 60 + mins
        except (ValueError, IndexError):
            duration_mins = 0

        rsi = market_data.get("rsi", 50)
        vol_ratio = market_data.get("volume_ratio", 1.0)
        ob_signal = market_data.get("orderbook_signal", "BALANCED")
        ob_imbalance = market_data.get("orderbook_imbalance", 0)

        prompt = f"""## ROLE
You are a SNIPER position manager. Evaluate this quick-trade position aggressively.
SNIPER targets 5-30 minute holds. Be ready to exit fast on ANY warning sign.

## POSITION
- Pair: {symbol} | Direction: {direction} | Leverage: {leverage}x
- Entry: {entry_price} | Current: {current_price}
- PnL: {pnl_pct:+.2f}% | Peak PnL: {peak_pnl:+.2f}% | Drawdown from peak: {peak_pnl - pnl_pct:.2f}%
- Duration: {duration} ({duration_mins} minutes)
- Stop Loss: {position.get('stop_loss', 'none')} | Take Profit: {position.get('take_profit', 'none')}

## CURRENT INDICATORS
- Price: {current_price} | Trend: {market_data.get('trend', 'N/A')}
- RSI: {rsi} | StochRSI K: {market_data.get('stoch_rsi_k', 'N/A')}
- MACD hist: {market_data.get('macd_histogram', 'N/A')}
- Bollinger %B: {market_data.get('bollinger_pct_b', 'N/A')}
- Volume ratio: {vol_ratio} (>3 against position = EXIT)
- ATR: {market_data.get('atr', 'N/A')}
- Support: {market_data.get('support', 'N/A')} | Resistance: {market_data.get('resistance', 'N/A')}

## ORDERBOOK
- Imbalance: {ob_imbalance:.3f} ({ob_signal})
- Big bid walls: {market_data.get('big_bid_walls', 0)} | Big ask walls: {market_data.get('big_ask_walls', 0)}

## MARKET SENTIMENT
- Funding rate: {market_data.get('funding_rate', 0):.4f}%
- OI change 25m: {market_data.get('oi_change_pct', 0):.2f}%
- Liquidation pressure: {market_data.get('liquidation_pressure', 'LOW')}
- Fear & Greed: {market_data.get('fear_greed', 50)} ({market_data.get('fear_greed_label', 'Neutral')})

## LEARNED MANAGEMENT RULES
{self._get_management_rules_text()}

## SNIPER EXIT RULES (AGGRESSIVE)
1. Duration > 60 min → CLOSE immediately (SNIPER max hold time exceeded)
2. PnL > +1.5% in < 10 min → CLOSE (quick profit secured)
3. PnL > +0.3% → move SL to breakeven (tighter than TRADER)
4. RSI > 80 for LONG or RSI < 20 for SHORT → CLOSE (extreme reached)
5. Volume ratio > 3x against position → CLOSE (momentum shift)
6. Orderbook imbalance sharply against position (SELL_PRESSURE for LONG, BUY_PRESSURE for SHORT) → CLOSE
7. Drawdown from peak > 40% of peak PnL → CLOSE (momentum lost)
8. High liquidation pressure + duration > 15 min → CLOSE (overstayed)
9. NEVER hold SNIPER position > 60 min — this is for quick trades only
10. Trail SL at 0.3% from peak (tighter than TRADER's 0.5%)

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
            agent="SNIPER",
            action="evaluate_exit",
            context=f"pair={symbol},pnl={pnl_pct:.1f}%,dur={duration_mins}m",
        )

        if "error" not in result:
            action = result.get("action", "HOLD")
            self.log(
                f"Exit eval: {symbol} → {action} "
                f"(urgency={result.get('urgency', '?')}) "
                f"pnl={pnl_pct:.1f}% dur={duration_mins}m"
            )

        return result

    def get_duplicate_stats(self) -> dict[str, Any]:
        """Get statistics about blocked duplicate snipes."""
        active_cooldowns = sum(
            1
            for pair, ts in self._snipe_cooldowns.items()
            if (datetime.utcnow() - ts).total_seconds() < 300
        )
        return {
            "total_duplicates_blocked": self._duplicate_counter,
            "active_cooldowns": active_cooldowns,
            "cooldown_pairs": [
                {
                    "pair": pair,
                    "remaining_seconds": max(
                        0, 300 - int((datetime.utcnow() - ts).total_seconds())
                    ),
                }
                for pair, ts in self._snipe_cooldowns.items()
                if (datetime.utcnow() - ts).total_seconds() < 300
            ],
        }

    def _get_management_rules_text(self) -> str:
        """Get position management rules from knowledge base for prompt."""
        sniper_learning = self.knowledge_base.data.get("sniper_learning", {})
        mgmt_rules = sniper_learning.get("management_rules", [])
        if not mgmt_rules:
            return "No learned management rules yet."
        # Get last 5 rules
        recent_rules = mgmt_rules[-5:]
        lines = []
        for r in recent_rules:
            rule_text = r.get("rule", str(r)) if isinstance(r, dict) else str(r)
            lines.append(f"- {rule_text[:100]}")
        return "\n".join(lines)

    async def _analyze_pair(self, pair: str) -> dict[str, Any]:
        """Analyze a pair for snipe opportunity."""
        cache_key = f"snipe:{pair}"
        cached = self._cache.get(cache_key, ttl=10.0)
        if cached is not None:
            return cached

        # Filter using knowledge base
        sniper_knowledge = self.knowledge_base.data.get("sniper_learning", {})
        worst_pairs = [p["symbol"] for p in sniper_knowledge.get("worst_pairs", [])]
        mistakes = sniper_knowledge.get("mistakes_to_avoid", [])

        # Skip worst pairs
        if pair in worst_pairs:
            self.log(f"⛔ Skipping {pair} - in worst_pairs", "warning")
            return {"snipe_ready": False}

        # Check for repeated mistakes with same strategy
        for mistake in mistakes:
            if mistake["symbol"] == pair:
                self.log(
                    f"⛔ Skipping {pair} - learned lesson: {mistake['lesson']}",
                    "warning"
                )
                return {"snipe_ready": False}

        # Check cooldown (5 minutes minimum between snipes for same pair)
        if pair in self._snipe_cooldowns:
            last_trigger = self._snipe_cooldowns[pair]
            elapsed_seconds = (datetime.utcnow() - last_trigger).total_seconds()
            if elapsed_seconds < 300:  # 5 minutes
                self._duplicate_counter += 1
                remaining = 300 - int(elapsed_seconds)
                self.log(
                    f"Snipe cooldown active for {pair}: {remaining}s remaining "
                    f"(duplicates blocked: {self._duplicate_counter})",
                    "debug",
                )
                return {"snipe_ready": False}

        # Filter triggers by settings
        from ..agent_settings import get_agent_settings
        sniper_settings = get_agent_settings().get_settings("SNIPER")
        trigger_filter = {
            "breakout": sniper_settings.get("trigger_breakout", True),
            "breakdown": sniper_settings.get("trigger_breakdown", True),
            "liquidation_cascade": sniper_settings.get("trigger_liquidation", False),
            "trend_continuation": sniper_settings.get("trigger_trend_continuation", True),
        }

        for trigger_name, trigger_func in self._triggers.items():
            if not trigger_filter.get(trigger_name, True):
                continue
            try:
                result = await trigger_func(pair)
                if result and result.get("triggered"):
                    snipe = {
                        "pair": pair,
                        "snipe_ready": True,
                        "trigger_type": trigger_name,
                        "direction": result.get("direction", "LONG"),
                        "entry_price": result.get("entry_price"),
                        "stop_loss": result.get("stop_loss"),
                        "take_profit": result.get("take_profit"),
                        "confidence": result.get("confidence", 70),
                        "leverage": result.get("leverage", 2),
                        "urgency": result.get("urgency", "medium"),
                        "reasoning": result.get("reasoning", ""),
                        "timestamp": datetime.utcnow().isoformat(),
                    }
                    # Set cooldown
                    self._snipe_cooldowns[pair] = datetime.utcnow()
                    self._cache.set(cache_key, snipe)
                    self.log(
                        f"Snipe cooldown started for {pair} (5 min)",
                        "debug",
                    )
                    return snipe
            except Exception as e:
                self.log(f"Trigger {trigger_name} error for {pair}: {e}", "error")

        return {"snipe_ready": False}

    async def _check_breakout(self, pair: str) -> dict[str, Any]:
        """Check for resistance breakout."""
        if not self.scanner:
            return {"triggered": False}
        try:
            data = await self.scanner.get_market_data(pair, "5m")
            if not data:
                return {"triggered": False}

            price = data.get("price", 0)
            high_24h = data.get("high_24h", 0)
            atr = data.get("atr", 0)

            # Breakout: price within 0.1% of 24h high with momentum
            if high_24h and price > high_24h * 0.999 and atr > 0:
                rsi = data.get("rsi", 50)
                if 55 < rsi < 80:  # Not overbought
                    confidence = self._calc_breakout_confidence(data)
                    sl, tp = self._calc_sl_tp(price, atr, "LONG")
                    max_lev = self._get_max_leverage()
                    return {
                        "triggered": True,
                        "direction": "LONG",
                        "entry_price": price,
                        "stop_loss": sl,
                        "take_profit": tp,
                        "confidence": confidence,
                        "leverage": min(5, max_lev),
                        "urgency": "high",
                        "reasoning": f"Breakout: price at 24h high, RSI={rsi}, conf={confidence}%",
                    }
        except Exception as e:
            self.log(f"Breakout check error {pair}: {e}", "error")
        return {"triggered": False}

    async def _check_breakdown(self, pair: str) -> dict[str, Any]:
        """Check for support breakdown."""
        if not self.scanner:
            return {"triggered": False}
        try:
            data = await self.scanner.get_market_data(pair, "5m")
            if not data:
                return {"triggered": False}

            price = data.get("price", 0)
            low_24h = data.get("low_24h", 0)
            atr = data.get("atr", 0)

            # Breakdown: price within 0.1% of 24h low
            if low_24h and price < low_24h * 1.001 and atr > 0:
                rsi = data.get("rsi", 50)
                if 20 < rsi < 45:  # Not oversold
                    confidence = self._calc_breakout_confidence(data)
                    sl, tp = self._calc_sl_tp(price, atr, "SHORT")
                    max_lev = self._get_max_leverage()
                    return {
                        "triggered": True,
                        "direction": "SHORT",
                        "entry_price": price,
                        "stop_loss": sl,
                        "take_profit": tp,
                        "confidence": confidence,
                        "leverage": min(5, max_lev),
                        "urgency": "high",
                        "reasoning": f"Breakdown: price at 24h low, RSI={rsi}, conf={confidence}%",
                    }
        except Exception as e:
            self.log(f"Breakdown check error {pair}: {e}", "error")
        return {"triggered": False}

    async def _check_liquidations(self, pair: str) -> dict[str, Any]:
        """Check for liquidation cascades (potential bounce after)."""
        if not self.scanner:
            return {"triggered": False}
        try:
            data = await self.scanner.get_market_data(pair, "5m")
            if not data:
                return {"triggered": False}

            # Safe extraction with None checks
            change_24h = data.get("change_24h")
            rsi = data.get("rsi")
            atr = data.get("atr")
            price = data.get("price")

            # Check for None values before processing
            if change_24h is None or rsi is None or atr is None or price is None:
                return {"triggered": False}

            # Ensure numeric values
            if not isinstance(change_24h, (int, float)):
                return {"triggered": False}
            if not isinstance(rsi, (int, float)):
                return {"triggered": False}
            if not isinstance(atr, (int, float)):
                return {"triggered": False}
            if not isinstance(price, (int, float)):
                return {"triggered": False}

            change = abs(change_24h)

            # Sharp move (>5%) + extreme RSI = potential liquidation cascade
            if change > 5 and (rsi < 20 or rsi > 80) and atr > 0 and price > 0:
                direction = "LONG" if rsi < 20 else "SHORT"
                confidence = self._calc_liquidation_confidence(change, rsi)
                sl, tp = self._calc_sl_tp(price, atr, direction)
                max_lev = self._get_max_leverage()
                return {
                    "triggered": True,
                    "direction": direction,
                    "entry_price": price,
                    "stop_loss": sl,
                    "take_profit": tp,
                    "confidence": confidence,
                    "leverage": min(3, max_lev),
                    "urgency": "high",
                    "reasoning": f"Liquidation cascade: {change:.1f}% move, RSI={rsi}, conf={confidence}%",
                }
        except Exception as e:
            self.log(f"Liquidation check error {pair}: {e}", "error")
        return {"triggered": False}

    async def _check_trend_continuation(self, pair: str) -> dict[str, Any]:
        """Check for trend continuation opportunity (SHORT in bearish, LONG in bullish).

        Unlike liquidation_cascade which looks for extreme RSI bounces,
        this trigger looks for moderate moves with trend confirmation.
        """
        if not self.scanner:
            return {"triggered": False}
        try:
            data = await self.scanner.get_market_data(pair, "5m")
            if not data:
                return {"triggered": False}

            trend = data.get("trend", "NEUTRAL")
            change_24h = data.get("change_24h", 0)
            rsi = data.get("rsi", 50)
            atr = data.get("atr", 0)
            price = data.get("price", 0)
            macd = data.get("macd", {})
            macd_hist = macd.get("histogram", 0) if macd else 0

            # Validate numeric
            if not all(isinstance(v, (int, float)) for v in [change_24h, rsi, atr, price]):
                return {"triggered": False}

            if atr <= 0 or price <= 0:
                return {"triggered": False}

            change = abs(change_24h)
            max_lev = self._get_max_leverage()

            # BEARISH trend continuation SHORT
            # Conditions: bearish trend, price falling (change > 3%), RSI 15-40, MACD bearish
            if trend == "BEARISH" and change_24h < -3 and 15 < rsi < 40 and macd_hist < 0:
                confidence = min(80, 60 + int(change))
                sl, tp = self._calc_sl_tp(price, atr, "SHORT")
                return {
                    "triggered": True,
                    "direction": "SHORT",
                    "entry_price": price,
                    "stop_loss": sl,
                    "take_profit": tp,
                    "confidence": confidence,
                    "leverage": min(2, max_lev),
                    "urgency": "medium",
                    "reasoning": f"Trend continuation SHORT: {change_24h:.1f}% drop, RSI={rsi:.0f}, MACD bearish",
                }

            # BULLISH trend continuation LONG
            # Conditions: bullish trend, price rising (change > 3%), RSI 60-85, MACD bullish
            if trend == "BULLISH" and change_24h > 3 and 60 < rsi < 85 and macd_hist > 0:
                confidence = min(80, 60 + int(change))
                sl, tp = self._calc_sl_tp(price, atr, "LONG")
                return {
                    "triggered": True,
                    "direction": "LONG",
                    "entry_price": price,
                    "stop_loss": sl,
                    "take_profit": tp,
                    "confidence": confidence,
                    "leverage": min(2, max_lev),
                    "urgency": "medium",
                    "reasoning": f"Trend continuation LONG: +{change_24h:.1f}% rise, RSI={rsi:.0f}, MACD bullish",
                }

        except Exception as e:
            self.log(f"Trend continuation check error {pair}: {e}", "error")
        return {"triggered": False}
