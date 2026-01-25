"""
BACKTEST — strategy testing on historical data.
"""

from dataclasses import dataclass, field


@dataclass
class BacktestResult:
    """Backtest result."""

    strategy_name: str
    pair: str
    timeframe: str
    start_date: str
    end_date: str
    initial_balance: float
    final_balance: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    profit_factor: float
    max_drawdown: float
    sharpe_ratio: float
    total_return_pct: float
    trades: list = field(default_factory=list)


class Backtester:
    """Strategy backtester."""

    __slots__ = ['_claude_client', '_knowledge_base', '_results']

    def __init__(self, claude_client, knowledge_base):
        self._claude_client = claude_client
        self._knowledge_base = knowledge_base
        self._results: list[BacktestResult] = []

    async def run_backtest(
        self,
        strategy_name: str,
        strategy_params: dict,
        pair: str,
        timeframe: str,
        start_date: str,
        end_date: str,
        initial_balance: float = 1000
    ) -> BacktestResult:
        """Run strategy backtest."""
        prompt = (
            "Run strategy backtest on historical data:\n\n"
            f"STRATEGY: {strategy_name}\n"
            f"PARAMETERS: {strategy_params}\n"
            f"PAIR: {pair}\n"
            f"TIMEFRAME: {timeframe}\n"
            f"PERIOD: {start_date} - {end_date}\n"
            f"INITIAL BALANCE: ${initial_balance}\n\n"
            "Simulate trading by strategy rules.\n"
            "Account for 0.1% commission per trade.\n\n"
            "RETURN ONLY JSON:\n"
            "{\n"
            '    "final_balance": number,\n'
            '    "total_trades": number,\n'
            '    "winning_trades": number,\n'
            '    "losing_trades": number,\n'
            '    "win_rate": number,\n'
            '    "profit_factor": number,\n'
            '    "max_drawdown_pct": number,\n'
            '    "sharpe_ratio": number,\n'
            '    "total_return_pct": number,\n'
            '    "best_trade_pct": number,\n'
            '    "worst_trade_pct": number,\n'
            '    "avg_trade_duration": "string",\n'
            '    "trades_sample": [\n'
            '        {"entry": number, "exit": number, "pnl_pct": number, "direction": "long/short"}\n'
            "    ]\n"
            "}"
        )

        result_data = await self._claude_client.analyze(prompt)

        result = BacktestResult(
            strategy_name=strategy_name,
            pair=pair,
            timeframe=timeframe,
            start_date=start_date,
            end_date=end_date,
            initial_balance=initial_balance,
            final_balance=result_data.get('final_balance', initial_balance),
            total_trades=result_data.get('total_trades', 0),
            winning_trades=result_data.get('winning_trades', 0),
            losing_trades=result_data.get('losing_trades', 0),
            win_rate=result_data.get('win_rate', 0),
            profit_factor=result_data.get('profit_factor', 0),
            max_drawdown=result_data.get('max_drawdown_pct', 0),
            sharpe_ratio=result_data.get('sharpe_ratio', 0),
            total_return_pct=result_data.get('total_return_pct', 0),
            trades=result_data.get('trades_sample', [])
        )

        self._results.append(result)
        return result

    async def compare_strategies(
        self,
        strategies: list[dict],
        pair: str,
        timeframe: str,
        start_date: str,
        end_date: str
    ) -> list[BacktestResult]:
        """Compare multiple strategies."""
        results: list[BacktestResult] = []

        for strategy in strategies:
            result = await self.run_backtest(
                strategy_name=strategy['name'],
                strategy_params=strategy['params'],
                pair=pair,
                timeframe=timeframe,
                start_date=start_date,
                end_date=end_date
            )
            results.append(result)

        results.sort(key=lambda x: x.total_return_pct, reverse=True)
        return results

    def get_results(self) -> list[BacktestResult]:
        """Get all backtest results."""
        return self._results
