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
| **Multi-bot: все боты с одинаковыми фильтрами** | runtime_settings глобальный | bot_settings передаётся в TradingEngine |
| **Multi-bot: Heikin Ashi настройки не работают** | читались из глобального словаря | bot_settings в _process_symbol() |
| **EMA фильтр показывал ❌ в аудите** | Дефолт False в trades_audit.py | Изменён дефолт на True (если сделка открылась - фильтр прошёл) |

---

## 9.1 Лимит потерь (Safety Limits)

**ВАЖНО: Лимиты работают ТОЛЬКО если включены toggle переключатели!**

**Настройки в UI:**
- `max_loss_enabled` - toggle для Max Loss Limit
- `consecutive_losses_enabled` - toggle для Consecutive Losses Limit
- `max_consecutive_losses` - количество убыточных сделок подряд (2-10)
- `cooldown_after_loss_streak` - пауза после серии убытков (5-120 мин)

**Как работает:**
```python
# engine.py _check_safety_limits()
# Проверки выполняются ТОЛЬКО если включены соответствующие toggle!
if self.config.max_loss_enabled:
    loss_percent = abs(daily_pnl_usdt) / allocated_balance * 100
    if loss_percent >= max_daily_loss_percent:
        return False  # Остановить торговлю

if self.config.consecutive_losses_enabled:
    if consecutive_losses >= max_consecutive_losses:
        # Проверить cooldown и остановить если нужно
```

**Пример:**
| Общий баланс | balance_usage_percent | allocated_balance | max_loss_percent | Стоп при убытке |
|-------------|----------------------|------------------|-----------------|----------------|
| 100 USDT | 10% | 10 USDT | 50% | 5 USDT |
| 50 USDT | 50% | 25 USDT | 20% | 5 USDT |

**Ключевые файлы:**
- `engine.py` - TradingEngineConfig (max_loss_enabled, consecutive_losses_enabled и т.д.)
- `run_web.py` - create_engine_config_for_bot() + логирование риск-настроек
- `main.py` - UI формы с toggle переключателями + JS функции toggle*

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
  - Индикаторы отображаются ТОЛЬКО если активны в настройках бота (role != "off")
  - SuperTrend цвета по направлению: зелёный (bullish/+1), красный (bearish/-1)
  - Толщина линий: ST1=1px (тонкая), ST2=2px (средняя), ST3=3px (толстая)
  - EMA200 жёлтая линия (если включена в настройках)
  - Линии Entry/SL/TP с PnL
  - Перетаскивание SL/TP мышью для ручного изменения
  - Обратный отсчёт до закрытия свечи
  - API: /api/klines/{symbol}, /api/indicators/{symbol}?bot_id=X, /api/positions/{id}/update-levels
- **Safety Limits с toggle переключателями**:
  - Max Loss Limit - теперь с toggle вкл/выкл (по умолчанию выкл, значение 50%)
  - Consecutive Losses Limit - НОВАЯ функция с toggle (max losses 2-10, cooldown 5-120 мин)
  - Лимиты применяются ТОЛЬКО если включены соответствующие toggle
  - Логирование при старте бота: `Risk: MaxLoss=ON/OFF (...) | ConsecLosses=ON/OFF (...)`
- **Asset Filters с toggle** - фильтры активов можно полностью отключить toggle переключателем
- **Telegram авторизация** - вход через Telegram Login Widget с проверкой разрешённых ID
- **Стратегия Heikin Ashi** - альтернативная торговая стратегия (см. раздел 16)

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

---

## 16. Стратегия Heikin Ashi

### Что это:
Альтернативная торговая стратегия, использующая свечи Heikin Ashi вместо Triple SuperTrend.

### Расчёт свечей Heikin Ashi:
```python
HA Close = (Open + High + Low + Close) / 4
HA Open = (prev_HA_Open + prev_HA_Close) / 2
HA High = max(High, HA_Open, HA_Close)
HA Low = min(Low, HA_Open, HA_Close)
HA Color = green если HA_Close > HA_Open, иначе red
```

