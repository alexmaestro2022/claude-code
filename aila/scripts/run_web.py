#!/usr/bin/env python3
"""
AILA - Run Bot with Web Interface

This script starts both the trading bot and the web dashboard.
"""

import asyncio
import sys
import signal
import threading
import os
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import structlog
import uvicorn

# NOTE: We need a placeholder for add_log that will be set after import
_log_callback = None

def _early_add_log(message: str):
    """Early log handler before add_log is available."""
    if _log_callback:
        _log_callback(message)
    else:
        print(message)

# Custom structlog processor that sends to web interface
def web_log_processor(logger, method_name, event_dict):
    """Send logs to web interface."""
    level = method_name
    event = event_dict.get("event", "")

    # Build message with key-value pairs
    extras = " ".join(
        f"{k}={v}" for k, v in event_dict.items()
        if k not in ("level", "event", "timestamp", "logger", "_record", "_from_structlog")
    )

    message = f"[{level:8}] {event}"
    if extras:
        message += f"  {extras}"

    _early_add_log(message)
    return event_dict


# Configure structlog with web processor BEFORE importing aila modules
# This ensures all loggers created in aila modules use this configuration
structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        web_log_processor,
        structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=False,  # Don't cache so we can pick up config changes
)

# Now import aila modules - their loggers will use the configured processor
from aila.config import settings
from aila.core.strategy import TripleSuperTrendConfig, TripleSuperTrendStrategy
from aila.exchange import BybitClient, BybitConfig
from aila.exchange.models import AccountType
from aila.trading import TradingEngine, TradingEngineConfig
from aila.api.main import (
    app, add_log, log_buffer, bot_state as api_bot_state, runtime_settings,
    get_log_message, LOG_MESSAGES, bot_engines, bot_clients, bot_runtime_settings,
    bots_registry, get_bot_runtime_settings, register_position, close_position_record
)

# Set the actual add_log callback now that it's imported
_log_callback = add_log

logger = structlog.get_logger(__name__)

# Global state for web API
bot_state = {
    "engine": None,
    "client": None,
    "running": False,
}


def sync_runtime_settings():
    """Sync runtime settings from config."""
    runtime_settings["timeframe"] = settings.strategy.timeframe
    runtime_settings["trading_pairs"] = settings.strategy.trading_pairs
    runtime_settings["risk_per_trade"] = settings.risk.risk_per_trade
    runtime_settings["tp_risk_ratio"] = settings.risk.tp_risk_ratio
    runtime_settings["sl_mode"] = settings.risk.sl_mode
    runtime_settings["leverage"] = settings.futures.default_leverage
    runtime_settings["max_open_positions"] = settings.risk.max_open_positions
    runtime_settings["ema_enabled"] = settings.strategy.ema_enabled


def create_bybit_config() -> BybitConfig:
    """Create Bybit configuration from runtime settings."""
    return BybitConfig(
        api_key=settings.exchange.api_key.get_secret_value(),
        api_secret=settings.exchange.api_secret.get_secret_value(),
        testnet=settings.exchange.testnet,
        account_type=AccountType(settings.exchange.account_type),
        default_leverage=runtime_settings.get("leverage", settings.futures.default_leverage),
    )


def close_all_positions_on_startup():
    """Close all open positions and cancel all orders on server startup.

    This is a safety feature to prevent orphaned positions when the bot restarts.
    All positions are closed at market price.
    """
    from aila.exchange.models import PositionSide

    print("\n[STARTUP] Checking for open positions and orders...")
    add_log("[info    ] Server startup - checking for orphaned positions...")

    try:
        # Create a temporary client for cleanup
        bybit_config = create_bybit_config()
        client = BybitClient(bybit_config)

        if not client.connect():
            print("[STARTUP] Could not connect to exchange, skipping cleanup")
            add_log("[warning ] Could not connect to exchange for cleanup")
            return

        # Get all open positions WITHOUT cache (critical!)
        positions = client.get_positions(use_cache=False)
        open_positions = [p for p in positions if float(p.size) > 0]

        print(f"[STARTUP] API returned {len(positions)} positions, {len(open_positions)} with size > 0")
        add_log(f"[info    ] Positions check: total={len(positions)}, open={len(open_positions)}")

        if open_positions:
            print(f"[STARTUP] Found {len(open_positions)} open position(s), closing at market...")
            add_log(f"[warning ] Found {len(open_positions)} orphaned position(s), closing...")

            for pos in open_positions:
                try:
                    # Determine side
                    side_str = pos.side.value.upper() if hasattr(pos.side, 'value') else str(pos.side).upper()
                    if side_str in ("BUY", "LONG"):
                        side = PositionSide.LONG
                    else:
                        side = PositionSide.SHORT

                    print(f"[STARTUP] Closing: {pos.symbol} {side_str} size={pos.size}")

                    # Close position at market
                    client.close_position(pos.symbol, side)
                    add_log(f"[info    ] Closed orphaned position: {pos.symbol} {side_str} size={pos.size}")
                    print(f"[STARTUP] ✓ Closed: {pos.symbol} {side_str} size={pos.size}")
                except Exception as e:
                    add_log(f"[error   ] Failed to close {pos.symbol}: {e}")
                    print(f"[STARTUP] ✗ ERROR closing {pos.symbol}: {e}")
        else:
            print("[STARTUP] No open positions found")

        # Cancel all open orders
        try:
            cancelled = client.cancel_all_orders()
            if cancelled > 0:
                print(f"[STARTUP] Cancelled {cancelled} open order(s)")
                add_log(f"[info    ] Cancelled {cancelled} orphaned order(s)")
            else:
                print("[STARTUP] No open orders found")
        except Exception as e:
            add_log(f"[error   ] Failed to cancel orders: {e}")
            print(f"[STARTUP] ERROR cancelling orders: {e}")

        print("[STARTUP] Cleanup complete\n")
        add_log("[info    ] Startup cleanup complete")

    except Exception as e:
        print(f"[STARTUP] ERROR during cleanup: {e}")
        add_log(f"[error   ] Startup cleanup failed: {e}")


