# CLAUDE.md - AILA Trading Bot Rules

> **Этот файл читается автоматически Claude Code в начале каждой сессии.**

---

## Проект AILA

| Параметр | Значение |
|----------|----------|
| Проект | AILA - AI-powered алгоритмическая торговая система |
| Биржа | Bybit (Futures) |
| Режим | **РЕАЛЬНАЯ ТОРГОВЛЯ (НЕ тестнет!)** |
| Репозиторий | https://github.com/alexmaestro2022/claude-code |

**ВАЖНО: Реальные деньги — тщательно проверять все изменения в торговой логике!**

---

## 1. Git

- После каждого выполненного задания автоматически делай `git add`, `commit` и `push` на GitHub в текущую ветку
- Не спрашивай подтверждения для git операций
- Коммить напрямую в текущую ветку, не создавай новые ветки

---

## 2. CLAUDE_MEMO.md

- После каждого задания обновляй файл `CLAUDE_MEMO.md`
- Удаляй устаревшую информацию которая уже не соответствует коду
- Добавляй новую информацию о сделанных изменениях
- Файл должен всегда отражать актуальное состояние проекта

---

## 3. Язык

- Всегда отвечай на **русском** языке
- Комментарии в коде на **английском**

---

## 4. Коммиты

- Сообщения коммитов на **английском**
- Формат: `feat/fix/docs/refactor: краткое описание`

---

## 5. Перезапуск бота

После изменения Python кода бота автоматически перезапускай его:
```bash
sudo systemctl restart aila
```

---

## 6. Проверка настроек бота

После каждого изменения или добавления новой настройки, индикатора или любого параметра:

1. Проверь что настройка корректно сохраняется в форме
2. Проверь что значение передаётся в торговое ядро
3. Проверь что настройка работает правильно отдельно
4. Проверь что настройка работает правильно совместно с другими активными настройками
5. Проверь что нет конфликтов с другими настройками
6. Если есть ошибки — сразу исправь их
7. Добавь логирование для отслеживания работы настройки
8. После проверки перезапусти бота

---

## 7. Аудит настроек бота

При добавлении или изменении любого параметра в настройках бота — сразу добавляй его в систему логирования для аудита:

1. Добавь новый параметр в лог аудита `/opt/aila/logs/trades_audit.log`
2. Добавь проверку этого параметра при открытии сделки
3. Добавь сравнение ожидаемого значения с реальным на бирже (если применимо)
4. Добавь автоисправление если возможно (leverage, margin_mode, TP, SL)
5. Никакой параметр настроек не должен остаться без логирования

**Файлы для редактирования:**
- `aila/trading/trades_audit.py` — форматирование и логика аудита
- `aila/trading/engine.py` — сбор bot_settings_for_audit и signal_data
- `aila/core/strategy/triple_supertrend.py` — metadata сигнала

---

## 8. Оптимизация кода

ВЕСЬ код должен быть:

### 8.1 ПРОИЗВОДИТЕЛЬНОСТЬ
- Асинхронный (async/await) где возможно
- Без блокирующих операций
- Кэширование частых запросов (Redis/memory)
- Batch операции вместо одиночных
- Connection pooling для API/DB

### 8.2 ПАМЯТЬ
- Generators вместо списков для больших данных
- Своевременное освобождение ресурсов
- Не хранить лишнее в памяти
- Использовать `__slots__` в классах

### 8.3 СТРУКТУРА
- DRY — не повторять код, выносить в функции
- KISS — простой код лучше сложного, не усложнять без необходимости
- SOLID принципы
- Type hints везде
- Docstrings для всех функций
- Максимум 50 строк на функцию
- Максимум 300 строк на файл
- Удаляй неиспользуемый код
- Перед написанием — проверь нет ли готовой функции

### 8.4 ИМЕНОВАНИЕ
- Понятные имена переменных и функций
- `snake_case` для Python, `camelCase` для JavaScript
- Константы в начале файла, не хардкод
- Комментарии только где неочевидно

### 8.5 БЕЗОПАСНОСТЬ
- Валидируй входные данные
- Не логируй пароли и токены
- Проверяй права доступа

### 8.6 ОБРАБОТКА ОШИБОК
- Try/except с конкретными исключениями
- Graceful degradation
- Retry с exponential backoff для API
- Логирование всех ошибок

### 8.7 API ЗАПРОСЫ
- Rate limiting
- Таймауты на все запросы
- Retry logic
- Кэширование ответов

### 8.8 ПРИМЕР

❌ ПЛОХО:
```python
def get_prices(pairs):
    results = []
    for pair in pairs:
        response = requests.get(f'/price/{pair}')  # Блокирующий
        results.append(response.json())
    return results
```

