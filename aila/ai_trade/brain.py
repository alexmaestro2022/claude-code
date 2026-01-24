import logging
import asyncio
from datetime import datetime
from .config import MODES, RISK_LIMITS, LOG_PATH
from .claude_client import ClaudeClient
from .knowledge_base import KnowledgeBase
from .market_scanner import MarketScanner
from .risk_manager import RiskManager
from .position_manager import PositionManager
from .learning_engine import LearningEngine
from .performance_tracker import PerformanceTracker

logger = logging.getLogger("ai_trade")


class AIBrain:
    """Main AI trader brain - orchestrates all components."""

    def __init__(self, exchange, mode: str = "OBSERVER"):
        self.exchange = exchange
        self.mode = mode
        self.mode_config = MODES.get(mode, MODES["OBSERVER"])
        self.running = False

        # Initialize components
        self.claude = ClaudeClient()
        self.kb = KnowledgeBase()
        self.scanner = MarketScanner(exchange)
        self.risk_manager = RiskManager()
        self.position_manager = PositionManager(exchange)
        self.learning_engine = LearningEngine(self.claude, self.kb)
        self.performance_tracker = PerformanceTracker(self.kb)

        # Setup logging
        self._setup_logging()

        logger.info(f"AI Brain initialized in {mode} mode")

    def _setup_logging(self):
        """Configure file logging for AI trade module."""
        handler = logging.FileHandler(LOG_PATH)
        handler.setLevel(logging.INFO)
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

    def set_mode(self, mode: str):
        """Change operating mode."""
        if mode not in MODES:
            logger.error(f"Unknown mode: {mode}")
            return False

        self.mode = mode
        self.mode_config = MODES[mode]
        logger.info(f"Mode changed to: {mode} - {self.mode_config['description']}")
        return True

    async def start(self):
        """Start the AI trading loop."""
        self.running = True
        logger.info(f"AI Brain started in {self.mode} mode")

        while self.running:
            try:
                await self._trading_cycle()
                await asyncio.sleep(60)  # Wait between cycles
            except Exception as e:
                logger.error(f"Trading cycle error: {e}")
                await asyncio.sleep(30)

    async def stop(self):
        """Stop the AI trading loop."""
        self.running = False
        logger.info("AI Brain stopped")

    async def _trading_cycle(self):
        """One full trading cycle: scan -> analyze -> decide -> execute."""
        # 1. Check open positions
        if self.position_manager.get_open_count() > 0:
            await self._manage_open_positions()

        # 2. Scan market for opportunities
        pairs = await self.scanner.get_top_pairs()
        if not pairs:
            return

        # 3. Analyze top pairs
        for pair_info in pairs[:5]:  # Analyze top 5
            symbol = pair_info["symbol"]

            # Skip if already have position
            if symbol in self.position_manager.open_positions:
                continue

            # Get detailed market data
            market_data = await self.scanner.get_market_data(symbol)
            if not market_data:
                continue

            # Get AI analysis
            knowledge = self.kb.get_context_for_analysis(symbol)
            analysis = await self.claude.get_market_analysis(symbol, market_data, knowledge)

            if "error" in analysis:
                continue

            decision = analysis.get("decision", "WAIT")
            confidence = analysis.get("confidence", 0)

            logger.info(f"Analysis {symbol}: {decision} (confidence={confidence}%)")

            # Only act on high-confidence signals
            if decision == "WAIT" or confidence < 70:
                continue

            # 4. Risk check
            analysis["pair"] = symbol
            risk_check = self.risk_manager.validate_trade(analysis, await self._get_balance())

            if not risk_check["approved"]:
                logger.info(f"Trade rejected by risk manager: {risk_check['reason']}")
                continue

            # 5. Execute based on mode
            if self.mode_config.get("can_trade"):
                await self._execute_trade(risk_check["signal"])
            else:
                logger.info(f"Signal generated (mode={self.mode}): {decision} {symbol} @ confidence={confidence}%")

    async def _manage_open_positions(self):
        """Check and manage open positions."""
        positions = await self.position_manager.check_positions()

        for pos in positions:
            # Check if should close based on PnL
            unrealized_pnl = pos.get("unrealized_pnl_pct", 0)

            # Emergency stop: close if loss exceeds 50% of position
            if unrealized_pnl <= -50:
                logger.warning(f"Emergency close {pos['symbol']}: PnL={unrealized_pnl:.1f}%")
                result = await self.position_manager.close_position(pos["symbol"], reason="emergency_stop")
                if result:
                    self.risk_manager.on_position_closed()
                    self.risk_manager.record_trade_result(result["pnl"])
                    self.performance_tracker.record_trade(result)
                    await self.learning_engine.learn_from_trade(result)

    async def _execute_trade(self, signal: dict):
        """Execute a trade signal."""
        result = await self.position_manager.open_position(signal)
        if result:
            self.risk_manager.on_position_opened()
            logger.info(f"Trade executed: {signal['decision']} {signal['pair']}")

    async def _get_balance(self) -> float:
        """Get current USDT balance."""
        try:
            balance = await self.exchange.fetch_balance()
            return balance.get("USDT", {}).get("free", 0)
        except Exception as e:
            logger.error(f"Error fetching balance: {e}")
            return 0

    def get_status(self) -> dict:
        """Get current AI brain status."""
        return {
            "mode": self.mode,
            "mode_description": self.mode_config["description"],
            "running": self.running,
            "open_positions": self.position_manager.get_open_count(),
            "risk_status": self.risk_manager.get_status(),
            "daily_report": self.performance_tracker.get_daily_report(),
            "overall_report": self.performance_tracker.get_overall_report(),
            "knowledge_stats": {
                "total_trades": self.kb.data["total_trades"],
                "win_rate": self.kb.data["win_rate"],
                "total_pnl": self.kb.data["total_pnl"],
                "mistakes_logged": len(self.kb.data["mistakes_to_avoid"]),
                "strategies_tracked": len(self.kb.data["best_strategies"]),
            }
        }
