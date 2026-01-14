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

from ..core.risk import PositionSizer, PositionSizingConfig, StopLossManager, TakeProfitManager
from ..core.strategy import Signal, SignalType, TripleSuperTrendStrategy
from ..exchange import BybitClient, FuturesTrader, SpotTrader
from ..exchange.models import AccountType, Position

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

    # Update intervals
    candle_update_interval: int = 60  # seconds
    position_check_interval: int = 10  # seconds

    # Trading settings
    auto_start: bool = False
    paper_trading: bool = False
    order_size: float = 100.0  # Fixed order size in USDT

    # Position sizing
    position_sizing_mode: str = "fixed_amount"  # fixed_amount | risk_percent | kelly
    risk_per_trade: float = 2.0  # % of balance to risk (for risk_percent mode)

    # Safety
    max_daily_loss_percent: float = 5.0
    max_weekly_loss_percent: float = 10.0
    max_drawdown_percent: float = 15.0
    min_balance_usdt: float = 100.0
    max_consecutive_losses: int = 3
    cooldown_after_loss_streak: int = 60  # minutes

    # Trailing stop settings
    trailing_enabled: bool = True
    trailing_mode: str = "supertrend"  # supertrend | percent
    trailing_activation: float = 1.0  # % profit to activate
    trailing_step: float = 0.5  # % trailing step

    # Break-even settings
    breakeven_enabled: bool = False
    breakeven_activation: float = 1.0  # % profit to move SL to entry
    breakeven_offset: float = 0.1  # % above entry for buffer

    # Leverage mode
    leverage_mode: str = "cross"  # cross | isolated

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

        # Risk management - use mode from config
        position_sizing_config = PositionSizingConfig(
            mode=self.config.position_sizing_mode,
            fixed_amount=Decimal(str(self.config.order_size)),
            risk_per_trade=self.config.risk_per_trade,
            max_open_positions=strategy.config.max_open_positions,
        )
        self.position_sizer = PositionSizer(position_sizing_config)
        self.stop_loss_manager = StopLossManager()
        self.take_profit_manager = TakeProfitManager()

        # Track highest/lowest prices for trailing stop
        self._highest_prices: dict[str, float] = {}
        self._lowest_prices: dict[str, float] = {}
        self._initial_balance: Optional[Decimal] = None
        self._week_start_balance: Optional[Decimal] = None
        self._week_start_time: Optional[datetime] = None

        # State
        self.state = EngineState.STOPPED
        self.stats = EngineStats()
        self._active_positions: dict[str, dict] = {}
        self._pending_signals: dict[str, Signal] = {}

        # Tasks
        self._main_task: Optional[asyncio.Task] = None
        self._position_task: Optional[asyncio.Task] = None

        # Callbacks
        self._on_signal_callbacks: list[Callable] = []
        self._on_trade_callbacks: list[Callable] = []
        self._on_error_callbacks: list[Callable] = []

    async def start(self) -> bool:
        """
        Start the trading engine.

        Returns:
            True if started successfully
        """
        if self.state != EngineState.STOPPED:
            logger.warning("Engine already running", state=self.state.value)
            return False

        self.state = EngineState.STARTING
        logger.info("Starting trading engine")

        try:
            # Validate connection
            if not self.client.is_connected:
                if not self.client.connect():
                    raise Exception("Failed to connect to exchange")

            # Initialize balances for safety checks
            try:
                balance = self.client.get_balance("USDT")
                self._initial_balance = balance.total
                self._week_start_balance = balance.total
                self._week_start_time = datetime.utcnow()
                logger.info("Initial balance recorded", balance=str(balance.total))
            except Exception as e:
                logger.warning("Could not get initial balance", error=str(e))
                self._initial_balance = None

            # Set margin mode if using futures
            if isinstance(self.trader, FuturesTrader) and hasattr(self.client, 'set_margin_mode'):
                try:
                    from ..exchange.models import MarginMode
                    margin_mode = MarginMode(self.config.leverage_mode)
                    # Note: margin mode is usually set per-symbol on first trade
                    logger.info("Leverage mode configured", mode=self.config.leverage_mode)
                except Exception as e:
                    logger.warning("Could not set margin mode", error=str(e))

            # Initialize state
            self.stats = EngineStats(start_time=datetime.utcnow())

            # Start main loop
            self._main_task = asyncio.create_task(self._main_loop())
            self._position_task = asyncio.create_task(self._position_monitoring_loop())

            self.state = EngineState.RUNNING
            logger.info("Trading engine started")
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

        self.state = EngineState.STOPPING
        logger.info("Stopping trading engine")

        # Close positions if requested
        if close_positions:
            await self._close_all_positions()

        # Cancel tasks
        if self._main_task:
            self._main_task.cancel()
            try:
                await self._main_task
            except asyncio.CancelledError:
                pass

        if self._position_task:
            self._position_task.cancel()
            try:
                await self._position_task
            except asyncio.CancelledError:
                pass

        self.state = EngineState.STOPPED
        logger.info("Trading engine stopped", stats=self.stats)

    async def _main_loop(self) -> None:
        """Main trading loop."""
        while self.state == EngineState.RUNNING:
            try:
                # Check safety limits
                if not self._check_safety_limits():
                    logger.warning("Safety limits triggered, pausing trading")
                    await asyncio.sleep(60)
                    continue

                # Process each trading pair
                for symbol in self.strategy.trading_pairs:
                    await self._process_symbol(symbol)

                # Wait for next iteration
                await asyncio.sleep(self.config.candle_update_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in main loop", error=str(e))
                await self._notify_error(e)
                await asyncio.sleep(10)

    async def _process_symbol(self, symbol: str) -> None:
        """
        Process a single trading symbol.

        Args:
            symbol: Trading pair symbol
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
                return

            # Generate signal
            signal = self.strategy.process(df, symbol)

            # Process signal
            if signal.signal_type != SignalType.NO_SIGNAL:
                await self._process_signal(signal, df)
                self.stats.signals_processed += 1

            # Check for exit signals on existing positions
            if symbol in self._active_positions:
                await self._check_position_exit(symbol, df)

        except Exception as e:
            logger.error("Error processing symbol", symbol=symbol, error=str(e))

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
                    "stop_loss": signal.stop_loss,
                    "take_profit": signal.take_profit,
                    "entry_time": datetime.utcnow(),
                    "order_id": order.order_id,
                }
                self.stats.trades_executed += 1
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

    async def _execute_entry(self, signal: Signal, quantity: Decimal) -> Optional[Any]:
        """Execute entry order."""
        try:
            if isinstance(self.trader, FuturesTrader):
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

                    if position_data["side"] == "long":
                        pnl = (float(exit_price) - entry_price) / entry_price * 100
                    else:
                        pnl = (entry_price - float(exit_price)) / entry_price * 100

                    if pnl > 0:
                        self.stats.winning_trades += 1
                        self.stats.consecutive_losses = 0
                    else:
                        self.stats.losing_trades += 1
                        self.stats.consecutive_losses += 1

                    self.stats.total_pnl += Decimal(str(pnl))
                    self.stats.daily_pnl += Decimal(str(pnl))
                    self.stats.last_trade_time = datetime.utcnow()

                    await self._notify_trade("exit", None, order, pnl=pnl, reason=reason)

            del self._active_positions[symbol]
            logger.info(
                "Position closed",
                symbol=symbol,
                reason=reason,
            )

        except Exception as e:
            logger.error("Failed to close position", symbol=symbol, error=str(e))

    async def _close_all_positions(self) -> None:
        """Close all open positions."""
        for symbol in list(self._active_positions.keys()):
            await self._close_position(symbol, reason="engine_stop")

    async def _position_monitoring_loop(self) -> None:
        """Monitor positions for SL/TP updates based on trailing mode settings."""
        # Track last candle close time for each symbol
        last_candle_time: dict[str, datetime] = {}

        while self.state == EngineState.RUNNING:
            try:
                for symbol, position_data in list(self._active_positions.items()):
                    try:
                        # Get current price
                        ticker = self.client.get_ticker(symbol)
                        if not ticker:
                            continue
                        current_price = float(ticker.last_price)

                        entry_price = position_data["entry_price"]
                        current_sl = position_data.get("stop_loss", 0)
                        side = position_data["side"]

                        # Track highest/lowest prices for trailing
                        if side == "long":
                            if symbol not in self._highest_prices or current_price > self._highest_prices[symbol]:
                                self._highest_prices[symbol] = current_price
                        else:
                            if symbol not in self._lowest_prices or current_price < self._lowest_prices[symbol]:
                                self._lowest_prices[symbol] = current_price

                        # Calculate profit percentage
                        if side == "long":
                            profit_percent = (current_price - entry_price) / entry_price * 100
                        else:
                            profit_percent = (entry_price - current_price) / entry_price * 100

                        new_sl_price = current_sl
                        update_reason = ""

                        # Check break-even first (if enabled)
                        if self.config.breakeven_enabled and not position_data.get("breakeven_applied"):
                            if profit_percent >= self.config.breakeven_activation:
                                # Move SL to break-even + offset
                                offset = entry_price * (self.config.breakeven_offset / 100)
                                if side == "long":
                                    breakeven_sl = entry_price + offset
                                    if breakeven_sl > current_sl:
                                        new_sl_price = breakeven_sl
                                        update_reason = "break-even"
                                        position_data["breakeven_applied"] = True
                                else:
                                    breakeven_sl = entry_price - offset
                                    if breakeven_sl < current_sl or current_sl == 0:
                                        new_sl_price = breakeven_sl
                                        update_reason = "break-even"
                                        position_data["breakeven_applied"] = True

                        # Then check trailing stop (if enabled and profit meets activation)
                        if self.config.trailing_enabled and profit_percent >= self.config.trailing_activation:
                            if self.config.trailing_mode == "supertrend":
                                # SuperTrend-based trailing - check on new candle close
                                df = self.client.get_klines(
                                    symbol=symbol,
                                    interval=self._timeframe_to_interval(self.strategy.timeframe),
                                    limit=250,
                                )

                                if not df.empty and len(df) >= 50:
                                    current_candle_time = df.index[-2] if len(df) > 1 else df.index[-1]
                                    prev_candle_time = last_candle_time.get(symbol)

                                    if prev_candle_time is None or current_candle_time > prev_candle_time:
                                        last_candle_time[symbol] = current_candle_time

                                        indicators = self.strategy.calculate_indicators(df)
                                        triple_st = indicators.get("triple_supertrend")

                                        if triple_st:
                                            sl_line = self.strategy.config.sl_supertrend_line
                                            line_map = {
                                                1: triple_st.st1.supertrend,
                                                2: triple_st.st2.supertrend,
                                                3: triple_st.st3.supertrend,
                                            }
                                            st_line = line_map.get(sl_line, triple_st.st2.supertrend)
                                            st_sl_price = float(st_line.iloc[-2])

                                            if side == "long" and st_sl_price > new_sl_price:
                                                new_sl_price = st_sl_price
                                                update_reason = f"trailing SuperTrend line {sl_line}"
                                            elif side == "short" and (st_sl_price < new_sl_price or new_sl_price == 0):
                                                new_sl_price = st_sl_price
                                                update_reason = f"trailing SuperTrend line {sl_line}"

                            elif self.config.trailing_mode == "percent":
                                # Percentage-based trailing
                                trailing_distance = self.config.trailing_step / 100

                                if side == "long":
                                    reference_price = self._highest_prices.get(symbol, current_price)
                                    potential_sl = reference_price * (1 - trailing_distance)
                                    if potential_sl > new_sl_price:
                                        new_sl_price = potential_sl
                                        update_reason = f"trailing {self.config.trailing_step}%"
                                else:
                                    reference_price = self._lowest_prices.get(symbol, current_price)
                                    potential_sl = reference_price * (1 + trailing_distance)
                                    if potential_sl < new_sl_price or new_sl_price == 0:
                                        new_sl_price = potential_sl
                                        update_reason = f"trailing {self.config.trailing_step}%"

                        # Update SL if changed
                        if new_sl_price != current_sl and update_reason:
                            logger.info(
                                "Updating stop-loss",
                                symbol=symbol,
                                side=side,
                                reason=update_reason,
                                old_sl=current_sl,
                                new_sl=new_sl_price,
                                profit_percent=round(profit_percent, 2),
                            )

                            if not self.config.paper_trading:
                                if isinstance(self.trader, FuturesTrader):
                                    try:
                                        self.trader.update_stop_loss(symbol, Decimal(str(new_sl_price)))
                                    except Exception as e:
                                        logger.error("Failed to update SL on exchange", error=str(e))

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
                    logger.warning(
                        "Trading paused - consecutive losses cooldown",
                        losses=self.stats.consecutive_losses,
                        cooldown_end=cooldown_end.isoformat(),
                    )
                    return False
                else:
                    self.stats.consecutive_losses = 0

        # Check daily loss limit
        if float(self.stats.daily_pnl) <= -self.config.max_daily_loss_percent:
            logger.warning(
                "Trading paused - daily loss limit reached",
                daily_pnl=float(self.stats.daily_pnl),
                limit=self.config.max_daily_loss_percent,
            )
            return False

        # Check weekly loss limit
        if self._week_start_balance is not None:
            try:
                current_balance = self.client.get_balance("USDT").total
                weekly_pnl_percent = float((current_balance - self._week_start_balance) / self._week_start_balance * 100)

                # Reset weekly tracking if new week started
                if self._week_start_time:
                    days_since_start = (datetime.utcnow() - self._week_start_time).days
                    if days_since_start >= 7:
                        self._week_start_balance = current_balance
                        self._week_start_time = datetime.utcnow()
                        weekly_pnl_percent = 0.0

                if weekly_pnl_percent <= -self.config.max_weekly_loss_percent:
                    logger.warning(
                        "Trading paused - weekly loss limit reached",
                        weekly_pnl=weekly_pnl_percent,
                        limit=self.config.max_weekly_loss_percent,
                    )
                    return False
            except Exception:
                pass  # Continue if can't check

        # Check max drawdown from initial balance
        if self._initial_balance is not None:
            try:
                current_balance = self.client.get_balance("USDT").total
                drawdown_percent = float((self._initial_balance - current_balance) / self._initial_balance * 100)

                if drawdown_percent >= self.config.max_drawdown_percent:
                    logger.warning(
                        "Trading paused - max drawdown reached",
                        drawdown=drawdown_percent,
                        limit=self.config.max_drawdown_percent,
                    )
                    return False
            except Exception:
                pass  # Continue if can't check

        # Check minimum balance
        try:
            current_balance = self.client.get_balance("USDT").available
            if float(current_balance) < self.config.min_balance_usdt:
                logger.warning(
                    "Trading paused - below minimum balance",
                    balance=float(current_balance),
                    min_required=self.config.min_balance_usdt,
                )
                return False
        except Exception:
            pass  # Continue if can't check

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
