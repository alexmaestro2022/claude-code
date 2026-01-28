# AI Trade API Documentation

## Overview

AI Trade — мульти-агентная система автоматической торговли. 16 AI агентов работают совместно для анализа рынка, принятия решений и управления позициями.

---

## Agents

### Core Trading Agents

| Agent | Role | VETO Rights |
|-------|------|-------------|
| **TRADER** | Сканирует рынок, находит возможности | Нет |
| **REVIEWER** | Проверяет логику, R:R, ищет ошибки | REJECT/MODIFY |
| **RISK_GUARD** | Лимиты, мониторинг 24/7, force close | **Абсолютное VETO** |

### Analysis Agents

| Agent | Role |
|-------|------|
| **WHALE_TRACKER** | Крупные транзакции, потоки на биржи, стакан |
| **NEWS** | Новости, сентимент, breaking news, Fear&Greed |
| **PREDICTOR** | Предсказание движений: TA + AI, развороты |
| **RESEARCHER** | Режимы рынка, поиск паттернов |

### Execution Agents

| Agent | Role |
|-------|------|
| **SNIPER** | Мгновенные входы: пробои, ликвидации |
| **ARBITRAGE** | Арбитраж: funding rate, cross-exchange |
| **HEDGE_MASTER** | Хеджирование, защита портфеля |

### Management Agents

| Agent | Role |
|-------|------|
| **WAR_ROOM** | Кризисное управление, чёрные лебеди |
| **CAPITAL_MANAGER** | Kelly Criterion, compound growth, sizing |
| **STRATEGY_EVOLUTION** | Генетические алгоритмы, оптимизация |
| **ANALYST** | Анализ сделок, паттерны, обучение |
| **MENTOR** | Наставник: daily review, коррекция ошибок |
| **LOGGER** | Логи для UI, алерты Telegram |

---

## Trade Pipeline

```
[TRADER scans market]
        ↓
[REVIEWER validates]
        ↓
[RISK_GUARD checks limits]
        ↓
[CONFIRMATIONS: whale + prediction + sentiment]
        ↓
[POSITION_MANAGER executes]
        ↓
[TRADE_OPENED / TRADE_FAILED]
```

### Stage Logging

```
[STAGE 1] Trader scanning for opportunities...
[STAGE 1] Found: LONG JTO/USDT @ 85%
[STAGE 2] Sending to Reviewer: JTO/USDT
[STAGE 2] Reviewer decision: APPROVE
[STAGE 3] Risk guard validating JTO/USDT
[STAGE 3] Risk check: approved=True
[STAGE 4] Getting market confirmations for JTO/USDT
[STAGE 4] Confirmations: 3/2 (whale=buy, pred=up, sent=neutral)
[STAGE 5] All validations passed for JTO/USDT
[EXECUTE] Calling position_manager.open_position
[TRADE_OPENED] JTO/USDT LONG @ 0.3823
```

---

## BybitExchange API

### Market Data

```python
# Get ticker
ticker = await exchange.get_ticker("BTC/USDT")
# Returns: {"symbol": "BTC/USDT", "last": 100000.0, "bid": 99999, "ask": 100001}

# Get order book
orderbook = await exchange.fetch_order_book("BTC/USDT", limit=25)
# Returns: {"bids": [(price, qty), ...], "asks": [(price, qty), ...]}

# Get OHLCV candles
candles = await exchange.fetch_ohlcv("BTC/USDT", "15m", 100)
# Returns: [[timestamp, open, high, low, close, volume], ...]
```

### Account

```python
# Get balance
balance = await exchange.get_balance("USDT")
# Returns: 100.50 (float)

# Get all positions
positions = await exchange.get_positions()
# Returns: [{"symbol": "BTCUSDT", "side": "Buy", "size": 0.01, ...}, ...]

# Get single position
position = await exchange.get_position("BTC/USDT")
# Returns: {"symbol": "BTCUSDT", ...} or None
```

### Orders