def create_strategy_config() -> TripleSuperTrendConfig:
    """Create strategy configuration from runtime settings."""
    # For auto_search mode, use max_simultaneous_orders as max_open_positions
    max_positions = runtime_settings.get("max_open_positions", settings.risk.max_open_positions)
    if runtime_settings.get("auto_search_active"):
        max_positions = runtime_settings.get("max_simultaneous_orders", 1)

    return TripleSuperTrendConfig(
        st1_period=settings.strategy.st1_period,
        st1_multiplier=settings.strategy.st1_multiplier,
        st2_period=settings.strategy.st2_period,
        st2_multiplier=settings.strategy.st2_multiplier,
        st3_period=settings.strategy.st3_period,
        st3_multiplier=settings.strategy.st3_multiplier,
        # Signal Entry configuration
        st1_role=runtime_settings.get("st1_role", "confirm"),
        st2_role=runtime_settings.get("st2_role", "confirm"),
        st3_role=runtime_settings.get("st3_role", "trigger"),
        trigger_confirm_candles=runtime_settings.get("trigger_confirm_candles", 1),
        # Use runtime settings instead of config file
        ema_enabled=runtime_settings.get("ema_enabled", settings.strategy.ema_enabled),
        ema_period=settings.strategy.ema_period,
        ema_filter_mode=runtime_settings.get("ema_filter_mode", settings.strategy.ema_filter_mode),
        timeframe=runtime_settings.get("timeframe", settings.strategy.timeframe),
        trading_pairs=runtime_settings.get("trading_pairs", settings.strategy.trading_pairs),
        risk_per_trade=runtime_settings.get("risk_per_trade", settings.risk.risk_per_trade),
        max_position_percent=settings.risk.max_position_percent,
        max_open_positions=max_positions,
        sl_mode=runtime_settings.get("sl_mode", settings.risk.sl_mode),
        sl_supertrend_line=runtime_settings.get("sl_supertrend_line", settings.risk.sl_supertrend_line),
        sl_fixed_percent=runtime_settings.get("sl_fixed_percent", settings.risk.sl_fixed_percent),
        tp_mode=settings.risk.tp_mode,
        tp_risk_ratio=runtime_settings.get("tp_risk_ratio", settings.risk.tp_risk_ratio),
        trailing_enabled=runtime_settings.get("trailing_enabled", settings.risk.trailing_enabled),
        trailing_mode=runtime_settings.get("trailing_mode", "fix_percent"),
        trailing_activation=runtime_settings.get("trailing_activation", settings.risk.trailing_activation),
        trailing_step=runtime_settings.get("trailing_step", settings.risk.trailing_step),
        trailing_st_line=runtime_settings.get("trailing_st_line", 2),
        trailing_confirm_candles=runtime_settings.get("trailing_confirm_candles", 1),
        partial_tp_enabled=runtime_settings.get("partial_tp_enabled", True),
        partial_tp_close_percent=runtime_settings.get("partial_tp_close_percent", 50),
        partial_tp_sl_move=runtime_settings.get("partial_tp_sl_move", "tp1"),
        partial_tp_sl_offset=runtime_settings.get("partial_tp_sl_offset", 0.2),
        trailing_tp_enabled=runtime_settings.get("trailing_tp_enabled", False),
        trailing_tp_mode=runtime_settings.get("trailing_tp_mode", "st_line"),
        trailing_tp_st_line=runtime_settings.get("trailing_tp_st_line", 2),
        trailing_tp_activation=runtime_settings.get("trailing_tp_activation", 0.5),
        trailing_tp_step=runtime_settings.get("trailing_tp_step", 1.0),
    )


