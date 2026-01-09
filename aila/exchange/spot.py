"""
AILA - Spot Trading Module

Specialized trading operations for Bybit spot market.
"""

from decimal import Decimal
from typing import Optional

import structlog

from .bybit_client import BybitClient, BybitConfig
from .models import Balance, Order, OrderSide, OrderType, TradingPair

logger = structlog.get_logger(__name__)


class SpotTrader:
    """
    Spot trading operations for Bybit.

    Provides high-level methods for spot trading:
    - Buy/Sell operations
    - Balance management
    - Position tracking (simulated via balance changes)

    Example:
        client = BybitClient(config)
        trader = SpotTrader(client)

        # Buy with USDT
        order = trader.buy("BTCUSDT", usdt_amount=Decimal("100"))

        # Sell entire holding
        order = trader.sell_all("BTCUSDT")
    """

    def __init__(self, client: BybitClient):
        """
        Initialize spot trader.

        Args:
            client: Configured BybitClient instance
        """
        self.client = client
        self._positions: dict[str, Decimal] = {}

    def buy(
        self,
        symbol: str,
        quantity: Optional[Decimal] = None,
        usdt_amount: Optional[Decimal] = None,
        price: Optional[Decimal] = None,
    ) -> Optional[Order]:
        """
        Buy asset on spot market.

        Either quantity or usdt_amount must be provided.

        Args:
            symbol: Trading pair symbol
            quantity: Amount of base asset to buy
            usdt_amount: Amount of USDT to spend
            price: Limit price (market order if not provided)

        Returns:
            Order object or None if failed
        """
        pair = self.client.get_trading_pair(symbol)
        if pair is None:
            logger.error("Trading pair not found", symbol=symbol)
            return None

        # Calculate quantity from USDT amount if needed
        if quantity is None and usdt_amount is not None:
            if price is not None:
                quantity = usdt_amount / price
            else:
                # Get current price for market order
                ticker = self.client.get_ticker(symbol)
                if ticker is None:
                    logger.error("Could not get ticker", symbol=symbol)
                    return None
                quantity = usdt_amount / ticker.ask_price

            quantity = pair.round_quantity(quantity)

        if quantity is None:
            logger.error("No quantity specified")
            return None

        # Validate order
        is_valid, error = pair.validate_order(quantity, price)
        if not is_valid:
            logger.error("Invalid order", error=error)
            return None

        order_type = OrderType.LIMIT if price else OrderType.MARKET

        try:
            order = self.client.place_order(
                symbol=symbol,
                side=OrderSide.BUY,
                order_type=order_type,
                quantity=quantity,
                price=price,
            )

            if order and order.is_filled:
                self._update_position(symbol, quantity)

            return order

        except Exception as e:
            logger.error("Buy order failed", symbol=symbol, error=str(e))
            return None

    def sell(
        self,
        symbol: str,
        quantity: Optional[Decimal] = None,
        percent: Optional[float] = None,
        price: Optional[Decimal] = None,
    ) -> Optional[Order]:
        """
        Sell asset on spot market.

        Args:
            symbol: Trading pair symbol
            quantity: Amount to sell
            percent: Percentage of holding to sell (0-100)
            price: Limit price (market order if not provided)

        Returns:
            Order object or None if failed
        """
        pair = self.client.get_trading_pair(symbol)
        if pair is None:
            logger.error("Trading pair not found", symbol=symbol)
            return None

        # Calculate quantity from percent if needed
        if quantity is None and percent is not None:
            base_asset = pair.base_asset
            balance = self.client.get_balance(base_asset)
            quantity = balance.available * Decimal(str(percent / 100))
            quantity = pair.round_quantity(quantity)

        if quantity is None:
            logger.error("No quantity specified")
            return None

        # Validate order
        is_valid, error = pair.validate_order(quantity, price)
        if not is_valid:
            logger.error("Invalid order", error=error)
            return None

        order_type = OrderType.LIMIT if price else OrderType.MARKET

        try:
            order = self.client.place_order(
                symbol=symbol,
                side=OrderSide.SELL,
                order_type=order_type,
                quantity=quantity,
                price=price,
            )

            if order and order.is_filled:
                self._update_position(symbol, -quantity)

            return order

        except Exception as e:
            logger.error("Sell order failed", symbol=symbol, error=str(e))
            return None

    def sell_all(self, symbol: str, price: Optional[Decimal] = None) -> Optional[Order]:
        """
        Sell entire holding of an asset.

        Args:
            symbol: Trading pair symbol
            price: Limit price (market order if not provided)

        Returns:
            Order object or None if failed
        """
        return self.sell(symbol, percent=100, price=price)

    def get_holding(self, symbol: str) -> Decimal:
        """
        Get current holding for a symbol.

        Args:
            symbol: Trading pair symbol

        Returns:
            Amount of base asset held
        """
        pair = self.client.get_trading_pair(symbol)
        if pair is None:
            return Decimal("0")

        balance = self.client.get_balance(pair.base_asset)
        return balance.available

    def get_holding_value(self, symbol: str) -> Decimal:
        """
        Get current holding value in USDT.

        Args:
            symbol: Trading pair symbol

        Returns:
            Value in USDT
        """
        holding = self.get_holding(symbol)
        if holding <= 0:
            return Decimal("0")

        ticker = self.client.get_ticker(symbol)
        if ticker is None:
            return Decimal("0")

        return holding * ticker.last_price

    def get_all_holdings(self) -> dict[str, dict]:
        """
        Get all non-zero holdings with values.

        Returns:
            Dictionary of symbol -> {quantity, value}
        """
        balances = self.client.get_all_balances()
        holdings = {}

        for balance in balances:
            if balance.asset == "USDT":
                holdings["USDT"] = {
                    "quantity": balance.available,
                    "value": balance.available,
                }
            else:
                symbol = f"{balance.asset}USDT"
                ticker = self.client.get_ticker(symbol)
                value = balance.available * ticker.last_price if ticker else Decimal("0")

                holdings[balance.asset] = {
                    "quantity": balance.available,
                    "value": value,
                    "symbol": symbol,
                }

        return holdings

    def calculate_position_size(
        self,
        symbol: str,
        risk_percent: float,
        stop_loss_percent: float,
    ) -> Decimal:
        """
        Calculate position size based on risk management.

        Args:
            symbol: Trading pair symbol
            risk_percent: Percentage of account to risk
            stop_loss_percent: Stop-loss percentage from entry

        Returns:
            Position size in base asset
        """
        usdt_balance = self.client.get_balance("USDT")
        risk_amount = usdt_balance.available * Decimal(str(risk_percent / 100))
        position_value = risk_amount / Decimal(str(stop_loss_percent / 100))

        ticker = self.client.get_ticker(symbol)
        if ticker is None:
            return Decimal("0")

        quantity = position_value / ticker.last_price

        pair = self.client.get_trading_pair(symbol)
        if pair:
            quantity = pair.round_quantity(quantity)

        return quantity

    def _update_position(self, symbol: str, delta: Decimal) -> None:
        """Update internal position tracking."""
        current = self._positions.get(symbol, Decimal("0"))
        self._positions[symbol] = current + delta

    def __repr__(self) -> str:
        return f"SpotTrader(client={self.client})"
