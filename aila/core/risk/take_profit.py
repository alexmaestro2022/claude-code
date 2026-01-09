"""
AILA - Take-Profit Management Module

Take-profit calculation and multi-target management.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class TakeProfitConfig:
    """Configuration for take-profit management."""

    mode: str = "risk_ratio"  # risk_ratio | fixed_percent | multi_target

    # Risk ratio mode
    risk_reward_ratio: float = 2.0  # TP = SL distance * ratio

    # Fixed percent mode
    fixed_percent: float = 4.0

    # Multi-target mode
    multi_targets: list = field(
        default_factory=lambda: [
            {"percent": 50, "target": 1.5},  # Close 50% at 1.5R
            {"percent": 30, "target": 2.0},  # Close 30% at 2R
            {"percent": 20, "target": 3.0},  # Close 20% at 3R
        ]
    )

    # Partial close settings
    partial_close_enabled: bool = True
    min_partial_close_percent: float = 10.0  # Minimum 10% per close


@dataclass
class TakeProfitLevel:
    """Single take-profit level."""

    price: Decimal
    close_percent: float  # Percentage of position to close at this level
    risk_ratio: float  # R:R ratio
    is_hit: bool = False
    filled_percent: float = 0.0


@dataclass
class TakeProfitResult:
    """Result of take-profit calculation."""

    primary_target: Decimal
    targets: list[TakeProfitLevel]
    mode: str
    total_risk_ratio: float


class TakeProfitManager:
    """
    Take-profit management system.

    Supports multiple take-profit modes:
    - risk_ratio: Based on risk-reward ratio from stop-loss
    - fixed_percent: Fixed percentage from entry
    - multi_target: Multiple targets with partial closes

    Example:
        config = TakeProfitConfig(
            mode="multi_target",
            multi_targets=[
                {"percent": 50, "target": 1.5},
                {"percent": 30, "target": 2.0},
                {"percent": 20, "target": 3.0},
            ],
        )
        manager = TakeProfitManager(config)

        result = manager.calculate_take_profit(
            side="long",
            entry_price=Decimal("50000"),
            stop_loss_price=Decimal("49000"),
        )

        for target in result.targets:
            print(f"Close {target.close_percent}% at {target.price} ({target.risk_ratio}R)")
    """

    def __init__(self, config: Optional[TakeProfitConfig] = None):
        """
        Initialize take-profit manager.

        Args:
            config: Take-profit configuration
        """
        self.config = config or TakeProfitConfig()

    def calculate_take_profit(
        self,
        side: str,
        entry_price: Decimal,
        stop_loss_price: Decimal,
    ) -> TakeProfitResult:
        """
        Calculate take-profit levels.

        Args:
            side: Position side ('long' or 'short')
            entry_price: Entry price
            stop_loss_price: Stop-loss price

        Returns:
            TakeProfitResult with calculated targets
        """
        # Calculate risk (distance to stop)
        risk = abs(entry_price - stop_loss_price)

        if self.config.mode == "risk_ratio":
            targets = self._calculate_ratio_targets(
                side,
                entry_price,
                risk,
            )
        elif self.config.mode == "fixed_percent":
            targets = self._calculate_fixed_targets(
                side,
                entry_price,
                risk,
            )
        elif self.config.mode == "multi_target":
            targets = self._calculate_multi_targets(
                side,
                entry_price,
                risk,
            )
        else:
            targets = self._calculate_ratio_targets(
                side,
                entry_price,
                risk,
            )

        primary_target = targets[0].price if targets else entry_price
        total_rr = sum(t.risk_ratio * (t.close_percent / 100) for t in targets)

        return TakeProfitResult(
            primary_target=primary_target,
            targets=targets,
            mode=self.config.mode,
            total_risk_ratio=total_rr,
        )

    def _calculate_ratio_targets(
        self,
        side: str,
        entry_price: Decimal,
        risk: Decimal,
    ) -> list[TakeProfitLevel]:
        """Calculate single target based on risk ratio."""
        reward = risk * Decimal(str(self.config.risk_reward_ratio))

        if side == "long":
            tp_price = entry_price + reward
        else:
            tp_price = entry_price - reward

        return [
            TakeProfitLevel(
                price=tp_price,
                close_percent=100.0,
                risk_ratio=self.config.risk_reward_ratio,
            )
        ]

    def _calculate_fixed_targets(
        self,
        side: str,
        entry_price: Decimal,
        risk: Decimal,
    ) -> list[TakeProfitLevel]:
        """Calculate single target based on fixed percentage."""
        distance = entry_price * Decimal(str(self.config.fixed_percent / 100))

        if side == "long":
            tp_price = entry_price + distance
        else:
            tp_price = entry_price - distance

        # Calculate equivalent risk ratio
        rr = float(distance / risk) if risk > 0 else 0

        return [
            TakeProfitLevel(
                price=tp_price,
                close_percent=100.0,
                risk_ratio=rr,
            )
        ]

    def _calculate_multi_targets(
        self,
        side: str,
        entry_price: Decimal,
        risk: Decimal,
    ) -> list[TakeProfitLevel]:
        """Calculate multiple targets for partial closes."""
        targets = []
        total_percent = 0.0

        for target_config in self.config.multi_targets:
            rr = target_config.get("target", 1.5)
            close_pct = target_config.get("percent", 33.33)

            # Validate percent
            if close_pct < self.config.min_partial_close_percent:
                continue

            reward = risk * Decimal(str(rr))

            if side == "long":
                tp_price = entry_price + reward
            else:
                tp_price = entry_price - reward

            targets.append(
                TakeProfitLevel(
                    price=tp_price,
                    close_percent=close_pct,
                    risk_ratio=rr,
                )
            )
            total_percent += close_pct

        # Normalize if total doesn't equal 100
        if total_percent != 100.0 and total_percent > 0:
            for target in targets:
                target.close_percent = (target.close_percent / total_percent) * 100

        return targets

    def get_next_target(
        self,
        targets: list[TakeProfitLevel],
    ) -> Optional[TakeProfitLevel]:
        """
        Get the next unhit target.

        Args:
            targets: List of take-profit levels

        Returns:
            Next unhit target or None if all hit
        """
        for target in targets:
            if not target.is_hit:
                return target
        return None

    def update_target_status(
        self,
        targets: list[TakeProfitLevel],
        current_price: Decimal,
        side: str,
    ) -> tuple[list[TakeProfitLevel], list[TakeProfitLevel]]:
        """
        Update target hit status based on current price.

        Args:
            targets: List of take-profit levels
            current_price: Current market price
            side: Position side ('long' or 'short')

        Returns:
            Tuple of (updated_targets, newly_hit_targets)
        """
        newly_hit = []

        for target in targets:
            if target.is_hit:
                continue

            is_hit = False
            if side == "long" and current_price >= target.price:
                is_hit = True
            elif side == "short" and current_price <= target.price:
                is_hit = True

            if is_hit:
                target.is_hit = True
                target.filled_percent = target.close_percent
                newly_hit.append(target)
                logger.info(
                    "Take-profit target hit",
                    price=str(target.price),
                    close_percent=target.close_percent,
                    risk_ratio=target.risk_ratio,
                )

        return targets, newly_hit

    def should_close_partial(
        self,
        targets: list[TakeProfitLevel],
        current_price: Decimal,
        side: str,
    ) -> tuple[bool, float, Decimal]:
        """
        Check if partial position should be closed.

        Args:
            targets: List of take-profit levels
            current_price: Current market price
            side: Position side ('long' or 'short')

        Returns:
            Tuple of (should_close, close_percent, target_price)
        """
        if not self.config.partial_close_enabled:
            return False, 0.0, Decimal("0")

        for target in targets:
            if target.is_hit:
                continue

            is_hit = False
            if side == "long" and current_price >= target.price:
                is_hit = True
            elif side == "short" and current_price <= target.price:
                is_hit = True

            if is_hit:
                return True, target.close_percent, target.price

        return False, 0.0, Decimal("0")

    def calculate_remaining_position(
        self,
        targets: list[TakeProfitLevel],
    ) -> float:
        """
        Calculate remaining position percentage after hits.

        Args:
            targets: List of take-profit levels

        Returns:
            Remaining position percentage (0-100)
        """
        closed = sum(t.close_percent for t in targets if t.is_hit)
        return max(0.0, 100.0 - closed)

    def calculate_realized_rr(
        self,
        targets: list[TakeProfitLevel],
    ) -> float:
        """
        Calculate realized risk-reward from hit targets.

        Args:
            targets: List of take-profit levels

        Returns:
            Weighted average risk-reward of hit targets
        """
        total_rr = 0.0
        total_pct = 0.0

        for target in targets:
            if target.is_hit:
                total_rr += target.risk_ratio * target.close_percent
                total_pct += target.close_percent

        if total_pct == 0:
            return 0.0

        return total_rr / total_pct

    def update_config(self, **kwargs) -> None:
        """Update configuration parameters."""
        for key, value in kwargs.items():
            if hasattr(self.config, key):
                setattr(self.config, key, value)

    def __repr__(self) -> str:
        return (
            f"TakeProfitManager(mode='{self.config.mode}', "
            f"partial_close={self.config.partial_close_enabled})"
        )