def create_engine_config() -> TradingEngineConfig:
    """Create trading engine configuration."""
    return TradingEngineConfig(
        max_daily_loss_percent=settings.risk.max_daily_loss_percent,
        paper_trading=not settings.is_production,
        order_size=runtime_settings.get("order_size", 100.0),
    )


# ============== Multi-bot support functions ==============

def create_bybit_config_for_bot(bot_id: str) -> BybitConfig:
    """Create Bybit configuration for a specific bot."""
    bot_settings = get_bot_runtime_settings(bot_id)
    return BybitConfig(
        api_key=settings.exchange.api_key.get_secret_value(),
        api_secret=settings.exchange.api_secret.get_secret_value(),
        testnet=settings.exchange.testnet,
        account_type=AccountType(settings.exchange.account_type),
        default_leverage=bot_settings.get("leverage", settings.futures.default_leverage),
    )


def create_strategy_config_for_bot(bot_id: str) -> TripleSuperTrendConfig:
    """Create strategy configuration for a specific bot."""
    bot_settings = get_bot_runtime_settings(bot_id)

    # For auto_search mode, use max_simultaneous_orders as max_open_positions
    max_positions = bot_settings.get("max_open_positions", settings.risk.max_open_positions)
    if bot_settings.get("auto_search_active"):
        max_positions = bot_settings.get("max_simultaneous_orders", 1)

    return TripleSuperTrendConfig(
        st1_period=settings.strategy.st1_period,
        st1_multiplier=settings.strategy.st1_multiplier,
        st2_period=settings.strategy.st2_period,
        st2_multiplier=settings.strategy.st2_multiplier,
        st3_period=settings.strategy.st3_period,
        st3_multiplier=settings.strategy.st3_multiplier,
        # Signal Entry configuration
        st1_role=bot_settings.get("st1_role", "confirm"),
        st2_role=bot_settings.get("st2_role", "confirm"),
        st3_role=bot_settings.get("st3_role", "trigger"),
        trigger_confirm_candles=bot_settings.get("trigger_confirm_candles", 1),
        ema_enabled=bot_settings.get("ema_enabled", settings.strategy.ema_enabled),
        ema_period=settings.strategy.ema_period,
        ema_filter_mode=bot_settings.get("ema_filter_mode", settings.strategy.ema_filter_mode),
        timeframe=bot_settings.get("timeframe", settings.strategy.timeframe),
        trading_pairs=bot_settings.get("trading_pairs", settings.strategy.trading_pairs),
        risk_per_trade=bot_settings.get("risk_per_trade", settings.risk.risk_per_trade),
        max_position_percent=settings.risk.max_position_percent,
        max_open_positions=max_positions,
        sl_mode=bot_settings.get("sl_mode", settings.risk.sl_mode),
        sl_supertrend_line=bot_settings.get("sl_supertrend_line", settings.risk.sl_supertrend_line),
        sl_fixed_percent=bot_settings.get("sl_fixed_percent", settings.risk.sl_fixed_percent),
        tp_mode=bot_settings.get("tp_mode", settings.risk.tp_mode),
        tp_risk_ratio=bot_settings.get("tp_risk_ratio", settings.risk.tp_risk_ratio),
        tp_fixed_percent=bot_settings.get("tp_fixed_percent", 2.0),
        trailing_enabled=bot_settings.get("trailing_enabled", settings.risk.trailing_enabled),
        trailing_mode=bot_settings.get("trailing_mode", "fix_percent"),
        trailing_activation=bot_settings.get("trailing_activation", settings.risk.trailing_activation),
        trailing_step=bot_settings.get("trailing_step", settings.risk.trailing_step),
        trailing_st_line=bot_settings.get("trailing_st_line", 2),
        trailing_confirm_candles=bot_settings.get("trailing_confirm_candles", 1),
        partial_tp_enabled=bot_settings.get("partial_tp_enabled", True),
        partial_tp_close_percent=bot_settings.get("partial_tp_close_percent", 50),
        partial_tp_sl_move=bot_settings.get("partial_tp_sl_move", "tp1"),
        partial_tp_sl_offset=bot_settings.get("partial_tp_sl_offset", 0.2),
        trailing_tp_enabled=bot_settings.get("trailing_tp_enabled", False),
        trailing_tp_mode=bot_settings.get("trailing_tp_mode", "st_line"),
        trailing_tp_st_line=bot_settings.get("trailing_tp_st_line", 2),
        trailing_tp_activation=bot_settings.get("trailing_tp_activation", 0.5),
        trailing_tp_step=bot_settings.get("trailing_tp_step", 1.0),
    )


