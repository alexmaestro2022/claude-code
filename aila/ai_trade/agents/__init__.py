"""
AI Trade Agents — Multi-agent trading system (12 agents).

Trading Pipeline:
- TraderAgent: Scans market, finds opportunities
- ReviewerAgent: Reviews and validates trade proposals
- RiskGuardAgent: Enforces risk limits, can VETO any trade

Intelligence:
- WhaleTrackerAgent: Monitors whale transactions and exchange flows
- NewsAgent: Monitors news, sentiment, breaking events
- PredictorAgent: Predicts price movements using TA + AI
- SniperAgent: Instant entries on breakouts, liquidations, funding flips
- ArbitrageAgent: Finds arbitrage opportunities (funding, cross-exchange, triangular)
- HedgeMasterAgent: Portfolio protection and risk hedging
- WarRoomAgent: Crisis management and black swan protection

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
from .predictor import PredictorAgent
from .sniper import SniperAgent
from .arbitrage import ArbitrageAgent
from .hedge_master import HedgeMasterAgent
from .war_room import WarRoomAgent

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
    "PredictorAgent",
    "SniperAgent",
    "ArbitrageAgent",
    "HedgeMasterAgent",
    "WarRoomAgent",
]
