"""Position Monitor — active management of open positions.

Runs every 15 seconds, applies fast rules (breakeven, trailing stop,
time-based warnings) without Claude API.  Every 2-3 minutes, calls
TRADER.evaluate_exit() via Claude for deeper analysis.
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger("ai_trade.position_monitor")
logger.setLevel(logging.INFO)

if not any(
    isinstance(h, logging.FileHandler)
    and getattr(h, "baseFilename", "").endswith("position_monitor.log")
    for h in logger.handlers
):
    _handler = logging.FileHandler("/opt/aila/logs/ai_trade/position_monitor.log")
    _handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(_handler)

# --- Configuration ---
CHECK_INTERVAL_SECONDS = 15

# TRADER thresholds (longer holds, wider stops)
TRADER_CLAUDE_EVAL_INTERVAL = 150  # 2.5 min
TRADER_BREAKEVEN_TRIGGER_PCT = 1.5
TRADER_BREAKEVEN_OFFSET_PCT = 0.1
TRADER_TRAILING_TRIGGER_PCT = 3.0
TRADER_TRAILING_DISTANCE_PCT = 1.5

# SNIPER thresholds (quick trades, tight stops)
SNIPER_CLAUDE_EVAL_INTERVAL = 60  # 1 min — check more often
SNIPER_BREAKEVEN_TRIGGER_PCT = 0.5
SNIPER_BREAKEVEN_OFFSET_PCT = 0.05
SNIPER_TRAILING_TRIGGER_PCT = 1.0
SNIPER_TRAILING_DISTANCE_PCT = 0.5

# Legacy defaults (for compatibility)
CLAUDE_EVAL_INTERVAL_SECONDS = TRADER_CLAUDE_EVAL_INTERVAL
BREAKEVEN_TRIGGER_PCT = TRADER_BREAKEVEN_TRIGGER_PCT
BREAKEVEN_OFFSET_PCT = TRADER_BREAKEVEN_OFFSET_PCT
TRAILING_TRIGGER_PCT = TRADER_TRAILING_TRIGGER_PCT
TRAILING_DISTANCE_PCT = TRADER_TRAILING_DISTANCE_PCT

# Time-based: warn if position open > expected_duration * 2
TIME_WARNING_MULTIPLIER = 2


class PositionMonitor:
    """Actively manages open positions with fast rules and Claude analysis."""

    __slots__ = (
        "_exchange", "_trader_agent", "_sniper_agent", "_position_manager", "_scanner",
        "_running", "_tracking", "_last_claude_eval",
    )

    def __init__(
        self,
        exchange: Any,
        trader_agent: Any,
        position_manager: Any,
        scanner: Any = None,
        sniper_agent: Any = None,
    ) -> None:
        self._exchange = exchange
        self._trader_agent = trader_agent
        self._sniper_agent = sniper_agent
        self._position_manager = position_manager
        self._scanner = scanner
        self._running = False
        # Per-symbol tracking state
        self._tracking: dict[str, dict[str, Any]] = {}
        # Last Claude evaluation time per symbol
        self._last_claude_eval: dict[str, datetime] = {}

    async def start(self) -> None:
        """Start the position monitoring loop."""
        if self._running:
            return
        self._running = True
        logger.info("[MONITOR] Position monitor started")
        asyncio.create_task(self._monitor_loop())

    def stop(self) -> None:
        """Stop the position monitoring loop."""
        self._running = False
        logger.info("[MONITOR] Position monitor stopped")

    async def _monitor_loop(self) -> None:
        """Main loop — runs every CHECK_INTERVAL_SECONDS."""
        while self._running:
            try:
                await self.check_positions()
            except Exception as e:
                logger.error(f"[MONITOR] Loop error: {e}")
            await asyncio.sleep(CHECK_INTERVAL_SECONDS)

    async def check_positions(self) -> list[dict[str, Any]]:
        """Check all open positions and apply management rules.

        Returns list of actions taken.
        """
        actions: list[dict[str, Any]] = []

        bot_positions = self._position_manager.get_bot_positions()
        if not bot_positions:
            return actions

        # Fetch real positions from exchange
        try:
            exchange_positions = await self._exchange.get_positions()
        except Exception as e:
            logger.error(f"[MONITOR] Failed to fetch positions: {e}")
            return actions

        exchange_map = {p["symbol"]: p for p in exchange_positions}

        # Clean up tracking for closed positions
        tracked_symbols = set(self._tracking.keys())
        active_symbols = set(bot_positions.keys())
        for closed in tracked_symbols - active_symbols:
            del self._tracking[closed]
            self._last_claude_eval.pop(closed, None)

        for symbol, bot_pos in bot_positions.items():
            exchange_pos = exchange_map.get(symbol)
            if not exchange_pos or exchange_pos.get("size", 0) == 0:
                continue

            # Initialize tracking state for new positions
            if symbol not in self._tracking:
                self._tracking[symbol] = {
                    "peak_pnl_pct": 0.0,
                    "breakeven_applied": False,
                    "trailing_active": False,
                    "trailing_sl": None,
                    "time_warning_sent": False,
                }

            track = self._tracking[symbol]

            # Calculate current PnL %
            entry_price = bot_pos.get("entry_price", 0)
            mark_price = exchange_pos.get("mark_price", 0)
            side = bot_pos.get("side", "LONG")
            leverage = int(bot_pos.get("leverage", 1))

            if entry_price <= 0 or mark_price <= 0:
                continue

            if side == "LONG":
                pnl_pct = ((mark_price - entry_price) / entry_price) * 100 * leverage
            else:
                pnl_pct = ((entry_price - mark_price) / entry_price) * 100 * leverage

            # Update peak PnL
            if pnl_pct > track["peak_pnl_pct"]:
                track["peak_pnl_pct"] = pnl_pct

            # Determine source for threshold selection
            source = bot_pos.get("source", "TRADER").upper()

            # --- Fast rules (no Claude) ---

            # 1. Breakeven rule (source-specific thresholds)
            action = await self._check_breakeven(
                symbol, side, entry_price, pnl_pct, track, source
            )
            if action:
                actions.append(action)

            # 2. Trailing stop rule (source-specific thresholds)
            action = await self._check_trailing_stop(
                symbol, side, entry_price, mark_price, pnl_pct, track, source
            )
            if action:
                actions.append(action)

            # 3. Time-based warning
            action = self._check_time_warning(symbol, bot_pos, track)
            if action:
                actions.append(action)

            # --- Claude evaluation (throttled) ---
            await self._maybe_claude_eval(
                symbol, bot_pos, exchange_pos, pnl_pct, actions
            )

        return actions

    async def _check_breakeven(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        pnl_pct: float,
        track: dict[str, Any],
        source: str = "TRADER",
    ) -> Optional[dict[str, Any]]:
        """Move SL to breakeven — thresholds depend on source (TRADER/SNIPER)."""
        if track["breakeven_applied"] or track["trailing_active"]:
            return None

        # Use source-specific thresholds
        if source == "SNIPER":
            trigger = SNIPER_BREAKEVEN_TRIGGER_PCT
            offset = SNIPER_BREAKEVEN_OFFSET_PCT
        else:
            trigger = TRADER_BREAKEVEN_TRIGGER_PCT
            offset = TRADER_BREAKEVEN_OFFSET_PCT

        if pnl_pct < trigger:
            return None

        # Calculate breakeven SL: entry + offset
        if side == "LONG":
            new_sl = entry_price * (1 + offset / 100)
        else:
            new_sl = entry_price * (1 - offset / 100)

        new_sl = await self._exchange.round_price(symbol, new_sl)

        logger.info(
            f"[MONITOR][{source}][BREAKEVEN] {symbol} PnL={pnl_pct:.1f}% >= {trigger}% "
            f"→ moving SL to breakeven @ {new_sl}"
        )

        result = await self._exchange.set_trading_stop(
            symbol=symbol, stop_loss=new_sl
        )

        if result.get("success"):
            track["breakeven_applied"] = True
            return {
                "symbol": symbol,
                "action": "BREAKEVEN",
                "new_sl": new_sl,
                "pnl_pct": round(pnl_pct, 2),
            }

        logger.warning(f"[MONITOR][BREAKEVEN] Failed for {symbol}: {result.get('message')}")
        return None

    async def _check_trailing_stop(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        mark_price: float,
        pnl_pct: float,
        track: dict[str, Any],
        source: str = "TRADER",
    ) -> Optional[dict[str, Any]]:
        """Activate/update trailing stop — thresholds depend on source (TRADER/SNIPER)."""
        # Use source-specific thresholds
        if source == "SNIPER":
            trigger = SNIPER_TRAILING_TRIGGER_PCT
            distance = SNIPER_TRAILING_DISTANCE_PCT
        else:
            trigger = TRADER_TRAILING_TRIGGER_PCT
            distance = TRADER_TRAILING_DISTANCE_PCT

        if pnl_pct < trigger:
            return None

        # Calculate trailing SL: trail distance% from current peak
        if side == "LONG":
            trailing_sl = mark_price * (1 - distance / 100)
        else:
            trailing_sl = mark_price * (1 + distance / 100)

        trailing_sl = await self._exchange.round_price(symbol, trailing_sl)

        # Only update if new SL is better than current
        current_trailing = track.get("trailing_sl")
        if current_trailing is not None:
            if side == "LONG" and trailing_sl <= current_trailing:
                return None
            if side == "SHORT" and trailing_sl >= current_trailing:
                return None

        if not track["trailing_active"]:
            logger.info(
                f"[MONITOR][{source}][TRAILING] {symbol} PnL={pnl_pct:.1f}% >= {trigger}% "
                f"→ activating trailing stop @ {trailing_sl} (distance={distance}%)"
            )
        else:
            logger.info(
                f"[MONITOR][{source}][TRAILING] {symbol} updating SL: "
                f"{current_trailing} → {trailing_sl} (PnL={pnl_pct:.1f}%)"
            )

        result = await self._exchange.set_trading_stop(
            symbol=symbol, stop_loss=trailing_sl
        )

        if result.get("success"):
            track["trailing_active"] = True
            track["trailing_sl"] = trailing_sl
            track["breakeven_applied"] = True  # Trailing supersedes breakeven
            return {
                "symbol": symbol,
                "action": "TRAILING_STOP",
                "new_sl": trailing_sl,
                "pnl_pct": round(pnl_pct, 2),
            }

        logger.warning(f"[MONITOR][TRAILING] Failed for {symbol}: {result.get('message')}")
        return None

    def _check_time_warning(
        self,
        symbol: str,
        bot_pos: dict[str, Any],
        track: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        """Warn if position is open longer than expected."""
        if track["time_warning_sent"]:
            return None

        opened_at = bot_pos.get("opened_at")
        if not opened_at:
            return None

        try:
            start = datetime.fromisoformat(opened_at)
        except (ValueError, TypeError):
            return None

        duration_minutes = (datetime.now() - start).total_seconds() / 60

        # Default expected duration: 4 hours (240 min)
        expected_duration = 240
        threshold = expected_duration * TIME_WARNING_MULTIPLIER

        if duration_minutes < threshold:
            return None

        track["time_warning_sent"] = True
        hours = int(duration_minutes // 60)
        mins = int(duration_minutes % 60)
        logger.warning(
            f"[MONITOR][TIME] {symbol} open for {hours}h{mins}m "
            f"(> {int(threshold)}min threshold) — needs Claude evaluation"
        )
        return {
            "symbol": symbol,
            "action": "TIME_WARNING",
            "duration_minutes": round(duration_minutes),
            "threshold_minutes": int(threshold),
        }

    async def _get_full_market_context(self, symbol: str) -> dict[str, Any]:
        """Collect ALL available market data for position evaluation."""
        result: dict[str, Any] = {"price": 0, "trend": "NEUTRAL", "rsi": 50}

        try:
            # 1. Scanner data (indicators, support/resistance, funding, etc.)
            if self._scanner:
                scanner_data = await self._scanner.get_market_data(symbol)
                if scanner_data:
                    result.update({
                        "price": scanner_data.get("price", 0),
                        "trend": scanner_data.get("trend", "NEUTRAL"),
                        "rsi": scanner_data.get("rsi", 50),
                        "stoch_rsi_k": scanner_data.get("stoch_rsi", {}).get("k", 50),
                        "macd_histogram": scanner_data.get("macd", {}).get("histogram", 0),
                        "bollinger_pct_b": scanner_data.get("bollinger", {}).get("pct_b", 0.5),
                        "volume_ratio": scanner_data.get("volume_profile", {}).get("ratio", 1.0),
                        "atr": scanner_data.get("atr", 0),
                        "support": scanner_data.get("support", 0),
                        "resistance": scanner_data.get("resistance", 0),
                        "ema50": scanner_data.get("ema50", 0),
                        "ema200": scanner_data.get("ema200", 0),
                        "funding_rate": scanner_data.get("funding_rate", 0),
                        "oi_change_pct": scanner_data.get("oi_change_pct", 0),
                        "liquidation_pressure": scanner_data.get("liquidation_pressure", "LOW"),
                        "fear_greed": scanner_data.get("fear_greed", 50),
                        "fear_greed_label": scanner_data.get("fear_greed_label", "Neutral"),
                    })

            # 2. Orderbook analysis
            orderbook = await self._exchange.get_orderbook_analysis(symbol)
            result.update({
                "orderbook_imbalance": orderbook.get("imbalance", 0),
                "orderbook_signal": orderbook.get("imbalance_signal", "BALANCED"),
                "big_bid_walls": len(orderbook.get("big_bid_walls", [])),
                "big_ask_walls": len(orderbook.get("big_ask_walls", [])),
                "spread_pct": orderbook.get("spread_pct", 0),
            })

        except Exception as e:
            logger.error(f"[MONITOR] Error getting market context for {symbol}: {e}")

        return result

    async def _maybe_claude_eval(
        self,
        symbol: str,
        bot_pos: dict[str, Any],
        exchange_pos: dict[str, Any],
        pnl_pct: float,
        actions: list[dict[str, Any]],
    ) -> None:
        """Call evaluate_exit() via Claude, routed by position source."""
        now = datetime.now()
        last_eval = self._last_claude_eval.get(symbol)

        # Determine source (TRADER or SNIPER)
        source = bot_pos.get("source", "TRADER").upper()
        is_sniper = source == "SNIPER"

        # Use source-specific eval interval
        eval_interval = SNIPER_CLAUDE_EVAL_INTERVAL if is_sniper else TRADER_CLAUDE_EVAL_INTERVAL

        if last_eval:
            elapsed = (now - last_eval).total_seconds()
            if elapsed < eval_interval:
                return

        self._last_claude_eval[symbol] = now

        # Build position context
        entry_price = bot_pos.get("entry_price", 0)
        opened_at = bot_pos.get("opened_at", "")
        duration = ""
        if opened_at:
            try:
                start = datetime.fromisoformat(opened_at)
                delta = now - start
                hours = int(delta.total_seconds() // 3600)
                mins = int((delta.total_seconds() % 3600) // 60)
                duration = f"{hours}h {mins}m"
            except (ValueError, TypeError):
                duration = "unknown"

        position_context = {
            "symbol": symbol,
            "direction": bot_pos.get("side", "LONG"),
            "entry_price": entry_price,
            "current_price": exchange_pos.get("mark_price", 0),
            "unrealized_pnl_pct": round(pnl_pct, 2),
            "duration": duration,
            "leverage": int(bot_pos.get("leverage", 1)),
            "stop_loss": bot_pos.get("stop_loss"),
            "take_profit": bot_pos.get("take_profit"),
            "peak_pnl_pct": round(
                self._tracking.get(symbol, {}).get("peak_pnl_pct", 0), 2
            ),
        }

        # Get FULL market context for Claude evaluation
        market_data = await self._get_full_market_context(symbol)
        # Ensure price from exchange is included
        if not market_data.get("price"):
            market_data["price"] = exchange_pos.get("mark_price", 0)

        try:
            # Route to appropriate agent based on source
            if is_sniper and self._sniper_agent:
                result = await self._sniper_agent.evaluate_exit(
                    position_context, market_data
                )
                agent_name = "SNIPER"
            else:
                result = await self._trader_agent.evaluate_exit(
                    position_context, market_data
                )
                agent_name = "TRADER"

            if "error" in result:
                logger.warning(f"[MONITOR][{agent_name}] {symbol} eval error: {result['error']}")
                return

            action = result.get("action", "HOLD")
            reason = result.get("reason", "")
            urgency = result.get("urgency", "low")

            logger.info(
                f"[MONITOR][{agent_name}] {symbol} → {action} "
                f"(urgency={urgency}) {reason[:80]}"
            )

            if action == "HOLD":
                return

            await self._execute_claude_action(symbol, bot_pos, result, actions)

        except Exception as e:
            logger.error(f"[MONITOR][{source}] {symbol} eval failed: {e}")

    async def _execute_claude_action(
        self,
        symbol: str,
        bot_pos: dict[str, Any],
        result: dict[str, Any],
        actions: list[dict[str, Any]],
    ) -> None:
        """Execute action recommended by Claude evaluate_exit."""
        action = result.get("action", "HOLD")
        side = bot_pos.get("side", "LONG")

        if action == "CLOSE":
            logger.warning(f"[MONITOR][CLOSE] {symbol} — Claude says CLOSE: {result.get('reason', '')[:80]}")
            close_result = await self._exchange.close_position_market(symbol)
            if close_result.get("success"):
                actions.append({
                    "symbol": symbol,
                    "action": "CLOSE",
                    "reason": result.get("reason", ""),
                })

        elif action == "MOVE_SL":
            new_sl = result.get("new_sl")
            if new_sl:
                new_sl = await self._exchange.round_price(symbol, float(new_sl))
                logger.info(f"[MONITOR][MOVE_SL] {symbol} → SL={new_sl}")
                sl_result = await self._exchange.set_trading_stop(
                    symbol=symbol, stop_loss=new_sl
                )
                if sl_result.get("success"):
                    actions.append({
                        "symbol": symbol,
                        "action": "MOVE_SL",
                        "new_sl": new_sl,
                    })

        elif action == "MOVE_TP":
            new_tp = result.get("new_tp")
            if new_tp:
                new_tp = await self._exchange.round_price(symbol, float(new_tp))
                logger.info(f"[MONITOR][MOVE_TP] {symbol} → TP={new_tp}")
                tp_result = await self._exchange.set_trading_stop(
                    symbol=symbol, take_profit=new_tp
                )
                if tp_result.get("success"):
                    actions.append({
                        "symbol": symbol,
                        "action": "MOVE_TP",
                        "new_tp": new_tp,
                    })

        elif action == "PARTIAL_CLOSE":
            close_pct = result.get("close_pct", 50)
            pos = await self._exchange.get_position(symbol)
            if pos and pos.get("size", 0) > 0:
                partial_size = pos["size"] * (close_pct / 100)
                partial_size = await self._exchange.round_qty(symbol, partial_size)
                if partial_size > 0:
                    close_side = "Sell" if side == "LONG" else "Buy"
                    logger.info(
                        f"[MONITOR][PARTIAL] {symbol} closing {close_pct}% "
                        f"({partial_size} of {pos['size']})"
                    )
                    order = await self._exchange.create_market_order(
                        symbol, close_side, partial_size,
                        params={"reduceOnly": True},
                    )
                    if order and order.get("id"):
                        actions.append({
                            "symbol": symbol,
                            "action": "PARTIAL_CLOSE",
                            "close_pct": close_pct,
                            "closed_size": partial_size,
                        })
