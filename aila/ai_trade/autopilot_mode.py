"""
AUTOPILOT MODE - fully autonomous trading with TRADER and SNIPER agents.
Both agents work in parallel with smart signal queue.
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, Optional

from .signal_queue import SignalQueue, SignalPriority
from .agent_stats import AgentStatsManager
from .config import AGENT_COOLDOWNS, PERFORMANCE_LIMITS

logger = logging.getLogger("ai_trade.autopilot")
logger.setLevel(logging.INFO)

# Add file handler if not exists
if not logger.handlers:
    _handler = logging.FileHandler("/opt/aila/logs/ai_trade/autopilot.log")
    _handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))
    logger.addHandler(_handler)


class AutopilotMode:
    """Fully autonomous trading mode with TRADER and SNIPER agents."""

    __slots__ = [
        '_orchestrator', '_running', '_config', '_stats', '_last_trade_time',
        '_last_scan_time', '_currently_scanning', '_current_pair', '_pairs_count',
        '_signal_queue', '_agent_stats', '_sniper_scan_counter',
        '_last_sniper_scan', '_current_agent'
    ]

    def __init__(self, orchestrator: Any) -> None:
        self._orchestrator = orchestrator
        self._running = False
        self._last_trade_time: Optional[datetime] = None
        # Scan tracking
        self._last_scan_time: Optional[datetime] = None
        self._last_sniper_scan: Optional[datetime] = None
        self._currently_scanning: bool = False
        self._current_pair: Optional[str] = None
        self._current_agent: Optional[str] = None
        self._pairs_count: int = 0
        self._sniper_scan_counter: int = 0

        # Signal queue and agent stats
        self._signal_queue = SignalQueue()
        self._agent_stats = AgentStatsManager()

        self._config = {
            'scan_interval_seconds': 60,       # TRADER scan interval
            'sniper_scan_interval_seconds': 10,  # SNIPER scan interval
            'min_confidence': 70,
            'max_trades_per_hour': 5,
            'max_trades_per_day': 20,
            'cooldown_after_loss_minutes': 30,
            'require_multiple_confirmations': True,
            'sniper_enabled': True,            # Enable SNIPER
            'trader_enabled': True,            # Enable TRADER
        }
        self._stats = {
            'trades_this_hour': 0,
            'trades_today': 0,
            'last_hour_reset': datetime.utcnow(),
            'last_day_reset': datetime.utcnow().date(),
            'trader_signals': 0,
            'sniper_signals': 0,
            'trader_trades': 0,
            'sniper_trades': 0,
        }

    async def start(self) -> dict[str, Any]:
        """Start autopilot mode."""
        if self._running:
            logger.warning("Autopilot already running, ignoring start request")
            return {'status': 'already_running', 'mode': 'AUTOPILOT'}

        safety_check = await self._pre_flight_check()
        if not safety_check['passed']:
            return {'status': 'failed', 'reason': safety_check['reason']}

        self._running = True
        self._orchestrator.mode = "AUTOPILOT"
        logger.warning("AUTOPILOT MODE ACTIVATED (TRADER + SNIPER)")

        # Sync open positions with queue
        await self._sync_positions()

        asyncio.create_task(self._autopilot_loop())

        return {'status': 'started', 'mode': 'AUTOPILOT', 'config': self._config}

    async def stop(self) -> dict[str, Any]:
        """Stop autopilot mode."""
        self._running = False
        self._orchestrator.mode = "IDLE"
        logger.warning("AUTOPILOT MODE DEACTIVATED - System IDLE")
        return {'status': 'stopped', 'mode': 'IDLE'}

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
            min_balance = 5
            if balance < min_balance:
                return {'passed': False, 'reason': f'Insufficient balance: ${balance}'}
            checks.append('balance_ok')
        except Exception as e:
            logger.error(f"Balance check failed: {e}")
            return {'passed': False, 'reason': f'Balance check error: {e}'}

        # Check agent levels
        trader_level = self._agent_stats.get_level("TRADER")
        sniper_level = self._agent_stats.get_level("SNIPER")
        checks.append(f'trader_level_{trader_level}')
        checks.append(f'sniper_level_{sniper_level}')

        return {'passed': True, 'checks': checks}

    async def _sync_positions(self) -> None:
        """Sync open positions with signal queue."""
        try:
            positions = await self._orchestrator.position_manager.get_open_positions()
            open_pairs = [p.get("symbol", "") for p in positions]
            self._signal_queue.sync_positions(open_pairs)
            logger.info(f"[QUEUE] Synced {len(open_pairs)} open positions")
        except Exception as e:
            logger.error(f"Position sync error: {e}")

    async def _autopilot_loop(self) -> None:
        """Main autopilot loop with parallel TRADER and SNIPER scanning."""
        while self._running:
            try:
                self._reset_counters_if_needed()

                if not await self._can_trade():
                    await asyncio.sleep(10)
                    continue

                market_safety = await self._orchestrator.check_market_safety()
                if market_safety.get('crisis_level') in ['critical', 'elevated']:
                    logger.warning("Market unsafe, skipping cycle")
                    await asyncio.sleep(300)
                    continue

                # Run TRADER and SNIPER scans
                await self._run_scan_cycle()

                # Process signal queue
                await self._process_queue()

                await asyncio.sleep(10)  # Base loop interval

            except Exception as e:
                logger.error(f"Autopilot error: {e}")
                await asyncio.sleep(60)

    async def _run_scan_cycle(self) -> None:
        """Run TRADER and SNIPER scans based on their intervals."""
        now = datetime.utcnow()
        tasks = []

        # TRADER scan (every 60 seconds)
        if self._config.get('trader_enabled', True):
            should_scan_trader = (
                self._last_scan_time is None or
                (now - self._last_scan_time).total_seconds() >= self._config['scan_interval_seconds']
            )
            if should_scan_trader:
                tasks.append(self._scan_trader())

        # SNIPER scan (every 10 seconds)
        if self._config.get('sniper_enabled', True):
            should_scan_sniper = (
                self._last_sniper_scan is None or
                (now - self._last_sniper_scan).total_seconds() >= self._config['sniper_scan_interval_seconds']
            )
            if should_scan_sniper:
                tasks.append(self._scan_sniper())

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _scan_trader(self) -> None:
        """Scan for TRADER opportunities."""
        self._currently_scanning = True
        self._current_agent = "TRADER"
        self._current_pair = None

        try:
            # Check if TRADER is paused
            paused, reason = self._agent_stats.is_paused("TRADER")
            if paused:
                logger.info(f"[TRADER] Paused: {reason}")
                return

            # Get pairs count
            try:
                pairs = await self._orchestrator.trader.scanner.get_top_pairs()
                self._pairs_count = len(pairs) if pairs else 0
            except Exception:
                self._pairs_count = 0

            logger.info("[TRADER][STAGE 1] Scanning for opportunities...")
            opportunity = await self._orchestrator.trader.find_opportunity()

            if opportunity and opportunity.get('decision') in ['LONG', 'SHORT']:
                pair = opportunity.get('pair', 'UNKNOWN')
                confidence = opportunity.get('confidence', 0)
                logger.info(f"[TRADER][STAGE 1] Found: {opportunity.get('decision')} {pair} @ {confidence}%")

                if confidence >= self._config['min_confidence']:
                    # Add to queue with NORMAL priority
                    result = self._signal_queue.add_signal(
                        signal=opportunity,
                        agent="TRADER",
                        priority=SignalPriority.NORMAL,
                    )
                    if result.get("added"):
                        self._stats['trader_signals'] += 1
                else:
                    logger.info(f"[TRADER][STAGE 1] Confidence {confidence}% < min {self._config['min_confidence']}%")
            else:
                logger.info("[TRADER][STAGE 1] No valid opportunity found")

        finally:
            self._last_scan_time = datetime.utcnow()
            self._currently_scanning = False
            self._current_agent = None

    async def _scan_sniper(self) -> None:
        """Scan for SNIPER opportunities."""
        self._sniper_scan_counter += 1

        try:
            # Check if SNIPER is paused
            paused, reason = self._agent_stats.is_paused("SNIPER")
            if paused:
                logger.debug(f"[SNIPER] Paused: {reason}")
                return

            # Only log every 6th scan (once per minute) to reduce noise
            verbose = self._sniper_scan_counter % 6 == 0

            if verbose:
                logger.info("[SNIPER][STAGE 1] Scanning for breakout opportunities...")

            snipes = await self._orchestrator.scan_snipe_opportunities()

            if snipes:
                for snipe in snipes:
                    pair = snipe.get('pair', 'UNKNOWN')
                    trigger = snipe.get('trigger_type', 'unknown')
                    direction = snipe.get('direction', 'UNKNOWN')
                    confidence = snipe.get('confidence', 0)

                    logger.warning(f"[SNIPER][STAGE 1] Found: {trigger} {direction} {pair} @ {confidence}%")

                    # Convert snipe to opportunity format
                    opportunity = {
                        'pair': pair,
                        'decision': direction,
                        'confidence': confidence,
                        'strategy': f'sniper_{trigger}',
                        'entry_price': snipe.get('entry_price'),
                        'stop_loss': snipe.get('stop_loss'),
                        'take_profit': snipe.get('take_profit'),
                        'leverage': 5,  # Conservative for sniper
                        'position_size_pct': 1,  # Smaller size for sniper
                        'reasoning': snipe.get('reasoning', ''),
                        'trigger_type': trigger,
                        'urgency': snipe.get('urgency', 'medium'),
                    }

                    # Add to queue with HIGH priority
                    result = self._signal_queue.add_signal(
                        signal=opportunity,
                        agent="SNIPER",
                        priority=SignalPriority.HIGH,
                    )
                    if result.get("added"):
                        self._stats['sniper_signals'] += 1

            elif verbose:
                logger.info("[SNIPER][STAGE 1] No breakout opportunities found")

        finally:
            self._last_sniper_scan = datetime.utcnow()

    async def _process_queue(self) -> None:
        """Process signals from queue."""
        queue_signal = self._signal_queue.get_next()

        if not queue_signal:
            return

        signal = queue_signal["signal"]
        agent = queue_signal["agent"]
        pair = signal.get("pair", "UNKNOWN")

        logger.info(f"[{agent}][STAGE 2] Processing queued signal: {pair}")

        # Validate through REVIEWER
        opportunity = await self._validate_signal(signal, agent)

        if opportunity:
            # Execute trade
            result = await self._execute_trade(opportunity, agent)

            if result:
                self._signal_queue.set_position_open(pair)
                self._stats[f'{agent.lower()}_trades'] += 1

        # Mark as processed (sets cooldown)
        self._signal_queue.mark_processed(pair, agent, opportunity is not None)

    async def _validate_signal(self, signal: dict[str, Any], agent: str) -> Optional[dict[str, Any]]:
        """Validate signal through REVIEWER and RISK_GUARD."""
        pair = signal.get('pair', 'UNKNOWN')

        # STAGE 2: REVIEWER
        logger.info(f"[{agent}][STAGE 2] Sending to Reviewer: {pair}")
        review = await self._orchestrator.reviewer.review(signal)
        logger.info(f"[{agent}][STAGE 2] Reviewer decision: {review.get('decision')}")

        if review.get('decision') == 'REJECT':
            logger.info(f"[{agent}][STAGE 2] Reviewer rejected: {review.get('reason')}")
            return None

        if review.get('decision') == 'MODIFY':
            signal = review.get('modified_opportunity', signal)
            logger.info(f"[{agent}][STAGE 2] Reviewer MODIFY applied")

        # STAGE 3: RISK_GUARD with agent-specific limits
        logger.info(f"[{agent}][STAGE 3] Risk guard validating {pair}")

        # Get agent-specific limits
        limits = self._agent_stats.get_level_limits(agent)
        signal['agent_limits'] = limits
        signal['source_agent'] = agent

        risk_check = await self._orchestrator.risk_guard.validate_trade(signal)
        logger.info(f"[{agent}][STAGE 3] Risk check: approved={risk_check.get('approved')}")

        if not risk_check.get('approved'):
            logger.info(f"[{agent}][STAGE 3] Risk guard blocked: {risk_check.get('reason')}")
            return None

        # STAGE 4: Market confirmations (only for TRADER)
        if agent == "TRADER" and self._config['require_multiple_confirmations']:
            logger.info(f"[{agent}][STAGE 4] Getting market confirmations for {pair}")
            context = await self._orchestrator.get_market_context(pair)

            confirmations = 0
            whale_signal = context.get('whale', {}).get('signal', '')
            prediction_dir = context.get('prediction', {}).get('direction', '')
            sentiment = context.get('market', {}).get('sentiment', '')

            if whale_signal in ['buy', 'strong_buy'] and signal['decision'] == 'LONG':
                confirmations += 1
            if whale_signal in ['sell', 'strong_sell'] and signal['decision'] == 'SHORT':
                confirmations += 1
            if prediction_dir == 'up' and signal['decision'] == 'LONG':
                confirmations += 1
            if prediction_dir == 'down' and signal['decision'] == 'SHORT':
                confirmations += 1
            if sentiment not in ['extreme_fear', 'extreme_greed']:
                confirmations += 1

            logger.info(
                f"[{agent}][STAGE 4] Confirmations: {confirmations}/2 "
                f"(whale={whale_signal}, pred={prediction_dir}, sent={sentiment})"
            )

            if confirmations < 2:
                logger.info(f"[{agent}][STAGE 4] Not enough confirmations: {confirmations}/2")
                return None

        logger.info(f"[{agent}][STAGE 5] All validations passed for {pair}")
        return signal

    async def _execute_trade(self, opportunity: dict[str, Any], agent: str) -> Optional[dict[str, Any]]:
        """Execute validated trade."""
        symbol = opportunity['pair']
        side = opportunity['decision']
        entry_price = opportunity.get('entry_price', 0)
        trigger_type = opportunity.get('trigger_type')

        # Get emoji for agent
        emoji = "🎯" if agent == "SNIPER" else "📈"

        logger.warning(
            f"[{agent}][READY_TO_TRADE] {emoji} {symbol} {side} @ {entry_price} - "
            f"all checks passed, executing..."
        )

        try:
            logger.info(f"[{agent}][EXECUTE] Calculating trade size for {symbol}")
            size = await self._orchestrator.calculate_trade_size(
                entry_price=opportunity['entry_price'],
                stop_loss=opportunity['stop_loss'],
                confidence=opportunity.get('confidence', 50)
            )
            logger.info(f"[{agent}][EXECUTE] Size calculated: ${size['position_size_usdt']:.2f}")

            opportunity['position_size_usdt'] = size['position_size_usdt']
            opportunity['source_agent'] = agent

            logger.info(f"[{agent}][EXECUTE] Opening position for {symbol}")
            result = await self._orchestrator.position_manager.open_position(opportunity)
            logger.info(f"[{agent}][EXECUTE] Position result: {result is not None}")

            if result:
                self._stats['trades_this_hour'] += 1
                self._stats['trades_today'] += 1
                self._last_trade_time = datetime.utcnow()

                # Add XP for entry
                self._agent_stats.add_xp(agent, 5, 'entry_timing')

                logger.info(f"[{agent}][TRADE_OPENED] {emoji} {symbol} {side} @ {entry_price}")

                # Send Telegram notification
                await self._send_trade_notification(opportunity, agent, trigger_type)

                return result
            else:
                logger.error(f"[{agent}][TRADE_FAILED] {symbol} {side} - position not opened")
                return None

        except Exception as e:
            logger.error(f"[{agent}][TRADE_FAILED] {symbol} {side} - {e}")
            return None

    async def _send_trade_notification(
        self, opportunity: dict[str, Any], agent: str, trigger_type: Optional[str]
    ) -> None:
        """Send Telegram notification for new trade."""
        try:
            telegram = self._orchestrator.telegram
            if not telegram:
                return

            emoji = "🎯" if agent == "SNIPER" else "📈"
            stats = self._agent_stats.get_stats(agent)

            pair = opportunity.get('pair', 'UNKNOWN')
            side = opportunity.get('decision', 'UNKNOWN')
            entry = opportunity.get('entry_price', 0)
            sl = opportunity.get('stop_loss', 0)
            tp = opportunity.get('take_profit', 0)
            confidence = opportunity.get('confidence', 0)

            trigger_text = f"\nТриггер: {trigger_type}" if trigger_type else ""

            message = f"""
{emoji} <b>{agent} TRADE OPENED</b>

