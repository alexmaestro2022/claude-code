"""
AI Trade Agents — Multi-agent trading system.

Agents:
- TraderAgent: Scans market, finds opportunities
- ReviewerAgent: Reviews and validates trade proposals
- RiskGuardAgent: Enforces risk limits, can VETO any trade
- AnalystAgent: Analyzes completed trades, finds patterns
- LoggerAgent: Records all agent actions, sends alerts
- MentorAgent: Trains TRADER, daily reviews, rule formation
- ResearcherAgent: Discovers market regimes and new patterns
"""

from .base_agent import BaseAgent
from .trader import TraderAgent
from .reviewer import ReviewerAgent
from .risk_guard import RiskGuardAgent
from .analyst import AnalystAgent
from .logger_agent import LoggerAgent
from .mentor import MentorAgent
from .researcher import ResearcherAgent

__all__ = [
    "BaseAgent",
    "TraderAgent",
    "ReviewerAgent",
    "RiskGuardAgent",
    "AnalystAgent",
    "LoggerAgent",
    "MentorAgent",
    "ResearcherAgent",
]
