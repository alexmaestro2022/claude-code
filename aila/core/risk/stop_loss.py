"""
AILA - Stop-Loss Management Module

Dynamic stop-loss calculation and management.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

import pandas as pd
import structlog

logger = structlog.get_logger(__name__)


@dataclass
class StopLossConfig:
    """Configuration for stop-loss management."""

    mode: str = "supertrend_line"  # supertrend_line | fixed_percent | atr

    # SuperTrend line mode
    supertrend_line: int = 2  # 1 (fast), 2 (medium), 3 (slow)

    # Fixed percent mode
    fixed_percent: float = 2.0  # Fixed % from entry

    # ATR mode
    atr_period: int = 14
    atr_multiplier: float = 1.5

    # Trailing stop
    trailing_enabled: bool = True
    trailing_activation: float = 1.0  # % profit to activate
    trailing_step: float = 0.5  # % trailing step

    # Safety
    max_stop_distance_percent: float = 10.0  # Max allowed SL distance


@dataclass
class StopLossLevel:
    """Stop-loss level result."""

    price: Decimal
    distance_percent: float
    mode: str
    is_trailing: bool = False


class StopLossManager:
    """
    Stop-loss management system.

    Supports multiple stop-loss modes:
    - supertrend_line: Use SuperTrend indicator line
    - fixed_percent: Fixed percentage from entry
    - atr: ATR-based dynamic stop

    Also supports:
    - Trailing stop with configurable activation
    - Break-even stop movement

    Example:
        config = StopLossConfig(
            mode="supertrend_line",
            supertrend_line=2,
            trailing_enabled=True,
            trailing_activation=1.0,
        )
        manager = StopLossManager(config)

        # Calculate initial stop
        sl = manager.calculate_stop_loss(
            side="long",
            entry_price=Decimal("50000"),
            supertrend_values={"st2": Decimal("49500")},
        )

        # Update trailing stop
        new_sl = manager.update_trailing_stop(
            side="long",
            entry_price=Decimal("50000"),
            current_price=Decimal("51000"),
            current_stop=sl.price,
        )
    """

    def __init__(self, config: Optional[StopLossConfig] = None):
        """
        Initialize stop-loss manager.

        Args:
            config: Stop-loss configuration
        """
        self.config = config or StopLossConfig()

    def calculate_stop_loss(
        self,
        side: str,
        entry_price: Decimal,
        supertrend_values: Optional[dict[str, Decimal]] = None,
        atr_value: Optional[Decimal] = None,
        df: Optional[pd.DataFrame] = None,
    ) -> StopLossLevel:
        """
        Calculate stop-loss price.

        Args:
            side: Position side ('long' or 'short')
            entry_price: Entry price
            supertrend_values: Dict of SuperTrend line values {'st1': ..., 'st2': ..., 'st3': ...}
            atr_value: Current ATR value (for ATR mode)
            df: DataFrame with OHLCV data (for calculating ATR if needed)

        Returns:
            StopLossLevel with calculated values
        """
        if self.config.mode == "supertrend_line":
            sl_price = self._calculate_supertrend_stop(
                side,
                entry_price,
                supertrend_values,
            )
        elif self.config.mode == "fixed_percent":
            sl_price = self._calculate_fixed_stop(side, entry_price)
        elif self.config.mode == "atr":
            sl_price = self._calculate_atr_stop(
                side,
                entry_price,
                atr_value,
                df,
            )
        else:
            # Default to fixed percent
            sl_price = self._calculate_fixed_stop(side, entry_price)

        # Calculate distance
        distance_percent = abs(float((entry_price - sl_price) / entry_price)) * 100

        # Apply max distance limit
        if distance_percent > self.config.max_stop_distance_percent:
            logger.warning(
                "Stop distance exceeds max, capping",
                original_distance=distance_percent,
                max_distance=self.config.max_stop_distance_percent,
            )
            sl_price = self._calculate_fixed_stop(
                side,
                entry_price,
                self.config.max_stop_distance_percent,
            )
            distance_percent = self.config.max_stop_distance_percent

        return StopLossLevel(
            price=sl_price,
            distance_percent=distance_percent,
            mode=self.config.mode,
        )

    def _calculate_supertrend_stop(
        self,
        side: str,
        entry_price: Decimal,
        supertrend_values: Optional[dict[str, Decimal]],
    ) -> Decimal:
        """Calculate stop-loss using SuperTrend line."""
        if supertrend_values is None:
            logger.warning("No SuperTrend values, using fixed percent")
            return self._calculate_fixed_stop(side, entry_price)

        line_key = f"st{self.config.supertrend_line}"
        st_value = supertrend_values.get(line_key)

        if st_value is None:
            logger.warning(f"SuperTrend line {line_key} not found, using fixed percent")
            return self._calculate_fixed_stop(side, entry_price)

        # Validate stop is on correct side
        if side == "long" and st_value >= entry_price:
            logger.warning("SuperTrend stop above entry for long, using fixed percent")
            return self._calculate_fixed_stop(side, entry_price)
        elif side == "short" and st_value <= entry_price:
            logger.warning("SuperTrend stop below entry for short, using fixed percent")
            return self._calculate_fixed_stop(side, entry_price)

        return st_value

    def _calculate_fixed_stop(
        self,
        side: str,
        entry_price: Decimal,
        percent: Optional[float] = None,
    ) -> Decimal:
        """Calculate fixed percentage stop-loss."""
        pct = percent if percent is not None else self.config.fixed_percent
        distance = entry_price * Decimal(str(pct / 100))

        if side == "long":
            return entry_price - distance
        else:
            return entry_price + distance

    def _calculate_atr_stop(
        self,
        side: str,
        entry_price: Decimal,
        atr_value: Optional[Decimal],
        df: Optional[pd.DataFrame] = None,
    ) -> Decimal:
        """Calculate ATR-based stop-loss."""
        if atr_value is None and df is not None:
            # Calculate ATR from DataFrame
            from ..indicators.atr import calculate_atr

            atr_series = calculate_atr(
                df["high"],
                df["low"],
                df["close"],
                period=self.config.atr_period,
            )
            atr_value = Decimal(str(atr_series.iloc[-1]))

        if atr_value is None:
            logger.warning("No ATR value available, using fixed percent")
            return self._calculate_fixed_stop(side, entry_price)

        distance = atr_value * Decimal(str(self.config.atr_multiplier))

        if side == "long":
            return entry_price - distance
        else:
            return entry_price + distance

    def update_trailing_stop(
        self,
        side: str,
        entry_price: Decimal,
        current_price: Decimal,
        current_stop: Decimal,
        highest_price: Optional[Decimal] = None,
        lowest_price: Optional[Decimal] = None,
    ) -> StopLossLevel:
        """
        Update trailing stop based on price movement.

        Args:
            side: Position side ('long' or 'short')
            entry_price: Original entry price
            current_price: Current market price
            current_stop: Current stop-loss price
            highest_price: Highest price since entry (for long)
            lowest_price: Lowest price since entry (for short)

        Returns:
            Updated StopLossLevel
        """
        if not self.config.trailing_enabled:
            return StopLossLevel(
                price=current_stop,
                distance_percent=abs(float((current_price - current_stop) / current_price)) * 100,
                mode=self.config.mode,
            )

        # Calculate profit percentage
        if side == "long":
            profit_percent = float((current_price - entry_price) / entry_price) * 100
            reference_price = highest_price or current_price
        else:
            profit_percent = float((entry_price - current_price) / entry_price) * 100
            reference_price = lowest_price or current_price

        new_stop = current_stop

        # Check if trailing should be activated
        if profit_percent >= self.config.trailing_activation:
            trailing_distance = reference_price * Decimal(str(self.config.trailing_step / 100))

            if side == "long":
                potential_stop = reference_price - trailing_distance
                if potential_stop > current_stop:
                    new_stop = potential_stop
                    logger.info(
                        "Trailing stop updated (long)",
                        old_stop=str(current_stop),
                        new_stop=str(new_stop),
                    )
            else:
                potential_stop = reference_price + trailing_distance
                if potential_stop < current_stop:
                    new_stop = potential_stop
                    logger.info(
                        "Trailing stop updated (short)",
                        old_stop=str(current_stop),
                        new_stop=str(new_stop),
                    )

        distance_percent = abs(float((current_price - new_stop) / current_price)) * 100

        return StopLossLevel(
            price=new_stop,
            distance_percent=distance_percent,
            mode=self.config.mode,
            is_trailing=new_stop != current_stop,
        )

    def should_trigger_stop(
        self,
        side: str,
        current_price: Decimal,
        stop_price: Decimal,
    ) -> bool:
        """
        Check if stop-loss should be triggered.

        Args:
            side: Position side ('long' or 'short')
            current_price: Current market price
            stop_price: Stop-loss price

        Returns:
            True if stop should be triggered
        """
        if side == "long":
            return current_price <= stop_price
        else:
            return current_price >= stop_price

    def update_config(self, **kwargs) -> None:
        """Update configuration parameters."""
        for key, value in kwargs.items():
            if hasattr(self.config, key):
                setattr(self.config, key, value)

    def __repr__(self) -> str:
        return (
            f"StopLossManager(mode='{self.config.mode}', "
            f"trailing={self.config.trailing_enabled})"
        )
