"""
AILA - Order Manager

Manages order lifecycle and tracking.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional

import structlog

from ..exchange import BybitClient
from ..exchange.models import Order, OrderStatus, OrderType

logger = structlog.get_logger(__name__)


@dataclass
class ManagedOrder:
    """Order with additional management metadata."""

    order: Order
    strategy_name: str
    signal_price: Decimal
    created_at: datetime = field(default_factory=datetime.utcnow)
    last_checked: Optional[datetime] = None
    check_count: int = 0
    notes: str = ""


class OrderManager:
    """
    Order lifecycle management.

    Tracks orders, handles fills, and manages order updates.

    Example:
        manager = OrderManager(client)

        # Submit order
        order = manager.submit_order(
            symbol="BTCUSDT",
            side="buy",
            quantity=Decimal("0.001"),
        )

        # Check status
        status = manager.get_order_status(order.order_id)

        # Cancel order
        manager.cancel_order(order.order_id)
    """

    def __init__(self, client: BybitClient):
        """
        Initialize order manager.

        Args:
            client: Configured Bybit client
        """
        self.client = client
        self._orders: dict[str, ManagedOrder] = {}
        self._filled_orders: list[ManagedOrder] = []

    def submit_order(
        self,
        symbol: str,
        side: str,
        quantity: Decimal,
        order_type: OrderType = OrderType.MARKET,
        price: Optional[Decimal] = None,
        stop_loss: Optional[Decimal] = None,
        take_profit: Optional[Decimal] = None,
        strategy_name: str = "default",
        signal_price: Optional[Decimal] = None,
    ) -> Optional[Order]:
        """
        Submit a new order.

        Args:
            symbol: Trading pair symbol
            side: Order side ('buy' or 'sell')
            quantity: Order quantity
            order_type: Order type
            price: Limit price (for limit orders)
            stop_loss: Stop-loss price
            take_profit: Take-profit price
            strategy_name: Name of strategy that generated the order
            signal_price: Price at signal generation

        Returns:
            Order object or None if failed
        """
        from ..exchange.models import OrderSide

        order_side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL

        try:
            order = self.client.place_order(
                symbol=symbol,
                side=order_side,
                order_type=order_type,
                quantity=quantity,
                price=price,
                stop_loss=stop_loss,
                take_profit=take_profit,
            )

            if order:
                managed = ManagedOrder(
                    order=order,
                    strategy_name=strategy_name,
                    signal_price=signal_price or Decimal(str(order.price or 0)),
                )
                self._orders[order.order_id] = managed

                logger.info(
                    "Order submitted",
                    order_id=order.order_id,
                    symbol=symbol,
                    side=side,
                    quantity=str(quantity),
                )

            return order

        except Exception as e:
            logger.error("Failed to submit order", symbol=symbol, error=str(e))
            return None

    def get_order_status(self, order_id: str) -> Optional[OrderStatus]:
        """
        Get current order status.

        Args:
            order_id: Order ID

        Returns:
            Order status or None if not found
        """
        managed = self._orders.get(order_id)
        if not managed:
            return None

        # Refresh from exchange
        try:
            order = self.client.get_order(managed.order.symbol, order_id)
            if order:
                managed.order = order
                managed.last_checked = datetime.utcnow()
                managed.check_count += 1

                if order.is_filled:
                    self._handle_fill(managed)

                return order.status
        except Exception as e:
            logger.error("Failed to get order status", order_id=order_id, error=str(e))

        return managed.order.status

    def cancel_order(self, order_id: str) -> bool:
        """
        Cancel an order.

        Args:
            order_id: Order ID

        Returns:
            True if cancelled successfully
        """
        managed = self._orders.get(order_id)
        if not managed:
            logger.warning("Order not found for cancellation", order_id=order_id)
            return False

        try:
            success = self.client.cancel_order(managed.order.symbol, order_id)
            if success:
                managed.order.status = OrderStatus.CANCELLED
                del self._orders[order_id]
                logger.info("Order cancelled", order_id=order_id)
            return success
        except Exception as e:
            logger.error("Failed to cancel order", order_id=order_id, error=str(e))
            return False

    def cancel_all_orders(self, symbol: Optional[str] = None) -> int:
        """
        Cancel all active orders.

        Args:
            symbol: Optional symbol filter

        Returns:
            Number of orders cancelled
        """
        count = self.client.cancel_all_orders(symbol)

        # Update local state
        if symbol:
            to_remove = [
                oid for oid, m in self._orders.items()
                if m.order.symbol == symbol
            ]
        else:
            to_remove = list(self._orders.keys())

        for oid in to_remove:
            if oid in self._orders:
                self._orders[oid].order.status = OrderStatus.CANCELLED
                del self._orders[oid]

        return count

    def get_active_orders(self, symbol: Optional[str] = None) -> list[ManagedOrder]:
        """
        Get active orders.

        Args:
            symbol: Optional symbol filter

        Returns:
            List of managed orders
        """
        orders = list(self._orders.values())
        if symbol:
            orders = [o for o in orders if o.order.symbol == symbol]
        return [o for o in orders if o.order.is_active]

    def get_filled_orders(
        self,
        limit: int = 100,
        symbol: Optional[str] = None,
    ) -> list[ManagedOrder]:
        """
        Get recently filled orders.

        Args:
            limit: Maximum number of orders to return
            symbol: Optional symbol filter

        Returns:
            List of filled orders
        """
        orders = self._filled_orders[-limit:]
        if symbol:
            orders = [o for o in orders if o.order.symbol == symbol]
        return orders

    def _handle_fill(self, managed: ManagedOrder) -> None:
        """Handle order fill."""
        if managed.order.order_id in self._orders:
            del self._orders[managed.order.order_id]

        self._filled_orders.append(managed)

        # Calculate slippage
        if managed.signal_price and managed.order.average_price:
            slippage = abs(
                float(managed.order.average_price - managed.signal_price) /
                float(managed.signal_price)
            ) * 100
            managed.notes = f"Slippage: {slippage:.2f}%"

        logger.info(
            "Order filled",
            order_id=managed.order.order_id,
            symbol=managed.order.symbol,
            avg_price=str(managed.order.average_price),
            notes=managed.notes,
        )

    def refresh_all(self) -> None:
        """Refresh status of all tracked orders."""
        for order_id in list(self._orders.keys()):
            self.get_order_status(order_id)

    @property
    def active_count(self) -> int:
        """Get count of active orders."""
        return len([o for o in self._orders.values() if o.order.is_active])

    def __repr__(self) -> str:
        return f"OrderManager(active={self.active_count}, filled={len(self._filled_orders)})"
