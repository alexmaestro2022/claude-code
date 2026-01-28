"""Agent orchestrator - coordinates all 12 agents in the trading pipeline."""

import asyncio
import logging
import os
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
from .agents.arbitrage import ArbitrageAgent
from .agents.hedge_master import HedgeMasterAgent
from .agents.war_room import WarRoomAgent
from .capital_manager import CapitalManager
from .strategy_evolution import StrategyEvolution
from .testing import Backtester, PaperTrader
from .autopilot_mode import AutopilotMode
from .scaling_manager import ScalingManager
from .exchanges.multi_exchange import MultiExchangeManager
from .exchanges.bybit_exchange import BybitExchange
from .claude_client import ClaudeClient, get_api_usage, set_api_usage_orchestrator
from .knowledge_base import KnowledgeBase
from .market_scanner import MarketScanner
from .risk_manager import RiskManager
from .position_manager import PositionManager
from .learning_cycles import LearningCycles
from .persistence import PersistenceManager
from .telegram_notifier import TelegramNotifier, get_telegram_notifier

logger = logging.getLogger("ai_trade")


class AgentOrchestrator:
    """Coordinates all agents. Flow: TRADER -> REVIEWER -> RISK_GUARD -> execute -> ANALYST."""

    def __init__(self, exchange: Any = None, mode: str = "IDLE") -> None:
        self.mode = mode

        # Create BybitExchange with API keys from .env (ignore passed exchange)
        api_key = os.getenv("BYBIT_API_KEY", "")
        api_secret = os.getenv("BYBIT_API_SECRET", "")
        testnet = os.getenv("BYBIT_TESTNET", "false").lower() == "true"
        self._bybit_exchange = BybitExchange(
            api_key=api_key,
            api_secret=api_secret,
            testnet=testnet,
        )
        self.exchange = self._bybit_exchange

        # Multi-exchange support
        self.exchanges = MultiExchangeManager()
        self.exchanges.add_exchange(self._bybit_exchange)

        self.claude_client = ClaudeClient()
        self.knowledge_base = KnowledgeBase()
        self.scanner = MarketScanner(self._bybit_exchange)
        self.risk_manager = RiskManager()
        self.position_manager = PositionManager(self._bybit_exchange)

        # Telegram notifier for alerts
        self.telegram = get_telegram_notifier()

        self.trader = TraderAgent(self.claude_client, self.knowledge_base, self.scanner, orchestrator=self)
        self.reviewer = ReviewerAgent(self.claude_client, self.knowledge_base)
        self.risk_guard = RiskGuardAgent(self.claude_client, self.knowledge_base, self.risk_manager, orchestrator=self)
        self.analyst = AnalystAgent(self.claude_client, self.knowledge_base)
        self.logger_agent = LoggerAgent(self.claude_client, self.knowledge_base, telegram_bot=self.telegram)
        self.mentor = MentorAgent(self.claude_client, self.knowledge_base)
        self.researcher = ResearcherAgent(self.claude_client, self.knowledge_base, self.scanner)
        self.whale_tracker = WhaleTrackerAgent(self.claude_client, self.knowledge_base, self._bybit_exchange)
        self.news_agent = NewsAgent(self.claude_client, self.knowledge_base)
        self.predictor = PredictorAgent(self.claude_client, self.knowledge_base, self.scanner)
        self.sniper = SniperAgent(self.claude_client, self.knowledge_base, self.scanner)

        self.arbitrage = ArbitrageAgent(self.claude_client, self.knowledge_base, self.exchanges)
        self.capital_manager = CapitalManager(self.knowledge_base)
        self.hedge_master = HedgeMasterAgent(self.claude_client, self.knowledge_base, log_path='/opt/aila/logs/ai_trade/hedge_master.log')
        self.war_room = WarRoomAgent(self.claude_client, self.knowledge_base, log_path='/opt/aila/logs/ai_trade/war_room.log')
        self.strategy_evolution = StrategyEvolution(self.claude_client, self.knowledge_base)
        self.backtester = Backtester(self.claude_client, self.knowledge_base)
        self.paper_trader = PaperTrader(initial_balance=1000)
        self.autopilot = AutopilotMode(self)
        self.scaling_manager = ScalingManager(self.knowledge_base, self.capital_manager)
        self.learning = LearningCycles(self)
        self._persistence = PersistenceManager()

        # Set orchestrator reference for API usage auto-stop
        set_api_usage_orchestrator(self)

        logger.info(f"AgentOrchestrator initialized in {mode} mode (16 agents)")
        if api_key:
            logger.info("Bybit API connected with real credentials")
        else:
            logger.warning("Bybit API: NO CREDENTIALS - paper trading only")

        # Start persistence and initialize capital in background
        asyncio.create_task(self._initialize_async())

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

    async def scan_arbitrage_opportunities(self) -> dict[str, Any]:
        """Scan for arbitrage opportunities across all types."""
        pairs = await self.scanner.get_top_pairs(limit=30)
        pair_symbols = [p["symbol"] for p in pairs]
        return await self.arbitrage.find_all_opportunities(pair_symbols)

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

        if self.mode == "IDLE":
            self.logger_agent.log_event(
                "SYSTEM", "IDLE_MODE",
                data={"pair": opportunity["pair"], "decision": opportunity["decision"]},
                message=f"IDLE mode - not executing {opportunity['decision']} {opportunity['pair']}",
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

    async def analyze_portfolio(self) -> dict[str, Any]:
        """Analyze portfolio risk, capital allocation, and scaling phase."""
        positions = await self.position_manager.check_positions()
        risk_analysis = await self.hedge_master.analyze_portfolio_risk(positions or [])
        allocation = self.capital_manager.get_allocation()
        phase = self.capital_manager.get_scaling_phase()
        return {
            'positions': positions or [],
            'risk': risk_analysis,
            'capital': allocation,
            'phase': phase
        }

    async def calculate_trade_size(self, entry_price: float, stop_loss: float, confidence: int = 50) -> dict:
        """Calculate optimal trade size using Kelly Criterion adjusted by confidence."""
        stats = self.knowledge_base.data
        kelly = self.capital_manager.kelly_criterion(
            win_rate=stats.get('win_rate', 0.5),
            avg_win=stats.get('avg_win', 1),
            avg_loss=stats.get('avg_loss', 1)
        )
        adjusted_risk = kelly * (confidence / 100)
        return self.capital_manager.calculate_position_size(
            entry_price=entry_price,
            stop_loss=stop_loss,
            risk_pct=adjusted_risk * 100
        )

    async def check_market_safety(self) -> dict[str, Any]:
        """Check market safety and activate emergency protocol if needed."""
        market_data = await self.scanner.get_market_overview()
        health = await self.war_room.monitor_market_health(market_data)
        if health['crisis_level'] == 'critical':
            black_swan = await self.war_room.detect_black_swan(market_data)
            if black_swan and black_swan.get('is_black_swan'):
                positions = await self.position_manager.check_positions()
                await self.war_room.activate_emergency_protocol(
                    black_swan['type'], positions or []
                )
        return health

    async def evolve_strategies(self, generations: int = 5) -> dict[str, Any]:
        """Run strategy evolution for specified number of generations."""
        historical = await self.scanner.get_historical_data('BTCUSDT', '1h', 100)
        if not self.strategy_evolution._population:
            base_strategies = [
                {'name': 'EMA_Cross', 'parameters': {'fast': 9, 'slow': 21, 'timeframe': '15m'}},
                {'name': 'RSI_Reversal', 'parameters': {'period': 14, 'oversold': 30, 'overbought': 70}},
                {'name': 'Breakout', 'parameters': {'lookback': 20, 'atr_multiplier': 1.5}}
            ]
            await self.strategy_evolution.initialize_population(base_strategies)
        for _ in range(generations):
            await self.strategy_evolution.evolve_generation(historical)
        return self.strategy_evolution.get_evolution_stats()

    async def start_autopilot(self) -> dict[str, Any]:
        """Start autopilot mode."""
        return await self.autopilot.start()

    async def stop_autopilot(self) -> dict[str, Any]:
        """Stop autopilot mode."""
        return await self.autopilot.stop()

    async def get_scaling_info(self) -> dict[str, Any]:
        """Get comprehensive scaling information."""
        settings = await self.scaling_manager.get_recommended_settings()
        upgrade = await self.scaling_manager.check_upgrade_eligibility()
        projection = await self.scaling_manager.calculate_growth_projection(12)
        return {
            'current_settings': settings,
            'upgrade_eligibility': upgrade,
            'projection_12m': projection,
            'all_phases': self.scaling_manager.get_all_phases()
        }

    async def run_backtest(self, strategy_name: str, params: dict, pair: str = "BTCUSDT") -> dict[str, Any]:
        """Run a backtest for a strategy."""
        result = await self.backtester.run_backtest(
            strategy_name=strategy_name,
            strategy_params=params,
            pair=pair,
            timeframe="1h",
            start_date="2024-01-01",
            end_date="2024-12-31"
        )
        return {
            'strategy_name': result.strategy_name,
            'pair': result.pair,
            'timeframe': result.timeframe,
            'start_date': result.start_date,
            'end_date': result.end_date,
            'initial_balance': result.initial_balance,
            'final_balance': result.final_balance,
            'total_trades': result.total_trades,
            'winning_trades': result.winning_trades,
            'losing_trades': result.losing_trades,
            'win_rate': result.win_rate,
            'profit_factor': result.profit_factor,
            'max_drawdown': result.max_drawdown,
            'sharpe_ratio': result.sharpe_ratio,
            'total_return_pct': result.total_return_pct,
            'trades': result.trades
        }

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
                "arbitrage": {"status": "active", "name": "ARBITRAGE", "exchanges": self.exchanges.count},
                "hedge_master": {"status": "active", "name": "HEDGE_MASTER", "active_hedges": len(self.hedge_master._active_hedges)},
                "war_room": {"status": "active", "name": "WAR_ROOM", "crisis_mode": self.war_room.is_crisis_mode()},
                "capital_manager": {"status": "active", "name": "CAPITAL_MANAGER", "phase": self.capital_manager.get_scaling_phase().get("phase", "unknown")},
                "strategy_evolution": {"status": "active", "name": "STRATEGY_EVOLUTION", "generation": self.strategy_evolution._generation},
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

    # ==================== PERSISTENCE ====================

    async def _collect_all_data(self) -> dict[str, Any]:
        """Collect ALL AI Trade data for persistence."""
        data: dict[str, Any] = {}

        # Knowledge Base
        if hasattr(self.knowledge_base, "data"):
            data["knowledge_base"] = self.knowledge_base.data

        # Trading state
        data["trading_state"] = {
            "mode": self.mode,
            "last_update": datetime.utcnow().isoformat(),
        }

        # Paper Trading
        if hasattr(self, "paper_trader"):
            data["paper_trading"] = {
                "balance": getattr(self.paper_trader, "_balance", 1000),
                "initial_balance": getattr(self.paper_trader, "_initial_balance", 1000),
                "trades": [
                    t.__dict__ if hasattr(t, "__dict__") else t
                    for t in getattr(self.paper_trader, "_trades", [])
                ],
                "positions": [
                    p.__dict__ if hasattr(p, "__dict__") else p
                    for p in getattr(self.paper_trader, "_positions", [])
                ],
            }

        # Evolution
        if hasattr(self, "strategy_evolution"):
            population = getattr(self.strategy_evolution, "_population", [])
            data["evolution"] = {
                "generation": getattr(self.strategy_evolution, "_generation", 0),
                "population": [
                    {
                        "name": getattr(g, "name", ""),
                        "parameters": getattr(g, "parameters", {}),
                        "fitness": getattr(g, "fitness", 0),
                        "generation": getattr(g, "generation", 0),
                    }
                    for g in population
                ],
            }

        # Risk Guard
        if hasattr(self, "risk_guard"):
            data["risk_stats"] = {
                "daily_pnl": getattr(self.risk_guard, "_daily_pnl", 0),
                "daily_trades": getattr(self.risk_guard, "_daily_trades", 0),
                "peak_balance": getattr(self.risk_guard, "_peak_balance", 0),
                "vetoed_count": len(getattr(self.risk_guard, "vetoed_trades", [])),
            }

        # Autopilot
        if hasattr(self, "autopilot"):
            data["autopilot"] = {
                "stats": getattr(self.autopilot, "_stats", {}),
                "config": getattr(self.autopilot, "_config", {}),
                "running": getattr(self.autopilot, "_running", False),
            }

        # War Room
        if hasattr(self, "war_room"):
            data["war_room"] = {
                "alert_history": getattr(self.war_room, "_alert_history", []),
                "crisis_mode": getattr(self.war_room, "_crisis_mode", False),
            }

        # Capital Manager
        if hasattr(self, "capital_manager"):
            data["capital"] = {
                "total": getattr(self.capital_manager, "_total_capital", 0),
                "config": getattr(self.capital_manager, "_config", {}),
            }

        return data

    async def _restore_all_data(self) -> bool:
        """Restore ALL data from disk."""
        try:
            logger.info("Restoring persisted data...")
            restored = 0

            # Knowledge Base
            kb = await self._persistence.load("knowledge_base")
            if kb and hasattr(self.knowledge_base, "data"):
                self.knowledge_base.data.update(kb)
                restored += 1

            # Trading state
            state = await self._persistence.load("trading_state")
            if state:
                # Map old OBSERVER mode to IDLE
                mode = state.get("mode", "IDLE")
                self.mode = "IDLE" if mode == "OBSERVER" else mode
                restored += 1

            # Paper Trading
            paper = await self._persistence.load("paper_trading")
            if paper and hasattr(self, "paper_trader"):
                if hasattr(self.paper_trader, "_balance"):
                    self.paper_trader._balance = paper.get("balance", 1000)
                if hasattr(self.paper_trader, "_initial_balance"):
                    self.paper_trader._initial_balance = paper.get("initial_balance", 1000)
                restored += 1

            # Evolution
            evo = await self._persistence.load("evolution")
            if evo and hasattr(self, "strategy_evolution"):
                if hasattr(self.strategy_evolution, "_generation"):
                    self.strategy_evolution._generation = evo.get("generation", 0)
                restored += 1

            # Risk stats
            risk = await self._persistence.load("risk_stats")
            if risk and hasattr(self, "risk_guard"):
                if hasattr(self.risk_guard, "_daily_pnl"):
                    self.risk_guard._daily_pnl = risk.get("daily_pnl", 0)
                if hasattr(self.risk_guard, "_daily_trades"):
                    self.risk_guard._daily_trades = risk.get("daily_trades", 0)
                if hasattr(self.risk_guard, "_peak_balance"):
                    self.risk_guard._peak_balance = risk.get("peak_balance", 0)
                restored += 1

            # Autopilot
            auto = await self._persistence.load("autopilot")
            if auto and hasattr(self, "autopilot"):
                if hasattr(self.autopilot, "_stats"):
                    self.autopilot._stats.update(auto.get("stats", {}))
                if hasattr(self.autopilot, "_config"):
                    self.autopilot._config.update(auto.get("config", {}))
                restored += 1

            # War Room
            war = await self._persistence.load("war_room")
            if war and hasattr(self, "war_room"):
                if hasattr(self.war_room, "_alert_history"):
                    self.war_room._alert_history = war.get("alert_history", [])
                restored += 1

            logger.info(f"Restored {restored} data components")
            return True
        except Exception as e:
            logger.error(f"Restore error: {e}")
            return False

    async def _initialize_async(self) -> None:
        """Initialize async components: persistence and capital."""
        await self._restore_all_data()
        asyncio.create_task(self._persistence.start_auto_save(self._collect_all_data))

        # Initialize capital_manager with real balance
        try:
            balance = await self._bybit_exchange.get_balance("USDT")
            if balance > 0:
                await self.capital_manager.update_capital(balance)
                logger.info(f"Capital manager initialized with ${balance:.2f}")

                # Update risk_manager level benefits
                level = self.knowledge_base.data.get("trader_profile", {}).get("level", 1)
                self.risk_manager.update_level_limits(level, self.knowledge_base)
                logger.info(f"Risk limits updated for AI level {level}")
        except Exception as e:
            logger.error(f"Failed to initialize capital: {e}")

    async def start_persistence(self) -> None:
        """Start persistence system (legacy method for compatibility)."""
        await self._initialize_async()

    async def save_now(self) -> dict[str, Any]:
        """Force immediate save."""
        data = await self._collect_all_data()
        saved = await self._persistence.save_all(data)
        return {"saved_files": saved, "timestamp": datetime.utcnow().isoformat()}

    async def backup_to_cloud_now(self) -> dict[str, Any]:
        """Force immediate cloud backup."""
        success = await self._persistence.backup_to_cloud()
        return {"success": success, "timestamp": datetime.utcnow().isoformat()}

    def get_persistence_status(self) -> dict[str, Any]:
        """Get persistence system status."""
        return self._persistence.get_status()

    def get_api_usage_stats(self) -> dict[str, Any]:
        """Get Claude API usage statistics for cost monitoring."""
        return get_api_usage()
