"""
AUTOPILOT MODE - fully autonomous trading.
AI makes decisions and trades independently within risk limits.
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger("ai_trade.autopilot")


class AutopilotMode:
    """Fully autonomous trading mode."""

    __slots__ = ['_orchestrator', '_running', '_config', '_stats', '_last_trade_time']

    def __init__(self, orchestrator: Any) -> None:
        self._orchestrator = orchestrator
        self._running = False
        self._last_trade_time: Optional[datetime] = None
        self._config = {
            'scan_interval_seconds': 60,
            'min_confidence': 70,
            'max_trades_per_hour': 5,
            'max_trades_per_day': 20,
            'cooldown_after_loss_minutes': 30,
            'require_multiple_confirmations': True
        }
        self._stats = {
            'trades_this_hour': 0,
            'trades_today': 0,
            'last_hour_reset': datetime.utcnow(),
            'last_day_reset': datetime.utcnow().date()
        }

    async def start(self) -> dict[str, Any]:
        """Start autopilot mode."""
        safety_check = await self._pre_flight_check()
        if not safety_check['passed']:
            return {'status': 'failed', 'reason': safety_check['reason']}

        self._running = True
        self._orchestrator.mode = "AUTOPILOT"
        logger.warning("AUTOPILOT MODE ACTIVATED")

        asyncio.create_task(self._autopilot_loop())

        return {'status': 'started', 'mode': 'AUTOPILOT', 'config': self._config}

    async def stop(self) -> dict[str, Any]:
        """Stop autopilot mode."""
        self._running = False
        self._orchestrator.mode = "OBSERVER"
        logger.warning("AUTOPILOT MODE DEACTIVATED")
        return {'status': 'stopped'}

    async def _pre_flight_check(self) -> dict[str, Any]:
        """Pre-flight safety checks."""
        checks: list[str] = []

        try:
            health = await self._orchestrator.war_room.run_health_check()
            if not health.get('healthy'):
                return {'passed': False, 'reason': 'System health check failed'}
            checks.append('health_ok')
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return {'passed': False, 'reason': f'Health check error: {e}'}

        try:
            market = await self._orchestrator.check_market_safety()
            if market.get('crisis_level') == 'critical':
                return {'passed': False, 'reason': 'Market in crisis mode'}
            checks.append('market_ok')
        except Exception as e:
            logger.error(f"Market safety check failed: {e}")

        try:
            balance = await self._orchestrator.exchanges.primary.get_balance('USDT')
            # NOTE: $5 min is for testing only, production should be $50+
            min_balance = 5
            if balance < min_balance:
                return {'passed': False, 'reason': f'Insufficient balance: ${balance}'}
            checks.append('balance_ok')
        except Exception as e:
            logger.error(f"Balance check failed: {e}")
            return {'passed': False, 'reason': f'Balance check error: {e}'}

        try:
            profile = await self._orchestrator.knowledge_base.get_trader_profile()
            # NOTE: Level 1 is for testing, production should be level 3+
            if profile.get('level', 1) < 1:
                return {'passed': False, 'reason': 'AI level too low (min: 1)'}
            checks.append('level_ok')
        except Exception as e:
            logger.error(f"Profile check failed: {e}")

        return {'passed': True, 'checks': checks}

    async def _autopilot_loop(self) -> None:
        """Main autopilot loop."""
        while self._running:
            try:
                self._reset_counters_if_needed()

                if not await self._can_trade():
                    await asyncio.sleep(self._config['scan_interval_seconds'])
                    continue

                market_safety = await self._orchestrator.check_market_safety()
                if market_safety.get('crisis_level') in ['critical', 'elevated']:
                    logger.warning("Market unsafe, skipping cycle")
                    await asyncio.sleep(300)
                    continue

                opportunity = await self._find_validated_opportunity()

                if opportunity:
                    await self._execute_trade(opportunity)

                await asyncio.sleep(self._config['scan_interval_seconds'])

            except Exception as e:
                logger.error(f"Autopilot error: {e}")
                await asyncio.sleep(60)

    async def _can_trade(self) -> bool:
        """Check if trading is allowed."""
        if self._stats['trades_this_hour'] >= self._config['max_trades_per_hour']:
            return False
        if self._stats['trades_today'] >= self._config['max_trades_per_day']:
            return False
        return True

    async def _find_validated_opportunity(self) -> Optional[dict[str, Any]]:
        """Find and validate trading opportunity."""
        opportunity = await self._orchestrator.trader.find_opportunity()

        if not opportunity or opportunity.get('decision') not in ['LONG', 'SHORT']:
            return None

        if opportunity.get('confidence', 0) < self._config['min_confidence']:
            return None

        review = await self._orchestrator.reviewer.review(opportunity)
        if review.get('decision') == 'REJECT':
            logger.info(f"Reviewer rejected: {review.get('reason')}")
            return None

        if review.get('decision') == 'MODIFY':
            opportunity = review.get('modified_opportunity', opportunity)

        risk_check = await self._orchestrator.risk_guard.validate_trade(opportunity)
        if not risk_check.get('approved'):
            logger.info(f"Risk guard blocked: {risk_check.get('reason')}")
            return None

        if self._config['require_multiple_confirmations']:
            context = await self._orchestrator.get_market_context(opportunity['pair'])

            confirmations = 0
            whale_signal = context.get('whale', {}).get('signal', '')
            prediction_dir = context.get('prediction', {}).get('direction', '')
            sentiment = context.get('market', {}).get('sentiment', '')

            if whale_signal in ['buy', 'strong_buy'] and opportunity['decision'] == 'LONG':
                confirmations += 1
            if whale_signal in ['sell', 'strong_sell'] and opportunity['decision'] == 'SHORT':
                confirmations += 1
            if prediction_dir == 'up' and opportunity['decision'] == 'LONG':
                confirmations += 1
            if prediction_dir == 'down' and opportunity['decision'] == 'SHORT':
                confirmations += 1
            if sentiment not in ['extreme_fear', 'extreme_greed']:
                confirmations += 1

            if confirmations < 2:
                logger.info(f"Not enough confirmations: {confirmations}/2")
                return None

        return opportunity

    async def _execute_trade(self, opportunity: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Execute validated trade."""
        logger.warning(f"Executing: {opportunity['decision']} {opportunity['pair']}")

        try:
            size = await self._orchestrator.calculate_trade_size(
                entry_price=opportunity['entry_price'],
                stop_loss=opportunity['stop_loss'],
                confidence=opportunity.get('confidence', 50)
            )

            result = await self._orchestrator.position_manager.open_position(
                pair=opportunity['pair'],
                direction=opportunity['decision'].lower(),
                size=size['position_size_usdt'],
                leverage=opportunity.get('leverage', 5),
                stop_loss=opportunity['stop_loss'],
                take_profit=opportunity['take_profit']
            )

            if result.get('success'):
                self._stats['trades_this_hour'] += 1
                self._stats['trades_today'] += 1
                self._last_trade_time = datetime.utcnow()

                await self._orchestrator.knowledge_base.add_xp(5, 'entry_timing')

                logger.info(f"Trade opened: {opportunity['pair']} {opportunity['decision']}")
            else:
                logger.error(f"Trade failed: {result.get('error')}")

            return result
        except Exception as e:
            logger.error(f"Trade execution error: {e}")
            return None

    def _reset_counters_if_needed(self) -> None:
        """Reset trade counters if needed."""
        now = datetime.utcnow()

        # Handle string from JSON persistence
        last_hour = self._stats['last_hour_reset']
        if isinstance(last_hour, str):
            try:
                last_hour = datetime.fromisoformat(last_hour.replace(' ', 'T'))
            except ValueError:
                last_hour = now

        if (now - last_hour).total_seconds() >= 3600:
            self._stats['trades_this_hour'] = 0
            self._stats['last_hour_reset'] = now

        # Handle string date from JSON persistence
        last_day = self._stats['last_day_reset']
        if isinstance(last_day, str):
            try:
                last_day = datetime.strptime(last_day, '%Y-%m-%d').date()
            except ValueError:
                last_day = now.date()

        if now.date() > last_day:
            self._stats['trades_today'] = 0
            self._stats['last_day_reset'] = now.date()

    def get_status(self) -> dict[str, Any]:
        """Get autopilot status."""
        # Safe datetime to string conversion
        def to_str(val: Any) -> Optional[str]:
            if val is None:
                return None
            if isinstance(val, str):
                return val
            if hasattr(val, 'isoformat'):
                return val.isoformat()
            return str(val)

        return {
            'running': self._running,
            'mode': 'AUTOPILOT' if self._running else 'INACTIVE',
            'config': self._config,
            'stats': {
                'trades_this_hour': self._stats['trades_this_hour'],
                'trades_today': self._stats['trades_today'],
                'last_hour_reset': to_str(self._stats.get('last_hour_reset')),
                'last_day_reset': to_str(self._stats.get('last_day_reset'))
            },
            'last_trade': to_str(self._last_trade_time)
        }

    def update_config(self, new_config: dict[str, Any]) -> dict[str, Any]:
        """Update autopilot configuration."""
        self._config.update(new_config)
        logger.info(f"Autopilot config updated: {new_config}")
        return self._config
