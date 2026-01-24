"""Risk manager - enforces hard risk limits."""

import logging
from datetime import date
from typing import Any

from .config import RISK_LIMITS

logger = logging.getLogger("ai_trade")


class RiskManager:
    """Enforces hard risk limits that AI cannot override."""

    __slots__ = (
        "_limits", "daily_pnl", "daily_trades",
        "_current_date", "open_positions_count", "peak_balance",
    )

    def __init__(self) -> None:
        self._limits = RISK_LIMITS
        self.daily_pnl: float = 0.0
        self.daily_trades: int = 0
        self._current_date: date = date.today()
        self.open_positions_count: int = 0
        self.peak_balance: float = 0.0

    def reset_daily_if_needed(self) -> None:
        """Reset daily counters if new day."""
        today = date.today()
        if today != self._current_date:
            logger.info(f"New day, resetting stats. Previous PnL: {self.daily_pnl}")
            self.daily_pnl = 0.0
            self.daily_trades = 0
            self._current_date = today

    def validate_trade(self, signal: dict[str, Any], balance: float) -> dict[str, Any]:
        """Validate trade signal against risk limits."""
        self.reset_daily_if_needed()

        if balance > self.peak_balance:
            self.peak_balance = balance

        # Check minimum balance
        if balance < self._limits["min_balance_usdt"]:
            return {"approved": False, "reason": f"Balance ${balance} below min ${self._limits['min_balance_usdt']}"}

        # Check max open positions
        if self.open_positions_count >= self._limits["max_open_positions"]:
            return {"approved": False, "reason": f"Max positions ({self._limits['max_open_positions']}) reached"}

        # Check daily loss limit
        if balance > 0 and self.daily_pnl < 0:
            daily_loss_pct = abs(self.daily_pnl) / balance * 100
            if daily_loss_pct >= self._limits["max_daily_loss_pct"]:
                return {"approved": False, "reason": f"Daily loss {daily_loss_pct:.1f}% >= {self._limits['max_daily_loss_pct']}%"}

        # Check drawdown
        if self.peak_balance > 0:
            drawdown_pct = (self.peak_balance - balance) / self.peak_balance * 100
            if drawdown_pct >= self._limits["max_drawdown_pct"]:
                return {"approved": False, "reason": f"Drawdown {drawdown_pct:.1f}% >= {self._limits['max_drawdown_pct']}%"}

        # Cap leverage
        leverage = signal.get("leverage", 1)
        if leverage > self._limits["max_leverage"]:
            signal["leverage"] = self._limits["max_leverage"]

        # Cap position size
        position_pct = signal.get("position_size_pct", self._limits["default_risk_per_trade_pct"])
        if position_pct > self._limits["max_position_size_pct"]:
            signal["position_size_pct"] = self._limits["max_position_size_pct"]

        signal["position_size_usdt"] = balance * (signal.get("position_size_pct", 2) / 100)
        return {"approved": True, "signal": signal}

    def record_trade_result(self, pnl: float) -> None:
        """Record trade result for daily tracking."""
        self.daily_pnl += pnl
        self.daily_trades += 1

    def on_position_opened(self) -> None:
        """Track position opened."""
        self.open_positions_count += 1

    def on_position_closed(self) -> None:
        """Track position closed."""
        self.open_positions_count = max(0, self.open_positions_count - 1)

    def get_status(self) -> dict[str, Any]:
        """Get current risk status."""
        daily_loss_used = abs(self.daily_pnl / max(self.peak_balance, 1) * 100) if self.daily_pnl < 0 else 0
        return {
            "daily_pnl": self.daily_pnl,
            "daily_trades": self.daily_trades,
            "open_positions": self.open_positions_count,
            "peak_balance": self.peak_balance,
            "daily_loss_limit_remaining": self._limits["max_daily_loss_pct"] - daily_loss_used,
            "can_trade": self.open_positions_count < self._limits["max_open_positions"],
        }
