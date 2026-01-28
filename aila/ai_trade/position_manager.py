"""Position manager - handles order execution and tracking."""

import asyncio
import logging
from datetime import datetime
from typing import Any, Optional

from ..utils.common import retry_async
from ..utils.position_conflict import check_position_conflict
from .config import MIN_ORDER_SIZE_USDT
from .telegram_notifier import get_telegram_notifier

logger = logging.getLogger("ai_trade")


class PositionManager:
    """Manages open positions and order execution."""

    __slots__ = ("_exchange", "_open_positions")

    def __init__(self, exchange: Any) -> None:
        self._exchange = exchange
        self._open_positions: dict[str, dict[str, Any]] = {}

    @retry_async(max_attempts=2, base_delay=1.0)
    async def open_position(self, signal: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Open a new position based on AI signal."""
        symbol = signal.get("pair")
        direction = signal.get("decision")
        logger.info(f"[POSITION] open_position called: {symbol} {direction}")

        if not symbol or not direction or direction == "WAIT":
            logger.warning(f"[POSITION] Rejected: symbol={symbol}, direction={direction}")
            return None

        # Check for position conflict (any existing position on this symbol)
        if check_position_conflict(symbol):
            logger.warning(f"[POSITION] Conflict: {symbol} already has open position, skipping signal")
            return None

        logger.info(f"[POSITION] No conflict, proceeding with {symbol}")

        try:
            leverage = signal.get("leverage", 1)
            position_size_usdt = signal.get("position_size_usdt", 0)
            logger.info(f"[POSITION] Size: ${position_size_usdt:.2f}, leverage: {leverage}x")

            # Enforce minimum order size for Bybit
            # Bybit requires min $10 position size, but margin = position_size / leverage
            if position_size_usdt < MIN_ORDER_SIZE_USDT:
                # Calculate required margin for minimum order
                required_margin = MIN_ORDER_SIZE_USDT / leverage
                logger.info(
                    f"Calculated size: ${position_size_usdt:.2f}, "
                    f"using minimum: ${MIN_ORDER_SIZE_USDT} (margin: ${required_margin:.2f} with {leverage}x)"
                )
                position_size_usdt = MIN_ORDER_SIZE_USDT
                signal["position_size_usdt"] = position_size_usdt

            logger.info(f"[POSITION] Setting leverage {leverage}x for {symbol}")
            await self._exchange.set_leverage(leverage, symbol)

            logger.info(f"[POSITION] Fetching ticker for {symbol}")
            ticker = await self._exchange.fetch_ticker(symbol)
            if not ticker or "last" not in ticker:
                logger.error(f"[POSITION] Failed to get ticker for {symbol}: {ticker}")
                return None
            price = ticker["last"]
            raw_amount = position_size_usdt / price

            # Round quantity to valid precision for Bybit
            amount = await self._exchange.round_qty(symbol, raw_amount)
            logger.info(f"[POSITION] Price: {price}, raw_qty: {raw_amount:.8f}, rounded_qty: {amount}")

            side = "buy" if direction == "LONG" else "sell"
            logger.info(f"[POSITION] Creating market order: {side} {amount} {symbol}")
            order = await self._exchange.create_market_order(symbol, side, amount)
            if not order or "id" not in order:
                logger.error(f"[POSITION] Failed to create market order for {symbol}: {order}")
                return None
            logger.info(f"[POSITION] Order created: {order.get('id')}")

            position = self._build_position(signal, order, price, amount)
            logger.info(f"[POSITION] Setting SL/TP for {symbol}")
            await self._set_sl_tp(symbol, signal, direction, amount)

            self._open_positions[symbol] = position
            logger.info(f"[POSITION] SUCCESS: {direction} {symbol} @ {price}, lev={leverage}x")

            # Send Telegram notification
            asyncio.create_task(self._notify_position_opened(position, signal))

            return position

        except Exception as e:
            import traceback
            logger.error(f"[POSITION] EXCEPTION opening {symbol}: {e}")
            logger.error(f"[POSITION] Traceback: {traceback.format_exc()}")
            # Notify about error
            asyncio.create_task(
                get_telegram_notifier().notify_error("Position Open", str(e))
            )
            return None

    @retry_async(max_attempts=2, base_delay=1.0)
    async def close_position(self, symbol: str, reason: str = "manual") -> Optional[dict[str, Any]]:
        """Close an open position."""
        if symbol not in self._open_positions:
            return None

        position = self._open_positions[symbol]
        try:
            close_side = "sell" if position["direction"] == "LONG" else "buy"
            await self._exchange.create_market_order(
                symbol, close_side, position["amount"], params={"reduceOnly": True}
            )
            await self._cancel_open_orders(symbol)

            ticker = await self._exchange.fetch_ticker(symbol)
            exit_price = ticker["last"]
            trade_result = self._build_trade_result(position, exit_price, reason)

            del self._open_positions[symbol]
            logger.info(f"Position closed: {symbol} PnL={trade_result['pnl']:.2f}")

            # Send Telegram notification
            asyncio.create_task(self._notify_position_closed(trade_result))

            return trade_result

        except Exception as e:
            logger.error(f"Error closing position {symbol}: {e}")
            asyncio.create_task(
                get_telegram_notifier().notify_error("Position Close", str(e))
            )
            return None

    async def check_positions(self) -> list[dict[str, Any]]:
        """Check status of all open positions."""
        updates: list[dict[str, Any]] = []
        for symbol, position in list(self._open_positions.items()):
            try:
                ticker = await self._exchange.fetch_ticker(symbol)
                current_price = ticker["last"]
                pnl_pct = self._calc_unrealized_pnl(position, current_price)

                updates.append({
                    "symbol": symbol,
                    "direction": position["direction"],
                    "entry_price": position["entry_price"],
                    "current_price": current_price,
                    "unrealized_pnl_pct": pnl_pct,
                    "leverage": position["leverage"],
                })
            except Exception as e:
                logger.error(f"Error checking position {symbol}: {e}")
        return updates

    def get_open_count(self) -> int:
        """Get number of open positions."""
        return len(self._open_positions)

    async def _set_sl_tp(
        self, symbol: str, signal: dict[str, Any], direction: str, amount: float
    ) -> None:
        """Set stop loss and take profit orders."""
        stop_loss = signal.get("stop_loss")
        take_profit = signal.get("take_profit")
        sl_side = "sell" if direction == "LONG" else "buy"

        # Round prices to valid precision
        if stop_loss:
            stop_loss = await self._exchange.round_price(symbol, stop_loss)
            logger.info(f"[POSITION] Setting SL @ {stop_loss}")
            await self._exchange.create_order(
                symbol, "stop_market", sl_side, amount,
                params={"stopPrice": stop_loss, "reduceOnly": True},
            )
        if take_profit:
            take_profit = await self._exchange.round_price(symbol, take_profit)
            logger.info(f"[POSITION] Setting TP @ {take_profit}")
            await self._exchange.create_order(
                symbol, "take_profit_market", sl_side, amount,
                params={"stopPrice": take_profit, "reduceOnly": True},
            )

    async def _cancel_open_orders(self, symbol: str) -> None:
        """Cancel all open orders for a symbol."""
        try:
            open_orders = await self._exchange.fetch_open_orders(symbol)
            for o in open_orders:
                await self._exchange.cancel_order(o["id"], symbol)
        except Exception:
            pass

    @staticmethod
    def _build_position(
        signal: dict[str, Any], order: dict[str, Any], price: float, amount: float
    ) -> dict[str, Any]:
        """Build position dict from signal and order data."""
        return {
            "id": order.get("id"),
            "symbol": signal.get("pair"),
            "direction": signal.get("decision"),
            "entry_price": price,
            "amount": amount,
            "leverage": signal.get("leverage", 1),
            "stop_loss": signal.get("stop_loss"),
            "take_profit": signal.get("take_profit"),
            "position_size_usdt": signal.get("position_size_usdt"),
            "strategy": signal.get("strategy"),
            "confidence": signal.get("confidence"),
            "opened_at": datetime.now().isoformat(),
            "status": "open",
        }

    @staticmethod
    def _build_trade_result(
        position: dict[str, Any], exit_price: float, reason: str
    ) -> dict[str, Any]:
        """Build trade result from position and exit data."""
        direction = position["direction"]
        entry = position["entry_price"]
        leverage = position["leverage"]

        if direction == "LONG":
            pnl_pct = (exit_price - entry) / entry * 100
        else:
            pnl_pct = (entry - exit_price) / entry * 100
        pnl_pct *= leverage
        pnl_usdt = position["position_size_usdt"] * (pnl_pct / 100)

        return {
            **position,
            "exit_price": exit_price,
            "pnl": pnl_usdt,
            "pnl_pct": pnl_pct,
            "close_reason": reason,
            "closed_at": datetime.now().isoformat(),
            "status": "closed",
        }

    @staticmethod
    def _calc_unrealized_pnl(position: dict[str, Any], current_price: float) -> float:
        """Calculate unrealized PnL percentage."""
        entry = position["entry_price"]
        if position["direction"] == "LONG":
            pnl_pct = (current_price - entry) / entry * 100
        else:
            pnl_pct = (entry - current_price) / entry * 100
        return pnl_pct * position["leverage"]

    async def _notify_position_opened(
        self, position: dict[str, Any], signal: dict[str, Any]
    ) -> None:
        """Send Telegram notification for opened position."""
        try:
            telegram = get_telegram_notifier()
            await telegram.notify_position_opened(
                symbol=position["symbol"],
                direction=position["direction"],
                size_usdt=position.get("position_size_usdt", 0),
                entry_price=position["entry_price"],
                stop_loss=position.get("stop_loss"),
                take_profit=position.get("take_profit"),
                leverage=position.get("leverage", 1),
                confidence=signal.get("confidence", 0),
            )
        except Exception as e:
            logger.error(f"Telegram notification failed: {e}")

    async def _notify_position_closed(self, trade_result: dict[str, Any]) -> None:
        """Send Telegram notification for closed position."""
        try:
            telegram = get_telegram_notifier()

            # Determine if SL or TP hit
            reason = trade_result.get("close_reason", "manual")
            pnl = trade_result.get("pnl", 0)

            if "stop_loss" in reason.lower() or pnl < 0 and reason == "sl":
                await telegram.notify_stop_loss_hit(
                    symbol=trade_result["symbol"],
                    direction=trade_result["direction"],
                    loss_usdt=abs(pnl),
                )
            elif "take_profit" in reason.lower() or reason == "tp":
                await telegram.notify_take_profit_hit(
                    symbol=trade_result["symbol"],
                    direction=trade_result["direction"],
                    profit_usdt=pnl,
                )
            else:
                # Calculate duration
                opened_at = trade_result.get("opened_at", "")
                closed_at = trade_result.get("closed_at", "")
                duration = ""
                if opened_at and closed_at:
                    try:
                        start = datetime.fromisoformat(opened_at)
                        end = datetime.fromisoformat(closed_at)
                        delta = end - start
                        hours, remainder = divmod(int(delta.total_seconds()), 3600)
                        minutes = remainder // 60
                        duration = f"{hours}h {minutes}m" if hours else f"{minutes}m"
                    except Exception:
                        pass

                await telegram.notify_position_closed(
                    symbol=trade_result["symbol"],
                    direction=trade_result["direction"],
                    pnl_usdt=pnl,
                    pnl_pct=trade_result.get("pnl_pct", 0),
                    reason=reason,
                    duration=duration,
                )
        except Exception as e:
            logger.error(f"Telegram notification failed: {e}")
