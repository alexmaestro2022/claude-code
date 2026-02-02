"""AI knowledge base - persistent trading experience store."""

import json
import logging
import os
from datetime import datetime
from typing import Any, Optional

from .config import KNOWLEDGE_BASE_PATH

logger = logging.getLogger("ai_trade")


class KnowledgeBase:
    """AI knowledge base - stores trading experience and lessons."""

    __slots__ = ("_path", "data")

    def __init__(self) -> None:
        self._path = KNOWLEDGE_BASE_PATH
        self.data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        """Load knowledge base from file."""
        if os.path.exists(self._path):
            try:
                with open(self._path, "r") as f:
                    raw = json.load(f)
                # Handle wrapped format {"data": {...}, "saved_at": ...}
                if "data" in raw and "saved_at" in raw:
                    return raw["data"]
                return raw
            except (json.JSONDecodeError, IOError) as e:
                logger.error(f"Error loading knowledge base: {e}")
        return self._default_structure()

    def save(self) -> None:
        """Save knowledge base to file (wrapped format for persistence compat)."""
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            wrapped = {
                "data": self.data,
                "saved_at": datetime.now().isoformat(),
                "version": "2.0",
            }
            with open(self._path, "w") as f:
                json.dump(wrapped, f, indent=2, ensure_ascii=False)
        except IOError as e:
            logger.error(f"Error saving knowledge base: {e}")

    def add_trade_result(self, trade: dict[str, Any]) -> None:
        """Record completed trade result."""
        self.data["total_trades"] += 1
        pnl = trade.get("pnl", 0)
        self.data["total_pnl"] += pnl
        pair = trade.get("pair", "UNKNOWN")

        pair_stats = self.data["pair_performance"].setdefault(pair, {
            "trades": 0, "wins": 0, "losses": 0, "total_pnl": 0, "avg_pnl": 0,
        })
        pair_stats["trades"] += 1
        pair_stats["total_pnl"] += pnl
        pair_stats["wins" if pnl > 0 else "losses"] += 1
        pair_stats["avg_pnl"] = pair_stats["total_pnl"] / pair_stats["trades"]

        # Update win rate
        total_wins = sum(p["wins"] for p in self.data["pair_performance"].values())
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

        key = "successful_setups" if pnl > 0 else "failed_setups"
        self.data[key].append(setup)
        self.data[key] = self.data[key][-50:]
        self.save()

    def add_mistake(self, mistake: str) -> None:
        """Add a mistake to avoid list."""
        self.data["mistakes_to_avoid"].append({
            "mistake": mistake,
            "added_at": datetime.now().isoformat(),
        })
        self.data["mistakes_to_avoid"] = self.data["mistakes_to_avoid"][-30:]
        self.save()

    def add_learning_note(self, note: str, category: str = "general") -> None:
        """Add a learning note."""
        self.data["learning_notes"].append({
            "note": note,
            "category": category,
            "added_at": datetime.now().isoformat(),
        })
        self.data["learning_notes"] = self.data["learning_notes"][-100:]
        self.save()

    def get_pair_stats(self, pair: str) -> dict[str, Any]:
        """Get statistics for a specific pair."""
        return self.data["pair_performance"].get(pair, {})

    def get_context_for_analysis(
        self, pair: Optional[str] = None, agent: str = "TRADER"
    ) -> dict[str, Any]:
        """Get relevant knowledge context for AI analysis.

        Merges top-level data with agent-specific learning data
        from trader_learning / sniper_learning sections.
        """
        # Collect successful setups from both sources
        top_setups = self.data.get("successful_setups", [])
        top_mistakes = self.data.get("mistakes_to_avoid", [])

        # Agent-specific learning data (written by autopilot)
        agent_key = f"{agent.lower()}_learning"
        agent_data = self.data.get(agent_key, {})
        agent_best = agent_data.get("best_pairs", [])
        agent_mistakes = agent_data.get("mistakes_to_avoid", [])
        learned_rules = agent_data.get("learned_rules", [])

        # Also check trader_profile for mentor-written rules
        profile_rules = self.data.get("trader_profile", {}).get("learned_rules", [])

        # Merge: agent-specific data takes priority (most recent)
        all_setups = top_setups + [
            {"pair": p.get("symbol", ""), "pnl": p.get("pnl_usdt", 0),
             "grade": p.get("grade", ""), "strategy": p.get("close_reason", ""),
             "timestamp": p.get("added_at", "")}
            for p in agent_best
        ]
        all_mistakes = top_mistakes + agent_mistakes

        # Merge learned rules from both sources
        all_rules = learned_rules + profile_rules

        context: dict[str, Any] = {
            "total_trades": self.data.get("total_trades", 0),
            "win_rate": self.data.get("win_rate", 0.0),
            "total_pnl": self.data.get("total_pnl", 0.0),
            "successful_setups": all_setups[-5:],
            "mistakes_to_avoid": all_mistakes[-5:],
            "best_strategies": self.data.get("best_strategies", [])[-3:],
            "learned_rules": all_rules[-5:],
        }
        if pair:
            context["pair_performance"] = {pair: self.get_pair_stats(pair)}
        return context

    def update_daily_stats(self, date_str: str, stats: dict[str, Any]) -> None:
        """Update daily statistics."""
        self.data["daily_stats"][date_str] = stats
        dates = sorted(self.data["daily_stats"].keys())
        if len(dates) > 90:
            for old_date in dates[:-90]:
                del self.data["daily_stats"][old_date]
        self.save()

    # --- XP and Skills System ---

    def add_xp(self, amount: int, skill: Optional[str] = None) -> None:
        """Add XP (general and/or to a specific skill)."""
        profile = self.data.setdefault("trader_profile", self._default_structure()["trader_profile"])
        profile["experience_points"] = max(0, profile.get("experience_points", 0) + amount)

        if skill and skill in profile.get("skills", {}):
            skill_data = profile["skills"][skill]
            skill_data["xp"] = max(0, skill_data.get("xp", 0) + amount)
            while skill_data["xp"] >= skill_data.get("max_xp", 100):
                skill_data["xp"] -= skill_data["max_xp"]
                skill_data["level"] = skill_data.get("level", 1) + 1
                skill_data["max_xp"] = int(skill_data["max_xp"] * 1.5)
                logger.info(f"Skill {skill} leveled up to {skill_data['level']}")

        self.check_level_up()
        self.save()

    def check_level_up(self) -> bool:
        """Check and perform level up if enough XP."""
        profile = self.data.get("trader_profile", {})
        xp = profile.get("experience_points", 0)
        next_level_xp = profile.get("next_level_xp", 100)
        leveled_up = False

        while xp >= next_level_xp:
            xp -= next_level_xp
            profile["level"] = profile.get("level", 1) + 1
            next_level_xp = int(next_level_xp * 1.3)
            leveled_up = True
            logger.info(f"LEVEL UP! Now level {profile['level']}")

        profile["experience_points"] = xp
        profile["next_level_xp"] = next_level_xp
        if leveled_up:
            self.save()
        return leveled_up

    def add_rule(self, rule: dict[str, Any]) -> None:
        """Add a learned rule."""
        profile = self.data.setdefault("trader_profile", self._default_structure()["trader_profile"])
        rules = profile.setdefault("learned_rules", [])
        rule["added_at"] = datetime.now().isoformat()
        rules.append(rule)
        profile["learned_rules"] = rules[-50:]
        self.save()

    def get_level_benefits(self) -> dict[str, Any]:
        """Get current limits based on trader level."""
        level = self.data.get("trader_profile", {}).get("level", 1)
        level_benefits = self.data.get("level_benefits", {})
        applicable: dict[str, Any] = {"max_leverage": 5, "max_positions": 1, "max_risk_pct": 1}
        for tier_level, benefits in sorted(level_benefits.items(), key=lambda x: int(x[0])):
            if level >= int(tier_level):
                applicable = benefits
        return applicable

    def get_skills(self) -> dict[str, Any]:
        """Get current skills summary."""
        return self.data.get("trader_profile", {}).get("skills", {})

    def get_trader_level(self) -> int:
        """Get current trader level."""
        return self.data.get("trader_profile", {}).get("level", 1)

    async def get_trader_profile(self) -> dict[str, Any]:
        """Get full trader profile for autopilot pre-flight check."""
        return self.data.get("trader_profile", self._default_structure()["trader_profile"])

    def update_market_regime(self, regime: dict[str, Any]) -> None:
        """Update current market regime."""
        self.data["market_regime"] = {**regime, "updated_at": datetime.now().isoformat()}
        self.save()

    def save_daily_review(self, review: dict[str, Any]) -> None:
        """Save daily mentor review."""
        reviews = self.data.setdefault("daily_reviews", [])
        review["date"] = datetime.now().isoformat()
        reviews.append(review)
        self.data["daily_reviews"] = reviews[-30:]
        self.save()

    def save_weekly_training(self, training: dict[str, Any], patterns: dict[str, Any]) -> None:
        """Save weekly training results."""
        trainings = self.data.setdefault("weekly_trainings", [])
        trainings.append({
            "training": training, "patterns": patterns, "date": datetime.now().isoformat(),
        })
        self.data["weekly_trainings"] = trainings[-12:]
        self.save()

    def get_today_trades(self) -> list[dict[str, Any]]:
        """Get all trades from today."""
        today = datetime.now().strftime("%Y-%m-%d")
        all_setups = self.data.get("successful_setups", []) + self.data.get("failed_setups", [])
        return [t for t in all_setups if t.get("timestamp", "").startswith(today)]

    def get_weekly_stats(self) -> dict[str, Any]:
        """Get statistics for the current week."""
        daily_stats = self.data.get("daily_stats", {})
        last_7 = dict(list(sorted(daily_stats.items()))[-7:])
        total_trades = sum(d.get("trades", 0) for d in last_7.values())
        total_wins = sum(d.get("wins", 0) for d in last_7.values())
        total_pnl = sum(d.get("pnl", 0) for d in last_7.values())

        return {
            "days": len(last_7),
            "total_trades": total_trades,
            "total_wins": total_wins,
            "total_pnl": total_pnl,
            "win_rate": (total_wins / total_trades * 100) if total_trades > 0 else 0,
            "daily_breakdown": last_7,
        }

    def get_all_trades(self) -> list[dict[str, Any]]:
        """Get all trades (successful + failed)."""
        return self.data.get("successful_setups", []) + self.data.get("failed_setups", [])

    @staticmethod
    def _default_structure() -> dict[str, Any]:
        """Default knowledge base structure."""
        return {
            "created_at": datetime.now().isoformat(),
            "total_trades": 0, "win_rate": 0.0, "total_pnl": 0.0,
            "pair_performance": {},
            "successful_setups": [], "failed_setups": [],
            "mistakes_to_avoid": [], "best_strategies": [],
            "market_patterns": [], "risk_adjustments": [],
            "daily_stats": {}, "learning_notes": [],
            "market_regime": {}, "daily_reviews": [], "weekly_trainings": [],
            "trader_profile": {
                "level": 1, "experience_points": 0, "next_level_xp": 100,
                "skills": {
                    skill: {"level": 1, "xp": 0, "max_xp": 100}
                    for skill in [
                        "trend_detection", "entry_timing", "exit_timing",
                        "risk_management", "position_sizing", "patience", "adaptability",
                    ]
                },
                "learned_rules": [], "mistakes_history": [],
                "strengths": [], "weaknesses": [],
            },
            "xp_config": {
                "rewards": {
                    "profitable_trade": 10, "profitable_streak_3": 30,
                    "profitable_streak_5": 50, "perfect_entry": 15,
                    "perfect_exit": 15, "followed_rules": 5,
                    "avoided_bad_trade": 20, "learned_from_mistake": 25,
                },
                "penalties": {
                    "losing_trade": -5, "broke_rule": -20,
                    "repeated_mistake": -30, "emotional_trade": -15,
                },
            },
            "level_benefits": {
                "1": {"max_leverage": 5, "max_positions": 1, "max_risk_pct": 1},
                "5": {"max_leverage": 10, "max_positions": 2, "max_risk_pct": 1.5},
                "10": {"max_leverage": 15, "max_positions": 3, "max_risk_pct": 2},
                "20": {"max_leverage": 20, "max_positions": 4, "max_risk_pct": 2.5},
                "50": {"max_leverage": 25, "max_positions": 5, "max_risk_pct": 3},
            },
        }
