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
6. **НЕ меняй критические настройки риск-менеджмента:**
   - `min R:R ratio` в reviewer.py (1.5:1) — защита от плохих сделок
   - `min_balance_usdt` в config.py ($10) — минимум для торговли
   - `MIN_ORDER_SIZE_USDT` ($10) — лимит Bybit API, нельзя обойти

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

## 2.1 ⚠️ ПРАВИЛА РАБОТЫ С AI TRADE — ЗАПРЕЩЕНО МЕНЯТЬ БЕЗ СОГЛАСОВАНИЯ:

### Параметры торговой стратегии:
- `min_confidence` (порог уверенности) — config.py, autopilot_mode.py
- `min_rr_ratio` (минимальный Risk/Reward 1.5:1) — reviewer.py
- Веса индикаторов и их влияние на confidence
- Логику расчёта confidence в TRADER
- Логику REVIEWER (критерии одобрения/отклонения)
- Логику RISK_GUARD (лимиты и вето)

### Параметры риск-менеджмента (config.py):
- `max_leverage`, `max_positions`, `max_risk_pct`
- `max_daily_loss_pct`, `max_drawdown_pct`
- `min_balance_usdt` ($10), `MIN_ORDER_SIZE_USDT` ($10 — лимит Bybit API)
- Level benefits (бонусы за уровни) — knowledge_base.json

### Параметры обучения (knowledge_base.py, learning_cycles.py):
- XP rewards/penalties
- Формулу повышения уровня (×1.3 каждый уровень)
- Логику MENTOR и ANALYST

### ✅ Можно менять БЕЗ согласования:
- Исправление багов (неработающий код)
- Добавление логирования
- UI/интерфейс
- Новый функционал (по запросу пользователя)

### 🚨 ПРАВИЛО: Перед любым изменением торговой логики — СПРОСИТЬ ПОЛЬЗОВАТЕЛЯ!

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
├── capital_manager.py       # CAPITAL_MANAGER — Kelly Criterion, compound growth
├── strategy_evolution.py    # STRATEGY_EVOLUTION — генетические алгоритмы стратегий
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
    ├── news_agent.py        # NEWS — новости, сентимент, breaking
    ├── predictor.py         # PREDICTOR — предсказание движений, развороты
    ├── sniper.py            # SNIPER — мгновенные входы, пробои, ликвидации
    ├── arbitrage.py         # ARBITRAGE — арбитраж: funding, cross-exchange, triangular
    ├── hedge_master.py      # HEDGE_MASTER — хеджирование, защита портфеля
    └── war_room.py          # WAR_ROOM — кризисное управление, чёрные лебеди
├── exchanges/
    ├── __init__.py
    ├── base_exchange.py     # Абстрактный интерфейс биржи
    ├── bybit_exchange.py    # Реализация Bybit
    └── multi_exchange.py    # Мульти-биржевой менеджер
```

### Мульти-агентный пайплайн:
```
WHALE_TRACKER ──┐
PREDICTOR ──────┤
                ├→ TRADER (найти) → REVIEWER → RISK_GUARD → Execute
NEWS ───────────┘                                               ↓
                                                         ANALYST ← Close
