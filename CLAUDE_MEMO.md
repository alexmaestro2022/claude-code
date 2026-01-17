# ПАМЯТКА ДЛЯ CLAUDE - AILA Trading Bot

> **ПРОЧИТАЙ ПОЛНОСТЬЮ ПЕРЕД НАЧАЛОМ РАБОТЫ!**

---

## 1. Общая информация

| Параметр | Значение |
|----------|----------|
| Проект | AILA - AI-powered алгоритмическая торговая система |
| Биржа | Bybit (Futures) |
| Режим | **РЕАЛЬНАЯ ТОРГОВЛЯ (НЕ тестнет!)** |
| Репозиторий | https://github.com/alexmaestro2022/claude-code |
| **Рабочая ветка** | **`claude/start-new-session-4XrKU`** |

**ВАЖНО: Реальные деньги — тщательно проверять все изменения в торговой логике!**

---

## 2. КРИТИЧЕСКИЕ ПРАВИЛА РАБОТЫ

### НИКОГДА НЕ ДЕЛАЙ:
1. **НЕ бери файлы из других веток** (`git checkout origin/другая-ветка -- файл`)
2. **НЕ мержи ветки** без явного разрешения пользователя
3. **НЕ переключайся на другие ветки** без разрешения
4. **НЕ удаляй существующий код** без понимания что он делает
5. **НЕ меняй ветку на сервере** без подтверждения пользователя

### ВСЕГДА ДЕЛАЙ:
1. **Работай ТОЛЬКО в ветке** `claude/start-new-session-4XrKU`
2. **Вноси ТОЧЕЧНЫЕ изменения** - только то что нужно исправить
3. **Проверяй что endpoint существует** перед изменением JS кода
4. **Читай файл перед редактированием**
5. **Спрашивай пользователя** если видишь старый интерфейс на сервере
6. **ОБНОВЛЯЙ CLAUDE_MEMO.md** после каждого важного изменения или новой фичи!
7. **ЛОГИРУЙ ПАРАМЕТРЫ** - всегда добавлять логи параметров стратегии и бота!

### ПРАВИЛО ЛОГИРОВАНИЯ ПАРАМЕТРОВ:
> **При любых изменениях в стратегии или создании новых фич - ОБЯЗАТЕЛЬНО логировать:**
> - Все параметры стратегии при старте бота (ST periods, multipliers, roles и т.д.)
> - Параметры которые используются для принятия решений (SL, TP, trailing и т.д.)
> - При изменении настроек - логировать старое и новое значение
> - При сигналах - логировать все данные на основе которых принято решение
>
> **Формат логов:**
> ```python
> logger.info("Bot config", st1_period=10, st1_mult=1.0, st1_role="confirm", ...)
> logger.info("Signal detected", symbol="BTCUSDT", st1_dir=1, st2_dir=1, st3_dir=1, ...)
> ```
>
> Это критично для отладки - без логов невозможно понять почему бот принял решение!

### ПРАВИЛО ОБНОВЛЕНИЯ CLAUDE_MEMO.md:
> **После завершения важной задачи (баг-фикс, новая фича, изменение логики) -
> СРАЗУ обнови этот файл, добавив информацию об изменениях!**
>
> Это критично важно: сессии могут зависать, и следующий Claude
> должен иметь актуальную информацию о состоянии проекта.

---

## 3. Инфраструктура

| Компонент | Значение |
|-----------|----------|
| Сервер | 149.28.16.83 |
| Путь на сервере | /opt/aila |
| База данных | SQLite (./data/aila.db) |
| Веб-интерфейс | http://149.28.16.83:8080 |
| Логи | /var/log/aila.log |
| Скрипт деплоя | /opt/aila/merge_claude.sh |

---

## 3.1 Workflow: Claude -> Сервер -> GitHub

**Схема работы:**
```
┌─────────────┐     push      ┌─────────────────────────────────┐
│   Claude    │ ───────────►  │ GitHub: claude/...-XXXXX        │
│  (сессия)   │               │ (ветка сессии)                  │
└─────────────┘               └─────────────────────────────────┘
                                          │
                                          │ merge_claude.sh
                                          ▼
                              ┌─────────────────────────────────┐
                              │ Сервер + GitHub рабочая ветка   │
                              │ claude/start-new-session-4XrKU  │
                              └─────────────────────────────────┘
```