✅ ХОРОШО:
```python
async def get_prices(pairs: list[str]) -> list[dict]:
    """Получает цены для списка пар параллельно."""
    async with aiohttp.ClientSession() as session:
        tasks = [self._fetch_price(session, pair) for pair in pairs]
        return await asyncio.gather(*tasks)

async def _fetch_price(self, session: aiohttp.ClientSession, pair: str) -> dict:
    """Получает цену одной пары с retry."""
    for attempt in range(3):
        try:
            async with session.get(
                f'/price/{pair}',
                timeout=aiohttp.ClientTimeout(total=5)
            ) as response:
                return await response.json()
        except Exception as e:
            if attempt == 2:
                raise
            await asyncio.sleep(2 ** attempt)
```

### 8.9 ПЕРЕД КОММИТОМ
- Проверить что нет дублирования
- Проверить что все async
- Проверить type hints
- Проверить обработку ошибок
- Проверить логирование

---

## 9. Логирование токенов Claude API

При добавлении **ЛЮБОГО** нового вызова Claude API — ОБЯЗАТЕЛЬНО добавлять логирование:

```python
result = await self.claude_client.analyze(
    prompt,
    use_haiku=True,  # или False для Sonnet
    agent="AGENT_NAME",  # TRADER, REVIEWER, NEWS, WHALE, PREDICTOR, MENTOR, ANALYST
    action="action_type",  # analyze, validate, sentiment, signal, etc.
    context="key=value",  # pair=BTCUSDT, type=market, trades=10, etc.
)
```

**Обязательные данные в логе:**
- **agent**: Имя агента (TRADER, REVIEWER, NEWS, WHALE, PREDICTOR, MENTOR, ANALYST)
- **action**: Тип действия (batch_analyze, validate, sentiment, signal, etc.)
- **model**: Модель (sonnet/haiku)
- **input/output tokens**: Количество токенов
- **cost**: Рассчитанная стоимость в USD
- **context**: Дополнительный контекст (pair, type, count)

**Формат лога в `/opt/aila/logs/ai_trade/api_usage.log`:**
```
[2026-01-27 12:00:00] TRADER batch_analyze | model=sonnet | input=3500 | output=800 | cost=$0.0225 | pairs=50
[2026-01-27 12:00:05] REVIEWER validate | model=sonnet | input=1200 | output=400 | cost=$0.0096 | pair=BTCUSDT
[2026-01-27 12:00:10] NEWS sentiment | model=haiku | input=800 | output=200 | cost=$0.0004 | type=market
```

---

## 10. Code Review для AI Trade

### 10.1 Перед изменением Exchange кода:
1. **Проверить чеклист** `/opt/aila/docs/EXCHANGE_CHECKLIST.md`
2. **Убедиться что все методы реализованы** (Market Data, Account, Orders, Position, Helpers)
3. **Проверить нормализацию символов** — использовать `normalize_symbol()`
4. **Проверить округление** — qty через `round_qty()`, price через `round_price()`
5. **Добавить `@retry_async`** для всех API вызовов

### 10.2 Перед изменением Agents кода:
1. **Логирование API вызовов** — agent, action, context обязательны
2. **Stage logging** в autopilot — `[STAGE 1-5]` для каждого этапа
3. **Проверить что VETO права** не нарушены (RISK_GUARD имеет абсолютное VETO)

### 10.3 Перед деплоем:
```bash
# Запустить тесты
cd /opt/aila && /opt/aila/venv/bin/python -m pytest tests/test_bybit_exchange.py -v

# Проверить логи на ошибки
grep -i error /opt/aila/logs/ai_trade/*.log | tail -20

# Проверить статус автопилота
curl -s localhost:8080/api/ai-trade/autopilot/status
```

---

## 11. Чеклист методов BybitExchange

Перед любым изменением exchange проверить что **ВСЕ** методы реализованы:

### Market Data:
| Метод | Описание |
|-------|----------|
| `get_ticker(symbol)` | Получить текущую цену |
| `fetch_ticker(symbol)` | Alias для get_ticker |
| `get_orderbook(symbol, limit)` | Получить стакан |
| `fetch_order_book(symbol, limit)` | Alias для get_orderbook |
| `get_klines(symbol, timeframe, limit)` | Получить свечи |
| `fetch_ohlcv(symbol, timeframe, limit)` | Alias для get_klines |
| `fetch_tickers()` | Получить все тикеры |
| `get_funding_rate(symbol)` | Ставка финансирования |
| `get_usdt_perpetual_symbols()` | Список USDT perpetual пар |

