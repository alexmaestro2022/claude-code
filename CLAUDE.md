# CLAUDE.md - AI Trade Code Quality Rules

This file is read automatically by Claude Code at the start of each session.

## Code Quality Rules (ОБЯЗАТЕЛЬНО к выполнению)

### Правило 1: Чеклист методов BybitExchange

Перед любым изменением exchange проверить что ВСЕ методы реализованы:

**Market Data:**
- `get_ticker(symbol)` - получить текущую цену
- `fetch_ticker(symbol)` - alias для get_ticker
- `get_orderbook(symbol, limit)` - получить стакан
- `fetch_order_book(symbol, limit)` - alias для get_orderbook
- `get_klines(symbol, timeframe, limit)` - получить свечи
- `fetch_ohlcv(symbol, timeframe, limit)` - alias для get_klines
- `fetch_tickers()` - получить все тикеры
- `get_funding_rate(symbol)` - ставка финансирования
- `get_usdt_perpetual_symbols()` - список USDT perpetual пар

**Account:**
- `get_balance(currency)` - баланс кошелька
- `get_positions()` - все открытые позиции
- `get_position(symbol)` - позиция по символу

**Orders:**
- `create_order(symbol, type, side, amount, price, params)` - создать ордер
- `create_market_order(symbol, side, amount, params)` - маркет ордер
- `place_order(symbol, side, type, qty, price)` - разместить ордер
- `cancel_order(order_id, symbol)` - отменить ордер
- `cancel_all_orders()` - отменить все ордера
- `get_open_orders(symbol)` - открытые ордера
- `fetch_open_orders(symbol)` - alias для get_open_orders

**Position Management:**
- `set_leverage(leverage, symbol)` - установить плечо
- `close_position(symbol, side, size)` - закрыть позицию
- `close_all_positions()` - закрыть все позиции

**Helpers:**
- `get_instrument_info(symbol)` - информация об инструменте
- `get_qty_precision(symbol)` - получить qtyStep
- `round_qty(symbol, qty)` - округлить количество
- `get_price_precision(symbol)` - получить tickSize
- `round_price(symbol, price)` - округлить цену
- `normalize_symbol(symbol)` - BTC/USDT → BTCUSDT

### Правило 2: Округление qty и price

**ВСЕГДА** округлять qty и price перед созданием ордера:

```python
# Правильно
qty = await exchange.round_qty(symbol, raw_qty)
price = await exchange.round_price(symbol, raw_price)
order = await exchange.create_market_order(symbol, side, qty)

# Неправильно
order = await exchange.create_market_order(symbol, side, raw_qty)  # ❌ Ошибка Qty invalid
```

### Правило 3: Нормализация символов

**ВСЕГДА** нормализовать символы перед API вызовами:

```python
# ccxt формат: BTC/USDT
# Bybit формат: BTCUSDT

symbol = exchange.normalize_symbol(symbol)  # BTC/USDT → BTCUSDT
```

### Правило 4: Логирование Claude API

При **ЛЮБОМ** вызове Claude API логировать в api_usage.log:

```python
result = await self.claude_client.analyze(
    prompt,
    use_haiku=True,
    agent="TRADER",           # TRADER, REVIEWER, NEWS, WHALE, PREDICTOR, MENTOR
    action="batch_analyze",   # analyze, validate, sentiment, signal
    context="pairs=50",       # pair=BTCUSDT, type=market, count=10
)
```

Формат лога:
```
[2026-01-27 12:00:00] TRADER batch_analyze | model=sonnet | input=3500 | output=800 | cost=$0.0225 | pairs=50
```

### Правило 5: Обработка ошибок

**ВСЕГДА** оборачивать внешние вызовы в try/except:

```python
# API биржи
try:
    result = await self._exchange.create_market_order(symbol, side, qty)
except Exception as e:
    logger.error(f"Order failed: {e}")
    return None

# Claude API
try:
    analysis = await self.claude_client.analyze(prompt)
except Exception as e:
    logger.error(f"Claude API error: {e}")
    return {"error": str(e)}
```

### Правило 6: Тестирование перед деплоем

Перед коммитом **ОБЯЗАТЕЛЬНО** запустить тесты:

```bash
cd /opt/aila && python -m pytest tests/test_bybit_exchange.py -v
```

### Правило 7: Stage логирование в Autopilot

При прохождении каждого этапа autopilot логировать с префиксом [STAGE]:

```python
logger.info(f"[STAGE 1] Trader scanning for opportunities...")
logger.info(f"[STAGE 2] Sending to Reviewer: {pair}")
logger.info(f"[STAGE 3] Risk guard validating {pair}")
logger.info(f"[STAGE 4] Getting market confirmations for {pair}")
logger.info(f"[STAGE 5] All validations passed for {pair}")
```

### Правило 8: Цветные метки логов для сделок

```python
logger.warning(f"[READY_TO_TRADE] {symbol} {side} @ {price} - all checks passed")
logger.info(f"[TRADE_OPENED] {symbol} {side} @ {price}")
logger.error(f"[TRADE_FAILED] {symbol} {side} - {error}")
```

### Правило 9: Retry декоратор для API

Все методы с API вызовами должны использовать `@retry_async`:

```python
from ..utils.common import retry_async

@retry_async(max_attempts=2)
async def get_ticker(self, symbol: str) -> dict:
    ...
```

### Правило 10: Type hints и docstrings

**ВСЕГДА** добавлять type hints и docstrings:

```python
async def round_qty(self, symbol: str, qty: float) -> float:
    """Round quantity to valid precision for Bybit.

    Args:
        symbol: Trading pair (e.g., "BTC/USDT" or "BTCUSDT")
        qty: Raw quantity to round

    Returns:
        Rounded quantity that meets Bybit's qtyStep requirements
    """
    ...
```

## Документация

- **Методы exchange**: `/opt/aila/docs/EXCHANGE_CHECKLIST.md`
- **API агентов**: `/opt/aila/docs/AI_TRADE_API.md`
- **Общая информация**: `/opt/aila/CLAUDE_MEMO.md`

## Тесты

Расположение: `/opt/aila/tests/`

Запуск:
```bash
cd /opt/aila && python -m pytest tests/ -v
```