### Сигналы входа:
- **LONG**: N последовательных зелёных свечей (после красной)
- **SHORT**: N последовательных красных свечей (после зелёной)

### Сигналы выхода:
- **Exit LONG**: N последовательных красных свечей
- **Exit SHORT**: N последовательных зелёных свечей

### Фильтры:
| Фильтр | Описание |
|--------|----------|
| **EMA Filter** | LONG только выше EMA, SHORT только ниже EMA |
| **ATR Volatility** | Минимальный ATR% для входа (фильтр низкой волатильности) |

### Риск-менеджмент для HA:
- **Emergency SL** - аварийный стоп-лосс (% от цены входа)
- **Trailing Stop** - trailing с активацией после N% прибыли

### Настройки runtime_settings:
```python
strategy_type = "heikinashi"  # или "supertrend"
ha_entry_candles = 2          # свечей для входа
ha_exit_on_color_change = True
ha_exit_candles = 1           # свечей для выхода
ha_ema_enabled = True
ha_ema_period = 200
ha_volatility_enabled = True
ha_min_atr = 0.5              # минимальный ATR%
ha_emergency_sl = 2.0         # аварийный SL%
ha_trailing_enabled = True
ha_trailing_activation = 1.0  # активация после N% прибыли
ha_trailing_distance = 0.5    # расстояние trailing%
```

### Ключевые функции (engine.py):
- `calculate_heikin_ashi(df)` - расчёт свечей HA
- `generate_heikin_ashi_signal(...)` - генерация сигналов с фильтрами

---

## 17. Telegram авторизация

### Как работает:
Вход в веб-интерфейс через Telegram Login Widget.

### Настройки (.env):
```bash
TELEGRAM_AUTH_BOT_TOKEN=your_bot_token
TELEGRAM_BOT_USERNAME=aila_zone_bot
ALLOWED_TELEGRAM_IDS=123456789,987654321
```

### Логика:
1. Пользователь нажимает кнопку входа через Telegram
2. Telegram возвращает данные с подписью (hash)
3. Сервер проверяет подпись через HMAC-SHA256
4. Проверяется что telegram_id в списке ALLOWED_TELEGRAM_IDS
5. Создаётся сессия (cookie)

### Файлы:
- `aila/api/main.py` - функции `verify_telegram_auth()`, session storage
- `static/` - иконки и manifest.json для PWA

### Видео фон на странице логина:
- Видео файл: `/static/bg-video.mp4` (оригинал: `/static/bg-video-original.mp4`)
- CSS: `#bg-video` (position: fixed, z-index: -2, object-fit: cover)
- Overlay: `.video-overlay` (чёрный полупрозрачный слой rgba(0,0,0,0.5), z-index: -1)
- HTML: `<video autoplay loop muted playsinline>` + `<div class="video-overlay">`
- Фон body заменён с градиента на `background: #000`

---

## 18. Multi-bot режим - архитектура настроек

### Проблема (ИСПРАВЛЕНО 2026-01-21):
При запуске нескольких ботов настройки читались из глобального `runtime_settings`,
а не из индивидуальных настроек бота. Это приводило к тому, что все боты
использовали одинаковые фильтры и настройки стратегии.

### Решение:
Добавлен параметр `bot_settings` в TradingEngine, который передаётся при создании.

**Файлы изменены:**
- `engine.py` - добавлен `bot_settings` в `__init__()`, изменены методы:
  - `_fast_filter_by_tickers()` - использует `self.bot_settings`
  - `_process_symbol()` - использует `self.bot_settings` для Heikin Ashi
  - `_check_auto_trade_filters()` - использует `self.bot_settings`
- `run_web.py` - передаёт `bot_settings=bot_settings` при создании TradingEngine

### Как работает:
```python
# run_web.py
bot_settings = get_bot_runtime_settings(bot_id)
engine = TradingEngine(client, strategy, engine_config, bot_settings=bot_settings)

# engine.py
def __init__(self, ..., bot_settings: Optional[dict] = None):
    self.bot_settings = bot_settings or {}

def _fast_filter_by_tickers(self, ...):
    settings = self.bot_settings  # Используем настройки конкретного бота
    if not settings:
        from ..api.main import runtime_settings  # Fallback
        settings = runtime_settings
```