def create_engine_config_for_bot(bot_id: str) -> TradingEngineConfig:
    """Create trading engine configuration for a specific bot."""
    bot_settings = get_bot_runtime_settings(bot_id)
    return TradingEngineConfig(
        max_daily_loss_percent=settings.risk.max_daily_loss_percent,
        paper_trading=not settings.is_production,
        order_size=bot_settings.get("order_size", 100.0),
        # Position sizing settings
        position_sizing_mode=bot_settings.get("position_sizing_mode", "fixed_amount"),
        risk_per_trade=bot_settings.get("risk_per_trade", 2.0),
        # Margin mode
        margin_mode=bot_settings.get("margin_mode", "cross"),
    )


async def start_trading_for_bot(bot_id: str):
    """Start a trading engine for a specific bot.

    This creates an independent engine instance for the bot, allowing
    multiple bots to run concurrently with different strategies.
    """
    if bot_id not in bots_registry:
        raise ValueError(f"Bot {bot_id} not found in registry")

    bot = bots_registry[bot_id]
    bot_name = bot.get("name", bot_id)

    add_log(f"[info    ] Starting bot: {bot_name}")

    # Get bot-specific settings
    bot_settings = get_bot_runtime_settings(bot_id)

    # Log bot configuration
    pairs = bot_settings.get("trading_pairs", [])
    tf = bot_settings.get("timeframe", "1h")
    leverage = bot_settings.get("leverage", 10)
    ema = bot_settings.get("ema_enabled", True)
    bot_mode = bot_settings.get("bot_mode", "manual")
    auto_search = bot_settings.get("auto_search_active", False)

    if auto_search:
        max_orders = bot_settings.get("max_simultaneous_orders", 1)
        add_log(f"[info    ] [{bot_name}] Mode: AUTO SEARCH - {len(pairs)} pairs, max orders={max_orders}")
    else:
        add_log(f"[info    ] [{bot_name}] Mode: MANUAL - {len(pairs)} pairs")

    order_size = bot_settings.get("order_size", 100.0)
    add_log(f"[info    ] [{bot_name}] Config: tf={tf} lev={leverage}x ema={ema} size={order_size} USDT")

    # Create configurations for this bot
    bybit_config = create_bybit_config_for_bot(bot_id)
    strategy_config = create_strategy_config_for_bot(bot_id)
    engine_config = create_engine_config_for_bot(bot_id)

    add_log(f"[info    ] [{bot_name}] Strategy: tp={strategy_config.tp_risk_ratio} sl={strategy_config.sl_mode} risk={strategy_config.risk_per_trade}%")

    # Initialize components for this bot
    client = BybitClient(bybit_config)
    strategy = TripleSuperTrendStrategy(strategy_config)
    engine = TradingEngine(client, strategy, engine_config)

    # Store in bot-specific registries
    bot_engines[bot_id] = engine
    bot_clients[bot_id] = client

    # Also update global bot_state for backwards compatibility (points to first/latest running bot)
    bot_state["client"] = client
    bot_state["engine"] = engine
    api_bot_state["client"] = client
    api_bot_state["engine"] = engine

    # Register callbacks with bot_id context
    async def on_signal(sig):
        add_log(f"[info    ] [{bot_name}] Signal: {sig.symbol} {sig.signal_type.value} price={sig.price:.2f} sl={sig.stop_loss:.2f} tp={sig.take_profit:.2f}")

    async def on_trade(trade_type, sig, order, **kwargs):
        pnl = kwargs.get("pnl")
        reason = kwargs.get("reason", "Unknown")

        if trade_type == "entry" and sig and order:
            # Register position when trade opens - explicitly pass bot_id
            side = "LONG" if sig.is_long else "SHORT"
            register_position(
                symbol=sig.symbol,
                side=side,
                entry_price=float(sig.price),
                size=float(order.qty) if hasattr(order, 'qty') else 0.0,
                sl=float(sig.stop_loss),
                tp=float(sig.take_profit),
                bot_id=bot_id,  # Explicit bot_id
            )
            add_log(f"[info    ] [{bot_name}] Trade opened: {sig.symbol} {side}")

        elif trade_type == "exit" and order:
            # Close position record when trade closes
            close_position_record(
                symbol=order.symbol if hasattr(order, 'symbol') else 'N/A',
                reason=reason,
                pnl_usdt=float(pnl) if pnl else None,
            )
            add_log(f"[info    ] [{bot_name}] Trade closed: {order.symbol if hasattr(order, 'symbol') else 'N/A'} pnl={pnl}")

    async def on_error(error):
        add_log(f"[error   ] [{bot_name}] {str(error)}")

    engine.on_signal(on_signal)
    engine.on_trade(on_trade)
    engine.on_error(on_error)

    # Connect to exchange
    if not client.connect():
        add_log(f"[error   ] [{bot_name}] Failed to connect to exchange")
        # Cleanup
        bot_engines.pop(bot_id, None)
        bot_clients.pop(bot_id, None)
        raise Exception("Failed to connect to exchange")

    # Validate connection
    is_valid, message = client.validate_connection()
    if not is_valid:
        add_log(f"[error   ] [{bot_name}] Connection validation failed: {message}")
        bot_engines.pop(bot_id, None)
        bot_clients.pop(bot_id, None)
        raise Exception(f"Connection validation failed: {message}")

    add_log(f"[info    ] [{bot_name}] {message}")

    # Start trading engine
    if not await engine.start():
        add_log(f"[error   ] [{bot_name}] Failed to start trading engine")
        bot_engines.pop(bot_id, None)
        bot_clients.pop(bot_id, None)
        raise Exception("Failed to start trading engine")

    add_log(f"[info    ] [{bot_name}] Bot running - pairs={strategy.trading_pairs} timeframe={strategy.timeframe}")

    # Update global state
    bot_state["running"] = True
    api_bot_state["running"] = True