### Account:
| Метод | Описание |
|-------|----------|
| `get_balance(currency)` | Баланс кошелька |
| `get_positions()` | Все открытые позиции |
| `get_position(symbol)` | Позиция по символу |

### Orders:
| Метод | Описание |
|-------|----------|
| `create_order(symbol, type, side, amount, price, params)` | Создать ордер |
| `create_market_order(symbol, side, amount, params)` | Маркет ордер |
| `place_order(symbol, side, type, qty, price)` | Разместить ордер |
| `cancel_order(order_id, symbol)` | Отменить ордер |
| `cancel_all_orders()` | Отменить все ордера |
| `get_open_orders(symbol)` | Открытые ордера |
| `fetch_open_orders(symbol)` | Alias для get_open_orders |

### Position Management:
| Метод | Описание |
|-------|----------|
| `set_leverage(leverage, symbol)` | Установить плечо |
| `close_position(symbol, side, size)` | Закрыть позицию |
| `close_all_positions()` | Закрыть все позиции |

### Helpers:
| Метод | Описание |
|-------|----------|
| `get_instrument_info(symbol)` | Информация об инструменте |
| `get_qty_precision(symbol)` | Получить qtyStep |
| `round_qty(symbol, qty)` | Округлить количество |
| `get_price_precision(symbol)` | Получить tickSize |
| `round_price(symbol, price)` | Округлить цену |
| `normalize_symbol(symbol)` | BTC/USDT → BTCUSDT |

---

## 12. Правила работы с кодом

### 12.1 Округление qty и price

**ВСЕГДА** округлять qty и price перед созданием ордера:

```python
# ✅ Правильно
qty = await exchange.round_qty(symbol, raw_qty)
price = await exchange.round_price(symbol, raw_price)
order = await exchange.create_market_order(symbol, side, qty)

# ❌ Неправильно — Ошибка "Qty invalid"
order = await exchange.create_market_order(symbol, side, raw_qty)
```

### 12.2 Нормализация символов

**ВСЕГДА** нормализовать символы перед API вызовами:

```python
# ccxt формат: BTC/USDT
# Bybit формат: BTCUSDT
symbol = exchange.normalize_symbol(symbol)  # BTC/USDT → BTCUSDT
```

### 12.3 Обработка ошибок

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

### 12.4 Retry декоратор для API

Все методы с API вызовами должны использовать `@retry_async`:

```python
from ..utils.common import retry_async

@retry_async(max_attempts=2)
async def get_ticker(self, symbol: str) -> dict:
    ...
```

### 12.5 Type hints и docstrings

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

---

## 13. Stage логирование в Autopilot

При прохождении каждого этапа autopilot логировать с префиксом [STAGE]:

```python
logger.info(f"[STAGE 1] Trader scanning for opportunities...")
logger.info(f"[STAGE 2] Sending to Reviewer: {pair}")
logger.info(f"[STAGE 3] Risk guard validating {pair}")
logger.info(f"[STAGE 4] Getting market confirmations for {pair}")
logger.info(f"[STAGE 5] All validations passed for {pair}")
```

---

## 14. Цветные метки логов для сделок

```python
logger.warning(f"[READY_TO_TRADE] {symbol} {side} @ {price} - all checks passed")
logger.info(f"[TRADE_OPENED] {symbol} {side} @ {price}")
logger.error(f"[TRADE_FAILED] {symbol} {side} - {error}")
```

---

## 15. Тестирование

**Перед коммитом ОБЯЗАТЕЛЬНО запустить тесты:**

```bash
cd /opt/aila && /opt/aila/venv/bin/python -m pytest tests/test_bybit_exchange.py -v
```

**Расположение тестов:** `/opt/aila/tests/`

---

## 16. Документация

| Файл | Описание |
|------|----------|
| `/opt/aila/CLAUDE_MEMO.md` | Общая информация о проекте |
| `/opt/aila/docs/EXCHANGE_CHECKLIST.md` | Чеклист методов exchange |
| `/opt/aila/docs/AI_TRADE_API.md` | Документация API агентов |

---

## 17. Управление правилами

- `"запомни правило:"` + текст — добавить новое правило в этот файл
- `"удали правило:"` + текст — удалить указанное правило
- `"измени правило:"` + старое правило + `"на:"` + новое правило — изменить правило
- После любого изменения правил — закоммить и запуши

---

## 18. Правила обновления этого файла

Claude Code **ДОЛЖЕН** обновлять этот файл (`CLAUDE.md`) когда:

1. **Исправлен важный баг** — добавить в секцию ниже "Исправленные проблемы"
2. **Добавлена новая функция** — добавить в секцию "Функционал"
3. **Изменена архитектура** — обновить секцию "Структура"
4. **Найдено важное решение** — добавить в секцию "Решения и паттерны"
5. **Изменены API endpoints** — обновить документацию