### Что это исправляет:
- ✅ Asset Filters работают индивидуально для каждого бота
- ✅ Heikin Ashi настройки (strategy_type, ha_*) работают индивидуально
- ✅ Volatility Filter работает индивидуально

---

## 19. Аудит сделок (Trades Audit)

### Назначение:
Комплексное логирование ВСЕХ настроек бота и автоисправление при открытии сделок.

### Файлы:
- `/opt/aila/logs/trades_audit.log` - лог аудита сделок
- `aila/trading/trades_audit.py` - модуль аудита
- `aila/trading/engine.py` - сбор bot_settings_for_audit в _execute_entry()

### Секции лога:
1. **ОСНОВНЫЕ НАСТРОЙКИ** - имя бота, режим, таймфрейм, макс. пар
2. **РИСК-МЕНЕДЖМЕНТ** - balance usage, max loss, consecutive losses, cooldown
3. **НАСТРОЙКИ СТРАТЕГИИ** - плечо, размер, маржа, strategy_type
4. **SuperTrend / Heikin Ashi** - параметры стратегии с ролями
5. **ТЕЙК-ПРОФИТ** - режим, R:R, trailing TP, partial TP
6. **СТОП-ЛОСС** - режим, offset, trailing SL
7. **ФИЛЬТРЫ СИГНАЛОВ** - EMA с strict/soft режимом
8. **ФИЛЬТРЫ АКТИВОВ** - volume, price, change, volatility
9. **ПРОВЕРКА НА БИРЖЕ** - сравнение с реальной позицией
10. **ПРОВЕРКА СИГНАЛА** - какие индикаторы сработали

### Автоисправление:
При расхождении параметров автоматически исправляются:
- ❌ Плечо не совпадает → `set_leverage()`
- ❌ Маржа не совпадает → `set_margin_mode()` (до установки плеча!)
- ❌ TP/SL не выставлены → `update_take_profit()` / `update_stop_loss()`
- ⚠️ Размер неверный → только логирование (округление биржи)

### Формат лога (пример):
```
[TRADE OPEN] 2026-01-21 18:29:12 1000PEPEUSDT SHORT

=== ВСЕ НАСТРОЙКИ БОТА ===
Бот: MyBot
Режим: auto_search
Таймфрейм: 1m
Плечо: 10x
Размер: 10.0 USDT (Fixed)
Маржа: Isolated

Стратегия: SuperTrend
- ST1: Conf (10, 1.0)
- ST2: Conf (11, 2.0)
- ST3: Trig (12, 3.0)
- Подтв. свечей: 2

TP: R:R 2:1 = 0.004800
- Trailing TP: вкл (ST Line Быстрый)
- Partial TP: вкл (50% на TP1, SL→TP1)

SL: SuperTrend линия ST2 (средний) = 0.004882

Фильтры:
- EMA 200: вкл, строгий

=== ПРОВЕРКА НА БИРЖЕ ===
✅ Плечо: 10x
✅ Маржа: isolated
✅ TP: 0.0048
✅ SL: 0.004882

=== ПРОВЕРКА СИГНАЛА ===
✅ ST1 (Conf): цена ниже линии
✅ ST2 (Conf): цена ниже линии
✅ ST3 (Trig): сработал
✅ EMA 200: цена ниже EMA (для SHORT это правильно, для LONG нужно выше)

ИТОГ: ✅ Все настройки работают корректно
======================================================================
```

### Добавление новых параметров:
При добавлении новой настройки бота - СРАЗУ добавить в аудит:
1. В `engine.py _execute_entry()` → добавить в `bot_settings_for_audit`
2. В `trades_audit.py` → добавить вывод в соответствующую секцию
3. Если параметр проверяется на бирже → добавить сравнение и автоисправление

---

## 20. Стратегия: 3 EMA Pullback Scalping

### Описание
Скальпинг-стратегия на откатах в тренде. Использует 3 EMA (50/100/150) для определения тренда и точек входа.

