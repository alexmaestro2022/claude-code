"""
AILA - Trade Executor

Handles trade execution with proper error handling and retries.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

import structlog

from ..core.strategy import Signal
from ..exchange import BybitClient, FuturesTrader, SpotTrader
from ..exchange.models import AccountType, Order, OrderType

logger = structlog.get_logger(__name__)


@dataclass
class ExecutionResult:
    """Result of trade execution."""

    success: bool
    order: Optional[Order] = None
    error: Optional[str] = None
    slippage: float = 0.0
    execution_time_ms: int = 0


class TradeExecutor:
    """
    Trade execution with error handling.

    Handles the actual order placement with:
    - Retry logic
    - Slippage calculation
    - Error handling

    Example:
        executor = TradeExecutor(client)

        result = executor.execute_entry(
            signal=signal,
            quantity=Decimal("0.001"),
        )

        if result.success:
            print(f"Order filled at {result.order.average_price}")
    """

    def __init__(
        self,
        client: BybitClient,
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ):
        """
        Initialize executor.

        Args:
            client: Configured Bybit client
            max_retries: Maximum retry attempts
            retry_delay: Delay between retries in seconds
        """
        self.client = client
        self.max_retries = max_retries
        self.retry_delay = retry_delay

        # Initialize appropriate trader
        if client.config.account_type == AccountType.FUTURES:
            self.trader = FuturesTrader(client)
        else:
            self.trader = SpotTrader(client)

    def execute_entry(
        self,
        signal: Signal,
        quantity: Decimal,
        use_limit: bool = False,
        limit_offset_pct: float = 0.1,
    ) -> ExecutionResult:
        """
        Execute entry order.

        Args:
            signal: Trading signal
            quantity: Position size
            use_limit: Use limit order instead of market
            limit_offset_pct: Offset for limit price

        Returns:
            ExecutionResult
        """
        import time

        start_time = time.time()

        for attempt in range(self.max_retries):
            try:
                order = self._place_entry_order(
                    signal=signal,
                    quantity=quantity,
                    use_limit=use_limit,
                    limit_offset_pct=limit_offset_pct,
                )

                if order:
                    execution_time = int((time.time() - start_time) * 1000)
                    slippage = self._calculate_slippage(
                        signal.price,
                        float(order.average_price or order.price or signal.price),
                        signal.is_long,
                    )

                    return ExecutionResult(
                        success=True,
                        order=order,
                        slippage=slippage,
                        execution_time_ms=execution_time,
                    )

            except Exception as e:
                logger.warning(
                    "Execution attempt failed",
                    attempt=attempt + 1,
                    error=str(e),
                )

                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay)

        execution_time = int((time.time() - start_time) * 1000)
        return ExecutionResult(
            success=False,
            error="Max retries exceeded",
            execution_time_ms=execution_time,
        )

    def execute_exit(
        self,
        symbol: str,
        side: str,
        quantity: Optional[Decimal] = None,
        reason: str = "signal",
    ) -> ExecutionResult:
        """
        Execute exit order.

        Args:
            symbol: Trading pair symbol
            side: Position side ('long' or 'short')
            quantity: Quantity to close (closes all if not specified)
            reason: Exit reason for logging

        Returns:
            ExecutionResult
        """
        import time

        start_time = time.time()

        for attempt in range(self.max_retries):
            try:
                if isinstance(self.trader, FuturesTrader):
                    from ..exchange.models import PositionSide

                    pos_side = PositionSide.LONG if side == "long" else PositionSide.SHORT
                    order = self.trader.close_position(
                        symbol=symbol,
                        side=pos_side,
                        percent=100 if quantity is None else None,
                    )
                else:
                    if quantity:
                        order = self.trader.sell(symbol=symbol, quantity=quantity)
                    else:
                        order = self.trader.sell_all(symbol=symbol)

                if order:
                    execution_time = int((time.time() - start_time) * 1000)

                    return ExecutionResult(
                        success=True,
                        order=order,
                        execution_time_ms=execution_time,
                    )

            except Exception as e:
                logger.warning(
                    "Exit attempt failed",
                    attempt=attempt + 1,
                    error=str(e),
                )

                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay)

        execution_time = int((time.time() - start_time) * 1000)
        return ExecutionResult(
            success=False,
            error="Max retries exceeded",
            execution_time_ms=execution_time,
        )

    def execute_partial_exit(
        self,
        symbol: str,
        side: str,
        percent: float,
    ) -> ExecutionResult:
        """
        Execute partial position exit.

        Args:
            symbol: Trading pair symbol
            side: Position side
            percent: Percentage to close (0-100)

        Returns:
            ExecutionResult
        """
        import time

        start_time = time.time()

        try:
            if isinstance(self.trader, FuturesTrader):
                from ..exchange.models import PositionSide

                pos_side = PositionSide.LONG if side == "long" else PositionSide.SHORT
                order = self.trader.close_position(
                    symbol=symbol,
                    side=pos_side,
                    percent=percent,
                )
            else:
                order = self.trader.sell(symbol=symbol, percent=percent)

            execution_time = int((time.time() - start_time) * 1000)

            if order:
                return ExecutionResult(
                    success=True,
                    order=order,
                    execution_time_ms=execution_time,
                )
            else:
                return ExecutionResult(
                    success=False,
                    error="Order not created",
                    execution_time_ms=execution_time,
                )

        except Exception as e:
            execution_time = int((time.time() - start_time) * 1000)
            return ExecutionResult(
                success=False,
                error=str(e),
                execution_time_ms=execution_time,
            )

    def _place_entry_order(
        self,
        signal: Signal,
        quantity: Decimal,
        use_limit: bool,
        limit_offset_pct: float,
    ) -> Optional[Order]:
        """Place entry order based on signal."""
        if isinstance(self.trader, FuturesTrader):
            if signal.is_long:
                return self.trader.open_long(
                    symbol=signal.symbol,
                    quantity=quantity,
                    stop_loss=Decimal(str(signal.stop_loss)) if signal.stop_loss else None,
                    take_profit=Decimal(str(signal.take_profit)) if signal.take_profit else None,
                )
            else:
                return self.trader.open_short(
                    symbol=signal.symbol,
                    quantity=quantity,
                    stop_loss=Decimal(str(signal.stop_loss)) if signal.stop_loss else None,
                    take_profit=Decimal(str(signal.take_profit)) if signal.take_profit else None,
                )
        else:
            if signal.is_long:
                return self.trader.buy(
                    symbol=signal.symbol,
                    quantity=quantity,
                )
            else:
                return self.trader.sell(
                    symbol=signal.symbol,
                    quantity=quantity,
                )

    def _calculate_slippage(
        self,
        expected_price: float,
        actual_price: float,
        is_long: bool,
    ) -> float:
        """Calculate slippage percentage."""
        if expected_price == 0:
            return 0.0

        slippage = ((actual_price - expected_price) / expected_price) * 100

        # Positive slippage is bad for longs, negative for shorts
        if not is_long:
            slippage = -slippage

        return slippage

    def update_stop_loss(
        self,
        symbol: str,
        new_stop: Decimal,
    ) -> bool:
        """
        Update stop-loss for existing position.

        Args:
            symbol: Trading pair symbol
            new_stop: New stop-loss price

        Returns:
            True if updated successfully
        """
        if isinstance(self.trader, FuturesTrader):
            return self.trader.update_stop_loss(symbol, new_stop)
        return False

    def update_take_profit(
        self,
        symbol: str,
        new_tp: Decimal,
    ) -> bool:
        """
        Update take-profit for existing position.

        Args:
            symbol: Trading pair symbol
            new_tp: New take-profit price

        Returns:
            True if updated successfully
        """
        if isinstance(self.trader, FuturesTrader):
            return self.trader.update_take_profit(symbol, new_tp)
        return False

    def __repr__(self) -> str:
        return f"TradeExecutor(max_retries={self.max_retries})"
