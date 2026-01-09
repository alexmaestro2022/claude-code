"""
AILA - SuperTrend Indicator

The SuperTrend indicator combines ATR with basic price action to generate
trend-following signals. It creates upper and lower bands around price,
and the trend direction changes when price crosses these bands.

Triple SuperTrend uses three instances with different parameters:
- ST1 (slow): period=12, multiplier=3.0
- ST2 (medium): period=11, multiplier=2.0
- ST3 (fast): period=10, multiplier=1.0
"""

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from .atr import calculate_atr


@dataclass
class SuperTrendResult:
    """Result of SuperTrend calculation."""

    supertrend: pd.Series  # SuperTrend line value
    direction: pd.Series  # 1 = bullish (green), -1 = bearish (red)
    upper_band: pd.Series  # Upper band
    lower_band: pd.Series  # Lower band
    period: int
    multiplier: float


@dataclass
class TripleSuperTrendResult:
    """Result of Triple SuperTrend calculation."""

    st1: SuperTrendResult  # Slow SuperTrend
    st2: SuperTrendResult  # Medium SuperTrend
    st3: SuperTrendResult  # Fast SuperTrend
    combined_direction: pd.Series  # 1 if all green, -1 if all red, 0 mixed


def calculate_supertrend(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 10,
    multiplier: float = 3.0,
) -> SuperTrendResult:
    """
    Calculate SuperTrend indicator.

    SuperTrend = (High + Low) / 2 ± (ATR * Multiplier)

    The indicator flips between upper and lower bands based on:
    - If close > previous SuperTrend and was bearish -> flip to bullish
    - If close < previous SuperTrend and was bullish -> flip to bearish

    Args:
        high: Series of high prices
        low: Series of low prices
        close: Series of close prices
        period: ATR period (default: 10)
        multiplier: ATR multiplier (default: 3.0)

    Returns:
        SuperTrendResult with all calculated values
    """
    # Calculate ATR
    atr = calculate_atr(high, low, close, period=period)

    # Calculate HL2 (typical price base)
    hl2 = (high + low) / 2

    # Calculate basic upper and lower bands
    basic_upper = hl2 + (multiplier * atr)
    basic_lower = hl2 - (multiplier * atr)

    # Initialize arrays for final bands and direction
    n = len(close)
    final_upper = np.zeros(n)
    final_lower = np.zeros(n)
    supertrend = np.zeros(n)
    direction = np.zeros(n)

    # First valid index (after ATR warmup)
    start_idx = period

    # Initialize first values
    if start_idx < n:
        final_upper[start_idx] = basic_upper.iloc[start_idx]
        final_lower[start_idx] = basic_lower.iloc[start_idx]
        direction[start_idx] = 1  # Start bullish

    # Calculate SuperTrend
    for i in range(start_idx + 1, n):
        # Update final upper band
        if basic_upper.iloc[i] < final_upper[i - 1] or close.iloc[i - 1] > final_upper[i - 1]:
            final_upper[i] = basic_upper.iloc[i]
        else:
            final_upper[i] = final_upper[i - 1]

        # Update final lower band
        if basic_lower.iloc[i] > final_lower[i - 1] or close.iloc[i - 1] < final_lower[i - 1]:
            final_lower[i] = basic_lower.iloc[i]
        else:
            final_lower[i] = final_lower[i - 1]

        # Determine direction and SuperTrend value
        if direction[i - 1] == 1:  # Was bullish
            if close.iloc[i] < final_lower[i]:
                direction[i] = -1  # Flip to bearish
                supertrend[i] = final_upper[i]
            else:
                direction[i] = 1  # Stay bullish
                supertrend[i] = final_lower[i]
        else:  # Was bearish
            if close.iloc[i] > final_upper[i]:
                direction[i] = 1  # Flip to bullish
                supertrend[i] = final_lower[i]
            else:
                direction[i] = -1  # Stay bearish
                supertrend[i] = final_upper[i]

    # Convert to pandas Series
    index = close.index
    supertrend_series = pd.Series(supertrend, index=index, name=f"supertrend_{period}_{multiplier}")
    direction_series = pd.Series(direction, index=index, name=f"st_direction_{period}_{multiplier}")
    upper_band_series = pd.Series(final_upper, index=index, name=f"st_upper_{period}_{multiplier}")
    lower_band_series = pd.Series(final_lower, index=index, name=f"st_lower_{period}_{multiplier}")

    # Replace zeros with NaN for warmup period
    supertrend_series[:start_idx] = np.nan
    direction_series[:start_idx] = np.nan
    upper_band_series[:start_idx] = np.nan
    lower_band_series[:start_idx] = np.nan

    return SuperTrendResult(
        supertrend=supertrend_series,
        direction=direction_series,
        upper_band=upper_band_series,
        lower_band=lower_band_series,
        period=period,
        multiplier=multiplier,
    )


