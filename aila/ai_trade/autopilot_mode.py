"""
AUTOPILOT MODE - fully autonomous trading with TRADER and SNIPER agents.
Both agents work in parallel with smart signal queue.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .signal_queue import SignalQueue, SignalPriority
from .agent_stats import AgentStatsManager
from .config import AGENT_COOLDOWNS, PERFORMANCE_LIMITS
from .position_monitor import PositionMonitor
from ..utils.oauth_refresh import OAuthRefresher

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
        '_last_sniper_scan', '_current_agent', '_cascade_stats',
        '_last_position_sync', '_position_monitor',
        '_oauth_refresher', '_oauth_warn_sent', '_oauth_expired_sent',
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

        # Inject agent_stats into sniper for level-based leverage limits
        if hasattr(self._orchestrator, 'sniper'):
            self._orchestrator.sniper._agent_stats = self._agent_stats

        # Cascade analysis stats
        self._cascade_stats = {
            'current_stage': None,  # 'vip_p1', 'p2', 'p3', or None
            'paused_reason': None,  # 'position_limit' or None
            'last_cascade_at': None,
            'stages_completed': {'vip_p1': 0, 'p2': 0, 'p3': 0},
            'signals_found': {'vip': 0, 'p1': 0, 'p2': 0, 'p3': 0},
        }

        # Position sync tracking
        self._last_position_sync: Optional[datetime] = None

        # OAuth auto-refresh
        self._oauth_refresher = OAuthRefresher()
        self._oauth_warn_sent: bool = False
        self._oauth_expired_sent: bool = False

        # Active position management
        self._position_monitor = PositionMonitor(
            exchange=self._orchestrator.exchanges.primary,
            trader_agent=self._orchestrator.trader,
            position_manager=self._orchestrator.position_manager,
            scanner=self._orchestrator.trader.scanner,
            sniper_agent=self._orchestrator.sniper,
        )

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

    async def _check_oauth_token(self) -> bool:
        """Check OAuth token expiry and auto-refresh. Returns False to stop."""
        try:
            remaining = self._oauth_refresher.get_remaining_seconds()

            # Token is fresh — reset flags
            if remaining >= 7200:
                self._oauth_warn_sent = False
                self._oauth_expired_sent = False
                return True

            # Token expiring soon or expired — try auto-refresh
            if remaining < 7200:
                refreshed = await self._oauth_refresher.ensure_valid_token()
                if refreshed:
                    new_remaining = self._oauth_refresher.get_remaining_seconds()
                    if new_remaining >= 7200:
                        logger.info(
                            f"[OAUTH] Auto-refreshed! New expiry in "
                            f"{new_remaining // 3600}h {(new_remaining % 3600) // 60}m"
                        )
                        self._oauth_warn_sent = False
                        self._oauth_expired_sent = False
                        return True

                # Refresh failed or token still short — warn once
                if not self._oauth_warn_sent and remaining > 0:
                    self._oauth_warn_sent = True
                    hours = int(remaining // 3600)
                    mins = int((remaining % 3600) // 60)
                    logger.warning(
                        f"[OAUTH] Refresh failed, token expires in {hours}h {mins}m"
                    )
                    try:
                        await self._orchestrator.telegram.send_message(
                            f"🟡 <b>AILA AI Trade</b>: OAuth auto-refresh failed!\n\n"
                            f"Token expires in ~{hours}h {mins}m.\n\n"
                            "Manual renewal:\n"
                            "<code>su - aila -c 'claude auth login'</code>\n"
                            "<code>cp /home/aila/.claude/.credentials.json "
                            "/opt/aila/.claude/.credentials.json</code>"
                        )
                    except Exception:
                        pass

                # Expired and refresh failed — stop autopilot
                if remaining <= 0 and not self._oauth_expired_sent:
                    self._oauth_expired_sent = True
                    logger.error("[OAUTH] Token EXPIRED and refresh failed! Stopping.")
                    try:
                        await self._orchestrator.telegram.send_message(
                            "🔴 <b>AILA AI Trade</b>: OAuth EXPIRED!\n\n"
                            "Auto-refresh FAILED. Autopilot STOPPED.\n\n"
                            "<code>su - aila -c 'claude auth login'</code>\n"
                            "<code>cp /home/aila/.claude/.credentials.json "
                            "/opt/aila/.claude/.credentials.json</code>\n"
                            "<code>sudo systemctl restart aila</code>"
                        )
                    except Exception:
                        pass
                    return False

        except Exception as e:
            logger.error(f"[OAUTH] Check error: {e}")
        return True

    async def _sync_positions(self) -> None:
        """Sync open positions with signal queue using REAL exchange data."""
        try:
            # Get real positions from exchange
            real_positions = await self._orchestrator.exchanges.primary.get_positions()
            open_pairs = []
            for p in real_positions:
                size = float(p.get('size', 0))
                if size != 0:
                    symbol = p.get('symbol', '')
                    # Convert BTCUSDT -> BTC/USDT for queue
                    if '/' not in symbol and symbol.endswith('USDT'):
                        symbol = symbol[:-4] + '/USDT'
                    open_pairs.append(symbol)
            self._signal_queue.sync_positions(open_pairs)
            logger.info(f"[QUEUE] Synced {len(open_pairs)} open positions from exchange")
        except Exception as e:
            logger.error(f"Position sync error: {e}")

    async def _autopilot_loop(self) -> None:
        """Main autopilot loop with parallel TRADER and SNIPER scanning."""
        while self._running:
            try:
                self._reset_counters_if_needed()

                # Check OAuth token every loop iteration
                if not await self._check_oauth_token():
                    logger.error("[OAUTH] Autopilot stopped due to expired token")
                    self._running = False
                    self._orchestrator.mode = "IDLE"
                    break

                if not await self._can_trade():
                    await asyncio.sleep(10)
                    continue

                market_safety = await self._orchestrator.check_market_safety()
                if market_safety.get('crisis_level') in ['critical', 'elevated']:
                    logger.warning("Market unsafe, skipping cycle")
                    await asyncio.sleep(300)
                    continue

                # Sync closed positions FIRST (before cascade checks limits)
                # This ensures bot_positions is up-to-date with exchange state
                await self._sync_closed_positions()

                # Run TRADER and SNIPER scans
                await self._run_scan_cycle()

                # Process signal queue
                await self._process_queue()

                # Active position management (every 15 seconds internally)
                await self._position_monitor.check_positions()

                await asyncio.sleep(10)  # Base loop interval

            except Exception as e:
                logger.error(f"Autopilot error: {e}")
                await asyncio.sleep(60)

    async def _run_scan_cycle(self) -> None:
        """Run TRADER and SNIPER scans based on their intervals.

        SNIPER runs first + queue processed immediately so signals
        don't expire while waiting for TRADER cascade (2-3 min).
        """
        now = datetime.utcnow()

        # SNIPER scan first (fast, every 10 seconds)
        if self._config.get('sniper_enabled', True):
            should_scan_sniper = (
                self._last_sniper_scan is None or
                (now - self._last_sniper_scan).total_seconds() >= self._config['sniper_scan_interval_seconds']
            )
            if should_scan_sniper:
                await self._scan_sniper()
                # Process queue immediately after SNIPER finds signals
                await self._process_queue()

        # TRADER scan (slow cascade, every 60 seconds)
        if self._config.get('trader_enabled', True):
            should_scan_trader = (
                self._last_scan_time is None or
                (now - self._last_scan_time).total_seconds() >= self._config['scan_interval_seconds']
            )
            if should_scan_trader:
                await self._scan_trader()

    async def _scan_trader(self) -> None:
        """Scan for TRADER opportunities with cascade analysis."""
        self._currently_scanning = True
        self._current_agent = "TRADER"
        self._current_pair = None

        try:
            # Check if TRADER is paused
            paused, reason = self._agent_stats.is_paused("TRADER")
            if paused:
                logger.info(f"[TRADER] Paused: {reason}")
                return

            # Get cascade settings
            from .agent_settings import get_agent_settings
            settings = get_agent_settings().get_settings("TRADER")
            cascade_enabled = settings.get('cascade_enabled', True)
            pause_on_position_limit = settings.get('pause_on_position_limit', True)

            if not cascade_enabled:
                # Fallback to standard scan
                await self._scan_trader_standard()
                return

            # Check position limit before scanning (agent-specific)
            limits = self._agent_stats.get_level_limits("TRADER")
            max_positions = limits.get('max_positions', 1)
            try:
                real_positions = await self._orchestrator.exchanges.primary.get_positions()
                all_exchange = len([p for p in real_positions if float(p.get('size', 0)) != 0])
                current_positions = self._orchestrator.position_manager.get_open_count_by_agent("TRADER")
                # Fallback: only if bot_positions is completely empty (no tracking data)
                # If bot_positions has data but no TRADER positions, don't count SNIPER positions
                bot_positions = self._orchestrator.position_manager.get_bot_positions()
                if current_positions == 0 and all_exchange > 0 and len(bot_positions) == 0:
                    current_positions = all_exchange  # Full fallback only when no tracking data
            except Exception as e:
                logger.warning(f"[TRADER][CASCADE] Error fetching positions: {e}, using cache")
                current_positions = self._orchestrator.position_manager.get_open_count_by_agent("TRADER")

            if current_positions >= max_positions and pause_on_position_limit:
                self._cascade_stats['paused_reason'] = 'position_limit'
                self._cascade_stats['current_stage'] = None
                logger.info(
                    f"[TRADER][CASCADE] Paused: position limit reached "
                    f"({current_positions}/{max_positions})"
                )
                return

            self._cascade_stats['paused_reason'] = None

            # Get pairs by priority
            p1_max = settings.get('priority_1_max_pairs', 10)
            p2_max = settings.get('priority_2_max_pairs', 10)
            p3_max = settings.get('priority_3_max_pairs', 10)
            min_change = settings.get('min_24h_change_pct', 3.0)
            max_change = settings.get('max_24h_change_pct', 50.0)

            pairs_by_priority = await self._orchestrator.trader.scanner.get_pairs_by_priority(
                min_change=min_change,
                max_change=max_change,
                p1_max=p1_max,
                p2_max=p2_max,
                p3_max=p3_max,
            )

            vip_pairs = pairs_by_priority.get('vip', [])
            p1_pairs = pairs_by_priority.get('priority_1', [])
            p2_pairs = pairs_by_priority.get('priority_2', [])
            p3_pairs = pairs_by_priority.get('priority_3', [])

            total_pairs = len(vip_pairs) + len(p1_pairs) + len(p2_pairs) + len(p3_pairs)
            self._pairs_count = total_pairs

            logger.info(
                f"[TRADER][CASCADE] Starting cascade analysis: "
                f"VIP={len(vip_pairs)}, P1={len(p1_pairs)}, P2={len(p2_pairs)}, P3={len(p3_pairs)}"
            )

            # STAGE 1: VIP + Priority 1
            self._cascade_stats['current_stage'] = 'vip_p1'
            stage1_pairs = vip_pairs + p1_pairs

            if stage1_pairs:
                logger.info(f"[TRADER][CASCADE][STAGE 1] Analyzing {len(stage1_pairs)} VIP+P1 pairs...")
                opportunity = await self._analyze_pairs_batch(stage1_pairs, "vip_p1")

                if opportunity:
                    added = await self._add_opportunity_to_queue(opportunity)
                    if added:
                        self._cascade_stats['stages_completed']['vip_p1'] += 1
                        # Re-check agent-specific position count
                        trader_pos = self._orchestrator.position_manager.get_open_count_by_agent("TRADER")
                        if trader_pos + 1 >= max_positions:
                            logger.info("[TRADER][CASCADE] Position limit will be reached, stopping cascade")
                            self._cascade_stats['last_cascade_at'] = datetime.utcnow()
                            return

            # STAGE 2: Priority 2
            self._cascade_stats['current_stage'] = 'p2'
            if p2_pairs:
                logger.info(f"[TRADER][CASCADE][STAGE 2] Analyzing {len(p2_pairs)} P2 pairs...")
                opportunity = await self._analyze_pairs_batch(p2_pairs, "p2")

                if opportunity:
                    added = await self._add_opportunity_to_queue(opportunity)
                    if added:
                        self._cascade_stats['stages_completed']['p2'] += 1
                        trader_pos = self._orchestrator.position_manager.get_open_count_by_agent("TRADER")
                        if trader_pos + 1 >= max_positions:
                            logger.info("[TRADER][CASCADE] Position limit will be reached, stopping cascade")
                            self._cascade_stats['last_cascade_at'] = datetime.utcnow()
                            return

            # STAGE 3: Priority 3
            self._cascade_stats['current_stage'] = 'p3'
            if p3_pairs:
                logger.info(f"[TRADER][CASCADE][STAGE 3] Analyzing {len(p3_pairs)} P3 pairs...")
                opportunity = await self._analyze_pairs_batch(p3_pairs, "p3")

                if opportunity:
                    added = await self._add_opportunity_to_queue(opportunity)
                    if added:
                        self._cascade_stats['stages_completed']['p3'] += 1

            self._cascade_stats['current_stage'] = None
            self._cascade_stats['last_cascade_at'] = datetime.utcnow()
            logger.info("[TRADER][CASCADE] Cascade analysis complete")

        finally:
            self._last_scan_time = datetime.utcnow()
            self._currently_scanning = False
            self._current_agent = None

    async def _scan_trader_standard(self) -> None:
        """Standard TRADER scan without cascade (fallback)."""
        try:
            pairs = await self._orchestrator.trader.scanner.get_top_pairs()
            self._pairs_count = len(pairs) if pairs else 0
        except Exception:
            self._pairs_count = 0

        logger.info("[TRADER][STAGE 1] Scanning for opportunities (standard mode)...")
        opportunity = await self._orchestrator.trader.find_opportunity()

        if opportunity and opportunity.get('decision') in ['LONG', 'SHORT']:
            await self._add_opportunity_to_queue(opportunity)
        else:
            logger.info("[TRADER][STAGE 1] No valid opportunity found")

    async def _analyze_pairs_batch(
        self, pairs: list[dict], stage: str
    ) -> dict | None:
        """Analyze batch of pairs and return best opportunity using batch Claude API."""
        # Filter out pairs that are already in queue or have open positions
        filtered_pairs = []
        for pair_data in pairs:
            symbol = pair_data.get('symbol', '')
            if self._signal_queue.has_signal(symbol) or self._signal_queue.has_position(symbol):
                continue
            filtered_pairs.append(pair_data)

        if not filtered_pairs:
            logger.info(f"[TRADER][CASCADE][{stage.upper()}] No new pairs to analyze")
            return None

        # Fetch market data for all pairs in parallel
        pairs_with_data = []
        for pair_data in filtered_pairs:
            symbol = pair_data.get('symbol', '')
            self._current_pair = symbol

            try:
                market_data = await self._orchestrator.trader.scanner.get_market_data(symbol)
                if market_data:
                    pairs_with_data.append({
                        "symbol": symbol,
                        "market_data": market_data
                    })
            except Exception as e:
                logger.debug(f"[TRADER][CASCADE] Error fetching {symbol}: {e}")

        if not pairs_with_data:
            logger.info(f"[TRADER][CASCADE][{stage.upper()}] No market data available")
            return None

        # Use batch analysis (single Claude API call for the entire stage)
        try:
            opportunity = await self._orchestrator.trader.analyze_pairs(pairs_with_data)

            if opportunity and opportunity.get('decision') in ['LONG', 'SHORT']:
                symbol = opportunity.get('pair', 'UNKNOWN')
                confidence = opportunity.get('confidence', 0)

                logger.info(
                    f"[TRADER][CASCADE][{stage.upper()}] Found: "
                    f"{opportunity.get('decision')} {symbol} @ {confidence}%"
                )

                # Track signal source
                if stage == 'vip_p1':
                    if symbol in ['BTC/USDT', 'ETH/USDT', 'SOL/USDT']:
                        self._cascade_stats['signals_found']['vip'] += 1
                    else:
                        self._cascade_stats['signals_found']['p1'] += 1
                else:
                    self._cascade_stats['signals_found'][stage] += 1

                return opportunity

        except Exception as e:
            logger.error(f"[TRADER][CASCADE][{stage.upper()}] Batch analysis error: {e}")

        return None

    async def _add_opportunity_to_queue(self, opportunity: dict) -> bool:
        """Add opportunity to signal queue if valid."""
        pair = opportunity.get('pair', 'UNKNOWN')
        confidence = opportunity.get('confidence', 0)
        decision = opportunity.get('decision', 'UNKNOWN')

        logger.info(f"[TRADER][STAGE 1] Found: {decision} {pair} @ {confidence}%")

        if confidence >= self._config['min_confidence']:
            result = self._signal_queue.add_signal(
                signal=opportunity,
                agent="TRADER",
                priority=SignalPriority.NORMAL,
            )
            if result.get("added"):
                self._stats['trader_signals'] += 1
                return True
        else:
            logger.info(
                f"[TRADER][STAGE 1] Confidence {confidence}% < min {self._config['min_confidence']}%"
            )

        return False

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

                    # Fetch market data so REVIEWER can validate
                    try:
                        market_data = await self._orchestrator.trader.scanner.get_market_data(pair)
                    except Exception as e:
                        logger.warning(f"[SNIPER] Failed to fetch market data for {pair}: {e}")
                        market_data = {}

                    # Convert snipe to opportunity format
                    opportunity = {
                        'pair': pair,
                        'decision': direction,
                        'confidence': confidence,
                        'strategy': f'sniper_{trigger}',
                        'entry_price': snipe.get('entry_price'),
                        'stop_loss': snipe.get('stop_loss'),
                        'take_profit': snipe.get('take_profit'),
                        'leverage': snipe.get('leverage', 2),
                        'position_size_pct': 1,  # Smaller size for sniper
                        'reasoning': snipe.get('reasoning', ''),
                        'trigger_type': trigger,
                        'urgency': snipe.get('urgency', 'medium'),
                        'market_data': market_data,
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

        # Check agent-specific position limit before processing
        limits = self._agent_stats.get_level_limits(agent)
        max_pos = limits.get('max_positions', 1)
        agent_positions = self._orchestrator.position_manager.get_open_count_by_agent(agent)
        if agent_positions >= max_pos:
            logger.info(
                f"[{agent}] Position limit reached ({agent_positions}/{max_pos}), "
                f"skipping {pair}"
            )
            self._signal_queue.mark_processed(pair, agent, success=False)
            return

        logger.info(f"[{agent}][STAGE 2] Processing queued signal: {pair}")

        # Validate through REVIEWER
        opportunity = await self._validate_signal(signal, agent)

        if opportunity:
            # Execute trade
            result = await self._execute_trade(opportunity, agent)

            if result:
                self._signal_queue.set_position_open(pair)
                self._stats[f'{agent.lower()}_trades'] += 1
                # Set cooldown ONLY after successful trade execution
                self._signal_queue.mark_processed(pair, agent, success=True)
                logger.info(f"[{agent}] Cooldown activated after successful trade")
            else:
                # Trade execution failed - clean processed_pairs so pair can be retried
                self._signal_queue.mark_processed(pair, agent, success=False)
                logger.info(f"[{agent}] Trade execution failed for {pair} - no cooldown")
        else:
            # Validation failed - clean processed_pairs so pair can be retried
            self._signal_queue.mark_processed(pair, agent, success=False)
            logger.info(f"[{agent}] Signal rejected for {pair} - no cooldown")

    async def _validate_signal(self, signal: dict[str, Any], agent: str) -> Optional[dict[str, Any]]:
        """Validate signal through REVIEWER and RISK_GUARD."""
        pair = signal.get('pair', 'UNKNOWN')

        # Refresh market data before review (may be stale from queue wait)
        try:
            fresh_data = await self._orchestrator.trader.scanner.get_market_data(pair)
            if fresh_data:
                signal['market_data'] = fresh_data
                logger.info(f"[{agent}][STAGE 2] Refreshed market data for {pair}")
        except Exception as e:
            logger.warning(f"[{agent}][STAGE 2] Failed to refresh market data for {pair}: {e}")

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
            # Get min_confirmations from settings
            from .agent_settings import get_agent_settings
            settings = get_agent_settings().get_settings("TRADER")
            min_confirmations = settings.get('min_confirmations', 2)
            require_confirmations = settings.get('require_confirmations', True)

            if not require_confirmations:
                logger.info(f"[{agent}][STAGE 4] Confirmations disabled in settings, skipping")
            else:
                # Reuse context from Stage 1 enrichment if available (saves 6 API calls)
                context = signal.get("_market_context")
                if context:
                    logger.info(f"[{agent}][STAGE 4] Reusing market context from Stage 1 for {pair}")
                else:
                    logger.info(f"[{agent}][STAGE 4] Fetching market confirmations for {pair}")
                    context = await self._orchestrator.get_market_context(pair)

                confirmations = 0
                available_checks = 0
                whale_signal = context.get('whale', {}).get('signal', '')
                prediction_dir = context.get('prediction', {}).get('direction', '')
                sentiment = context.get('market', {}).get('sentiment', '')
                decision = signal['decision']

                # Whale confirmation (only count if data available)
                if whale_signal:
                    available_checks += 1
                    if whale_signal in ['buy', 'strong_buy'] and decision == 'LONG':
                        confirmations += 1
                    elif whale_signal in ['sell', 'strong_sell'] and decision == 'SHORT':
                        confirmations += 1

                # Prediction confirmation (only count if data available)
                if prediction_dir:
                    available_checks += 1
                    if prediction_dir == 'up' and decision == 'LONG':
                        confirmations += 1
                    elif prediction_dir == 'down' and decision == 'SHORT':
                        confirmations += 1

                # Sentiment confirmation (only count if data available)
                if sentiment:
                    available_checks += 1
                    if sentiment not in ['extreme_fear', 'extreme_greed']:
                        confirmations += 1

                # Adjust min_confirmations based on available data
                effective_min = min(min_confirmations, max(1, available_checks))

                logger.info(
                    f"[{agent}][STAGE 4] Confirmations: {confirmations}/{effective_min} "
                    f"(available={available_checks}, whale={whale_signal}, "
                    f"pred={prediction_dir}, sent={sentiment})"
                )

                if confirmations < effective_min:
                    logger.info(f"[{agent}][STAGE 4] Not enough confirmations: {confirmations}/{effective_min}")
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
            trade_leverage = opportunity.get('leverage', 3)
            size = await self._orchestrator.calculate_trade_size(
                entry_price=opportunity['entry_price'],
                stop_loss=opportunity['stop_loss'],
                confidence=opportunity.get('confidence', 50),
                leverage=trade_leverage,
                symbol=symbol
            )

            # Check if capital manager approved the trade
            if not size.get('can_trade', True):
                reason = size.get('reason', 'unknown')
                logger.warning(f"[{agent}][EXECUTE] Capital manager rejected: {reason}")
                return None

            logger.info(
                f"[{agent}][EXECUTE] Size: ${size['position_size_usdt']:.2f} "
                f"(margin=${size.get('margin_required', 0):.2f}, risk={size.get('risk_pct', 0):.1f}%)"
            )

            opportunity['position_size_usdt'] = size['position_size_usdt']
            opportunity['source_agent'] = agent

            # Map SNIPER's 'direction' to position_manager's 'decision'
            if 'direction' in opportunity and 'decision' not in opportunity:
                opportunity['decision'] = opportunity['direction']

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
                    'cascade': {
                        'current_stage': self._cascade_stats['current_stage'],
                        'paused_reason': self._cascade_stats['paused_reason'],
                        'signals_found': self._cascade_stats['signals_found'],
                        'stages_completed': self._cascade_stats['stages_completed'],
                    },
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

        # Get cascade status
        cascade_status = None
        if self._cascade_stats['paused_reason']:
            cascade_status = 'paused'
        elif self._cascade_stats['current_stage']:
            cascade_status = 'scanning'
        else:
            cascade_status = 'idle'

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
            'cascade': {
                'status': cascade_status,
                'current_stage': self._cascade_stats['current_stage'],
                'paused_reason': self._cascade_stats['paused_reason'],
                'last_cascade_at': self._cascade_stats['last_cascade_at'].isoformat() if self._cascade_stats['last_cascade_at'] else None,
                'signals_found': self._cascade_stats['signals_found'],
            },
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

    # ========== Position Sync and Learning ==========

    async def _sync_closed_positions(self) -> None:
        """Check for positions closed by exchange (SL/TP hit) every 30 seconds."""
        now = datetime.utcnow()

        # Only run every 30 seconds
        if self._last_position_sync:
            elapsed = (now - self._last_position_sync).total_seconds()
            if elapsed < 30:
                return

        self._last_position_sync = now

        try:
            # Get bot positions we're tracking
            position_manager = self._orchestrator.position_manager
            bot_positions = position_manager.get_bot_positions()

            if not bot_positions:
                return

            # Get current positions from exchange
            exchange = self._orchestrator.exchanges.primary
            exchange_positions = await exchange.get_positions()
            exchange_symbols = {p["symbol"] for p in exchange_positions}

            # Check which bot positions are no longer on exchange
            for symbol, pos_data in list(bot_positions.items()):
                if symbol not in exchange_symbols:
                    # Position closed on exchange!
                    logger.info(f"[SYNC] Detected closed position: {symbol}")

                    # Get closed PnL from exchange history
                    opened_at_ts = pos_data.get("opened_at_ts", 0)
                    closed_pnl = await exchange.get_closed_pnl_for_symbol(
                        symbol, opened_at_ts
                    )

                    if closed_pnl:
                        await self._on_position_closed(symbol, pos_data, closed_pnl)
                    else:
                        # Fallback: position closed but no PnL found
                        logger.warning(f"[SYNC] No closed PnL found for {symbol}, removing from tracking")
                        position_manager.remove_bot_position(symbol)

        except Exception as e:
            logger.error(f"Position sync error: {e}")

    async def _on_position_closed(
        self,
        symbol: str,
        position_data: dict[str, Any],
        closed_pnl: dict[str, Any],
    ) -> None:
        """Handle position closed by exchange (SL/TP hit)."""
        source = position_data.get("source", "TRADER")
        pnl_usdt = closed_pnl.get("pnl_usdt", 0)
        pnl_pct = 0

        # Calculate PnL percentage
        entry_price = position_data.get("entry_price", 0)
        exit_price = closed_pnl.get("exit_price", 0)
        leverage = int(position_data.get("leverage", 1))

        if entry_price > 0 and exit_price > 0:
            side = position_data.get("side", "LONG")
            if side == "LONG":
                pnl_pct = ((exit_price - entry_price) / entry_price) * 100 * leverage
            else:
                pnl_pct = ((entry_price - exit_price) / entry_price) * 100 * leverage

        is_win = pnl_usdt > 0
        close_reason = closed_pnl.get("close_reason", "unknown")

        # Calculate duration
        opened_at = position_data.get("opened_at", "")
        duration_minutes = 0
        if opened_at:
            try:
                start = datetime.fromisoformat(opened_at)
                duration_minutes = int((datetime.now() - start).total_seconds() / 60)
            except Exception:
                pass

        # Calculate R:R ratio
        rr_ratio = 0.0
        if position_data.get("stop_loss") and position_data.get("take_profit"):
            sl = position_data["stop_loss"]
            tp = position_data["take_profit"]
            risk = abs(entry_price - sl)
            reward = abs(tp - entry_price)
            if risk > 0:
                rr_ratio = reward / risk

        logger.info(
            f"[{source}][TRADE_CLOSED] {symbol} "
            f"PnL: ${pnl_usdt:.2f} ({pnl_pct:.1f}%) "
            f"Reason: {close_reason}"
        )

        # 1. Evaluate trade grade first
        grade = await self._evaluate_trade_grade(position_data, closed_pnl, is_win)

        # 2. Record trade stats with full trade data
        trade_data = {
            "symbol": symbol,
            "side": position_data.get("side", "LONG"),
            "entry_price": entry_price,
            "exit_price": exit_price,
            "close_reason": close_reason,
            "leverage": str(leverage),
            "grade": grade,
            "strategy": position_data.get("strategy", "unknown"),
            "duration_minutes": duration_minutes,
            "decision_log": position_data.get("decision_log", []),
        }
        result = self._agent_stats.record_trade(
            agent=source,
            pnl_usdt=pnl_usdt,
            pnl_pct=pnl_pct,
            duration_minutes=duration_minutes,
            rr_ratio=rr_ratio,
            trigger_type=close_reason,
            trade_data=trade_data,
        )

        # 3. Mark position as closed in queue
        ccxt_symbol = symbol.replace("USDT", "/USDT") if "/" not in symbol else symbol
        self._signal_queue.set_position_closed(ccxt_symbol)

        # 4. Update knowledge base
        await self._update_knowledge_base(source, symbol, position_data, closed_pnl, grade)

        # 4.5 AI analysis via ANALYST (Claude Haiku - non-critical)
        analyst_result = await self._run_analyst(source, symbol, trade_data, pnl_usdt, pnl_pct)

        # 4.6 MENTOR generates rules from ANALYST feedback
        # MENTOR for ALL grades including C (breakeven = learning opportunity)
        if analyst_result and analyst_result.get("grade") in ("A", "B", "C", "D", "F"):
            await self._run_mentor(source, symbol, trade_data, pnl_usdt, pnl_pct, analyst_result)

        # 5. Send Telegram notification
        await self._send_trade_closed_notification(
            source, symbol, position_data, closed_pnl, grade, result
        )

        # 6. Remove from bot positions and cancel orphan orders
        self._orchestrator.position_manager.remove_bot_position(symbol)

        # 6.5 Cancel any remaining orders for this symbol (prevent orphans)
        try:
            await self._orchestrator.position_manager._cancel_open_orders(symbol)
            logger.info(f"[SYNC] Cancelled orphan orders for {symbol}")
        except Exception as e:
            logger.warning(f"[SYNC] Failed to cancel orders for {symbol}: {e}")

        # 7. Check for level up
        if result.get("xp_result", {}).get("leveled_up"):
            await self._send_level_up_notification(source, result["xp_result"])

    async def _run_analyst(
        self,
        agent: str,
        symbol: str,
        trade_data: dict[str, Any],
        pnl_usdt: float,
        pnl_pct: float,
    ) -> dict[str, Any] | None:
        """Run ANALYST on closed trade for AI-powered learning."""
        try:
            analyst = self._orchestrator.analyst
            trade_for_analysis = {
                **trade_data,
                "pair": symbol,
                "pnl": pnl_usdt,
                "pnl_pct": pnl_pct,
                "source_agent": agent,
            }
            result = await analyst.analyze(trade_for_analysis)
            ai_grade = result.get("grade", "?")
            lesson = result.get("lesson_learned", result.get("lesson", ""))
            mgmt_score = result.get("position_management_score", "?")
            mgmt_lessons = result.get("management_lessons", [])

            logger.info(
                f"[{agent}][ANALYST] AI grade={ai_grade} mgmt={mgmt_score} | {lesson[:60]}"
            )
            if mgmt_lessons:
                logger.info(f"[{agent}][ANALYST] Management lessons: {mgmt_lessons[:2]}")

            return result
        except Exception as e:
            logger.error(f"[{agent}][ANALYST] Analysis failed: {e}")
            return None

    async def _run_mentor(
        self,
        agent: str,
        symbol: str,
        trade_data: dict[str, Any],
        pnl_usdt: float,
        pnl_pct: float,
        analyst_result: dict[str, Any],
    ) -> None:
        """Run MENTOR to generate rules from ANALYST feedback."""
        grade = analyst_result.get("grade", "?")
        lesson = analyst_result.get("lesson_learned", analyst_result.get("lesson", ""))
        logger.info(
            f"[{agent}][MENTOR] Starting for {symbol} grade={grade} "
            f"lesson_len={len(lesson) if lesson else 0}"
        )
        try:
            mentor = self._orchestrator.mentor
            trade_for_mentor = {
                **trade_data,
                "symbol": symbol,
                "pnl": pnl_usdt,
                "pnl_pct": pnl_pct,
                "source_agent": agent,
            }
            result = await mentor.review_trade(trade_for_mentor, analyst_result)
            rules_added = result.get("rules_added", 0)
            skipped = result.get("skipped", False)
            if skipped:
                logger.info(f"[{agent}][MENTOR] Skipped for {symbol}: no lesson or invalid grade")
            elif rules_added > 0:
                rules = result.get("new_rules", [])
                mgmt_added = result.get("management_rules_added", 0)
                logger.info(
                    f"[{agent}][MENTOR] Added {rules_added} rules ({mgmt_added} mgmt): {rules}"
                )
            else:
                logger.info(f"[{agent}][MENTOR] No new rules for {symbol} (Claude returned empty)")
        except Exception as e:
            logger.error(f"[{agent}][MENTOR] Rule generation failed: {e}", exc_info=True)

    async def _evaluate_trade_grade(
        self,
        position_data: dict[str, Any],
        closed_pnl: dict[str, Any],
        is_win: bool,
    ) -> str:
        """Evaluate trade and assign grade A/B/C/D/F."""
        pnl_pct = 0
        entry_price = position_data.get("entry_price", 0)
        exit_price = closed_pnl.get("exit_price", 0)
        leverage = int(position_data.get("leverage", 1))

        if entry_price > 0 and exit_price > 0:
            side = position_data.get("side", "LONG")
            if side == "LONG":
                pnl_pct = ((exit_price - entry_price) / entry_price) * 100 * leverage
            else:
                pnl_pct = ((entry_price - exit_price) / entry_price) * 100 * leverage

        close_reason = closed_pnl.get("close_reason", "")

        if is_win:
            if close_reason == "take_profit":
                return "A" if pnl_pct > 5 else "B"
            return "B"  # Win but not via TP
        else:
            if close_reason == "stop_loss":
                if pnl_pct > -3:
                    return "C"  # Small loss, acceptable
                return "D"  # Larger loss
            return "F"  # Bad loss

    async def _update_knowledge_base(
        self,
        agent: str,
        symbol: str,
        position_data: dict[str, Any],
        closed_pnl: dict[str, Any],
        grade: str,
    ) -> None:
        """Update agent knowledge base with trade results."""
        try:
            kb = self._orchestrator.knowledge_base
            pnl_usdt = closed_pnl.get("pnl_usdt", 0)
            is_win = pnl_usdt > 0
            close_reason = closed_pnl.get("close_reason", "unknown")

            # Get or create agent-specific data
            agent_key = f"{agent.lower()}_learning"
            if agent_key not in kb.data:
                kb.data[agent_key] = {
                    "best_pairs": [],
                    "worst_pairs": [],
                    "learned_rules": [],
                    "mistakes_to_avoid": [],
                }

            agent_data = kb.data[agent_key]

            # Update best/worst pairs
            pair_entry = {
                "symbol": symbol,
                "pnl_usdt": pnl_usdt,
                "grade": grade,
                "close_reason": close_reason,
                "added_at": datetime.now().isoformat(),
            }

            if is_win:
                # Add to best pairs (keep top 10)
                agent_data["best_pairs"].append(pair_entry)
                agent_data["best_pairs"] = sorted(
                    agent_data["best_pairs"],
                    key=lambda x: x.get("pnl_usdt", 0),
                    reverse=True,
                )[:10]
                logger.info(f"[{agent}][LEARN] Added to best_pairs: {symbol}")
            else:
                # Add to worst pairs (keep top 10)
                agent_data["worst_pairs"].append(pair_entry)
                agent_data["worst_pairs"] = sorted(
                    agent_data["worst_pairs"],
                    key=lambda x: x.get("pnl_usdt", 0),
                )[:10]
                logger.info(f"[{agent}][LEARN] Added to worst_pairs: {symbol}")

                # Add mistake to avoid for bad trades
                if grade in ["D", "F"]:
                    strategy = position_data.get("strategy", "unknown")
                    confidence = position_data.get("confidence", 0)
                    mistake = {
                        "symbol": symbol,
                        "strategy": strategy,
                        "confidence": confidence,
                        "grade": grade,
                        "lesson": f"Avoid {strategy} on {symbol} with conf={confidence}%",
                        "added_at": datetime.now().isoformat(),
                    }
                    agent_data["mistakes_to_avoid"].append(mistake)
                    agent_data["mistakes_to_avoid"] = agent_data["mistakes_to_avoid"][-20:]
                    logger.info(f"[{agent}][LEARN] Added mistake: {mistake['lesson']}")

            kb.save()

        except Exception as e:
            logger.error(f"Knowledge base update error: {e}")

    async def _send_trade_closed_notification(
        self,
        agent: str,
        symbol: str,
        position_data: dict[str, Any],
        closed_pnl: dict[str, Any],
        grade: str,
        stats_result: dict[str, Any],
    ) -> None:
        """Send Telegram notification for closed trade."""
        try:
            telegram = self._orchestrator.telegram
            if not telegram:
                return

            pnl_usdt = closed_pnl.get("pnl_usdt", 0)
            is_win = pnl_usdt > 0
            emoji = "✅" if is_win else "❌"
            result_text = "WIN" if is_win else "LOSS"
            close_reason = closed_pnl.get("close_reason", "unknown")

            # Calculate pnl percent
            entry_price = position_data.get("entry_price", 0)
            exit_price = closed_pnl.get("exit_price", 0)
            leverage = int(position_data.get("leverage", 1))
            pnl_pct = 0
            if entry_price > 0 and exit_price > 0:
                side = position_data.get("side", "LONG")
                if side == "LONG":
                    pnl_pct = ((exit_price - entry_price) / entry_price) * 100 * leverage
                else:
                    pnl_pct = ((entry_price - exit_price) / entry_price) * 100 * leverage

            # Get updated stats
            stats = stats_result.get("stats_snapshot", {})
            xp_result = stats_result.get("xp_result", {})
            xp_change = xp_result.get("xp_added", 0)

            # Format close reason
            reason_display = {
                "stop_loss": "Stop-Loss",
                "take_profit": "Take-Profit",
                "manual": "Manual",
            }.get(close_reason, close_reason.title())

            # Format message
            ccxt_symbol = symbol.replace("USDT", "/USDT") if "/" not in symbol else symbol
            side = position_data.get("side", "LONG")

            message = f"""
📉 <b>{agent}: Позиция закрыта</b>

Пара: <b>{ccxt_symbol}</b>
Сторона: {side}
Результат: {result_text} {emoji}
PnL: <b>${pnl_usdt:.2f}</b> ({pnl_pct:+.1f}%)
Причина: {reason_display}
Оценка: <b>{grade}</b>

📊 <b>Статистика {agent}:</b>
Сделок: {stats.get('trades_today', 0)} | Win: {stats.get('winrate', 0):.0f}%
XP: {xp_change:+d} | PnL сегодня: ${stats.get('pnl_today_usdt', 0):.2f}
"""
            await telegram.send_message(message.strip())

        except Exception as e:
            logger.error(f"Trade closed notification error: {e}")
