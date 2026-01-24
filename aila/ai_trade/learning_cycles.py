"""
Automatic learning cycles: after trade, hourly, daily, weekly.
"""

import asyncio
import logging
from datetime import datetime, time, timedelta

logger = logging.getLogger("ai_trade")


class LearningCycles:
    """
    Manages automatic learning cycles for the AI trader.
    - After each trade: immediate analysis and XP
    - Hourly: market regime check
    - Daily (00:00 UTC): mentor review
    - Weekly (Sunday 00:00 UTC): deep training
    """

    def __init__(self, orchestrator):
        self.orchestrator = orchestrator
        self.mentor = orchestrator.mentor
        self.analyst = orchestrator.analyst
        self.researcher = orchestrator.researcher
        self.knowledge_base = orchestrator.knowledge_base
        self.running = False
        self._tasks = []

    async def on_trade_closed(self, trade: dict):
        """Immediate learning after each trade."""
        logger.info(f"Learning from trade: {trade.get('symbol', 'unknown')}")

        # 1. ANALYST analyzes the trade
        analysis = await self.analyst.analyze(trade)

        # 2. Calculate and apply XP
        xp = self._calculate_xp(trade, analysis)
        self.knowledge_base.add_xp(xp["total"], xp.get("skill"))

        # 3. If mistake detected — MENTOR corrects
        mistakes = analysis.get("what_went_wrong", [])
        if mistakes and trade.get("pnl", 0) < 0:
            correction = await self.mentor.correct_mistake({
                "trade": trade,
                "mistakes": mistakes,
                "analysis": analysis,
            })
            if correction.get("new_rule"):
                self.knowledge_base.add_rule({
                    "rule": correction["new_rule"],
                    "source": "trade_correction",
                })

        # 4. Check for streaks
        await self._check_streaks()

        # 5. Check level up
        self.knowledge_base.check_level_up()

        # 6. Log the learning event
        self.orchestrator.logger_agent.log_event(
            agent="LEARNING",
            action="TRADE_LEARNED",
            data={
                "pair": trade.get("symbol"),
                "xp_gained": xp["total"],
                "skill": xp.get("skill"),
                "grade": analysis.get("grade", "C"),
                "level": self.knowledge_base.get_trader_level(),
            },
            message=f"Learned from {trade.get('symbol')}: "
                    f"XP={xp['total']:+d}, Grade={analysis.get('grade', 'C')}, "
                    f"Level={self.knowledge_base.get_trader_level()}"
        )

    async def hourly_cycle(self):
        """Hourly cycle — market regime analysis."""
        while self.running:
            try:
                await asyncio.sleep(3600)  # 1 hour
                if not self.running:
                    break

                logger.info("Hourly learning cycle: analyzing market regime")

                # Get market overview
                market_data = await self.researcher.get_market_overview()
                if market_data:
                    regime = await self.researcher.analyze_market_regime(market_data)
                    if "error" not in regime:
                        self.knowledge_base.update_market_regime(regime)

                        # Log regime change
                        self.orchestrator.logger_agent.log_event(
                            agent="RESEARCHER",
                            action="REGIME_UPDATED",
                            data=regime,
                            message=f"Market regime: {regime.get('regime')} / "
                                    f"{regime.get('direction')} (risk={regime.get('risk_level')})"
                        )

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Hourly cycle error: {e}")
                await asyncio.sleep(60)

    async def daily_cycle(self):
        """Daily cycle at 00:00 UTC — mentor review."""
        while self.running:
            try:
                # Calculate time until next midnight UTC
                now = datetime.utcnow()
                next_midnight = datetime.combine(
                    now.date() + timedelta(days=1), time(0, 0)
                )
                wait_seconds = (next_midnight - now).total_seconds()
                await asyncio.sleep(wait_seconds)

                if not self.running:
                    break

                logger.info("Daily learning cycle: mentor review")

                # Get today's trades
                trades_today = self.knowledge_base.get_today_trades()

                if trades_today:
                    # Mentor reviews the day
                    review = await self.mentor.daily_review(trades_today)
                    if "error" not in review:
                        self.knowledge_base.save_daily_review(review)

                        # Log daily review
                        self.orchestrator.logger_agent.log_event(
                            agent="MENTOR",
                            action="DAILY_REVIEW",
                            data={
                                "grade": review.get("grade"),
                                "trades_count": len(trades_today),
                                "recommendations": review.get("recommendations", []),
                            },
                            message=f"Daily review: Grade={review.get('grade')}, "
                                    f"{len(trades_today)} trades analyzed"
                        )
                else:
                    logger.info("No trades today, skipping daily review")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Daily cycle error: {e}")
                await asyncio.sleep(300)

    async def weekly_cycle(self):
        """Weekly cycle (Sunday 00:00 UTC) — deep training."""
        while self.running:
            try:
                # Calculate time until next Sunday 00:00 UTC
                now = datetime.utcnow()
                days_until_sunday = (6 - now.weekday()) % 7
                if days_until_sunday == 0 and now.hour >= 0:
                    days_until_sunday = 7
                next_sunday = datetime.combine(
                    now.date() + timedelta(days=days_until_sunday),
                    time(0, 0)
                )
                wait_seconds = (next_sunday - now).total_seconds()
                await asyncio.sleep(wait_seconds)

                if not self.running:
                    break

                logger.info("Weekly learning cycle: deep training")

                # Get weekly stats
                weekly_stats = self.knowledge_base.get_weekly_stats()

                if weekly_stats.get("total_trades", 0) > 0:
                    # Mentor weekly training
                    training = await self.mentor.weekly_training(weekly_stats)

                    # Researcher finds patterns
                    all_trades = self.knowledge_base.get_all_trades()
                    patterns = await self.researcher.find_new_patterns(all_trades)

                    # Save results
                    self.knowledge_base.save_weekly_training(training, patterns)

                    # Log weekly training
                    self.orchestrator.logger_agent.log_event(
                        agent="MENTOR",
                        action="WEEKLY_TRAINING",
                        data={
                            "weekly_grade": training.get("weekly_grade"),
                            "trades_count": weekly_stats.get("total_trades"),
                            "pnl": weekly_stats.get("total_pnl"),
                            "patterns_found": len(patterns.get("patterns_discovered", [])),
                        },
                        message=f"Weekly training: Grade={training.get('weekly_grade')}, "
                                f"PnL=${weekly_stats.get('total_pnl', 0):.2f}, "
                                f"{len(patterns.get('patterns_discovered', []))} patterns found"
                    )
                else:
                    logger.info("No trades this week, skipping weekly training")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Weekly cycle error: {e}")
                await asyncio.sleep(300)

    def _calculate_xp(self, trade: dict, analysis: dict) -> dict:
        """Calculate XP for a trade."""
        xp_config = self.knowledge_base.data.get("xp_config", {})
        rewards = xp_config.get("rewards", {})
        penalties = xp_config.get("penalties", {})

        total_xp = 0
        skill = None

        pnl = trade.get("pnl", 0)

        if pnl > 0:
            # Profitable trade
            total_xp += rewards.get("profitable_trade", 10)

            # Check for good entry/exit
            grade = analysis.get("grade", "C")
            if grade == "A":
                total_xp += rewards.get("perfect_entry", 15)
                total_xp += rewards.get("perfect_exit", 15)
                skill = "entry_timing"
            elif grade == "B":
                total_xp += rewards.get("followed_rules", 5)

            # Skill assignment based on strategy
            what_right = analysis.get("what_went_right", [])
            if any("entry" in str(w).lower() for w in what_right):
                skill = "entry_timing"
            elif any("exit" in str(w).lower() for w in what_right):
                skill = "exit_timing"
            elif any("trend" in str(w).lower() for w in what_right):
                skill = "trend_detection"

        else:
            # Losing trade
            total_xp += penalties.get("losing_trade", -5)

            # Check for rule violations
            what_wrong = analysis.get("what_went_wrong", [])
            if what_wrong:
                # Check if it's a repeated mistake
                mistakes_history = self.knowledge_base.data.get(
                    "trader_profile", {}
                ).get("mistakes_history", [])

                for mistake in what_wrong:
                    if any(str(mistake).lower() in str(m).lower() for m in mistakes_history[-10:]):
                        total_xp += penalties.get("repeated_mistake", -30)
                        break

            # Determine affected skill
            if any("entry" in str(w).lower() for w in what_wrong):
                skill = "entry_timing"
            elif any("exit" in str(w).lower() or "late" in str(w).lower() for w in what_wrong):
                skill = "exit_timing"
            elif any("risk" in str(w).lower() or "size" in str(w).lower() for w in what_wrong):
                skill = "risk_management"
            elif any("patient" in str(w).lower() or "early" in str(w).lower() for w in what_wrong):
                skill = "patience"

        return {"total": total_xp, "skill": skill}

    async def _check_streaks(self):
        """Check for win/loss streaks and apply bonus XP."""
        successful = self.knowledge_base.data.get("successful_setups", [])
        failed = self.knowledge_base.data.get("failed_setups", [])

        # Get last N trades in order
        all_trades = sorted(
            successful[-10:] + failed[-10:],
            key=lambda x: x.get("timestamp", ""),
            reverse=True
        )

        if not all_trades:
            return

        # Count current win streak
        win_streak = 0
        for t in all_trades:
            if t.get("pnl", 0) > 0:
                win_streak += 1
            else:
                break

        rewards = self.knowledge_base.data.get("xp_config", {}).get("rewards", {})

        if win_streak >= 5:
            bonus = rewards.get("profitable_streak_5", 50)
            self.knowledge_base.add_xp(bonus)
            logger.info(f"Win streak 5! Bonus XP: +{bonus}")
        elif win_streak >= 3:
            bonus = rewards.get("profitable_streak_3", 30)
            self.knowledge_base.add_xp(bonus)
            logger.info(f"Win streak 3! Bonus XP: +{bonus}")

    def start_all_cycles(self):
        """Start all learning cycles as background tasks."""
        self.running = True
        self._tasks = [
            asyncio.create_task(self.hourly_cycle()),
            asyncio.create_task(self.daily_cycle()),
            asyncio.create_task(self.weekly_cycle()),
        ]
        logger.info("All learning cycles started")

    def stop_all_cycles(self):
        """Stop all learning cycles."""
        self.running = False
        for task in self._tasks:
            task.cancel()
        self._tasks = []
        logger.info("All learning cycles stopped")
