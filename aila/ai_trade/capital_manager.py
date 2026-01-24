"""
CAPITAL MANAGER — professional capital management.
Kelly Criterion, compound growth, profit withdrawal.
"""

from typing import Optional
from dataclasses import dataclass

from ..utils.common import TTLCache


@dataclass(slots=True)
class CapitalAllocation:
    """Capital distribution."""

    trading: float
    reserve: float
    pending_withdrawal: float


class CapitalManager:
    """Manages capital and position sizing."""

    __slots__ = ['_knowledge_base', '_cache', '_config', '_total_capital', '_allocation']

    def __init__(self, knowledge_base, config: dict = None):
        self._knowledge_base = knowledge_base
        self._cache = TTLCache(default_ttl=60)
        self._config = config or self._default_config()
        self._total_capital = 0.0
        self._allocation = CapitalAllocation(0, 0, 0)

    def _default_config(self) -> dict:
        return {
            'reserve_pct': 20,
            'max_risk_per_trade_pct': 2,
            'withdrawal_pct': 30,
            'compound_pct': 50,
            'min_trade_size_usdt': 10,
            'kelly_fraction': 0.5
        }

    async def update_capital(self, total: float) -> None:
        """Update total capital and recalculate allocation."""
        self._total_capital = total
        await self._recalculate_allocation()

    async def _recalculate_allocation(self) -> None:
        """Recalculate capital allocation between trading, reserve, withdrawal."""
        reserve = self._total_capital * (self._config['reserve_pct'] / 100)
        trading = self._total_capital - reserve - self._allocation.pending_withdrawal
        self._allocation = CapitalAllocation(
            trading=max(0, trading),
            reserve=reserve,
            pending_withdrawal=self._allocation.pending_withdrawal
        )

    def kelly_criterion(self, win_rate: float, avg_win: float, avg_loss: float) -> float:
        """Calculate optimal position size using Kelly Criterion."""
        if avg_loss == 0 or win_rate <= 0 or win_rate >= 1:
            return 0.0
        p = win_rate
        q = 1 - p
        b = avg_win / avg_loss
        kelly = (p * b - q) / b
        kelly *= self._config['kelly_fraction']
        kelly = min(kelly, self._config['max_risk_per_trade_pct'] / 100)
        kelly = max(kelly, 0)
        return kelly

    def calculate_position_size(self, entry_price: float, stop_loss: float, risk_pct: Optional[float] = None) -> dict:
        """Calculate position size based on risk percentage and stop distance."""
        if risk_pct is None:
            risk_pct = self._config['max_risk_per_trade_pct']
        available = self._allocation.trading
        risk_amount = available * (risk_pct / 100)
        stop_distance_pct = abs(entry_price - stop_loss) / entry_price
        if stop_distance_pct == 0:
            return {'position_size_usdt': 0, 'quantity': 0, 'risk_amount': 0, 'risk_pct': 0}
        position_size = risk_amount / stop_distance_pct
        position_size = min(position_size, available)
        position_size = max(position_size, self._config['min_trade_size_usdt'])
        quantity = position_size / entry_price
        return {
            'position_size_usdt': round(position_size, 2),
            'quantity': quantity,
            'risk_amount': round(risk_amount, 2),
            'risk_pct': risk_pct
        }

    def calculate_compound_plan(self, initial: float, target: float, monthly_return_pct: float) -> dict:
        """Calculate compound growth plan from initial to target capital."""
        if monthly_return_pct <= 0 or initial >= target:
            return {'months_needed': 0, 'monthly_targets': [], 'weekly_targets': []}
        months = 0
        current = initial
        monthly_targets = [current]
        while current < target and months < 120:
            current *= (1 + monthly_return_pct / 100)
            monthly_targets.append(round(current, 2))
            months += 1
        weekly_return = (1 + monthly_return_pct / 100) ** (1 / 4) - 1
        weekly_targets: list[float] = []
        current = initial
        for _ in range(months * 4):
            current *= (1 + weekly_return)
            weekly_targets.append(round(current, 2))
            if current >= target:
                break
        return {
            'months_needed': months,
            'monthly_targets': monthly_targets,
            'weekly_targets': weekly_targets[:52]
        }

    async def process_profit(self, profit: float) -> dict:
        """Distribute profit between reinvestment, withdrawal, and reserve."""
        if profit <= 0:
            return {'reinvested': 0, 'to_withdraw': 0, 'to_reserve': 0}
        compound_pct = self._config['compound_pct']
        withdrawal_pct = self._config['withdrawal_pct']
        reserve_pct = 100 - compound_pct - withdrawal_pct
        reinvested = profit * (compound_pct / 100)
        to_withdraw = profit * (withdrawal_pct / 100)
        to_reserve = profit * (reserve_pct / 100)
        self._allocation.pending_withdrawal += to_withdraw
        return {
            'reinvested': round(reinvested, 2),
            'to_withdraw': round(to_withdraw, 2),
            'to_reserve': round(to_reserve, 2)
        }

    def get_allocation(self) -> dict:
        """Get current capital allocation."""
        return {
            'total': self._total_capital,
            'trading': self._allocation.trading,
            'reserve': self._allocation.reserve,
            'pending_withdrawal': self._allocation.pending_withdrawal,
            'available_for_trade': self._allocation.trading
        }

    def get_scaling_phase(self) -> dict:
        """Get current scaling phase based on capital size."""
        capital = self._total_capital
        phases = [
            {'name': 'Starter', 'min': 0, 'max': 1000, 'leverage': 10, 'risk': 2},
            {'name': 'Growth', 'min': 1000, 'max': 10000, 'leverage': 7, 'risk': 1.5},
            {'name': 'Established', 'min': 10000, 'max': 50000, 'leverage': 5, 'risk': 1},
            {'name': 'Professional', 'min': 50000, 'max': 200000, 'leverage': 3, 'risk': 0.5},
            {'name': 'Institutional', 'min': 200000, 'max': float('inf'), 'leverage': 2, 'risk': 0.25}
        ]
        for phase in phases:
            if phase['min'] <= capital < phase['max']:
                return {
                    'phase': phase['name'],
                    'recommended_leverage': phase['leverage'],
                    'recommended_risk_pct': phase['risk'],
                    'capital_range': f"${phase['min']:,} - ${phase['max']:,}"
                }
        return phases[-1]