class SuperTrend:
    """
    SuperTrend Indicator class for object-oriented usage.

    Example:
        st = SuperTrend(period=10, multiplier=3.0)
        result = st.calculate(df)
        print(result.direction)  # 1 = bullish, -1 = bearish
    """

    def __init__(
        self,
        period: int = 10,
        multiplier: float = 3.0,
    ):
        """
        Initialize SuperTrend indicator.

        Args:
            period: ATR period
            multiplier: ATR multiplier
        """
        self.period = period
        self.multiplier = multiplier
        self._last_result: Optional[SuperTrendResult] = None

    def calculate(
        self,
        df: pd.DataFrame,
        high_col: str = "high",
        low_col: str = "low",
        close_col: str = "close",
    ) -> SuperTrendResult:
        """
        Calculate SuperTrend from DataFrame.

        Args:
            df: DataFrame with OHLC data
            high_col: Name of high column
            low_col: Name of low column
            close_col: Name of close column

        Returns:
            SuperTrendResult with all calculated values
        """
        result = calculate_supertrend(
            df[high_col],
            df[low_col],
            df[close_col],
            period=self.period,
            multiplier=self.multiplier,
        )

        self._last_result = result
        return result

    @property
    def last_direction(self) -> Optional[int]:
        """Get the last calculated direction."""
        if self._last_result is not None:
            return int(self._last_result.direction.iloc[-1])
        return None

    @property
    def last_value(self) -> Optional[float]:
        """Get the last SuperTrend value."""
        if self._last_result is not None:
            return self._last_result.supertrend.iloc[-1]
        return None

    def __repr__(self) -> str:
        return f"SuperTrend(period={self.period}, multiplier={self.multiplier})"