SNIPER (пробои, ликвидации) ─→ Execute                      ↓
ARBITRAGE (funding, cross-ex) ─→ Execute              MENTOR (обучение)
RESEARCHER (режим рынка)
HEDGE_MASTER (защита портфеля, emergency hedge)          ↓
WAR_ROOM (кризис, чёрные лебеди, экстренные протоколы)
CAPITAL_MANAGER (Kelly, sizing, compound)     LOGGER (всё) → Telegram
STRATEGY_EVOLUTION (генетические алгоритмы, оптимизация)
```

### Агенты (16 шт.):
| Агент | Роль | Право VETO |
|-------|------|-----------|
| **TRADER** | Сканирует рынок, находит возможности | Нет |
| **REVIEWER** | Проверяет логику, R:R, ищет ошибки | REJECT/MODIFY |
| **RISK_GUARD** | Лимиты, мониторинг 24/7, force close | Абсолютное VETO |
| **WHALE_TRACKER** | Крупные транзакции, потоки на биржи, стакан | Нет |
| **NEWS** | Новости, сентимент, breaking news, Fear&Greed | Нет |
| **PREDICTOR** | Предсказание движений: TA + AI, развороты, паттерны | Нет |
| **SNIPER** | Мгновенные входы: пробои, ликвидации, funding flip | Нет |
| **ARBITRAGE** | Арбитраж: funding rate, cross-exchange, triangular | Нет |
| **HEDGE_MASTER** | Хеджирование, защита портфеля, market-neutral | Нет |
| **WAR_ROOM** | Кризисное управление, чёрные лебеди, экстренные протоколы | Нет |
| **CAPITAL_MANAGER** | Kelly Criterion, compound growth, sizing, фазы | Нет |
| **STRATEGY_EVOLUTION** | Генетические алгоритмы, оптимизация стратегий | Нет |
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
- **IDLE** — система ожидает, никаких действий
- **AUTOPILOT** — полностью автономная торговля и обучение

> **ВАЖНО**: Observer mode удалён (2026-01-27). Теперь только IDLE и AUTOPILOT.

### Жёсткие лимиты рисков (AI не может нарушить):
- Max leverage: 20x
- Max position size: 10% депозита
- Max daily loss: 5%
- Max drawdown: 15%
- Min balance: 10 USDT
- Max open positions: 3
- **Min order size: $10 USDT** (лимит Bybit Futures)

### Данные:
- База знаний: `/opt/aila/data/ai_knowledge.json`
- Логи: `/opt/aila/logs/ai_trade.log`
- Логи агентов: `/opt/aila/logs/ai_trade/{agent}.log` (включая hedge_master.log)

### Claude API (ОПТИМИЗАЦИЯ 2026-01-27):
**Модели:**
- `claude-sonnet-4-20250514` — критичные агенты (TRADER, REVIEWER, PREDICTOR, WHALE_TRACKER, WAR_ROOM)
- `claude-haiku-4-5-20251001` — вспомогательные агенты (NEWS, MENTOR, ANALYST)

**Оптимизация batch-анализа:**
- TRADER: вместо 10 отдельных запросов → **1 batch-запрос со всеми 50 парами**
- Экономия: ~90% снижение стоимости Claude API

**Кэширование (увеличенный TTL):**
- WHALE_TRACKER: 60s → **300s (5 мин)**
- NEWS pair_sentiment: 120s → **300s (5 мин)**
- NEWS market_sentiment: 300s → **600s (10 мин)**
- PREDICTOR: 30s → **180s (3 мин)**

**Счётчик API:**
- `get_api_usage()` в `claude_client.py` — статистика вызовов и стоимости
- API endpoint: `GET /api/ai-trade/status` включает `api_usage` с подсчётом cost per hour

**Файлы:**
- `claude_client.py` — batch_analyze_market(), APIUsageCounter, use_haiku параметр
- `config.py` — CLAUDE_MODEL_HAIKU
- `orchestrator.py` — get_api_usage_stats()

**Мониторинг расходов API (добавлено 2026-01-27):**
- **Виджет на главном экране AI Trade** — показывает расход, бюджет, прогресс-бар
- Индикатор в хедере: показывает расход за день
- Settings modal: секция "Claude API" с детальной статистикой + Top consumers
- Файл данных: `/opt/aila/data/ai_trade/api_usage.json`
- API endpoint: `GET /api/ai-trade/api-usage` (includes top_consumers, today_top_consumers, week, budget)
- Лимиты: $5/день (warning), $100/месяц (warning)
- Endpoint для изменения лимитов: `PUT /api/ai-trade/api-usage/limits`
- Endpoint для установки бюджета: `PUT /api/ai-trade/api-usage/budget?budget=10`

**Виджет Claude API Usage (добавлено 2026-01-27):**
- Расположение: главный экран AI Trade, рядом с Sentiment и Prediction
- Прогресс-бар бюджета с цветовой индикацией:
  - Зелёный: < 80% бюджета
  - Жёлтый: >= 80% бюджета (предупреждение)
  - Красный: > 100% бюджета (превышен)
- Статистика:
  - Расход сегодня: $X.XX
  - Расход за неделю: $X.XX
  - Вызовов сегодня: X (Sonnet: Y, Haiku: Z)
  - Токенов: input X / output Y
  - Остаток бюджета: $X.XX
- Кнопка "Set Budget" — модальное окно для установки дневного бюджета
- Автообновление каждые 30 секунд

**Детальное логирование токенов:**
- Лог файл: `/opt/aila/logs/ai_trade/api_usage.log`
- Формат: `[timestamp] AGENT action | model | input | output | cost | context`
- Пример: `[2026-01-27 12:00:00] TRADER batch_analyze | model=sonnet | input=3500 | output=800 | cost=$0.0225 | pairs=50`
- Статистика по агентам: calls, cost, avg_cost, avg_input, avg_output, pct_of_total

**ВАЖНО:** Ключ НЕ хранится в репозитории, только в .env на сервере

### Telegram уведомления AI Trade:
- Bot: `@aila_ai_trade_bot`
- Настройки в `.env`: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
- **Модуль:** `aila/ai_trade/telegram_notifier.py`
- **События:**
  - Открытие позиции (symbol, direction, size, entry, SL, TP)
  - Закрытие позиции (PnL, причина)
  - Stop Loss / Take Profit
  - Критические ошибки
  - Смена режима (IDLE → AUTOPILOT)
- **Интеграция:** LoggerAgent получает telegram_bot в orchestrator.py

### Веб-интерфейс AI Trade:
- **Страница**: `/ai-trade` (требует авторизации)
- **API**: `/api/ai-trade/*` (роутер в `aila/api/routes/ai_trade.py`)
- **Шаблон**: `aila/api/templates/ai_trade.html` (Tailwind CSS + Font Awesome)
- **Endpoints**: status, agents, profile, portfolio, opportunities, market-context, prediction, sentiment, whale, capital, health, evolution, logs, settings, mode
- **Кнопка режима**: AUTOPILOT (fa-robot), статус IDLE/AUTOPILOT показан над кнопкой
- **Подтверждение AUTOPILOT**: модальное окно с предупреждением перед включением автоторговли
- **Локализация RU/EN**: читает язык из localStorage/cookie `ailaLang` (синхронизация с основным ботом)
- **Settings Panel** (добавлено 2026-01-26): кнопка "Settings" в хедере, модальное окно со ВСЕМИ параметрами:
  - Trading: min_confidence, scan_interval, top_pairs_count, min_volume_24h, volatility range
  - Risk Limits: max_leverage, max_position_pct, max_daily_loss_pct, max_drawdown_pct, min_balance
  - Level Limits: текущие лимиты по уровню AI (leverage, positions, risk_per_trade)
  - Autopilot: max_trades_per_hour/day, cooldown_after_loss, require_confirmations
  - Filters: min_volume_24h, min_volatility_pct, max_volatility_pct
  - Indicators: RSI, EMA, ATR настройки для сканера
  - Integrations: whale_alert_api, news_api, telegram_bot — статус подключения
  - Capital & Persistence: текущий баланс, статус S3 бэкапа, auto_save
  - API endpoint: `GET /api/ai-trade/settings/full`

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

## 23. AUTOPILOT Mode — Полностью автономная торговля

### Описание:
Режим полной автоматизации торговли. AI принимает решения и торгует самостоятельно в рамках риск-лимитов.

### Файлы:
- `aila/ai_trade/autopilot_mode.py` — логика автопилота

### Pre-flight checks (проверки перед запуском):
1. System health check (war_room)
2. Market safety check (не в кризисе)
3. Balance check (минимум $5 для тестирования, в проде $50+)
4. AI level check (минимум уровень 1 для тестирования, в проде 3+)

### Конфигурация:
```python
{
    'scan_interval_seconds': 60,      # Интервал сканирования
    'min_confidence': 70,             # Минимальный confidence для входа
    'max_trades_per_hour': 5,         # Макс. сделок в час
    'max_trades_per_day': 20,         # Макс. сделок в день
    'cooldown_after_loss_minutes': 30, # Пауза после убытка
    'require_multiple_confirmations': True  # Требовать подтверждения от нескольких агентов
}
```

### Логика входа:
1. Проверка лимитов (trades_this_hour, trades_today)
2. Проверка market safety
3. Поиск opportunity через TRADER
4. Валидация через REVIEWER
5. Риск-проверка через RISK_GUARD
6. Множественные подтверждения (whale, prediction, sentiment)
7. Исполнение через position_manager

### API Endpoints:
- `POST /api/ai-trade/autopilot/start` — запустить автопилот
- `POST /api/ai-trade/autopilot/stop` — остановить автопилот
- `GET /api/ai-trade/autopilot/status` — статус автопилота
- `PUT /api/ai-trade/autopilot/config` — обновить конфиг
- `GET /api/ai-trade/autopilot/heartbeat` — real-time статус для UI индикатора

### Visual Activity Indicator (добавлено 2026-01-27):
**Визуальный индикатор активности AUTOPILOT в UI:**

1. **Пульсирующий круг** рядом с кнопкой AUTOPILOT:
   - Зелёный + пульсация = активен
   - Быстрая анимация = сканирует
   - Оранжевый = предупреждение (>120 сек без скана)
   - Серый = остановлен

2. **Status Bar внизу экрана:**
   - Слева: иконка состояния + текст "AUTOPILOT активен/остановлен"
   - При сканировании: спиннер + текущая пара
   - Справа: количество пар, время с последнего скана

3. **Heartbeat endpoint возвращает:**
   ```json
   {
     "running": true,
     "last_scan_time": "2026-01-27T17:15:30",
     "seconds_since_last_scan": 45.2,
     "currently_scanning": false,
     "current_pair": null,
     "pairs_count": 50,
     "scan_interval": 60
   }
   ```

4. **JavaScript polling каждые 5 секунд** для обновления индикатора

**Файлы:**
- `aila/ai_trade/autopilot_mode.py` — добавлены `get_heartbeat()`, tracking scan time
- `aila/api/routes/ai_trade.py` — добавлен endpoint `/autopilot/heartbeat`
- `aila/api/templates/ai_trade.html` — добавлен status bar, CSS анимации, JS polling

---

## 24. Scaling Manager — Автоматическое масштабирование

### Описание:
Автоматическое увеличение размеров позиций по мере роста капитала и уровня AI.

### Файлы:
- `aila/ai_trade/scaling_manager.py` — логика масштабирования

### Фазы масштабирования:
| Фаза | Капитал | Уровень | Плечо | Риск | Позиций | Цель/мес |
|------|---------|---------|-------|------|---------|----------|
| Starter | $0-1K | 1+ | 10x | 2% | 2 | 50% |
| Growth | $1K-5K | 5+ | 10x | 1.5% | 3 | 40% |
| Established | $5K-20K | 10+ | 7x | 1% | 4 | 30% |
| Professional | $20K-100K | 20+ | 5x | 0.5% | 5 | 20% |
| Institutional | $100K+ | 50+ | 3x | 0.25% | 6 | 10% |

### Методы:
- `get_current_phase()` — определяет текущую фазу
- `get_recommended_settings()` — рекомендуемые настройки
- `check_upgrade_eligibility()` — готовность к переходу на следующую фазу
- `calculate_growth_projection(months)` — проекция роста капитала

### API Endpoints:
- `GET /api/ai-trade/scaling` — полная информация о масштабировании
- `GET /api/ai-trade/scaling/projection?months=12` — проекция роста
- `GET /api/ai-trade/scaling/phases` — все фазы

---

## 25. PERSISTENCE — Сохранение данных

### Описание:
Полное сохранение ВСЕХ данных AI Trade. Локально каждые 60 сек + Yandex Object Storage каждый час.
При перезагрузке сервера ВСЕ данные восстанавливаются автоматически.

### Файлы:
- `aila/ai_trade/persistence.py` — логика сохранения

### Локальное сохранение:
- Папка: `/opt/aila/data/ai_trade/`
- Интервал: каждые 60 секунд
- Файлы: knowledge_base.json, trading_state.json, paper_trading.json, observer.json, evolution.json, risk_stats.json, autopilot.json, war_room.json, capital.json

### Yandex Object Storage бэкап:
- Bucket: `aila-backups`
- Endpoint: `https://storage.yandexcloud.net`
- Region: `ru-central1`
- Интервал: каждый час
- Хранится: последние 24 бэкапа
- Формат: `ai_trade_backup_YYYY-MM-DD_HH-MM.json`
- Библиотека: boto3 (S3-совместимый API)
- **Размер бэкапа: ~5 KB** (используется whitelist файлов)

### ИСПРАВЛЕНО 2026-01-26: Огромные бэкапы
**Проблема:** бэкапы росли с 5KB до 1.5GB (экспоненциальный рост)
**Причина:** фильтр пропускал `ai_trade_backup_*`, но локальные бэкапы назывались `aila_backup_*`
**Решение:** whitelist файлов в `persistence.py:BACKUP_FILES`

### Что сохраняется:
- **Knowledge Base**: уровень, XP, навыки, правила, история обучения
- **Trading state**: режим работы (IDLE/AUTOPILOT)
- **Paper trading**: баланс, сделки, позиции
- **Strategy evolution**: поколение, популяция
- **Risk stats**: PnL, trades, peak balance
- **Autopilot**: stats, config
- **War room**: алерты, crisis mode
- **Capital manager**: total, config

### Инициализация при старте (ИСПРАВЛЕНО 2026-01-26):
Orchestrator и persistence инициализируются автоматически при старте FastAPI:
- `aila/api/main.py` — в `startup_event()` вызывается `get_orchestrator()`
- Это запускает `start_persistence()` сразу при старте сервера
- Ранее orchestrator инициализировался лениво (при первом API запросе), что приводило к `s3_connected: false` и `auto_save_running: false`

### Восстановление:
При старте бота автоматически:
1. Инициализируется AgentOrchestrator в startup_event
2. Запускается persistence с auto_save (каждые 60 сек)
3. Подключается S3 клиент для Yandex Object Storage
4. Загружаются данные из `/opt/aila/data/ai_trade/`
5. AI продолжает с того же места

### API Endpoints:
- `GET /api/ai-trade/persistence/status` — статус системы
- `POST /api/ai-trade/persistence/save` — принудительное сохранение
- `POST /api/ai-trade/persistence/backup` — принудительный бэкап в облако

---

## 26. Systemd — Управление ботом

### Сервис:
- Файл: `/etc/systemd/system/aila.service`
- User: `aila`
- Автозапуск: включен (enabled)

### Команды управления:
```bash
sudo systemctl status aila    # статус
sudo systemctl start aila     # запуск
sudo systemctl stop aila      # остановка
sudo systemctl restart aila   # перезапуск
sudo journalctl -u aila -f    # логи в реальном времени
sudo journalctl -u aila -n 100  # последние 100 строк логов
```

### При перезагрузке сервера:
- Бот запускается автоматически
- Загружает данные из `/opt/aila/data/ai_trade/`
- AI продолжает работу без потери данных

---

---

## 27. Position Conflict Protection — Защита от конфликта позиций

### Описание:
Предотвращает одновременное открытие позиций по одной паре основным ботом и AI Trade.

### Файлы:
- `aila/utils/position_conflict.py` — общий модуль проверки конфликтов
- `aila/ai_trade/position_manager.py` — использует проверку
- `aila/trading/engine.py` — использует проверку

### Как работает:
```python
# Перед открытием позиции (AI Trade или основной бот):
if check_position_conflict(symbol):
    logger.warning(f"Position conflict: {symbol} already has open position")
    return None  # Не открываем позицию
```

### Функции (position_conflict.py):
- `check_position_conflict(symbol)` — возвращает True если позиция существует
- `get_all_open_positions()` — возвращает список всех открытых позиций

### Логика:
1. Создаётся singleton Bybit клиент с API ключами из .env
2. При вызове `check_position_conflict(symbol)`:
   - Запрашивает позиции с Bybit API
   - Проверяет есть ли позиция с size > 0
   - Возвращает True (конфликт) или False (безопасно)
3. Обе системы проверяют перед открытием позиции

---

## 28. AI Trade — Real Bybit Integration

### ИСПРАВЛЕНО 2026-01-26:
AI Trade теперь подключён к реальному Bybit с API ключами.

### Изменения:
1. **orchestrator.py** — загружает API ключи из .env, создаёт BybitExchange с credentials
2. **ai_trade.py routes** — создаёт pybit.HTTP клиент с API ключами
3. **position_manager.py** — проверяет конфликт позиций перед открытием
4. **risk_manager.py** — добавлен `update_level_limits()` для level-based рисков
5. **capital_manager** — инициализируется с реальным балансом при старте

### Проверка:
```bash
curl -s http://localhost:8080/api/ai-trade/capital | python3 -m json.tool
# Должен показать реальный баланс USDT
```

### ВАЖНО:
После изменений требуется перезапуск бота:
```bash
sudo systemctl restart aila
```

---

### ИСПРАВЛЕНО 2026-01-26 (session):

1. **autopilot/status API error** — `'str' object has no attribute 'isoformat'`
   - Проблема: при загрузке данных из JSON, `last_hour_reset` уже строка, не datetime
   - Решение: проверка `hasattr(obj, 'isoformat')` перед вызовом
   - Файл: `aila/ai_trade/autopilot_mode.py:get_status()`

2. **Arbitrage symbol format** — `symbol invalid (ErrCode: 10001)` для LINK/USDT
   - Проблема: Bybit API ожидает LINKUSDT, а не LINK/USDT
   - Решение: `normalized_pair = pair.replace("/", "")`
   - Файл: `aila/ai_trade/agents/arbitrage.py:_analyze_funding()`

3. **fetch_order_book** — уже добавлен в BybitExchange (строки 59-61)
   - Ошибки в логах whale_tracker были от старого кода до рестарта

---

---

## 29. Dynamic Pairs Scanning — Динамическое сканирование пар

### Описание:
AI Trade автоматически получает список ВСЕХ USDT perpetual фьючерсов с Bybit и сканирует их динамически. Список обновляется каждый час.

### Изменения (2026-01-26):

**config.py:**
```python
SCANNER_CONFIG = {
    ...
    "scan_all_pairs": True,           # Сканировать ВСЕ USDT perpetual пары
    "pairs_cache_ttl": 3600,          # Кэш списка инструментов 1 час
}
```

**bybit_exchange.py:**
```python
async def get_usdt_perpetual_symbols() -> list[str]:
    """Получить все активные USDT perpetual пары с Bybit."""
    # GET /v5/market/instruments-info?category=linear
    # Фильтр: status=Trading, quoteCoin=USDT, contractType=LinearPerpetual

async def set_leverage(leverage: int, symbol: str) -> bool:
    """Установить плечо для символа (POST /v5/position/set-leverage)."""
    # Конвертирует symbol из формата BTC/USDT в BTCUSDT
    # Устанавливает buyLeverage и sellLeverage одинаковыми
    # Возвращает True если успешно (включая случай когда уже установлено)
```

**market_scanner.py:**
```python
async def _refresh_instruments() -> list[str]:
    """Обновить список инструментов с кэшированием."""
    # Кэш на pairs_cache_ttl секунд
    # Логирование: "Loaded X USDT perpetual pairs from Bybit"
    # При обновлении: "Pairs updated: +Y new, -Z delisted"
```

### Как работает:
1. При первом сканировании загружается полный список пар (~400+)
2. Пары фильтруются по volume ($5M+) и volatility (1-15%)
3. Результат: ~50 пар для детального анализа (вместо фиксированных 20)
4. Список обновляется каждый час (новые добавляются, делистнутые удаляются)

### Логи:
```
[INFO] Loaded 412 USDT perpetual pairs from Bybit
[INFO] Scanning 50 pairs for opportunities
[INFO] Pairs updated: +2 new, -1 delisted
```

---

## 30. ИСПРАВЛЕНИЕ 2026-01-27: AI Trade не открывал сделки

### Проблема:
Автопилот находил сигналы (LONG 1000PEPE/USDT @ confidence=75%), но сделки не открывались.

### Причины:
1. **Отсутствующий метод validate_trade()** — в `autopilot_mode.py` вызывался `risk_guard.validate_trade()`, но в `risk_guard.py` был только метод `check()`. Это вызывало AttributeError.

2. **Плохой R/R ratio** — TRADER генерировал сделки с R/R 1.2-1.4 (требуется минимум 1.5:1).

3. **Высокий leverage 3x** — для волатильных монет (PEPE, SKR) leverage 3x слишком высокий.

4. **Слишком близкий stop loss** — 2-2.5% от цены входа недостаточно для мем-коинов.

### Исправления:

**1. risk_guard.py** — добавлен метод `validate_trade()`:
```python
async def validate_trade(self, opportunity: dict) -> dict:
    """Alias for check() with auto balance fetch."""
    balance = 100
    if self.orchestrator and hasattr(self.orchestrator, 'exchanges'):
        try:
            balance = await self.orchestrator.exchanges.primary.get_balance('USDT')
        except Exception as e:
            self.log(f"Failed to get balance: {e}", "warning")
    return await self.check(opportunity, balance)
```
+ добавлен параметр `orchestrator` в `__init__`

**2. orchestrator.py** — передаётся `orchestrator=self` при создании RiskGuardAgent

**3. claude_client.py** — улучшен промпт для TRADER:
```
## RISK MANAGEMENT RULES (MANDATORY)
1. Risk/Reward ratio MUST be >= 1.5:1
2. Stop loss distance: minimum 3% for volatile, 2% for stable
3. Leverage: max 2x for meme/volatile, max 3x for majors (BTC, ETH, BNB)
4. Position size: 2-4% of capital
```

**4. reviewer.py** — улучшен промпт для REVIEWER:
- Добавлено требование заполнять ВСЕ 4 поля modifications при MODIFY
- Добавлены REQUIRED THRESHOLDS секция

**5. autopilot_mode.py** — добавлено логирование в файл `/opt/aila/logs/ai_trade/autopilot.log`

### Результат:
После применения изменений и перезапуска бота (`sudo systemctl restart aila`), AI Trade должен:
- Генерировать сделки с R/R >= 1.5
- Использовать leverage 2-3x в зависимости от волатильности
- Ставить stop loss минимум 3% от входа
- Применять modifications при MODIFY

---

## 31. ИСПРАВЛЕНИЕ 2026-01-27: Улучшение качества сигналов TRADER

### Проблема:
TRADER генерировал сигналы которые REVIEWER отклонял из-за:
1. R/R ratio ниже 1.5:1 (1.2-1.4)
2. LONG против BEARISH тренда
3. Слишком близкий Stop Loss (2-2.5%)

### Решение — Pre-filtering в trader.py:

**1. Фильтр направления тренда:**
```python
# PRE-FILTER: Skip LONG signals against BEARISH trend
if decision == "LONG" and trend == "BEARISH":
    self.log(f"{symbol}: LONG vs BEARISH trend - skipping")
    decision = "WAIT"

# PRE-FILTER: Skip SHORT signals against BULLISH trend
if decision == "SHORT" and trend == "BULLISH":
    self.log(f"{symbol}: SHORT vs BULLISH trend - skipping")
    decision = "WAIT"
```

**2. Валидация и автокорректировка R:R ratio:**
```python
MIN_RR_RATIO = 1.5  # Минимальный R:R
MIN_SL_DISTANCE_PCT = 2.0  # Минимальный SL 2%

def _validate_and_adjust_rr(analysis, market_data, symbol):
    # 1. Проверяет SL distance >= 2%
    # 2. Если SL слишком близко — корректирует
    # 3. Вычисляет R:R ratio
    # 4. Если R:R < 1.5 — корректирует TP
    # 5. Если корректировка TP > 10% — отклоняет сигнал
```

**3. Логирование корректировок:**
```
[TRADER] SKR/USDT: SL too tight (1.5%), adjusted to 2.0%
[TRADER] SKR/USDT: R:R adjusted from 1.2:1 to 1.5:1 (TP: 0.0248 -> 0.0256)
[TRADER] ENSO/USDT: LONG vs BEARISH trend - skipping
```

### Файлы:
- `aila/ai_trade/agents/trader.py` — добавлены `MIN_RR_RATIO`, `MIN_SL_DISTANCE_PCT`, `_validate_and_adjust_rr()`

### Результат:
- Сигналы с R:R < 1.5 автоматически корректируются или отклоняются
- LONG vs BEARISH / SHORT vs BULLISH отсекаются до отправки в REVIEWER
- REVIEWER получает только качественные сигналы
- Меньше ненужных вызовов Claude API (экономия токенов)

---

## 32. ИСПРАВЛЕНИЕ 2026-01-27: Баги AUTOPILOT

### Обнаруженные проблемы:
1. **Дублирование сканов** — Observer и Autopilot работали параллельно, каждый скан выполнялся дважды
2. **last_scan_time: null** — heartbeat API показывал null потому что exception прерывал цикл до записи времени
3. **KnowledgeBase error** — отсутствовал метод `get_trader_profile()` для pre-flight check
4. **Нечёткие rejection причины** — сигналы отфильтрованные по тренду (LONG vs BEARISH) показывались как "no clear signal"

### Исправления:

**1. autopilot_mode.py** — остановка Observer при запуске Autopilot:
```python
async def start(self):
    # Stop observer if running to prevent duplicate scans
    if hasattr(self._orchestrator, 'observer') and self._orchestrator.observer._running:
        await self._orchestrator.observer.stop()
```

**2. autopilot_mode.py** — try-finally для last_scan_time:
```python
try:
    opportunity = await self._find_validated_opportunity()
finally:
    # Always update scan time, even on error
    self._currently_scanning = False
    self._last_scan_time = datetime.utcnow()
```

**3. observer_mode.py** — остановка Autopilot при запуске Observer:
```python
async def start(self):
    # Stop autopilot if running to prevent duplicate scans
    if hasattr(self._orchestrator, 'autopilot') and self._orchestrator.autopilot._running:
        await self._orchestrator.autopilot.stop()
```

**4. knowledge_base.py** — добавлен метод:
```python
async def get_trader_profile(self) -> dict:
    """Get full trader profile for autopilot pre-flight check."""
    return self.data.get("trader_profile", self._default_structure()["trader_profile"])
```

**5. trader.py** — улучшено логирование rejection reason:
```python
# При фильтрации по тренду сохраняется причина
analysis["_rejection_reason"] = "LONG vs BEARISH trend"
# В логах теперь отображается точная причина
```

### Результат:
- ✅ Один скан = одна запись в логе (вместо двух)
- ✅ heartbeat показывает актуальное last_scan_time
- ✅ Pre-flight check работает без ошибок
- ✅ Логи показывают точную причину rejection

### Файлы изменены:
- `aila/ai_trade/autopilot_mode.py`
- `aila/ai_trade/observer_mode.py`
- `aila/ai_trade/knowledge_base.py`
- `aila/ai_trade/agents/trader.py`

---

## 32. ИСПРАВЛЕНИЕ 2026-01-27: Balance check с учётом leverage + дублирование сканов

### Проблема 1: RISK_GUARD блокировал сделки при достаточном балансе
```
VETO: Balance $8.72 below minimum $10
```
При балансе $8.72 и leverage 2x, минимальная маржа = $10 / 2 = $5. Баланса хватало!

### Проблема 2: Двойное сканирование
Каждый цикл сканирования выполнялся дважды — Observer и Autopilot работали параллельно.

### Исправления:

**1. risk_guard.py** — balance check с учётом leverage:
```python
# Было:
if balance < RISK_LIMITS["min_balance_usdt"]:  # $10
    return VETO

# Стало:
min_order_size = RISK_LIMITS["min_balance_usdt"]  # $10 min order (Bybit limit)
min_margin_needed = min_order_size / max(leverage, 1)  # With leverage
if balance < min_margin_needed:
    return VETO
```

**2. autopilot_mode.py и observer_mode.py** — защита от дублирования:
```python
async def start(self):
    # Prevent duplicate starts
    if self._running:
        logger.warning("Already running, ignoring start request")
        return
    ...
```

### Результат:
- Баланс $8.72 с leverage 2x: min_margin = $10/2 = $5 → $8.72 > $5 ✅
- Один режим не может запуститься дважды
- Observer и Autopilot не работают параллельно

### Файлы изменены:
- `aila/ai_trade/agents/risk_guard.py`
- `aila/ai_trade/autopilot_mode.py`
- `aila/ai_trade/observer_mode.py`

---

## 33. РЕФАКТОРИНГ 2026-01-27: Удаление Observer mode

### Причина:
- Observer mode создавал путаницу (два режима с пересекающейся функциональностью)
- Проблема дублирования сканов (Observer + Autopilot параллельно)
- Упрощение интерфейса

### Изменения:
1. **orchestrator.py** — удалён import и инициализация ObserverMode
2. **autopilot_mode.py** — режим IDLE вместо OBSERVER при остановке
3. **ai_trade.py (routes)** — удалены endpoints `/observer/*`
4. **ai_trade.html** — удалена кнопка OBSERVER, одна кнопка AUTOPILOT

### Новые режимы:
- **IDLE** — система ожидает, ничего не делает
- **AUTOPILOT** — активная торговля и обучение

### UI:
- Одна кнопка AUTOPILOT (включить/выключить)
- Статус показывает IDLE (серый) или AUTOPILOT (зелёный)
- При остановке закрывает все позиции и отменяет ордера

### Файлы изменены:
- `aila/ai_trade/orchestrator.py`
- `aila/ai_trade/autopilot_mode.py`
- `aila/api/routes/ai_trade.py`
- `aila/api/templates/ai_trade.html`

---

## 34. SHORT позиции — улучшение промптов (2026-01-27)

### Проблема:
- Код полностью поддерживал SHORT, но Claude (trader AI) генерировал только LONG сигналы
- Даже для BEARISH пар возвращал LONG → блокировался проверкой тренда

### Диагностика:
| Компонент | LONG | SHORT |
|-----------|------|-------|
| Claude Prompt | ✅ | ✅ |
| trader.py | ✅ | ✅ |
| autopilot_mode.py | ✅ | ✅ |
| risk_guard.py | ✅ | ✅ |
| position_manager.py | ✅ | ✅ |
| bybit_exchange.py | ✅ | ✅ |

### Решение:
Добавлено в промпты `claude_client.py`:
```
- LONG: price > EMA50 > EMA200, RSI 40-70, trend=BULLISH
- SHORT: price < EMA50 < EMA200, RSI 30-60, trend=BEARISH (profit when price DROPS)
- Consider SHORT for BEARISH trends (downtrending pairs can be profitable!)
```

### Файлы изменены:
- `aila/ai_trade/claude_client.py` — улучшены промпты для SHORT

---

## 35. UI Polling — оптимизация API вызовов (2026-01-27)

### Проблема:
- UI делал polling каждые 30 сек на endpoints `/prediction/BTCUSDT` и `/whale/BTCUSDT`
- Эти запросы вызывали Claude API даже при выключенном автопилоте
- Лишние расходы на API (~$2/час при неактивном боте)

### Решение:

**1. ai_trade.html — проверка статуса автопилота:**
```javascript
async function updateDashboard() {
    const autopilotStatus = await fetchData('/autopilot/status');
    const isAutopilotRunning = autopilotStatus && autopilotStatus.running;

    if (isAutopilotRunning) {
        // Fetch fresh data from Claude API
        fetchData('/prediction/BTCUSDT');
        fetchData('/whale/BTCUSDT');
    } else {
        // Show cached data or "inactive" status
        showInactiveWidget('prediction', 'Prediction');
    }
}
```

**2. ai_trade.py — кэширование endpoints:**
```python
# Cache for inactive autopilot
_prediction_cache: dict[str, dict[str, Any]] = {}
_whale_cache: dict[str, dict[str, Any]] = {}
CACHE_TTL_SECONDS = 300  # 5 minutes

@router.get("/prediction/{pair}")
async def get_prediction(pair: str):
    if not await _is_autopilot_running():
        # Return cached data, no Claude API call
        return cached_data
    # Autopilot active - fetch fresh data
    result = await orch.predictor.predict_movement(pair)
    _prediction_cache[pair] = result
    return result
```

### Результат:
- При выключенном автопилоте: 0 API вызовов от UI polling
- При включённом: нормальная работа с обновлениями
- UI показывает "Cached (autopilot off)" вместо пустого поля

### Файлы изменены:
- `aila/api/routes/ai_trade.py` — кэширование + проверка autopilot
- `aila/api/templates/ai_trade.html` — условный polling

---

## 36. Диагностика Trade Pipeline (2026-01-28)

### Проблема: Сигналы с confidence 85% не открывают сделки

**Расследование показало:**
1. **TRADER** находит сигналы с 85% confidence (BTR/USDT, PUMPFUN/USDT)
2. **REVIEWER** отклоняет большинство — причины:
   - R/R ratio < 1.5:1
   - Trend = NEUTRAL (не BULLISH)
   - Price ниже EMA50 (противоречит reasoning)
   - Слишком большой предыдущий рост (90%+ за 24h)
3. **JTO/USDT прошёл все проверки** и дошёл до READY_TO_TRADE
4. **TRADE_FAILED** — position_manager не смог открыть позицию

### Исправления:

**1. position_conflict.py — нормализация символа:**
```python
# Было: symbol напрямую (JTO/USDT)
# Стало: bybit_symbol = symbol.replace("/", "")  # JTOUSDT
```

**2. position_manager.py — детальное логирование:**
- Логирование каждого этапа: conflict check, leverage, ticker, order
- Traceback при исключениях

**3. autopilot_mode.py — stage logging:**
- [STAGE 1] Trader scanning
- [STAGE 2] Reviewer decision
- [STAGE 3] Risk guard validation
- [STAGE 4] Market confirmations
- [STAGE 5] All validations passed
- [EXECUTE] Position opening

### Файлы изменены:
- `aila/utils/position_conflict.py` — fix: нормализация символа для Bybit API
- `aila/ai_trade/position_manager.py` — добавлено детальное логирование
- `aila/ai_trade/autopilot_mode.py` — добавлен stage logging для отладки

---

## 37. Исправление Qty Invalid (2026-01-28)

### Проблема:
```
Qty invalid: "26.157467957101755" — слишком много десятичных знаков
```

Bybit API требует округления qty до qtyStep (например 0.01 для JTO).

### Решение:

**bybit_exchange.py — новые методы:**
```python
async def get_instrument_info(symbol) -> dict:
    """Получает qtyStep, minQty, maxQty из /v5/market/instruments-info"""

async def get_qty_precision(symbol) -> float:
    """Возвращает qtyStep для символа"""

async def round_qty(symbol, qty) -> float:
    """Округляет qty до правильной точности (floor к qtyStep)"""
```

**position_manager.py — использование round_qty:**
```python
raw_amount = position_size_usdt / price
amount = await self._exchange.round_qty(symbol, raw_amount)
logger.info(f"[POSITION] Price: {price}, raw_qty: {raw_amount:.8f}, rounded_qty: {amount}")
```

### Файлы изменены:
- `aila/ai_trade/exchanges/bybit_exchange.py` — добавлены get_instrument_info, get_qty_precision, round_qty
- `aila/ai_trade/position_manager.py` — округление qty перед созданием ордера

---

## 37. Система качества кода AI Trade (2026-01-28)

### Создано:

**CLAUDE.md** — правила качества кода (читается автоматически каждую сессию):
- Правило 1: Чеклист методов BybitExchange (Market, Account, Orders, Position, Helpers)
- Правило 2: Округление qty и price перед созданием ордера
- Правило 3: Нормализация символов (BTC/USDT → BTCUSDT)
- Правило 4: Логирование Claude API (agent, action, context)
- Правило 5: Обработка ошибок (try/except для внешних вызовов)
- Правило 6: Тестирование перед деплоем
- Правило 7: Stage логирование в Autopilot [STAGE 1-5]
- Правило 8: Цветные метки логов [READY_TO_TRADE], [TRADE_OPENED], [TRADE_FAILED]
- Правило 9: Retry декоратор для API (@retry_async)
- Правило 10: Type hints и docstrings

**docs/AI_TRADE_API.md** — документация API агентов и exchange методов
**docs/EXCHANGE_CHECKLIST.md** — чеклист методов BybitExchange

**tests/test_bybit_exchange.py** — 16 тестов:
- test_has_market_data_methods
- test_has_account_methods
- test_has_order_methods
- test_has_position_methods
- test_has_helper_methods
- test_normalize_symbol (3 теста)
- test_round_qty (5 тестов)
- test_round_price (2 теста)
- test_cache_is_used

### Проверено:
- Все методы BybitExchange реализованы и работают
- round_qty и round_price используются в position_manager
- Stage логирование [STAGE 1-5] в autopilot_mode.py
- Цветные метки [READY_TO_TRADE], [TRADE_OPENED], [TRADE_FAILED]
- Тесты: 16 passed

### Запуск тестов:
```bash
cd /opt/aila && /opt/aila/venv/bin/python -m pytest tests/test_bybit_exchange.py -v
```

---

## 38. Улучшения блока Claude API (2026-01-28)

### Новые возможности:

**Кнопка сброса истории:**
- Иконка fa-rotate-right рядом с начальным балансом
- Модальное окно с полем ввода нового баланса
- API: `POST /api/ai-trade/api-usage/reset?initial_balance=50`

**Система бюджетных лимитов:**
- Toggle "Использовать лимит бюджета" (вкл/выкл)
- Дневной лимит (daily_limit) в USD
- Общий лимит (total_limit) в USD
- API: `PUT /api/ai-trade/api-usage/settings?use_budget_limit=true&daily_limit=10&total_limit=50`

**Автоостановка автопилота:**
- Если лимит достигнут и toggle включен — автопилот останавливается автоматически
- Telegram уведомление: "API LIMIT REACHED... Autopilot has been stopped automatically."
- Проверка лимита происходит при каждом вызове Claude API

**Логика при смене дня:**
- `accumulated_cost` — общий расход с момента последнего сброса (не сбрасывается при новом дне)
- `daily_cost` — расход за сегодня (сбрасывается при новом дне)
- `remaining_balance` = initial_balance - accumulated_cost

**Структура api_usage.json:**
```json
{
  "budget": {
    "initial_balance": 50.0,
    "accumulated_cost": 17.1,
    "last_reset_date": "2026-01-28T12:00:00",
    "daily_reset_date": "2026-01-28"
  },
  "settings": {
    "use_budget_limit": false,
    "daily_limit": 10.0,
    "total_limit": 50.0
  }
}
```

**UI компоненты:**
- Прогресс-бары для дневного и общего лимита
- Цветовая индикация: зеленый (<80%), желтый (80-99%), красный (>=100%)
- Отображение остатка баланса

### Файлы изменены:
- `aila/ai_trade/claude_client.py` — APIUsageTracker с новыми методами
- `aila/api/routes/ai_trade.py` — новые endpoints
- `aila/ai_trade/orchestrator.py` — set_api_usage_orchestrator
- `aila/api/templates/ai_trade.html` — UI + модалка сброса

---

## 19. Исправления багов

### fix: API usage reset button (2026-01-28)
**Проблема:** Модалка сброса истории API не открывалась при нажатии кнопки.

**Причина:** CSS класс `.modal-overlay` по умолчанию имеет `opacity: 0` и `visibility: hidden`. Для показа модалки нужен класс `.show`, но JavaScript использовал удаление класса `hidden` вместо добавления `show`.

**Исправление:**
- `openResetModal()`: изменено `classList.remove('hidden')` → `classList.add('show')`
- `closeResetModal()`: изменено `classList.add('hidden')` → `classList.remove('show')`
- Удалён лишний класс `hidden` из HTML элемента модалки

### refactor: Claude API settings modal (2026-01-28)
**Изменение:** Настройки Claude API вынесены из общих настроек в отдельную модалку карточки.

**Что сделано:**
- Добавлена иконка шестерёнки (fa-cog) в заголовок карточки "Claude API"
- Удалена кнопка "Установить бюджет" из виджета карточки
- Создана новая модалка `#api-settings-modal` для настроек Claude API
- Удалена секция Claude API из общих настроек (settings-modal)
- Добавлены функции `openApiSettingsModal()` и `closeApiSettingsModal()`

**UI (обновлено):**
- Шестерёнка в правом верхнем углу карточки → открывает модалку с настройками
- Модалка настроек (`loadApiUsage()`): начальный баланс + сброс, toggle лимитов, дневной/общий лимит, кнопка сохранить
- Общие настройки (`loadApiUsageStats()`): прогресс-бары, остаток баланса, вызовы сегодня, модели, последний сброс, топ потребителей по агентам

---

**Последнее обновление:** 2026-01-28 (fix: restore API usage statistics)
**Текущая версия:** v2.3.0 (см. файл `/opt/aila/VERSION`)
**Рабочая ветка:** `claude/start-new-session-4XrKU`
