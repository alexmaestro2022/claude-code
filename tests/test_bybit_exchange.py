"""Tests for BybitExchange class."""

import pytest
import math
from unittest.mock import MagicMock, patch, AsyncMock

# Add project root to path
import sys
sys.path.insert(0, "/opt/aila")

from aila.ai_trade.exchanges.bybit_exchange import BybitExchange


class TestBybitExchangeMethods:
    """Test that all required methods exist."""

    def setup_method(self):
        """Setup test fixtures."""
        with patch("aila.ai_trade.exchanges.bybit_exchange.HTTP"):
            self.exchange = BybitExchange()

    def test_has_market_data_methods(self):
        """Test market data methods exist."""
        assert hasattr(self.exchange, "get_ticker")
        assert hasattr(self.exchange, "fetch_ticker")
        assert hasattr(self.exchange, "get_orderbook")
        assert hasattr(self.exchange, "fetch_order_book")
        assert hasattr(self.exchange, "get_klines")
        assert hasattr(self.exchange, "fetch_ohlcv")
        assert hasattr(self.exchange, "fetch_tickers")
        assert hasattr(self.exchange, "get_funding_rate")
        assert hasattr(self.exchange, "get_usdt_perpetual_symbols")

    def test_has_account_methods(self):
        """Test account methods exist."""
        assert hasattr(self.exchange, "get_balance")
        assert hasattr(self.exchange, "get_positions")
        assert hasattr(self.exchange, "get_position")

    def test_has_order_methods(self):
        """Test order methods exist."""
        assert hasattr(self.exchange, "create_order")
        assert hasattr(self.exchange, "create_market_order")
        assert hasattr(self.exchange, "place_order")
        assert hasattr(self.exchange, "cancel_order")
        assert hasattr(self.exchange, "cancel_all_orders")
        assert hasattr(self.exchange, "get_open_orders")
        assert hasattr(self.exchange, "fetch_open_orders")

    def test_has_position_methods(self):
        """Test position methods exist."""
        assert hasattr(self.exchange, "set_leverage")
        assert hasattr(self.exchange, "close_position")
        assert hasattr(self.exchange, "close_all_positions")

    def test_has_helper_methods(self):
        """Test helper methods exist."""
        assert hasattr(self.exchange, "get_instrument_info")
        assert hasattr(self.exchange, "get_qty_precision")
        assert hasattr(self.exchange, "round_qty")
        assert hasattr(self.exchange, "get_price_precision")
        assert hasattr(self.exchange, "round_price")
        assert hasattr(self.exchange, "normalize_symbol")


class TestNormalizeSymbol:
    """Test symbol normalization."""

    def setup_method(self):
        """Setup test fixtures."""
        with patch("aila.ai_trade.exchanges.bybit_exchange.HTTP"):
            self.exchange = BybitExchange()

    def test_normalize_ccxt_format(self):
        """Test normalizing ccxt format (BTC/USDT -> BTCUSDT)."""
        assert self.exchange.normalize_symbol("BTC/USDT") == "BTCUSDT"
        assert self.exchange.normalize_symbol("ETH/USDT") == "ETHUSDT"
        assert self.exchange.normalize_symbol("JTO/USDT") == "JTOUSDT"

    def test_normalize_already_bybit_format(self):
        """Test normalizing already Bybit format (BTCUSDT -> BTCUSDT)."""
        assert self.exchange.normalize_symbol("BTCUSDT") == "BTCUSDT"
        assert self.exchange.normalize_symbol("ETHUSDT") == "ETHUSDT"

    def test_normalize_empty_string(self):
        """Test normalizing empty string."""
        assert self.exchange.normalize_symbol("") == ""


class TestRoundQty:
    """Test quantity rounding."""

    def setup_method(self):
        """Setup test fixtures."""
        with patch("aila.ai_trade.exchanges.bybit_exchange.HTTP"):
            self.exchange = BybitExchange()
            # Mock instrument info
            self.exchange._instruments_cache = {
                "BTCUSDT": {"qtyStep": 0.001, "minQty": 0.001, "maxQty": 100},
                "JTOUSDT": {"qtyStep": 0.01, "minQty": 0.01, "maxQty": 10000},
                "PEPEUSDT": {"qtyStep": 1, "minQty": 1, "maxQty": 1000000000},
            }

    @pytest.mark.asyncio
    async def test_round_qty_btc(self):
        """Test rounding BTC quantity (qtyStep=0.001)."""
        result = await self.exchange.round_qty("BTC/USDT", 0.12345678)
        assert result == 0.123

    @pytest.mark.asyncio
    async def test_round_qty_jto(self):
        """Test rounding JTO quantity (qtyStep=0.01)."""
        result = await self.exchange.round_qty("JTO/USDT", 26.157467)
        assert result == 26.15

    @pytest.mark.asyncio
    async def test_round_qty_pepe(self):
        """Test rounding PEPE quantity (qtyStep=1)."""
        result = await self.exchange.round_qty("PEPE/USDT", 1234567.89)
        assert result == 1234567

    @pytest.mark.asyncio
    async def test_round_qty_enforces_minimum(self):
        """Test that round_qty enforces minimum quantity."""
        result = await self.exchange.round_qty("BTC/USDT", 0.0001)
        assert result == 0.001  # minQty

    @pytest.mark.asyncio
    async def test_round_qty_floors_down(self):
        """Test that round_qty floors down (not rounds)."""
        result = await self.exchange.round_qty("BTC/USDT", 0.1239)
        assert result == 0.123  # Not 0.124


class TestRoundPrice:
    """Test price rounding."""

    def setup_method(self):
        """Setup test fixtures."""
        with patch("aila.ai_trade.exchanges.bybit_exchange.HTTP"):
            self.exchange = BybitExchange()
            # Mock instrument info
            self.exchange._instruments_cache = {
                "BTCUSDT": {"tickSize": 0.01, "qtyStep": 0.001, "minQty": 0.001},
                "ETHUSDT": {"tickSize": 0.01, "qtyStep": 0.001, "minQty": 0.001},
                "PEPEUSDT": {"tickSize": 0.00000001, "qtyStep": 1, "minQty": 1},
            }

    @pytest.mark.asyncio
    async def test_round_price_btc(self):
        """Test rounding BTC price (tickSize=0.01)."""
        result = await self.exchange.round_price("BTC/USDT", 100000.123)
        assert result == 100000.12

    @pytest.mark.asyncio
    async def test_round_price_pepe(self):
        """Test rounding PEPE price (tickSize=0.00000001)."""
        result = await self.exchange.round_price("PEPE/USDT", 0.000012345)
        assert result == 0.00001234 or result == 0.00001235  # Rounding


class TestInstrumentsCache:
    """Test instruments caching."""

    def setup_method(self):
        """Setup test fixtures."""
        with patch("aila.ai_trade.exchanges.bybit_exchange.HTTP") as mock_http:
            self.mock_client = MagicMock()
            mock_http.return_value = self.mock_client
            self.exchange = BybitExchange()

    @pytest.mark.asyncio
    async def test_cache_is_used(self):
        """Test that cached instrument info is used."""
        # Pre-populate cache
        self.exchange._instruments_cache["BTCUSDT"] = {
            "qtyStep": 0.001,
            "minQty": 0.001,
            "maxQty": 100,
            "tickSize": 0.01,
        }

        # Call method - should use cache, not API
        result = await self.exchange.get_instrument_info("BTC/USDT")

        # Verify cache was used (no API call)
        self.mock_client.get_instruments_info.assert_not_called()
        assert result["qtyStep"] == 0.001


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
