"""Agent orchestrator - coordinates all 11 agents in the trading pipeline."""

import asyncio
import logging
from datetime import datetime
from typing import Any, Optional

from .agents.trader import TraderAgent
from .agents.reviewer import ReviewerAgent
from .agents.risk_guard import RiskGuardAgent
from .agents.analyst import AnalystAgent
from .agents.logger_agent import LoggerAgent
from .agents.mentor import MentorAgent
from .agents.researcher import ResearcherAgent
from .agents.whale_tracker import WhaleTrackerAgent
from .agents.news_agent import NewsAgent
from .agents.predictor import PredictorAgent
from .agents.sniper import SniperAgent
from .claude_client import ClaudeClient
from .knowledge_base import KnowledgeBase
from .market_scanner import MarketScanner
from .risk_manager import RiskManager
from .position_manager import PositionManager
from .learning_cycles import LearningCycles

logger = logging.getLogger("ai_trade")


class AgentOrchestrator:
    """Coordinates all agents. Flow: TRADER -> REVIEWER -> RISK_GUARD -> execute -> ANALYST."""

    def __init__(self, exchange: Any, mode: str = "OBSERVER") -> None:
        self.exchange = exchange
        self.mode = mode

        self.claude_client = ClaudeClient()
        self.knowledge_base = KnowledgeBase()
        self.scanner = MarketScanner(exchange)
        self.risk_manager = RiskManager()
        self.position_manager = PositionManager(exchange)

        self.trader = TraderAgent(self.claude_client, self.knowledge_base, self.scanner, orchestrator=self)
        self.reviewer = ReviewerAgent(self.claude_client, self.knowledge_base)
        self.risk_guard = RiskGuardAgent(self.claude_client, self.knowledge_base, self.risk_manager)
        self.analyst = AnalystAgent(self.claude_client, self.knowledge_base)
        self.logger_agent = LoggerAgent(self.claude_client, self.knowledge_base)
        self.mentor = MentorAgent(self.claude_client, self.knowledge_base)
        self.researcher = ResearcherAgent(self.claude_client, self.knowledge_base, self.scanner)
        self.whale_tracker = WhaleTrackerAgent(self.claude_client, self.knowledge_base, exchange)
        self.news_agent = NewsAgent(self.claude_client, self.knowledge_base)
        self.predictor = PredictorAgent(self.claude_client, self.knowledge_base, self.scanner)
        self.sniper = SniperAgent(self.claude_client, self.knowledge_base, self.scanner)
        self.learning = LearningCycles(self)

        logger.info(f"AgentOrchestrator initialized in {mode} mode (11 agents)")

    async def get_market_context(self, pair: str) -> dict[str, Any]:
        """Gather whale, news, prediction, and market context in parallel."""
        results = await asyncio.gather(
            self.whale_tracker.get_whale_signal(pair),
            self.news_agent.get_pair_sentiment(pair),
            self.news_agent.get_market_sentiment(),
            self.news_agent.detect_breaking_news(),
            self.predictor.predict_movement(pair),
            self.predictor.detect_reversal(pair),
            return_exceptions=True,
        )

        def _safe(idx: int) -> dict[str, Any]:
            return results[idx] if not isinstance(results[idx], Exception) else {}

        return {
            "whale": _safe(0),
            "news": _safe(1),
            "market": _safe(2),
            "breaking_news": _safe(3),
            "prediction": _safe(4),
            "reversal": _safe(5),
        }

    async def scan_snipe_opportunities(self) -> list[dict[str, Any]]:
        """Scan market for sniper opportunities."""
        pairs = await self.scanner.get_top_pairs()
        pair_symbols = [p["symbol"] for p in pairs[:20]]
        return await self.sniper.scan_for_snipes(pair_symbols)

    async def process_trading_cycle(self) -> dict[str, Any]:
        """Full trading cycle with multi-agent pipeline."""
        cycle_result: dict[str, Any] = {
            "timestamp": datetime.now().isoformat(),
            "mode": self.mode,
            "opportunity": None, "review": None,
            "risk_check": None, "trade_result": None,
            "status": "no_opportunity",
        }

        opportunity = await self.trader.find_opportunity()
        if not opportunity:
            return cycle_result

        cycle_result["opportunity"] = opportunity
        cycle_result["status"] = "opportunity_found"
        self.logger_agent.log_opportunity(opportunity)

        if self.mode == "OBSERVER":
            self.logger_agent.log_event(
                "SYSTEM", "OBSERVER_MODE",
                data={"pair": opportunity["pair"], "decision": opportunity["decision"]},
                message=f"Observer mode - not executing {opportunity['decision']} {opportunity['pair']}",
            )
            return cycle_result

        review = await self.reviewer.review(opportunity)
        cycle_result["review"] = review
        self.logger_agent.log_review(opportunity, review)

        if review.get("decision") == "REJECT":
            cycle_result["status"] = "rejected_by_reviewer"
            return cycle_result

        if review.get("decision") == "MODIFY" and review.get("modified_opportunity"):
            opportunity = review["modified_opportunity"]
            cycle_result["opportunity"] = opportunity

        balance = await self._get_balance()
        risk_check = await self.risk_guard.check(opportunity, balance)
        cycle_result["risk_check"] = risk_check
        self.logger_agent.log_risk_check(opportunity, risk_check)

        if not risk_check.get("approved"):
            cycle_result["status"] = "vetoed_by_risk_guard"
            return cycle_result

        if risk_check.get("signal"):
            opportunity = risk_check["signal"]

        if self.mode == "ADVISOR":
            self.logger_agent.log_event(
                "SYSTEM", "ADVISOR_SUGGESTION", data=opportunity,
                message=f"Suggestion: {opportunity['decision']} {opportunity['pair']}",
            )
            cycle_result["status"] = "awaiting_approval"
        elif self.mode == "AUTOPILOT":
            result = await self._execute_trade(opportunity)
            cycle_result["trade_result"] = result
            if result:
                cycle_result["status"] = "trade_executed"
                await self.logger_agent.log_trade_opened(opportunity, result)
            else:
                cycle_result["status"] = "execution_failed"

        return cycle_result

    async def process_completed_trade(self, trade_result: dict[str, Any]) -> None:
        """Process a completed trade through learning system."""
        await self.logger_agent.log_trade_closed(trade_result)
        await self.learning.on_trade_closed(trade_result)
        self.risk_manager.record_trade_result(trade_result.get("pnl", 0))

    def start_learning_cycles(self) -> None:
        """Start background learning cycles."""
        self.learning.start_all_cycles()

    def stop_learning_cycles(self) -> None:
        """Stop background learning cycles."""
        self.learning.stop_all_cycles()

    async def monitor_positions(self) -> None:
        """Risk guard monitors all open positions."""
        positions = await self.position_manager.check_positions()
        if not positions:
            return

        balance = await self._get_balance()
        force_close_list = await self.risk_guard.monitor_positions(positions, balance)

        for close_order in force_close_list:
            symbol = close_order["symbol"]
            self.logger_agent.log_force_close(symbol, close_order["reason"])
            if self.mode == "AUTOPILOT":
                result = await self.position_manager.close_position(symbol, reason=f"risk_guard: {close_order['reason']}")
                if result:
                    self.risk_manager.on_position_closed()
                    await self.process_completed_trade(result)

    async def approve_suggestion(self, opportunity: dict[str, Any]) -> dict[str, Any]:
        """Execute a trade after user approval in ADVISOR mode."""
        if self.mode != "ADVISOR":
            return {"error": "Not in ADVISOR mode"}
        result = await self._execute_trade(opportunity)
        if result:
            await self.logger_agent.log_trade_opened(opportunity, result)
            return {"status": "executed", "result": result}
        return {"status": "execution_failed"}

    async def _execute_trade(self, opportunity: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Execute a trade through position manager."""
        balance = await self._get_balance()
        opportunity["position_size_usdt"] = balance * (opportunity.get("position_size_pct", 2) / 100)
        result = await self.position_manager.open_position(opportunity)
        if result:
            self.risk_manager.on_position_opened()
        return result

    async def _get_balance(self) -> float:
        """Get current USDT balance."""
        try:
            balance = await self.exchange.fetch_balance()
            return balance.get("USDT", {}).get("free", 0)
        except Exception as e:
            logger.error(f"Error fetching balance: {e}")
            return 0

    def get_status(self) -> dict[str, Any]:
        """Get orchestrator status for web interface."""
        profile = self.knowledge_base.data.get("trader_profile", {})
        regime = self.knowledge_base.data.get("market_regime", {})

        return {
            "mode": self.mode,
            "agents": {
                "trader": {"status": "active", "name": "TRADER"},
                "reviewer": {"status": "active", "name": "REVIEWER"},
                "risk_guard": {"status": "active", "name": "RISK_GUARD", "vetoed_count": len(self.risk_guard.vetoed_trades)},
                "analyst": {"status": "active", "name": "ANALYST"},
                "mentor": {"status": "active", "name": "MENTOR"},
                "researcher": {"status": "active", "name": "RESEARCHER", "current_regime": regime.get("regime", "unknown")},
                "whale_tracker": {"status": "active", "name": "WHALE_TRACKER"},
                "news": {"status": "active", "name": "NEWS"},
                "predictor": {"status": "active", "name": "PREDICTOR"},
                "sniper": {"status": "active", "name": "SNIPER", "pending": len(self.sniper._pending_snipes)},
                "logger": {"status": "active", "name": "LOGGER", "events_count": len(self.logger_agent.events)},
            },
            "trader_profile": {
                "level": profile.get("level", 1),
                "xp": profile.get("experience_points", 0),
                "next_level_xp": profile.get("next_level_xp", 100),
                "skills": profile.get("skills", {}),
                "rules_count": len(profile.get("learned_rules", [])),
            },
            "market_regime": regime,
            "level_benefits": self.knowledge_base.get_level_benefits(),
            "risk_status": self.risk_manager.get_status(),
            "open_positions": self.position_manager.get_open_count(),
            "knowledge": {
                "total_trades": self.knowledge_base.data["total_trades"],
                "win_rate": self.knowledge_base.data["win_rate"],
                "total_pnl": self.knowledge_base.data["total_pnl"],
            },
            "learning_cycles": {"running": self.learning.running},
        }

    def get_events(self, limit: int = 50, agent: Optional[str] = None) -> list[dict[str, Any]]:
        """Get recent events for web interface."""
        return self.logger_agent.get_recent_events(limit, agent)