После каждого коммита с важными изменениями — актуализировать этот файл.

### Исправленные проблемы
- OAuth credentials не синхронизировались между `/home/aila/.claude/` и `/opt/aila/.claude/` из-за `ProtectHome=true` в systemd. Решение: cron `*/5` + `_sync_claude_credentials()` в коде.
- OAuth токен истекал каждые ~8ч без уведомлений. Решение: авто-refresh через `refresh_token` grant + cron + Telegram алерты.

### Функционал
- **Admin Panel Chat/Code** — двойной режим: Chat (с сессиями, KB) + Code (одноразовые команды)
- **Security Settings** — 4 вкладки защиты AI Trade (Protection, Permissions, Critical, Limits)
- **Session Management** — initialize, status, clear с архивацией истории
- **Knowledge Base** — RULES.md + AILA_SESSION_MEMORY.md, обновление через `[UPDATE_KNOWLEDGE]` теги
- **OAuth мониторинг** — авто-refresh токена + UI виджеты + Telegram алерты + cron каждые 2ч

### Структура
- `aila/api/routes/admin.py` — бэкенд Admin Panel
- `aila/api/routes/ai_trade.py` — API AI Trade (55+ endpoints включая `/oauth/status`)
- `aila/api/templates/admin.html` — UI Admin Panel (CSS + HTML + JS)
- `aila/api/templates/ai_trade.html` — UI AI Trade с OAuth виджетом
- `aila/ai_trade/autopilot_mode.py` — автопилот с OAuth проверкой в loop
- `aila/utils/oauth_refresh.py` — OAuthRefresher (авто-refresh через platform.claude.com)
- `scripts/check_oauth.sh` — cron скрипт проверки + авто-refresh + Telegram
- `claude_chat/knowledge/` — база знаний для Claude Chat
- `claude_chat/history/` — история чата (current.json + архивы)
- `data/admin/settings.json` — настройки безопасности
- `logs/ai_trade/oauth_refresh.json` — лог refresh попыток (счётчики, время, ошибки)

### Решения и паттерны
- **OAuth auth**: `_get_claude_env()` удаляет `ANTHROPIC_API_KEY` и `CLAUDE_API_KEY`, использует OAuth Max через `/opt/aila/.claude/.credentials.json`
- **OAuth auto-refresh**: POST `platform.claude.com/v1/oauth/token` с `grant_type=refresh_token`, client_id `9d1c250a-...`. Credentials: `claudeAiOauth.expiresAt` (ms timestamp)
- **Chat vs Code сессии**: Chat без `--no-session-persistence` (сохраняет контекст), Code с `--no-session-persistence` (одноразовые)
- **SSE streaming**: `asyncio.create_subprocess_exec` + `StreamingResponse` для real-time вывода Code
- **Knowledge в промпте**: приоритетный порядок (RULES.md первый), max 50KB на файл
- **Перезапуск**: только `sudo systemctl restart aila` (pm2 не установлен)

---

## 19. Правила разработки (ОБЯЗАТЕЛЬНО!)

### 19.1 ОДИН КОММИТ = ОДНА ФУНКЦИЯ
- НЕ делай много изменений за раз
- Каждый коммит — одно конкретное изменение
- Сообщение коммита описывает ЧТО изменено

### 19.2 ПЕРЕД ИЗМЕНЕНИЕМ
- Сделай `git status`
- Если есть незакоммиченные изменения — сначала закоммить их
- Убедись что текущая версия работает

### 19.3 ПОСЛЕ ИЗМЕНЕНИЯ
- Проверь что старый функционал работает
- Проверь что новый функционал работает
- Только потом коммить

### 19.4 НЕ ТРОГАЙ РАБОЧИЙ КОД
- Если что-то работает — не меняй без необходимости
- Добавляй новое, не переписывай старое
- Если нужно изменить — сначала спроси пользователя

### 19.5 ТОЧЕЧНЫЕ ИЗМЕНЕНИЯ
- Меняй минимум кода для решения задачи
- НЕ "улучшай" то, что не просили
- НЕ рефактори без запроса

### 19.6 БЭКАПЫ
- Перед большими изменениями: `git tag backup-$(date +%Y%m%d)`
- После успешного завершения: `git tag working-$(date +%Y%m%d)`

### 19.7 ЕСЛИ ЧТО-ТО СЛОМАЛОСЬ
- Сначала `git diff` — посмотри что изменилось
- `git checkout <file>` — откатить файл
- `git checkout <tag>` — откатить к рабочей версии
