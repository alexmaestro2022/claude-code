"""
HEDGE MASTER — position protection and risk hedging.
"""

from typing import Optional

from .base_agent import BaseAgent
from ...utils.common import TTLCache


class HedgeMasterAgent(BaseAgent):
    """Manages hedging and position protection."""

    __slots__ = ['_cache', '_active_hedges']

    def __init__(self, claude_client, knowledge_base, log_path: str = None):
        super().__init__("HEDGE_MASTER", claude_client, knowledge_base, log_path)
        self._cache = TTLCache(default_ttl=30)
        self._active_hedges: list[dict] = []

    async def analyze_portfolio_risk(self, positions: list[dict]) -> dict:
        """Analyze portfolio risk and suggest hedging strategies."""
        if not positions:
            return {'risk_level': 'none', 'exposure': 0, 'recommendations': []}
        total_exposure = sum(abs(p.get('size', 0) * p.get('entry_price', 0)) for p in positions)
        long_exposure = sum(
            p.get('size', 0) * p.get('entry_price', 0)
            for p in positions if p.get('side', '').lower() == 'buy'
        )
        short_exposure = sum(
            abs(p.get('size', 0) * p.get('entry_price', 0))
            for p in positions if p.get('side', '').lower() == 'sell'
        )
        net_exposure = long_exposure - short_exposure
        if total_exposure == 0:
            risk_level = 'none'
        elif abs(net_exposure) / total_exposure > 0.8:
            risk_level = 'high'
        elif abs(net_exposure) / total_exposure > 0.5:
            risk_level = 'medium'
        else:
            risk_level = 'low'
        prompt = (
            "Analyze portfolio risk:\n\n"
            f"Positions: {positions}\n"
            f"Total exposure: ${total_exposure:.2f}\n"
            f"Long: ${long_exposure:.2f}\n"
            f"Short: ${short_exposure:.2f}\n"
            f"Net: ${net_exposure:.2f}\n\n"
            "RETURN ONLY JSON:\n"
            "{\n"
            '    "risk_score": 0-100,\n'
            '    "main_risks": ["risk1", "risk2"],\n'
            '    "correlation_risk": "low/medium/high",\n'
            '    "recommendations": ["rec1", "rec2"],\n'
            '    "hedge_suggestions": [\n'
            "        {\n"
            '            "type": "hedge type",\n'
            '            "pair": "symbol",\n'
            '            "direction": "long/short",\n'
            '            "size_pct": number\n'
            "        }\n"
            "    ]\n"
            "}"
        )
        analysis = await self.claude_client.analyze(prompt)
        return {
            'risk_level': risk_level,
            'risk_score': analysis.get('risk_score', 50),
            'total_exposure': total_exposure,
            'long_exposure': long_exposure,
            'short_exposure': short_exposure,
            'net_exposure': net_exposure,
            'main_risks': analysis.get('main_risks', []),
            'recommendations': analysis.get('recommendations', []),
            'hedge_suggestions': analysis.get('hedge_suggestions', [])
        }

    async def suggest_hedge(self, position: dict) -> Optional[dict]:
        """Suggest hedge for a specific position."""
        prompt = (
            "Suggest hedge for position:\n\n"
            f"Pair: {position.get('symbol')}\n"
            f"Direction: {position.get('side')}\n"
            f"Size: {position.get('size')}\n"
            f"Entry: {position.get('entry_price')}\n"
            f"Current price: {position.get('mark_price')}\n"
            f"PnL: {position.get('pnl')}\n\n"
            "RETURN ONLY JSON:\n"
            "{\n"
            '    "hedge_needed": true/false,\n'
            '    "urgency": "low/medium/high",\n'
            '    "hedge_type": "type",\n'
            '    "hedge_pair": "symbol",\n'
            '    "hedge_direction": "long/short",\n'
            '    "hedge_size_pct": 0-100,\n'
            '    "expected_protection_pct": number,\n'
            '    "cost_estimate_pct": number,\n'
            '    "reasoning": "explanation"\n'
            "}"
        )
        return await self.claude_client.analyze(prompt)

    async def create_market_neutral_position(self, pair1: str, pair2: str, total_size: float) -> dict:
        """Create a market-neutral position between two correlated pairs."""
        prompt = (
            "Create market-neutral position:\n\n"
            f"Pair 1: {pair1}\n"
            f"Pair 2: {pair2}\n"
            f"Total size: ${total_size}\n\n"
            "RETURN ONLY JSON:\n"
            "{\n"
            '    "correlation": number,\n'
            '    "pair1_direction": "long/short",\n'
            '    "pair1_size": number,\n'
            '    "pair2_direction": "long/short",\n'
            '    "pair2_size": number,\n'
            '    "entry_spread": number,\n'
            '    "target_spread": number,\n'
            '    "stop_spread": number,\n'
            '    "expected_profit_pct": number,\n'
            '    "max_risk_pct": number,\n'
            '    "reasoning": "explanation"\n'
            "}"
        )
        return await self.claude_client.analyze(prompt)

    async def emergency_hedge(self, positions: list[dict], threat: str) -> dict:
        """Emergency hedging when threat detected."""
        self.log(f"Emergency hedge triggered: {threat}", "WARNING")
        prompt = (
            "EMERGENCY HEDGING!\n\n"
            f"Threat: {threat}\n"
            f"Positions: {positions}\n\n"
            "RETURN ONLY JSON:\n"
            "{\n"
            '    "actions": [\n'
            "        {\n"
            '            "action": "close/hedge/reduce",\n'
            '            "pair": "symbol",\n'
            '            "size_pct": number,\n'
            '            "urgency": "immediate/asap/soon"\n'
            "        }\n"
            "    ],\n"
            '    "expected_loss_reduction_pct": number,\n'
            '    "reasoning": "explanation"\n'
            "}"
        )
        return await self.claude_client.analyze(prompt)

    def get_active_hedges(self) -> list[dict]:
        """Get list of active hedge positions."""
        return self._active_hedges

    async def monitor_hedges(self) -> list[dict]:
        """Monitor active hedges and return alerts."""
        alerts: list[dict] = []
        for hedge in self._active_hedges:
            pass
        return alerts
