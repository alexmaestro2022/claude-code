"""Automatic learning cycles: after trade, hourly, daily, weekly."""

import asyncio
import logging
from datetime import datetime, time, timedelta
from typing import Any, Optional

logger = logging.getLogger("ai_trade")


class LearningCycles:
    """
    Manages automatic learning cycles for the AI trader.
    - After each trade: immediate analysis and XP
    - Hourly: market regime check
    - Daily (00:00 UTC): mentor review
    - Weekly (Sunday 00:00 UTC): deep training
    """

    __slots__ = ("orchestrator", "mentor", "analyst", "researcher", "knowledge_base", "running", "_tasks")

    def __init__(self, orchestrator: Any) -> None:
        self.orchestrator = orchestrator
        self.mentor = orchestrator.mentor
        self.analyst = orchestrator.analyst
        self.researcher = orchestrator.researcher
        self.knowledge_base = orchestrator.knowledge_base
        self.running: bool = False
        self._tasks: list[asyncio.Task] = []

    async def on_trade_closed(self, trade: dict[str, Any]) -> None:
        """Immediate learning after each trade."""
        logger.info(f"Learning from trade: {trade.get('symbol', 'unknown')}")

        analysis = await self.analyst.analyze(trade)
        xp = self._calculate_xp(trade, analysis)
        self.knowledge_base.add_xp(xp["total"], xp.get("skill"))

        if analysis.get("what_went_wrong") and trade.get("pnl", 0) < 0:
            correction = await self.mentor.correct_mistake({
                "trade": trade,
                "mistakes": analysis["what_went_wrong"],
                "analysis": analysis,
            })
            if correction.get("new_rule"):
                self.knowledge_base.add_rule({"rule": correction["new_rule"], "source": "trade_correction"})

        await self._check_streaks()
        self.knowledge_base.check_level_up()

        self.orchestrator.logger_agent.log_event(
            agent="LEARNING", action="TRADE_LEARNED",
            data={
                "pair": trade.get("symbol"), "xp_gained": xp["total"],
                "skill": xp.get("skill"), "grade": analysis.get("grade", "C"),
                "level": self.knowledge_base.get_trader_level(),
            },
            message=f"Learned from {trade.get('symbol')}: XP={xp['total']:+d}, "
                    f"Grade={analysis.get('grade', 'C')}, Level={self.knowledge_base.get_trader_level()}",
        )

    async def hourly_cycle(self) -> None:
        """Hourly market regime analysis."""
        while self.running:
            try:
                await asyncio.sleep(3600)
                if not self.running:
                    break

                market_data = await self.researcher.get_market_overview()
                if market_data:
                    regime = await self.researcher.analyze_market_regime(market_data)
                    if "error" not in regime:
                        self.knowledge_base.update_market_regime(regime)
                        self.orchestrator.logger_agent.log_event(
                            agent="RESEARCHER", action="REGIME_UPDATED", data=regime,
                            message=f"Regime: {regime.get('regime')}/{regime.get('direction')}",
                        )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Hourly cycle error: {e}")
                await asyncio.sleep(60)

    async def daily_cycle(self) -> None:
        """Daily mentor review at 00:00 UTC."""
        while self.running:
            try:
                wait = self._seconds_until_next(hour=0)
                await asyncio.sleep(wait)
                if not self.running:
                    break

                trades_today = self.knowledge_base.get_today_trades()
                if trades_today:
                    review = await self.mentor.daily_review(trades_today)
                    if "error" not in review:
                        self.knowledge_base.save_daily_review(review)
                        self.orchestrator.logger_agent.log_event(
                            agent="MENTOR", action="DAILY_REVIEW",
                            data={"grade": review.get("grade"), "trades": len(trades_today)},
                            message=f"Daily: Grade={review.get('grade')}, {len(trades_today)} trades",
                        )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Daily cycle error: {e}")
                await asyncio.sleep(300)

    async def weekly_cycle(self) -> None:
        """Weekly deep training on Sundays at 00:00 UTC."""
        while self.running:
            try:
                wait = self._seconds_until_sunday()
                await asyncio.sleep(wait)
                if not self.running:
                    break

                weekly_stats = self.knowledge_base.get_weekly_stats()
                if weekly_stats.get("total_trades", 0) > 0:
                    training = await self.mentor.weekly_training(weekly_stats)
                    all_trades = self.knowledge_base.get_all_trades()
                    patterns = await self.researcher.find_new_patterns(all_trades)
                    self.knowledge_base.save_weekly_training(training, patterns)
                    self.orchestrator.logger_agent.log_event(
                        agent="MENTOR", action="WEEKLY_TRAINING",
                        data={"grade": training.get("weekly_grade"), "pnl": weekly_stats.get("total_pnl")},
                        message=f"Weekly: Grade={training.get('weekly_grade')}, "
                                f"PnL=${weekly_stats.get('total_pnl', 0):.2f}",
                    )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Weekly cycle error: {e}")
                await asyncio.sleep(300)

    def start_all_cycles(self) -> None:
        """Start all learning cycles as background tasks."""
        self.running = True
        self._tasks = [
            asyncio.create_task(self.hourly_cycle()),
            asyncio.create_task(self.daily_cycle()),
            asyncio.create_task(self.weekly_cycle()),
        ]
        logger.info("All learning cycles started")

    def stop_all_cycles(self) -> None:
        """Stop all learning cycles."""
        self.running = False
        for task in self._tasks:
            task.cancel()
        self._tasks = []
        logger.info("All learning cycles stopped")

    def _calculate_xp(self, trade: dict[str, Any], analysis: dict[str, Any]) -> dict[str, Any]:
        """Calculate XP for a trade."""
        xp_config = self.knowledge_base.data.get("xp_config", {})
        rewards = xp_config.get("rewards", {})
        penalties = xp_config.get("penalties", {})
        total_xp = 0
        skill: Optional[str] = None
        pnl = trade.get("pnl", 0)

        if pnl > 0:
            total_xp += rewards.get("profitable_trade", 10)
            grade = analysis.get("grade", "C")
            if grade == "A":
                total_xp += rewards.get("perfect_entry", 15) + rewards.get("perfect_exit", 15)
                skill = "entry_timing"
            elif grade == "B":
                total_xp += rewards.get("followed_rules", 5)
            skill = skill or self._detect_skill(analysis.get("what_went_right", []))
        else:
            total_xp += penalties.get("losing_trade", -5)
            what_wrong = analysis.get("what_went_wrong", [])
            if what_wrong:
                history = self.knowledge_base.data.get("trader_profile", {}).get("mistakes_history", [])
                if any(
                    any(str(m).lower() in str(h).lower() for h in history[-10:])
                    for m in what_wrong
                ):
                    total_xp += penalties.get("repeated_mistake", -30)
            skill = self._detect_skill(what_wrong, losing=True)

        return {"total": total_xp, "skill": skill}

    async def _check_streaks(self) -> None:
        """Check for win/loss streaks and apply bonus XP."""
        all_trades = sorted(
            self.knowledge_base.data.get("successful_setups", [])[-10:]
            + self.knowledge_base.data.get("failed_setups", [])[-10:],
            key=lambda x: x.get("timestamp", ""), reverse=True,
        )
        if not all_trades:
            return

        win_streak = 0
        for t in all_trades:
            if t.get("pnl", 0) > 0:
                win_streak += 1
            else:
                break

        rewards = self.knowledge_base.data.get("xp_config", {}).get("rewards", {})
        if win_streak >= 5:
            self.knowledge_base.add_xp(rewards.get("profitable_streak_5", 50))
        elif win_streak >= 3:
            self.knowledge_base.add_xp(rewards.get("profitable_streak_3", 30))

    @staticmethod
    def _detect_skill(factors: list, losing: bool = False) -> Optional[str]:
        """Detect which skill is affected from factors list."""
        text = " ".join(str(f).lower() for f in factors)
        if "entry" in text:
            return "entry_timing"
        if "exit" in text or "late" in text:
            return "exit_timing"
        if "trend" in text:
            return "trend_detection"
        if losing:
            if "risk" in text or "size" in text:
                return "risk_management"
            if "patient" in text or "early" in text:
                return "patience"
        return None

    @staticmethod
    def _seconds_until_next(hour: int = 0) -> float:
        """Calculate seconds until next occurrence of given hour UTC."""
        now = datetime.utcnow()
        target = datetime.combine(now.date() + timedelta(days=1), time(hour, 0))
        return (target - now).total_seconds()

    @staticmethod
    def _seconds_until_sunday() -> float:
        """Calculate seconds until next Sunday 00:00 UTC."""
        now = datetime.utcnow()
        days = (6 - now.weekday()) % 7
        if days == 0 and now.hour >= 0:
            days = 7
        target = datetime.combine(now.date() + timedelta(days=days), time(0, 0))
        return (target - now).total_seconds()
