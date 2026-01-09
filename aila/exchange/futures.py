"""
AILA - Futures Trading Module

Specialized trading operations for Bybit futures market.
"""

from decimal import Decimal
from typing import Optional

import structlog

from .bybit_client import BybitClient
from .models import (
    MarginMode,
    Order,
    OrderSide,
    OrderType,
    Position,
    PositionSide,
    TradingPair,
)

logger = structlog.get_logger(__name__)


class FuturesTrader:
    """
    Futures trading operations for Bybit.

    Provides high-level methods for futures trading:
    - Long/Short position entry
    - Position management with SL/TP
    - Leverage control
    - Trailing stop management

    Example:
        client = BybitClient(config)
        trader = FuturesTrader(client)

        # Open long position
        order = trader.open_long(
            "BTCUSDT",
            usdt_amount=Decimal("100"),
            stop_loss=Decimal("40000"),
            take_profit=Decimal("45000"),
        )

        # Close position
        trader.close_position("BTCUSDT")
    """

    def __init__(
        self,
        client: BybitClient,
        default_leverage: int = 10,
    ):
        """
        Initialize futures trader.

        Args:
            client: Configured BybitClient instance
            default_leverage: Default leverage to use
        """
        self.client = client
        self.default_leverage = default_leverage
        self._leverage_cache: dict[str, int] = {}

    def open_long(
        self,
        symbol: str,
        quantity: Optional[Decimal] = None,
        usdt_amount: Optional[Decimal] = None,
        leverage: Optional[int] = None,
        stop_loss: Optional[Decimal] = None,
        take_profit: Optional[Decimal] = None,
        reduce_only: bool = False,
    ) -> Optional[Order]:
        """
        Open a long position.

        Args:
            symbol: Trading pair symbol
            quantity: Position size in base asset
            usdt_amount: Position size in USDT (used with leverage)
            leverage: Leverage to use (uses default if not specified)
            stop_loss: Stop-loss price
            take_profit: Take-profit price
            reduce_only: Whether this order should only reduce position

        Returns:
            Order object or None if failed
        """
        return self._open_position(
            symbol=symbol,
            side=PositionSide.LONG,
            quantity=quantity,
            usdt_amount=usdt_amount,
            leverage=leverage,
            stop_loss=stop_loss,
            take_profit=take_profit,
            reduce_only=reduce_only,
        )

    def open_short(
        self,
        symbol: str,
        quantity: Optional[Decimal] = None,
        usdt_amount: Optional[Decimal] = None,
        leverage: Optional[int] = None,
        stop_loss: Optional[Decimal] = None,
        take_profit: Optional[Decimal] = None,
        reduce_only: bool = False,
    ) -> Optional[Order]:
        """
        Open a short position.

        Args:
            symbol: Trading pair symbol
            quantity: Position size in base asset
            usdt_amount: Position size in USDT (used with leverage)
            leverage: Leverage to use (uses default if not specified)
            stop_loss: Stop-loss price
            take_profit: Take-profit price
            reduce_only: Whether this order should only reduce position

        Returns:
            Order object or None if failed
        """
        return self._open_position(
            symbol=symbol,
            side=PositionSide.SHORT,
            quantity=quantity,
            usdt_amount=usdt_amount,
            leverage=leverage,
            stop_loss=stop_loss,
            take_profit=take_profit,
            reduce_only=reduce_only,
        )

    def _open_position(
        self,
        symbol: str,
        side: PositionSide,
        quantity: Optional[Decimal] = None,
        usdt_amount: Optional[Decimal] = None,
        leverage: Optional[int] = None,
        stop_loss: Optional[Decimal] = None,
        take_profit: Optional[Decimal] = None,
        reduce_only: bool = False,
    ) -> Optional[Order]:
        """Internal method to open a position."""
        pair = self.client.get_trading_pair(symbol)
        if pair is None:
            logger.error("Trading pair not found", symbol=symbol)
            return None

        # Set leverage if needed
        lev = leverage or self.default_leverage
        if symbol not in self._leverage_cache or self._leverage_cache[symbol] != lev:
            if self.set_leverage(symbol, lev):
                self._leverage_cache[symbol] = lev

        # Calculate quantity from USDT amount
        if quantity is None and usdt_amount is not None:
            ticker = self.client.get_ticker(symbol)
            if ticker is None:
                logger.error("Could not get ticker", symbol=symbol)
                return None

            # USDT amount * leverage / price = quantity
            position_value = usdt_amount * Decimal(str(lev))
            quantity = position_value / ticker.last_price
            quantity = pair.round_quantity(quantity)

        if quantity is None:
            logger.error("No quantity specified")
            return None

        # Validate order
        is_valid, error = pair.validate_order(quantity)
        if not is_valid:
            logger.error("Invalid order", error=error)
            return None

        order_side = OrderSide.BUY if side == PositionSide.LONG else OrderSide.SELL

        try:
            order = self.client.place_order(
                symbol=symbol,
                side=order_side,
                order_type=OrderType.MARKET,
                quantity=quantity,
                stop_loss=stop_loss,
                take_profit=take_profit,
                reduce_only=reduce_only,
            )

            logger.info(
                "Position opened",
                symbol=symbol,
                side=side.value,
                quantity=str(quantity),
                leverage=lev,
            )

            return order

        except Exception as e:
            logger.error("Failed to open position", symbol=symbol, error=str(e))
            return None

    def close_position(
        self,
        symbol: str,
        side: Optional[PositionSide] = None,
        percent: float = 100,
    ) -> Optional[Order]:
        """
        Close a position partially or fully.

        Args:
            symbol: Trading pair symbol
            side: Position side to close (auto-detects if not specified)
            percent: Percentage of position to close (0-100)

        Returns:
            Order object or None if failed
        """
        positions = self.client.get_positions(symbol)

        if not positions:
            logger.warning("No position to close", symbol=symbol)
            return None

        # Find the position to close
        position = None
        if side:
            position = next((p for p in positions if p.side == side), None)
        else:
            position = positions[0]  # Take first position

        if position is None:
            logger.warning("Position not found", symbol=symbol, side=side)
            return None

        close_qty = position.size * Decimal(str(percent / 100))
        pair = self.client.get_trading_pair(symbol)
        if pair:
            close_qty = pair.round_quantity(close_qty)

        return self.client.close_position(symbol, position.side, close_qty)

    def close_all_positions(self) -> int:
        """
        Close all open positions.

        Returns:
            Number of positions closed
        """
        positions = self.client.get_positions()
        closed = 0

        for position in positions:
            try:
                order = self.client.close_position(position.symbol, position.side)
                if order:
                    closed += 1
            except Exception as e:
                logger.error(
                    "Failed to close position",
                    symbol=position.symbol,
                    error=str(e),
                )

        return closed

    def set_leverage(self, symbol: str, leverage: int) -> bool:
        """
        Set leverage for a symbol.

        Args:
            symbol: Trading pair symbol
            leverage: Leverage value

        Returns:
            True if successful
        """
        return self.client.set_leverage(symbol, leverage)

    def get_position(self, symbol: str) -> Optional[Position]:
        """
        Get current position for a symbol.

        Args:
            symbol: Trading pair symbol

        Returns:
            Position object or None if no position
        """
        positions = self.client.get_positions(symbol)
        return positions[0] if positions else None

    def get_all_positions(self) -> list[Position]:
        """
        Get all open positions.

        Returns:
            List of Position objects
        """
        return self.client.get_positions()

    def update_stop_loss(self, symbol: str, stop_loss: Decimal) -> bool:
        """
        Update stop-loss for an open position.

        Args:
            symbol: Trading pair symbol
            stop_loss: New stop-loss price

        Returns:
            True if successful
        """
        try:
            response = self.client.http.set_trading_stop(
                category="linear",
                symbol=symbol,
                stopLoss=str(stop_loss),
            )
            self.client._handle_response(response, "update_stop_loss")
            logger.info("Stop-loss updated", symbol=symbol, stop_loss=str(stop_loss))
            return True
        except Exception as e:
            logger.error("Failed to update stop-loss", symbol=symbol, error=str(e))
            return False

    def update_take_profit(self, symbol: str, take_profit: Decimal) -> bool:
        """
        Update take-profit for an open position.

        Args:
            symbol: Trading pair symbol
            take_profit: New take-profit price

        Returns:
            True if successful
        """
        try:
            response = self.client.http.set_trading_stop(
                category="linear",
                symbol=symbol,
                takeProfit=str(take_profit),
            )
            self.client._handle_response(response, "update_take_profit")
            logger.info("Take-profit updated", symbol=symbol, take_profit=str(take_profit))
            return True
        except Exception as e:
            logger.error("Failed to update take-profit", symbol=symbol, error=str(e))
            return False

    def set_trailing_stop(
        self,
        symbol: str,
        trailing_distance: Decimal,
        activation_price: Optional[Decimal] = None,
    ) -> bool:
        """
        Set trailing stop for an open position.

        Args:
            symbol: Trading pair symbol
            trailing_distance: Trailing distance in price
            activation_price: Price at which trailing starts

        Returns:
            True if successful
        """
        try:
            params = {
                "category": "linear",
                "symbol": symbol,
                "trailingStop": str(trailing_distance),
            }
            if activation_price:
                params["activePrice"] = str(activation_price)

            response = self.client.http.set_trading_stop(**params)
            self.client._handle_response(response, "set_trailing_stop")
            logger.info(
                "Trailing stop set",
                symbol=symbol,
                distance=str(trailing_distance),
            )
            return True
        except Exception as e:
            logger.error("Failed to set trailing stop", symbol=symbol, error=str(e))
            return False

    def calculate_position_size(
        self,
        symbol: str,
        risk_percent: float,
        stop_loss_price: Decimal,
        entry_price: Optional[Decimal] = None,
        leverage: Optional[int] = None,
    ) -> Decimal:
        """
        Calculate position size based on risk management.

        Args:
            symbol: Trading pair symbol
            risk_percent: Percentage of account to risk
            stop_loss_price: Stop-loss price
            entry_price: Entry price (uses current price if not specified)
            leverage: Leverage to use

        Returns:
            Position size in base asset
        """
        # Get current price if entry not specified
        if entry_price is None:
            ticker = self.client.get_ticker(symbol)
            if ticker is None:
                return Decimal("0")
            entry_price = ticker.last_price

        # Get balance
        usdt_balance = self.client.get_balance("USDT")
        risk_amount = usdt_balance.available * Decimal(str(risk_percent / 100))

        # Calculate stop distance
        stop_distance = abs(entry_price - stop_loss_price) / entry_price

        if stop_distance == 0:
            return Decimal("0")

        # Position value = risk / stop_distance
        position_value = risk_amount / stop_distance

        # Apply leverage
        lev = leverage or self.default_leverage
        position_value = position_value * Decimal(str(lev))

        # Convert to quantity
        quantity = position_value / entry_price

        # Round to valid step
        pair = self.client.get_trading_pair(symbol)
        if pair:
            quantity = pair.round_quantity(quantity)

        return quantity

    def get_unrealized_pnl(self, symbol: Optional[str] = None) -> Decimal:
        """
        Get total unrealized PnL.

        Args:
            symbol: Optional symbol filter

        Returns:
            Total unrealized PnL in USDT
        """
        positions = self.client.get_positions(symbol)
        return sum(p.unrealized_pnl for p in positions)

    def get_total_position_value(self) -> Decimal:
        """
        Get total notional value of all positions.

        Returns:
            Total position value in USDT
        """
        positions = self.client.get_positions()
        return sum(p.notional_value for p in positions)

    def __repr__(self) -> str:
        return f"FuturesTrader(client={self.client}, leverage={self.default_leverage})"
