"""Base exchange class — abstract interface for all exchanges."""

from abc import ABC, abstractmethod
from typing import Optional


class BaseExchange(ABC):
    """Abstract exchange interface."""

    __slots__ = ("_name", "_api_key", "_api_secret", "_testnet")

    def __init__(
        self, name: str, api_key: str = "", api_secret: str = "", testnet: bool = False
    ) -> None:
        self._name = name
        self._api_key = api_key
        self._api_secret = api_secret
        self._testnet = testnet

    @property
    def name(self) -> str:
        return self._name

    @abstractmethod
    async def get_ticker(self, symbol: str) -> dict:
        """Get current price ticker."""

    @abstractmethod
    async def get_orderbook(self, symbol: str, limit: int = 20) -> dict:
        """Get order book."""

    @abstractmethod
    async def get_balance(self, currency: str = "USDT") -> float:
        """Get balance for currency."""

    @abstractmethod
    async def place_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float,
        price: Optional[float] = None,
    ) -> dict:
        """Place an order."""

    @abstractmethod
    async def cancel_order(self, symbol: str, order_id: str) -> bool:
        """Cancel an order."""

    @abstractmethod
    async def get_positions(self) -> list[dict]:
        """Get open positions."""

    @abstractmethod
    async def get_funding_rate(self, symbol: str) -> float:
        """Get funding rate for symbol."""
