import json
import logging
import os
from datetime import datetime
from typing import Optional
from .base_agent import BaseAgent

AGENT_LOG_DIR = "/opt/aila/logs/ai_trade"


class LoggerAgent(BaseAgent):
    """
    Logger agent - records ALL agent actions.
    Provides formatted logs for web interface.
    Sends alerts via Telegram for critical events.
    """

    def __init__(self, claude_client, knowledge_base, telegram_bot=None):
        super().__init__(
            name="LOGGER",
            claude_client=claude_client,
            knowledge_base=knowledge_base,
            log_path=os.path.join(AGENT_LOG_DIR, "orchestrator.log")
        )
        self.telegram_bot = telegram_bot
        self.events = []  # In-memory event buffer for web UI
        self.max_events = 500

    async def think(self, context: dict) -> dict:
        """Process logging context."""
        return {"status": "ok", "events_count": len(self.events)}

    def log_event(self, agent: str, action: str, data: dict = None, message: str = ""):
        """Record a structured event from any agent."""
        event = {
            "timestamp": datetime.now().isoformat(),
            "agent": agent,
            "action": action,
            "data": data or {},
            "message": message,
        }

        self.events.append(event)
        # Keep buffer limited
        if len(self.events) > self.max_events:
            self.events = self.events[-self.max_events:]

        # Write to file
        self._write_to_file(event)

        # Log to standard logger
        self.log(f"[{agent}] {action}: {message}")

        return event

    def log_opportunity(self, opportunity: dict):
        """Log a found trading opportunity."""
        return self.log_event(
            agent="TRADER",
            action="OPPORTUNITY_FOUND",
            data={
                "pair": opportunity.get("pair"),
                "direction": opportunity.get("decision"),
                "confidence": opportunity.get("confidence"),
                "strategy": opportunity.get("strategy"),
            },
            message=f"Opportunity found: {opportunity.get('decision')} {opportunity.get('pair')}, "
                    f"confidence {opportunity.get('confidence')}%"
        )

    def log_review(self, opportunity: dict, review: dict):
        """Log review decision."""
        decision = review.get("decision", "UNKNOWN")
        return self.log_event(
            agent="REVIEWER",
            action=f"REVIEW_{decision}",
            data={
                "pair": opportunity.get("pair"),
                "decision": decision,
                "reason": review.get("reason", ""),
                "issues": review.get("issues_found", []),
            },
            message=f"Review {decision}: {opportunity.get('pair')} — {review.get('reason', '')}"
        )

    def log_risk_check(self, opportunity: dict, result: dict):
        """Log risk guard decision."""
        approved = result.get("approved", False)
        action = "RISK_APPROVED" if approved else "RISK_VETO"
        return self.log_event(
            agent="RISK_GUARD",
            action=action,
            data={
                "pair": opportunity.get("pair"),
                "approved": approved,
                "reason": result.get("reason", ""),
                "issues": result.get("issues", []),
            },
            message=f"Risk {'APPROVED' if approved else 'VETO'}: {opportunity.get('pair')} — "
                    f"{result.get('reason', 'OK')}"
        )

    async def log_trade_opened(self, opportunity: dict, result: dict):
        """Log trade execution."""
        event = self.log_event(
            agent="SYSTEM",
            action="TRADE_OPENED",
            data={
                "pair": opportunity.get("pair"),
                "direction": opportunity.get("decision"),
                "entry_price": result.get("entry_price"),
                "leverage": result.get("leverage"),
                "size_usdt": result.get("position_size_usdt"),
            },
            message=f"Trade opened: {opportunity.get('decision')} {opportunity.get('pair')} "
                    f"@ {result.get('entry_price')}, {result.get('leverage')}x"
        )

        # Send Telegram alert
        await self._send_telegram_alert(
            f"🟢 Trade Opened\n"
            f"{opportunity.get('decision')} {opportunity.get('pair')}\n"
            f"Entry: {result.get('entry_price')}\n"
            f"Leverage: {result.get('leverage')}x\n"
            f"Size: ${result.get('position_size_usdt', 0):.2f}"
        )

        return event

    async def log_trade_closed(self, trade_result: dict):
        """Log trade closure."""
        pnl = trade_result.get("pnl", 0)
        emoji = "🟢" if pnl > 0 else "🔴"

        event = self.log_event(
            agent="SYSTEM",
            action="TRADE_CLOSED",
            data={
                "pair": trade_result.get("symbol"),
                "direction": trade_result.get("direction"),
                "pnl": pnl,
                "pnl_pct": trade_result.get("pnl_pct", 0),
                "reason": trade_result.get("close_reason", ""),
            },
            message=f"Trade closed: {trade_result.get('symbol')} "
                    f"PnL=${pnl:.2f} ({trade_result.get('pnl_pct', 0):.2f}%)"
        )

        # Send Telegram alert
        await self._send_telegram_alert(
            f"{emoji} Trade Closed\n"
            f"{trade_result.get('direction')} {trade_result.get('symbol')}\n"
            f"PnL: ${pnl:.2f} ({trade_result.get('pnl_pct', 0):.2f}%)\n"
            f"Reason: {trade_result.get('close_reason', 'unknown')}"
        )

        return event

    def log_analysis(self, trade: dict, analysis: dict):
        """Log trade analysis result."""
        return self.log_event(
            agent="ANALYST",
            action="TRADE_ANALYZED",
            data={
                "pair": trade.get("symbol"),
                "grade": analysis.get("grade"),
                "lesson": analysis.get("lesson", ""),
            },
            message=f"Analysis: {trade.get('symbol')} Grade={analysis.get('grade')} — "
                    f"{analysis.get('lesson', '')}"
        )

    def log_force_close(self, symbol: str, reason: str):
        """Log forced position close by risk guard."""
        return self.log_event(
            agent="RISK_GUARD",
            action="FORCE_CLOSE",
            data={"pair": symbol, "reason": reason},
            message=f"FORCE CLOSE: {symbol} — {reason}"
        )

    def get_recent_events(self, limit: int = 50, agent: Optional[str] = None) -> list:
        """Get recent events for web interface."""
        events = self.events
        if agent:
            events = [e for e in events if e["agent"] == agent]
        return events[-limit:]

    def _write_to_file(self, event: dict):
        """Write event to log file."""
        try:
            log_file = os.path.join(AGENT_LOG_DIR, "orchestrator.log")
            with open(log_file, "a") as f:
                f.write(f"{event['timestamp']} [{event['agent']}] {event['action']}: {event['message']}\n")
        except IOError:
            pass

    async def _send_telegram_alert(self, message: str):
        """Send alert via Telegram if bot is configured."""
        if not self.telegram_bot:
            return
        try:
            await self.telegram_bot.send_alert(message)
        except Exception as e:
            self.log(f"Telegram alert failed: {e}", "error")
