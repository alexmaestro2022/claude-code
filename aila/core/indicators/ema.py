"""
AILA - EMA (Exponential Moving Average) Indicator

The EMA is used as a trend filter in the Triple SuperTrend strategy.
Default period is 200 (EMA200) for identifying the major trend direction.
"""

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class EMAResult:
    """Result of EMA calculation."""

    ema: pd.Series
    period: int
    trend: pd.Series  # 1 = bullish (price > EMA), -1 = bearish (price < EMA)


def calculate_ema(
    data: pd.Series,
    period: int = 200,
    adjust: bool = False,
) -> pd.Series:
    """
    Calculate Exponential Moving Average.

    EMA = Price(t) * k + EMA(y) * (1-k)
    where k = 2 / (N + 1) and N = period

    Args:
        data: Series of prices (typically close prices)
        period: EMA period (default: 200)
        adjust: Whether to adjust EMA calculation (default: False)

    Returns:
        Series of EMA values
    """
    ema = data.ewm(span=period, adjust=adjust).mean()
    ema.name = f"ema_{period}"
    return ema


def calculate_trend_direction(
    price: pd.Series,
    ema: pd.Series,
) -> pd.Series:
    """
    Calculate trend direction based on price vs EMA.

    Args:
        price: Series of close prices
        ema: Series of EMA values

    Returns:
        Series with 1 (bullish) or -1 (bearish)
    """
    trend = pd.Series(
        np.where(price > ema, 1, -1),
        index=price.index,
        name="ema_trend",
    )
    return trend


class EMA:
    """
    EMA Indicator class for object-oriented usage.

    Used as a trend filter in the Triple SuperTrend strategy:
    - If EMA filter is enabled:
      - LONG signals only when price > EMA
      - SHORT signals only when price < EMA

    Example:
        ema_indicator = EMA(period=200)
        result = ema_indicator.calculate(df)
        print(result.ema)
        print(result.trend)
    """

    def __init__(
        self,
        period: int = 200,
        enabled: bool = True,
    ):
        """
        Initialize EMA indicator.

        Args:
            period: EMA period (default: 200)
            enabled: Whether EMA filter is enabled
        """
        self.period = period
        self.enabled = enabled
        self._last_result: Optional[EMAResult] = None

    def calculate(
        self,
        df: pd.DataFrame,
        price_col: str = "close",
    ) -> EMAResult:
        """
        Calculate EMA from DataFrame.

        Args:
            df: DataFrame with price data
            price_col: Name of price column (default: 'close')

        Returns:
            EMAResult with ema and trend series
        """
        price = df[price_col]
        ema = calculate_ema(price, period=self.period)
        trend = calculate_trend_direction(price, ema)

        self._last_result = EMAResult(
            ema=ema,
            period=self.period,
            trend=trend,
        )

        return self._last_result

    def update(
        self,
        price: float,
        prev_ema: float,
    ) -> float:
        """
        Update EMA with new price data (for real-time calculation).

        Args:
            price: Current close price
            prev_ema: Previous EMA value

        Returns:
            Updated EMA value
        """
        k = 2.0 / (self.period + 1)
        new_ema = price * k + prev_ema * (1 - k)
        return new_ema

    def get_trend(self, price: float, ema: float) -> int:
        """
        Get trend direction based on current price and EMA.

        Args:
            price: Current price
            ema: Current EMA value

        Returns:
            1 if bullish (price > ema), -1 if bearish
        """
        return 1 if price > ema else -1

    def filter_signal(
        self,
        signal: int,
        price: float,
        ema: float,
        mode: str = "strict",
    ) -> tuple[bool, float]:
        """
        Filter trading signal based on EMA trend.

        Args:
            signal: Trading signal (1 for long, -1 for short)
            price: Current price
            ema: Current EMA value
            mode: Filter mode ('strict' or 'soft')

        Returns:
            Tuple of (allow_trade, position_multiplier)
            - strict mode: (False, 0) if signal against EMA trend
            - soft mode: (True, 0.5) if signal against EMA trend
        """
        if not self.enabled:
            return True, 1.0

        trend = self.get_trend(price, ema)

        if signal == trend:
            # Signal aligns with EMA trend
            return True, 1.0
        else:
            # Signal against EMA trend
            if mode == "strict":
                return False, 0.0
            else:  # soft mode
                return True, 0.5

    @property
    def last_value(self) -> Optional[float]:
        """Get the last calculated EMA value."""
        if self._last_result is not None:
            return self._last_result.ema.iloc[-1]
        return None

    @property
    def last_trend(self) -> Optional[int]:
        """Get the last calculated trend direction."""
        if self._last_result is not None:
            return self._last_result.trend.iloc[-1]
        return None

    def __repr__(self) -> str:
        return f"EMA(period={self.period}, enabled={self.enabled})"
