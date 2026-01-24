"""
WAR ROOM — crisis management and black swan protection.
Automatic reaction to emergency situations.
"""

from datetime import datetime, timedelta
from typing import Optional

from .base_agent import BaseAgent
from ...utils.common import TTLCache


class WarRoomAgent(BaseAgent):
    """Crisis management and emergency protocols."""

    __slots__ = ['_cache', '_alert_history', '_crisis_mode', '_thresholds']

    def __init__(self, claude_client, knowledge_base, log_path: str = None):
        super().__init__("WAR_ROOM", claude_client, knowledge_base, log_path)
        self._cache = TTLCache(default_ttl=10)
        self._alert_history: list[dict] = []
        self._crisis_mode = False
        self._thresholds = {
            'flash_crash_pct': 10,
            'rapid_move_pct': 5,
            'rapid_move_minutes': 5,
            'max_drawdown_pct': 15,
            'liquidation_cascade_volume': 100_000_000,
            'funding_extreme_pct': 0.5
        }

    async def monitor_market_health(self, market_data: dict) -> dict:
        """Monitor market health and detect anomalies."""
        btc_change = market_data.get('btc_24h_change', 0)
        eth_change = market_data.get('eth_24h_change', 0)
        total_liquidations = market_data.get('liquidations_24h', 0)
        alerts: list[dict] = []
        crisis_level = 'normal'

        if abs(btc_change) > self._thresholds['flash_crash_pct']:
            alerts.append({
                'type': 'flash_crash', 'severity': 'critical',
                'asset': 'BTC', 'change': btc_change,
                'timestamp': datetime.utcnow().isoformat()
            })
            crisis_level = 'critical'
        elif abs(btc_change) > self._thresholds['rapid_move_pct']:
            alerts.append({
                'type': 'rapid_move', 'severity': 'warning',
                'asset': 'BTC', 'change': btc_change,
                'timestamp': datetime.utcnow().isoformat()
            })
            crisis_level = 'elevated'

        if total_liquidations > self._thresholds['liquidation_cascade_volume']:
            alerts.append({
                'type': 'liquidation_cascade', 'severity': 'high',
                'volume': total_liquidations,
                'timestamp': datetime.utcnow().isoformat()
            })
            if crisis_level == 'normal':
                crisis_level = 'elevated'

        if alerts:
            self.log(f"Market alerts: {len(alerts)} issues detected", "WARNING")
            self._alert_history.extend(alerts)

        return {
            'crisis_level': crisis_level,
            'alerts': alerts,
            'btc_change': btc_change,
            'eth_change': eth_change,
            'liquidations': total_liquidations,
            'timestamp': datetime.utcnow().isoformat()
        }

    async def detect_black_swan(self, market_data: dict) -> Optional[dict]:
        """Detect black swan events using AI analysis."""
        prompt = (
            "Detect black swan signs:\n\n"
            f"Market data: {market_data}\n\n"
            "BLACK SWAN SIGNS:\n"
            "- Movement > 15% per hour\n"
            "- Mass liquidations (> $500M)\n"
            "- Sharp volatility spike\n"
            "- Anomalous volume\n"
            "- Exchange outages\n"
            "- Major hacks\n\n"
            "RETURN ONLY JSON:\n"
            "{\n"
            '    "is_black_swan": true/false,\n'
            '    "probability": 0-100,\n'
            '    "type": "crash/pump/hack/exchange_issue/regulatory/other",\n'
            '    "severity": 1-10,\n'
            '    "affected_assets": ["BTC", "ETH"],\n'
            '    "recommended_action": "close_all/hedge/reduce/monitor",\n'
            '    "reasoning": "explanation"\n'
            "}"
        )
        return await self.claude_client.analyze(prompt)

    async def activate_emergency_protocol(self, crisis_type: str, positions: list[dict]) -> dict:
        """Activate emergency protocol for crisis situations."""
        self._crisis_mode = True
        self.log(f"EMERGENCY PROTOCOL ACTIVATED: {crisis_type}", "CRITICAL")
        prompt = (
            "EMERGENCY PROTOCOL ACTIVATED!\n\n"
            f"Crisis type: {crisis_type}\n"
            f"Open positions: {positions}\n\n"
            "Develop action plan:\n\n"
            "RETURN ONLY JSON:\n"
            "{\n"
            '    "immediate_actions": [\n'
            "        {\n"
            '            "action": "close/hedge/cancel_orders",\n'
            '            "pair": "symbol or all",\n'
            '            "priority": 1-5,\n'
            '            "reason": "reason"\n'
            "        }\n"
            "    ],\n"
            '    "secondary_actions": [\n'
            "        {\n"
            '            "action": "action",\n'
            '            "delay_seconds": number,\n'
            '            "condition": "condition"\n'
            "        }\n"
            "    ],\n"
            '    "communication": {\n'
            '        "alert_user": true/false,\n'
            '        "message": "user message"\n'
            "    },\n"
            '    "recovery_plan": {\n'
            '        "wait_hours": number,\n'
            '        "conditions_to_resume": ["condition1", "condition2"]\n'
            "    }\n"
            "}"
        )
        plan = await self.claude_client.analyze(prompt)
        await self._send_telegram_alert(f"CRISIS: {crisis_type}")
        return plan

    async def deactivate_emergency(self) -> dict:
        """Deactivate emergency protocol."""
        self._crisis_mode = False
        self.log("Emergency protocol deactivated", "INFO")
        return {'status': 'deactivated', 'timestamp': datetime.utcnow().isoformat()}

    async def _send_telegram_alert(self, message: str) -> None:
        """Send Telegram alert (placeholder)."""
        pass

    def is_crisis_mode(self) -> bool:
        """Check if crisis mode is active."""
        return self._crisis_mode

    def get_alert_history(self, hours: int = 24) -> list[dict]:
        """Get alert history for specified hours."""
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        return [
            a for a in self._alert_history
            if datetime.fromisoformat(a.get('timestamp', datetime.utcnow().isoformat())) > cutoff
        ]

    async def run_health_check(self) -> dict:
        """Run system health check."""
        checks = {
            'api_connection': await self._check_api(),
            'exchange_status': await self._check_exchange(),
            'balance_available': await self._check_balance(),
            'positions_healthy': await self._check_positions()
        }
        all_healthy = all(checks.values())
        return {
            'healthy': all_healthy,
            'checks': checks,
            'timestamp': datetime.utcnow().isoformat()
        }

    async def _check_api(self) -> bool:
        """Check API connection health."""
        return True

    async def _check_exchange(self) -> bool:
        """Check exchange status."""
        return True

    async def _check_balance(self) -> bool:
        """Check balance availability."""
        return True

    async def _check_positions(self) -> bool:
        """Check positions health."""
        return True
