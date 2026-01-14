"""
AILA - Position Sizing Module

Calculates optimal position sizes based on risk parameters.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class PositionSizingConfig:
    """Configuration for position sizing."""

    mode: str = "risk_percent"  # risk_percent | fixed_amount | kelly

    # Risk percent mode
    risk_per_trade: float = 2.0  # % of balance to risk per trade
    max_position_percent: float = 20.0  # Max % of balance in one position
    max_open_positions: int = 3

    # Fixed amount mode
    fixed_amount: Decimal = Decimal("100")  # Fixed USDT per trade

    # Kelly criterion mode
    kelly_fraction: float = 0.25  # Fraction of Kelly optimal

    # Safety limits
    min_position_size: Decimal = Decimal("10")  # Minimum position in USDT
    max_position_size: Decimal = Decimal("10000")  # Maximum position in USDT


@dataclass
class PositionSizeResult:
    """Result of position size calculation."""

    quantity: Decimal
    position_value: Decimal
    risk_amount: Decimal
    risk_percent: float
    leverage_used: int = 1


class PositionSizer:
    """
    Position sizing calculator.

    Supports multiple sizing methods:
    - risk_percent: Size based on % of balance at risk
    - fixed_amount: Fixed USDT amount per trade
    - kelly: Kelly criterion with configurable fraction

    Example:
        config = PositionSizingConfig(
            mode="risk_percent",
            risk_per_trade=2.0,
            max_position_percent=20.0,
        )
        sizer = PositionSizer(config)

        result = sizer.calculate(
            balance=Decimal("10000"),
            entry_price=Decimal("50000"),
            stop_loss_price=Decimal("49000"),
        )
        print(f"Position size: {result.quantity}")
    """

    def __init__(self, config: Optional[PositionSizingConfig] = None):
        """
        Initialize position sizer.

        Args:
            config: Position sizing configuration
        """
        self.config = config or PositionSizingConfig()

    def calculate(
        self,
        balance: Decimal,
        entry_price: Decimal,
        stop_loss_price: Decimal,
        leverage: int = 1,
        existing_positions: int = 0,
        signal_strength_multiplier: float = 1.0,
    ) -> PositionSizeResult:
        """
        Calculate position size.

        Args:
            balance: Available balance in USDT
            entry_price: Expected entry price
            stop_loss_price: Stop-loss price
            leverage: Leverage to use (for futures)
            existing_positions: Number of current open positions
            signal_strength_multiplier: Multiplier based on signal strength (0-1)

        Returns:
            PositionSizeResult with calculated values
        """
        # Check position limits
        if existing_positions >= self.config.max_open_positions:
            logger.warning(
                "Max positions reached",
                current=existing_positions,
                max=self.config.max_open_positions,
            )
            return PositionSizeResult(
                quantity=Decimal("0"),
                position_value=Decimal("0"),
                risk_amount=Decimal("0"),
                risk_percent=0.0,
            )

        # Calculate based on mode
        if self.config.mode == "risk_percent":
            result = self._calculate_risk_percent(
                balance,
                entry_price,
                stop_loss_price,
                leverage,
            )
        elif self.config.mode == "fixed_amount":
            result = self._calculate_fixed_amount(
                entry_price,
                stop_loss_price,
                leverage,
            )
        elif self.config.mode == "kelly":
            result = self._calculate_kelly(
                balance,
                entry_price,
                stop_loss_price,
                leverage,
            )
        else:
            # Default to risk percent
            result = self._calculate_risk_percent(
                balance,
                entry_price,
                stop_loss_price,
                leverage,
            )

        # Apply signal strength multiplier
        if signal_strength_multiplier < 1.0:
            result = PositionSizeResult(
                quantity=result.quantity * Decimal(str(signal_strength_multiplier)),
                position_value=result.position_value * Decimal(str(signal_strength_multiplier)),
                risk_amount=result.risk_amount * Decimal(str(signal_strength_multiplier)),
                risk_percent=result.risk_percent * signal_strength_multiplier,
                leverage_used=result.leverage_used,
            )

        # Apply safety limits
        result = self._apply_limits(result, balance, leverage)

        return result

    def _calculate_risk_percent(
        self,
        balance: Decimal,
        entry_price: Decimal,
        stop_loss_price: Decimal,
        leverage: int,
    ) -> PositionSizeResult:
        """Calculate position size based on risk percentage."""
        # Calculate risk amount
        risk_amount = balance * Decimal(str(self.config.risk_per_trade / 100))

        # Calculate stop distance percentage
        stop_distance = abs(entry_price - stop_loss_price)
        stop_percent = stop_distance / entry_price

        if stop_percent == 0:
            logger.warning("Stop distance is zero")
            return PositionSizeResult(
                quantity=Decimal("0"),
                position_value=Decimal("0"),
                risk_amount=Decimal("0"),
                risk_percent=0.0,
            )

        # Position value = risk / stop_percent (with leverage)
        # For leveraged positions, the stop_percent affects margin, not notional
        position_value = risk_amount / stop_percent

        # Apply leverage to get actual position size
        notional_value = position_value * Decimal(str(leverage))
        quantity = notional_value / entry_price

        return PositionSizeResult(
            quantity=quantity,
            position_value=position_value,  # Margin required
            risk_amount=risk_amount,
            risk_percent=self.config.risk_per_trade,
            leverage_used=leverage,
        )

    def _calculate_fixed_amount(
        self,
        entry_price: Decimal,
        stop_loss_price: Decimal,
        leverage: int,
    ) -> PositionSizeResult:
        """Calculate position size based on fixed amount.

        fixed_amount is the desired position value (notional), NOT margin.
        Example: fixed_amount=10 USDT with 10x leverage = 10 USDT position, 1 USDT margin.
        """
        # fixed_amount is the notional position value
        notional_value = self.config.fixed_amount
        quantity = notional_value / entry_price

        # Margin required = notional / leverage
        margin_value = notional_value / Decimal(str(leverage))

        # Calculate implied risk based on position value
        stop_distance = abs(entry_price - stop_loss_price)
        stop_percent = stop_distance / entry_price
        risk_amount = notional_value * stop_percent

        return PositionSizeResult(
            quantity=quantity,
            position_value=notional_value,  # Notional position value (matches min_position_size)
            risk_amount=risk_amount,
            risk_percent=float(stop_percent * 100),
            leverage_used=leverage,
        )

    def _calculate_kelly(
        self,
        balance: Decimal,
        entry_price: Decimal,
        stop_loss_price: Decimal,
        leverage: int,
        win_rate: float = 0.55,  # Default assumed win rate
        avg_win_loss_ratio: float = 1.5,  # Default R:R
    ) -> PositionSizeResult:
        """
        Calculate position size using Kelly Criterion.

        Kelly formula: f* = (bp - q) / b
        where:
        - f* = optimal fraction of capital
        - b = odds (win/loss ratio)
        - p = probability of winning
        - q = probability of losing (1 - p)
        """
        p = win_rate
        q = 1 - p
        b = avg_win_loss_ratio

        # Kelly fraction
        kelly_f = (b * p - q) / b

        # Apply fractional Kelly for safety
        kelly_f = kelly_f * self.config.kelly_fraction

        # Ensure positive and reasonable
        kelly_f = max(0, min(kelly_f, 0.25))

        # Calculate position
        risk_amount = balance * Decimal(str(kelly_f))

        stop_distance = abs(entry_price - stop_loss_price)
        stop_percent = stop_distance / entry_price

        if stop_percent == 0:
            return PositionSizeResult(
                quantity=Decimal("0"),
                position_value=Decimal("0"),
                risk_amount=Decimal("0"),
                risk_percent=0.0,
            )

        position_value = risk_amount / stop_percent
        notional_value = position_value * Decimal(str(leverage))
        quantity = notional_value / entry_price

        return PositionSizeResult(
            quantity=quantity,
            position_value=position_value,
            risk_amount=risk_amount,
            risk_percent=kelly_f * 100,
            leverage_used=leverage,
        )

    def _apply_limits(
        self,
        result: PositionSizeResult,
        balance: Decimal,
        leverage: int,
    ) -> PositionSizeResult:
        """Apply safety limits to position size."""
        position_value = result.position_value
        quantity = result.quantity

        # Skip max_position_percent check for fixed_amount mode
        # In fixed_amount mode, we use the specified amount regardless of balance
        if self.config.mode != "fixed_amount":
            # Check max position percent
            max_value = balance * Decimal(str(self.config.max_position_percent / 100))
            if position_value > max_value:
                ratio = max_value / position_value
                position_value = max_value
                quantity = quantity * ratio
                logger.info(
                    "Position capped by max percent",
                    max_percent=self.config.max_position_percent,
                )

        # Check min/max absolute limits
        if position_value < self.config.min_position_size:
            logger.warning(
                "Position below minimum",
                value=str(position_value),
                min=str(self.config.min_position_size),
            )
            return PositionSizeResult(
                quantity=Decimal("0"),
                position_value=Decimal("0"),
                risk_amount=Decimal("0"),
                risk_percent=0.0,
            )

        if position_value > self.config.max_position_size:
            ratio = self.config.max_position_size / position_value
            position_value = self.config.max_position_size
            quantity = quantity * ratio
            logger.info(
                "Position capped by max size",
                max_size=str(self.config.max_position_size),
            )

        # Recalculate risk
        risk_amount = position_value * Decimal(str(result.risk_percent / 100))

        return PositionSizeResult(
            quantity=quantity,
            position_value=position_value,
            risk_amount=risk_amount,
            risk_percent=result.risk_percent,
            leverage_used=result.leverage_used,
        )

    def validate_position(
        self,
        quantity: Decimal,
        entry_price: Decimal,
        balance: Decimal,
        leverage: int = 1,
    ) -> tuple[bool, str]:
        """
        Validate a proposed position size.

        Args:
            quantity: Position quantity
            entry_price: Entry price
            balance: Available balance
            leverage: Leverage used

        Returns:
            Tuple of (is_valid, error_message)
        """
        position_value = (quantity * entry_price) / Decimal(str(leverage))

        # Check against max percent
        max_value = balance * Decimal(str(self.config.max_position_percent / 100))
        if position_value > max_value:
            return False, f"Position exceeds max {self.config.max_position_percent}% of balance"

        # Check absolute limits
        if position_value < self.config.min_position_size:
            return False, f"Position below minimum {self.config.min_position_size} USDT"

        if position_value > self.config.max_position_size:
            return False, f"Position exceeds maximum {self.config.max_position_size} USDT"

        return True, ""

    def update_config(self, **kwargs) -> None:
        """Update configuration parameters."""
        for key, value in kwargs.items():
            if hasattr(self.config, key):
                setattr(self.config, key, value)

    def __repr__(self) -> str:
        return (
            f"PositionSizer(mode='{self.config.mode}', "
            f"risk_per_trade={self.config.risk_per_trade}%)"
        )
