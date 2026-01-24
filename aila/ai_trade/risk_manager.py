import logging
from datetime import datetime, date
from .config import RISK_LIMITS

logger = logging.getLogger("ai_trade")


class RiskManager:
    """Enforces hard risk limits that AI cannot override."""

    def __init__(self):
        self.limits = RISK_LIMITS
        self.daily_pnl = 0.0
        self.daily_trades = 0
        self.current_date = date.today()
        self.open_positions_count = 0
        self.peak_balance = 0.0

    def reset_daily_if_needed(self):
        """Reset daily counters if new day."""
        today = date.today()
        if today != self.current_date:
            logger.info(f"New day detected, resetting daily stats. Previous day PnL: {self.daily_pnl}")
            self.daily_pnl = 0.0
            self.daily_trades = 0
            self.current_date = today

    def validate_trade(self, signal: dict, balance: float) -> dict:
        """
        Validate trade signal against risk limits.
        Returns dict with 'approved' bool and 'reason' if rejected.
        """
        self.reset_daily_if_needed()

        # Update peak balance
        if balance > self.peak_balance:
            self.peak_balance = balance

        # Check minimum balance
        if balance < self.limits["min_balance_usdt"]:
            return {"approved": False, "reason": f"Balance ${balance} below minimum ${self.limits['min_balance_usdt']}"}

        # Check max open positions
        if self.open_positions_count >= self.limits["max_open_positions"]:
            return {"approved": False, "reason": f"Max open positions reached ({self.limits['max_open_positions']})"}

        # Check daily loss limit
        daily_loss_pct = abs(self.daily_pnl) / balance * 100 if balance > 0 and self.daily_pnl < 0 else 0
        if daily_loss_pct >= self.limits["max_daily_loss_pct"]:
            return {"approved": False, "reason": f"Daily loss limit reached ({daily_loss_pct:.1f}% >= {self.limits['max_daily_loss_pct']}%)"}

        # Check max drawdown
        if self.peak_balance > 0:
            drawdown_pct = (self.peak_balance - balance) / self.peak_balance * 100
            if drawdown_pct >= self.limits["max_drawdown_pct"]:
                return {"approved": False, "reason": f"Max drawdown reached ({drawdown_pct:.1f}% >= {self.limits['max_drawdown_pct']}%)"}

        # Validate leverage
        leverage = signal.get("leverage", 1)
        if leverage > self.limits["max_leverage"]:
            signal["leverage"] = self.limits["max_leverage"]
            logger.warning(f"Leverage capped from {leverage} to {self.limits['max_leverage']}")

        # Validate position size
        position_size_pct = signal.get("position_size_pct", self.limits["default_risk_per_trade_pct"])
        if position_size_pct > self.limits["max_position_size_pct"]:
            signal["position_size_pct"] = self.limits["max_position_size_pct"]
            logger.warning(f"Position size capped from {position_size_pct}% to {self.limits['max_position_size_pct']}%")

        # Calculate actual position size in USDT
        position_size_usdt = balance * (signal.get("position_size_pct", 2) / 100)
        signal["position_size_usdt"] = position_size_usdt

        return {"approved": True, "signal": signal}

    def record_trade_result(self, pnl: float):
        """Record trade result for daily tracking."""
        self.daily_pnl += pnl
        self.daily_trades += 1
        logger.info(f"Trade result recorded: PnL={pnl:.2f}, Daily PnL={self.daily_pnl:.2f}")

    def on_position_opened(self):
        """Track position opened."""
        self.open_positions_count += 1

    def on_position_closed(self):
        """Track position closed."""
        self.open_positions_count = max(0, self.open_positions_count - 1)

    def get_status(self) -> dict:
        """Get current risk status."""
        return {
            "daily_pnl": self.daily_pnl,
            "daily_trades": self.daily_trades,
            "open_positions": self.open_positions_count,
            "peak_balance": self.peak_balance,
            "daily_loss_limit_remaining": self.limits["max_daily_loss_pct"] - abs(self.daily_pnl / max(self.peak_balance, 1) * 100) if self.daily_pnl < 0 else self.limits["max_daily_loss_pct"],
            "can_trade": self.open_positions_count < self.limits["max_open_positions"],
        }
