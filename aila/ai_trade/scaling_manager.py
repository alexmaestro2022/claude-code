"""
SCALING MANAGER - manages trading scaling.
Automatic position size increase as capital and AI level grows.
"""

import logging
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger("ai_trade.scaling")


@dataclass
class ScalingPhase:
    """Represents a scaling phase with limits and targets."""
    name: str
    min_capital: float
    max_capital: float
    min_level: int
    max_leverage: int
    max_risk_pct: float
    max_positions: int
    target_monthly_pct: float


class ScalingManager:
    """Manages trading scaling based on capital and AI level."""

    __slots__ = ['_knowledge_base', '_capital_manager', '_phases', '_current_phase']

    def __init__(self, knowledge_base: Any, capital_manager: Any) -> None:
        self._knowledge_base = knowledge_base
        self._capital_manager = capital_manager
        self._current_phase: Optional[ScalingPhase] = None

        self._phases = [
            ScalingPhase("Starter", 0, 1000, 1, 10, 2.0, 2, 50),
            ScalingPhase("Growth", 1000, 5000, 5, 10, 1.5, 3, 40),
            ScalingPhase("Established", 5000, 20000, 10, 7, 1.0, 4, 30),
            ScalingPhase("Professional", 20000, 100000, 20, 5, 0.5, 5, 20),
            ScalingPhase("Institutional", 100000, float('inf'), 50, 3, 0.25, 6, 10)
        ]

    async def get_current_phase(self) -> ScalingPhase:
        """Determine current scaling phase based on capital and level."""
        try:
            allocation = self._capital_manager.get_allocation()
            capital = allocation.get('total', 0)
        except Exception:
            capital = 0

        try:
            profile = await self._knowledge_base.get_trader_profile()
            level = profile.get('level', 1)
        except Exception:
            level = 1

        for phase in self._phases:
            if phase.min_capital <= capital < phase.max_capital and level >= phase.min_level:
                self._current_phase = phase
                return phase

        self._current_phase = self._phases[0]
        return self._current_phase

    async def get_recommended_settings(self) -> dict[str, Any]:
        """Get recommended settings for current phase."""
        phase = await self.get_current_phase()

        return {
            'phase': phase.name,
            'max_leverage': phase.max_leverage,
            'max_risk_pct': phase.max_risk_pct,
            'max_positions': phase.max_positions,
            'target_monthly_pct': phase.target_monthly_pct,
            'capital_range': f"${phase.min_capital:,.0f} - ${phase.max_capital:,.0f}" if phase.max_capital != float('inf') else f"${phase.min_capital:,.0f}+"
        }

    async def check_upgrade_eligibility(self) -> dict[str, Any]:
        """Check if ready to upgrade to next phase."""
        current = await self.get_current_phase()
        current_idx = self._phases.index(current)

        if current_idx >= len(self._phases) - 1:
            return {'eligible': False, 'reason': 'Already at max phase'}

        next_phase = self._phases[current_idx + 1]

        try:
            allocation = self._capital_manager.get_allocation()
            capital = allocation.get('total', 0)
        except Exception:
            capital = 0

        try:
            profile = await self._knowledge_base.get_trader_profile()
            level = profile.get('level', 1)
        except Exception:
            level = 1

        requirements = {
            'capital': {
                'required': next_phase.min_capital,
                'current': capital,
                'met': capital >= next_phase.min_capital
            },
            'level': {
                'required': next_phase.min_level,
                'current': level,
                'met': level >= next_phase.min_level
            }
        }

        eligible = all(r['met'] for r in requirements.values())

        return {
            'eligible': eligible,
            'current_phase': current.name,
            'next_phase': next_phase.name,
            'requirements': requirements
        }

    async def calculate_growth_projection(self, months: int = 12) -> dict[str, Any]:
        """Calculate capital growth projection."""
        try:
            allocation = self._capital_manager.get_allocation()
            capital = allocation.get('total', 0)
        except Exception:
            capital = 100  # Default starting capital

        phase = await self.get_current_phase()

        projections: list[dict[str, Any]] = []
        current_capital = capital if capital > 0 else 100
        current_phase = phase

        for month in range(1, months + 1):
            monthly_return = current_phase.target_monthly_pct / 100
            current_capital *= (1 + monthly_return)

            # Check if phase upgrade needed
            for p in self._phases:
                if p.min_capital <= current_capital < p.max_capital:
                    current_phase = p
                    break

            projections.append({
                'month': month,
                'capital': round(current_capital, 2),
                'phase': current_phase.name
            })

        initial = capital if capital > 0 else 100
        final = projections[-1]['capital'] if projections else initial

        return {
            'initial_capital': initial,
            'final_capital': final,
            'total_growth_pct': round(((final / initial) - 1) * 100, 2) if initial > 0 else 0,
            'projections': projections
        }

    def get_phase_info(self, phase_name: str) -> Optional[dict[str, Any]]:
        """Get info about a specific phase."""
        for phase in self._phases:
            if phase.name.lower() == phase_name.lower():
                return {
                    'name': phase.name,
                    'min_capital': phase.min_capital,
                    'max_capital': phase.max_capital if phase.max_capital != float('inf') else None,
                    'min_level': phase.min_level,
                    'max_leverage': phase.max_leverage,
                    'max_risk_pct': phase.max_risk_pct,
                    'max_positions': phase.max_positions,
                    'target_monthly_pct': phase.target_monthly_pct
                }
        return None

    def get_all_phases(self) -> list[dict[str, Any]]:
        """Get info about all phases."""
        return [
            {
                'name': p.name,
                'min_capital': p.min_capital,
                'max_capital': p.max_capital if p.max_capital != float('inf') else None,
                'min_level': p.min_level,
                'max_leverage': p.max_leverage,
                'max_risk_pct': p.max_risk_pct,
                'max_positions': p.max_positions,
                'target_monthly_pct': p.target_monthly_pct
            }
            for p in self._phases
        ]
