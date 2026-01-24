"""
AI Trade Agents — Multi-agent trading system (9 agents).

Trading Pipeline:
- TraderAgent: Scans market, finds opportunities
- ReviewerAgent: Reviews and validates trade proposals
- RiskGuardAgent: Enforces risk limits, can VETO any trade

Intelligence:
- WhaleTrackerAgent: Monitors whale transactions and exchange flows
- NewsAgent: Monitors news, sentiment, breaking events

Learning:
- AnalystAgent: Analyzes completed trades, finds patterns
- MentorAgent: Trains TRADER, daily reviews, rule formation
- ResearcherAgent: Discovers market regimes and new patterns

Infrastructure:
- LoggerAgent: Records all agent actions, sends alerts
"""

from .base_agent import BaseAgent
from .trader import TraderAgent
from .reviewer import ReviewerAgent
from .risk_guard import RiskGuardAgent
from .analyst import AnalystAgent
from .logger_agent import LoggerAgent
from .mentor import MentorAgent
from .researcher import ResearcherAgent
from .whale_tracker import WhaleTrackerAgent
from .news_agent import NewsAgent

__all__ = [
    "BaseAgent",
    "TraderAgent",
    "ReviewerAgent",
    "RiskGuardAgent",
    "AnalystAgent",
    "LoggerAgent",
    "MentorAgent",
    "ResearcherAgent",
    "WhaleTrackerAgent",
    "NewsAgent",
]
