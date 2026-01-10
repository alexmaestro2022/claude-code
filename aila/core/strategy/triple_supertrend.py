"""
AILA - Triple SuperTrend Strategy

Main trading strategy combining three SuperTrend indicators with optional EMA200 filter.

Entry Rules:
- LONG: All three SuperTrends are green (direction = 1) AND price > EMA200 (if enabled)
- SHORT: All three SuperTrends are red (direction = -1) AND price < EMA200 (if enabled)

Exit Rules:
- Stop-loss at selected SuperTrend line (configurable: 1, 2, or 3)
- Take-profit based on risk-reward ratio
- Optional trailing stop
"""

from dataclasses import dataclass, field
from typing import Any, Optional

import pandas as pd

from ..indicators import EMA, TripleSuperTrend
from .base import BaseStrategy, StrategyConfig
from .signals import Signal, SignalStrength, SignalType


@dataclass
class TripleSuperTrendConfig(StrategyConfig):
    """Configuration for Triple SuperTrend strategy."""

    name: str = "TripleSuperTrend"

    # SuperTrend 1 (slow)
    st1_period: int = 12
    st1_multiplier: float = 3.0

    # SuperTrend 2 (medium)
    st2_period: int = 11
    st2_multiplier: float = 2.0

    # SuperTrend 3 (fast)
    st3_period: int = 10
    st3_multiplier: float = 1.0

    # EMA filter
    ema_enabled: bool = True
    ema_period: int = 200
    ema_filter_mode: str = "strict"  # 'strict' or 'soft'

    # Stop-loss settings
    sl_mode: str = "supertrend_line"  # 'supertrend_line', 'fixed_percent', 'atr'
    sl_supertrend_line: int = 2  # 1 (fast), 2 (medium), or 3 (slow)
    sl_fixed_percent: float = 2.0
    sl_atr_multiplier: float = 1.5

    # Take-profit settings
    tp_mode: str = "risk_ratio"  # 'risk_ratio', 'fixed_percent', 'multi_target'
    tp_risk_ratio: float = 2.0
    tp_fixed_percent: float = 4.0
    tp_multi_targets: list = field(
        default_factory=lambda: [
            {"percent": 50, "target": 1.5},
            {"percent": 30, "target": 2.0},
            {"percent": 20, "target": 3.0},
        ]
    )

    # Trailing stop
    trailing_enabled: bool = True
    trailing_activation: float = 1.0  # Activate after +1% profit
    trailing_step: float = 0.5  # 0.5% trailing step

    # Allow custom parameters modification
    allow_custom_params: bool = True


