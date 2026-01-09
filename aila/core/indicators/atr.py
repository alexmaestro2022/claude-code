"""
AILA - ATR (Average True Range) Indicator

The ATR measures market volatility by decomposing the entire range of an asset
price for a given period. It is used in the SuperTrend indicator calculation.
"""

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class ATRResult:
    """Result of ATR calculation."""

    true_range: pd.Series
    atr: pd.Series
    period: int


def calculate_true_range(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
) -> pd.Series:
    """
    Calculate True Range.

    True Range is the greatest of:
    - Current High - Current Low
    - |Current High - Previous Close|
    - |Current Low - Previous Close|

    Args:
        high: Series of high prices
        low: Series of low prices
        close: Series of close prices

    Returns:
        Series of True Range values
    """
    prev_close = close.shift(1)

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()

    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    true_range.name = "true_range"

    return true_range


def calculate_atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
    smoothing: str = "rma",
) -> pd.Series:
    """
    Calculate Average True Range (ATR).

    Args:
        high: Series of high prices
        low: Series of low prices
        close: Series of close prices
        period: ATR period (default: 14)
        smoothing: Smoothing method - 'rma' (Wilder's), 'sma', or 'ema'

    Returns:
        Series of ATR values
    """
    true_range = calculate_true_range(high, low, close)

    if smoothing == "sma":
        atr = true_range.rolling(window=period).mean()
    elif smoothing == "ema":
        atr = true_range.ewm(span=period, adjust=False).mean()
    else:  # rma (Wilder's smoothing) - default for SuperTrend
        alpha = 1.0 / period
        atr = true_range.ewm(alpha=alpha, adjust=False).mean()

    atr.name = f"atr_{period}"
    return atr


class ATR:
    """
    ATR Indicator class for object-oriented usage.

    Example:
        atr_indicator = ATR(period=14)
        result = atr_indicator.calculate(df)
        print(result.atr)
    """

    def __init__(
        self,
        period: int = 14,
        smoothing: str = "rma",
    ):
        """
        Initialize ATR indicator.

        Args:
            period: ATR period
            smoothing: Smoothing method ('rma', 'sma', 'ema')
        """
        self.period = period
        self.smoothing = smoothing
        self._last_result: Optional[ATRResult] = None

    def calculate(
        self,
        df: pd.DataFrame,
        high_col: str = "high",
        low_col: str = "low",
        close_col: str = "close",
    ) -> ATRResult:
        """
        Calculate ATR from DataFrame.

        Args:
            df: DataFrame with OHLC data
            high_col: Name of high column
            low_col: Name of low column
            close_col: Name of close column

        Returns:
            ATRResult with true_range and atr series
        """
        true_range = calculate_true_range(
            df[high_col],
            df[low_col],
            df[close_col],
        )

        atr = calculate_atr(
            df[high_col],
            df[low_col],
            df[close_col],
            period=self.period,
            smoothing=self.smoothing,
        )

        self._last_result = ATRResult(
            true_range=true_range,
            atr=atr,
            period=self.period,
        )

        return self._last_result

    def update(
        self,
        high: float,
        low: float,
        close: float,
        prev_close: float,
        prev_atr: float,
    ) -> float:
        """
        Update ATR with new candle data (for real-time calculation).

        Args:
            high: Current high price
            low: Current low price
            close: Current close price
            prev_close: Previous close price
            prev_atr: Previous ATR value

        Returns:
            Updated ATR value
        """
        # Calculate current True Range
        tr1 = high - low
        tr2 = abs(high - prev_close)
        tr3 = abs(low - prev_close)
        true_range = max(tr1, tr2, tr3)

        # Wilder's smoothing (RMA)
        alpha = 1.0 / self.period
        new_atr = alpha * true_range + (1 - alpha) * prev_atr

        return new_atr

    @property
    def last_value(self) -> Optional[float]:
        """Get the last calculated ATR value."""
        if self._last_result is not None:
            return self._last_result.atr.iloc[-1]
        return None

    def __repr__(self) -> str:
        return f"ATR(period={self.period}, smoothing='{self.smoothing}')"