async def stop_trading_for_bot(bot_id: str):
    """Stop the trading engine for a specific bot."""
    if bot_id not in bots_registry:
        return

    bot = bots_registry[bot_id]
    bot_name = bot.get("name", bot_id)

    add_log(f"[info    ] Stopping bot: {bot_name}...")

    engine = bot_engines.get(bot_id)
    client = bot_clients.get(bot_id)

    # Stop engine first to stop scanning (8 sec timeout to allow internal cleanup)
    if engine:
        add_log(f"[info    ] [{bot_name}] Stopping trading engine...")
        try:
            await asyncio.wait_for(engine.stop(), timeout=8.0)
            add_log(f"[info    ] [{bot_name}] Trading engine stopped")
        except asyncio.TimeoutError:
            add_log(f"[warning ] [{bot_name}] Engine stop timed out, forcing...")
            # Force stop flag to ensure loops exit
            engine._stop_requested = True
        except Exception as e:
            add_log(f"[error   ] [{bot_name}] Engine stop error: {e}")
            engine._stop_requested = True

    # Close all positions for this bot (market orders for speed)
    if client and getattr(client, 'is_connected', False):
        try:
            add_log(f"[info    ] [{bot_name}] Cancelling orders and closing positions...")

            # Cancel all pending orders
            try:
                cancelled = client.cancel_all_orders()
                add_log(f"[info    ] [{bot_name}] Cancelled {cancelled} orders")
            except Exception as e:
                add_log(f"[warning ] [{bot_name}] Cancel orders: {e}")

            # Close all open positions at market price
            try:
                positions = client.get_positions()
                open_positions = [p for p in positions if float(p.size) > 0]

                if open_positions:
                    add_log(f"[info    ] [{bot_name}] Closing {len(open_positions)} positions...")
                    for pos in open_positions:
                        try:
                            client.close_position(pos.symbol, pos.side)
                            add_log(f"[info    ] [{bot_name}] Closed {pos.symbol} {pos.side.value}")
                        except Exception as e:
                            add_log(f"[error   ] [{bot_name}] Close {pos.symbol}: {e}")
                else:
                    add_log(f"[info    ] [{bot_name}] No open positions")
            except Exception as e:
                add_log(f"[error   ] [{bot_name}] Get positions: {e}")

            # Disconnect
            try:
                client.disconnect()
            except Exception:
                pass
        except Exception as e:
            add_log(f"[error   ] [{bot_name}] Cleanup error: {e}")

    # Remove from registries
    bot_engines.pop(bot_id, None)
    bot_clients.pop(bot_id, None)

    # Update global state if no more running bots
    if not bot_engines:
        bot_state["running"] = False
        bot_state["engine"] = None
        bot_state["client"] = None
        api_bot_state["running"] = False
        api_bot_state["engine"] = None
        api_bot_state["client"] = None
    else:
        # Point global state to another running bot
        other_bot_id = next(iter(bot_engines.keys()))
        bot_state["engine"] = bot_engines[other_bot_id]
        bot_state["client"] = bot_clients[other_bot_id]
        api_bot_state["engine"] = bot_engines[other_bot_id]
        api_bot_state["client"] = bot_clients[other_bot_id]

    add_log(f"[info    ] Bot {bot_name} stopped")


# Background client for balance display (independent of bots)
_background_client = None
_bg_client_error_logged = False


def get_background_client():
    """Get or create background client for balance/positions display."""
    global _background_client, _bg_client_error_logged
    if _background_client is None or not _background_client.is_connected:
        try:
            config = create_bybit_config()
            _background_client = BybitClient(config)
            if _background_client.connect():
                # BybitClient.connect() already logs via structlog
                # validate_connection() also logs the balance
                _background_client.validate_connection()
                _bg_client_error_logged = False
            else:
                if not _bg_client_error_logged:
                    logger.error("Failed to connect to Bybit API")
                    _bg_client_error_logged = True
                return None
        except Exception as e:
            if not _bg_client_error_logged:
                logger.error(f"Background client error: {e}")
                _bg_client_error_logged = True
            return None
    return _background_client


