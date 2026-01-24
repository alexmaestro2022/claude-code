import json
import os
import logging
from datetime import datetime
from typing import Optional
from .config import KNOWLEDGE_BASE_PATH

logger = logging.getLogger("ai_trade")


class KnowledgeBase:
    """AI knowledge base - stores trading experience and lessons."""

    def __init__(self):
        self.path = KNOWLEDGE_BASE_PATH
        self.data = self._load()

    def _load(self) -> dict:
        """Load knowledge base from file."""
        if os.path.exists(self.path):
            try:
                with open(self.path, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                logger.error(f"Error loading knowledge base: {e}")

        return self._default_structure()

    def _default_structure(self) -> dict:
        """Default knowledge base structure."""
        return {
            "created_at": datetime.now().isoformat(),
            "total_trades": 0,
            "win_rate": 0.0,
            "total_pnl": 0.0,
            "pair_performance": {},
            "successful_setups": [],
            "failed_setups": [],
            "mistakes_to_avoid": [],
            "best_strategies": [],
            "market_patterns": [],
            "risk_adjustments": [],
            "daily_stats": {},
            "learning_notes": [],
        }

    def save(self):
        """Save knowledge base to file."""
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w") as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
            logger.info("Knowledge base saved")
        except IOError as e:
            logger.error(f"Error saving knowledge base: {e}")

    def add_trade_result(self, trade: dict):
        """Record completed trade result."""
        self.data["total_trades"] += 1
        pnl = trade.get("pnl", 0)
        self.data["total_pnl"] += pnl

        pair = trade.get("pair", "UNKNOWN")

        # Update pair performance
        if pair not in self.data["pair_performance"]:
            self.data["pair_performance"][pair] = {
                "trades": 0, "wins": 0, "losses": 0,
                "total_pnl": 0, "avg_pnl": 0,
            }

        pair_stats = self.data["pair_performance"][pair]
        pair_stats["trades"] += 1
        pair_stats["total_pnl"] += pnl

        if pnl > 0:
            pair_stats["wins"] += 1
        else:
            pair_stats["losses"] += 1

        pair_stats["avg_pnl"] = pair_stats["total_pnl"] / pair_stats["trades"]

        # Update win rate
        total_wins = sum(
            p["wins"] for p in self.data["pair_performance"].values()
        )
        if self.data["total_trades"] > 0:
            self.data["win_rate"] = total_wins / self.data["total_trades"] * 100

        # Categorize setup
        setup = {
            "pair": pair,
            "strategy": trade.get("strategy"),
            "direction": trade.get("direction"),
            "pnl": pnl,
            "pnl_pct": trade.get("pnl_pct", 0),
            "duration": trade.get("duration"),
            "timestamp": datetime.now().isoformat(),
        }

        if pnl > 0:
            self.data["successful_setups"].append(setup)
            # Keep last 50 successful setups
            self.data["successful_setups"] = self.data["successful_setups"][-50:]
        else:
            self.data["failed_setups"].append(setup)
            self.data["failed_setups"] = self.data["failed_setups"][-50:]

        self.save()

    def add_mistake(self, mistake: str):
        """Add a mistake to avoid list."""
        entry = {
            "mistake": mistake,
            "added_at": datetime.now().isoformat(),
        }
        self.data["mistakes_to_avoid"].append(entry)
        # Keep last 30 mistakes
        self.data["mistakes_to_avoid"] = self.data["mistakes_to_avoid"][-30:]
        self.save()

    def add_learning_note(self, note: str, category: str = "general"):
        """Add a learning note."""
        entry = {
            "note": note,
            "category": category,
            "added_at": datetime.now().isoformat(),
        }
        self.data["learning_notes"].append(entry)
        self.data["learning_notes"] = self.data["learning_notes"][-100:]
        self.save()

    def get_pair_stats(self, pair: str) -> dict:
        """Get statistics for a specific pair."""
        return self.data["pair_performance"].get(pair, {})

    def get_context_for_analysis(self, pair: Optional[str] = None) -> dict:
        """Get relevant knowledge context for AI analysis."""
        context = {
            "total_trades": self.data["total_trades"],
            "win_rate": self.data["win_rate"],
            "total_pnl": self.data["total_pnl"],
            "successful_setups": self.data["successful_setups"][-5:],
            "mistakes_to_avoid": self.data["mistakes_to_avoid"][-5:],
            "best_strategies": self.data["best_strategies"][-3:],
        }

        if pair:
            context["pair_performance"] = {
                pair: self.get_pair_stats(pair)
            }

        return context

    def update_daily_stats(self, date: str, stats: dict):
        """Update daily statistics."""
        self.data["daily_stats"][date] = stats
        # Keep last 90 days
        dates = sorted(self.data["daily_stats"].keys())
        if len(dates) > 90:
            for old_date in dates[:-90]:
                del self.data["daily_stats"][old_date]
        self.save()
