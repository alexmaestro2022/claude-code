#!/usr/bin/env python3
"""
AILA - Main Bot Runner

This script starts the AILA trading bot.
"""

import asyncio
import signal
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import structlog

from aila.config import settings
from aila.core.strategy import TripleSuperTrendConfig, TripleSuperTrendStrategy
from aila.exchange import BybitClient, BybitConfig
from aila.exchange.models import AccountType
from aila.trading import TradingEngine, TradingEngineConfig

logger = structlog.get_logger(__name__)


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


async def main():
    """Main entry point."""
    logger.info(
        "Starting AILA Trading Bot",
        version=settings.version,
        env=settings.env,
        testnet=settings.exchange.testnet,
    )

    # Create configurations
    bybit_config = create_bybit_config()
    strategy_config = create_strategy_config()
    engine_config = create_engine_config()

    # Log important strategy settings for debugging
    logger.info(
        "Strategy configuration loaded",
        sl_mode=strategy_config.sl_mode,
        sl_supertrend_line=strategy_config.sl_supertrend_line,
        sl_fixed_percent=strategy_config.sl_fixed_percent,
        tp_mode=strategy_config.tp_mode,
        tp_risk_ratio=strategy_config.tp_risk_ratio,
        ema_enabled=strategy_config.ema_enabled,
        trailing_enabled=strategy_config.trailing_enabled,
    )

    # Initialize components
    client = BybitClient(bybit_config)
    strategy = TripleSuperTrendStrategy(strategy_config)
    engine = TradingEngine(client, strategy, engine_config)

    # Register signal handlers for graceful shutdown
    shutdown_event = asyncio.Event()

    def handle_shutdown(signum, frame):
        logger.info("Shutdown signal received")
        shutdown_event.set()

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    # Register callbacks for notifications
    async def on_signal(sig):
        logger.info(
            "Signal generated",
            symbol=sig.symbol,
            type=sig.signal_type.value,
            price=sig.price,
            stop_loss=sig.stop_loss,
            take_profit=sig.take_profit,
            sl_distance_pct=abs((sig.price - sig.stop_loss) / sig.price * 100) if sig.stop_loss else None,
            tp_distance_pct=abs((sig.take_profit - sig.price) / sig.price * 100) if sig.take_profit else None,
            rr_ratio=abs((sig.take_profit - sig.price) / (sig.price - sig.stop_loss)) if sig.stop_loss and sig.take_profit and sig.stop_loss != sig.price else None,
        )

    async def on_trade(trade_type, sig, order, **kwargs):
        logger.info(
            "Trade executed",
            type=trade_type,
            symbol=order.symbol if order else "N/A",
            pnl=kwargs.get("pnl"),
        )

    async def on_error(error):
        logger.error("Trading error", error=str(error))

    engine.on_signal(on_signal)
    engine.on_trade(on_trade)
    engine.on_error(on_error)

    try:
        # Connect to exchange
        if not client.connect():
            logger.error("Failed to connect to exchange")
            return 1

        # Validate connection
        is_valid, message = client.validate_connection()
        if not is_valid:
            logger.error("Connection validation failed", message=message)
            return 1

        logger.info("Exchange connection validated", message=message)

        # Start trading engine
        if not await engine.start():
            logger.error("Failed to start trading engine")
            return 1

        logger.info(
            "Trading bot started",
            pairs=strategy.trading_pairs,
            timeframe=strategy.timeframe,
        )

        # Wait for shutdown signal
        await shutdown_event.wait()

    except Exception as e:
        logger.exception("Unexpected error", error=str(e))
        return 1

    finally:
        # Graceful shutdown
        logger.info("Initiating graceful shutdown")

        # Stop engine (optionally close positions)
        close_positions = False  # Set to True to close positions on shutdown
        await engine.stop(close_positions=close_positions)

        # Disconnect from exchange
        client.disconnect()

        logger.info("Bot stopped", stats=engine.stats)

    return 0


def main_sync():
    """Synchronous main entry point."""
    return asyncio.run(main())


if __name__ == "__main__":
    sys.exit(main_sync())
