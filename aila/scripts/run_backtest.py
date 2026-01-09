#!/usr/bin/env python3
"""
AILA - Backtest Runner

This script runs backtests on historical data.
"""

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pandas as pd
import structlog

from aila.core.strategy import TripleSuperTrendConfig, TripleSuperTrendStrategy

logger = structlog.get_logger(__name__)


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="AILA Backtest Runner")

    parser.add_argument(
        "--symbol",
        type=str,
        default="BTCUSDT",
        help="Trading pair symbol",
    )

    parser.add_argument(
        "--timeframe",
        type=str,
        default="1h",
        help="Timeframe (1m, 5m, 15m, 1h, 4h, 1d)",
    )

    parser.add_argument(
        "--start",
        type=str,
        help="Start date (YYYY-MM-DD)",
    )

    parser.add_argument(
        "--end",
        type=str,
        help="End date (YYYY-MM-DD)",
    )

    parser.add_argument(
        "--days",
        type=int,
        default=90,
        help="Number of days to backtest (if start/end not specified)",
    )

    parser.add_argument(
        "--initial-balance",
        type=float,
        default=10000,
        help="Initial balance in USDT",
    )

    parser.add_argument(
        "--leverage",
        type=int,
        default=10,
        help="Leverage for futures",
    )

    parser.add_argument(
        "--output",
        type=str,
        help="Output file for results (CSV)",
    )

    return parser.parse_args()


def load_historical_data(
    symbol: str,
    timeframe: str,
    start_date: datetime,
    end_date: datetime,
) -> pd.DataFrame:
    """
    Load historical data for backtesting.

    In production, this would fetch from the exchange or a local database.
    For now, it returns mock data.
    """
    logger.info(
        "Loading historical data",
        symbol=symbol,
        timeframe=timeframe,
        start=start_date.isoformat(),
        end=end_date.isoformat(),
    )

    # Create mock data for demonstration
    # In production, replace with actual data loading
    date_range = pd.date_range(start=start_date, end=end_date, freq="1h")

    import numpy as np

    np.random.seed(42)

    # Generate realistic price data
    initial_price = 50000.0
    returns = np.random.normal(0, 0.02, len(date_range))
    prices = initial_price * np.cumprod(1 + returns)

    df = pd.DataFrame({
        "timestamp": date_range,
        "open": prices * (1 + np.random.uniform(-0.01, 0.01, len(date_range))),
        "high": prices * (1 + np.random.uniform(0, 0.02, len(date_range))),
        "low": prices * (1 - np.random.uniform(0, 0.02, len(date_range))),
        "close": prices,
        "volume": np.random.uniform(100, 1000, len(date_range)),
    })

    df.set_index("timestamp", inplace=True)

    return df