class TripleSuperTrendStrategy(BaseStrategy):
    """
    Triple SuperTrend + EMA200 trading strategy.

    This strategy generates signals when all three SuperTrend indicators
    align in the same direction, with optional confirmation from EMA200.

    Example:
        config = TripleSuperTrendConfig(
            timeframe='1h',
            trading_pairs=['BTCUSDT', 'ETHUSDT'],
            ema_enabled=True,
        )
        strategy = TripleSuperTrendStrategy(config)

        # Process new candle data
        signal = strategy.process(df, 'BTCUSDT')
        if signal.is_entry:
            print(f"Signal: {signal}")
    """

    def __init__(self, config: Optional[TripleSuperTrendConfig] = None):
        """
        Initialize Triple SuperTrend strategy.

        Args:
            config: Strategy configuration (uses defaults if not provided)
        """
        if config is None:
            config = TripleSuperTrendConfig()

        super().__init__(config)
        self.config: TripleSuperTrendConfig = config

        # Initialize indicators
        self._triple_st = TripleSuperTrend(
            st1_period=config.st1_period,
            st1_multiplier=config.st1_multiplier,
            st2_period=config.st2_period,
            st2_multiplier=config.st2_multiplier,
            st3_period=config.st3_period,
            st3_multiplier=config.st3_multiplier,
        )

        self._ema = EMA(
            period=config.ema_period,
            enabled=config.ema_enabled,
        )

        # Track previous signals to detect changes
        self._prev_signal: Optional[int] = None

    def calculate_indicators(self, df: pd.DataFrame) -> dict[str, Any]:
        """
        Calculate Triple SuperTrend and EMA indicators.

        Args:
            df: DataFrame with OHLCV data

        Returns:
            Dictionary with indicator results
        """
        self.validate_dataframe(df)

        # Calculate Triple SuperTrend
        triple_st_result = self._triple_st.calculate(df)

        # Calculate EMA if enabled
        ema_result = None
        if self.config.ema_enabled:
            ema_result = self._ema.calculate(df)

        return {
            "triple_supertrend": triple_st_result,
            "ema": ema_result,
            "current_price": df["close"].iloc[-1],
        }

    def generate_signal(self, df: pd.DataFrame, symbol: str) -> Signal:
        """
        Generate trading signal based on Triple SuperTrend + EMA.

        LONG conditions:
        - All three SuperTrends are bullish (green)
        - Price > EMA200 (if EMA filter enabled in strict mode)

        SHORT conditions:
        - All three SuperTrends are bearish (red)
        - Price < EMA200 (if EMA filter enabled in strict mode)

        Args:
            df: DataFrame with OHLCV data
            symbol: Trading pair symbol

        Returns:
            Trading signal
        """
        indicators = self._indicators_data
        current_price = indicators["current_price"]
        triple_st = indicators["triple_supertrend"]
        ema_result = indicators["ema"]

        # Get combined SuperTrend direction
        st_direction = int(triple_st.combined_direction.iloc[-1])

        # Check for signal change (new entry signal only on direction change)
        if st_direction == self._prev_signal:
            # No change in direction, no new entry signal
            return Signal.no_signal(symbol, current_price)

        # Update previous signal
        prev_signal = self._prev_signal
        self._prev_signal = st_direction

        # Check EMA filter if enabled
        position_multiplier = 1.0
        if self.config.ema_enabled and ema_result is not None:
            ema_value = ema_result.ema.iloc[-1]
            ema_trend = ema_result.trend.iloc[-1]

            if self.config.ema_filter_mode == "strict":
                # Strict mode: only trade in direction of EMA
                if st_direction == 1 and ema_trend != 1:
                    return Signal.no_signal(symbol, current_price)
                if st_direction == -1 and ema_trend != -1:
                    return Signal.no_signal(symbol, current_price)
            else:  # soft mode
                # Soft mode: reduce position size when against EMA
                if st_direction != ema_trend:
                    position_multiplier = 0.5

        # Generate entry signal
        if st_direction == 1:
            # All SuperTrends bullish - LONG signal
            signal = Signal.long(
                symbol=symbol,
                price=current_price,
                strength=self._determine_signal_strength(triple_st),
                position_size_multiplier=position_multiplier,
                metadata={
                    "strategy": self.name,
                    "st1_direction": int(triple_st.st1.direction.iloc[-1]),
                    "st2_direction": int(triple_st.st2.direction.iloc[-1]),
                    "st3_direction": int(triple_st.st3.direction.iloc[-1]),
                    "ema_enabled": self.config.ema_enabled,
                    "prev_signal": prev_signal,
                },
            )
            return signal

        elif st_direction == -1:
            # All SuperTrends bearish - SHORT signal
            signal = Signal.short(
                symbol=symbol,
                price=current_price,
                strength=self._determine_signal_strength(triple_st),
                position_size_multiplier=position_multiplier,
                metadata={
                    "strategy": self.name,
                    "st1_direction": int(triple_st.st1.direction.iloc[-1]),
                    "st2_direction": int(triple_st.st2.direction.iloc[-1]),
                    "st3_direction": int(triple_st.st3.direction.iloc[-1]),
                    "ema_enabled": self.config.ema_enabled,
                    "prev_signal": prev_signal,
                },
            )
            return signal

        # Mixed signals - no entry
        return Signal.no_signal(symbol, current_price)

    def _determine_signal_strength(self, triple_st) -> SignalStrength:
        """Determine signal strength based on indicator confluence."""
        # For now, all aligned signals are considered strong
        # Could be enhanced with volume, momentum, etc.
        return SignalStrength.STRONG

    def get_stop_loss(self, signal: Signal, df: pd.DataFrame) -> float:
        """
        Calculate stop-loss price based on configuration.

        Modes:
        - supertrend_line: Use selected SuperTrend line as stop-loss
        - fixed_percent: Fixed percentage from entry
        - atr: ATR-based stop-loss

        Args:
            signal: Trading signal
            df: DataFrame with OHLCV data

        Returns:
            Stop-loss price
        """
        import structlog
        logger = structlog.get_logger(__name__)

        entry_price = signal.price
        triple_st = self._indicators_data["triple_supertrend"]

        if self.config.sl_mode == "supertrend_line":
            # Use selected SuperTrend line
            line_map = {
                1: float(triple_st.st1.supertrend.iloc[-1]),
                2: float(triple_st.st2.supertrend.iloc[-1]),
                3: float(triple_st.st3.supertrend.iloc[-1]),
            }
            sl_price = line_map.get(self.config.sl_supertrend_line, float(triple_st.st2.supertrend.iloc[-1]))

            logger.info(
                "SL calculation - SuperTrend mode",
                entry_price=entry_price,
                sl_line_number=self.config.sl_supertrend_line,
                st1_line=line_map.get(1),
                st2_line=line_map.get(2),
                st3_line=line_map.get(3),
                selected_sl_price=sl_price,
                signal_side="long" if signal.is_long else "short",
            )

            # Ensure stop-loss is on correct side
            if signal.is_long and sl_price >= entry_price:
                # Use fixed percent as fallback
                sl_price = entry_price * (1 - self.config.sl_fixed_percent / 100)
                logger.warning(
                    "SL fallback to fixed_percent for LONG",
                    reason="SuperTrend line above entry",
                    new_sl_price=sl_price,
                    fixed_percent=self.config.sl_fixed_percent,
                )
            elif signal.is_short and sl_price <= entry_price:
                sl_price = entry_price * (1 + self.config.sl_fixed_percent / 100)
                logger.warning(
                    "SL fallback to fixed_percent for SHORT",
                    reason="SuperTrend line below entry",
                    new_sl_price=sl_price,
                    fixed_percent=self.config.sl_fixed_percent,
                )

            return sl_price

        elif self.config.sl_mode == "fixed_percent":
            # Fixed percentage from entry
            if signal.is_long:
                return entry_price * (1 - self.config.sl_fixed_percent / 100)
            else:
                return entry_price * (1 + self.config.sl_fixed_percent / 100)

        elif self.config.sl_mode == "atr":
            # ATR-based stop-loss
            # Use ATR from the medium SuperTrend
            from ..indicators.atr import calculate_atr

            atr = calculate_atr(
                df["high"],
                df["low"],
                df["close"],
                period=self.config.st2_period,
            ).iloc[-1]

            atr_distance = atr * self.config.sl_atr_multiplier

            if signal.is_long:
                return entry_price - atr_distance
            else:
                return entry_price + atr_distance

        # Default fallback
        percent = self.config.sl_fixed_percent / 100
        if signal.is_long:
            return entry_price * (1 - percent)
        return entry_price * (1 + percent)

    def get_take_profit(
        self,
        signal: Signal,
        stop_loss: float,
        df: pd.DataFrame,
    ) -> float:
        """
        Calculate take-profit price based on configuration.

        Modes:
        - risk_ratio: Based on risk-reward ratio from stop-loss
        - fixed_percent: Fixed percentage from entry
        - multi_target: First target of multiple targets

        Args:
            signal: Trading signal
            stop_loss: Stop-loss price
            df: DataFrame with OHLCV data

        Returns:
            Take-profit price (primary target)
        """
        import structlog
        logger = structlog.get_logger(__name__)

        entry_price = signal.price
        risk = abs(entry_price - stop_loss)

        logger.info(
            "TP calculation started",
            entry_price=entry_price,
            stop_loss=stop_loss,
            risk=risk,
            tp_mode=self.config.tp_mode,
            tp_risk_ratio=self.config.tp_risk_ratio,
            signal_side="long" if signal.is_long else "short",
        )

        if self.config.tp_mode == "risk_ratio":
            # Risk-reward ratio based
            reward = risk * self.config.tp_risk_ratio

            if signal.is_long:
                tp_price = entry_price + reward
            else:
                tp_price = entry_price - reward

            logger.info(
                "TP calculation - risk_ratio mode",
                reward=reward,
                tp_price=tp_price,
                actual_rr_ratio=reward / risk if risk > 0 else 0,
            )
            return tp_price

        elif self.config.tp_mode == "fixed_percent":
            # Fixed percentage from entry
            if signal.is_long:
                return entry_price * (1 + self.config.tp_fixed_percent / 100)
            else:
                return entry_price * (1 - self.config.tp_fixed_percent / 100)

        elif self.config.tp_mode == "multi_target":
            # Use first target from multi-target list
            if self.config.tp_multi_targets:
                first_target = self.config.tp_multi_targets[0]
                target_ratio = first_target.get("target", 1.5)
                reward = risk * target_ratio

                if signal.is_long:
                    return entry_price + reward
                else:
                    return entry_price - reward

        # Default fallback: 2:1 RR
        reward = risk * 2.0
        if signal.is_long:
            return entry_price + reward
        return entry_price - reward

    def get_multi_targets(
        self,
        signal: Signal,
        stop_loss: float,
    ) -> list[dict]:
        """
        Get multiple take-profit targets for partial closes.

        Args:
            signal: Trading signal
            stop_loss: Stop-loss price

        Returns:
            List of targets with price and percentage to close
        """
        if not self.config.tp_multi_targets:
            return []

        entry_price = signal.price
        risk = abs(entry_price - stop_loss)
        targets = []

        for target_config in self.config.tp_multi_targets:
            target_ratio = target_config.get("target", 1.5)
            close_percent = target_config.get("percent", 33)
            reward = risk * target_ratio

            if signal.is_long:
                price = entry_price + reward
            else:
                price = entry_price - reward

            targets.append({
                "price": price,
                "close_percent": close_percent,
                "risk_ratio": target_ratio,
            })

        return targets

    def should_close_position(
        self,
        position_side: str,
        df: pd.DataFrame,
    ) -> tuple[bool, str]:
        """
        Check if position should be closed based on signal change.

        Args:
            position_side: Current position side ('long' or 'short')
            df: DataFrame with OHLCV data

        Returns:
            Tuple of (should_close, reason)
        """
        triple_st = self._indicators_data.get("triple_supertrend")
        if triple_st is None:
            return False, ""

        st_direction = int(triple_st.combined_direction.iloc[-1])

        if position_side == "long" and st_direction == -1:
            return True, "SuperTrend flipped bearish"

        if position_side == "short" and st_direction == 1:
            return True, "SuperTrend flipped bullish"

        return False, ""

    @property
    def min_candles_required(self) -> int:
        """Minimum candles required for calculation."""
        # Need enough for EMA200 + some buffer
        return max(
            self.config.ema_period + 50 if self.config.ema_enabled else 50,
            self.config.st1_period + 50,
        )

    def update_config(self, **kwargs) -> None:
        """
        Update strategy configuration.

        Args:
            **kwargs: Configuration parameters to update
        """
        if not self.config.allow_custom_params:
            raise ValueError("Custom parameter modification is disabled")

        for key, value in kwargs.items():
            if hasattr(self.config, key):
                setattr(self.config, key, value)

        # Reinitialize indicators if SuperTrend params changed
        st_params = ["st1_period", "st1_multiplier", "st2_period", "st2_multiplier",
                     "st3_period", "st3_multiplier"]
        if any(k in kwargs for k in st_params):
            self._triple_st = TripleSuperTrend(
                st1_period=self.config.st1_period,
                st1_multiplier=self.config.st1_multiplier,
                st2_period=self.config.st2_period,
                st2_multiplier=self.config.st2_multiplier,
                st3_period=self.config.st3_period,
                st3_multiplier=self.config.st3_multiplier,
            )

        # Reinitialize EMA if params changed
        ema_params = ["ema_period", "ema_enabled"]
        if any(k in kwargs for k in ema_params):
            self._ema = EMA(
                period=self.config.ema_period,
                enabled=self.config.ema_enabled,
            )

    def to_dict(self) -> dict:
        """Convert strategy to dictionary."""
        base_dict = super().to_dict()
        base_dict["config"].update({
            "supertrend": {
                "st1": {"period": self.config.st1_period, "multiplier": self.config.st1_multiplier},
                "st2": {"period": self.config.st2_period, "multiplier": self.config.st2_multiplier},
                "st3": {"period": self.config.st3_period, "multiplier": self.config.st3_multiplier},
            },
            "ema": {
                "enabled": self.config.ema_enabled,
                "period": self.config.ema_period,
                "filter_mode": self.config.ema_filter_mode,
            },
            "stop_loss": {
                "mode": self.config.sl_mode,
                "supertrend_line": self.config.sl_supertrend_line,
                "fixed_percent": self.config.sl_fixed_percent,
            },
            "take_profit": {
                "mode": self.config.tp_mode,
                "risk_ratio": self.config.tp_risk_ratio,
                "fixed_percent": self.config.tp_fixed_percent,
            },
            "trailing": {
                "enabled": self.config.trailing_enabled,
                "activation": self.config.trailing_activation,
                "step": self.config.trailing_step,
            },
        })
        return base_dict
