"""
AILA - Base Strategy Class

Abstract base class that all trading strategies must inherit from.
Provides common interface and shared functionality.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

import pandas as pd

from .signals import Signal, SignalType


@dataclass
class StrategyConfig:
    """Base configuration for strategies."""

    name: str = "BaseStrategy"
    timeframe: str = "1h"
    trading_pairs: list[str] = field(default_factory=lambda: ["BTCUSDT"])

    # Risk settings
    risk_per_trade: float = 2.0  # % of balance
    max_position_percent: float = 20.0  # Max % of balance in one position
    max_open_positions: int = 3

    # Filter settings
    min_volume_multiplier: float = 1.0
    max_spread_percent: float = 0.1

    # Trading hours (UTC)
    trading_hours_enabled: bool = False
    trading_start_hour: int = 0
    trading_end_hour: int = 24


@dataclass
class StrategyState:
    """Current state of a strategy."""

    is_active: bool = False
    last_signal: Optional[Signal] = None
    last_signal_time: Optional[datetime] = None
    current_position: Optional[str] = None  # 'long', 'short', or None
    signals_generated: int = 0
    trades_executed: int = 0


class BaseStrategy(ABC):
    """
    Abstract base class for trading strategies.

    All strategies must implement:
    - calculate_indicators(): Calculate required indicators
    - generate_signal(): Generate trading signal based on indicators
    - get_stop_loss(): Calculate stop-loss price
    - get_take_profit(): Calculate take-profit price

    Example:
        class MyStrategy(BaseStrategy):
            def calculate_indicators(self, df):
                # Calculate your indicators
                pass

            def generate_signal(self, df):
                # Generate signal based on indicators
                return Signal.no_signal(self.symbol, current_price)
    """

    def __init__(self, config: StrategyConfig):
        """
        Initialize strategy.

        Args:
            config: Strategy configuration
        """
        self.config = config
        self.state = StrategyState()
        self._indicators_data: dict[str, Any] = {}

    @property
    def name(self) -> str:
        """Get strategy name."""
        return self.config.name

    @property
    def timeframe(self) -> str:
        """Get strategy timeframe."""
        return self.config.timeframe

    @property
    def trading_pairs(self) -> list[str]:
        """Get list of trading pairs."""
        return self.config.trading_pairs

    @abstractmethod
    def calculate_indicators(self, df: pd.DataFrame) -> dict[str, Any]:
        """
        Calculate all required indicators.

        Args:
            df: DataFrame with OHLCV data

        Returns:
            Dictionary with indicator results
        """
        pass

    @abstractmethod
    def generate_signal(self, df: pd.DataFrame, symbol: str) -> Signal:
        """
        Generate trading signal based on current data.

        Args:
            df: DataFrame with OHLCV data and calculated indicators
            symbol: Trading pair symbol

        Returns:
            Trading signal
        """
        pass

    @abstractmethod
    def get_stop_loss(self, signal: Signal, df: pd.DataFrame) -> float:
        """
        Calculate stop-loss price for a signal.

        Args:
            signal: Trading signal
            df: DataFrame with OHLCV data

        Returns:
            Stop-loss price
        """
        pass

    @abstractmethod
    def get_take_profit(
        self,
        signal: Signal,
        stop_loss: float,
        df: pd.DataFrame,
    ) -> float:
        """
        Calculate take-profit price for a signal.

        Args:
            signal: Trading signal
            stop_loss: Stop-loss price
            df: DataFrame with OHLCV data

        Returns:
            Take-profit price
        """
        pass

    def process(self, df: pd.DataFrame, symbol: str) -> Signal:
        """
        Main processing method - calculate indicators and generate signal.

        Args:
            df: DataFrame with OHLCV data
            symbol: Trading pair symbol

        Returns:
            Trading signal with stop-loss and take-profit
        """
        # Check if trading is allowed
        if not self._is_trading_allowed():
            return Signal.no_signal(symbol, df["close"].iloc[-1])

        # Calculate indicators
        self._indicators_data = self.calculate_indicators(df)

        # Generate signal
        signal = self.generate_signal(df, symbol)

        # If it's an entry signal, calculate SL/TP
        if signal.is_entry:
            signal.stop_loss = self.get_stop_loss(signal, df)
            signal.take_profit = self.get_take_profit(signal, signal.stop_loss, df)

            # Update state
            self.state.last_signal = signal
            self.state.last_signal_time = datetime.utcnow()
            self.state.signals_generated += 1

        return signal

    def _is_trading_allowed(self) -> bool:
        """Check if trading is allowed based on time restrictions."""
        if not self.config.trading_hours_enabled:
            return True

        current_hour = datetime.utcnow().hour
        start = self.config.trading_start_hour
        end = self.config.trading_end_hour

        if start <= end:
            return start <= current_hour < end
        else:
            # Handles overnight range (e.g., 22-6)
            return current_hour >= start or current_hour < end

    def activate(self) -> None:
        """Activate the strategy."""
        self.state.is_active = True

    def deactivate(self) -> None:
        """Deactivate the strategy."""
        self.state.is_active = False

    def reset(self) -> None:
        """Reset strategy state."""
        self.state = StrategyState()
        self._indicators_data = {}

    def get_indicator(self, name: str) -> Any:
        """Get calculated indicator by name."""
        return self._indicators_data.get(name)

    def validate_dataframe(self, df: pd.DataFrame) -> bool:
        """
        Validate that DataFrame has required columns.

        Args:
            df: DataFrame to validate

        Returns:
            True if valid, raises ValueError otherwise
        """
        required_columns = {"open", "high", "low", "close", "volume"}
        missing = required_columns - set(df.columns)

        if missing:
            raise ValueError(f"Missing required columns: {missing}")

        if len(df) < self.min_candles_required:
            raise ValueError(
                f"Insufficient data: need at least {self.min_candles_required} candles, "
                f"got {len(df)}"
            )

        return True

    @property
    def min_candles_required(self) -> int:
        """Minimum number of candles required for strategy calculation."""
        return 200  # Default for EMA200

    def to_dict(self) -> dict:
        """Convert strategy to dictionary for serialization."""
        return {
            "name": self.name,
            "config": {
                "timeframe": self.config.timeframe,
                "trading_pairs": self.config.trading_pairs,
                "risk_per_trade": self.config.risk_per_trade,
                "max_position_percent": self.config.max_position_percent,
                "max_open_positions": self.config.max_open_positions,
            },
            "state": {
                "is_active": self.state.is_active,
                "signals_generated": self.state.signals_generated,
                "trades_executed": self.state.trades_executed,
            },
        }

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name='{self.name}', timeframe='{self.timeframe}')"