def run_backtest(
    df: pd.DataFrame,
    strategy: TripleSuperTrendStrategy,
    initial_balance: float,
    leverage: int,
) -> dict:
    """
    Run backtest on historical data.

    Args:
        df: DataFrame with OHLCV data
        strategy: Strategy instance
        initial_balance: Initial balance
        leverage: Leverage to use

    Returns:
        Dictionary with backtest results
    """
    logger.info("Starting backtest", candles=len(df))

    balance = initial_balance
    position = None
    trades = []
    equity_curve = []

    min_candles = strategy.min_candles_required

    for i in range(min_candles, len(df)):
        current_df = df.iloc[:i + 1]
        current_price = current_df["close"].iloc[-1]
        current_time = current_df.index[-1]

        # Generate signal
        signal = strategy.process(current_df, "BTCUSDT")

        # Process signal
        if signal.is_entry and position is None:
            # Open position
            risk_amount = balance * (strategy.config.risk_per_trade / 100)
            stop_distance = abs(current_price - signal.stop_loss) / current_price if signal.stop_loss else 0.02
            position_value = (risk_amount / stop_distance) * leverage if stop_distance > 0 else 0
            quantity = position_value / current_price

            position = {
                "side": "long" if signal.is_long else "short",
                "entry_price": current_price,
                "quantity": quantity,
                "stop_loss": signal.stop_loss,
                "take_profit": signal.take_profit,
                "entry_time": current_time,
            }

        elif position is not None:
            # Check for exit
            should_exit = False
            exit_reason = ""

            # Check stop-loss
            if position["stop_loss"]:
                if position["side"] == "long" and current_price <= position["stop_loss"]:
                    should_exit = True
                    exit_reason = "stop_loss"
                elif position["side"] == "short" and current_price >= position["stop_loss"]:
                    should_exit = True
                    exit_reason = "stop_loss"

            # Check take-profit
            if position["take_profit"] and not should_exit:
                if position["side"] == "long" and current_price >= position["take_profit"]:
                    should_exit = True
                    exit_reason = "take_profit"
                elif position["side"] == "short" and current_price <= position["take_profit"]:
                    should_exit = True
                    exit_reason = "take_profit"

            # Check signal reversal
            if not should_exit:
                should_close, reason = strategy.should_close_position(
                    position["side"],
                    current_df,
                )
                if should_close:
                    should_exit = True
                    exit_reason = "signal"

            if should_exit:
                # Calculate PnL
                if position["side"] == "long":
                    pnl = (current_price - position["entry_price"]) / position["entry_price"]
                else:
                    pnl = (position["entry_price"] - current_price) / position["entry_price"]

                pnl_amount = position["quantity"] * position["entry_price"] * pnl
                balance += pnl_amount

                trades.append({
                    "entry_time": position["entry_time"],
                    "exit_time": current_time,
                    "side": position["side"],
                    "entry_price": position["entry_price"],
                    "exit_price": current_price,
                    "quantity": position["quantity"],
                    "pnl": pnl_amount,
                    "pnl_percent": pnl * 100,
                    "exit_reason": exit_reason,
                })

                position = None

        # Record equity
        equity = balance
        if position:
            if position["side"] == "long":
                unrealized = (current_price - position["entry_price"]) / position["entry_price"]
            else:
                unrealized = (position["entry_price"] - current_price) / position["entry_price"]
            equity += position["quantity"] * position["entry_price"] * unrealized

        equity_curve.append({
            "timestamp": current_time,
            "equity": equity,
            "balance": balance,
        })

    # Calculate metrics
    if trades:
        winning_trades = [t for t in trades if t["pnl"] > 0]
        losing_trades = [t for t in trades if t["pnl"] <= 0]

        total_pnl = sum(t["pnl"] for t in trades)
        win_rate = len(winning_trades) / len(trades) * 100 if trades else 0
        avg_win = sum(t["pnl"] for t in winning_trades) / len(winning_trades) if winning_trades else 0
        avg_loss = sum(t["pnl"] for t in losing_trades) / len(losing_trades) if losing_trades else 0
        profit_factor = abs(sum(t["pnl"] for t in winning_trades) / sum(t["pnl"] for t in losing_trades)) if losing_trades and sum(t["pnl"] for t in losing_trades) != 0 else 0

        # Calculate max drawdown
        equity_series = pd.Series([e["equity"] for e in equity_curve])
        rolling_max = equity_series.expanding().max()
        drawdown = (equity_series - rolling_max) / rolling_max * 100
        max_drawdown = drawdown.min()
    else:
        total_pnl = 0
        win_rate = 0
        avg_win = 0
        avg_loss = 0
        profit_factor = 0
        max_drawdown = 0

    results = {
        "initial_balance": initial_balance,
        "final_balance": balance,
        "total_pnl": total_pnl,
        "total_return": (balance - initial_balance) / initial_balance * 100,
        "total_trades": len(trades),
        "winning_trades": len([t for t in trades if t["pnl"] > 0]),
        "losing_trades": len([t for t in trades if t["pnl"] <= 0]),
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor": profit_factor,
        "max_drawdown": max_drawdown,
        "trades": trades,
        "equity_curve": equity_curve,
    }

    return results


def print_results(results: dict):
    """Print backtest results."""
    print("\n" + "=" * 60)
    print("AILA BACKTEST RESULTS")
    print("=" * 60)
    print(f"\nInitial Balance: ${results['initial_balance']:,.2f}")
    print(f"Final Balance:   ${results['final_balance']:,.2f}")
    print(f"Total PnL:       ${results['total_pnl']:,.2f}")
    print(f"Total Return:    {results['total_return']:.2f}%")
    print(f"\nTotal Trades:    {results['total_trades']}")
    print(f"Winning Trades:  {results['winning_trades']}")
    print(f"Losing Trades:   {results['losing_trades']}")
    print(f"Win Rate:        {results['win_rate']:.1f}%")
    print(f"\nAvg Win:         ${results['avg_win']:,.2f}")
    print(f"Avg Loss:        ${results['avg_loss']:,.2f}")
    print(f"Profit Factor:   {results['profit_factor']:.2f}")
    print(f"Max Drawdown:    {results['max_drawdown']:.2f}%")
    print("=" * 60)


def main():
    """Main entry point."""
    args = parse_args()

    logger.info(
        "AILA Backtest Runner",
        symbol=args.symbol,
        timeframe=args.timeframe,
    )

    # Parse dates
    if args.start and args.end:
        start_date = datetime.strptime(args.start, "%Y-%m-%d")
        end_date = datetime.strptime(args.end, "%Y-%m-%d")
    else:
        end_date = datetime.utcnow()
        start_date = end_date - timedelta(days=args.days)

    # Load data
    df = load_historical_data(
        symbol=args.symbol,
        timeframe=args.timeframe,
        start_date=start_date,
        end_date=end_date,
    )

    if df.empty:
        logger.error("No data loaded")
        return 1

    # Create strategy
    config = TripleSuperTrendConfig(
        timeframe=args.timeframe,
        trading_pairs=[args.symbol],
    )
    strategy = TripleSuperTrendStrategy(config)

    # Run backtest
    results = run_backtest(
        df=df,
        strategy=strategy,
        initial_balance=args.initial_balance,
        leverage=args.leverage,
    )

    # Print results
    print_results(results)

    # Save to file if specified
    if args.output:
        trades_df = pd.DataFrame(results["trades"])
        trades_df.to_csv(args.output, index=False)
        logger.info("Results saved", output=args.output)

    return 0


if __name__ == "__main__":
    sys.exit(main())