# Update stats endpoint with real data
@app.get("/api/stats")
async def get_stats():
    """Get current trading statistics."""
    stats = {
        "balance": None,
        "positions": 0,
        "trades": 0,
        "pnl": 0.0,
        "api_stats": None,
    }

    # Try to get balance from active bot client first
    client = bot_state.get("client")
    if not client or not getattr(client, 'is_connected', False):
        # Fall back to background client
        client = get_background_client()

    if client and getattr(client, 'is_connected', False):
        try:
            balance = client.get_balance("USDT")
            stats["balance"] = float(balance.total)
        except Exception as e:
            logger.debug(f"Failed to get balance: {e}")

        try:
            positions = client.get_positions()
            stats["positions"] = len([p for p in positions if float(p.size) > 0])
        except Exception as e:
            logger.debug(f"Failed to get positions: {e}")

        # Get API stats (ping, requests)
        try:
            if hasattr(client, 'get_api_stats'):
                stats["api_stats"] = client.get_api_stats()
        except Exception as e:
            logger.debug(f"Failed to get API stats: {e}")

    if bot_state.get("engine"):
        engine = bot_state["engine"]
        stats["trades"] = engine.stats.trades_executed
        stats["pnl"] = float(engine.stats.daily_pnl)

    return stats


@app.get("/api/ping")
async def get_ping():
    """Get API ping to exchange."""
    client = bot_state.get("client")
    if not client or not getattr(client, 'is_connected', False):
        client = get_background_client()

    if client and getattr(client, 'is_connected', False):
        try:
            ping_ms = client.ping()
            api_stats = client.get_api_stats() if hasattr(client, 'get_api_stats') else {}
            return {
                "ping_ms": round(ping_ms, 1),
                "total_requests": api_stats.get("total_requests", 0),
                "requests_per_5s": api_stats.get("requests_this_minute", 0),  # Per 5 seconds
                "rate_limit": api_stats.get("rate_limit", 600),
                "rate_usage": round(api_stats.get("rate_usage_percent", 0), 1),
            }
        except Exception as e:
            return {"error": str(e)}

    return {"error": "Not connected"}


@app.post("/api/restart-server")
async def restart_server():
    """Restart the server by exiting process (systemd will restart it)."""
    import os

    add_log("[info    ] Server restart requested via web interface")

    # First stop all running bots and close positions
    try:
        if bot_state.get("running"):
            add_log("[info    ] Stopping bot and closing positions before restart...")
            await stop_trading()
    except Exception as e:
        add_log(f"[warning ] Error stopping bot: {e}")

    # Schedule restart in background (so we can return response first)
    async def do_restart():
        await asyncio.sleep(0.5)  # Give time for response to be sent
        add_log("[info    ] Exiting process for restart...")
        os._exit(0)  # systemd will restart the service

    asyncio.create_task(do_restart())
    return {"status": "restarting", "message": "Server will restart in 0.5 second"}


async def start_trading():
    """Start the trading bot (called from API)."""
    add_log(f"[info    ] {get_log_message('starting_bot')}")

    # Log runtime settings
    pairs = runtime_settings.get("trading_pairs", settings.strategy.trading_pairs)
    tf = runtime_settings.get("timeframe", settings.strategy.timeframe)
    leverage = runtime_settings.get("leverage", settings.futures.default_leverage)
    ema = runtime_settings.get("ema_enabled", settings.strategy.ema_enabled)
    bot_mode = runtime_settings.get("bot_mode", "manual")
    auto_search = runtime_settings.get("auto_search_active", False)

    if auto_search:
        max_orders = runtime_settings.get("max_simultaneous_orders", 1)
        add_log(f"[info    ] {get_log_message('mode_auto', pairs=len(pairs), max_orders=max_orders)}")
    else:
        add_log(f"[info    ] {get_log_message('mode_manual', pairs=len(pairs))}")

    order_size = runtime_settings.get("order_size", 100.0)
    add_log(f"[info    ] {get_log_message('config_info', tf=tf, lev=leverage, ema=ema, size=order_size)}")

    # Create configurations
    bybit_config = create_bybit_config()
    strategy_config = create_strategy_config()
    engine_config = create_engine_config()

    add_log(f"[info    ] {get_log_message('strategy_info', tp=strategy_config.tp_risk_ratio, sl=strategy_config.sl_mode, risk=strategy_config.risk_per_trade)}")

    # Initialize components
    client = BybitClient(bybit_config)
    strategy = TripleSuperTrendStrategy(strategy_config)
    engine = TradingEngine(client, strategy, engine_config)

    # Store in global state
    bot_state["client"] = client
    bot_state["engine"] = engine
    api_bot_state["client"] = client
    api_bot_state["engine"] = engine

    # Register callbacks
    async def on_signal(sig):
        add_log(f"[info    ] Signal: {sig.symbol} {sig.signal_type.value} price={sig.price:.2f} sl={sig.stop_loss:.2f} tp={sig.take_profit:.2f}")

    async def on_trade(trade_type, sig, order, **kwargs):
        pnl = kwargs.get("pnl")
        reason = kwargs.get("reason", "Unknown")

        if trade_type == "entry" and sig and order:
            # Register position when trade opens
            from aila.api.main import register_position
            side = "LONG" if sig.is_long else "SHORT"
            register_position(
                symbol=sig.symbol,
                side=side,
                entry_price=float(sig.price),
                size=float(order.qty) if hasattr(order, 'qty') else 0.0,
                sl=float(sig.stop_loss),
                tp=float(sig.take_profit),
            )
            add_log(f"[info    ] Trade opened: {sig.symbol} {side}")

        elif trade_type == "exit" and order:
            # Close position record when trade closes
            from aila.api.main import close_position_record
            close_position_record(
                symbol=order.symbol if hasattr(order, 'symbol') else 'N/A',
                reason=reason,
                pnl_usdt=float(pnl) if pnl else None,
            )
            add_log(f"[info    ] Trade closed: {order.symbol if hasattr(order, 'symbol') else 'N/A'} pnl={pnl}")

    async def on_error(error):
        add_log(f"[error   ] {str(error)}")

    engine.on_signal(on_signal)
    engine.on_trade(on_trade)
    engine.on_error(on_error)

    # Connect to exchange
    if not client.connect():
        add_log("[error   ] Failed to connect to exchange")
        raise Exception("Failed to connect to exchange")

    # Validate connection
    is_valid, message = client.validate_connection()
    if not is_valid:
        add_log(f"[error   ] Connection validation failed: {message}")
        raise Exception(f"Connection validation failed: {message}")

    add_log(f"[info    ] {message}")

    # Start trading engine
    if not await engine.start():
        add_log("[error   ] Failed to start trading engine")
        raise Exception("Failed to start trading engine")

    add_log(f"[info    ] Bot running - pairs={strategy.trading_pairs} timeframe={strategy.timeframe}")
    bot_state["running"] = True
    api_bot_state["running"] = True


