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

        # Prioritize rules by importance: high first, then medium, then low
        # Max 15 rules: all high, up to 8 medium, up to 5 low
        prioritized_rules = self._prioritize_rules(all_rules, max_total=15)

        context: dict[str, Any] = {
            "total_trades": self.data.get("total_trades", 0),
            "win_rate": self.data.get("win_rate", 0.0),
            "total_pnl": self.data.get("total_pnl", 0.0),
            "successful_setups": all_setups[-5:],
            "mistakes_to_avoid": all_mistakes[-5:],
            "best_strategies": self.data.get("best_strategies", [])[-3:],
            "learned_rules": prioritized_rules,
        }
        if pair:
            context["pair_performance"] = {pair: self.get_pair_stats(pair)}
        return context

    def _prioritize_rules(self, rules: list, max_total: int = 15) -> list:
        """Prioritize rules by importance for prompt inclusion.

        Returns: all high-importance, up to 8 medium, up to 5 low.
        Format for prompt: 🔴 HIGH, 🟡 MEDIUM, 🟢 LOW
        """
        high, medium, low = [], [], []
        for r in rules:
            if not isinstance(r, dict):
                low.append({"rule": str(r), "importance": "low", "confirmed_count": 1})
                continue
            imp = r.get("importance", "low")
            if imp == "high":
                high.append(r)
            elif imp == "medium":
                medium.append(r)
            else:
                low.append(r)

        # Take all high, up to 8 medium (most recent), up to 5 low (most recent)
        result = high[-15:] + medium[-8:] + low[-5:]
        return result[-max_total:]

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
        """Add or confirm a learned rule with deduplication and TTL.

        New rule structure:
        {
            "rule": "text",
            "category": "entry_timing|position_management|exit_timing|risk|general",
            "source": "TRADER|SNIPER",
            "confirmed_count": 1,
            "created_at": "2026-02-05",
            "last_confirmed_at": "2026-02-05",
            "importance": "low|medium|high"
        }
        """
        profile = self.data.setdefault("trader_profile", self._default_structure()["trader_profile"])
        rules = profile.setdefault("learned_rules", [])
        now = datetime.now().isoformat()

        # Normalize rule structure
        rule_text = rule.get("rule", "")
        if not rule_text or len(rule_text) < 10:
            return

        # Check for similar existing rule (simple keyword matching)
        similar_idx = self._find_similar_rule(rules, rule_text)

        if similar_idx >= 0:
            # Confirm existing rule
            existing = rules[similar_idx]
            existing["confirmed_count"] = existing.get("confirmed_count", 1) + 1
            existing["last_confirmed_at"] = now
            # Upgrade importance if confirmed 3+ times
            if existing["confirmed_count"] >= 3:
                existing["importance"] = "high"
            elif existing["confirmed_count"] >= 2:
                existing["importance"] = "medium"
        else:
            # Add new rule with full structure
            new_rule = {
                "rule": rule_text,
                "category": rule.get("category", "general"),
                "source": rule.get("source", rule.get("agent", "TRADER")),
                "grade": rule.get("grade", "C"),
                "symbol": rule.get("symbol", ""),
                "confirmed_count": 1,
                "created_at": now,
                "last_confirmed_at": now,
                "importance": self._calc_importance(rule.get("grade", "C")),
            }
            rules.append(new_rule)

        # Clean expired rules (30 days for low, 60 for high)
        rules = self._cleanup_expired_rules(rules)

        # Limit: max 30 rules, remove oldest low-importance first
        rules = self._limit_rules(rules, max_rules=30)

        profile["learned_rules"] = rules
        self.save()

    def _find_similar_rule(self, rules: list, new_rule_text: str) -> int:
        """Find index of similar rule by keyword matching."""
        new_words = set(new_rule_text.lower().split())
        for i, r in enumerate(rules):
            existing_text = r.get("rule", "") if isinstance(r, dict) else str(r)
            existing_words = set(existing_text.lower().split())
            # If 50%+ words match, consider similar
            common = len(new_words & existing_words)
            total = min(len(new_words), len(existing_words))
            if total > 0 and common / total > 0.5:
                return i
        return -1

    def _calc_importance(self, grade: str) -> str:
        """Calculate initial importance based on grade."""
        if grade in ("A", "F"):
            return "medium"  # Strong signals deserve attention
        return "low"

    def _cleanup_expired_rules(self, rules: list) -> list:
        """Remove rules that haven't been confirmed in TTL period."""
        now = datetime.now()
        result = []
        for r in rules:
            if not isinstance(r, dict):
                continue
            last_confirmed = r.get("last_confirmed_at", r.get("created_at", ""))
            importance = r.get("importance", "low")
            ttl_days = 60 if importance == "high" else 30

            try:
                last_dt = datetime.fromisoformat(last_confirmed)
                age_days = (now - last_dt).days
                if age_days <= ttl_days:
                    result.append(r)
            except (ValueError, TypeError):
                result.append(r)  # Keep if can't parse date
        return result

    def _limit_rules(self, rules: list, max_rules: int = 30) -> list:
        """Limit rules count, removing oldest low-importance first."""
        if len(rules) <= max_rules:
            return rules

        # Sort by importance (high first) then by last_confirmed (recent first)
        def sort_key(r):
            imp_order = {"high": 0, "medium": 1, "low": 2}
            imp = imp_order.get(r.get("importance", "low"), 2)
            last = r.get("last_confirmed_at", "")
            return (imp, last)

        rules.sort(key=sort_key, reverse=True)
        return rules[:max_rules]

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
                "strengths": [], "weaknesses": [], "trade_history": [],
            },
            "sniper_profile": {
                "level": 1, "experience_points": 0, "next_level_xp": 100,
                "skills": {
                    skill: {"level": 1, "xp": 0, "max_xp": 100}
                    for skill in [
                        "breakout_detection", "entry_timing", "quick_exit",
                        "risk_management", "position_sizing", "patience", "scalping",
                    ]
                },
                "learned_rules": [], "mistakes_history": [],
                "strengths": [], "weaknesses": [], "trade_history": [],
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
