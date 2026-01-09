"""
AILA - Trading Module

This module provides the main trading functionality:
- TradingEngine: Core trading loop and signal processing
- OrderManager: Order lifecycle management
- PositionManager: Position tracking and management
- Executor: Trade execution
"""

from .engine import TradingEngine, TradingEngineConfig
from .executor import TradeExecutor
from .order_manager import OrderManager
from .position_manager import PositionManager

__all__ = [
    "TradingEngine",
    "TradingEngineConfig",
    "OrderManager",
    "PositionManager",
    "TradeExecutor",
]
