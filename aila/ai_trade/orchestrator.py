import logging
import asyncio
from datetime import datetime
from .agents.trader import TraderAgent
from .agents.reviewer import ReviewerAgent
from .agents.risk_guard import RiskGuardAgent
from .agents.analyst import AnalystAgent
from .agents.logger_agent import LoggerAgent
from .claude_client import ClaudeClient
from .knowledge_base import KnowledgeBase
from .market_scanner import MarketScanner
from .risk_manager import RiskManager
from .position_manager import PositionManager

logger = logging.getLogger("ai_trade")


class AgentOrchestrator:
    """
    Coordinates all agents in the trading pipeline.
    Flow: TRADER → REVIEWER → RISK_GUARD → execution → ANALYST
    """

    def __init__(self, exchange, mode: str = "OBSERVER"):
        self.exchange = exchange
        self.mode = mode

        # Core components
        self.claude_client = ClaudeClient()
        self.knowledge_base = KnowledgeBase()
        self.scanner = MarketScanner(exchange)
        self.risk_manager = RiskManager()
        self.position_manager = PositionManager(exchange)

        # Agents
        self.trader = TraderAgent(
            self.claude_client, self.knowledge_base, self.scanner
        )
        self.reviewer = ReviewerAgent(
            self.claude_client, self.knowledge_base
        )
        self.risk_guard = RiskGuardAgent(
            self.claude_client, self.knowledge_base, self.risk_manager
        )
        self.analyst = AnalystAgent(
            self.claude_client, self.knowledge_base
        )
        self.logger_agent = LoggerAgent(
            self.claude_client, self.knowledge_base
        )

        logger.info(f"AgentOrchestrator initialized in {mode} mode")

    async def process_trading_cycle(self) -> dict:
        """
        Full trading cycle with multi-agent pipeline.
        Returns cycle result with all agent decisions.
        """
        cycle_result = {
            "timestamp": datetime.now().isoformat(),
            "mode": self.mode,
            "opportunity": None,
            "review": None,
            "risk_check": None,
            "trade_result": None,
            "status": "no_opportunity",
        }

        # 1. TRADER finds opportunity
        opportunity = await self.trader.find_opportunity()

        if not opportunity:
            return cycle_result

        cycle_result["opportunity"] = opportunity
        cycle_result["status"] = "opportunity_found"
        self.logger_agent.log_opportunity(opportunity)

        # In OBSERVER mode, just log and return
        if self.mode == "OBSERVER":
            self.logger_agent.log_event(
                "SYSTEM", "OBSERVER_MODE",
                data={"pair": opportunity["pair"], "decision": opportunity["decision"]},
                message=f"Observer mode — not executing {opportunity['decision']} {opportunity['pair']}"
            )
            return cycle_result

        # 2. REVIEWER checks the proposal
        review = await self.reviewer.review(opportunity)
        cycle_result["review"] = review
        self.logger_agent.log_review(opportunity, review)

        if review.get("decision") == "REJECT":
            cycle_result["status"] = "rejected_by_reviewer"
            return cycle_result

        # Apply modifications if any
        if review.get("decision") == "MODIFY" and review.get("modified_opportunity"):
            opportunity = review["modified_opportunity"]
            cycle_result["opportunity"] = opportunity

        # 3. RISK_GUARD final check
        balance = await self._get_balance()
        risk_check = await self.risk_guard.check(opportunity, balance)
        cycle_result["risk_check"] = risk_check
        self.logger_agent.log_risk_check(opportunity, risk_check)

        if not risk_check.get("approved"):
            cycle_result["status"] = "vetoed_by_risk_guard"
            return cycle_result

        # Use adjusted signal from risk guard
        if risk_check.get("signal"):
            opportunity = risk_check["signal"]

        # 4. Execute based on mode
        if self.mode == "ADVISOR":
            # In ADVISOR mode, log suggestion but don't execute
            self.logger_agent.log_event(
                "SYSTEM", "ADVISOR_SUGGESTION",
                data=opportunity,
                message=f"Suggestion: {opportunity['decision']} {opportunity['pair']} "
                        f"@ confidence={opportunity['confidence']}% — awaiting approval"
            )
            cycle_result["status"] = "awaiting_approval"
            return cycle_result

        elif self.mode == "AUTOPILOT":
            # Execute the trade
            result = await self._execute_trade(opportunity)
            cycle_result["trade_result"] = result
            if result:
                cycle_result["status"] = "trade_executed"
                await self.logger_agent.log_trade_opened(opportunity, result)
            else:
                cycle_result["status"] = "execution_failed"

        return cycle_result

    async def process_completed_trade(self, trade_result: dict):
        """Process a completed trade through ANALYST."""
        # 1. Log trade closure
        await self.logger_agent.log_trade_closed(trade_result)

        # 2. ANALYST analyzes the trade
        analysis = await self.analyst.analyze(trade_result)
        self.logger_agent.log_analysis(trade_result, analysis)

        # 3. Update risk manager
        self.risk_manager.record_trade_result(trade_result.get("pnl", 0))

        return analysis

    async def monitor_positions(self):
        """Risk guard monitors all open positions."""
        positions = await self.position_manager.check_positions()
        if not positions:
            return

        balance = await self._get_balance()

        # Risk guard checks for violations
        force_close_list = await self.risk_guard.monitor_positions(positions, balance)

        for close_order in force_close_list:
            symbol = close_order["symbol"]
            reason = close_order["reason"]

            self.logger_agent.log_force_close(symbol, reason)

            # Force close if in AUTOPILOT mode
            if self.mode == "AUTOPILOT":
                result = await self.position_manager.close_position(symbol, reason=f"risk_guard: {reason}")
                if result:
                    self.risk_manager.on_position_closed()
                    await self.process_completed_trade(result)

    async def approve_suggestion(self, opportunity: dict) -> dict:
        """
        User approves a suggestion in ADVISOR mode.
        Executes the trade after approval.
        """
        if self.mode != "ADVISOR":
            return {"error": "Not in ADVISOR mode"}

        result = await self._execute_trade(opportunity)
        if result:
            await self.logger_agent.log_trade_opened(opportunity, result)
            return {"status": "executed", "result": result}

        return {"status": "execution_failed"}

    async def _execute_trade(self, opportunity: dict) -> dict:
        """Execute a trade through position manager."""
        # Calculate position size in USDT
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

    def get_status(self) -> dict:
        """Get orchestrator status for web interface."""
        return {
            "mode": self.mode,
            "agents": {
                "trader": {"status": "active", "name": "TRADER"},
                "reviewer": {"status": "active", "name": "REVIEWER"},
                "risk_guard": {
                    "status": "active",
                    "name": "RISK_GUARD",
                    "vetoed_count": len(self.risk_guard.vetoed_trades),
                },
                "analyst": {"status": "active", "name": "ANALYST"},
                "logger": {
                    "status": "active",
                    "name": "LOGGER",
                    "events_count": len(self.logger_agent.events),
                },
            },
            "risk_status": self.risk_manager.get_status(),
            "open_positions": self.position_manager.get_open_count(),
            "knowledge": {
                "total_trades": self.knowledge_base.data["total_trades"],
                "win_rate": self.knowledge_base.data["win_rate"],
                "total_pnl": self.knowledge_base.data["total_pnl"],
            },
        }

    def get_events(self, limit: int = 50, agent: str = None) -> list:
        """Get recent events for web interface."""
        return self.logger_agent.get_recent_events(limit, agent)