### Логика LONG (ИСПРАВЛЕНО 2026-01-22):
1. **Тренд вверх**: EMA50 > EMA100 > EMA150, наклон положительный (нормализован в %), EMA раздвинуты
2. **Откат**: НЕПРЕРЫВНОЕ окно свечей где цена была ниже EMA50
3. **Тест EMA100**: цена касается EMA100 (допуск 0.1%), но close остаётся ВЫШЕ (не пробивает)
4. **Запрет пробоя EMA150**: откат НЕ ПРОБИВАЕТ EMA150 (касание допустимо, пробой тенью — нет)
5. **Вход**: ПЕРВАЯ свеча которая открылась выше EMA50 (предыдущая обязательно в откате)
6. **SL**: под минимумом НЕПРЕРЫВНОГО отката + буфер
7. **TP**: R:R 1.5 или трейлинг (BE + подтяжка на новых high/low)

### Логика SHORT:
Зеркально LONG.

### Параметры:
- **EMA периоды**: 50 / 100 / 150 (настраиваемые)
- **Фильтр тренда**: наклон EMA > 0.1% за свечу (нормализован!), расстояние > 1x ATR
- **Касание EMA100**: допуск 0.1%, close не должен пробивать
- **Запрет пробоя EMA150**: тень не должна пробивать EMA150 (касание OK)
- **SL**: под/над НЕПРЕРЫВНЫМ откатом или фикс. %
- **TP**: трейлинг или фикс. R:R
- **Риск**: не более 2% на сделку

### ИСПРАВЛЕНИЯ 2026-01-22:

| # | Было | Стало |
|---|------|-------|
| 1 | EMA150: запрет касания | EMA150: запрет ПРОБОЯ (тень за EMA150) |
| 2 | detect_pullback() собирал ВСЕ свечи за EMA50 | detect_continuous_pullback() — только НЕПРЕРЫВНОЕ окно |
| 3 | check_entry_trigger() только open > EMA50 | + проверка что prev свеча была в откате (ПЕРВАЯ после отката) |
| 4 | check_ema100_test() сравнивал с EMA100[-1] | Каждую свечу сравнивает с EMA100 на момент этой свечи |
| 5 | Не было проверки пробоя EMA100 | check_ema100_not_breached() — close не должен уйти за EMA100 |
| 6 | Slope в абс. единицах (разный для BTC/EURUSD) | calculate_slope_percent() — в % за свечу (нормализован) |
| 7 | Расстояние EMA иногда сравнивало массивы | Всегда используются ema[-1] |
| 8 | risk_limits в settings | daily_stats отдельно с pre-trade guard |
| 9 | Трейлинг для SHORT неполный | Полная логика new_high_low и ema50 для SHORT |
| 10 | last_high/last_low не всегда инициализированы | Инициализация при открытии позиции |
| 11 | UI: "Запрет касания EMA150" | UI: "Запрет пробоя EMA150 (включая тени)" |

### Файлы:
- `/opt/aila/aila/strategies/__init__.py` — модуль стратегий
- `/opt/aila/aila/strategies/ema_pullback.py` — логика стратегии
- `/opt/aila/aila/strategies/ema_pullback_settings.py` — дефолтные настройки
- `/opt/aila/aila/strategies/ema_pullback_runner.py` — runner торгового цикла
- `/opt/aila/logs/strategy_3ema_audit.log` — логи стратегии

### Runner (торговый цикл):
Класс `EmaPullbackRunner` запускает независимый торговый цикл:
- `_main_loop()` — сканирование пар, генерация сигналов, открытие позиций
- `_trailing_loop()` — управление открытыми позициями (трейлинг SL, BE)
- `_execute_signal()` — размещение ордеров через Bybit API
- `_update_trailing()` — обновление SL при новых хаях/лоях

Глобальный runner: `get_runner()` / `set_runner()` в `ema_pullback_runner.py`

Интеграция в `run_web.py`:
- `start_ema_strategy()` — создаёт runner и запускает asyncio task
- `stop_ema_strategy()` — останавливает runner
- Callbacks привязаны в `main()` к `ema_strategy_state`

