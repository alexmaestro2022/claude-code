"""ANALYST - analyzes completed trades and finds patterns."""

import json
from typing import Any

from .base_agent import BaseAgent


class AnalystAgent(BaseAgent):
    """Analyzes completed trades, finds patterns, updates knowledge base."""

    def __init__(self, claude_client: Any, knowledge_base: Any) -> None:
        super().__init__(
            name="ANALYST",
            claude_client=claude_client,
            knowledge_base=knowledge_base,
            log_path="/opt/aila/logs/ai_trade/analyst.log",
        )

    async def think(self, context: dict[str, Any]) -> dict[str, Any]:
        """Analyze a completed trade."""
        trade = context.get("trade")
        if not trade:
            return {"error": "No trade provided"}
        return await self.analyze(trade)

    async def analyze(self, trade: dict) -> dict:
        """
        Analyze completed trade and extract lessons.
        Updates knowledge base with findings.
        """
        self.log(f"Analyzing trade: {trade.get('symbol')} PnL={trade.get('pnl', 0):.2f}")

        # Get AI analysis
        analysis = await self.claude_client.analyze_trade_result(trade)

        if "error" in analysis:
            self.log(f"Analysis error: {analysis['error']}", "error")
            return analysis

        # Update knowledge base
        self.knowledge_base.add_trade_result(trade)

        grade = analysis.get("grade", "C")
        lesson = analysis.get("lesson_learned", "")

        # Add mistake if identified
        mistake = analysis.get("add_to_mistakes_to_avoid")
        if mistake and mistake != "null" and grade in ("D", "F"):
            self.knowledge_base.add_mistake(mistake)
            self.log(f"New mistake recorded: {mistake}")

        # Add learning note
        if lesson:
            self.knowledge_base.add_learning_note(lesson, category="trade_review")

        self.log(f"Grade: {grade} | Lesson: {lesson}")

        return {
            "grade": grade,
            "lesson": lesson,
            "what_went_right": analysis.get("what_went_right", []),
            "what_went_wrong": analysis.get("what_went_wrong", []),
            "improvement": analysis.get("improvement_suggestion", ""),
        }

    async def find_patterns(self) -> dict:
        """Analyze recent trades to find patterns."""
        successful = self.knowledge_base.data.get("successful_setups", [])[-20:]
        failed = self.knowledge_base.data.get("failed_setups", [])[-20:]

        if len(successful) + len(failed) < 5:
            return {"message": "Not enough trades for pattern analysis"}

        prompt = f"""Analyze these trading results and find patterns.

## SUCCESSFUL TRADES (last 20)
{json.dumps(successful, indent=2)}

## FAILED TRADES (last 20)
{json.dumps(failed, indent=2)}

## TASK
Find patterns and respond in JSON:

{{
    "winning_patterns": ["pattern1", "pattern2"],
    "losing_patterns": ["pattern1", "pattern2"],
    "best_pairs": ["pair1", "pair2"],
    "worst_pairs": ["pair1", "pair2"],
    "best_time_of_day": "HH:MM range",
    "recommended_adjustments": [
        {{"parameter": "name", "current": "value", "suggested": "value", "reason": "why"}}
    ],
    "overall_assessment": "summary"
}}
"""
        result = await self.claude_client.analyze(prompt)

        if "error" not in result:
            self.log(f"Pattern analysis complete: {result.get('overall_assessment', '')}")
            # Store patterns in knowledge base
            if result.get("winning_patterns"):
                self.knowledge_base.data.setdefault("market_patterns", []).extend(
                    [{"type": "winning", "pattern": p} for p in result["winning_patterns"]]
                )
                self.knowledge_base.data["market_patterns"] = \
                    self.knowledge_base.data["market_patterns"][-50:]
                self.knowledge_base.save()

        return result

    async def weekly_review(self) -> dict:
        """Generate a weekly performance review."""
        daily_stats = self.knowledge_base.data.get("daily_stats", {})
        last_7_days = dict(list(sorted(daily_stats.items()))[-7:])

        if not last_7_days:
            return {"message": "No data for weekly review"}

        total_trades = sum(d.get("trades", 0) for d in last_7_days.values())
        total_pnl = sum(d.get("pnl", 0) for d in last_7_days.values())
        total_wins = sum(d.get("wins", 0) for d in last_7_days.values())

        review = {
            "period": f"{list(last_7_days.keys())[0]} to {list(last_7_days.keys())[-1]}",
            "total_trades": total_trades,
            "total_pnl": total_pnl,
            "win_rate": (total_wins / total_trades * 100) if total_trades > 0 else 0,
            "best_day": max(last_7_days.items(), key=lambda x: x[1].get("pnl", 0)),
            "worst_day": min(last_7_days.items(), key=lambda x: x[1].get("pnl", 0)),
        }

        self.log(f"Weekly review: {total_trades} trades, PnL=${total_pnl:.2f}, WR={review['win_rate']:.1f}%")
        return review
