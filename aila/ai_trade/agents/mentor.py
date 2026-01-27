"""MENTOR - trains TRADER through daily reviews and corrections."""

import json
from typing import Any

from .base_agent import BaseAgent


class MentorAgent(BaseAgent):
    """Trains TRADER: daily reviews, mistake correction, rule formation."""

    def __init__(self, claude_client: Any, knowledge_base: Any) -> None:
        super().__init__(
            name="MENTOR",
            claude_client=claude_client,
            knowledge_base=knowledge_base,
            log_path="/opt/aila/logs/ai_trade/mentor.log",
        )

    async def think(self, context: dict[str, Any]) -> dict[str, Any]:
        """Process mentoring context."""
        action = context.get("action", "review")
        if action == "daily_review":
            return await self.daily_review(context.get("trades", []))
        elif action == "correct":
            return await self.correct_mistake(context.get("bad_decision", {}))
        elif action == "weekly":
            return await self.weekly_training(context.get("weekly_stats", {}))
        return {"error": "Unknown action"}

    async def daily_review(self, trades: list) -> dict:
        """Daily review of all trades."""
        if not trades:
            return {"grade": "N/A", "message": "No trades today"}

        skills = self.knowledge_base.get_skills()
        rules = self.knowledge_base.data.get("trader_profile", {}).get("learned_rules", [])

        prompt = f"""You are an experienced trading mentor with 30 years of experience.
Review today's trades and provide feedback.

TODAY'S TRADES:
{json.dumps(trades, indent=2)}

CURRENT TRADER SKILLS:
{json.dumps(skills, indent=2)}

CURRENT RULES:
{json.dumps(rules[-10:], indent=2)}

ANALYZE AND RESPOND IN JSON:

{{
    "grade": "A" | "B" | "C" | "D" | "F",
    "good_decisions": ["what was done well"],
    "repeated_mistakes": ["which mistakes are repeating"],
    "recommendations": ["specific recommendations for tomorrow"],
    "skill_to_improve": "which skill to develop",
    "new_rules": ["rules TRADER must remember"],
    "stop_doing": "what to stop doing",
    "start_doing": "what to start doing",
    "confidence_adjustment": -10 to +10,
    "risk_adjustment": "increase" | "decrease" | "maintain"
}}
"""
        result = await self.claude_client.analyze(
            prompt, use_haiku=True, agent="MENTOR", action="daily_review", context="type=daily"
        )

        if "error" not in result:
            self.log(f"Daily review: Grade={result.get('grade')}")

            # Save new rules
            new_rules = result.get("new_rules", [])
            for rule in new_rules:
                if rule:
                    self.knowledge_base.add_rule({
                        "rule": rule,
                        "source": "daily_review",
                        "grade": result.get("grade"),
                    })

            # Log repeated mistakes
            repeated = result.get("repeated_mistakes", [])
            if repeated:
                self.log(f"Repeated mistakes: {repeated}", "warning")

        return result

    async def correct_mistake(self, bad_decision: dict) -> dict:
        """Immediate correction when a bad decision is detected."""
        self.log(f"Correcting mistake: {bad_decision.get('trade', {}).get('symbol', 'unknown')}")

        prompt = f"""TRADER made a bad decision. Correct them immediately.

BAD DECISION:
{json.dumps(bad_decision, indent=2)}

KNOWN MISTAKES TO AVOID:
{json.dumps(self.knowledge_base.data.get('mistakes_to_avoid', [])[-5:], indent=2)}

Explain briefly and respond in JSON:

{{
    "explanation": "why this was bad",
    "correct_action": "what should have been done",
    "new_rule": "rule to prevent repetition",
    "severity": "low" | "medium" | "high",
    "skill_affected": "trend_detection" | "entry_timing" | "exit_timing" | "risk_management" | "position_sizing" | "patience" | "adaptability",
    "xp_penalty": -5 to -30
}}
"""
        result = await self.claude_client.analyze(
            prompt, use_haiku=True, agent="MENTOR", action="correction",
            context=f"pair={bad_decision.get('trade', {}).get('symbol', 'unknown')}"
        )

        if "error" not in result:
            # Add new rule if provided
            new_rule = result.get("new_rule")
            if new_rule:
                self.knowledge_base.add_rule({
                    "rule": new_rule,
                    "source": "mistake_correction",
                    "severity": result.get("severity", "medium"),
                })

            # Record mistake in history
            profile = self.knowledge_base.data.get("trader_profile", {})
            mistakes_history = profile.get("mistakes_history", [])
            mistakes_history.append({
                "mistake": result.get("explanation", ""),
                "correction": result.get("correct_action", ""),
                "skill": result.get("skill_affected", ""),
                "timestamp": bad_decision.get("timestamp", ""),
            })
            profile["mistakes_history"] = mistakes_history[-50:]
            self.knowledge_base.data["trader_profile"] = profile
            self.knowledge_base.save()

            self.log(f"Correction applied: {result.get('explanation', '')[:80]}")

        return result

    async def weekly_training(self, weekly_stats: dict) -> dict:
        """Weekly deep analysis and training session."""
        if not weekly_stats:
            return {"message": "No weekly stats available"}

        skills = self.knowledge_base.get_skills()
        profile = self.knowledge_base.data.get("trader_profile", {})

        prompt = f"""You are a trading mentor conducting a weekly training session.
Provide deep analysis and training plan.

WEEKLY STATISTICS:
{json.dumps(weekly_stats, indent=2)}

CURRENT SKILL LEVELS:
{json.dumps(skills, indent=2)}

TRADER PROFILE:
- Level: {profile.get('level', 1)}
- XP: {profile.get('experience_points', 0)}
- Strengths: {json.dumps(profile.get('strengths', []))}
- Weaknesses: {json.dumps(profile.get('weaknesses', []))}

RECENT MISTAKES:
{json.dumps(profile.get('mistakes_history', [])[-10:], indent=2)}

LEARNED RULES:
{json.dumps(profile.get('learned_rules', [])[-10:], indent=2)}

Respond in JSON:

{{
    "weekly_grade": "A" | "B" | "C" | "D" | "F",
    "progress_summary": "overall progress description",
    "strengths_confirmed": ["confirmed strengths"],
    "weaknesses_identified": ["identified weaknesses"],
    "training_focus": "main area to focus on next week",
    "strategy_adjustments": [
        {{"parameter": "name", "current": "value", "suggested": "value", "reason": "why"}}
    ],
    "rules_to_reinforce": ["rules that need reinforcement"],
    "rules_to_remove": ["outdated rules to remove"],
    "skill_xp_bonuses": {{
        "skill_name": 10
    }},
    "motivation": "motivational message for the trader"
}}
"""
        result = await self.claude_client.analyze(
            prompt, use_haiku=True, agent="MENTOR", action="weekly_training", context="type=weekly"
        )

        if "error" not in result:
            self.log(f"Weekly training: Grade={result.get('weekly_grade')}, Focus={result.get('training_focus')}")

            # Update strengths/weaknesses
            profile = self.knowledge_base.data.get("trader_profile", {})
            if result.get("strengths_confirmed"):
                profile["strengths"] = result["strengths_confirmed"]
            if result.get("weaknesses_identified"):
                profile["weaknesses"] = result["weaknesses_identified"]
            self.knowledge_base.data["trader_profile"] = profile

            # Apply skill XP bonuses
            bonuses = result.get("skill_xp_bonuses", {})
            for skill, xp in bonuses.items():
                if isinstance(xp, (int, float)) and xp > 0:
                    self.knowledge_base.add_xp(int(xp), skill)

            self.knowledge_base.save()

        return result

    async def evaluate_decision_quality(self, opportunity: dict, market_context: dict) -> dict:
        """Evaluate the quality of a trading decision before execution."""
        rules = self.knowledge_base.data.get("trader_profile", {}).get("learned_rules", [])

        # Check if any rules are being violated
        violations = []
        for rule_entry in rules[-20:]:
            rule = rule_entry.get("rule", "") if isinstance(rule_entry, dict) else str(rule_entry)
            # Simple keyword matching for quick check
            if "leverage" in rule.lower() and opportunity.get("leverage", 1) > 10:
                violations.append(rule)
            if "wait" in rule.lower() and opportunity.get("confidence", 0) < 75:
                violations.append(rule)

        if violations:
            self.log(f"Rule violations detected: {violations}", "warning")

        return {
            "violations": violations,
            "should_proceed": len(violations) == 0,
            "warnings": violations,
        }