### API Endpoints:
- `GET /api/strategy/3ema_pullback/settings` — получить настройки
- `POST /api/strategy/3ema_pullback/settings` — сохранить настройки
- `GET /api/strategy/3ema_pullback/status` — статус стратегии (running/stopped)
- `POST /api/strategy/3ema_pullback/start` — запустить стратегию
- `POST /api/strategy/3ema_pullback/stop` — остановить стратегию
- `GET /api/strategy/3ema_pullback/positions` — открытые позиции стратегии

### UI:
- Кнопка "Стратегии" в навигации (выпадающий список)
- Окно настроек стратегии с кнопками Start/Stop
- Статус стратегии (Running/Stopped) отображается в модальном окне

---

## 21. AI Trade Module — Самообучающийся AI трейдер

### Описание:
Автономный AI-трейдер на базе Claude API. Анализирует рынок, принимает решения о входе/выходе, учится на своих ошибках.

### Архитектура:
```
/opt/aila/aila/ai_trade/
├── __init__.py              # Экспорт AIBrain
├── config.py                # Настройки, лимиты, режимы
├── claude_client.py         # Интеграция с Anthropic API
├── knowledge_base.py        # База знаний (хранит опыт)
├── market_scanner.py        # Сканер рынка (RSI, EMA, ATR)
├── brain.py                 # Главный мозг (использует AgentOrchestrator)
├── orchestrator.py          # Координатор мульти-агентной системы
├── risk_manager.py          # Жёсткие лимиты рисков
├── position_manager.py      # Управление позициями
├── learning_engine.py       # Обучение на результатах (legacy)
├── learning_cycles.py       # Циклы обучения (hourly/daily/weekly)
├── performance_tracker.py   # Трекер производительности
└── agents/
    ├── __init__.py
    ├── base_agent.py        # Базовый класс агента
    ├── trader.py            # TRADER — сканирует, находит возможности
    ├── reviewer.py          # REVIEWER — проверяет логику, R:R
    ├── risk_guard.py        # RISK_GUARD — VETO, лимиты, мониторинг
    ├── analyst.py           # ANALYST — анализ сделок, паттерны
    ├── logger_agent.py      # LOGGER — логи, алерты Telegram
    ├── mentor.py            # MENTOR — наставник, обучает TRADER
    ├── researcher.py        # RESEARCHER — режимы рынка, паттерны
    ├── whale_tracker.py     # WHALE_TRACKER — крупные игроки, потоки
    └── news_agent.py        # NEWS — новости, сентимент, breaking
```

### Мульти-агентный пайплайн:
```
WHALE_TRACKER ──┐
                ├→ TRADER (найти) → REVIEWER → RISK_GUARD → Execute
NEWS ───────────┘                                               ↓
                                                         ANALYST ← Close
RESEARCHER (режим рынка)                                    ↓
                                                     MENTOR (обучение)
                                                         ↓
                                              LOGGER (всё) → Telegram
```

### Агенты (9 шт.):
| Агент | Роль | Право VETO |
|-------|------|-----------|
| **TRADER** | Сканирует рынок, находит возможности | Нет |
| **REVIEWER** | Проверяет логику, R:R, ищет ошибки | REJECT/MODIFY |
| **RISK_GUARD** | Лимиты, мониторинг 24/7, force close | Абсолютное VETO |
| **WHALE_TRACKER** | Крупные транзакции, потоки на биржи, стакан | Нет |
| **NEWS** | Новости, сентимент, breaking news, Fear&Greed | Нет |
| **ANALYST** | Анализ сделок, паттерны, обучение | Нет |
| **LOGGER** | Логи для UI, алерты Telegram | Нет |
| **MENTOR** | Наставник: daily review, коррекция ошибок, правила | Нет |
| **RESEARCHER** | Режимы рынка, поиск паттернов, гипотезы | Нет |

### Внешние API (api_keys.py):
| Сервис | Назначение | Тип |
|--------|-----------|-----|
| Whale Alert | Крупные транзакции | Бесплатный tier |
| CryptoPanic | Новости крипторынка | Бесплатный tier |
| NewsAPI | Общие новости | Бесплатный tier |
| Fear & Greed | Индекс страха/жадности | Бесплатный |
| Glassnode | On-chain аналитика | Платный |

