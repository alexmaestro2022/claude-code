import logging
from datetime import datetime
from .claude_client import ClaudeClient
from .knowledge_base import KnowledgeBase

logger = logging.getLogger("ai_trade")


class LearningEngine:
    """Learns from trade results and improves decision making."""

    def __init__(self, claude_client: ClaudeClient, knowledge_base: KnowledgeBase):
        self.claude = claude_client
        self.kb = knowledge_base

    async def learn_from_trade(self, trade_result: dict):
        """Analyze completed trade and update knowledge base."""
        logger.info(f"Learning from trade: {trade_result.get('symbol')} PnL={trade_result.get('pnl', 0):.2f}")

        # Get AI analysis of the trade
        analysis = await self.claude.analyze_trade_result(trade_result)

        if "error" in analysis:
            logger.error(f"Failed to analyze trade: {analysis['error']}")
            return

        # Record trade result in knowledge base
        self.kb.add_trade_result(trade_result)

        # Add mistakes to avoid
        if analysis.get("add_to_mistakes_to_avoid"):
            self.kb.add_mistake(analysis["add_to_mistakes_to_avoid"])

        # Add learning note
        lesson = analysis.get("lesson_learned", "")
        if lesson:
            self.kb.add_learning_note(lesson, category="trade_analysis")

        # Update strategy performance
        grade = analysis.get("grade", "C")
        strategy = trade_result.get("strategy", "unknown")
        self._update_strategy_stats(strategy, trade_result, grade)

        logger.info(f"Trade analysis complete. Grade: {grade}, Lesson: {lesson}")

    def _update_strategy_stats(self, strategy: str, trade: dict, grade: str):
        """Update strategy performance statistics."""
        best_strategies = self.kb.data.get("best_strategies", [])

        # Find or create strategy entry
        strategy_entry = None
        for s in best_strategies:
            if s["name"] == strategy:
                strategy_entry = s
                break

        if not strategy_entry:
            strategy_entry = {
                "name": strategy,
                "trades": 0,
                "wins": 0,
                "total_pnl": 0,
                "avg_grade": "",
                "grades": [],
            }
            best_strategies.append(strategy_entry)

        strategy_entry["trades"] += 1
        strategy_entry["total_pnl"] += trade.get("pnl", 0)
        strategy_entry["grades"].append(grade)
        strategy_entry["grades"] = strategy_entry["grades"][-20:]  # Keep last 20

        if trade.get("pnl", 0) > 0:
            strategy_entry["wins"] += 1

        # Calculate average grade
        grade_values = {"A": 5, "B": 4, "C": 3, "D": 2, "F": 1}
        avg = sum(grade_values.get(g, 3) for g in strategy_entry["grades"]) / len(strategy_entry["grades"])
        for letter, val in grade_values.items():
            if avg >= val - 0.5:
                strategy_entry["avg_grade"] = letter
                break

        self.kb.data["best_strategies"] = best_strategies
        self.kb.save()

    async def periodic_review(self):
        """Periodic review of overall performance and strategy adjustments."""
        total_trades = self.kb.data["total_trades"]
        if total_trades < 5:
            return  # Not enough data

        win_rate = self.kb.data["win_rate"]
        recent_trades = (self.kb.data["successful_setups"][-10:] +
                        self.kb.data["failed_setups"][-10:])

        if not recent_trades:
            return

        # Check if performance is declining
        recent_pnl = sum(t.get("pnl", 0) for t in recent_trades[-5:])

        if recent_pnl < 0 and win_rate < 40:
            self.kb.add_learning_note(
                f"Performance declining: last 5 trades PnL={recent_pnl:.2f}, win_rate={win_rate:.1f}%. "
                "Consider reducing position sizes and being more selective.",
                category="performance_warning"
            )
            logger.warning(f"Performance declining. Recent PnL: {recent_pnl:.2f}")

        logger.info(f"Periodic review complete. Win rate: {win_rate:.1f}%, Total PnL: {self.kb.data['total_pnl']:.2f}")
