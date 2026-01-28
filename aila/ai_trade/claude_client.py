"""Claude API client for AI Trade module."""

import json
import logging
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Any, Optional

import anthropic

from ..utils.common import retry_async
from .config import ANTHROPIC_API_KEY, CLAUDE_MODEL, CLAUDE_MODEL_HAIKU

logger = logging.getLogger("ai_trade")

# Setup dedicated API usage logger
api_usage_logger = logging.getLogger("ai_trade.api_usage")
_api_log_path = Path("/opt/aila/logs/ai_trade/api_usage.log")
_api_log_path.parent.mkdir(parents=True, exist_ok=True)
_api_handler = logging.FileHandler(_api_log_path, encoding="utf-8")
_api_handler.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", "%Y-%m-%d %H:%M:%S"))
api_usage_logger.addHandler(_api_handler)
api_usage_logger.setLevel(logging.INFO)
api_usage_logger.propagate = False

# Pricing per 1M tokens (Jan 2025)
PRICING = {
    "sonnet": {"input": 3.0, "output": 15.0},  # $3/$15 per 1M tokens
    "haiku": {"input": 0.25, "output": 1.25},  # $0.25/$1.25 per 1M tokens
}


class APIUsageTracker:
    """Tracks Claude API usage with persistent daily/monthly storage and per-agent stats."""

    __slots__ = (
        "_session_start", "_data_path", "_data",
        "_daily_cost_warning", "_monthly_cost_warning",
        "_orchestrator_ref",
    )

    def __init__(self, data_path: str = "/opt/aila/data/ai_trade/api_usage.json") -> None:
        self._session_start = datetime.utcnow()
        self._data_path = Path(data_path)
        self._daily_cost_warning = 5.0  # Warning if daily cost > $5
        self._monthly_cost_warning = 100.0  # Warning if monthly cost > $100
        self._orchestrator_ref = None  # Will be set by orchestrator
        self._data = self._load_data()
        self._check_daily_reset()

    def _load_data(self) -> dict[str, Any]:
        """Load usage data from file."""
        default = {
            "session": self._empty_session(),
            "daily": {},
            "monthly": {},
            "total": {"calls": 0, "cost": 0.0, "input_tokens": 0, "output_tokens": 0},
            "agents": {},  # Per-agent statistics
            "warnings": [],
            "budget": {
                "initial_balance": 50.0,  # Default initial balance $50
                "accumulated_cost": 0.0,  # Total cost since last reset
                "last_reset_date": None,  # Last full reset date
                "daily_reset_date": date.today().isoformat(),  # Last daily reset
            },
            "settings": {
                "use_budget_limit": False,  # Toggle for budget limit
                "daily_limit": 10.0,  # Daily limit in USD
                "total_limit": 50.0,  # Total limit in USD
            },
        }
        try:
            if self._data_path.exists():
                with open(self._data_path, "r") as f:
                    data = json.load(f)
                    # Reset session on load
                    data["session"] = self._empty_session()
                    # Ensure agents dict exists
                    if "agents" not in data:
                        data["agents"] = {}
                    # Ensure budget dict exists with all fields
                    if "budget" not in data:
                        data["budget"] = default["budget"].copy()
                        # Migrate: set accumulated_cost from total cost
                        if data.get("total", {}).get("cost", 0) > 0:
                            data["budget"]["accumulated_cost"] = data["total"]["cost"]
                    else:
                        for key, val in default["budget"].items():
                            if key not in data["budget"]:
                                data["budget"][key] = val
                        # Migrate: if accumulated_cost is 0 but we have total cost
                        if data["budget"].get("accumulated_cost", 0) == 0 and data.get("total", {}).get("cost", 0) > 0:
                            data["budget"]["accumulated_cost"] = data["total"]["cost"]
                    # Ensure settings dict exists with all fields
                    if "settings" not in data:
                        data["settings"] = default["settings"]
                    else:
                        for key, val in default["settings"].items():
                            if key not in data["settings"]:
                                data["settings"][key] = val
                    return data
        except Exception as e:
            logger.error(f"Failed to load API usage data: {e}")
        return default

    def _empty_session(self) -> dict[str, Any]:
        """Create empty session data structure."""
        return {
            "start_time": self._session_start.isoformat(),
            "calls": {"sonnet": 0, "haiku": 0},
            "input_tokens": {"sonnet": 0, "haiku": 0},
            "output_tokens": {"sonnet": 0, "haiku": 0},
        }

    def _save_data(self) -> None:
        """Save usage data to file."""
        try:
            self._data_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._data_path, "w") as f:
                json.dump(self._data, f, indent=2, default=str)
        except Exception as e:
            logger.error(f"Failed to save API usage data: {e}")

    def _check_daily_reset(self) -> None:
        """Check if we need to reset daily cost at start of new day."""
        today = date.today().isoformat()
        last_daily_reset = self._data.get("budget", {}).get("daily_reset_date")
        if last_daily_reset != today:
            # New day - reset daily cost but keep accumulated_cost
            logger.info(f"[API_USAGE] New day detected, resetting daily cost. Previous: {last_daily_reset}")
            self._data["budget"]["daily_reset_date"] = today
            self._save_data()

    def set_orchestrator(self, orchestrator: Any) -> None:
        """Set orchestrator reference for auto-stop functionality."""
        self._orchestrator_ref = orchestrator

    async def _stop_autopilot_on_limit(self, reason: str) -> None:
        """Stop autopilot when budget limit reached."""
        if self._orchestrator_ref is None:
            logger.warning("[API_USAGE] Cannot stop autopilot - no orchestrator reference")
            return
        try:
            # Stop autopilot
            if hasattr(self._orchestrator_ref, "autopilot"):
                await self._orchestrator_ref.stop_autopilot()
                logger.warning(f"[API_USAGE] Autopilot stopped: {reason}")

            # Send Telegram notification
            if hasattr(self._orchestrator_ref, "telegram") and self._orchestrator_ref.telegram:
                await self._orchestrator_ref.telegram.send_message(
                    f"<b>API LIMIT REACHED</b>\n\n{reason}\n\n<i>Autopilot has been stopped automatically.</i>"
                )
        except Exception as e:
            logger.error(f"[API_USAGE] Failed to stop autopilot: {e}")

    def check_budget_limit(self) -> tuple[bool, str]:
        """
        Check if budget limit is reached.
        Returns (is_limit_reached, reason).
        """
        settings = self._data.get("settings", {})
        if not settings.get("use_budget_limit", False):
            return False, ""

        budget = self._data.get("budget", {})
        today = date.today().isoformat()

        # Get today's cost
        today_data = self._data["daily"].get(today, {"cost": {"sonnet": 0.0, "haiku": 0.0}})
        daily_cost = sum(today_data.get("cost", {}).values())

        # Get accumulated cost
        accumulated_cost = budget.get("accumulated_cost", 0.0)

        # Check daily limit
        daily_limit = settings.get("daily_limit", 10.0)
        if daily_limit > 0 and daily_cost >= daily_limit:
            return True, f"Daily API limit ${daily_limit:.2f} reached (spent today: ${daily_cost:.2f})"

        # Check total limit
        total_limit = settings.get("total_limit", 50.0)
        if total_limit > 0 and accumulated_cost >= total_limit:
            return True, f"Total API limit ${total_limit:.2f} reached (total spent: ${accumulated_cost:.2f})"

        return False, ""

    def reset(self, initial_balance: float = 50.0) -> dict[str, Any]:
        """
        Reset all usage statistics and set new initial balance.
        Returns reset confirmation.
        """
        now = datetime.utcnow()
        today = date.today().isoformat()

        # Save old stats for return
        old_accumulated = self._data.get("budget", {}).get("accumulated_cost", 0.0)
        old_total = self._data.get("total", {}).get("cost", 0.0)

        # Reset all counters
        self._data["session"] = self._empty_session()
        self._data["daily"] = {}
        self._data["monthly"] = {}
        self._data["total"] = {"calls": 0, "cost": 0.0, "input_tokens": 0, "output_tokens": 0}
        self._data["agents"] = {}
        self._data["warnings"] = []

        # Set new budget
        self._data["budget"] = {
            "initial_balance": initial_balance,
            "accumulated_cost": 0.0,
            "last_reset_date": now.isoformat(),
            "daily_reset_date": today,
        }

        self._save_data()
        logger.info(f"[API_USAGE] Reset completed. New balance: ${initial_balance:.2f}")

        return {
            "success": True,
            "initial_balance": initial_balance,
            "previous_accumulated_cost": round(old_accumulated, 4),
            "previous_total_cost": round(old_total, 4),
            "reset_time": now.isoformat(),
        }

    def get_budget_settings(self) -> dict[str, Any]:
        """Get current budget settings."""
        settings = self._data.get("settings", {})
        budget = self._data.get("budget", {})
        today = date.today().isoformat()

        # Calculate current costs
        today_data = self._data["daily"].get(today, {"cost": {"sonnet": 0.0, "haiku": 0.0}})
        daily_cost = sum(today_data.get("cost", {}).values())
        accumulated_cost = budget.get("accumulated_cost", 0.0)
        initial_balance = budget.get("initial_balance", 50.0)

        return {
            "use_budget_limit": settings.get("use_budget_limit", False),
            "daily_limit": settings.get("daily_limit", 10.0),
            "total_limit": settings.get("total_limit", 50.0),
            "initial_balance": initial_balance,
            "accumulated_cost": round(accumulated_cost, 4),
            "daily_cost": round(daily_cost, 4),
            "remaining_balance": round(max(0, initial_balance - accumulated_cost), 4),
            "daily_pct": round(daily_cost / max(settings.get("daily_limit", 10.0), 0.01) * 100, 1),
            "total_pct": round(accumulated_cost / max(settings.get("total_limit", 50.0), 0.01) * 100, 1),
            "last_reset_date": budget.get("last_reset_date"),
        }

    def update_settings(
        self,
        use_budget_limit: bool = None,
        daily_limit: float = None,
        total_limit: float = None,
    ) -> dict[str, Any]:
        """Update budget limit settings."""
        settings = self._data.setdefault("settings", {})

        if use_budget_limit is not None:
            settings["use_budget_limit"] = use_budget_limit
        if daily_limit is not None:
            settings["daily_limit"] = max(0.0, daily_limit)
        if total_limit is not None:
            settings["total_limit"] = max(0.0, total_limit)

        self._save_data()
        logger.info(f"[API_USAGE] Settings updated: {settings}")

        return {"success": True, "settings": settings}

    def _calculate_cost(self, model_key: str, input_tokens: int, output_tokens: int) -> float:
        """Calculate cost in USD for given tokens."""
        pricing = PRICING.get(model_key, PRICING["sonnet"])
        return (
            input_tokens * pricing["input"] / 1_000_000
            + output_tokens * pricing["output"] / 1_000_000
        )

    def record(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        agent: str = "UNKNOWN",
        action: str = "analyze",
        context: str = "",
    ) -> Optional[str]:
        """
        Record API call usage with detailed logging.
        Returns warning message if cost threshold exceeded.
        """
        key = "haiku" if "haiku" in model.lower() else "sonnet"
        cost = self._calculate_cost(key, input_tokens, output_tokens)
        today = date.today().isoformat()
        month = today[:7]  # YYYY-MM

        # Check if day changed
        self._check_daily_reset()

        # Log detailed call to api_usage.log
        ctx = f" | {context}" if context else ""
        api_usage_logger.info(
            f"{agent} {action} | model={key} | input={input_tokens} | "
            f"output={output_tokens} | cost=${cost:.4f}{ctx}"
        )

        # Update session
        self._data["session"]["calls"][key] += 1
        self._data["session"]["input_tokens"][key] += input_tokens
        self._data["session"]["output_tokens"][key] += output_tokens

        # Update daily
        if today not in self._data["daily"]:
            self._data["daily"][today] = {
                "calls": {"sonnet": 0, "haiku": 0},
                "cost": {"sonnet": 0.0, "haiku": 0.0},
                "input_tokens": {"sonnet": 0, "haiku": 0},
                "output_tokens": {"sonnet": 0, "haiku": 0},
                "agents": {},  # Per-agent daily stats
            }
        daily = self._data["daily"][today]
        daily["calls"][key] += 1
        daily["cost"][key] += cost
        daily["input_tokens"][key] += input_tokens
        daily["output_tokens"][key] += output_tokens

        # Update daily per-agent stats
        if "agents" not in daily:
            daily["agents"] = {}
        if agent not in daily["agents"]:
            daily["agents"][agent] = {"calls": 0, "cost": 0.0, "input_tokens": 0, "output_tokens": 0}
        daily["agents"][agent]["calls"] += 1
        daily["agents"][agent]["cost"] += cost
        daily["agents"][agent]["input_tokens"] += input_tokens
        daily["agents"][agent]["output_tokens"] += output_tokens

        # Update monthly
        if month not in self._data["monthly"]:
            self._data["monthly"][month] = {
                "calls": {"sonnet": 0, "haiku": 0},
                "cost": {"sonnet": 0.0, "haiku": 0.0},
            }
        self._data["monthly"][month]["calls"][key] += 1
        self._data["monthly"][month]["cost"][key] += cost

        # Update total
        self._data["total"]["calls"] += 1
        self._data["total"]["cost"] += cost
        self._data["total"]["input_tokens"] += input_tokens
        self._data["total"]["output_tokens"] += output_tokens

        # Update accumulated_cost (tracks cost since last reset)
        self._data.setdefault("budget", {})
        self._data["budget"]["accumulated_cost"] = self._data["budget"].get("accumulated_cost", 0.0) + cost

        # Update global per-agent stats
        if agent not in self._data["agents"]:
            self._data["agents"][agent] = {
                "calls": 0, "cost": 0.0, "input_tokens": 0, "output_tokens": 0,
                "avg_cost": 0.0, "avg_input": 0, "avg_output": 0,
            }
        agent_data = self._data["agents"][agent]
        agent_data["calls"] += 1
        agent_data["cost"] += cost
        agent_data["input_tokens"] += input_tokens
        agent_data["output_tokens"] += output_tokens
        # Update averages
        agent_data["avg_cost"] = agent_data["cost"] / agent_data["calls"]
        agent_data["avg_input"] = agent_data["input_tokens"] // agent_data["calls"]
        agent_data["avg_output"] = agent_data["output_tokens"] // agent_data["calls"]

        # Save periodically (every 10 calls)
        if self._data["total"]["calls"] % 10 == 0:
            self._save_data()

        # Check budget limits and auto-stop if needed
        limit_reached, limit_reason = self.check_budget_limit()
        if limit_reached and self._orchestrator_ref:
            import asyncio
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(self._stop_autopilot_on_limit(limit_reason))
                else:
                    loop.run_until_complete(self._stop_autopilot_on_limit(limit_reason))
            except Exception as e:
                logger.error(f"[API_USAGE] Failed to trigger auto-stop: {e}")

        # Check warnings
        warning = None
        daily_total = sum(self._data["daily"].get(today, {}).get("cost", {}).values())
        monthly_total = sum(self._data["monthly"].get(month, {}).get("cost", {}).values())

        if limit_reached:
            warning = f"LIMIT_REACHED: {limit_reason}"
        elif daily_total >= self._daily_cost_warning:
            warning = f"Daily API cost ${daily_total:.2f} exceeds ${self._daily_cost_warning} limit!"
        elif monthly_total >= self._monthly_cost_warning:
            warning = f"Monthly API cost ${monthly_total:.2f} exceeds ${self._monthly_cost_warning} limit!"

        if warning and warning not in self._data.get("warnings", []):
            self._data.setdefault("warnings", []).append(warning)
            logger.warning(warning)

        return warning

    def get_stats(self) -> dict[str, Any]:
        """Get comprehensive usage statistics."""
        today = date.today().isoformat()
        month = today[:7]

        # Session stats
        session = self._data["session"]
        session_runtime = (datetime.utcnow() - self._session_start).total_seconds()
        session_cost = (
            self._calculate_cost("sonnet", session["input_tokens"]["sonnet"], session["output_tokens"]["sonnet"])
            + self._calculate_cost("haiku", session["input_tokens"]["haiku"], session["output_tokens"]["haiku"])
        )

        # Today stats
        today_data = self._data["daily"].get(today, {
            "calls": {"sonnet": 0, "haiku": 0},
            "cost": {"sonnet": 0.0, "haiku": 0.0},
            "agents": {},
        })
        today_calls = sum(today_data.get("calls", {}).values())
        today_cost = sum(today_data.get("cost", {}).values())

        # Month stats
        month_data = self._data["monthly"].get(month, {
            "calls": {"sonnet": 0, "haiku": 0},
            "cost": {"sonnet": 0.0, "haiku": 0.0},
        })
        month_calls = sum(month_data.get("calls", {}).values())
        month_cost = sum(month_data.get("cost", {}).values())

        # Budget stats
        budget_data = self._data.get("budget", {})
        settings = self._data.get("settings", {})
        initial_balance = budget_data.get("initial_balance", 50.0)
        accumulated_cost = budget_data.get("accumulated_cost", 0.0)
        daily_limit = settings.get("daily_limit", 10.0)
        total_limit = settings.get("total_limit", 50.0)
        use_budget_limit = settings.get("use_budget_limit", False)

        # Check if limit reached
        limit_reached, limit_reason = self.check_budget_limit()

        return {
            "session": {
                "start_time": session["start_time"],
                "runtime_hours": round(session_runtime / 3600, 2),
                "calls": session["calls"],
                "total_calls": sum(session["calls"].values()),
                "input_tokens": session["input_tokens"],
                "output_tokens": session["output_tokens"],
                "cost_usd": round(session_cost, 4),
                "cost_per_hour": round(session_cost / max(session_runtime / 3600, 0.01), 4),
            },
            "today": {
                "date": today,
                "calls": today_data.get("calls", {}),
                "total_calls": today_calls,
                "cost_usd": round(today_cost, 4),
                "cost_breakdown": today_data.get("cost", {}),
                "warning": today_cost >= self._daily_cost_warning,
                "agents": today_data.get("agents", {}),
            },
            "month": {
                "month": month,
                "calls": month_data.get("calls", {}),
                "total_calls": month_calls,
                "cost_usd": round(month_cost, 4),
                "cost_breakdown": month_data.get("cost", {}),
                "warning": month_cost >= self._monthly_cost_warning,
            },
            "total": {
                "all_time_calls": self._data["total"]["calls"],
                "all_time_cost_usd": round(self._data["total"]["cost"], 4),
                "all_time_input_tokens": self._data["total"]["input_tokens"],
                "all_time_output_tokens": self._data["total"]["output_tokens"],
            },
            "limits": {
                "daily_warning_usd": self._daily_cost_warning,
                "monthly_warning_usd": self._monthly_cost_warning,
            },
            "budget": {
                "initial_balance": initial_balance,
                "accumulated_cost": round(accumulated_cost, 4),
                "daily_cost": round(today_cost, 4),
                "remaining_balance": round(max(0, initial_balance - accumulated_cost), 4),
                "last_reset_date": budget_data.get("last_reset_date"),
                "daily_reset_date": budget_data.get("daily_reset_date"),
                # Limit settings
                "use_budget_limit": use_budget_limit,
                "daily_limit": daily_limit,
                "total_limit": total_limit,
                # Percentages
                "daily_pct": round(today_cost / max(daily_limit, 0.01) * 100, 1) if daily_limit > 0 else 0,
                "total_pct": round(accumulated_cost / max(total_limit, 0.01) * 100, 1) if total_limit > 0 else 0,
                # Limit status
                "limit_reached": limit_reached,
                "limit_reason": limit_reason,
            },
            "week": {
                "cost_usd": self.get_week_cost(),
            },
            "pricing": PRICING,
        }

    def get_top_consumers(self, limit: int = 10) -> list[dict[str, Any]]:
        """Get top API consumers by cost."""
        agents = self._data.get("agents", {})
        sorted_agents = sorted(
            [
                {
                    "agent": name,
                    "calls": data["calls"],
                    "cost": round(data["cost"], 4),
                    "avg_cost": round(data["avg_cost"], 6),
                    "avg_input": data["avg_input"],
                    "avg_output": data["avg_output"],
                    "pct_of_total": round(
                        data["cost"] / max(self._data["total"]["cost"], 0.0001) * 100, 1
                    ),
                }
                for name, data in agents.items()
            ],
            key=lambda x: x["cost"],
            reverse=True,
        )
        return sorted_agents[:limit]

    def get_today_top_consumers(self, limit: int = 10) -> list[dict[str, Any]]:
        """Get today's top API consumers by cost."""
        today = date.today().isoformat()
        today_data = self._data["daily"].get(today, {})
        agents = today_data.get("agents", {})
        today_total = sum(today_data.get("cost", {}).values())

        sorted_agents = sorted(
            [
                {
                    "agent": name,
                    "calls": data["calls"],
                    "cost": round(data["cost"], 4),
                    "avg_cost": round(data["cost"] / max(data["calls"], 1), 6),
                    "pct_of_today": round(data["cost"] / max(today_total, 0.0001) * 100, 1),
                }
                for name, data in agents.items()
            ],
            key=lambda x: x["cost"],
            reverse=True,
        )
        return sorted_agents[:limit]

    def set_warning_limits(self, daily: float = None, monthly: float = None) -> None:
        """Set warning limits for daily/monthly cost."""
        if daily is not None:
            self._daily_cost_warning = daily
        if monthly is not None:
            self._monthly_cost_warning = monthly

    def get_week_cost(self) -> float:
        """Calculate total cost for the last 7 days."""
        week_cost = 0.0
        today = date.today()
        for i in range(7):
            day = (today - timedelta(days=i)).isoformat()
            day_data = self._data["daily"].get(day, {})
            week_cost += sum(day_data.get("cost", {}).values())
        return round(week_cost, 4)

    def get_recent_warnings(self) -> list[str]:
        """Get recent warnings."""
        return self._data.get("warnings", [])[-10:]

    def clear_warnings(self) -> None:
        """Clear all warnings."""
        self._data["warnings"] = []
        self._save_data()

    def force_save(self) -> None:
        """Force save data to file."""
        self._save_data()