### В начале КАЖДОЙ сессии Claude должен:
```bash
git fetch origin claude/start-new-session-4XrKU
git checkout -B claude/новая-сессия-XXXXX origin/claude/start-new-session-4XrKU
```

### После завершения работы Claude пишет:
```
Готово! Изменения запушены в ветку: claude/xxx-xxx-XXXXX

Для применения на сервере:
/opt/aila/merge_claude.sh claude/xxx-xxx-XXXXX
```

### Пользователь на сервере выполняет:
```bash
/opt/aila/merge_claude.sh claude/xxx-xxx-XXXXX
```

Скрипт автоматически:
1. Получает изменения с GitHub
2. Показывает что будет смержено
3. Спрашивает подтверждение (y/n)
4. Мержит в рабочую ветку
5. Пушит на GitHub
6. Перезапускает бота

---

## 4. Торговая стратегия: Triple SuperTrend + EMA200

### Индикаторы (ПРАВИЛЬНЫЕ НАЗВАНИЯ!):

| SuperTrend | Period | Multiplier | Скорость |
|------------|--------|------------|----------|
| **ST1** | 10 | 1.0 | **FAST (быстрая)** |
| **ST2** | 11 | 2.0 | **Medium (средняя)** |
| **ST3** | 12 | 3.0 | **SLOW (медленная)** |
| EMA200 | 200 | - | Трендовый фильтр |

### Signal Entry - система ролей:
- **off** - линия не используется
- **confirm** - линия должна УЖЕ быть в направлении (на prev И curr свече)
- **trigger** - линия должна ТОЛЬКО ЧТО развернуться

### По умолчанию:
- ST1 (Fast) = confirm
- ST2 (Medium) = confirm
- ST3 (Slow) = **trigger** <- медленная разворачивается последней

### trigger_confirm_candles - подтверждение триггера:

Параметр `trigger_confirm_candles` определяет сколько ЗАКРЫТЫХ свечей триггер должен быть в нужном направлении:

```
Для trigger_confirm_candles = 3:

Свеча:  [-5]  [-4]  [-3]  [-2]  [-1]
         ↑     ↑     ↑     ↑     ↑
        RED  GREEN GREEN GREEN (forming)
         │     └─────┴─────┘
         │      3 свечи подтверждения
         └── точка разворота (должна быть в противоположном направлении)
```

**Логика проверки (triple_supertrend.py):**
```python
# Проверяем что последние N закрытых свечей в нужном направлении
for i in range(confirm_candles):
    idx = -2 - i  # -2 (current closed), -3 (prev), -4, etc.
    if int(trigger_history.iloc[idx]) != target_dir:
        return False

# Проверяем точку разворота - свеча ДО периода подтверждения
turn_idx = -2 - confirm_candles
if int(trigger_history.iloc[turn_idx]) == target_dir:
    return False  # Не недавний разворот
```

---

## 5. Баланс бота и PnL

### initial_balance - выделенный депозит:

Рассчитывается как: `баланс_биржи × balance_usage_percent / 100`

**Где рассчитывается:**
1. **При создании бота** - `main.py create_bot()` (если есть client)
2. **При редактировании** - `main.py update_bot()` (при изменении balance_usage_percent)
3. **При запуске бота** - `run_web.py` (с актуальным балансом)

### Отображение баланса в UI:
```javascript
// Текущий баланс = начальный + PnL
const currentBalance = allocatedDeposit + totalPnl;
// Цвет: зеленый если в плюсе, красный если в минусе
const balanceColor = totalPnl > 0 ? '#00ff88' : (totalPnl < 0 ? '#ff4444' : '#00d4ff');
```

### Статистика бота (обновляется при закрытии позиции):
- `total_trades` - общее количество сделок
- `winning_trades` - выигрышные сделки
- `losing_trades` - убыточные сделки
- `total_pnl_history` - накопленный PnL от закрытых позиций

**close_position_record()** вызывается из `engine.py` при закрытии позиции для обновления статистики.

---

## 6. Position Sizing

### Режим fixed_amount:
- `order_size` = размер позиции (notional value), НЕ маржа!
- Пример: `order_size=10 USDT` с leverage 10x = позиция 10 USDT, маржа 1 USDT

| Баланс | Leverage | order_size | Макс. позиций |
|--------|----------|------------|---------------|
| 4.5 USDT | 10x | 10 USDT | ~4 позиции (маржа 1 USDT каждая) |