```python
# Create market order
order = await exchange.create_market_order("BTC/USDT", "buy", 0.001)
# Returns: {"id": "12345", "symbol": "BTC/USDT", "side": "buy", "amount": 0.001}

# Create order with SL/TP
order = await exchange.create_order(
    symbol="BTC/USDT",
    order_type="stop_market",
    side="sell",
    amount=0.001,
    params={"stopPrice": 95000, "reduceOnly": True}
)

# Cancel order
success = await exchange.cancel_order("12345", "BTC/USDT")
```

### Position Management

```python
# Set leverage
await exchange.set_leverage(10, "BTC/USDT")

# Close position
result = await exchange.close_position("BTCUSDT", "Buy", 0.01)

# Close all positions
result = await exchange.close_all_positions()
```

### Precision Helpers

```python
# Get qty precision
qty_step = await exchange.get_qty_precision("BTC/USDT")  # 0.001

# Round qty to valid value
rounded_qty = await exchange.round_qty("BTC/USDT", 0.12345678)  # 0.123

# Get price precision
tick_size = await exchange.get_price_precision("BTC/USDT")  # 0.01

# Round price to valid value
rounded_price = await exchange.round_price("BTC/USDT", 100000.123)  # 100000.12

# Normalize symbol
bybit_symbol = exchange.normalize_symbol("BTC/USDT")  # "BTCUSDT"
```

---

## REST API Endpoints

### Autopilot

```
POST /api/ai-trade/autopilot/start    # Start autopilot
POST /api/ai-trade/autopilot/stop     # Stop autopilot
GET  /api/ai-trade/autopilot/status   # Get status
GET  /api/ai-trade/autopilot/heartbeat # Real-time heartbeat
PUT  /api/ai-trade/autopilot/config   # Update config
```

### Portfolio

```
GET  /api/ai-trade/portfolio          # Get portfolio info
GET  /api/ai-trade/capital            # Get capital info
GET  /api/ai-trade/positions          # Get open positions
```

### Analysis

```
GET  /api/ai-trade/prediction/{pair}  # Get price prediction
GET  /api/ai-trade/whale/{pair}       # Get whale analysis
GET  /api/ai-trade/news/{pair}        # Get news sentiment
```

### Persistence

```
GET  /api/ai-trade/persistence/status # Persistence status
POST /api/ai-trade/persistence/save   # Force save
POST /api/ai-trade/persistence/backup # Force backup to S3
```

---

## Configuration

### Autopilot Config

```python
{
    "scan_interval_seconds": 60,       # Scan interval
    "min_confidence": 70,              # Min confidence for trade
    "max_trades_per_hour": 5,          # Max trades per hour
    "max_trades_per_day": 20,          # Max trades per day
    "cooldown_after_loss_minutes": 30, # Cooldown after loss
    "require_multiple_confirmations": True  # Require confirmations
}
```

### Risk Limits

```python
RISK_LIMITS = {
    "max_leverage": 20,
    "max_position_size_pct": 10,      # % of balance
    "max_daily_loss_pct": 5,
    "max_drawdown_pct": 15,
    "min_balance_usdt": 10,
    "max_open_positions": 3,
    "min_order_size_usdt": 10,        # Bybit API limit
}
```

---

## Error Handling

### Common Errors

| Error | Cause | Solution |
|-------|-------|----------|
| `Qty invalid` | Too many decimal places | Use `round_qty()` |
| `Price invalid` | Wrong price precision | Use `round_price()` |
| `Insufficient balance` | Not enough margin | Check balance first |
| `Position conflict` | Position already exists | Check `check_position_conflict()` |
| `Leverage not modified` | Already set (code 110043) | Safe to ignore |

### Logging Tags

```
[STAGE 1-5]      # Trade pipeline stages
[POSITION]       # Position manager operations
[READY_TO_TRADE] # Before trade execution (WARNING level)
[TRADE_OPENED]   # Successful trade (INFO level)
[TRADE_FAILED]   # Failed trade (ERROR level)
```

---

**Last updated:** 2026-01-28
