"""Position manager - handles order execution and tracking."""

import logging
from datetime import datetime
from typing import Any, Optional

from ..utils.common import retry_async
from ..utils.position_conflict import check_position_conflict

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
        if not symbol or not direction or direction == "WAIT":
            return None

        # Check for position conflict (any existing position on this symbol)
        if check_position_conflict(symbol):
            logger.warning(f"Position conflict: {symbol} already has open position, skipping signal")
            return None

        try:
            leverage = signal.get("leverage", 1)
            position_size_usdt = signal.get("position_size_usdt", 0)

            await self._exchange.set_leverage(leverage, symbol)

            ticker = await self._exchange.fetch_ticker(symbol)
            price = ticker["last"]
            amount = position_size_usdt / price

            side = "buy" if direction == "LONG" else "sell"
            order = await self._exchange.create_market_order(symbol, side, amount)

            position = self._build_position(signal, order, price, amount)
            await self._set_sl_tp(symbol, signal, direction, amount)

            self._open_positions[symbol] = position
            logger.info(f"Position opened: {direction} {symbol} @ {price}, lev={leverage}x")
            return position

        except Exception as e:
            logger.error(f"Error opening position {symbol}: {e}")
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
            return trade_result

        except Exception as e:
            logger.error(f"Error closing position {symbol}: {e}")
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

        if stop_loss:
            await self._exchange.create_order(
                symbol, "stop_market", sl_side, amount,
                params={"stopPrice": stop_loss, "reduceOnly": True},
            )
        if take_profit:
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
