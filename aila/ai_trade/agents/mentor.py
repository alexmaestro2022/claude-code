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

            # Apply confidence adjustment to trader settings
            conf_adj = result.get("confidence_adjustment", 0)
            if isinstance(conf_adj, (int, float)) and conf_adj != 0:
                self._apply_confidence_adjustment(int(conf_adj))

            # Apply risk adjustment to trader settings
            risk_adj = result.get("risk_adjustment", "maintain")
            if risk_adj in ("increase", "decrease"):
                self._apply_risk_adjustment(risk_adj)

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

            # Apply XP penalty
            xp_penalty = result.get("xp_penalty", 0)
            if isinstance(xp_penalty, (int, float)) and xp_penalty < 0:
                skill = result.get("skill_affected", "")
                self.knowledge_base.add_xp(int(xp_penalty), skill or None)
                self.log(f"XP penalty: {xp_penalty} (skill={skill})")

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

    def _apply_confidence_adjustment(self, adjustment: int) -> None:
        """Apply confidence adjustment to TRADER settings."""
        try:
            from ..agent_settings import get_agent_settings
            settings = get_agent_settings()
            current = settings.get_settings("TRADER")
            old_conf = current.get("min_confidence", 70)
            new_conf = max(50, min(95, old_conf + adjustment))
            if new_conf != old_conf:
                settings.update_settings("TRADER", {"min_confidence": new_conf})
                self.log(f"Confidence adjusted: {old_conf} -> {new_conf} ({adjustment:+d})")
        except Exception as e:
            self.log(f"Failed to apply confidence adjustment: {e}", "error")

    def _apply_risk_adjustment(self, direction: str) -> None:
        """Apply risk adjustment to TRADER settings."""
        try:
            from ..agent_settings import get_agent_settings
            settings = get_agent_settings()
            current = settings.get_settings("TRADER")
            old_rr = current.get("min_rr_ratio", 1.5)
            # Decrease risk tolerance = raise R:R requirement
            # Increase risk tolerance = lower R:R requirement (min 1.2)
            if direction == "decrease":
                new_rr = min(5.0, round(old_rr + 0.2, 1))
            else:
                new_rr = max(1.2, round(old_rr - 0.1, 1))
            if new_rr != old_rr:
                settings.update_settings("TRADER", {"min_rr_ratio": new_rr})
                self.log(f"Risk R:R adjusted: {old_rr} -> {new_rr} ({direction})")
        except Exception as e:
            self.log(f"Failed to apply risk adjustment: {e}", "error")

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

    async def review_trade(
        self, trade_data: dict[str, Any], analyst_result: dict[str, Any]
    ) -> dict[str, Any]:
        """Review single trade after ANALYST and generate learned rules.

        Args:
            trade_data: Trade details (symbol, side, pnl, etc.)
            analyst_result: ANALYST grade and lesson

        Returns:
            Dict with generated rules and adjustments
        """
        symbol = trade_data.get("symbol", trade_data.get("pair", "unknown"))
        grade = analyst_result.get("grade", "C")
        lesson = analyst_result.get("lesson_learned", analyst_result.get("lesson", ""))
        pnl = trade_data.get("pnl", trade_data.get("pnl_usdt", 0))
        source = trade_data.get("source_agent", "TRADER")

        # Skip if no meaningful lesson
        if not lesson or grade == "?":
            return {"rules_added": 0, "skipped": True}

        prompt = f"""You are a trading mentor. Based on ANALYST feedback, create actionable rules.

TRADE RESULT:
- Symbol: {symbol}
- Side: {trade_data.get('side', '?')}
- PnL: ${pnl:.2f} ({trade_data.get('pnl_pct', 0):.1f}%)
- Grade: {grade}
- Agent: {source}

ANALYST LESSON:
{lesson}

EXISTING RULES (don't duplicate):
{json.dumps(self.knowledge_base.data.get('trader_profile', {}).get('learned_rules', [])[-5:], indent=2)}

TASK: Extract 0-2 specific, actionable rules from this trade.
Rules must be:
- Specific (include numbers, conditions)
- Actionable (what to DO or AVOID)
- Not duplicating existing rules

JSON response:
{{
    "new_rules": ["rule 1", "rule 2"] or [],
    "applies_to": "TRADER" | "SNIPER" | "BOTH",
    "confidence_adjustment": -5 to +5 (0 if neutral),
    "pattern_type": "mistake" | "success" | "neutral"
}}"""

        try:
            result = await self.claude_client.analyze(
                prompt,
                use_haiku=True,
                agent="MENTOR",
                action="review_trade",
                context=f"pair={symbol},grade={grade}",
            )

            if "error" in result:
                self.log(f"Review failed: {result.get('error')}", "error")
                return {"rules_added": 0, "error": result.get("error")}

            # Add new rules to knowledge base
            new_rules = result.get("new_rules", [])
            rules_added = 0
            applies_to = result.get("applies_to", "BOTH")

            for rule in new_rules:
                if rule and len(rule) > 10:
                    rule_entry = {
                        "rule": rule,
                        "source": "trade_review",
                        "grade": grade,
                        "symbol": symbol,
                        "agent": source,
                    }
                    self.knowledge_base.add_rule(rule_entry)
                    rules_added += 1
                    self.log(f"New rule: {rule[:60]}...")

                    # Also add to agent-specific learning
                    if applies_to in ("TRADER", "BOTH"):
                        tl = self.knowledge_base.data.setdefault("trader_learning", {})
                        lr = tl.setdefault("learned_rules", [])
                        lr.append(rule_entry)
                        tl["learned_rules"] = lr[-30:]

                    if applies_to in ("SNIPER", "BOTH"):
                        sl = self.knowledge_base.data.setdefault("sniper_learning", {})
                        lr = sl.setdefault("learned_rules", [])
                        lr.append(rule_entry)
                        sl["learned_rules"] = lr[-30:]

            # Apply confidence adjustment
            conf_adj = result.get("confidence_adjustment", 0)
            if isinstance(conf_adj, int) and conf_adj != 0:
                self._apply_confidence_adjustment(conf_adj)

            # Process management_lessons from ANALYST (position management rules)
            management_lessons = analyst_result.get("management_lessons", [])
            mgmt_rules_added = 0
            for lesson in management_lessons[:3]:  # Max 3 management lessons per trade
                if lesson and len(lesson) > 10:
                    mgmt_rule_entry = {
                        "rule": lesson,
                        "category": "position_management",  # NEW category
                        "source": "analyst_management_lesson",
                        "grade": grade,
                        "symbol": symbol,
                        "agent": source,
                    }
                    # Add to agent-specific management rules
                    learning_key = f"{source.lower()}_learning"
                    agent_learning = self.knowledge_base.data.setdefault(learning_key, {})
                    mgmt_rules = agent_learning.setdefault("management_rules", [])
                    mgmt_rules.append(mgmt_rule_entry)
                    agent_learning["management_rules"] = mgmt_rules[-15:]  # Keep last 15
                    mgmt_rules_added += 1
                    self.log(f"[{source}] Mgmt rule: {lesson[:50]}...")

            self.knowledge_base.save()

            self.log(
                f"Trade review: {symbol} grade={grade} → {rules_added} rules, {mgmt_rules_added} mgmt rules"
            )

            return {
                "rules_added": rules_added + mgmt_rules_added,
                "new_rules": new_rules,
                "management_rules_added": mgmt_rules_added,
                "applies_to": applies_to,
                "pattern_type": result.get("pattern_type", "neutral"),
            }

        except Exception as e:
            self.log(f"Review trade error: {e}", "error")
            return {"rules_added": 0, "error": str(e)}
