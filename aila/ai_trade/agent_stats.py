"""
AGENT STATS - Manages statistics, XP, and levels for TRADER and SNIPER agents.
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from .config import (
    TRADER_STATS_PATH, SNIPER_STATS_PATH,
    TRADER_LEVELS, SNIPER_LEVELS, AGENT_XP_THRESHOLDS,
    PERFORMANCE_LIMITS,
)

# Max pause duration — auto-reset after this
MAX_PAUSE_HOURS = 4

logger = logging.getLogger("ai_trade.agent_stats")


class AgentStatsManager:
    """Manages statistics, XP, and levels for trading agents."""

    __slots__ = ("_trader_stats", "_sniper_stats", "_trader_path", "_sniper_path")

    def __init__(self) -> None:
        self._trader_path = Path(TRADER_STATS_PATH)
        self._sniper_path = Path(SNIPER_STATS_PATH)
        self._trader_stats = self._load_stats("TRADER")
        self._sniper_stats = self._load_stats("SNIPER")

    def _load_stats(self, agent: str) -> dict[str, Any]:
        """Load stats from file."""
        path = self._trader_path if agent == "TRADER" else self._sniper_path
        try:
            if path.exists():
                with open(path, "r") as f:
                    container = json.load(f)
                    return container.get("data", self._default_stats(agent))
        except Exception as e:
            logger.error(f"Error loading {agent} stats: {e}")
        return self._default_stats(agent)

    def _save_stats(self, agent: str) -> None:
        """Save stats to file."""
        path = self._trader_path if agent == "TRADER" else self._sniper_path
        stats = self._trader_stats if agent == "TRADER" else self._sniper_stats
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            container = {
                "data": stats,
                "saved_at": datetime.utcnow().isoformat(),
                "version": "2.0",
            }
            with open(path, "w") as f:
                json.dump(container, f, indent=2, ensure_ascii=False, default=str)
        except Exception as e:
            logger.error(f"Error saving {agent} stats: {e}")

    def _default_stats(self, agent: str) -> dict[str, Any]:
        """Default stats structure."""
        base = {
            "agent": agent,
            "level": 1,
            "xp": 0,
            "xp_to_next_level": AGENT_XP_THRESHOLDS.get(2, 100),
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "winrate": 0.0,
            "total_pnl_usdt": 0.0,
            "total_pnl_percent": 0.0,
            "best_trade_usdt": 0.0,
            "worst_trade_usdt": 0.0,
            "avg_trade_duration_minutes": 0,
            "avg_rr_ratio": 0.0,
            "consecutive_wins": 0,
            "consecutive_losses": 0,
            "max_consecutive_wins": 0,
            "max_consecutive_losses": 0,
            "trades_today": 0,
            "pnl_today_usdt": 0.0,
            "trades_this_week": 0,
            "pnl_this_week_usdt": 0.0,
            "last_trade_time": None,
            "last_win_time": None,
            "last_loss_time": None,
            "paused_until": None,
            "pause_reason": None,
            "api_usage": {
                "calls_today": 0,
                "tokens_in_today": 0,
                "tokens_out_today": 0,
                "cost_today_usdt": 0.0,
                "calls_total": 0,
                "tokens_in_total": 0,
                "tokens_out_total": 0,
                "cost_total_usdt": 0.0,
            },
            "daily_history": {},
            "weekly_history": {},
        }

        if agent == "SNIPER":
            base["trigger_stats"] = {
                "breakout": {"trades": 0, "wins": 0, "pnl": 0.0},
                "breakdown": {"trades": 0, "wins": 0, "pnl": 0.0},
                "liquidation_cascade": {"trades": 0, "wins": 0, "pnl": 0.0},
                "funding_flip": {"trades": 0, "wins": 0, "pnl": 0.0},
            }

        return base

    def get_stats(self, agent: str) -> dict[str, Any]:
        """Get stats for agent."""
        self._reset_daily_if_needed(agent)
        return self._trader_stats if agent == "TRADER" else self._sniper_stats

    def get_level(self, agent: str) -> int:
        """Get current level for agent."""
        stats = self.get_stats(agent)
        return stats.get("level", 1)

    def get_level_limits(self, agent: str) -> dict[str, Any]:
        """Get trading limits based on agent level."""
        level = self.get_level(agent)
        levels = TRADER_LEVELS if agent == "TRADER" else SNIPER_LEVELS
        return levels.get(level, levels.get(1, {}))

    def add_xp(self, agent: str, amount: int, reason: str = "") -> dict[str, Any]:
        """Add XP to agent and check for level up."""
        stats = self._trader_stats if agent == "TRADER" else self._sniper_stats
        old_level = stats["level"]
        stats["xp"] += amount

        # Check level up
        leveled_up = False
        while stats["level"] < 10:
            next_level = stats["level"] + 1
            required_xp = AGENT_XP_THRESHOLDS.get(next_level, float("inf"))
            if stats["xp"] >= required_xp:
                stats["level"] = next_level
                stats["xp_to_next_level"] = AGENT_XP_THRESHOLDS.get(next_level + 1, 0) - stats["xp"]
                leveled_up = True
                logger.warning(f"[{agent}] LEVEL UP! Now level {stats['level']}")
            else:
                stats["xp_to_next_level"] = required_xp - stats["xp"]
                break

        self._save_stats(agent)

        return {
            "agent": agent,
            "xp_added": amount,
            "reason": reason,
            "new_xp": stats["xp"],
            "level": stats["level"],
            "leveled_up": leveled_up,
            "old_level": old_level,
            "xp_to_next_level": stats["xp_to_next_level"],
        }

    def record_trade(
        self,
        agent: str,
        pnl_usdt: float,
        pnl_pct: float,
        duration_minutes: int,
        rr_ratio: float,
        trigger_type: Optional[str] = None,
        trade_data: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Record completed trade and update stats."""
        stats = self._trader_stats if agent == "TRADER" else self._sniper_stats
        is_win = pnl_usdt > 0

        # Store trade in history
        if trade_data:
            if "trades_history" not in stats or stats["trades_history"] is None:
                stats["trades_history"] = []
            trade_record = {
                "symbol": trade_data.get("symbol", ""),
                "side": trade_data.get("side", ""),
                "pnl_usdt": pnl_usdt,
                "pnl_pct": round(pnl_pct, 2),
                "entry_price": trade_data.get("entry_price", 0),
                "exit_price": trade_data.get("exit_price", 0),
                "close_reason": trade_data.get("close_reason", "unknown"),
                "leverage": trade_data.get("leverage", "1"),
                "grade": trade_data.get("grade", "B" if is_win else "D"),
                "closed_at": datetime.utcnow().isoformat(),
            }
            stats["trades_history"].insert(0, trade_record)  # Latest first
            # Keep only last 100 trades
            stats["trades_history"] = stats["trades_history"][:100]

        # Update counts
        stats["total_trades"] += 1
        stats["trades_today"] += 1
        stats["trades_this_week"] += 1

        if is_win:
            stats["winning_trades"] += 1
            stats["consecutive_wins"] += 1
            stats["consecutive_losses"] = 0
            stats["last_win_time"] = datetime.utcnow().isoformat()
            if stats["consecutive_wins"] > stats["max_consecutive_wins"]:
                stats["max_consecutive_wins"] = stats["consecutive_wins"]
        else:
            stats["losing_trades"] += 1
            stats["consecutive_losses"] += 1
            stats["consecutive_wins"] = 0
            stats["last_loss_time"] = datetime.utcnow().isoformat()
            if stats["consecutive_losses"] > stats["max_consecutive_losses"]:
                stats["max_consecutive_losses"] = stats["consecutive_losses"]

        # Update PnL
        stats["total_pnl_usdt"] += pnl_usdt
        stats["pnl_today_usdt"] += pnl_usdt
        stats["pnl_this_week_usdt"] += pnl_usdt

        if pnl_usdt > stats["best_trade_usdt"]:
            stats["best_trade_usdt"] = pnl_usdt
        if pnl_usdt < stats["worst_trade_usdt"]:
            stats["worst_trade_usdt"] = pnl_usdt

        # Update averages
        stats["winrate"] = (stats["winning_trades"] / stats["total_trades"] * 100) if stats["total_trades"] > 0 else 0

        # Update SNIPER trigger stats
        if agent == "SNIPER" and trigger_type:
            trigger_stats = stats.get("trigger_stats", {}).get(trigger_type, {})
            trigger_stats["trades"] = trigger_stats.get("trades", 0) + 1
            if is_win:
                trigger_stats["wins"] = trigger_stats.get("wins", 0) + 1
            trigger_stats["pnl"] = trigger_stats.get("pnl", 0.0) + pnl_usdt
            stats.setdefault("trigger_stats", {})[trigger_type] = trigger_stats

        stats["last_trade_time"] = datetime.utcnow().isoformat()

        # Check for loss streak pause
        pause_result = self._check_loss_streak_pause(agent)

        # Calculate XP
        xp_change = self._calculate_xp(is_win, pnl_pct, stats["consecutive_wins"], stats["consecutive_losses"])
        xp_result = self.add_xp(agent, xp_change, "trade_result")

        self._save_stats(agent)

        return {
            "agent": agent,
            "trade_recorded": True,
            "is_win": is_win,
            "pnl_usdt": pnl_usdt,
            "xp_result": xp_result,
            "pause_result": pause_result,
            "stats_snapshot": {
                "winrate": stats["winrate"],
                "consecutive_wins": stats["consecutive_wins"],
                "consecutive_losses": stats["consecutive_losses"],
                "trades_today": stats["trades_today"],
                "pnl_today_usdt": stats["pnl_today_usdt"],
            },
        }

    def _calculate_xp(
        self, is_win: bool, pnl_pct: float, consecutive_wins: int, consecutive_losses: int
    ) -> int:
        """Calculate XP change based on trade result."""
        if is_win:
            base_xp = 10
            # Bonus for big wins
            if pnl_pct > 5:
                base_xp += 10
            elif pnl_pct > 3:
                base_xp += 5
            # Streak bonus
            if consecutive_wins >= 5:
                base_xp += 20
            elif consecutive_wins >= 3:
                base_xp += 10
            return base_xp
        else:
            base_xp = -5
            # Bigger penalty for big losses
            if pnl_pct < -5:
                base_xp -= 10
            # Streak penalty
            if consecutive_losses >= 3:
                base_xp -= 10
            return base_xp

    def _check_loss_streak_pause(self, agent: str) -> dict[str, Any]:
        """Check if agent should be paused due to loss streak."""
        stats = self._trader_stats if agent == "TRADER" else self._sniper_stats
        max_streak = PERFORMANCE_LIMITS.get("loss_streak_pause", 3)
        pause_minutes = PERFORMANCE_LIMITS.get("loss_streak_pause_minutes", 60)

        if stats["consecutive_losses"] >= max_streak:
            now = datetime.utcnow()
            pause_until = now + timedelta(minutes=pause_minutes)
            stats["paused_until"] = pause_until.isoformat()
            stats["paused_at"] = now.isoformat()
            stats["pause_reason"] = f"{stats['consecutive_losses']} consecutive losses"
            logger.warning(f"[{agent}] PAUSED for {pause_minutes} min due to {stats['consecutive_losses']} losses (auto-reset in {MAX_PAUSE_HOURS}h)")
            return {"paused": True, "until": stats["paused_until"], "reason": stats["pause_reason"]}

        return {"paused": False}

    def is_paused(self, agent: str) -> tuple[bool, Optional[str]]:
        """Check if agent is paused. Auto-clears after MAX_PAUSE_HOURS."""
        stats = self.get_stats(agent)
        paused_until = stats.get("paused_until")

        if not paused_until:
            return False, None

        if isinstance(paused_until, str):
            paused_until = datetime.fromisoformat(paused_until)

        now = datetime.utcnow()

        # Auto-reset if pause exceeds MAX_PAUSE_HOURS (even if paused_until is further)
        paused_at = stats.get("paused_at")
        if paused_at:
            if isinstance(paused_at, str):
                paused_at = datetime.fromisoformat(paused_at)
            hours_paused = (now - paused_at).total_seconds() / 3600
            if hours_paused >= MAX_PAUSE_HOURS:
                logger.warning(f"[{agent}] Auto-reset pause after {hours_paused:.1f}h (max {MAX_PAUSE_HOURS}h)")
                self.clear_pause(agent)
                return False, None

        if now < paused_until:
            return True, stats.get("pause_reason")

        # Clear expired pause
        stats["paused_until"] = None
        stats["pause_reason"] = None
        stats["paused_at"] = None
        self._save_stats(agent)
        return False, None

    def clear_pause(self, agent: str) -> None:
        """Clear pause for agent."""
        stats = self._trader_stats if agent == "TRADER" else self._sniper_stats
        stats["paused_until"] = None
        stats["paused_at"] = None
        stats["pause_reason"] = None
        stats["consecutive_losses"] = 0
        self._save_stats(agent)
        logger.info(f"[{agent}] Pause cleared")

    def record_api_usage(
        self, agent: str, tokens_in: int, tokens_out: int, cost_usdt: float
    ) -> None:
        """Record API usage for agent."""
        stats = self._trader_stats if agent == "TRADER" else self._sniper_stats
        api = stats.setdefault("api_usage", {})

        api["calls_today"] = api.get("calls_today", 0) + 1
        api["tokens_in_today"] = api.get("tokens_in_today", 0) + tokens_in
        api["tokens_out_today"] = api.get("tokens_out_today", 0) + tokens_out
        api["cost_today_usdt"] = api.get("cost_today_usdt", 0.0) + cost_usdt

        api["calls_total"] = api.get("calls_total", 0) + 1
        api["tokens_in_total"] = api.get("tokens_in_total", 0) + tokens_in
        api["tokens_out_total"] = api.get("tokens_out_total", 0) + tokens_out
        api["cost_total_usdt"] = api.get("cost_total_usdt", 0.0) + cost_usdt

        # Don't save on every API call - will be saved by persistence manager

    def _reset_daily_if_needed(self, agent: str) -> None:
        """Reset daily stats if new day."""
        stats = self._trader_stats if agent == "TRADER" else self._sniper_stats
        last_trade = stats.get("last_trade_time")

        if not last_trade:
            return

        if isinstance(last_trade, str):
            try:
                last_trade = datetime.fromisoformat(last_trade)
            except ValueError:
                return

        today = datetime.utcnow().date()
        if last_trade.date() < today:
            # Save yesterday's stats to history
            yesterday = last_trade.strftime("%Y-%m-%d")
            stats.setdefault("daily_history", {})[yesterday] = {
                "trades": stats["trades_today"],
                "pnl_usdt": stats["pnl_today_usdt"],
                "api_calls": stats.get("api_usage", {}).get("calls_today", 0),
                "api_cost": stats.get("api_usage", {}).get("cost_today_usdt", 0.0),
            }

            # Reset daily counters
            stats["trades_today"] = 0
            stats["pnl_today_usdt"] = 0.0
            api = stats.get("api_usage", {})
            api["calls_today"] = 0
            api["tokens_in_today"] = 0
            api["tokens_out_today"] = 0
            api["cost_today_usdt"] = 0.0

            # Check weekly reset
            if last_trade.isocalendar()[1] != today.isocalendar()[1]:
                week_key = f"{last_trade.year}-W{last_trade.isocalendar()[1]:02d}"
                stats.setdefault("weekly_history", {})[week_key] = {
                    "trades": stats["trades_this_week"],
                    "pnl_usdt": stats["pnl_this_week_usdt"],
                }
                stats["trades_this_week"] = 0
                stats["pnl_this_week_usdt"] = 0.0

            self._save_stats(agent)

    def get_all_stats(self) -> dict[str, Any]:
        """Get stats for both agents."""
        return {
            "trader": self.get_stats("TRADER"),
            "sniper": self.get_stats("SNIPER"),
        }

    def save_all(self) -> None:
        """Save all stats."""
        self._save_stats("TRADER")
        self._save_stats("SNIPER")