async def stop_trading():
    """Stop the trading bot (called from API)."""
    add_log("[info    ] Stopping bot...")

    # Mark as stopped immediately for UI responsiveness
    bot_state["running"] = False
    api_bot_state["running"] = False

    # Stop engine first to stop scanning (8 sec timeout to allow internal cleanup)
    if bot_state["engine"]:
        add_log("[info    ] Stopping trading engine...")
        try:
            await asyncio.wait_for(bot_state["engine"].stop(), timeout=8.0)
            add_log("[info    ] Trading engine stopped")
        except asyncio.TimeoutError:
            add_log("[warning ] Engine stop timed out, forcing...")
            # Force stop flag to ensure loops exit
            bot_state["engine"]._stop_requested = True
        except Exception as e:
            add_log(f"[error   ] Engine stop error: {e}")
            bot_state["engine"]._stop_requested = True

    # Close all positions (market orders for speed)
    client = bot_state.get("client")
    if client and getattr(client, 'is_connected', False):
        try:
            add_log("[info    ] Cancelling orders and closing positions...")

            # Cancel all pending orders
            try:
                cancelled = client.cancel_all_orders()
                add_log(f"[info    ] Cancelled {cancelled} orders")
            except Exception as e:
                add_log(f"[warning ] Cancel orders: {e}")

            # Close all open positions at market price
            try:
                positions = client.get_positions()
                open_positions = [p for p in positions if float(p.size) > 0]

                if open_positions:
                    add_log(f"[info    ] Closing {len(open_positions)} positions...")
                    for pos in open_positions:
                        try:
                            client.close_position(pos.symbol, pos.side)
                            add_log(f"[info    ] Closed {pos.symbol} {pos.side.value}")
                        except Exception as e:
                            add_log(f"[error   ] Close {pos.symbol}: {e}")
                else:
                    add_log("[info    ] No open positions")
            except Exception as e:
                add_log(f"[error   ] Get positions: {e}")

            # Disconnect
            try:
                client.disconnect()
            except Exception:
                pass

        except Exception as e:
            add_log(f"[error   ] Error closing positions: {e}")

    # Clear state
    bot_state["engine"] = None
    bot_state["client"] = None
    api_bot_state["engine"] = None
    api_bot_state["client"] = None

    add_log("[info    ] Bot stopped")


