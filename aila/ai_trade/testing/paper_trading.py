"""
PAPER TRADING — simulated trading without real money.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class PaperPosition:
    """Paper position."""

    id: str
    pair: str
    direction: str
    entry_price: float
    size: float
    leverage: int
    stop_loss: float
    take_profit: float
    opened_at: str
    pnl: float = 0.0
    status: str = "open"


@dataclass
class PaperTrade:
    """Paper trade (closed position)."""

    id: str
    pair: str
    direction: str
    entry_price: float
    exit_price: float
    size: float
    leverage: int
    pnl: float
    pnl_pct: float
    opened_at: str
    closed_at: str
    reason: str
    strategy: str


class PaperTrader:
    """Trading simulator without real money."""

    __slots__ = ['_initial_balance', '_balance', '_positions', '_trades', '_config']

    def __init__(self, initial_balance: float = 1000):
        self._initial_balance = initial_balance
        self._balance = initial_balance
        self._positions: list[PaperPosition] = []
        self._trades: list[PaperTrade] = []
        self._config = {
            'commission_pct': 0.1,
            'slippage_pct': 0.05
        }

    async def open_position(
        self,
        pair: str,
        direction: str,
        size: float,
        leverage: int,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        strategy: str = "manual"
    ) -> PaperPosition:
        """Open a paper position."""
        slippage = entry_price * (self._config['slippage_pct'] / 100)
        if direction == "long":
            actual_entry = entry_price + slippage
        else:
            actual_entry = entry_price - slippage

        commission = size * (self._config['commission_pct'] / 100)
        self._balance -= commission

        position = PaperPosition(
            id=f"paper_{datetime.utcnow().timestamp()}",
            pair=pair,
            direction=direction,
            entry_price=actual_entry,
            size=size,
            leverage=leverage,
            stop_loss=stop_loss,
            take_profit=take_profit,
            opened_at=datetime.utcnow().isoformat()
        )

        self._positions.append(position)
        return position

    async def close_position(
        self,
        position_id: str,
        exit_price: float,
        reason: str = "manual"
    ) -> Optional[PaperTrade]:
        """Close a paper position."""
        position = next((p for p in self._positions if p.id == position_id), None)
        if not position:
            return None

        slippage = exit_price * (self._config['slippage_pct'] / 100)
        if position.direction == "long":
            actual_exit = exit_price - slippage
            pnl_pct = ((actual_exit - position.entry_price) / position.entry_price
                       * 100 * position.leverage)
        else:
            actual_exit = exit_price + slippage
            pnl_pct = ((position.entry_price - actual_exit) / position.entry_price
                       * 100 * position.leverage)

        pnl = position.size * (pnl_pct / 100)
        commission = position.size * (self._config['commission_pct'] / 100)
        self._balance += pnl - commission

        trade = PaperTrade(
            id=position.id,
            pair=position.pair,
            direction=position.direction,
            entry_price=position.entry_price,
            exit_price=actual_exit,
            size=position.size,
            leverage=position.leverage,
            pnl=pnl,
            pnl_pct=pnl_pct,
            opened_at=position.opened_at,
            closed_at=datetime.utcnow().isoformat(),
            reason=reason,
            strategy=""
        )

        self._trades.append(trade)
        self._positions = [p for p in self._positions if p.id != position_id]
        return trade

    async def update_positions(self, current_prices: dict) -> list[PaperTrade]:
        """Update positions and check SL/TP triggers."""
        closed_trades: list[PaperTrade] = []

        for position in self._positions[:]:
            price = current_prices.get(position.pair)
            if not price:
                continue

            if position.direction == "long":
                position.pnl = ((price - position.entry_price) / position.entry_price
                                * 100 * position.leverage)
                if price <= position.stop_loss:
                    trade = await self.close_position(position.id, position.stop_loss, "stop_loss")
                    if trade:
                        closed_trades.append(trade)
                elif price >= position.take_profit:
                    trade = await self.close_position(position.id, position.take_profit, "take_profit")
                    if trade:
                        closed_trades.append(trade)
            else:
                position.pnl = ((position.entry_price - price) / position.entry_price
                                * 100 * position.leverage)
                if price >= position.stop_loss:
                    trade = await self.close_position(position.id, position.stop_loss, "stop_loss")
                    if trade:
                        closed_trades.append(trade)
                elif price <= position.take_profit:
                    trade = await self.close_position(position.id, position.take_profit, "take_profit")
                    if trade:
                        closed_trades.append(trade)

        return closed_trades

    def get_stats(self) -> dict:
        """Get trading statistics."""
        if not self._trades:
            return {
                'balance': self._balance,
                'initial_balance': self._initial_balance,
                'pnl': 0,
                'pnl_pct': 0,
                'total_trades': 0,
                'win_rate': 0,
                'open_positions': len(self._positions)
            }

        winning = [t for t in self._trades if t.pnl > 0]
        losing = [t for t in self._trades if t.pnl <= 0]

        return {
            'balance': round(self._balance, 2),
            'initial_balance': self._initial_balance,
            'pnl': round(self._balance - self._initial_balance, 2),
            'pnl_pct': round((self._balance - self._initial_balance) / self._initial_balance * 100, 2),
            'total_trades': len(self._trades),
            'winning_trades': len(winning),
            'losing_trades': len(losing),
            'win_rate': round(len(winning) / len(self._trades) * 100, 1) if self._trades else 0,
            'avg_win': round(sum(t.pnl for t in winning) / len(winning), 2) if winning else 0,
            'avg_loss': round(sum(t.pnl for t in losing) / len(losing), 2) if losing else 0,
            'open_positions': len(self._positions)
        }

    def get_positions(self) -> list[PaperPosition]:
        """Get open positions."""
        return self._positions

    def get_trades(self) -> list[PaperTrade]:
        """Get trade history."""
        return self._trades

    def reset(self) -> None:
        """Reset paper trader state."""
        self._balance = self._initial_balance
        self._positions = []
        self._trades = []
