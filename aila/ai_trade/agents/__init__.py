"""
AI Trade Agents — Multi-agent trading system.

Agents:
- TraderAgent: Scans market, finds opportunities
- ReviewerAgent: Reviews and validates trade proposals
- RiskGuardAgent: Enforces risk limits, can VETO any trade
- AnalystAgent: Analyzes completed trades, finds patterns
- LoggerAgent: Records all agent actions, sends alerts
"""

from .base_agent import BaseAgent
from .trader import TraderAgent
from .reviewer import ReviewerAgent
from .risk_guard import RiskGuardAgent
from .analyst import AnalystAgent
from .logger_agent import LoggerAgent

__all__ = [
    "BaseAgent",
    "TraderAgent",
    "ReviewerAgent",
    "RiskGuardAgent",
    "AnalystAgent",
    "LoggerAgent",
]