class TripleSuperTrend:
    """
    Triple SuperTrend indicator combining three SuperTrend instances.

    Default configuration:
    - ST1 (slow): period=12, multiplier=3.0
    - ST2 (medium): period=11, multiplier=2.0
    - ST3 (fast): period=10, multiplier=1.0

    Signals:
    - LONG: All three SuperTrends are bullish (direction = 1)
    - SHORT: All three SuperTrends are bearish (direction = -1)
    - NEUTRAL: Mixed directions

    Example:
        triple_st = TripleSuperTrend()
        result = triple_st.calculate(df)
        print(result.combined_direction)
    """

    def __init__(
        self,
        st1_period: int = 12,
        st1_multiplier: float = 3.0,
        st2_period: int = 11,
        st2_multiplier: float = 2.0,
        st3_period: int = 10,
        st3_multiplier: float = 1.0,
    ):
        """
        Initialize Triple SuperTrend indicator.

        Args:
            st1_period: Period for slow SuperTrend
            st1_multiplier: Multiplier for slow SuperTrend
            st2_period: Period for medium SuperTrend
            st2_multiplier: Multiplier for medium SuperTrend
            st3_period: Period for fast SuperTrend
            st3_multiplier: Multiplier for fast SuperTrend
        """
        self.st1 = SuperTrend(period=st1_period, multiplier=st1_multiplier)
        self.st2 = SuperTrend(period=st2_period, multiplier=st2_multiplier)
        self.st3 = SuperTrend(period=st3_period, multiplier=st3_multiplier)
        self._last_result: Optional[TripleSuperTrendResult] = None

    def calculate(
        self,
        df: pd.DataFrame,
        high_col: str = "high",
        low_col: str = "low",
        close_col: str = "close",
    ) -> TripleSuperTrendResult:
        """
        Calculate Triple SuperTrend from DataFrame.

        Args:
            df: DataFrame with OHLC data
            high_col: Name of high column
            low_col: Name of low column
            close_col: Name of close column

        Returns:
            TripleSuperTrendResult with all three SuperTrends and combined direction
        """
        st1_result = self.st1.calculate(df, high_col, low_col, close_col)
        st2_result = self.st2.calculate(df, high_col, low_col, close_col)
        st3_result = self.st3.calculate(df, high_col, low_col, close_col)

        # Calculate combined direction
        combined = self._calculate_combined_direction(
            st1_result.direction,
            st2_result.direction,
            st3_result.direction,
        )

        self._last_result = TripleSuperTrendResult(
            st1=st1_result,
            st2=st2_result,
            st3=st3_result,
            combined_direction=combined,
        )

        return self._last_result

    def _calculate_combined_direction(
        self,
        dir1: pd.Series,
        dir2: pd.Series,
        dir3: pd.Series,
    ) -> pd.Series:
        """
        Calculate combined direction from three SuperTrend directions.

        Returns:
            1 if all bullish, -1 if all bearish, 0 if mixed
        """
        combined = pd.Series(
            np.where(
                (dir1 == 1) & (dir2 == 1) & (dir3 == 1),
                1,
                np.where(
                    (dir1 == -1) & (dir2 == -1) & (dir3 == -1),
                    -1,
                    0,
                ),
            ),
            index=dir1.index,
            name="triple_st_direction",
        )
        return combined

    def get_signal(self) -> Optional[int]:
        """
        Get the current trading signal.

        Returns:
            1 for LONG, -1 for SHORT, 0 for no signal, None if not calculated
        """
        if self._last_result is not None:
            return int(self._last_result.combined_direction.iloc[-1])
        return None

    def get_stop_loss_line(self, line_number: int = 2) -> Optional[float]:
        """
        Get the SuperTrend line value for stop-loss.

        Args:
            line_number: Which SuperTrend line to use (1, 2, or 3)

        Returns:
            The SuperTrend line value to use as stop-loss
        """
        if self._last_result is None:
            return None

        result_map = {
            1: self._last_result.st1,
            2: self._last_result.st2,
            3: self._last_result.st3,
        }

        st_result = result_map.get(line_number)
        if st_result is not None:
            return st_result.supertrend.iloc[-1]
        return None

    def is_all_bullish(self) -> bool:
        """Check if all three SuperTrends are bullish."""
        return self.get_signal() == 1

    def is_all_bearish(self) -> bool:
        """Check if all three SuperTrends are bearish."""
        return self.get_signal() == -1

    @property
    def config(self) -> dict:
        """Get current configuration."""
        return {
            "st1": {"period": self.st1.period, "multiplier": self.st1.multiplier},
            "st2": {"period": self.st2.period, "multiplier": self.st2.multiplier},
            "st3": {"period": self.st3.period, "multiplier": self.st3.multiplier},
        }

    def __repr__(self) -> str:
        return (
            f"TripleSuperTrend("
            f"st1=({self.st1.period}, {self.st1.multiplier}), "
            f"st2=({self.st2.period}, {self.st2.multiplier}), "
            f"st3=({self.st3.period}, {self.st3.multiplier}))"
        )