# Global usage tracker
api_usage = APIUsageTracker()


class ClaudeClient:
    """Async client for Claude API interactions with retry logic."""

    __slots__ = ("_client", "_model", "_model_haiku")

    def __init__(self) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
        self._model = CLAUDE_MODEL
        self._model_haiku = CLAUDE_MODEL_HAIKU

    @retry_async(max_attempts=3, base_delay=2.0, exceptions=(anthropic.APIError,))
    async def analyze(
        self,
        prompt: str,
        max_tokens: int = 4096,
        use_haiku: bool = False,
        agent: str = "UNKNOWN",
        action: str = "analyze",
        context: str = "",
    ) -> dict[str, Any]:
        """Send prompt to Claude and return parsed JSON response."""
        model = self._model_haiku if use_haiku else self._model
        try:
            message = await self._client.messages.create(
                model=model,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            response_text = message.content[0].text

            # Record usage with detailed logging
            warning = api_usage.record(
                model=model,
                input_tokens=message.usage.input_tokens,
                output_tokens=message.usage.output_tokens,
                agent=agent,
                action=action,
                context=context,
            )
            if warning:
                logger.warning(warning)

            return self._extract_json(response_text)
        except anthropic.APIError:
            raise
        except Exception as e:
            logger.error(f"Claude API error: {e}")
            return {"error": str(e)}

    async def batch_analyze_market(
        self, pairs_data: list[dict[str, Any]], knowledge: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Batch analyze all pairs in a single API call.
        Returns the best opportunity or WAIT decision.
        """
        if not pairs_data:
            return {"decision": "WAIT", "reason": "No pairs to analyze"}

        prompt = self._build_batch_market_prompt(pairs_data, knowledge)
        return await self.analyze(
            prompt,
            max_tokens=4096,
            use_haiku=False,
            agent="TRADER",
            action="batch_analyze",
            context=f"pairs={len(pairs_data)}",
        )

    async def get_market_analysis(
        self, pair: str, market_data: dict[str, Any], knowledge: dict[str, Any]
    ) -> dict[str, Any]:
        """Analyze market and make entry decision (legacy single-pair method)."""
        prompt = self._build_market_prompt(pair, market_data, knowledge)
        return await self.analyze(
            prompt,
            agent="TRADER",
            action="single_analyze",
            context=f"pair={pair}",
        )

    async def analyze_trade_result(self, trade: dict[str, Any]) -> dict[str, Any]:
        """Analyze completed trade for learning (uses Haiku - non-critical)."""
        prompt = self._build_trade_analysis_prompt(trade)
        pair = trade.get("symbol", trade.get("pair", "unknown"))
        return await self.analyze(
            prompt,
            use_haiku=True,
            agent="ANALYST",
            action="trade_result",
            context=f"pair={pair}",
        )

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any]:
        """Extract JSON from response text."""
        start = text.find("{")
        end = text.rfind("}") + 1
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                pass
        return {"raw_response": text}

    @staticmethod
    def _build_batch_market_prompt(
        pairs_data: list[dict[str, Any]], knowledge: dict[str, Any]
    ) -> str:
        """Build batch market analysis prompt for all pairs."""
        setups = knowledge.get("successful_setups", [])[-5:]
        mistakes = knowledge.get("mistakes_to_avoid", [])[-5:]

        # Format pairs data compactly
        pairs_summary = []
        for p in pairs_data:
            md = p.get("market_data", {})
            pairs_summary.append({
                "symbol": p["symbol"],
                "price": md.get("price"),
                "change_24h": md.get("change_24h"),
                "volume_24h": md.get("volume_24h"),
                "rsi": md.get("rsi"),
                "trend": md.get("trend"),
                "ema50": md.get("ema50"),
                "ema200": md.get("ema200"),
                "atr": md.get("atr"),
            })

        return f"""You are an expert cryptocurrency trader. Analyze ALL {len(pairs_data)} pairs and select the SINGLE BEST trading opportunity.

## ALL PAIRS DATA
{json.dumps(pairs_summary, indent=1)}

## RECENT SUCCESSFUL TRADES
{json.dumps(setups, indent=2)}

## MISTAKES TO AVOID
{json.dumps(mistakes, indent=2)}

## RISK MANAGEMENT RULES (MANDATORY)
1. Risk/Reward ratio MUST be >= 1.5:1
2. Stop loss: min 3% for volatile coins, 2% for stable (BTC, ETH)
3. Leverage: max 2x for meme/volatile, max 3x for majors
4. Position size: 2-4% of capital
5. NEVER go LONG in BEARISH trend, NEVER go SHORT in BULLISH trend

## ANALYSIS CRITERIA
- Look for strong trends with RSI confirmation
- Prefer pairs with high volume (>$10M daily)
- Check for trend alignment (price vs EMA50 vs EMA200)
- Consider volatility (ATR) for stop loss calculation
- LONG: price > EMA50 > EMA200, RSI 40-70, trend=BULLISH
- SHORT: price < EMA50 < EMA200, RSI 30-60, trend=BEARISH (SHORT is SELLING, profit when price DROPS)

## TASK
Analyze all pairs and respond STRICTLY in JSON:
{{
    "decision": "LONG" | "SHORT" | "WAIT",
    "pair": "SYMBOL/USDT or null if WAIT",
    "confidence": 0-100,
    "strategy": "strategy name",
    "entry_price": number or null,
    "stop_loss": number or null,
    "take_profit": number or null,
    "leverage": 1-3,
    "position_size_pct": 2-4,
    "reasoning": "why this pair is the best choice",
    "risks": ["risk1", "risk2"],
    "expected_duration": "5m" | "1h" | "4h" | "1d",
    "pairs_analyzed": {len(pairs_data)},
    "runner_up": "second best pair or null"
}}

CRITICAL:
- If NO pair has a good setup, choose "WAIT"
- Better to miss a trade than lose money
- Only choose LONG/SHORT if confidence >= 70%
- Consider SHORT for BEARISH trends (downtrending pairs can be profitable!)"""

    @staticmethod
    def _build_market_prompt(
        pair: str, market_data: dict[str, Any], knowledge: dict[str, Any]
    ) -> str:
        """Build market analysis prompt (legacy single-pair)."""
        pair_perf = knowledge.get("pair_performance", {}).get(pair, {})
        setups = knowledge.get("successful_setups", [])[-5:]
        mistakes = knowledge.get("mistakes_to_avoid", [])[-5:]

        return f"""You are an expert cryptocurrency trader. Analyze the market.

## CURRENT SITUATION
Pair: {pair}
Price: {market_data.get('price')}
24h Change: {market_data.get('change_24h')}%
24h Volume: ${market_data.get('volume_24h', 0):,.0f}
Trend: {market_data.get('trend')}

Indicators:
- RSI(14): {market_data.get('rsi')}
- EMA50: {market_data.get('ema50')}
- EMA200: {market_data.get('ema200')}
- ATR: {market_data.get('atr')}

## PAIR HISTORY
{json.dumps(pair_perf, indent=2)}

## RECENT SUCCESSFUL TRADES
{json.dumps(setups, indent=2)}

## MISTAKES TO AVOID
{json.dumps(mistakes, indent=2)}

## RISK MANAGEMENT RULES (MANDATORY)
1. Risk/Reward ratio MUST be >= 1.5:1 (take_profit distance / stop_loss distance)
2. Stop loss distance: minimum 3% from entry for volatile coins, 2% for stable
3. Leverage: max 2x for meme/volatile coins, max 3x for major coins (BTC, ETH, BNB)
4. Position size: 2-4% of capital
5. LONG: price > EMA50 > EMA200, trend=BULLISH
6. SHORT: price < EMA50 < EMA200, trend=BEARISH (profit when price DROPS)

## TASK
Respond STRICTLY in JSON:
{{
    "decision": "LONG" | "SHORT" | "WAIT",
    "confidence": 0-100,
    "strategy": "strategy name",
    "entry_price": number or null,
    "stop_loss": number or null (min 3% from entry),
    "take_profit": number or null (must give R/R >= 1.5),
    "leverage": 1-3 (2 for volatile, 3 for majors),
    "position_size_pct": 2-4,
    "reasoning": "detailed reasoning",
    "risks": ["risk1", "risk2"],
    "expected_duration": "5m" | "1h" | "4h" | "1d"
}}

CRITICAL: If R/R < 1.5 - choose WAIT. Better to miss a trade than lose money."""

    @staticmethod
    def _build_trade_analysis_prompt(trade: dict[str, Any]) -> str:
        """Build trade analysis prompt."""
        return f"""Analyze the completed trade and extract lessons.

## TRADE
{json.dumps(trade, indent=2)}

## TASK
Respond STRICTLY in JSON:
{{
    "grade": "A" | "B" | "C" | "D" | "F",
    "what_went_right": ["point1", "point2"],
    "what_went_wrong": ["point1", "point2"],
    "lesson_learned": "main lesson",
    "improvement_suggestion": "how to improve",
    "add_to_mistakes_to_avoid": "if there was an error, what to add"
}}"""


def get_api_usage() -> dict[str, Any]:
    """Get current API usage statistics."""
    return api_usage.get_stats()


def get_top_consumers(limit: int = 10) -> list[dict[str, Any]]:
    """Get top API consumers by cost (all time)."""
    return api_usage.get_top_consumers(limit)


def get_today_top_consumers(limit: int = 10) -> list[dict[str, Any]]:
    """Get today's top API consumers by cost."""
    return api_usage.get_today_top_consumers(limit)


def set_api_usage_limits(daily: float = None, monthly: float = None) -> None:
    """Set API usage warning limits."""
    api_usage.set_warning_limits(daily, monthly)


def get_api_warnings() -> list[str]:
    """Get recent API usage warnings."""
    return api_usage.get_recent_warnings()


def save_api_usage() -> None:
    """Force save API usage data."""
    api_usage.force_save()


def reset_api_usage(initial_balance: float = 50.0) -> dict[str, Any]:
    """Reset API usage statistics with new initial balance."""
    return api_usage.reset(initial_balance)


def get_budget_settings() -> dict[str, Any]:
    """Get current budget settings."""
    return api_usage.get_budget_settings()


def update_budget_settings(
    use_budget_limit: bool = None,
    daily_limit: float = None,
    total_limit: float = None,
) -> dict[str, Any]:
    """Update budget limit settings."""
    return api_usage.update_settings(
        use_budget_limit=use_budget_limit,
        daily_limit=daily_limit,
        total_limit=total_limit,
    )


def set_api_usage_orchestrator(orchestrator: Any) -> None:
    """Set orchestrator reference for auto-stop functionality."""
    api_usage.set_orchestrator(orchestrator)
