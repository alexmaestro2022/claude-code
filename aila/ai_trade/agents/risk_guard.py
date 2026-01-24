import json
from datetime import datetime
from .base_agent import BaseAgent
from ..config import RISK_LIMITS


class RiskGuardAgent(BaseAgent):
    """
    Risk guard agent - enforces ALL risk limits.
    Has VETO power over any trade.
    Monitors positions 24/7 independently.
    """

    def __init__(self, claude_client, knowledge_base, risk_manager):
        super().__init__(
            name="RISK_GUARD",
            claude_client=claude_client,
            knowledge_base=knowledge_base,
            log_path="/opt/aila/logs/ai_trade/risk_guard.log"
        )
        self.risk_manager = risk_manager
        self.vetoed_trades = []

    async def think(self, context: dict) -> dict:
        """Check trade against risk limits."""
        opportunity = context.get("opportunity")
        balance = context.get("balance", 0)
        if not opportunity:
            return {"approved": False, "reason": "No opportunity provided"}
        return await self.check(opportunity, balance)

    async def check(self, opportunity: dict, balance: float) -> dict:
        """
        Final risk check before execution.
        Returns: {approved: bool, reason: str, adjustments: dict}
        """
        self.log(f"Risk check: {opportunity['decision']} {opportunity['pair']}")

        issues = []
        adjustments = {}

        # 1. Hard limit checks (non-negotiable)
        leverage = opportunity.get("leverage", 1)
        if leverage > RISK_LIMITS["max_leverage"]:
            adjustments["leverage"] = RISK_LIMITS["max_leverage"]
            issues.append(f"Leverage {leverage}x exceeds max {RISK_LIMITS['max_leverage']}x, capped")

        position_pct = opportunity.get("position_size_pct", 2)
        if position_pct > RISK_LIMITS["max_position_size_pct"]:
            adjustments["position_size_pct"] = RISK_LIMITS["max_position_size_pct"]
            issues.append(f"Position size {position_pct}% exceeds max {RISK_LIMITS['max_position_size_pct']}%")

        # 2. Balance checks
        if balance < RISK_LIMITS["min_balance_usdt"]:
            self.log(f"VETO: Balance ${balance} below minimum", "warning")
            self._record_veto(opportunity, "Insufficient balance")
            return {"approved": False, "reason": f"Balance ${balance:.2f} below min ${RISK_LIMITS['min_balance_usdt']}"}

        # 3. Daily loss check
        risk_status = self.risk_manager.get_status()
        if not risk_status.get("can_trade", True):
            self.log("VETO: Trading suspended by risk manager", "warning")
            self._record_veto(opportunity, "Trading suspended")
            return {"approved": False, "reason": "Trading suspended - daily loss limit reached"}

        # 4. Max positions check
        open_positions = risk_status.get("open_positions", 0)
        if open_positions >= RISK_LIMITS["max_open_positions"]:
            self.log(f"VETO: Max positions reached ({open_positions})", "warning")
            self._record_veto(opportunity, "Max positions reached")
            return {"approved": False, "reason": f"Max open positions ({RISK_LIMITS['max_open_positions']}) reached"}

        # 5. Stop loss validation
        stop_loss = opportunity.get("stop_loss")
        entry_price = opportunity.get("entry_price") or opportunity.get("market_data", {}).get("price", 0)

        if not stop_loss:
            issues.append("No stop loss set — high risk")
            # Calculate a safe SL based on ATR
            atr = opportunity.get("market_data", {}).get("atr", 0)
            if atr and entry_price:
                if opportunity["decision"] == "LONG":
                    adjustments["stop_loss"] = entry_price - (atr * 2)
                else:
                    adjustments["stop_loss"] = entry_price + (atr * 2)
                issues.append(f"Auto-set SL at 2x ATR: {adjustments['stop_loss']}")

        elif entry_price and stop_loss:
            # Check SL isn't too far (max 10% for leveraged)
            sl_distance_pct = abs(entry_price - stop_loss) / entry_price * 100
            effective_risk = sl_distance_pct * leverage
            if effective_risk > 50:
                self.log(f"VETO: Effective risk too high ({effective_risk:.1f}%)", "warning")
                self._record_veto(opportunity, f"Effective risk {effective_risk:.1f}% too high")
                return {"approved": False, "reason": f"Effective risk {effective_risk:.1f}% too high (max 50%)"}

        # 6. Risk/Reward check
        take_profit = opportunity.get("take_profit")
        if stop_loss and take_profit and entry_price:
            risk = abs(entry_price - stop_loss)
            reward = abs(take_profit - entry_price)
            rr_ratio = reward / risk if risk > 0 else 0

            if rr_ratio < 1.0:
                issues.append(f"Poor R:R ratio ({rr_ratio:.2f}:1)")
                if rr_ratio < 0.5:
                    self.log(f"VETO: R:R ratio {rr_ratio:.2f} unacceptable", "warning")
                    self._record_veto(opportunity, f"R:R {rr_ratio:.2f} < 0.5")
                    return {"approved": False, "reason": f"Risk/Reward {rr_ratio:.2f}:1 unacceptable (min 0.5:1)"}

        # 7. Validate via risk_manager
        signal_copy = opportunity.copy()
        rm_check = self.risk_manager.validate_trade(signal_copy, balance)
        if not rm_check["approved"]:
            self.log(f"VETO: Risk manager rejected: {rm_check['reason']}", "warning")
            self._record_veto(opportunity, rm_check["reason"])
            return {"approved": False, "reason": rm_check["reason"]}

        # Apply adjustments
        if adjustments:
            for key, val in adjustments.items():
                opportunity[key] = val

        # Approved
        if issues:
            self.log(f"APPROVED with warnings: {', '.join(issues)}")
        else:
            self.log("APPROVED — all checks passed")

        return {
            "approved": True,
            "issues": issues,
            "adjustments": adjustments,
            "signal": opportunity,
        }

    async def monitor_positions(self, positions: list, balance: float) -> list:
        """
        Monitor open positions for risk violations.
        Returns list of positions that should be force-closed.
        """
        force_close = []

        for pos in positions:
            unrealized_pnl_pct = pos.get("unrealized_pnl_pct", 0)

            # Emergency close at -50% of position value
            if unrealized_pnl_pct <= -50:
                self.log(f"FORCE CLOSE {pos['symbol']}: PnL={unrealized_pnl_pct:.1f}%", "warning")
                force_close.append({
                    "symbol": pos["symbol"],
                    "reason": f"Emergency: PnL {unrealized_pnl_pct:.1f}%",
                    "urgency": "critical",
                })

            # Warning at -30%
            elif unrealized_pnl_pct <= -30:
                self.log(f"WARNING {pos['symbol']}: PnL={unrealized_pnl_pct:.1f}% approaching limit", "warning")

        # Check total exposure
        total_unrealized = sum(
            abs(p.get("unrealized_pnl_pct", 0)) for p in positions
            if p.get("unrealized_pnl_pct", 0) < 0
        )

        if total_unrealized > RISK_LIMITS["max_drawdown_pct"]:
            self.log(f"TOTAL DRAWDOWN WARNING: {total_unrealized:.1f}%", "warning")
            # Close the worst position
            worst = min(positions, key=lambda p: p.get("unrealized_pnl_pct", 0))
            if worst.get("unrealized_pnl_pct", 0) < -10:
                force_close.append({
                    "symbol": worst["symbol"],
                    "reason": f"Total drawdown {total_unrealized:.1f}% exceeds limit",
                    "urgency": "high",
                })

        return force_close

    def _record_veto(self, opportunity: dict, reason: str):
        """Record vetoed trade for analysis."""
        self.vetoed_trades.append({
            "pair": opportunity.get("pair"),
            "direction": opportunity.get("decision"),
            "reason": reason,
            "timestamp": datetime.now().isoformat(),
        })
        # Keep last 50
        self.vetoed_trades = self.vetoed_trades[-50:]
