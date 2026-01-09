"""
AILA - Exchange Data Models

Data models for orders, positions, balances, and other exchange entities.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional


class OrderType(Enum):
    """Order types supported by the exchange."""

    MARKET = "Market"
    LIMIT = "Limit"
    STOP_LOSS = "StopLoss"
    TAKE_PROFIT = "TakeProfit"
    STOP_LIMIT = "StopLimit"
    TRAILING_STOP = "TrailingStop"


class OrderSide(Enum):
    """Order side (buy/sell)."""

    BUY = "Buy"
    SELL = "Sell"


class OrderStatus(Enum):
    """Order status."""

    NEW = "New"
    PARTIALLY_FILLED = "PartiallyFilled"
    FILLED = "Filled"
    CANCELLED = "Cancelled"
    REJECTED = "Rejected"
    PENDING = "Pending"
    EXPIRED = "Expired"


class PositionSide(Enum):
    """Position side for futures."""

    LONG = "Buy"
    SHORT = "Sell"
    NONE = "None"


class AccountType(Enum):
    """Account type."""

    SPOT = "spot"
    FUTURES = "futures"
    UNIFIED = "unified"


class MarginMode(Enum):
    """Margin mode for futures."""

    CROSS = "cross"
    ISOLATED = "isolated"


@dataclass
class Order:
    """
    Order data model.

    Represents a trading order on the exchange.
    """

    order_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: Decimal
    price: Optional[Decimal] = None
    status: OrderStatus = OrderStatus.NEW
    filled_quantity: Decimal = Decimal("0")
    average_price: Optional[Decimal] = None
    stop_loss: Optional[Decimal] = None
    take_profit: Optional[Decimal] = None
    reduce_only: bool = False
    close_on_trigger: bool = False
    time_in_force: str = "GTC"
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: Optional[datetime] = None
    exchange_order_id: Optional[str] = None
    client_order_id: Optional[str] = None

    @property
    def is_filled(self) -> bool:
        """Check if order is completely filled."""
        return self.status == OrderStatus.FILLED

    @property
    def is_active(self) -> bool:
        """Check if order is still active."""
        return self.status in (OrderStatus.NEW, OrderStatus.PARTIALLY_FILLED, OrderStatus.PENDING)

    @property
    def is_buy(self) -> bool:
        """Check if order is a buy order."""
        return self.side == OrderSide.BUY

    @property
    def remaining_quantity(self) -> Decimal:
        """Get remaining unfilled quantity."""
        return self.quantity - self.filled_quantity

    def to_dict(self) -> dict:
        """Convert order to dictionary."""
        return {
            "order_id": self.order_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "order_type": self.order_type.value,
            "quantity": str(self.quantity),
            "price": str(self.price) if self.price else None,
            "status": self.status.value,
            "filled_quantity": str(self.filled_quantity),
            "average_price": str(self.average_price) if self.average_price else None,
            "stop_loss": str(self.stop_loss) if self.stop_loss else None,
            "take_profit": str(self.take_profit) if self.take_profit else None,
            "reduce_only": self.reduce_only,
            "time_in_force": self.time_in_force,
            "created_at": self.created_at.isoformat(),
            "exchange_order_id": self.exchange_order_id,
        }


@dataclass
class Position:
    """
    Position data model for futures trading.

    Represents an open position on the exchange.
    """

    symbol: str
    side: PositionSide
    size: Decimal
    entry_price: Decimal
    leverage: int = 1
    margin_mode: MarginMode = MarginMode.CROSS
    unrealized_pnl: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    liquidation_price: Optional[Decimal] = None
    stop_loss: Optional[Decimal] = None
    take_profit: Optional[Decimal] = None
    trailing_stop: Optional[Decimal] = None
    position_margin: Decimal = Decimal("0")
    mark_price: Optional[Decimal] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: Optional[datetime] = None

    @property
    def is_long(self) -> bool:
        """Check if position is long."""
        return self.side == PositionSide.LONG

    @property
    def is_short(self) -> bool:
        """Check if position is short."""
        return self.side == PositionSide.SHORT

    @property
    def notional_value(self) -> Decimal:
        """Calculate notional value of position."""
        return self.size * self.entry_price

    @property
    def pnl_percent(self) -> float:
        """Calculate unrealized PnL percentage."""
        if self.entry_price == 0:
            return 0.0
        return float(self.unrealized_pnl / (self.size * self.entry_price)) * 100

    def to_dict(self) -> dict:
        """Convert position to dictionary."""
        return {
            "symbol": self.symbol,
            "side": self.side.value,
            "size": str(self.size),
            "entry_price": str(self.entry_price),
            "leverage": self.leverage,
            "margin_mode": self.margin_mode.value,
            "unrealized_pnl": str(self.unrealized_pnl),
            "realized_pnl": str(self.realized_pnl),
            "liquidation_price": str(self.liquidation_price) if self.liquidation_price else None,
            "stop_loss": str(self.stop_loss) if self.stop_loss else None,
            "take_profit": str(self.take_profit) if self.take_profit else None,
            "mark_price": str(self.mark_price) if self.mark_price else None,
        }


@dataclass
class Balance:
    """
    Account balance data model.

    Represents balance for a single asset.
    """

    asset: str
    total: Decimal
    available: Decimal
    locked: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")
    equity: Optional[Decimal] = None
    account_type: AccountType = AccountType.SPOT
    updated_at: datetime = field(default_factory=datetime.utcnow)

    @property
    def used(self) -> Decimal:
        """Get used balance (total - available)."""
        return self.total - self.available

    def to_dict(self) -> dict:
        """Convert balance to dictionary."""
        return {
            "asset": self.asset,
            "total": str(self.total),
            "available": str(self.available),
            "locked": str(self.locked),
            "unrealized_pnl": str(self.unrealized_pnl),
            "equity": str(self.equity) if self.equity else None,
            "account_type": self.account_type.value,
        }


@dataclass
class Ticker:
    """
    Ticker data model.

    Represents current market price information.
    """

    symbol: str
    last_price: Decimal
    bid_price: Decimal
    ask_price: Decimal
    high_24h: Decimal
    low_24h: Decimal
    volume_24h: Decimal
    turnover_24h: Decimal
    change_24h: float
    timestamp: datetime = field(default_factory=datetime.utcnow)

    @property
    def spread(self) -> Decimal:
        """Calculate bid-ask spread."""
        return self.ask_price - self.bid_price

    @property
    def spread_percent(self) -> float:
        """Calculate spread as percentage."""
        if self.last_price == 0:
            return 0.0
        return float(self.spread / self.last_price) * 100


@dataclass
class Candle:
    """
    Candlestick data model.

    Represents OHLCV data for a single candle.
    """

    symbol: str
    timeframe: str
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    turnover: Optional[Decimal] = None
    is_closed: bool = True

    def to_dict(self) -> dict:
        """Convert candle to dictionary."""
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "timestamp": self.timestamp.isoformat(),
            "open": str(self.open),
            "high": str(self.high),
            "low": str(self.low),
            "close": str(self.close),
            "volume": str(self.volume),
            "is_closed": self.is_closed,
        }


@dataclass
class TradingPair:
    """
    Trading pair information.

    Contains trading rules and limits for a symbol.
    """

    symbol: str
    base_asset: str
    quote_asset: str
    status: str = "Trading"
    min_order_qty: Decimal = Decimal("0.001")
    max_order_qty: Decimal = Decimal("1000000")
    qty_step: Decimal = Decimal("0.001")
    min_order_value: Decimal = Decimal("1")
    tick_size: Decimal = Decimal("0.01")
    max_leverage: int = 100
    is_spot: bool = True
    is_futures: bool = True

    def round_quantity(self, quantity: Decimal) -> Decimal:
        """Round quantity to valid step size."""
        return (quantity // self.qty_step) * self.qty_step

    def round_price(self, price: Decimal) -> Decimal:
        """Round price to valid tick size."""
        return (price // self.tick_size) * self.tick_size

    def validate_order(self, quantity: Decimal, price: Optional[Decimal] = None) -> tuple[bool, str]:
        """
        Validate order parameters.

        Returns:
            Tuple of (is_valid, error_message)
        """
        if quantity < self.min_order_qty:
            return False, f"Quantity {quantity} below minimum {self.min_order_qty}"

        if quantity > self.max_order_qty:
            return False, f"Quantity {quantity} above maximum {self.max_order_qty}"

        if price is not None:
            order_value = quantity * price
            if order_value < self.min_order_value:
                return False, f"Order value {order_value} below minimum {self.min_order_value}"

        return True, ""