---

## 7. Структура проекта

```
/opt/aila/
├── VERSION                  # Файл версии бота (редактировать здесь!)
├── aila/
│   ├── core/
│   │   ├── indicators/      # SuperTrend, EMA, ATR
│   │   ├── strategy/        # Triple SuperTrend логика
│   │   └── risk/            # Position sizing, SL/TP
│   ├── exchange/            # Bybit API интеграция
│   ├── trading/             # Trading engine
│   ├── api/
│   │   └── main.py          # ГЛАВНЫЙ ФАЙЛ - FastAPI + UI (~6000 строк)
│   ├── config/              # Настройки
│   └── scripts/
│       └── run_web.py       # Запуск + API endpoints + чтение VERSION
├── merge_claude.sh          # Скрипт деплоя от Claude
├── venv/                    # Python virtual environment
├── data/                    # SQLite база данных
└── .env                     # API ключи (НЕ ТРОГАТЬ!)
```

### Версия бота:
**Файл:** `/opt/aila/VERSION`

Для изменения версии - редактировать этот файл. Версия автоматически отображается:
- В нижней панели интерфейса (слева)
- Через API: `GET /api/version`

```bash
# Изменить версию
echo "2.2.0" > /opt/aila/VERSION
```

### Ключевые файлы:

| Файл | Описание |
|------|----------|
| `aila/api/main.py` | **ГЛАВНЫЙ** - FastAPI + весь UI (монолит ~6000 строк) |
| `aila/scripts/run_web.py` | Запуск + /api/stats, /api/ping, /api/restart-server |
| `aila/core/strategy/triple_supertrend.py` | Логика стратегии Signal Entry |
| `aila/core/indicators/supertrend.py` | Индикаторы SuperTrend |
| `aila/core/risk/position_sizing.py` | Расчет размера позиции |
| `aila/trading/engine.py` | TradingEngine + close_position_record call |

---

## 8. Добавление новых настроек - ЧЕКЛИСТ

При добавлении новой настройки нужно изменить **11 мест**:

**В main.py:**
1. HTML форма создания бота (`newBot...`)
2. HTML форма редактирования бота (`editBot...`)
3. JavaScript - createBot()
4. JavaScript - loadEditBot()
5. JavaScript - saveEditBot()
6. Backend - create_bot endpoint
7. Backend - update_bot endpoint (+ real-time обновление engine!)
8. Backend - start_bot (bot_settings)
9. Backend - resume_specific_bot (обновление strategy.config!)
10. Переводы (translations.en и translations.ru)

**Если настройка влияет на торговлю:**
11. run_web.py - create_strategy_config_for_bot()

**ВАЖНО: Типы данных для boolean полей:**
> JavaScript checkbox.checked возвращает boolean, но при сериализации в JSON
> может стать строкой "false". Используй функцию `_to_bool()` из main.py:
> ```python
> "ema_enabled": _to_bool(config.get("ema_enabled", True), True),
> ```
> Это касается: ema_enabled, trailing_enabled, partial_tp_enabled, trailing_tp_enabled

---

## 9. Частые ошибки и решения

| Ошибка | Причина | Решение |
|--------|---------|---------|
| Баланс "--" | Нет background client | Проверь /api/stats в run_web.py |
| Пинг не показывается | Нет background client | Проверь /api/ping в run_web.py |
| Restart зависает | Неправильный JS | Должен быть polling после restart |
| qty в 10x больше | position_sizing умножал на leverage | НЕ умножать на leverage! |
| ST1/ST3 перепутаны | Неправильные названия в UI | ST1=Fast, ST3=Slow |
| Старый интерфейс | Кэш браузера | Ctrl+Shift+R |
| Конфликты при мерже | Ветка сессии создана от старого кода | Создавать от рабочей ветки |
| Настройки не применяются | Не обновлён engine.strategy.config | Добавить в update_bot и resume |
| **Ложные сигналы входа** | Использовалась незакрытая свеча | Использовать iloc[-2] вместо iloc[-1] |
| **Параметры ST не меняются** | `.env` файл переопределяет settings.py | Изменить `/opt/aila/.env` на сервере! |
| **Баланс бота 0 или неверный** | initial_balance не рассчитан | Рассчитывается в run_web.py при старте |
| **trigger_confirm не работал** | Логика была противоречивой | Исправлено - проверяет N свечей подряд |
| **EMA фильтр не отключался** | JS отправлял строку "false" | _to_bool() конвертирует в boolean |
| **Qty invalid при partial/trailing TP** | qty не округлён до step_size | Использовать trading_pair.round_quantity() |
| **Safety limits при 100% лимите** | max_loss_percent не передавался в engine | Добавить в bot_settings и create_engine_config |

