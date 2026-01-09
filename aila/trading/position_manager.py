"""
AILA - Position Manager

Tracks and manages open trading positions.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional

import structlog

from ..exchange import BybitClient
from ..exchange.models import Position, PositionSide

logger = structlog.get_logger(__name__)


@dataclass
class ManagedPosition:
    """Position with additional management metadata."""

    position: Position
    strategy_name: str
    entry_signal_price: Decimal
    entry_time: datetime = field(default_factory=datetime.utcnow)
    initial_stop_loss: Optional[Decimal] = None
    initial_take_profit: Optional[Decimal] = None
    trailing_activated: bool = False
    partial_closes: list = field(default_factory=list)
    highest_price: Optional[Decimal] = None
    lowest_price: Optional[Decimal] = None
    max_unrealized_pnl: Decimal = Decimal("0")


class PositionManager:
    """
    Position tracking and management.

    Tracks all open positions with their metadata and history.

    Example:
        manager = PositionManager(client)

        # Track new position
        manager.track_position(
            symbol="BTCUSDT",
            strategy_name="TripleSuperTrend",
            entry_price=Decimal("50000"),
        )

        # Update positions
        manager.refresh_all()

        # Get position stats
        stats = manager.get_position_stats("BTCUSDT")
    """

    def __init__(self, client: BybitClient):
        """
        Initialize position manager.

        Args:
            client: Configured Bybit client
        """
        self.client = client
        self._positions: dict[str, ManagedPosition] = {}

    def track_position(
        self,
        symbol: str,
        strategy_name: str,
        entry_price: Decimal,
        stop_loss: Optional[Decimal] = None,
        take_profit: Optional[Decimal] = None,
    ) -> Optional[ManagedPosition]:
        """
        Start tracking a position.

        Args:
            symbol: Trading pair symbol
            strategy_name: Name of strategy that opened the position
            entry_price: Entry price
            stop_loss: Initial stop-loss price
            take_profit: Initial take-profit price

        Returns:
            Managed position or None if position not found
        """
        positions = self.client.get_positions(symbol)
        if not positions:
            logger.warning("No position found to track", symbol=symbol)
            return None

        position = positions[0]
        managed = ManagedPosition(
            position=position,
            strategy_name=strategy_name,
            entry_signal_price=entry_price,
            initial_stop_loss=stop_loss,
            initial_take_profit=take_profit,
            highest_price=Decimal(str(position.mark_price or entry_price)),
            lowest_price=Decimal(str(position.mark_price or entry_price)),
        )

        self._positions[symbol] = managed
        logger.info(
            "Position tracked",
            symbol=symbol,
            side=position.side.value,
            size=str(position.size),
            strategy=strategy_name,
        )

        return managed

    def get_position(self, symbol: str) -> Optional[ManagedPosition]:
        """
        Get managed position.

        Args:
            symbol: Trading pair symbol

        Returns:
            Managed position or None if not found
        """
        return self._positions.get(symbol)

    def update_position(self, symbol: str) -> Optional[ManagedPosition]:
        """
        Update position from exchange.

        Args:
            symbol: Trading pair symbol

        Returns:
            Updated managed position or None
        """
        managed = self._positions.get(symbol)
        if not managed:
            return None

        positions = self.client.get_positions(symbol)
        if not positions:
            # Position closed
            self._handle_close(managed)
            return None

        position = positions[0]
        managed.position = position

        # Update high/low tracking
        if position.mark_price:
            mark = Decimal(str(position.mark_price))
            if managed.highest_price is None or mark > managed.highest_price:
                managed.highest_price = mark
            if managed.lowest_price is None or mark < managed.lowest_price:
                managed.lowest_price = mark

        # Track max unrealized PnL
        if position.unrealized_pnl > managed.max_unrealized_pnl:
            managed.max_unrealized_pnl = position.unrealized_pnl

        return managed

    def record_partial_close(
        self,
        symbol: str,
        quantity: Decimal,
        price: Decimal,
        reason: str = "partial_tp",
    ) -> bool:
        """
        Record a partial position close.

        Args:
            symbol: Trading pair symbol
            quantity: Quantity closed
            price: Close price
            reason: Reason for close

        Returns:
            True if recorded successfully
        """
        managed = self._positions.get(symbol)
        if not managed:
            return False

        managed.partial_closes.append({
            "quantity": float(quantity),
            "price": float(price),
            "reason": reason,
            "time": datetime.utcnow().isoformat(),
        })

        logger.info(
            "Partial close recorded",
            symbol=symbol,
            quantity=str(quantity),
            price=str(price),
            reason=reason,
        )

        return True

    def mark_trailing_activated(self, symbol: str) -> bool:
        """
        Mark that trailing stop is activated.

        Args:
            symbol: Trading pair symbol

        Returns:
            True if marked successfully
        """
        managed = self._positions.get(symbol)
        if not managed:
            return False

        managed.trailing_activated = True
        logger.info("Trailing stop activated", symbol=symbol)
        return True

    def refresh_all(self) -> None:
        """Refresh all tracked positions."""
        for symbol in list(self._positions.keys()):
            self.update_position(symbol)

    def get_all_positions(self) -> list[ManagedPosition]:
        """Get all tracked positions."""
        return list(self._positions.values())

    def get_position_stats(self, symbol: str) -> Optional[dict]:
        """
        Get position statistics.

        Args:
            symbol: Trading pair symbol

        Returns:
            Position statistics dictionary
        """
        managed = self._positions.get(symbol)
        if not managed:
            return None

        position = managed.position
        entry_price = float(managed.entry_signal_price)
        current_price = float(position.mark_price or entry_price)

        if position.side == PositionSide.LONG:
            price_change_pct = ((current_price - entry_price) / entry_price) * 100
        else:
            price_change_pct = ((entry_price - current_price) / entry_price) * 100

        return {
            "symbol": symbol,
            "side": position.side.value,
            "size": str(position.size),
            "entry_price": str(managed.entry_signal_price),
            "current_price": str(position.mark_price),
            "unrealized_pnl": str(position.unrealized_pnl),
            "unrealized_pnl_pct": f"{price_change_pct:.2f}%",
            "max_unrealized_pnl": str(managed.max_unrealized_pnl),
            "highest_price": str(managed.highest_price),
            "lowest_price": str(managed.lowest_price),
            "trailing_activated": managed.trailing_activated,
            "partial_closes": len(managed.partial_closes),
            "entry_time": managed.entry_time.isoformat(),
            "duration": str(datetime.utcnow() - managed.entry_time),
            "strategy": managed.strategy_name,
        }

    def get_total_unrealized_pnl(self) -> Decimal:
        """Get total unrealized PnL across all positions."""
        return sum(
            m.position.unrealized_pnl for m in self._positions.values()
        )

    def _handle_close(self, managed: ManagedPosition) -> None:
        """Handle position close."""
        symbol = managed.position.symbol
        if symbol in self._positions:
            del self._positions[symbol]

        logger.info(
            "Position closed",
            symbol=symbol,
            duration=str(datetime.utcnow() - managed.entry_time),
            max_pnl=str(managed.max_unrealized_pnl),
        )

    def stop_tracking(self, symbol: str) -> bool:
        """
        Stop tracking a position.

        Args:
            symbol: Trading pair symbol

        Returns:
            True if stopped successfully
        """
        if symbol in self._positions:
            del self._positions[symbol]
            return True
        return False

    @property
    def position_count(self) -> int:
        """Get count of tracked positions."""
        return len(self._positions)

    def __repr__(self) -> str:
        return f"PositionManager(positions={self.position_count})"
