"""
OBSERVER MODE — observation mode.
AI analyzes market and shows what it would do, without real trading.
"""

import asyncio
import logging
from datetime import datetime

logger = logging.getLogger("ai_trade")


class ObserverMode:
    """Observation mode — AI analyzes but doesn't trade."""

    __slots__ = ['_orchestrator', '_paper_trader', '_signals', '_running']

    def __init__(self, orchestrator, paper_trader):
        self._orchestrator = orchestrator
        self._paper_trader = paper_trader
        self._signals: list[dict] = []
        self._running = False

    async def start(self) -> None:
        """Start observation mode."""
        # Stop autopilot if running to prevent duplicate scans
        if hasattr(self._orchestrator, 'autopilot') and self._orchestrator.autopilot._running:
            await self._orchestrator.autopilot.stop()
            logger.info("Stopped Autopilot mode before starting Observer")

        self._running = True
        self._orchestrator.mode = "OBSERVER"
        logger.info("Observer mode started")

        while self._running:
            try:
                await self._observation_cycle()
                await asyncio.sleep(60)
            except Exception as e:
                logger.error(f"Observer error: {e}")
                await asyncio.sleep(30)

    async def stop(self) -> None:
        """Stop observation mode."""
        self._running = False
        logger.info("Observer mode stopped")

    async def _observation_cycle(self) -> None:
        """Single observation cycle."""
        opportunity = await self._orchestrator.trader.find_opportunity()

        if opportunity and opportunity.get('decision') in ['LONG', 'SHORT']:
            signal = {
                'timestamp': datetime.utcnow().isoformat(),
                'pair': opportunity.get('pair'),
                'direction': opportunity.get('decision'),
                'entry_price': opportunity.get('entry_price', 0),
                'stop_loss': opportunity.get('stop_loss', 0),
                'take_profit': opportunity.get('take_profit', 0),
                'confidence': opportunity.get('confidence', 0),
                'strategy': opportunity.get('strategy', ''),
                'reasoning': opportunity.get('reasoning', ''),
                'status': 'signal'
            }

            self._signals.append(signal)
            logger.info(
                f"Observer signal: {signal['direction']} {signal['pair']} "
                f"@ {signal['entry_price']}"
            )

            if signal['entry_price'] and signal['stop_loss'] and signal['take_profit']:
                await self._paper_trader.open_position(
                    pair=signal['pair'],
                    direction=signal['direction'].lower(),
                    size=100,
                    leverage=5,
                    entry_price=signal['entry_price'],
                    stop_loss=signal['stop_loss'],
                    take_profit=signal['take_profit'],
                    strategy=signal.get('strategy', 'ai_observer')
                )

        # Update existing paper positions
        prices = await self._get_current_prices()
        closed = await self._paper_trader.update_positions(prices)

        for trade in closed:
            logger.info(
                f"Observer paper trade closed: {trade.pair} "
                f"PnL: {trade.pnl_pct:.2f}%"
            )

    async def _get_current_prices(self) -> dict:
        """Get current prices for open positions."""
        pairs = [p.pair for p in self._paper_trader.get_positions()]
        prices: dict = {}

        for pair in pairs:
            try:
                data = await self._orchestrator.scanner.get_pair_data(pair)
                if data and data.get('price'):
                    prices[pair] = data['price']
            except Exception:
                pass

        return prices

    def get_signals(self, limit: int = 50) -> list[dict]:
        """Get recent signals."""
        return self._signals[-limit:]

    def get_paper_stats(self) -> dict:
        """Get paper trading statistics."""
        return self._paper_trader.get_stats()

    def get_status(self) -> dict:
        """Get observer mode status."""
        return {
            'running': self._running,
            'mode': 'OBSERVER',
            'signals_count': len(self._signals),
            'paper_stats': self._paper_trader.get_stats(),
            'open_positions': len(self._paper_trader.get_positions())
        }
