import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger("ai_trade")


class PositionManager:
    """Manages open positions and order execution."""

    def __init__(self, exchange):
        self.exchange = exchange
        self.open_positions = {}

    async def open_position(self, signal: dict) -> Optional[dict]:
        """Open a new position based on AI signal."""
        symbol = signal.get("pair")
        direction = signal.get("decision")  # LONG or SHORT
        leverage = signal.get("leverage", 1)
        position_size_usdt = signal.get("position_size_usdt", 0)
        stop_loss = signal.get("stop_loss")
        take_profit = signal.get("take_profit")

        if not symbol or not direction or direction == "WAIT":
            return None

        try:
            # Set leverage
            await self.exchange.set_leverage(leverage, symbol)

            # Calculate amount
            ticker = await self.exchange.fetch_ticker(symbol)
            price = ticker["last"]
            amount = position_size_usdt / price

            # Place market order
            side = "buy" if direction == "LONG" else "sell"
            order = await self.exchange.create_market_order(symbol, side, amount)

            position = {
                "id": order.get("id"),
                "symbol": symbol,
                "direction": direction,
                "entry_price": price,
                "amount": amount,
                "leverage": leverage,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "position_size_usdt": position_size_usdt,
                "strategy": signal.get("strategy"),
                "confidence": signal.get("confidence"),
                "reasoning": signal.get("reasoning"),
                "opened_at": datetime.now().isoformat(),
                "status": "open",
            }

            # Set SL/TP orders
            if stop_loss:
                sl_side = "sell" if direction == "LONG" else "buy"
                await self.exchange.create_order(
                    symbol, "stop_market", sl_side, amount,
                    params={"stopPrice": stop_loss, "reduceOnly": True}
                )

            if take_profit:
                tp_side = "sell" if direction == "LONG" else "buy"
                await self.exchange.create_order(
                    symbol, "take_profit_market", tp_side, amount,
                    params={"stopPrice": take_profit, "reduceOnly": True}
                )

            self.open_positions[symbol] = position
            logger.info(f"Position opened: {direction} {symbol} @ {price}, leverage={leverage}x, size=${position_size_usdt:.2f}")

            return position

        except Exception as e:
            logger.error(f"Error opening position {symbol}: {e}")
            return None

    async def close_position(self, symbol: str, reason: str = "manual") -> Optional[dict]:
        """Close an open position."""
        if symbol not in self.open_positions:
            logger.warning(f"No open position for {symbol}")
            return None

        position = self.open_positions[symbol]

        try:
            # Close with market order
            close_side = "sell" if position["direction"] == "LONG" else "buy"
            order = await self.exchange.create_market_order(
                symbol, close_side, position["amount"],
                params={"reduceOnly": True}
            )

            # Cancel remaining SL/TP orders
            try:
                open_orders = await self.exchange.fetch_open_orders(symbol)
                for o in open_orders:
                    await self.exchange.cancel_order(o["id"], symbol)
            except Exception:
                pass

            # Calculate PnL
            ticker = await self.exchange.fetch_ticker(symbol)
            exit_price = ticker["last"]

            if position["direction"] == "LONG":
                pnl_pct = (exit_price - position["entry_price"]) / position["entry_price"] * 100
            else:
                pnl_pct = (position["entry_price"] - exit_price) / position["entry_price"] * 100

            pnl_pct *= position["leverage"]
            pnl_usdt = position["position_size_usdt"] * (pnl_pct / 100)

            trade_result = {
                **position,
                "exit_price": exit_price,
                "pnl": pnl_usdt,
                "pnl_pct": pnl_pct,
                "close_reason": reason,
                "closed_at": datetime.now().isoformat(),
                "duration": str(datetime.now() - datetime.fromisoformat(position["opened_at"])),
                "status": "closed",
            }

            del self.open_positions[symbol]
            logger.info(f"Position closed: {symbol} PnL={pnl_usdt:.2f} ({pnl_pct:.2f}%), reason={reason}")

            return trade_result

        except Exception as e:
            logger.error(f"Error closing position {symbol}: {e}")
            return None

    async def check_positions(self) -> list:
        """Check status of all open positions."""
        updates = []
        for symbol, position in list(self.open_positions.items()):
            try:
                ticker = await self.exchange.fetch_ticker(symbol)
                current_price = ticker["last"]

                if position["direction"] == "LONG":
                    unrealized_pnl_pct = (current_price - position["entry_price"]) / position["entry_price"] * 100
                else:
                    unrealized_pnl_pct = (position["entry_price"] - current_price) / position["entry_price"] * 100

                unrealized_pnl_pct *= position["leverage"]

                updates.append({
                    "symbol": symbol,
                    "direction": position["direction"],
                    "entry_price": position["entry_price"],
                    "current_price": current_price,
                    "unrealized_pnl_pct": unrealized_pnl_pct,
                    "leverage": position["leverage"],
                })

            except Exception as e:
                logger.error(f"Error checking position {symbol}: {e}")

        return updates

    def get_open_count(self) -> int:
        """Get number of open positions."""
        return len(self.open_positions)