---

## 9.1 Лимит потерь (Safety Limits)

**Как работает:**
```python
# engine.py _check_safety_limits()
loss_percent = abs(daily_pnl_usdt) / allocated_balance * 100
if loss_percent >= max_daily_loss_percent:
    return False  # Остановить торговлю
```

**Пример:**
| Общий баланс | balance_usage_percent | allocated_balance | max_loss_percent | Стоп при убытке |
|-------------|----------------------|------------------|-----------------|----------------|
| 100 USDT | 10% | 10 USDT | 50% | 5 USDT |
| 50 USDT | 50% | 25 USDT | 20% | 5 USDT |

**Ключевые файлы:**
- `engine.py` - `allocated_balance` в TradingEngineConfig + логика проверки
- `run_web.py` - `engine.config.allocated_balance = calculated_initial_balance`
- `main.py` - `bot_settings["max_loss_percent"]` копируется при start_bot

---

## 9.2 Логика триггера - когда возникает сигнал

**Сигнал возникает ТОЛЬКО при СМЕНЕ направления триггера!**

```
ST3 history: [+1, +1, +1, -1, -1]
                         ↑
                    Момент смены = СИГНАЛ SHORT

ST3 history: [-1, -1, -1, -1, -1]
                    ↑     ↑
               Уже в шорт = НЕТ нового сигнала
```

**Если в логах видите:**
```
ST3 direction history: candles=['17:12=-1', '17:13=-1', '17:14=-1', '17:15=-1', '17:16=-1']
signals=0
```
Это нормально! ST3 уже в направлении -1 несколько свечей. Бот ждёт нового перехода.

---

## 10. При проблемах

1. **НЕ** бери файлы из других веток!
2. **ЧИТАЙ** код перед изменением
3. **ПРОВЕРЯЙ** что все endpoints существуют
4. **СПРАШИВАЙ** пользователя перед сменой ветки на сервере
5. **НАПОМИНАЙ** про Ctrl+Shift+R
6. **ЗАПРАШИВАЙ** логи при ошибках

---

## 11. ВАЖНО: Данные свечей Bybit API

**Bybit API возвращает НЕЗАКРЫТУЮ текущую свечу как последний элемент!**

```python
# В данных klines от Bybit:
iloc[-1] = текущая НЕЗАКРЫТАЯ свеча (меняется каждую секунду!)
iloc[-2] = последняя ЗАКРЫТАЯ свеча
iloc[-3] = предпоследняя ЗАКРЫТАЯ свеча
```

**Для сигналов входа ОБЯЗАТЕЛЬНО использовать ЗАКРЫТЫЕ свечи:**
```python
# ПРАВИЛЬНО (triple_supertrend.py):
st1_dir_curr = int(triple_st.st1.direction.iloc[-2])  # последняя закрытая
st1_dir_prev = int(triple_st.st1.direction.iloc[-3])  # предпоследняя закрытая

# НЕПРАВИЛЬНО (приводило к ложным сигналам):
st1_dir_curr = int(triple_st.st1.direction.iloc[-1])  # незакрытая свеча!
```

**Исключение - можно использовать iloc[-1] для:**
- `current_price` - актуальная цена для размещения ордера
- Stop-loss расчёт - актуальное значение линии SuperTrend

---

## 12. КРИТИЧНО: Конфигурация .env на сервере

**`.env` файл на сервере ПЕРЕОПРЕДЕЛЯЕТ значения из settings.py!**

Путь: `/opt/aila/.env`

**Правильные параметры SuperTrend:**
```bash
# SuperTrend 1 (Fast)
STRATEGY_ST1_PERIOD=10
STRATEGY_ST1_MULTIPLIER=1.0

# SuperTrend 2 (Medium)
STRATEGY_ST2_PERIOD=11
STRATEGY_ST2_MULTIPLIER=2.0

# SuperTrend 3 (Slow) - ТРИГГЕР по умолчанию
STRATEGY_ST3_PERIOD=12
STRATEGY_ST3_MULTIPLIER=3.0
```

