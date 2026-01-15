"""
AILA - Trading Engine

Main trading loop that coordinates strategy signals with order execution.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any, Callable, Optional

import pandas as pd
import structlog

from ..core.risk import PositionSizer, PositionSizingConfig, StopLossConfig, StopLossManager, TakeProfitManager
from ..core.strategy import Signal, SignalType, TripleSuperTrendStrategy
from ..exchange import BybitClient, FuturesTrader, SpotTrader
from ..exchange.models import AccountType, MarginMode, Position, PositionSide

logger = structlog.get_logger(__name__)


class EngineState(Enum):
    """Trading engine states."""

    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"


@dataclass
class TradingEngineConfig:
    """Configuration for trading engine."""

    # Update intervals (optimized for Bybit 600 req/5s limit)
    candle_update_interval: int = 60  # seconds
    position_check_interval: int = 1  # seconds - ultra fast position monitoring
    scan_interval: int = 1  # seconds between scans - ultra fast (safe for <50 pairs)

    # Fast scanning optimization (Bybit: 600 req/5s = 120 req/s)
    max_parallel_kline_requests: int = 10  # parallel kline requests
    kline_request_delay: float = 0.1  # 100ms between batches (safe margin)

    # Trading settings
    auto_start: bool = False
    paper_trading: bool = False
    order_size: float = 100.0  # Fixed order size in USDT

    # Position sizing settings
    position_sizing_mode: str = "fixed_amount"  # fixed_amount | risk_percent | kelly
    risk_per_trade: float = 2.0  # % of balance to risk (for risk_percent mode)

    # Margin mode
    margin_mode: str = "cross"  # cross | isolated

    # Safety
    max_daily_loss_percent: float = 5.0
    max_consecutive_losses: int = 3
    cooldown_after_loss_streak: int = 60  # minutes

    # Execution
    use_market_orders: bool = True
    slippage_tolerance: float = 0.1  # percent


@dataclass
class EngineStats:
    """Trading engine statistics."""

    start_time: Optional[datetime] = None
    signals_processed: int = 0
    trades_executed: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_pnl: Decimal = Decimal("0")
    daily_pnl: Decimal = Decimal("0")
    consecutive_losses: int = 0
    last_trade_time: Optional[datetime] = None

    @property
    def win_rate(self) -> float:
        """Calculate win rate percentage."""
        total = self.winning_trades + self.losing_trades
        if total == 0:
            return 0.0
        return (self.winning_trades / total) * 100


class TradingEngine:
    """
    Main trading engine for AILA.

    Coordinates:
    - Strategy signal generation
    - Risk management checks
    - Order execution
    - Position management

    Example:
        client = BybitClient(config)
        strategy = TripleSuperTrendStrategy()

        engine = TradingEngine(
            client=client,
            strategy=strategy,
        )

        await engine.start()
        # Engine runs in background
        await engine.stop()
    """

    def __init__(
        self,
        client: BybitClient,
        strategy: TripleSuperTrendStrategy,
        config: Optional[TradingEngineConfig] = None,
    ):
        """
        Initialize trading engine.

        Args:
            client: Configured Bybit client
            strategy: Trading strategy instance
            config: Engine configuration
        """
        self.client = client
        self.strategy = strategy
        self.config = config or TradingEngineConfig()

        # Initialize traders based on account type
        if client.config.account_type == AccountType.FUTURES:
            self.trader = FuturesTrader(
                client,
                default_leverage=client.config.default_leverage,
            )
        else:
            self.trader = SpotTrader(client)

        # Risk management - use position_sizing_mode from config
        position_sizing_config = PositionSizingConfig(
            mode=self.config.position_sizing_mode,
            fixed_amount=Decimal(str(self.config.order_size)),
            risk_per_trade=self.config.risk_per_trade,
            max_open_positions=strategy.config.max_open_positions,
        )
        self.position_sizer = PositionSizer(position_sizing_config)

        # Stop-loss manager
        stop_loss_config = StopLossConfig()
        self.stop_loss_manager = StopLossManager(stop_loss_config)
        self.take_profit_manager = TakeProfitManager()

        # State
        self.state = EngineState.STOPPED
        self.stats = EngineStats()
        self._active_positions: dict[str, dict] = {}
        self._pending_signals: dict[str, Signal] = {}

        # API-centric position tracking
        self._last_api_positions: dict[str, dict] = {}  # symbol -> position data from API

        # Tasks
        self._main_task: Optional[asyncio.Task] = None
        self._position_task: Optional[asyncio.Task] = None

        # Callbacks
        self._on_signal_callbacks: list[Callable] = []
        self._on_trade_callbacks: list[Callable] = []
        self._on_error_callbacks: list[Callable] = []

        # Pause flag - when True, skip scanning but keep monitoring positions
        self.paused = False

        # Stop flag - when True, all tasks should exit immediately
        self._stop_requested = False

    async def start(self) -> bool:
        """
        Start the trading engine.

        Returns:
            True if started successfully
        """
        if self.state != EngineState.STOPPED:
            logger.warning("Engine already running", state=self.state.value)
            return False

        # Reset stop flag before starting
        self._stop_requested = False
        self.state = EngineState.STARTING
        logger.info("Starting trading engine")

        try:
            # Validate connection
            if not self.client.is_connected:
                if not self.client.connect():
                    raise Exception("Failed to connect to exchange")

            # Initialize state
            self.stats = EngineStats(start_time=datetime.utcnow())

            # Sync existing positions from API at startup
            await self._sync_positions_from_api()

            # Start main loop
            self._main_task = asyncio.create_task(self._main_loop())
            self._position_task = asyncio.create_task(self._position_monitoring_loop())

            self.state = EngineState.RUNNING
            logger.info("Trading engine started")

            # Log strategy parameters for debugging
            if hasattr(self.strategy, 'config'):
                cfg = self.strategy.config
                logger.info(
                    "Strategy config loaded",
                    st1_period=getattr(cfg, 'st1_period', None),
                    st1_mult=getattr(cfg, 'st1_multiplier', None),
                    st1_role=getattr(cfg, 'st1_role', None),
                    st2_period=getattr(cfg, 'st2_period', None),
                    st2_mult=getattr(cfg, 'st2_multiplier', None),
                    st2_role=getattr(cfg, 'st2_role', None),
                    st3_period=getattr(cfg, 'st3_period', None),
                    st3_mult=getattr(cfg, 'st3_multiplier', None),
                    st3_role=getattr(cfg, 'st3_role', None),
                    trigger_confirm=getattr(cfg, 'trigger_confirm_candles', None),
                    ema_enabled=getattr(cfg, 'ema_enabled', None),
                    sl_mode=getattr(cfg, 'sl_mode', None),
                    sl_line=getattr(cfg, 'sl_supertrend_line', None),
                    tp_mode=getattr(cfg, 'tp_mode', None),
                    tp_ratio=getattr(cfg, 'tp_risk_ratio', None),
                )

            return True

        except Exception as e:
            self.state = EngineState.ERROR
            logger.error("Failed to start engine", error=str(e))
            await self._notify_error(e)
            return False

    async def stop(self, close_positions: bool = False) -> None:
        """
        Stop the trading engine.

        Args:
            close_positions: Whether to close all positions before stopping
        """
        if self.state == EngineState.STOPPED:
            return

        # Set stop flag FIRST - this makes loops exit immediately
        self._stop_requested = True
        self.state = EngineState.STOPPING
        logger.info("Stopping trading engine - stop_requested=True")

        # Close positions if requested
        if close_positions:
            await self._close_all_positions()

        # Cancel tasks with proper timeout (no shield - we want them to actually cancel!)
        if self._main_task and not self._main_task.done():
            self._main_task.cancel()
            try:
                await asyncio.wait_for(self._main_task, timeout=3.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                logger.warning("Main task cancel timeout - forcing")
            except Exception:
                pass

        if self._position_task and not self._position_task.done():
            self._position_task.cancel()
            try:
                await asyncio.wait_for(self._position_task, timeout=3.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                logger.warning("Position task cancel timeout - forcing")
            except Exception:
                pass

        # Clear active positions tracking
        self._active_positions.clear()

        self.state = EngineState.STOPPED
        self._stop_requested = False  # Reset for next start
        logger.info("Trading engine stopped", stats=self.stats)

    async def _main_loop(self) -> None:
        """Main trading loop with OPTIMIZED fast scanning.

        Optimization: Uses single get_all_tickers() call to filter symbols
        BEFORE requesting expensive kline data. This reduces API calls from
        ~500 to ~50 for typical filter settings.

        Bybit API limits: 600 requests / 5 seconds (120 req/s)
        """
        scan_count = 0
        while self.state == EngineState.RUNNING and not self._stop_requested:
            try:
                # Check stop flag at start of each iteration
                if self._stop_requested:
                    logger.info("Main loop: stop requested, exiting")
                    break

                # Check if paused - skip scanning but continue loop for position monitoring
                if self.paused:
                    await asyncio.sleep(1)
                    continue

                # Check if we need to scan (not at max positions)
                max_positions = self.strategy.config.max_open_positions
                current_positions = len(self._active_positions)

                if current_positions >= max_positions:
                    # Max positions reached - check for closed positions and wait
                    await self._check_closed_positions()
                    await asyncio.sleep(3)  # Check every 3 seconds if position closed
                    continue

                scan_count += 1

                # Check safety limits
                if not self._check_safety_limits():
                    logger.warning("Safety limits triggered, pausing trading")
                    await asyncio.sleep(60)
                    continue

                # OPTIMIZED FAST SCAN:
                # Step 1: Get ALL tickers in ONE request
                all_tickers = self.client.get_all_tickers(use_cache=False)

                # Step 2: Pre-filter symbols by ticker data (price/volume/change)
                # This avoids expensive kline requests for symbols that won't pass filters
                filtered_symbols = await self._fast_filter_by_tickers(
                    self.strategy.trading_pairs,
                    all_tickers
                )

                pairs_count = len(self.strategy.trading_pairs)
                filtered_count = len(filtered_symbols)

                # Log scan start
                try:
                    from ..api.main import get_log_message
                    scan_msg = get_log_message("scanning_pairs", count=pairs_count)
                except Exception:
                    scan_msg = f"Scanning {pairs_count} pairs..."
                logger.info(
                    f"{scan_msg} (filtered to {filtered_count})",
                    scan=scan_count,
                    pairs=pairs_count,
                    filtered=filtered_count,
                    positions=current_positions
                )

                # Step 3: Process only filtered symbols with parallel kline requests
                signals_found = await self._process_symbols_parallel(
                    filtered_symbols,
                    max_positions - current_positions
                )

                # Log scan complete
                try:
                    from ..api.main import get_log_message
                    complete_msg = get_log_message("scan_complete", num=scan_count, signals=signals_found, positions=len(self._active_positions))
                except Exception:
                    complete_msg = f"Scan #{scan_count} complete"
                logger.info(complete_msg, filtered=filtered_count, signals=signals_found)

                # Short delay between scans if still need positions
                if len(self._active_positions) < max_positions:
                    await asyncio.sleep(self.config.scan_interval)
                else:
                    await asyncio.sleep(self.config.candle_update_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in main loop", error=str(e))
                await self._notify_error(e)
                await asyncio.sleep(10)

    async def _fast_filter_by_tickers(
        self,
        symbols: list[str],
        all_tickers: dict
    ) -> list[str]:
        """
        Fast pre-filter symbols using ticker data WITHOUT kline requests.

        This is the key optimization: filter 458 symbols down to ~30-50
        using a single get_all_tickers() call instead of 458 individual requests.

        Args:
            symbols: List of symbols to filter
            all_tickers: Dictionary of all tickers from get_all_tickers()

        Returns:
            List of symbols that pass ticker-based filters
        """
        try:
            from ..api.main import runtime_settings
        except ImportError:
            return symbols  # Fallback: return all symbols

        # Get filter settings
        min_volume = runtime_settings.get("filter_min_volume", 0)
        max_volume = runtime_settings.get("filter_max_volume", 0)
        min_price = runtime_settings.get("filter_min_price", 0)
        max_price = runtime_settings.get("filter_max_price", 0)
        min_change = runtime_settings.get("filter_min_change", 0)
        max_change = runtime_settings.get("filter_max_change", 0)

        # If all basic filters are disabled, return all symbols
        if all(v == 0 for v in [min_volume, max_volume, min_price, max_price, min_change, max_change]):
            return symbols

        filtered = []
        for symbol in symbols:
            ticker = all_tickers.get(symbol)
            if not ticker:
                continue  # Skip symbols without ticker data

            price = float(ticker.last_price)
            volume = float(ticker.turnover_24h)
            change = float(ticker.change_24h)

            # Apply filters
            if min_volume > 0 and volume < min_volume:
                continue
            if max_volume > 0 and volume > max_volume:
                continue
            if min_price > 0 and price < min_price:
                continue
            if max_price > 0 and price > max_price:
                continue
            if min_change != 0 and change < min_change:
                continue
            if max_change != 0 and change > max_change:
                continue

            filtered.append(symbol)

        logger.debug(f"Fast filter: {len(symbols)} -> {len(filtered)} symbols")
        return filtered

    async def _process_symbols_parallel(
        self,
        symbols: list[str],
        slots_available: int
    ) -> int:
        """
        Process symbols with parallel kline requests for maximum speed.

        Uses batched parallel requests to stay within Bybit rate limits
        (600 req/5s = 120 req/s). Default: 10 parallel requests.

        Args:
            symbols: Pre-filtered list of symbols to process
            slots_available: Number of position slots available

        Returns:
            Number of signals found
        """
        if not symbols or slots_available <= 0:
            return 0

        signals_found = 0
        batch_size = self.config.max_parallel_kline_requests
        max_positions = self.strategy.config.max_open_positions

        # Process in batches
        for i in range(0, len(symbols), batch_size):
            # Check if we've filled all slots
            if len(self._active_positions) >= max_positions:
                logger.info("Max positions reached during parallel scan, stopping")
                break

            batch = symbols[i:i + batch_size]

            # Process batch in parallel
            tasks = [self._process_symbol(symbol) for symbol in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Count signals found
            for result in results:
                if result is True:
                    signals_found += 1

            # Small delay between batches to respect rate limits
            if i + batch_size < len(symbols):
                await asyncio.sleep(self.config.kline_request_delay)

        return signals_found

    async def _process_symbol(self, symbol: str) -> bool:
        """
        Process a single trading symbol.

        Args:
            symbol: Trading pair symbol

        Returns:
            True if a signal was found
        """
        try:
            # Get candle data
            df = self.client.get_klines(
                symbol=symbol,
                interval=self._timeframe_to_interval(self.strategy.timeframe),
                limit=250,  # Enough for EMA200 + buffer
            )

            if df.empty or len(df) < self.strategy.min_candles_required:
                logger.warning("Insufficient candle data", symbol=symbol, count=len(df))
                return False

            # Generate signal
            signal = self.strategy.process(df, symbol)

            # Process signal
            if signal.signal_type != SignalType.NO_SIGNAL:
                await self._process_signal(signal, df)
                self.stats.signals_processed += 1
                return True

            # Check for exit signals on existing positions
            if symbol in self._active_positions:
                await self._check_position_exit(symbol, df)

            return False

        except Exception as e:
            logger.error("Error processing symbol", symbol=symbol, error=str(e))
            return False

    async def _process_signal(self, signal: Signal, df: pd.DataFrame) -> None:
        """
        Process a trading signal.

        Args:
            signal: Trading signal
            df: DataFrame with candle data
        """
        symbol = signal.symbol

        # Check if we already have a position in this symbol
        if symbol in self._active_positions:
            existing = self._active_positions[symbol]
            if existing["side"] == ("long" if signal.is_long else "short"):
                return  # Already in same direction

        # Notify callbacks
        await self._notify_signal(signal)

        # Check auto-trade filters (for auto_search mode)
        filter_passed, filter_reason = await self._check_auto_trade_filters(signal.symbol, df)
        if not filter_passed:
            logger.info("Signal filtered out", symbol=symbol, reason=filter_reason)
            return

        # Check risk limits
        existing_positions = len(self._active_positions)
        if existing_positions >= self.strategy.config.max_open_positions:
            logger.info("Max positions reached", current=existing_positions)
            return

        # Calculate position size
        balance = self.client.get_balance("USDT")
        size_result = self.position_sizer.calculate(
            balance=balance.available,
            entry_price=Decimal(str(signal.price)),
            stop_loss_price=Decimal(str(signal.stop_loss)) if signal.stop_loss else Decimal(str(signal.price * 0.98)),
            leverage=self.client.config.default_leverage if hasattr(self.client.config, 'default_leverage') else 1,
            existing_positions=existing_positions,
            signal_strength_multiplier=signal.position_size_multiplier,
        )

        if size_result.quantity <= 0:
            logger.warning("Position size too small", symbol=symbol)
            return

        # Execute trade
        if not self.config.paper_trading:
            order = await self._execute_entry(signal, size_result.quantity)
            if order:
                self._active_positions[symbol] = {
                    "side": "long" if signal.is_long else "short",
                    "entry_price": signal.price,
                    "quantity": float(size_result.quantity),
                    "initial_quantity": float(size_result.quantity),
                    "stop_loss": signal.stop_loss,
                    "original_sl": signal.stop_loss,  # Store original SL for TP2 calculation
                    "take_profit": signal.take_profit,
                    "entry_time": datetime.utcnow(),
                    "order_id": order.order_id,
                    "partial_tp_executed": False,
                }
                self.stats.trades_executed += 1
                # Log trade open with color marker
                side_str = "LONG" if signal.is_long else "SHORT"
                self._log_trade_open(symbol, side_str, size_result.quantity, signal.price, signal.stop_loss, signal.take_profit)
                await self._notify_trade("entry", signal, order)
        else:
            # Paper trading - just log
            logger.info(
                "Paper trade signal",
                symbol=symbol,
                side="long" if signal.is_long else "short",
                quantity=str(size_result.quantity),
                entry=signal.price,
                sl=signal.stop_loss,
                tp=signal.take_profit,
            )

    async def _check_auto_trade_filters(self, symbol: str, df: pd.DataFrame) -> tuple[bool, str]:
        """
        Check if the symbol passes all auto-trade filters.

        Args:
            symbol: Trading pair symbol
            df: DataFrame with candle data

        Returns:
            Tuple of (passed, reason) where passed is True if all filters pass
        """
        try:
            # Import runtime_settings here to avoid circular imports
            from ..api.main import runtime_settings

            # Get filter settings
            min_volume = runtime_settings.get("filter_min_volume", 0)
            max_volume = runtime_settings.get("filter_max_volume", 0)
            min_price = runtime_settings.get("filter_min_price", 0)
            max_price = runtime_settings.get("filter_max_price", 0)
            min_change = runtime_settings.get("filter_min_change", 0)
            max_change = runtime_settings.get("filter_max_change", 0)
            volatility_period = runtime_settings.get("filter_volatility_period", 0)
            min_volatility = runtime_settings.get("filter_min_volatility", 0)
            max_volatility = runtime_settings.get("filter_max_volatility", 0)

            # Log filter settings for debugging
            logger.debug(
                "Filter settings",
                symbol=symbol,
                min_volume=min_volume,
                max_volume=max_volume,
                min_price=min_price,
                max_price=max_price,
            )

            # If all filters are disabled (0), pass through
            if all(v == 0 for v in [min_volume, max_volume, min_price, max_price,
                                      min_change, max_change, volatility_period]):
                logger.debug("All filters disabled, passing through", symbol=symbol)
                return True, ""

            # Get ticker data for volume and price change
            ticker = self.client.get_ticker(symbol)
            if not ticker:
                logger.warning("No ticker data, passing through", symbol=symbol)
                return True, ""  # No ticker data, pass through

            current_price = float(ticker.last_price)
            volume_24h = float(ticker.turnover_24h)  # Volume in USDT
            change_24h = float(ticker.change_24h)  # Percentage change

            # Log ticker data for debugging
            logger.info(
                "Filter check",
                symbol=symbol,
                volume_24h=f"{volume_24h:.0f}",
                min_volume=min_volume,
                price=f"{current_price:.6f}",
                change=f"{change_24h:.2f}%",
            )

            # Check volume filter
            if min_volume > 0 and volume_24h < min_volume:
                logger.info("FILTERED by volume", symbol=symbol, volume=f"{volume_24h:.0f}", min_volume=min_volume)
                return False, f"Volume {volume_24h:.0f} < min {min_volume:.0f}"
            if max_volume > 0 and volume_24h > max_volume:
                logger.info("FILTERED by max volume", symbol=symbol, volume=f"{volume_24h:.0f}", max_volume=max_volume)
                return False, f"Volume {volume_24h:.0f} > max {max_volume:.0f}"

            # Check price filter
            if min_price > 0 and current_price < min_price:
                return False, f"Price {current_price:.4f} < min {min_price:.4f}"
            if max_price > 0 and current_price > max_price:
                return False, f"Price {current_price:.4f} > max {max_price:.4f}"

            # Check price change filter
            if min_change != 0 and change_24h < min_change:
                return False, f"Change {change_24h:.2f}% < min {min_change:.2f}%"
            if max_change != 0 and change_24h > max_change:
                return False, f"Change {change_24h:.2f}% > max {max_change:.2f}%"

            # Check volatility filter
            if volatility_period > 0 and (min_volatility > 0 or max_volatility > 0):
                # Calculate volatility as percentage range over period
                period_data = df.tail(volatility_period)
                if len(period_data) >= volatility_period:
                    high = period_data["high"].max()
                    low = period_data["low"].min()
                    avg_price = (high + low) / 2
                    volatility = ((high - low) / avg_price) * 100 if avg_price > 0 else 0

                    if min_volatility > 0 and volatility < min_volatility:
                        return False, f"Volatility {volatility:.2f}% < min {min_volatility:.2f}%"
                    if max_volatility > 0 and volatility > max_volatility:
                        return False, f"Volatility {volatility:.2f}% > max {max_volatility:.2f}%"

            return True, ""

        except Exception as e:
            logger.warning("Error checking auto-trade filters", symbol=symbol, error=str(e))
            return True, ""  # On error, pass through

    async def _execute_entry(self, signal: Signal, quantity: Decimal) -> Optional[Any]:
        """Execute entry order."""
        try:
            if isinstance(self.trader, FuturesTrader):
                # Set margin mode before opening position
                margin_mode = MarginMode(self.config.margin_mode)
                self.trader.set_margin_mode(signal.symbol, margin_mode)

                if signal.is_long:
                    order = self.trader.open_long(
                        symbol=signal.symbol,
                        quantity=quantity,
                        stop_loss=Decimal(str(signal.stop_loss)) if signal.stop_loss else None,
                        take_profit=Decimal(str(signal.take_profit)) if signal.take_profit else None,
                    )
                else:
                    order = self.trader.open_short(
                        symbol=signal.symbol,
                        quantity=quantity,
                        stop_loss=Decimal(str(signal.stop_loss)) if signal.stop_loss else None,
                        take_profit=Decimal(str(signal.take_profit)) if signal.take_profit else None,
                    )
            else:
                # Spot trading
                if signal.is_long:
                    order = self.trader.buy(
                        symbol=signal.symbol,
                        quantity=quantity,
                    )
                else:
                    order = self.trader.sell(
                        symbol=signal.symbol,
                        quantity=quantity,
                    )

            return order

        except Exception as e:
            logger.error("Failed to execute entry", symbol=signal.symbol, error=str(e))
            return None

    async def _check_position_exit(self, symbol: str, df: pd.DataFrame) -> None:
        """Check if position should be exited."""
        position_data = self._active_positions.get(symbol)
        if not position_data:
            return

        should_close, reason = self.strategy.should_close_position(
            position_side=position_data["side"],
            df=df,
        )

        if should_close:
            await self._close_position(symbol, reason)

    async def _close_position(self, symbol: str, reason: str = "signal") -> None:
        """Close a position."""
        position_data = self._active_positions.get(symbol)
        if not position_data:
            return

        try:
            if not self.config.paper_trading:
                if isinstance(self.trader, FuturesTrader):
                    order = self.trader.close_position(symbol)
                else:
                    order = self.trader.sell_all(symbol)

                if order:
                    # Calculate PnL
                    entry_price = position_data["entry_price"]
                    exit_price = order.average_price or order.price
                    quantity = position_data.get("quantity", 0)

                    if position_data["side"] == "long":
                        pnl = (float(exit_price) - entry_price) / entry_price * 100
                    else:
                        pnl = (entry_price - float(exit_price)) / entry_price * 100

                    # Calculate PnL in USDT
                    pnl_usdt = (pnl / 100) * float(quantity) * entry_price

                    if pnl > 0:
                        self.stats.winning_trades += 1
                        self.stats.consecutive_losses = 0
                    else:
                        self.stats.losing_trades += 1
                        self.stats.consecutive_losses += 1

                    self.stats.total_pnl += Decimal(str(pnl))
                    self.stats.daily_pnl += Decimal(str(pnl_usdt))  # USDT for daily PnL
                    self.stats.last_trade_time = datetime.utcnow()

                    # Log trade close with color marker
                    self._log_trade_close(symbol, pnl, pnl_usdt)

                    await self._notify_trade("exit", None, order, pnl=pnl, reason=reason)

            del self._active_positions[symbol]
            logger.info(
                "Position closed",
                symbol=symbol,
                reason=reason,
            )

        except Exception as e:
            logger.error("Failed to close position", symbol=symbol, error=str(e))

    async def _sync_positions_from_api(self) -> None:
        """
        Sync positions from Bybit API at startup.

        Loads all existing open positions into local tracking.
        """
        try:
            api_positions = self.client.get_positions(use_cache=False)

            for pos in api_positions:
                if float(pos.size) > 0:
                    pos_data = {
                        "symbol": pos.symbol,
                        "side": "long" if pos.side.value == "Buy" else "short",
                        "size": float(pos.size),
                        "entry_price": float(pos.entry_price),
                        "quantity": float(pos.size),
                        "leverage": pos.leverage,
                        "unrealized_pnl": float(pos.unrealized_pnl),
                        "stop_loss": float(pos.stop_loss) if pos.stop_loss else None,
                        "take_profit": float(pos.take_profit) if pos.take_profit else None,
                    }
                    self._active_positions[pos.symbol] = pos_data
                    self._last_api_positions[pos.symbol] = pos_data

            if self._active_positions:
                logger.info(f"Synced {len(self._active_positions)} existing positions from API")

        except Exception as e:
            logger.warning(f"Failed to sync positions from API: {e}")

    async def _close_all_positions(self) -> None:
        """Close all open positions."""
        for symbol in list(self._active_positions.keys()):
            await self._close_position(symbol, reason="engine_stop")

    async def _check_closed_positions(self) -> None:
        """
        API-centric position tracking.

        Compares current API positions with last snapshot to detect closed positions.
        All data (PnL, prices, etc.) comes directly from Bybit API.
        """
        try:
            # Get ALL current positions from Bybit API
            api_positions = self.client.get_positions(use_cache=False)

            # Build current positions map: symbol -> position data
            current_positions: dict[str, dict] = {}
            for pos in api_positions:
                if float(pos.size) > 0:
                    current_positions[pos.symbol] = {
                        "symbol": pos.symbol,
                        "side": "long" if pos.side.value == "Buy" else "short",
                        "size": float(pos.size),
                        "entry_price": float(pos.entry_price),
                        "leverage": pos.leverage,
                        "unrealized_pnl": float(pos.unrealized_pnl),
                        "stop_loss": float(pos.stop_loss) if pos.stop_loss else None,
                        "take_profit": float(pos.take_profit) if pos.take_profit else None,
                    }

            # Find positions that were closed (in last snapshot but not in current)
            closed_symbols = set(self._last_api_positions.keys()) - set(current_positions.keys())

            # Also check _active_positions for any that are no longer open
            for symbol in list(self._active_positions.keys()):
                if symbol not in current_positions:
                    closed_symbols.add(symbol)

            # Process each closed position
            for symbol in closed_symbols:
                # Get closed PnL data directly from Bybit API
                try:
                    closed_pnl = self.client.get_symbol_closed_pnl(symbol, limit=5)

                    if closed_pnl:
                        # All data from Bybit API
                        pnl_usdt = float(closed_pnl.get("closedPnl", 0))
                        avg_entry = float(closed_pnl.get("avgEntryPrice", 0))
                        avg_exit = float(closed_pnl.get("avgExitPrice", 0))
                        closed_size = float(closed_pnl.get("closedSize", 0))
                        cum_entry_value = float(closed_pnl.get("cumEntryValue", 0))
                        leverage = float(closed_pnl.get("leverage", 1))
                        side = closed_pnl.get("side", "")
                        exec_type = closed_pnl.get("execType", "")
                        order_id = closed_pnl.get("orderId", "")
                        created_time = closed_pnl.get("createdTime", "")

                        # Calculate PnL % from API data
                        pnl_percent = 0.0
                        if cum_entry_value > 0 and leverage > 0:
                            margin_used = cum_entry_value / leverage
                            pnl_percent = (pnl_usdt / margin_used) * 100

                        # Determine close reason from execType
                        close_reason = "SL/TP"
                        if "StopLoss" in exec_type or "Stop" in exec_type:
                            close_reason = "STOP LOSS"
                        elif "TakeProfit" in exec_type or "Profit" in exec_type:
                            close_reason = "TAKE PROFIT"
                        elif "Trade" in exec_type:
                            close_reason = "MARKET"

                        # Log with all API data in clean format
                        pnl_emoji = "💰" if pnl_usdt >= 0 else "📉"
                        pnl_sign = "+" if pnl_usdt >= 0 else ""
                        logger.info(
                            f"━━━ CLOSED: {close_reason} ━━━ {symbol} {pnl_emoji}"
                        )
                        logger.info(
                            f"Side: {side} | Entry: {avg_entry:.6f} → Exit: {avg_exit:.6f}"
                        )
                        logger.info(
                            f"PnL: {pnl_sign}{pnl_percent:.2f}% ({pnl_sign}{pnl_usdt:.4f} USDT) | Size: {closed_size:.4f} | Leverage: {leverage:.0f}x"
                        )

                        # Update stats from API data
                        if pnl_usdt > 0:
                            self.stats.winning_trades += 1
                            self.stats.consecutive_losses = 0
                        elif pnl_usdt < 0:
                            self.stats.losing_trades += 1
                            self.stats.consecutive_losses += 1

                        self.stats.total_pnl += Decimal(str(pnl_percent))
                        self.stats.daily_pnl += Decimal(str(pnl_usdt))
                        self.stats.last_trade_time = datetime.utcnow()
                        self.stats.trades_executed += 1

                        # Log formatted message for web UI
                        self._log_trade_close(symbol, pnl_percent, pnl_usdt)
                    else:
                        # No PnL from API, try to use local data
                        local_pos = self._active_positions.get(symbol) or self._last_api_positions.get(symbol)
                        if local_pos:
                            entry_price = local_pos.get("entry_price", 0)
                            side = local_pos.get("side", "unknown")
                            sl = local_pos.get("stop_loss", 0)
                            tp = local_pos.get("take_profit", 0)
                            logger.info(
                                f"━━━ POSITION CLOSED ━━━ {symbol}",
                            )
                            logger.info(
                                f"Side: {side} | Entry: {entry_price:.6f} | SL: {sl:.6f} | TP: {tp:.6f}"
                            )
                            logger.info(
                                f"(PnL data not available from API - check Bybit for details)"
                            )
                        else:
                            logger.info(f"Position closed: {symbol} (no data available)")

                except Exception as e:
                    logger.warning(f"Failed to get closed PnL for {symbol}: {e}")

                # Remove from local tracking
                self._active_positions.pop(symbol, None)

            # Update last API positions snapshot
            self._last_api_positions = current_positions

            # Sync _active_positions with API (API is source of truth)
            for symbol, pos_data in current_positions.items():
                if symbol not in self._active_positions:
                    # Position exists on API but not locally - sync it
                    self._active_positions[symbol] = pos_data

        except Exception as e:
            logger.debug(f"Error in API position check: {e}")

    async def _position_monitoring_loop(self) -> None:
        """Monitor positions for SL/TP updates based on SuperTrend line."""
        # Track last candle close time for each symbol
        last_candle_time: dict[str, datetime] = {}

        while self.state == EngineState.RUNNING and not self._stop_requested:
            try:
                # Check stop flag at start of each iteration
                if self._stop_requested:
                    logger.info("Position monitoring: stop requested, exiting")
                    break

                # When paused, only check for closed positions but don't execute any orders
                if self.paused:
                    await self._check_closed_positions()
                    await asyncio.sleep(3)
                    continue

                # Check for positions closed by SL/TP on exchange
                await self._check_closed_positions()

                for symbol, position_data in list(self._active_positions.items()):
                    # Check stop flag inside loop for faster exit
                    if self._stop_requested:
                        logger.info("Position monitoring: stop requested in loop, breaking")
                        break

                    try:
                        # Get candle data to check for new closed candle
                        df = self.client.get_klines(
                            symbol=symbol,
                            interval=self._timeframe_to_interval(self.strategy.timeframe),
                            limit=250,
                        )

                        if df.empty or len(df) < 50:
                            continue

                        # Get the last closed candle time (second to last row, as last is still forming)
                        current_candle_time = df.index[-2] if len(df) > 1 else df.index[-1]

                        # Check if a new candle has closed
                        prev_candle_time = last_candle_time.get(symbol)
                        if prev_candle_time is not None and current_candle_time <= prev_candle_time:
                            # No new candle closed, skip
                            continue

                        # Update last candle time
                        last_candle_time[symbol] = current_candle_time

                        current_sl = position_data.get("stop_loss") or 0
                        side = position_data["side"]
                        entry_price = position_data.get("entry_price") or 0
                        take_profit = position_data.get("take_profit") or 0
                        current_price = float(df["close"].iloc[-2])  # Last closed candle price

                        # === PARTIAL TP LOGIC ===
                        partial_tp_enabled = getattr(self.strategy.config, "partial_tp_enabled", True)
                        partial_tp_executed = position_data.get("partial_tp_executed", False)

                        if partial_tp_enabled and not partial_tp_executed and take_profit > 0:
                            # Calculate TP1 (1:1 R:R) - halfway to full TP
                            # Risk = distance from entry to SL
                            # TP1 = entry + risk (for long) or entry - risk (for short)
                            if side == "long":
                                risk = entry_price - current_sl
                                tp1 = entry_price + risk  # 1:1 R:R
                            else:
                                risk = current_sl - entry_price
                                tp1 = entry_price - risk  # 1:1 R:R

                            # Check if price reached TP1
                            tp1_reached = False
                            if side == "long" and current_price >= tp1:
                                tp1_reached = True
                            elif side == "short" and current_price <= tp1:
                                tp1_reached = True

                            if tp1_reached:
                                close_percent = getattr(self.strategy.config, "partial_tp_close_percent", 50)
                                sl_move_mode = getattr(self.strategy.config, "partial_tp_sl_move", "tp1")
                                sl_offset_pct = getattr(self.strategy.config, "partial_tp_sl_offset", 0.2)

                                # Calculate partial close quantity
                                current_qty = position_data.get("quantity", 0)
                                close_qty = current_qty * (close_percent / 100)
                                remaining_qty = current_qty - close_qty

                                # Calculate new SL based on sl_move mode
                                if sl_move_mode == "entry":
                                    # Move SL to entry price with offset (lock small profit)
                                    if side == "long":
                                        new_sl_at_tp = entry_price * (1 + sl_offset_pct / 100)
                                    else:
                                        new_sl_at_tp = entry_price * (1 - sl_offset_pct / 100)
                                else:
                                    # Move SL to TP1 with offset (default)
                                    if side == "long":
                                        new_sl_at_tp = tp1 * (1 - sl_offset_pct / 100)
                                    else:
                                        new_sl_at_tp = tp1 * (1 + sl_offset_pct / 100)

                                logger.info(
                                    f"Partial TP triggered at TP1 (1:1 R:R)",
                                    symbol=symbol,
                                    side=side,
                                    entry=entry_price,
                                    tp1=tp1,
                                    current_price=current_price,
                                    close_percent=close_percent,
                                    sl_move=sl_move_mode,
                                    close_qty=close_qty,
                                    remaining_qty=remaining_qty,
                                    new_sl=new_sl_at_tp,
                                )

                                # Execute partial close on exchange
                                if not self.config.paper_trading:
                                    if isinstance(self.trader, FuturesTrader):
                                        try:
                                            # Close partial position using close_position with quantity
                                            pos_side = PositionSide.LONG if side == "long" else PositionSide.SHORT
                                            self.trader.client.close_position(
                                                symbol=symbol,
                                                side=pos_side,
                                                quantity=Decimal(str(round(close_qty, 8))),
                                            )
                                            # Update SL on exchange
                                            self.trader.update_stop_loss(symbol, Decimal(str(new_sl_at_tp)))
                                            logger.info(
                                                "Partial TP executed on exchange",
                                                symbol=symbol,
                                                closed_qty=close_qty,
                                            )
                                        except Exception as e:
                                            logger.error("Failed to execute partial TP", error=str(e))

                                # Update local position data
                                position_data["quantity"] = remaining_qty
                                position_data["stop_loss"] = new_sl_at_tp
                                position_data["partial_tp_executed"] = True
                                current_sl = new_sl_at_tp  # Update for trailing logic

                        # === TRAILING TP LOGIC === For remaining position after partial TP
                        trailing_tp_enabled = getattr(self.strategy.config, "trailing_tp_enabled", False)
                        partial_tp_executed = position_data.get("partial_tp_executed", False)

                        if trailing_tp_enabled and partial_tp_executed:
                            trailing_tp_mode = getattr(self.strategy.config, "trailing_tp_mode", "st_line")

                            if trailing_tp_mode == "st_line":
                                # ST Line TP mode: Exit when price crosses SuperTrend line (reversal signal)
                                indicators = self.strategy.calculate_indicators(df)
                                triple_st = indicators.get("triple_supertrend")

                                if triple_st:
                                    # Use trailing_tp_st_line setting
                                    trailing_tp_st_line = getattr(self.strategy.config, "trailing_tp_st_line", 2)

                                    dir_map = {
                                        1: triple_st.st1.direction,
                                        2: triple_st.st2.direction,
                                        3: triple_st.st3.direction,
                                    }
                                    st_direction = dir_map.get(trailing_tp_st_line, triple_st.st2.direction)

                                    # Get current and previous direction
                                    curr_dir = int(st_direction.iloc[-2])  # Last closed candle
                                    prev_dir = int(st_direction.iloc[-3]) if len(st_direction) > 2 else curr_dir

                                    # Check for reversal signal
                                    should_exit = False
                                    if side == "long" and prev_dir == 1 and curr_dir == -1:
                                        # Long position: ST line turned bearish (reversal)
                                        should_exit = True
                                        exit_reason = "ST Line reversed to bearish"
                                    elif side == "short" and prev_dir == -1 and curr_dir == 1:
                                        # Short position: ST line turned bullish (reversal)
                                        should_exit = True
                                        exit_reason = "ST Line reversed to bullish"

                                    if should_exit:
                                        remaining_qty = position_data.get("quantity", 0)
                                        logger.info(
                                            f"Trailing TP (ST Line) - closing remaining position",
                                            symbol=symbol,
                                            side=side,
                                            reason=exit_reason,
                                            quantity=remaining_qty,
                                        )

                                        # Close remaining position on exchange
                                        if not self.config.paper_trading:
                                            if isinstance(self.trader, FuturesTrader):
                                                try:
                                                    pos_side = PositionSide.LONG if side == "long" else PositionSide.SHORT
                                                    self.trader.client.close_position(
                                                        symbol=symbol,
                                                        side=pos_side,
                                                        quantity=Decimal(str(round(remaining_qty, 8))),
                                                    )
                                                    logger.info(
                                                        "Trailing TP (ST Line) executed on exchange",
                                                        symbol=symbol,
                                                        closed_qty=remaining_qty,
                                                    )
                                                except Exception as e:
                                                    logger.error("Failed to execute Trailing TP (ST Line)", error=str(e))

                                        # Remove position from tracking
                                        del self._active_positions[symbol]
                                        continue

                            elif trailing_tp_mode == "trailing_percent":
                                # Trailing % TP mode: Move TP when price approaches within activation %
                                trailing_tp_activation = getattr(self.strategy.config, "trailing_tp_activation", 0.5)
                                trailing_tp_step = getattr(self.strategy.config, "trailing_tp_step", 1.0)

                                current_tp = position_data.get("take_profit", 0)
                                if current_tp > 0 and entry_price > 0:
                                    # Calculate distance to TP as percentage
                                    if side == "long":
                                        distance_to_tp_pct = ((current_tp - current_price) / current_price) * 100
                                    else:
                                        distance_to_tp_pct = ((current_price - current_tp) / current_price) * 100

                                    # Check if price is within activation distance
                                    if distance_to_tp_pct <= trailing_tp_activation and distance_to_tp_pct > 0:
                                        # Move TP further by step %
                                        if side == "long":
                                            new_tp = current_tp * (1 + trailing_tp_step / 100)
                                        else:
                                            new_tp = current_tp * (1 - trailing_tp_step / 100)

                                        logger.info(
                                            f"Trailing TP (%) - moving TP further",
                                            symbol=symbol,
                                            side=side,
                                            old_tp=current_tp,
                                            new_tp=new_tp,
                                            distance_pct=f"{distance_to_tp_pct:.2f}%",
                                        )

                                        # Update TP on exchange
                                        if not self.config.paper_trading:
                                            if isinstance(self.trader, FuturesTrader):
                                                try:
                                                    self.trader.update_take_profit(symbol, Decimal(str(new_tp)))
                                                except Exception as e:
                                                    logger.error("Failed to update TP on exchange", error=str(e))

                                        # Update local position data
                                        position_data["take_profit"] = new_tp

                        # Skip trailing if disabled
                        if not self.strategy.config.trailing_enabled:
                            continue

                        trailing_mode = getattr(self.strategy.config, "trailing_mode", "fix_percent")
                        new_sl_price = None
                        should_update = False

                        if trailing_mode == "st_line":
                            # ST Line trailing mode - follow SuperTrend line with confirmation
                            indicators = self.strategy.calculate_indicators(df)
                            triple_st = indicators.get("triple_supertrend")

                            if not triple_st:
                                continue

                            # Use trailing_st_line setting (not sl_supertrend_line)
                            trailing_st_line = getattr(self.strategy.config, "trailing_st_line", 2)
                            confirm_candles = getattr(self.strategy.config, "trailing_confirm_candles", 1)

                            line_map = {
                                1: triple_st.st1.supertrend,
                                2: triple_st.st2.supertrend,
                                3: triple_st.st3.supertrend,
                            }
                            st_line = line_map.get(trailing_st_line, triple_st.st2.supertrend)

                            # Get ST values for confirmation (last N closed candles)
                            # iloc[-2] is last closed, iloc[-3] is second to last closed, etc.
                            confirmed = True
                            candidate_sl = float(st_line.iloc[-2])

                            for i in range(confirm_candles):
                                idx = -2 - i  # -2, -3, -4, etc.
                                if abs(idx) > len(st_line):
                                    confirmed = False
                                    break
                                candle_st = float(st_line.iloc[idx])
                                # All confirmation candles must agree on direction
                                if side == "long":
                                    if candle_st < candidate_sl:
                                        candidate_sl = candle_st  # Use the lowest (most conservative)
                                else:
                                    if candle_st > candidate_sl:
                                        candidate_sl = candle_st  # Use the highest (most conservative)

                            if confirmed:
                                new_sl_price = candidate_sl
                                # Only move SL in profit direction
                                if side == "long":
                                    if new_sl_price > current_sl:
                                        should_update = True
                                else:
                                    if new_sl_price < current_sl or current_sl == 0:
                                        should_update = True

                        else:
                            # Fix % trailing mode - activate after profit threshold
                            if entry_price <= 0:
                                continue

                            activation_pct = self.strategy.config.trailing_activation
                            step_pct = self.strategy.config.trailing_step

                            # Calculate current profit %
                            if side == "long":
                                profit_pct = ((current_price - entry_price) / entry_price) * 100
                            else:
                                profit_pct = ((entry_price - current_price) / entry_price) * 100

                            # Check if trailing should activate
                            if profit_pct >= activation_pct:
                                # Calculate new trailing SL
                                if side == "long":
                                    new_sl_price = current_price * (1 - step_pct / 100)
                                    if new_sl_price > current_sl:
                                        should_update = True
                                else:
                                    new_sl_price = current_price * (1 + step_pct / 100)
                                    if new_sl_price < current_sl or current_sl == 0:
                                        should_update = True

                        if should_update and new_sl_price is not None:
                            # Validate SL is on correct side of current price
                            # For LONG: SL must be BELOW current price
                            # For SHORT: SL must be ABOVE current price
                            sl_valid = False
                            if side == "long" and new_sl_price < current_price:
                                sl_valid = True
                            elif side == "short" and new_sl_price > current_price:
                                sl_valid = True

                            if not sl_valid:
                                logger.debug(
                                    "Trailing SL skip - SL would be on wrong side of price",
                                    symbol=symbol,
                                    side=side,
                                    new_sl=new_sl_price,
                                    current_price=current_price,
                                )
                            else:
                                logger.info(
                                    f"Trailing SL update ({trailing_mode})",
                                    symbol=symbol,
                                    side=side,
                                    old_sl=current_sl,
                                    new_sl=new_sl_price,
                                )

                                # Update on exchange if not paper trading
                                if not self.config.paper_trading:
                                    if isinstance(self.trader, FuturesTrader):
                                        try:
                                            self.trader.update_stop_loss(symbol, Decimal(str(new_sl_price)))
                                        except Exception as e:
                                            logger.error("Failed to update SL on exchange", error=str(e))

                                # Update local position data
                                position_data["stop_loss"] = new_sl_price

                    except Exception as e:
                        logger.error("Error updating position SL", symbol=symbol, error=str(e))

                await asyncio.sleep(self.config.position_check_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in position monitoring", error=str(e))
                await asyncio.sleep(5)

    def _check_safety_limits(self) -> bool:
        """Check if trading should continue based on safety limits."""
        # Check consecutive losses
        if self.stats.consecutive_losses >= self.config.max_consecutive_losses:
            if self.stats.last_trade_time:
                cooldown_end = self.stats.last_trade_time + timedelta(
                    minutes=self.config.cooldown_after_loss_streak
                )
                if datetime.utcnow() < cooldown_end:
                    return False
                else:
                    self.stats.consecutive_losses = 0

        # Check daily loss limit
        if float(self.stats.daily_pnl) <= -self.config.max_daily_loss_percent:
            return False

        return True

    def _timeframe_to_interval(self, timeframe: str) -> str:
        """Convert timeframe string to Bybit interval."""
        mapping = {
            "1m": "1",
            "3m": "3",
            "5m": "5",
            "15m": "15",
            "30m": "30",
            "1h": "60",
            "2h": "120",
            "4h": "240",
            "6h": "360",
            "12h": "720",
            "1d": "D",
            "1w": "W",
        }
        return mapping.get(timeframe, "60")

    # Callback management
    def on_signal(self, callback: Callable) -> None:
        """Register signal callback."""
        self._on_signal_callbacks.append(callback)

    def on_trade(self, callback: Callable) -> None:
        """Register trade callback."""
        self._on_trade_callbacks.append(callback)

    def on_error(self, callback: Callable) -> None:
        """Register error callback."""
        self._on_error_callbacks.append(callback)

    async def _notify_signal(self, signal: Signal) -> None:
        """Notify signal callbacks."""
        for callback in self._on_signal_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(signal)
                else:
                    callback(signal)
            except Exception as e:
                logger.error("Signal callback error", error=str(e))

    async def _notify_trade(self, trade_type: str, signal: Optional[Signal], order: Any, **kwargs) -> None:
        """Notify trade callbacks."""
        for callback in self._on_trade_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(trade_type, signal, order, **kwargs)
                else:
                    callback(trade_type, signal, order, **kwargs)
            except Exception as e:
                logger.error("Trade callback error", error=str(e))

    async def _notify_error(self, error: Exception) -> None:
        """Notify error callbacks."""
        for callback in self._on_error_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(error)
                else:
                    callback(error)
            except Exception as e:
                logger.error("Error callback error", error=str(e))

    def _log_trade_open(self, symbol: str, side: str, qty, entry: float, sl: float, tp: float) -> None:
        """Log trade open with color marker for web interface."""
        try:
            from ..api.main import runtime_settings, get_log_message
            lang = runtime_settings.get("language", "en")
            if lang == "ru":
                msg = f"[ОТКРЫТИЕ] Позиция открыта: {symbol} {side} кол-во={qty:.4f} вход={entry:.6f} SL={sl:.6f} TP={tp:.6f}"
            else:
                msg = f"[TRADE_OPEN] Position opened: {symbol} {side} qty={qty:.4f} entry={entry:.6f} SL={sl:.6f} TP={tp:.6f}"
            logger.info(msg)
        except Exception as e:
            logger.info(f"[TRADE_OPEN] {symbol} {side} qty={qty} entry={entry} SL={sl} TP={tp}")

    def _log_trade_close(self, symbol: str, pnl: float, pnl_usdt: float) -> None:
        """Log trade close with color marker for web interface."""
        try:
            from ..api.main import runtime_settings
            lang = runtime_settings.get("language", "en")
            if pnl > 0:
                if lang == "ru":
                    msg = f"[ПРИБЫЛЬ] Позиция закрыта: {symbol} PnL: +{pnl:.2f}% (+{pnl_usdt:.2f} USDT)"
                else:
                    msg = f"[TRADE_PROFIT] Position closed: {symbol} PnL: +{pnl:.2f}% (+{pnl_usdt:.2f} USDT)"
            else:
                if lang == "ru":
                    msg = f"[УБЫТОК] Позиция закрыта: {symbol} PnL: {pnl:.2f}% ({pnl_usdt:.2f} USDT)"
                else:
                    msg = f"[TRADE_LOSS] Position closed: {symbol} PnL: {pnl:.2f}% ({pnl_usdt:.2f} USDT)"
            logger.info(msg)
        except Exception as e:
            if pnl > 0:
                logger.info(f"[TRADE_PROFIT] {symbol} PnL: +{pnl:.2f}%")
            else:
                logger.info(f"[TRADE_LOSS] {symbol} PnL: {pnl:.2f}%")

    @property
    def is_running(self) -> bool:
        """Check if engine is running."""
        return self.state == EngineState.RUNNING

    def get_status(self) -> dict:
        """Get engine status."""
        return {
            "state": self.state.value,
            "stats": {
                "start_time": self.stats.start_time.isoformat() if self.stats.start_time else None,
                "signals_processed": self.stats.signals_processed,
                "trades_executed": self.stats.trades_executed,
                "winning_trades": self.stats.winning_trades,
                "losing_trades": self.stats.losing_trades,
                "win_rate": self.stats.win_rate,
                "total_pnl": str(self.stats.total_pnl),
                "daily_pnl": str(self.stats.daily_pnl),
            },
            "active_positions": len(self._active_positions),
            "trading_pairs": self.strategy.trading_pairs,
        }

    def __repr__(self) -> str:
        return f"TradingEngine(state={self.state.value}, positions={len(self._active_positions)})"
