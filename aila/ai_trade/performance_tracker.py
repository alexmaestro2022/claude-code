"""Performance tracker for AI Trade module."""

import logging
from datetime import date, datetime
from typing import Any, Optional

from .knowledge_base import KnowledgeBase

logger = logging.getLogger("ai_trade")


class PerformanceTracker:
    """Tracks and reports AI trader performance metrics."""

    __slots__ = ("_kb", "_session_start", "_session_trades", "_session_pnl")

    def __init__(self, knowledge_base: KnowledgeBase) -> None:
        self._kb = knowledge_base
        self._session_start = datetime.now()
        self._session_trades: int = 0
        self._session_pnl: float = 0.0

    def record_trade(self, trade: dict[str, Any]) -> None:
        """Record a trade for performance tracking."""
        self._session_trades += 1
        pnl = trade.get("pnl", 0)
        self._session_pnl += pnl

        today = date.today().isoformat()
        daily = self._kb.data.get("daily_stats", {}).get(today, {
            "trades": 0, "wins": 0, "losses": 0,
            "pnl": 0, "best_trade": 0, "worst_trade": 0,
        })

        daily["trades"] += 1
        daily["pnl"] += pnl
        if pnl > 0:
            daily["wins"] += 1
            daily["best_trade"] = max(daily.get("best_trade", 0), pnl)
        else:
            daily["losses"] += 1
            daily["worst_trade"] = min(daily.get("worst_trade", 0), pnl)

        self._kb.update_daily_stats(today, daily)

    def get_daily_report(self) -> dict[str, Any]:
        """Get today's performance report."""
        today = date.today().isoformat()
        daily = self._kb.data.get("daily_stats", {}).get(today, {})
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

    def get_overall_report(self) -> dict[str, Any]:
        """Get overall performance report."""
        kb_data = self._kb.data
        streaks = self._calculate_streaks(kb_data)
        daily_stats = kb_data.get("daily_stats", {})
        last_30_pnl = sum(s.get("pnl", 0) for s in list(daily_stats.values())[-30:])

        return {
            "total_trades": kb_data.get("total_trades", 0),
            "win_rate": kb_data.get("win_rate", 0),
            "total_pnl": kb_data.get("total_pnl", 0),
            "last_30_days_pnl": last_30_pnl,
            "current_streak": streaks["current"],
            "max_win_streak": streaks["max_win"],
            "max_loss_streak": streaks["max_loss"],
            "top_pairs": self._get_top_pairs(),
            "best_strategy": self._get_best_strategy(),
            "session_trades": self._session_trades,
            "session_pnl": self._session_pnl,
        }

    @staticmethod
    def _calculate_streaks(kb_data: dict[str, Any]) -> dict[str, int]:
        """Calculate win/loss streaks."""
        successful = kb_data.get("successful_setups", [])
        failed = kb_data.get("failed_setups", [])
        all_trades = sorted(
            [{"pnl": t.get("pnl", 0), "ts": t.get("timestamp", "")} for t in successful + failed],
            key=lambda x: x["ts"],
        )

        current = max_win = max_loss = 0
        streak = 0
        for t in all_trades:
            if t["pnl"] > 0:
                streak = streak + 1 if streak > 0 else 1
                max_win = max(max_win, streak)
            else:
                streak = streak - 1 if streak < 0 else -1
                max_loss = max(max_loss, abs(streak))
            current = streak

        return {"current": current, "max_win": max_win, "max_loss": max_loss}

    def _get_top_pairs(self, limit: int = 5) -> list[dict[str, Any]]:
        """Get top performing pairs."""
        pairs = self._kb.data.get("pair_performance", {})
        sorted_pairs = sorted(pairs.items(), key=lambda x: x[1].get("total_pnl", 0), reverse=True)
        return [
            {"pair": p[0], "pnl": p[1].get("total_pnl", 0), "trades": p[1].get("trades", 0)}
            for p in sorted_pairs[:limit]
        ]

    def _get_best_strategy(self) -> Optional[dict[str, Any]]:
        """Get the best performing strategy."""
        strategies = self._kb.data.get("best_strategies", [])
        if not strategies:
            return None
        best = max(strategies, key=lambda s: s.get("total_pnl", 0))
        return {
            "name": best.get("name"),
            "trades": best.get("trades", 0),
            "pnl": best.get("total_pnl", 0),
            "win_rate": (best.get("wins", 0) / max(best.get("trades", 1), 1)) * 100,
        }