**ВАЖНО:**
- ST1 = FAST (быстрая, period=10, mult=1.0)
- ST2 = MEDIUM (средняя, period=11, mult=2.0)
- ST3 = SLOW (медленная, period=12, mult=3.0) - используется как TRIGGER

**При изменении параметров ST - проверить И settings.py И .env на сервере!**

---

## 13. Bybit API Rate Limits

**Лимиты:**
- 600 запросов / 5 секунд (120 req/s в среднем)
- При превышении - бан IP на 10 минут
- WebSocket не считается в rate limit

**Оптимизация сканирования (engine.py):**
```
БЫЛО: 458 пар × get_kline = 458 запросов (~23 сек)
СТАЛО: 1 get_all_tickers + ~30 get_kline = ~35 запросов (~3 сек)
```

**Ключевые методы:**
- `get_all_tickers()` - все 458 тикеров за 1 запрос
- `_fast_filter_by_tickers()` - фильтрация по price/volume БЕЗ kline
- `_process_symbols_parallel()` - параллельные запросы kline (10 одновременно)

---

## 14. Рабочие функции в текущей ветке

Ветка `claude/start-new-session-4XrKU` содержит:
- Multi-bot архитектура (создание ботов через веб-интерфейс)
- Signal Entry - система ролей ST линий (off/confirm/trigger)
- **trigger_confirm_candles** - подтверждение триггера N свечами
- **ИСПРАВЛЕНО: Сигналы входа по ЗАКРЫТЫМ свечам** (iloc[-2] вместо iloc[-1])
- Real-time обновление настроек при update_bot и resume
- **FAST SCAN** - оптимизированное сканирование (458 -> ~30 пар за 3 сек)
- **STARTUP CLEANUP** - при перезапуске сервера закрываются ВСЕ позиции и ордера
- /api/stats - баланс работает даже без запущенных ботов
- /api/ping - пинг биржи работает
- /api/version - возвращает версию из файла VERSION
- /api/restart-server - перезагрузка с остановкой ботов
- Правильный position sizing (order_size = notional)
- Trailing SL по SuperTrend линиям
- Auto Search mode для ботов
- Partial TP с переносом SL
- Trailing TP для остатка позиции
- **Подробное логирование параметров** при старте и сигналах
- **Динамический баланс бота** - показывает initial_balance + PnL
- **Статистика бота** - winrate, total trades, PnL history (обновляется при закрытии позиций)
- **Копирование логов** - последние 100 строк
- **Компактная шапка** - зелёная точка статуса слева, логотип (клик = refresh), выбор языка EN/RU справа
- **Dashboard карточки** - 2 ряда по 3 карточки: Balance/PnL/Winrate и Positions/Bots/Trades
- **Нижняя панель** - версия слева, API stats (ping, requests), кнопка Restart справа
- **График позиции** - клик на карточку позиции открывает полноэкранный график:
  - Свечи с TradingView Lightweight Charts
  - Индикаторы: ST1 (зелёный), ST2 (голубой), ST3 (оранжевый), EMA200 (жёлтый)
  - Линии Entry/SL/TP с PnL
  - Перетаскивание SL/TP мышью для ручного изменения
  - Обратный отсчёт до закрытия свечи
  - API: /api/klines/{symbol}, /api/indicators/{symbol}, /api/positions/{id}/update-levels

---

## 15. Команды для сервера (справочно)

**Проверка статуса:**
```bash
cd /opt/aila && git branch -v
tail -20 /var/log/aila.log
```

**Проверка .env параметров:**
```bash
cat /opt/aila/.env | grep -E "STRATEGY_ST"
```

**Ручной перезапуск бота:**
```bash
pkill -f "aila.scripts.run_web"; sleep 2; nohup /opt/aila/venv/bin/python -m aila.scripts.run_web > /var/log/aila.log 2>&1 &
```

**Деплой через скрипт (рекомендуется):**
```bash
/opt/aila/merge_claude.sh claude/имя-ветки-сессии
```

---

**Последнее обновление:** 2026-01-17
**Текущая версия:** v2.1.0 (см. файл `/opt/aila/VERSION`)
**Рабочая ветка:** `claude/start-new-session-4XrKU`
