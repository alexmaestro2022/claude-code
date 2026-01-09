"""
AILA - Custom Exceptions

All custom exceptions used throughout the application.
"""


class AILAError(Exception):
    """Base exception for AILA."""

    def __init__(self, message: str, code: str = "AILA_ERROR"):
        self.message = message
        self.code = code
        super().__init__(self.message)


class ConfigurationError(AILAError):
    """Configuration related errors."""

    def __init__(self, message: str, config_key: str = ""):
        self.config_key = config_key
        super().__init__(
            message=message,
            code="CONFIG_ERROR",
        )


class ExchangeError(AILAError):
    """Exchange API related errors."""

    def __init__(
        self,
        message: str,
        exchange: str = "bybit",
        response_code: int = 0,
    ):
        self.exchange = exchange
        self.response_code = response_code
        super().__init__(
            message=message,
            code="EXCHANGE_ERROR",
        )


class OrderError(AILAError):
    """Order related errors."""

    def __init__(
        self,
        message: str,
        symbol: str = "",
        order_id: str = "",
    ):
        self.symbol = symbol
        self.order_id = order_id
        super().__init__(
            message=message,
            code="ORDER_ERROR",
        )


class InsufficientBalanceError(AILAError):
    """Insufficient balance for operation."""

    def __init__(
        self,
        message: str,
        required: float = 0,
        available: float = 0,
    ):
        self.required = required
        self.available = available
        super().__init__(
            message=message,
            code="INSUFFICIENT_BALANCE",
        )


class StrategyError(AILAError):
    """Strategy related errors."""

    def __init__(
        self,
        message: str,
        strategy_name: str = "",
    ):
        self.strategy_name = strategy_name
        super().__init__(
            message=message,
            code="STRATEGY_ERROR",
        )


class RiskLimitError(AILAError):
    """Risk limit exceeded."""

    def __init__(
        self,
        message: str,
        limit_type: str = "",
        current_value: float = 0,
        limit_value: float = 0,
    ):
        self.limit_type = limit_type
        self.current_value = current_value
        self.limit_value = limit_value
        super().__init__(
            message=message,
            code="RISK_LIMIT",
        )


class DataError(AILAError):
    """Data related errors."""

    def __init__(
        self,
        message: str,
        symbol: str = "",
    ):
        self.symbol = symbol
        super().__init__(
            message=message,
            code="DATA_ERROR",
        )


class WebSocketError(AILAError):
    """WebSocket connection errors."""

    def __init__(
        self,
        message: str,
        url: str = "",
    ):
        self.url = url
        super().__init__(
            message=message,
            code="WEBSOCKET_ERROR",
        )