def update_engine_settings():
    """Update engine's strategy config with current runtime_settings (for resume after pause)."""
    engine = bot_state.get("engine")
    if not engine or not engine.strategy:
        return False

    # Update strategy config with new settings
    if runtime_settings.get("auto_search_active"):
        max_positions = runtime_settings.get("max_simultaneous_orders", 1)
    else:
        max_positions = runtime_settings.get("max_open_positions", 1)

    engine.strategy.config.max_open_positions = max_positions
    engine.strategy.config.risk_per_trade = runtime_settings.get("risk_per_trade", 2.0)
    engine.strategy.config.tp_risk_ratio = runtime_settings.get("tp_risk_ratio", 2.0)
    engine.strategy.config.sl_mode = runtime_settings.get("sl_mode", "supertrend_line")
    engine.strategy.config.sl_supertrend_line = runtime_settings.get("sl_supertrend_line", 2)
    engine.strategy.config.sl_fixed_percent = runtime_settings.get("sl_fixed_percent", 2.0)
    engine.strategy.config.trailing_enabled = runtime_settings.get("trailing_enabled", True)
    engine.strategy.config.trailing_mode = runtime_settings.get("trailing_mode", "fix_percent")
    engine.strategy.config.trailing_activation = runtime_settings.get("trailing_activation", 1.0)
    engine.strategy.config.trailing_step = runtime_settings.get("trailing_step", 0.5)
    engine.strategy.config.trailing_st_line = runtime_settings.get("trailing_st_line", 2)
    engine.strategy.config.trailing_confirm_candles = runtime_settings.get("trailing_confirm_candles", 1)
    engine.strategy.config.partial_tp_enabled = runtime_settings.get("partial_tp_enabled", True)
    engine.strategy.config.partial_tp_close_percent = runtime_settings.get("partial_tp_close_percent", 50)
    engine.strategy.config.partial_tp_sl_move = runtime_settings.get("partial_tp_sl_move", "tp1")
    engine.strategy.config.partial_tp_sl_offset = runtime_settings.get("partial_tp_sl_offset", 0.2)
    engine.strategy.config.trailing_tp_enabled = runtime_settings.get("trailing_tp_enabled", False)
    engine.strategy.config.trailing_tp_mode = runtime_settings.get("trailing_tp_mode", "st_line")
    engine.strategy.config.trailing_tp_st_line = runtime_settings.get("trailing_tp_st_line", 2)
    engine.strategy.config.trailing_tp_activation = runtime_settings.get("trailing_tp_activation", 0.5)
    engine.strategy.config.trailing_tp_step = runtime_settings.get("trailing_tp_step", 1.0)
    # Signal Entry configuration
    engine.strategy.config.st1_role = runtime_settings.get("st1_role", "confirm")
    engine.strategy.config.st2_role = runtime_settings.get("st2_role", "confirm")
    engine.strategy.config.st3_role = runtime_settings.get("st3_role", "trigger")
    engine.strategy.config.trigger_confirm_candles = runtime_settings.get("trigger_confirm_candles", 1)
    engine.strategy.config.ema_enabled = runtime_settings.get("ema_enabled", True)
    engine.strategy.config.ema_filter_mode = runtime_settings.get("ema_filter_mode", "strict")

    add_log(f"[info    ] Engine settings updated: max_positions={max_positions}")
    return True


# Export update function for API
bot_state["update_settings_callback"] = update_engine_settings


async def run_bot():
    """Run the trading bot (auto-start mode)."""
    add_log("[info    ] Starting AILA Trading Bot with Web Interface")

    try:
        await start_trading()

        # Keep running
        while bot_state["running"]:
            await asyncio.sleep(1)

    except Exception as e:
        add_log(f"[error   ] Unexpected error: {str(e)}")

    finally:
        if bot_state["running"]:
            await stop_trading()


def run_uvicorn():
    """Run uvicorn in a separate thread."""
    config = uvicorn.Config(
        app,
        host=settings.web.host,
        port=settings.web.port,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    server.run()


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="AILA Trading Bot with Web Interface")
    parser.add_argument("--autostart", action="store_true", help="Auto-start the legacy bot (deprecated)")
    args = parser.parse_args()

    # Sync settings on startup
    sync_runtime_settings()

    # Close all orphaned positions and orders on startup (safety feature)
    close_all_positions_on_startup()

    # Set up API callbacks
    # Legacy single-bot callbacks (for backwards compatibility)
    api_bot_state["start_callback"] = start_trading
    api_bot_state["stop_callback"] = stop_trading
    # New multi-bot callbacks
    api_bot_state["start_callback_for_bot"] = start_trading_for_bot
    api_bot_state["stop_callback_for_bot"] = stop_trading_for_bot

    # Start web server in background thread
    web_thread = threading.Thread(target=run_uvicorn, daemon=True)
    web_thread.start()

    print(f"\n{'='*50}")
    print(f"  AILA Trading Bot - Web Interface")
    print(f"  Dashboard: http://{settings.web.host}:{settings.web.port}")
    print(f"  Mode: Multi-bot (create bots via web interface)")
    print(f"{'='*50}\n")

    try:
        if args.autostart:
            # Legacy auto-start mode (deprecated)
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(run_bot())
            finally:
                loop.close()
        else:
            # Default: Web interface only mode (multi-bot)
            # Bots are created and started via web interface
            import time
            while True:
                time.sleep(0.1)
    except KeyboardInterrupt:
        print("\nExiting...")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        os._exit(0)


if __name__ == "__main__":
    main()
