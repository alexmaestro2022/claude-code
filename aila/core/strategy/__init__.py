"""
AILA - Strategy Module

This module provides trading strategy implementations:
- BaseStrategy: Abstract base class for all strategies
- TripleSuperTrendStrategy: Main strategy using Triple SuperTrend + EMA200
- Signal: Trading signal model
"""

from .base import BaseStrategy, StrategyConfig
from .signals import Signal, SignalType
from .triple_supertrend import TripleSuperTrendConfig, TripleSuperTrendStrategy

__all__ = [
    "BaseStrategy",
    "StrategyConfig",
    "Signal",
    "SignalType",
    "TripleSuperTrendConfig",
    "TripleSuperTrendStrategy",
]
