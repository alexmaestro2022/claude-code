import logging
import asyncio
from datetime import datetime
from .config import MODES, LOG_PATH, SCANNER_CONFIG
from .orchestrator import AgentOrchestrator
from .performance_tracker import PerformanceTracker

logger = logging.getLogger("ai_trade")


class AIBrain:
    """
    Main AI trader brain — uses multi-agent orchestrator.
    Modes: OBSERVER, ADVISOR, AUTOPILOT
    """

    def __init__(self, exchange, mode: str = "OBSERVER"):
        self.exchange = exchange
        self.mode = mode
        self.mode_config = MODES.get(mode, MODES["OBSERVER"])
        self.running = False

        # Setup logging first
        self._setup_logging()

        # Multi-agent orchestrator
        self.orchestrator = AgentOrchestrator(exchange, mode)

        # Performance tracker (uses orchestrator's knowledge base)
        self.performance_tracker = PerformanceTracker(self.orchestrator.knowledge_base)

        logger.info(f"AI Brain initialized in {mode} mode (multi-agent)")

    def _setup_logging(self):
        """Configure file logging for AI trade module."""
        handler = logging.FileHandler(LOG_PATH)
        handler.setLevel(logging.INFO)
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        handler.setFormatter(formatter)

        if not logger.handlers:
            logger.addHandler(handler)
        logger.setLevel(logging.INFO)

    def set_mode(self, mode: str) -> bool:
        """Change operating mode."""
        if mode not in MODES:
            logger.error(f"Unknown mode: {mode}")
            return False

        self.mode = mode
        self.mode_config = MODES[mode]
        self.orchestrator.mode = mode
        logger.info(f"Mode changed to: {mode} - {self.mode_config['description']}")
        return True

    async def start(self):
        """Start the AI trading loop."""
        self.running = True
        logger.info(f"AI Brain started in {self.mode} mode")

        # Run trading cycle and position monitoring concurrently
        while self.running:
            try:
                tasks = [
                    self._trading_cycle(),
                    self._position_monitoring(),
                ]
                await asyncio.gather(*tasks)
                await asyncio.sleep(SCANNER_CONFIG["scan_interval_seconds"])
            except Exception as e:
                logger.error(f"Trading cycle error: {e}")
                await asyncio.sleep(30)

    async def stop(self):
        """Stop the AI trading loop."""
        self.running = False
        logger.info("AI Brain stopped")

    async def _trading_cycle(self):
        """
        One full trading cycle using multi-agent pipeline:
        TRADER → REVIEWER → RISK_GUARD → execute → ANALYST
        """
        # Skip if already at max positions
        if self.orchestrator.position_manager.get_open_count() >= 3:
            return

        cycle_result = await self.orchestrator.process_trading_cycle()

        status = cycle_result.get("status", "no_opportunity")
        if status == "trade_executed":
            trade = cycle_result.get("trade_result")
            if trade:
                self.performance_tracker.record_trade(trade)

        logger.info(f"Cycle complete: {status}")

    async def _position_monitoring(self):
        """Monitor open positions via RISK_GUARD."""
        if self.orchestrator.position_manager.get_open_count() == 0:
            return

        await self.orchestrator.monitor_positions()

        # Check for closed positions
        positions = await self.orchestrator.position_manager.check_positions()
        for pos in positions:
            # If position was closed by SL/TP on exchange
            # (detected by absence in next check)
            pass

    async def approve_trade(self, opportunity: dict) -> dict:
        """Approve a trade suggestion in ADVISOR mode."""
        result = await self.orchestrator.approve_suggestion(opportunity)
        if result.get("status") == "executed":
            self.performance_tracker.record_trade(result["result"])
        return result

    async def force_close(self, symbol: str) -> dict:
        """Manually force close a position."""
        result = await self.orchestrator.position_manager.close_position(
            symbol, reason="manual_close"
        )
        if result:
            self.orchestrator.risk_manager.on_position_closed()
            await self.orchestrator.process_completed_trade(result)
            self.performance_tracker.record_trade(result)
            return {"status": "closed", "result": result}
        return {"status": "error", "message": f"No open position for {symbol}"}

    async def run_analysis(self):
        """Manually trigger pattern analysis."""
        return await self.orchestrator.analyst.find_patterns()

    def get_status(self) -> dict:
        """Get current AI brain status."""
        return {
            "mode": self.mode,
            "mode_description": self.mode_config["description"],
            "running": self.running,
            "agents": self.orchestrator.get_status(),
            "daily_report": self.performance_tracker.get_daily_report(),
            "overall_report": self.performance_tracker.get_overall_report(),
        }

    def get_events(self, limit: int = 50, agent: str = None) -> list:
        """Get recent agent events for web interface."""
        return self.orchestrator.get_events(limit, agent)
