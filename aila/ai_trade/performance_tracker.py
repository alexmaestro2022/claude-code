import logging
from datetime import datetime, date
from typing import Optional
from .knowledge_base import KnowledgeBase

logger = logging.getLogger("ai_trade")


class PerformanceTracker:
    """Tracks and reports AI trader performance metrics."""

    def __init__(self, knowledge_base: KnowledgeBase):
        self.kb = knowledge_base
        self.session_start = datetime.now()
        self.session_trades = 0
        self.session_pnl = 0.0

    def record_trade(self, trade: dict):
        """Record a trade for performance tracking."""
        self.session_trades += 1
        pnl = trade.get("pnl", 0)
        self.session_pnl += pnl

        # Update daily stats
        today = date.today().isoformat()
        daily = self.kb.data.get("daily_stats", {}).get(today, {
            "trades": 0, "wins": 0, "losses": 0,
            "pnl": 0, "best_trade": 0, "worst_trade": 0,
        })

        daily["trades"] += 1
        daily["pnl"] += pnl

        if pnl > 0:
            daily["wins"] += 1
            if pnl > daily.get("best_trade", 0):
                daily["best_trade"] = pnl
        else:
            daily["losses"] += 1
            if pnl < daily.get("worst_trade", 0):
                daily["worst_trade"] = pnl

        self.kb.update_daily_stats(today, daily)

    def get_daily_report(self) -> dict:
        """Get today's performance report."""
        today = date.today().isoformat()
        daily = self.kb.data.get("daily_stats", {}).get(today, {})

        trades = daily.get("trades", 0)
        wins = daily.get("wins", 0)

        return {
            "date": today,
            "trades": trades,
            "wins": wins,
            "losses": daily.get("losses", 0),
            "win_rate": (wins / trades * 100) if trades > 0 else 0,
            "pnl": daily.get("pnl", 0),
            "best_trade": daily.get("best_trade", 0),
            "worst_trade": daily.get("worst_trade", 0),
        }

    def get_overall_report(self) -> dict:
        """Get overall performance report."""
        kb_data = self.kb.data

        # Calculate streaks
        successful = kb_data.get("successful_setups", [])
        failed = kb_data.get("failed_setups", [])

        all_trades = sorted(
            [{"pnl": t.get("pnl", 0), "ts": t.get("timestamp", "")} for t in successful + failed],
            key=lambda x: x["ts"]
        )

        current_streak = 0
        max_win_streak = 0
        max_loss_streak = 0
        temp_streak = 0

        for t in all_trades:
            if t["pnl"] > 0:
                if temp_streak > 0:
                    temp_streak += 1
                else:
                    temp_streak = 1
                max_win_streak = max(max_win_streak, temp_streak)
            else:
                if temp_streak < 0:
                    temp_streak -= 1
                else:
                    temp_streak = -1
                max_loss_streak = max(max_loss_streak, abs(temp_streak))
            current_streak = temp_streak

        # Get last 30 days performance
        daily_stats = kb_data.get("daily_stats", {})
        last_30_days_pnl = sum(
            stats.get("pnl", 0) for stats in list(daily_stats.values())[-30:]
        )

        return {
            "total_trades": kb_data.get("total_trades", 0),
            "win_rate": kb_data.get("win_rate", 0),
            "total_pnl": kb_data.get("total_pnl", 0),
            "last_30_days_pnl": last_30_days_pnl,
            "current_streak": current_streak,
            "max_win_streak": max_win_streak,
            "max_loss_streak": max_loss_streak,
            "top_pairs": self._get_top_pairs(),
            "best_strategy": self._get_best_strategy(),
            "session_trades": self.session_trades,
            "session_pnl": self.session_pnl,
        }

    def _get_top_pairs(self, limit: int = 5) -> list:
        """Get top performing pairs."""
        pairs = self.kb.data.get("pair_performance", {})
        sorted_pairs = sorted(
            pairs.items(),
            key=lambda x: x[1].get("total_pnl", 0),
            reverse=True
        )
        return [
            {"pair": p[0], "pnl": p[1].get("total_pnl", 0), "trades": p[1].get("trades", 0)}
            for p in sorted_pairs[:limit]
        ]

    def _get_best_strategy(self) -> Optional[dict]:
        """Get the best performing strategy."""
        strategies = self.kb.data.get("best_strategies", [])
        if not strategies:
            return None

        best = max(strategies, key=lambda s: s.get("total_pnl", 0))
        return {
            "name": best.get("name"),
            "trades": best.get("trades", 0),
            "pnl": best.get("total_pnl", 0),
            "win_rate": (best.get("wins", 0) / max(best.get("trades", 1), 1)) * 100,
        }