<b>Пара:</b> {pair}
<b>Направление:</b> {side}
<b>Вход:</b> {entry:.6f}
<b>SL:</b> {sl:.6f}
<b>TP:</b> {tp:.6f}
<b>Confidence:</b> {confidence}%{trigger_text}

<b>📊 {agent} Stats:</b>
Level: {stats['level']} | WR: {stats['winrate']:.1f}%
PnL: ${stats['total_pnl_usdt']:.2f}
Trades today: {stats['trades_today']}
"""
            await telegram.send_message(message.strip())

        except Exception as e:
            logger.error(f"Telegram notification error: {e}")

    async def _can_trade(self) -> bool:
        """Check if trading is allowed."""
        if self._stats['trades_this_hour'] >= self._config['max_trades_per_hour']:
            return False
        if self._stats['trades_today'] >= self._config['max_trades_per_day']:
            return False
        return True

    def _reset_counters_if_needed(self) -> None:
        """Reset trade counters if needed."""
        now = datetime.utcnow()

        last_hour = self._stats['last_hour_reset']
        if isinstance(last_hour, str):
            try:
                last_hour = datetime.fromisoformat(last_hour.replace(' ', 'T'))
            except ValueError:
                last_hour = now

        if (now - last_hour).total_seconds() >= 3600:
            self._stats['trades_this_hour'] = 0
            self._stats['last_hour_reset'] = now

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
            'mode': 'AUTOPILOT' if self._running else 'IDLE',
            'config': self._config,
            'stats': {
                'trades_this_hour': self._stats['trades_this_hour'],
                'trades_today': self._stats['trades_today'],
                'trader_signals': self._stats['trader_signals'],
                'sniper_signals': self._stats['sniper_signals'],
                'trader_trades': self._stats['trader_trades'],
                'sniper_trades': self._stats['sniper_trades'],
                'last_hour_reset': to_str(self._stats.get('last_hour_reset')),
                'last_day_reset': to_str(self._stats.get('last_day_reset'))
            },
            'last_trade': to_str(self._last_trade_time),
            'queue': self._signal_queue.get_status(),
            'agents': {
                'trader': {
                    'enabled': self._config.get('trader_enabled', True),
                    'level': self._agent_stats.get_level("TRADER"),
                    'limits': self._agent_stats.get_level_limits("TRADER"),
                    'paused': self._agent_stats.is_paused("TRADER")[0],
                    'cooldown_remaining': self._signal_queue.get_cooldown_remaining("TRADER"),
                },
                'sniper': {
                    'enabled': self._config.get('sniper_enabled', True),
                    'level': self._agent_stats.get_level("SNIPER"),
                    'limits': self._agent_stats.get_level_limits("SNIPER"),
                    'paused': self._agent_stats.is_paused("SNIPER")[0],
                    'cooldown_remaining': self._signal_queue.get_cooldown_remaining("SNIPER"),
                },
            },
        }

    def update_config(self, new_config: dict[str, Any]) -> dict[str, Any]:
        """Update autopilot configuration."""
        self._config.update(new_config)
        logger.info(f"Autopilot config updated: {new_config}")
        return self._config

    def get_heartbeat(self) -> dict[str, Any]:
        """Get real-time autopilot heartbeat for UI indicator."""
        now = datetime.utcnow()

        seconds_since_scan: Optional[float] = None
        if self._last_scan_time:
            last_scan = self._last_scan_time
            if isinstance(last_scan, str):
                try:
                    last_scan = datetime.fromisoformat(last_scan.replace(' ', 'T'))
                except ValueError:
                    last_scan = None
            if last_scan:
                seconds_since_scan = (now - last_scan).total_seconds()

        seconds_since_sniper: Optional[float] = None
        if self._last_sniper_scan:
            last_sniper = self._last_sniper_scan
            if isinstance(last_sniper, str):
                try:
                    last_sniper = datetime.fromisoformat(last_sniper.replace(' ', 'T'))
                except ValueError:
                    last_sniper = None
            if last_sniper:
                seconds_since_sniper = (now - last_sniper).total_seconds()

        return {
            'running': self._running,
            'last_scan_time': self._last_scan_time.isoformat() if self._last_scan_time else None,
            'last_sniper_scan_time': self._last_sniper_scan.isoformat() if self._last_sniper_scan else None,
            'seconds_since_last_scan': round(seconds_since_scan, 1) if seconds_since_scan else None,
            'seconds_since_sniper_scan': round(seconds_since_sniper, 1) if seconds_since_sniper else None,
            'currently_scanning': self._currently_scanning,
            'current_agent': self._current_agent,
            'current_pair': self._current_pair,
            'pairs_count': self._pairs_count,
            'scan_interval': self._config.get('scan_interval_seconds', 60),
            'sniper_scan_interval': self._config.get('sniper_scan_interval_seconds', 10),
            'queue_size': len(self._signal_queue._queue),
        }

    def set_current_pair(self, pair: Optional[str]) -> None:
        """Set currently scanning pair."""
        self._current_pair = pair

    def get_agent_stats(self) -> dict[str, Any]:
        """Get stats for both agents."""
        return self._agent_stats.get_all_stats()

    def get_queue_status(self) -> dict[str, Any]:
        """Get signal queue status."""
        return self._signal_queue.get_status()

    def clear_agent_pause(self, agent: str) -> None:
        """Clear pause for agent."""
        self._agent_stats.clear_pause(agent)

    def clear_agent_cooldown(self, agent: str) -> None:
        """Clear cooldown for agent."""
        self._signal_queue.clear_cooldown(agent)

    async def record_trade_result(
        self,
        agent: str,
        pnl_usdt: float,
        pnl_pct: float,
        duration_minutes: int,
        rr_ratio: float,
        pair: str,
        trigger_type: Optional[str] = None,
    ) -> dict[str, Any]:
        """Record trade result and update agent stats."""
        # Mark position as closed in queue
        self._signal_queue.set_position_closed(pair)

        # Record stats
        result = self._agent_stats.record_trade(
            agent=agent,
            pnl_usdt=pnl_usdt,
            pnl_pct=pnl_pct,
            duration_minutes=duration_minutes,
            rr_ratio=rr_ratio,
            trigger_type=trigger_type,
        )

        # Check for level up notification
        if result.get("xp_result", {}).get("leveled_up"):
            await self._send_level_up_notification(agent, result["xp_result"])

        return result

    async def _send_level_up_notification(self, agent: str, xp_result: dict[str, Any]) -> None:
        """Send Telegram notification for level up."""
        try:
            telegram = self._orchestrator.telegram
            if not telegram:
                return

            emoji = "🎯" if agent == "SNIPER" else "📈"
            new_level = xp_result.get("level", 1)
            limits = self._agent_stats.get_level_limits(agent)

            message = f"""
🎉 <b>{emoji} {agent} LEVEL UP!</b>

<b>Новый уровень:</b> {new_level}

<b>Новые лимиты:</b>
• Max Leverage: {limits.get('max_leverage')}x
• Max Positions: {limits.get('max_positions')}
• Max Risk: {limits.get('max_risk_pct')}%
• Max Daily Trades: {limits.get('max_daily_trades')}
"""
            await telegram.send_message(message.strip())

        except Exception as e:
            logger.error(f"Level up notification error: {e}")
