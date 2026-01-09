"""
AILA - Core Indicators Module

This module provides technical indicators for the trading strategy:
- ATR (Average True Range)
- EMA (Exponential Moving Average)
- SuperTrend
"""

from .atr import ATR, calculate_atr, calculate_true_range
from .ema import EMA, calculate_ema
from .supertrend import SuperTrend, calculate_supertrend

__all__ = [
    "ATR",
    "EMA",
    "SuperTrend",
    "calculate_atr",
    "calculate_ema",
    "calculate_supertrend",
    "calculate_true_range",
]
