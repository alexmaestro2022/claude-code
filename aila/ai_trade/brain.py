"""Main AI trader brain - uses multi-agent orchestrator."""

import asyncio
import logging
from typing import Any, Optional

from .config import MODES, LOG_PATH, SCANNER_CONFIG
from .orchestrator import AgentOrchestrator
from .performance_tracker import PerformanceTracker

logger = logging.getLogger("ai_trade")


class AIBrain:
    """Main AI trader brain. Modes: OBSERVER, ADVISOR, AUTOPILOT."""

    __slots__ = ("exchange", "mode", "_mode_config", "running", "orchestrator", "performance_tracker")

    def __init__(self, exchange: Any, mode: str = "OBSERVER") -> None:
        self.exchange = exchange
        self.mode = mode
        self._mode_config = MODES.get(mode, MODES["OBSERVER"])
        self.running = False

        self._setup_logging()
        self.orchestrator = AgentOrchestrator(exchange, mode)
        self.performance_tracker = PerformanceTracker(self.orchestrator.knowledge_base)
        logger.info(f"AI Brain initialized in {mode} mode (multi-agent)")

    def set_mode(self, mode: str) -> bool:
        """Change operating mode."""
        if mode not in MODES:
            logger.error(f"Unknown mode: {mode}")
            return False
        self.mode = mode
        self._mode_config = MODES[mode]
        self.orchestrator.mode = mode
        logger.info(f"Mode changed to: {mode}")
        return True

    async def start(self) -> None:
        """Start the AI trading loop."""
        self.running = True
        self.orchestrator.start_learning_cycles()
        logger.info(f"AI Brain started in {self.mode} mode")

        while self.running:
            try:
                await asyncio.gather(
                    self._trading_cycle(),
                    self._position_monitoring(),
                )
                await asyncio.sleep(SCANNER_CONFIG["scan_interval_seconds"])
            except Exception as e:
                logger.error(f"Trading cycle error: {e}")
                await asyncio.sleep(30)

    async def stop(self) -> None:
        """Stop the AI trading loop."""
        self.running = False
        self.orchestrator.stop_learning_cycles()
        logger.info("AI Brain stopped")

    async def approve_trade(self, opportunity: dict[str, Any]) -> dict[str, Any]:
        """Approve a trade suggestion in ADVISOR mode."""
        result = await self.orchestrator.approve_suggestion(opportunity)
        if result.get("status") == "executed":
            self.performance_tracker.record_trade(result["result"])
        return result

    async def force_close(self, symbol: str) -> dict[str, Any]:
        """Manually force close a position."""
        result = await self.orchestrator.position_manager.close_position(symbol, reason="manual_close")
        if result:
            self.orchestrator.risk_manager.on_position_closed()
            await self.orchestrator.process_completed_trade(result)
            self.performance_tracker.record_trade(result)
            return {"status": "closed", "result": result}
        return {"status": "error", "message": f"No open position for {symbol}"}

    async def run_analysis(self) -> dict[str, Any]:
        """Manually trigger pattern analysis."""
        return await self.orchestrator.analyst.find_patterns()

    def get_status(self) -> dict[str, Any]:
        """Get current AI brain status."""
        return {
            "mode": self.mode,
            "mode_description": self._mode_config["description"],
            "running": self.running,
            "agents": self.orchestrator.get_status(),
            "daily_report": self.performance_tracker.get_daily_report(),
            "overall_report": self.performance_tracker.get_overall_report(),
        }

    def get_events(self, limit: int = 50, agent: Optional[str] = None) -> list[dict[str, Any]]:
        """Get recent agent events for web interface."""
        return self.orchestrator.get_events(limit, agent)

    async def _trading_cycle(self) -> None:
        """One full trading cycle using multi-agent pipeline."""
        if self.orchestrator.position_manager.get_open_count() >= 3:
            return

        cycle_result = await self.orchestrator.process_trading_cycle()
        status = cycle_result.get("status", "no_opportunity")

        if status == "trade_executed":
            trade = cycle_result.get("trade_result")
            if trade:
                self.performance_tracker.record_trade(trade)
        logger.info(f"Cycle complete: {status}")

    async def _position_monitoring(self) -> None:
        """Monitor open positions via RISK_GUARD."""
        if self.orchestrator.position_manager.get_open_count() == 0:
            return
        await self.orchestrator.monitor_positions()

    @staticmethod
    def _setup_logging() -> None:
        """Configure file logging for AI trade module."""
        if logger.handlers:
            return
        handler = logging.FileHandler(LOG_PATH)
        handler.setLevel(logging.INFO)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S",
        ))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
