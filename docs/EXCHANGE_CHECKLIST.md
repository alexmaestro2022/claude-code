# BybitExchange Method Checklist

> **ВАЖНО**: Перед изменением bybit_exchange.py — проверить что все методы реализованы!

## Market Data Methods

| Method | Status | Description |
|--------|--------|-------------|
| `get_ticker(symbol)` | ✅ | Get current price ticker |
| `fetch_ticker(symbol)` | ✅ | Alias for get_ticker (ccxt-compatible) |
| `get_orderbook(symbol, limit=25)` | ✅ | Get order book |
| `fetch_order_book(symbol, limit=25)` | ✅ | Alias for get_orderbook (ccxt-compatible) |
| `get_klines(symbol, timeframe, limit)` | ✅ | Get OHLCV candles (Bybit-style) |
| `fetch_ohlcv(symbol, timeframe, limit)` | ✅ | Get OHLCV candles (ccxt-compatible) |
| `fetch_tickers()` | ✅ | Get all tickers |
| `get_funding_rate(symbol)` | ✅ | Get funding rate |
| `get_usdt_perpetual_symbols()` | ✅ | Get all USDT perpetual symbols |

## Account Methods

| Method | Status | Description |
|--------|--------|-------------|
| `get_balance(currency="USDT")` | ✅ | Get wallet balance |
| `get_positions()` | ✅ | Get all open positions |
| `get_position(symbol)` | ✅ | Get single position by symbol |

## Order Methods

| Method | Status | Description |
|--------|--------|-------------|
| `create_order(symbol, type, side, amount, price, params)` | ✅ | Create order (ccxt-compatible) |
| `create_market_order(symbol, side, amount, params)` | ✅ | Create market order |
| `place_order(symbol, side, type, qty, price)` | ✅ | Place order (Bybit-style) |
| `cancel_order(order_id, symbol)` | ✅ | Cancel single order |
| `cancel_all_orders()` | ✅ | Cancel all open orders |
| `get_open_orders(symbol=None)` | ✅ | Get open orders (Bybit-style) |
| `fetch_open_orders(symbol=None)` | ✅ | Get open orders (ccxt-compatible) |

## Position Methods

| Method | Status | Description |
|--------|--------|-------------|
| `set_leverage(leverage, symbol)` | ✅ | Set leverage for symbol |
| `close_position(symbol, side, size)` | ✅ | Close single position |
| `close_all_positions()` | ✅ | Close all positions |

## Helper Methods

| Method | Status | Description |
|--------|--------|-------------|
| `get_instrument_info(symbol)` | ✅ | Get instrument info (qtyStep, tickSize, etc.) |
| `get_qty_precision(symbol)` | ✅ | Get quantity step |
| `round_qty(symbol, qty)` | ✅ | Round qty to valid precision |
| `get_price_precision(symbol)` | ✅ | Get price tick size |
| `round_price(symbol, price)` | ✅ | Round price to valid precision |
| `normalize_symbol(symbol)` | ✅ | Convert BTC/USDT → BTCUSDT |

---

## Usage Example

```python
from aila.ai_trade.exchanges.bybit_exchange import BybitExchange

exchange = BybitExchange(api_key="...", api_secret="...")

# Get precision and round values
qty_step = await exchange.get_qty_precision("BTC/USDT")  # 0.001
rounded_qty = await exchange.round_qty("BTC/USDT", 0.12345678)  # 0.123

tick_size = await exchange.get_price_precision("BTC/USDT")  # 0.01
rounded_price = await exchange.round_price("BTC/USDT", 100000.123)  # 100000.12

# Normalize symbol
bybit_symbol = exchange.normalize_symbol("BTC/USDT")  # "BTCUSDT"
```

---

## Adding New Methods

When adding new methods to BybitExchange:

1. **Add to this checklist** with status and description
2. **Add type hints** for all parameters and return values
3. **Add docstring** explaining what the method does
4. **Add `@retry_async` decorator** for API calls
5. **Normalize symbol** using `normalize_symbol()` helper
6. **Add test** in `tests/test_bybit_exchange.py`
7. **Update CLAUDE_MEMO.md** if method is critical

---

**Last updated:** 2026-01-28