### Система самообучения (XP и уровни):
```
Уровень 1:  max_leverage=5x,  max_positions=1, risk=1%
Уровень 5:  max_leverage=10x, max_positions=2, risk=1.5%
Уровень 10: max_leverage=15x, max_positions=3, risk=2%
Уровень 20: max_leverage=20x, max_positions=4, risk=2.5%
Уровень 50: max_leverage=25x, max_positions=5, risk=3%
```

### Навыки трейдера:
- trend_detection, entry_timing, exit_timing
- risk_management, position_sizing, patience, adaptability

### XP награды/штрафы:
- Прибыльная сделка: +10 XP
- Серия 3/5 побед: +30/+50 XP
- Идеальный вход/выход: +15 XP
- Убыточная: -5 XP
- Нарушение правила: -20 XP
- Повтор ошибки: -30 XP

### Циклы обучения (LearningCycles):
- **После сделки**: анализ → XP → коррекция → level up
- **Каждый час**: определение режима рынка (RESEARCHER)
- **Каждый день (00:00 UTC)**: разбор дня (MENTOR)
- **Каждую неделю (воскресенье)**: глубокое обучение (MENTOR + RESEARCHER)

### Режимы работы:
- **OBSERVER** — только анализ, без сделок
- **ADVISOR** — предлагает сделки, пользователь подтверждает
- **AUTOPILOT** — полностью автономная торговля

### Жёсткие лимиты рисков (AI не может нарушить):
- Max leverage: 20x
- Max position size: 10% депозита
- Max daily loss: 5%
- Max drawdown: 15%
- Min balance: 10 USDT
- Max open positions: 3

### Данные:
- База знаний: `/opt/aila/data/ai_knowledge.json`
- Логи: `/opt/aila/logs/ai_trade.log`
- Логи агентов: `/opt/aila/logs/ai_trade/{agent}.log`

### Claude API:
- Модель: `claude-sonnet-4-20250514`
- API ключ: через переменную окружения `ANTHROPIC_API_KEY`

### Формат событий для веб-интерфейса:
```json
{
    "timestamp": "2026-01-24T15:30:00",
    "agent": "TRADER",
    "action": "OPPORTUNITY_FOUND",
    "data": {"pair": "BTCUSDT", "direction": "LONG", "confidence": 85},
    "message": "Opportunity found: LONG BTCUSDT, confidence 85%"
}
```

---

## 22. Shared Utils — /opt/aila/aila/utils/common.py

### Добавлено при оптимизации (2026-01-24):
```python
retry_async(max_attempts=3, base_delay=1.0, max_delay=30.0, exceptions=(Exception,))
# Декоратор для retry с экспоненциальным backoff

TTLCache(default_ttl=60.0)
# In-memory кэш с TTL: get(key, ttl), set(key, value), invalidate(key), cleanup()

RateLimiter(max_requests=10, window_seconds=1.0)
# Скользящее окно rate limiting: await acquire()

clamp(value, min_val, max_val)
# Ограничение значения в диапазон [min, max]
```

### Оптимизации в AI Trade модуле:
- `claude_client.py`: AsyncAnthropic + @retry_async(3 попытки)
- `market_scanner.py`: TTLCache (30s для пар, 5s для цен)
- `whale_tracker.py`: aiohttp.ClientSession + TTLCache (60s whale, 120s transactions)
- `news_agent.py`: aiohttp.ClientSession + TTLCache (120s news, 300s sentiment)
- `position_manager.py`: @retry_async(2 попытки) на open/close
- Все классы: `__slots__` для оптимизации памяти
- Все файлы: современные type hints (dict, list вместо Dict, List)
- `learning_cycles.py`: static helpers (_detect_skill, _seconds_until_*)

---

**Последнее обновление:** 2026-01-24
**Текущая версия:** v2.2.0 (см. файл `/opt/aila/VERSION`)
**Рабочая ветка:** `claude/start-new-session-4XrKU`
