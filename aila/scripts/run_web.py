#!/usr/bin/env python3
"""
AILA - Run Bot with Web Interface

This script starts both the trading bot and the web dashboard.
"""

import asyncio
import sys
import signal
import threading
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import structlog
import uvicorn

from aila.config import settings
from aila.core.strategy import TripleSuperTrendConfig, TripleSuperTrendStrategy
from aila.exchange import BybitClient, BybitConfig
from aila.exchange.models import AccountType
from aila.trading import TradingEngine, TradingEngineConfig
from aila.api.main import app, add_log, log_buffer, bot_state as api_bot_state, runtime_settings

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
    """Create Bybit configuration from settings."""
    return BybitConfig(
        api_key=settings.exchange.api_key.get_secret_value(),
        api_secret=settings.exchange.api_secret.get_secret_value(),
        testnet=settings.exchange.testnet,
        account_type=AccountType(settings.exchange.account_type),
        default_leverage=settings.futures.default_leverage,
    )


def create_strategy_config() -> TripleSuperTrendConfig:
    """Create strategy configuration from settings."""
    return TripleSuperTrendConfig(
        st1_period=settings.strategy.st1_period,
        st1_multiplier=settings.strategy.st1_multiplier,
        st2_period=settings.strategy.st2_period,
        st2_multiplier=settings.strategy.st2_multiplier,
        st3_period=settings.strategy.st3_period,
        st3_multiplier=settings.strategy.st3_multiplier,
        ema_enabled=settings.strategy.ema_enabled,
        ema_period=settings.strategy.ema_period,
        ema_filter_mode=settings.strategy.ema_filter_mode,
        timeframe=settings.strategy.timeframe,
        trading_pairs=settings.strategy.trading_pairs,
        risk_per_trade=settings.risk.risk_per_trade,
        max_position_percent=settings.risk.max_position_percent,
        max_open_positions=settings.risk.max_open_positions,
        sl_mode=settings.risk.sl_mode,
        sl_supertrend_line=settings.risk.sl_supertrend_line,
        sl_fixed_percent=settings.risk.sl_fixed_percent,
        tp_mode=settings.risk.tp_mode,
        tp_risk_ratio=settings.risk.tp_risk_ratio,
        trailing_enabled=settings.risk.trailing_enabled,
        trailing_activation=settings.risk.trailing_activation,
        trailing_step=settings.risk.trailing_step,
    )


def create_engine_config() -> TradingEngineConfig:
    """Create trading engine configuration."""
    return TradingEngineConfig(
        max_daily_loss_percent=settings.risk.max_daily_loss_percent,
        paper_trading=not settings.is_production,
    )


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

    add_log(message)
    return event_dict


# Configure structlog with web processor
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
    cache_logger_on_first_use=True,
)


# Update stats endpoint with real data
@app.get("/api/stats")
async def get_stats():
    """Get current trading statistics."""
    stats = {
        "balance": None,
        "positions": 0,
        "trades": 0,
        "pnl": 0.0,
    }

    if bot_state["client"] and bot_state["client"].is_connected:
        try:
            balance = bot_state["client"].get_balance("USDT")
            stats["balance"] = float(balance.total)
        except Exception:
            pass

        try:
            positions = bot_state["client"].get_positions()
            stats["positions"] = len([p for p in positions if float(p.quantity) > 0])
        except Exception:
            pass

    if bot_state["engine"]:
        engine = bot_state["engine"]
        stats["trades"] = engine.stats.trades_executed
        stats["pnl"] = float(engine.stats.daily_pnl)

    return stats


async def start_trading():
    """Start the trading bot (called from API)."""
    add_log("[info    ] Starting AILA Trading Bot...")

    # Sync settings
    sync_runtime_settings()

    # Create configurations
    bybit_config = create_bybit_config()
    strategy_config = create_strategy_config()
    engine_config = create_engine_config()

    add_log(f"[info    ] Strategy: tp_risk_ratio={strategy_config.tp_risk_ratio} sl_mode={strategy_config.sl_mode}")

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
        add_log(f"[info    ] Trade: {trade_type} {order.symbol if order else 'N/A'} pnl={pnl}")

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

    bot_state["running"] = False
    api_bot_state["running"] = False

    if bot_state["engine"]:
        await bot_state["engine"].stop()
    if bot_state["client"]:
        bot_state["client"].disconnect()

    bot_state["engine"] = None
    bot_state["client"] = None
    api_bot_state["engine"] = None
    api_bot_state["client"] = None

    add_log("[info    ] Bot stopped")


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
    parser.add_argument("--no-autostart", action="store_true", help="Don't auto-start the bot")
    args = parser.parse_args()

    # Sync settings on startup
    sync_runtime_settings()

    # Set up API callbacks
    api_bot_state["start_callback"] = start_trading
    api_bot_state["stop_callback"] = stop_trading

    # Start web server in background thread
    web_thread = threading.Thread(target=run_uvicorn, daemon=True)
    web_thread.start()

    print(f"\n{'='*50}")
    print(f"  AILA Trading Bot - Web Interface")
    print(f"  Dashboard: http://{settings.web.host}:{settings.web.port}")
    if args.no_autostart:
        print(f"  Mode: Manual start (use web interface)")
    else:
        print(f"  Mode: Auto-start")
    print(f"{'='*50}\n")

    # Handle shutdown
    def handle_shutdown(signum, frame):
        print("\nShutdown signal received...")
        bot_state["running"] = False
        api_bot_state["running"] = False

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    # Run bot or keep server running
    try:
        if args.no_autostart:
            # Just keep the web server running
            add_log("[info    ] Web interface started. Use buttons to control bot.")
            while True:
                asyncio.get_event_loop().run_until_complete(asyncio.sleep(1))
        else:
            asyncio.run(run_bot())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
