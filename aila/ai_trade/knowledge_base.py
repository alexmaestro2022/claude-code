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
            "market_regime": {},
            "daily_reviews": [],
            "weekly_trainings": [],
            "trader_profile": {
                "level": 1,
                "experience_points": 0,
                "next_level_xp": 100,
                "skills": {
                    "trend_detection": {"level": 1, "xp": 0, "max_xp": 100},
                    "entry_timing": {"level": 1, "xp": 0, "max_xp": 100},
                    "exit_timing": {"level": 1, "xp": 0, "max_xp": 100},
                    "risk_management": {"level": 1, "xp": 0, "max_xp": 100},
                    "position_sizing": {"level": 1, "xp": 0, "max_xp": 100},
                    "patience": {"level": 1, "xp": 0, "max_xp": 100},
                    "adaptability": {"level": 1, "xp": 0, "max_xp": 100},
                },
                "learned_rules": [],
                "mistakes_history": [],
                "strengths": [],
                "weaknesses": [],
            },
            "xp_config": {
                "rewards": {
                    "profitable_trade": 10,
                    "profitable_streak_3": 30,
                    "profitable_streak_5": 50,
                    "perfect_entry": 15,
                    "perfect_exit": 15,
                    "followed_rules": 5,
                    "avoided_bad_trade": 20,
                    "learned_from_mistake": 25,
                },
                "penalties": {
                    "losing_trade": -5,
                    "broke_rule": -20,
                    "repeated_mistake": -30,
                    "emotional_trade": -15,
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

    # --- XP and Skills System ---

    def add_xp(self, amount: int, skill: Optional[str] = None):
        """Add XP (general and/or to a specific skill)."""
        profile = self.data.setdefault("trader_profile", self._default_structure()["trader_profile"])

        # Add general XP
        profile["experience_points"] = max(0, profile.get("experience_points", 0) + amount)

        # Add skill XP if specified
        if skill and skill in profile.get("skills", {}):
            skill_data = profile["skills"][skill]
            skill_data["xp"] = max(0, skill_data.get("xp", 0) + amount)

            # Check skill level up
            while skill_data["xp"] >= skill_data.get("max_xp", 100):
                skill_data["xp"] -= skill_data["max_xp"]
                skill_data["level"] = skill_data.get("level", 1) + 1
                skill_data["max_xp"] = int(skill_data["max_xp"] * 1.5)
                logger.info(f"Skill {skill} leveled up to {skill_data['level']}!")

        # Check general level up
        self.check_level_up()
        self.save()

        if amount > 0:
            logger.info(f"XP gained: +{amount}" + (f" ({skill})" if skill else ""))
        elif amount < 0:
            logger.info(f"XP penalty: {amount}" + (f" ({skill})" if skill else ""))

    def check_level_up(self):
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

    def add_rule(self, rule: dict):
        """Add a learned rule."""
        profile = self.data.setdefault("trader_profile", self._default_structure()["trader_profile"])
        rules = profile.setdefault("learned_rules", [])

        rule["added_at"] = datetime.now().isoformat()
        rules.append(rule)

        # Keep last 50 rules
        profile["learned_rules"] = rules[-50:]
        self.save()
        logger.info(f"New rule learned: {rule.get('rule', '')[:60]}")

    def get_level_benefits(self) -> dict:
        """Get current limits based on trader level."""
        profile = self.data.get("trader_profile", {})
        level = profile.get("level", 1)

        level_benefits = self.data.get("level_benefits", {})

        # Find applicable level tier
        applicable = {"max_leverage": 5, "max_positions": 1, "max_risk_pct": 1}
        for tier_level, benefits in sorted(level_benefits.items(), key=lambda x: int(x[0])):
            if level >= int(tier_level):
                applicable = benefits

        return applicable

    def get_skills(self) -> dict:
        """Get current skills summary."""
        profile = self.data.get("trader_profile", {})
        return profile.get("skills", {})

    def get_trader_level(self) -> int:
        """Get current trader level."""
        return self.data.get("trader_profile", {}).get("level", 1)

    def update_market_regime(self, regime: dict):
        """Update current market regime."""
        self.data["market_regime"] = {
            **regime,
            "updated_at": datetime.now().isoformat(),
        }
        self.save()

    def save_daily_review(self, review: dict):
        """Save daily mentor review."""
        reviews = self.data.setdefault("daily_reviews", [])
        review["date"] = datetime.now().isoformat()
        reviews.append(review)
        self.data["daily_reviews"] = reviews[-30:]
        self.save()

    def save_weekly_training(self, training: dict, patterns: dict):
        """Save weekly training results."""
        trainings = self.data.setdefault("weekly_trainings", [])
        trainings.append({
            "training": training,
            "patterns": patterns,
            "date": datetime.now().isoformat(),
        })
        self.data["weekly_trainings"] = trainings[-12:]
        self.save()

    def get_today_trades(self) -> list:
        """Get all trades from today."""
        today = datetime.now().strftime("%Y-%m-%d")
        all_setups = self.data.get("successful_setups", []) + self.data.get("failed_setups", [])
        return [t for t in all_setups if t.get("timestamp", "").startswith(today)]

    def get_weekly_stats(self) -> dict:
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

    def get_all_trades(self) -> list:
        """Get all trades (successful + failed)."""
        return (
            self.data.get("successful_setups", []) +
            self.data.get("failed_setups", [])
        )
