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
| Рабочая ветка | `claude/aila-trading-system-IODYI` |

**ВАЖНО: Реальные деньги — тщательно проверять все изменения в торговой логике!**

---

## 2. Инфраструктура

| Компонент | Значение |
|-----------|----------|
| Сервер | 149.28.16.83 |
| Путь на сервере | /opt/aila |
| Сервис | aila (systemd) |
| База данных | SQLite (./data/aila.db) |
| Веб-интерфейс | http://149.28.16.83:8080 |
| Логи | /opt/aila/logs/aila.log, error.log, trades.log |

---

## 3. Рабочий процесс

### Распределение ролей:
- **Claude**: Работает напрямую с репозиторием, вносит изменения, коммитит, пушит
- **Пользователь**: Выполняет команды на сервере через SSH, рестартует бота

### Команда для деплоя (выполняет пользователь):
```bash
cd /opt/aila && git fetch origin <branch> && git reset --hard origin/<branch> && sudo systemctl restart aila
```

### Просмотр логов:
```bash
# Последние 100 строк
tail -100 /opt/aila/logs/aila.log

# В реальном времени
tail -f /opt/aila/logs/aila.log

# Только ошибки
tail -100 /opt/aila/logs/error.log

# Логи systemd
journalctl -u aila -n 100 --no-pager
```

### Альтернативный запуск (без systemd):
```bash
# Запуск в фоне
cd /opt/aila && nohup /opt/aila/venv/bin/python -m aila.scripts.run_web > /var/log/aila.log 2>&1 &

# Перезапуск
pkill -f "aila.scripts.run_web"; sleep 2; nohup /opt/aila/venv/bin/python -m aila.scripts.run_web > /var/log/aila.log 2>&1 &
```

---

## 4. Торговая стратегия: Triple SuperTrend + EMA200

### Индикаторы:

| SuperTrend | Period | Multiplier | Роль |
|------------|--------|------------|------|
| ST1 (slow) | 12 | 3.0 | Определение тренда |
| ST2 (medium) | 11 | 2.0 | Stop-Loss по умолчанию |
| ST3 (fast) | 10 | 1.0 | Быстрые сигналы |
| EMA200 | 200 | - | Трендовый фильтр |

### Условия входа:
- **LONG**: Все 3 SuperTrend зеленые (direction=1) + цена > EMA200
- **SHORT**: Все 3 SuperTrend красные (direction=-1) + цена < EMA200

### Условия выхода:
- Stop-Loss на линии ST2 (или фиксированный %)
- Take-Profit по Risk:Reward ratio (по умолчанию 2:1)
- Trailing Stop после +1% профита (шаг 0.5%)
- Смена направления всех SuperTrend

---

## 5. Risk Management

### Position Sizing:

| Режим | Описание |
|-------|----------|
| risk_percent | % от баланса на риск (по умолчанию 2%) |
| fixed_amount | Фиксированная сумма в USDT |
| kelly | Kelly Criterion (25% от оптимального) |

### Stop-Loss:

| Режим | Описание |
|-------|----------|
| supertrend_line | По линии ST (1, 2 или 3) |
| fixed_percent | Фиксированный % от входа |
| atr | На основе ATR |

### Take-Profit:

| Режим | Описание |
|-------|----------|
| risk_ratio | По R:R (по умолчанию 2:1) |
| fixed_percent | Фиксированный % |
| multi_target | Несколько целей (частичное закрытие) |

### Safety Limits:
- Max daily loss: 5%
- Max weekly loss: 10%
- Max drawdown: 15%
- Min balance: 100 USDT
- Max open positions: 3
- Consecutive losses limit: 3 (потом cooldown 60 мин)

---

## 6. Структура проекта

```
/opt/aila/
├── aila/
│   ├── core/
│   │   ├── indicators/      # SuperTrend, EMA, ATR
│   │   ├── strategy/        # Triple SuperTrend логика
│   │   └── risk/            # Position sizing, SL/TP
│   ├── exchange/            # Bybit API интеграция
│   ├── trading/             # Trading engine, order/position manager
│   ├── api/
│   │   └── main.py          # ГЛАВНЫЙ ФАЙЛ - FastAPI + UI (~6000 строк)
│   ├── config/
│   │   ├── settings.py      # Pydantic settings
│   │   ├── strategies.yaml  # Параметры стратегии
│   │   └── logging.yaml     # Логирование
│   ├── scripts/             # Entry points (run_bot, run_web)
│   ├── database/            # SQLAlchemy модели
│   ├── notifications/       # Telegram
│   └── utils/               # Helpers
├── venv/                    # Python virtual environment
├── data/                    # SQLite база данных
├── logs/                    # Логи
└── .env                     # API ключи (НЕ ТРОГАТЬ!)
```

### Ключевые файлы:

| Файл | Описание |
|------|----------|
| `aila/api/main.py` | **ГЛАВНЫЙ** - FastAPI + весь UI (монолит ~6000 строк) |
| `aila/core/strategy/triple_supertrend.py` | Логика стратегии |
| `aila/core/risk/position_sizing.py` | Расчет размера позиции |
| `aila/core/risk/stop_loss.py` | Управление Stop-Loss |
| `aila/trading/engine.py` | TradingEngineConfig |
| `aila/exchange/futures.py` | Bybit API клиент |
| `aila/config/settings.py` | Все настройки приложения |
| `aila/config/strategies.yaml` | Параметры стратегии |
| `.env` | **Переменные окружения (НЕ ТРОГАТЬ!)** |

---

## 7. Технологии

- Python 3.11+
- FastAPI + Uvicorn (веб-сервер)
- pybit (Bybit API)
- pandas/numpy (данные)
- SQLAlchemy + aiosqlite (БД)
- Pydantic v2 (валидация)
- structlog (логирование)
- python-telegram-bot (уведомления)

---

## 8. КРИТИЧЕСКИ ВАЖНЫЕ ПРАВИЛА

### 8.1. Ветки Git

**Рабочая ветка:** `claude/aila-trading-system-IODYI` - содержит ПОЛНЫЙ UI

**Сессионные ветки:** `claude/start-new-session-*` - могут иметь УСТАРЕВШИЙ main.py!

**ВСЕГДА начинай с:**
```bash
git fetch origin claude/aila-trading-system-IODYI
git checkout origin/claude/aila-trading-system-IODYI -- aila/api/main.py
```

**Если старый интерфейс - НЕ МЕНЯЙ ВЕТКУ! Спроси пользователя сначала.**

### 8.2. Файл main.py - МОНОЛИТ

`aila/api/main.py` содержит ВСЁ в одном файле:
- FastAPI endpoints
- HTML разметка (DASHBOARD_HTML)
- CSS стили
- JavaScript код
- Переводы (EN/RU)

**НЕ создавай отдельные файлы для фронтенда!**

### 8.3. Правила безопасности

1. **Реальные деньги** — тщательно проверять все изменения в торговой логике
2. **Не трогать .env** — содержит API ключи
3. **Тестов нет** — быть внимательным с изменениями
4. **После изменений** — пользователь делает git pull + рестарт
5. **Логи** — при ошибках запрашивать логи у пользователя

---

## 9. Добавление новых настроек - ЧЕКЛИСТ

При добавлении новой настройки в UI нужно изменить **7 мест** в main.py:

### 1. HTML форма создания бота
```html
<input id="newBotНовоеПоле" ...>
```

### 2. HTML форма редактирования бота
```html
<input id="editBotНовоеПоле" ...>
```

### 3. JavaScript - создание бота (createBot)
```javascript
const config = {
    новое_поле: document.getElementById('newBotНовоеПоле').value,
};
```

### 4. JavaScript - загрузка в форму редактирования
```javascript
document.getElementById('editBotНовоеПоле').value = bot.новое_поле || default;
```

### 5. JavaScript - сохранение редактирования (saveEditBot)
```javascript
const config = {
    новое_поле: document.getElementById('editBotНовоеПоле').value,
};
```

### 6. Backend - три места:
```python
# В create_bot (POST /api/bots):
"новое_поле": config.get("новое_поле", default),

# В update_bot (PUT /api/bots/{id}):
if "новое_поле" in config:
    bot["новое_поле"] = config["новое_поле"]

# В start_bot (bot_settings):
bot_settings["новое_поле"] = bot.get("новое_поле", default)
```

### 7. Переводы (translations.en и translations.ru)
```javascript
новоеПоле: 'New Field',  // EN
новоеПоле: 'Новое поле', // RU
```

---

## 10. Важные алиасы

| UI поле | Backend поле | Причина |
|---------|--------------|---------|
| leverage_mode | margin_mode | TradingEngineConfig использует margin_mode |

```python
# Обязательный alias в bot_settings:
bot_settings["margin_mode"] = bot.get("leverage_mode", "cross")
```

---

## 11. Кэш браузера

При изменении интерфейса:

1. **Добавь заголовки в dashboard endpoint:**
```python
return Response(
    content=DASHBOARD_HTML,
    media_type="text/html",
    headers={
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0",
    }
)
```

2. **Обнови версию в title:**
```html
<title>AILA Trading Bot v2.2</title>
```

3. **Напомни пользователю:** `Ctrl+Shift+R` в браузере

---

## 12. Функции интерфейса IODYI

- Использование баланса (%) - слайдер
- Лимит потерь (%) - слайдер
- Position Sizing Mode (fixed_amount / risk_percent / kelly)
- Margin Mode (cross / isolated)
- Фильтры авто-торговли (Vol 24h, Price, Change %, Volatility)
- **Signal Entry** - гибкая конфигурация ролей ST линий (off/confirm/trigger)
- Trailing SL, Partial TP, Trailing TP
- Multi-bot архитектура (до 10 ботов)

---

## 13. Частые ошибки и решения

| Ошибка | Причина | Решение |
|--------|---------|---------|
| ImportError: cannot import name 'X' | main.py из неправильной ветки | `git checkout origin/claude/aila-trading-system-IODYI -- aila/api/main.py` |
| Старый интерфейс в браузере | Кэш | `Ctrl+Shift+R` или режим инкогнито |
| Настройки не сохраняются | Нет в JS config | Добавить в createBot и saveEditBot |
| Настройки не применяются | Нет в bot_settings | Добавить в start_bot |
| UI поля не отображаются | Нет в HTML | Добавить в обе формы (new + edit) |
| **Настройка в UI, но не работает в торговле** | Нет в TradingEngineConfig | Добавить поле + передать из run_web.py |
| **Менеджер создаётся без конфига** | Нет config в __init__ | Создать config из TradingEngineConfig |
| Поля не показываются в edit форме | Нет toggle вызова | Добавить toggle после установки значений |

---

## 14. Переменные окружения (.env) - НЕ ТРОГАТЬ!

```bash
# Exchange
BYBIT_API_KEY=xxx
BYBIT_API_SECRET=xxx
BYBIT_TESTNET=false
BYBIT_ACCOUNT_TYPE=futures

# Strategy
STRATEGY_TIMEFRAME=1h
STRATEGY_EMA_ENABLED=true

# Risk
RISK_SL_MODE=supertrend_line
RISK_TP_MODE=risk_ratio
RISK_RISK_PER_TRADE=2.0

# Futures
FUTURES_DEFAULT_LEVERAGE=10
FUTURES_LEVERAGE_MODE=cross

# Telegram
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=xxx
TELEGRAM_CHAT_ID=xxx

# Web
WEB_HOST=0.0.0.0
WEB_PORT=8080
```

---

## 15. Частые операции

### Изменение параметров стратегии:
- Файл: `aila/config/strategies.yaml`
- Или через веб-интерфейс

### Добавление торговой пары:
- В веб-интерфейсе или в `strategies.yaml` → `pairs:`

### Изменение leverage:
- В `strategies.yaml` → `futures.leverage_presets`
- Или через интерфейс

---

## 16. При проблемах

1. **НЕ** переключай ветки без подтверждения пользователя
2. **ВСЕГДА** бери main.py из ветки IODYI
3. **ПРОВЕРЯЙ** что все 7 мест обновлены при добавлении настроек
4. **ДОБАВЛЯЙ** Cache-Control заголовки
5. **НАПОМИНАЙ** про Ctrl+Shift+R
6. **ЗАПРАШИВАЙ** логи при ошибках

---

## 17. ПРАВИЛО ДЛЯ CLAUDE: Автообновление этого файла

> **КРИТИЧЕСКИ ВАЖНО**: Сессии часто зависают! Обновляй файл **СРАЗУ В ПРОЦЕССЕ РАБОТЫ**, не жди конца сессии!

### Когда обновлять (НЕМЕДЛЕННО после события):
1. Решил проблему → добавь в CHANGELOG + "Частые ошибки"
2. Добавил новую функцию → добавь в CHANGELOG
3. Узнал что-то важное о проекте → добавь в соответствующий раздел
4. Изменил архитектуру → обнови структуру проекта
5. **СРАЗУ коммить и пушь** - не накапливай изменения!

### Формат записи в CHANGELOG:
```
### YYYY-MM-DD (сессия XXXXX)
- **[ТИП]** Описание
```
Типы: `[ИСПРАВЛЕНО]`, `[ДОБАВЛЕНО]`, `[ИЗМЕНЕНО]`, `[ВАЖНО]`, `[УРОК]`

**Что считается важной информацией:**
- Новые файлы/модули в проекте
- Изменения в архитектуре
- Новые настройки/функции
- Решения проблем (добавить в "Частые ошибки")
- Изменения в инфраструктуре
- Новые правила работы
- Любые "грабли" на которые наступили

---

## CHANGELOG (История изменений)

> При конфликте информации - использовать ПОСЛЕДНЮЮ запись!

### 2026-01-14 (сессия 4XrKU) - SIGNAL ENTRY FEATURE

#### SIGNAL ENTRY - НОВАЯ СИСТЕМА СИГНАЛОВ:
- **[ДОБАВЛЕНО]** Signal Entry блок в UI с конфигурацией ролей ST линий:
  - ST1 (Slow), ST2 (Medium), ST3 (Fast) - каждая с dropdown
  - Роли: ❌ Выкл (off), 🟢 Подтв (confirm), 🎯 Триггер (trigger)
  - Только ОДИН триггер разрешён
  - Подтверждение триггера свечами (1-3)
- **[УДАЛЕНО]** early_entry_enabled - заменено на гибкую систему ролей
- **[ЛОГИКА]** Генерация сигнала:
  - Триггер: линия должна "только что развернуться" (prev != target, curr == target)
  - Подтверждение: линии должны УЖЕ быть в направлении (prev == target)
  - EMA фильтр работает ПОСЛЕ определения сигнала (strict/soft)
- **[ПО УМОЛЧАНИЮ]** ST1=confirm, ST2=confirm, ST3=trigger (как старый early_entry)

#### Файлы изменённые:
- `aila/api/main.py` - UI блок Signal Entry, CSS, JS, endpoints
- `aila/core/strategy/triple_supertrend.py` - новые поля в config + логика сигнала
- `aila/scripts/run_web.py` - передача st1_role, st2_role, st3_role, trigger_confirm_candles

---

### 2026-01-14 (сессия 4XrKU) - ПРОДОЛЖЕНИЕ

#### UI ИЗМЕНЕНИЯ:
- **[ДОБАВЛЕНО]** Мигающий круглый индикатор статуса в карточке бота:
  - 🟢 Зелёный мигает - running
  - 🟡 Жёлтый мигает - paused
  - 🔴 Красный мигает - stopping/error
  - ⚪ Серый не мигает - stopped
- **[ИЗМЕНЕНО]** PnL отображается в USDT и % одинаковым размером шрифта (18px)
- **[УДАЛЕНО]** Прямоугольный текстовый статус-бейдж (был дублирующим)

#### НАСТРОЙКИ ПО УМОЛЧАНИЮ:
- **[ИЗМЕНЕНО]** Режим бота: Auto Search (было Manual)
- **[ИЗМЕНЕНО]** Trailing SL ST Line: Fast (было Medium)
- **[ИСПРАВЛЕНО]** При Auto Search скрывается поле "Торговая пара", показывается "Макс. пар"

#### УДАЛЕНА ФИЧА BREAKEVEN:
- **[УДАЛЕНО]** Breakeven полностью удалён из кода (-183 строки)
- **[ПРИЧИНА]** Дублирует Partial TP с режимом "entry" (то же самое + закрытие части)
- **[РЕКОМЕНДАЦИЯ]** Использовать Partial TP: sl_move_mode="entry" для безубытка

#### ИСПРАВЛЕНИЯ ОШИБОК:
- **[ИСПРАВЛЕНО]** PositionSide enum error: 'PositionSide' object has no attribute 'upper'
  - Проблема: вызов .upper() на enum вместо строки
  - Решение: использовать .name атрибут (ex_pos.side.name)
- **[ИСПРАВЛЕНО]** NoneType error: '>' not supported between NoneType and int
  - Проблема: stop_loss мог быть None при сравнении
  - Решение: использовать `or 0` паттерн (position_data.get("stop_loss") or 0)

---

### 2026-01-14 (сессия 4XrKU - ранее) - АУДИТ НАСТРОЕК

#### КРИТИЧЕСКИЕ ИСПРАВЛЕНИЯ В ТОРГОВОЙ ЛОГИКЕ:
- **[ИСПРАВЛЕНО]** breakeven_* настройки НЕ РАБОТАЛИ! StopLossManager создавался без конфига
  - Добавлены поля в TradingEngineConfig: breakeven_enabled, breakeven_activation, breakeven_offset
  - TradingEngine.__init__ теперь создаёт StopLossConfig и передаёт в StopLossManager
  - run_web.py передаёт настройки из bot_settings
- **[ИСПРАВЛЕНО]** position_sizing_mode НЕ РАБОТАЛ! Был hardcoded "fixed_amount"
  - Добавлены поля в TradingEngineConfig: position_sizing_mode, risk_per_trade
  - PositionSizingConfig теперь использует mode из конфига
- **[ДОБАВЛЕНО]** set_margin_mode - функция для реальной установки margin mode на Bybit
  - Добавлен метод BybitClient.set_margin_mode()
  - Добавлен метод FuturesTrader.set_margin_mode()
  - Добавлено поле margin_mode в TradingEngineConfig
  - _execute_entry вызывает set_margin_mode перед открытием позиции
- **[ПРОВЕРЕНО]** EMA фильтр (strict/soft) - работает корректно
- **[ПРОВЕРЕНО]** Early Entry - работает корректно

#### Файлы изменённые в этой части:
- `aila/trading/engine.py` - TradingEngineConfig, импорт StopLossConfig/MarginMode, инициализация менеджеров
- `aila/exchange/bybit_client.py` - добавлен set_margin_mode()
- `aila/exchange/futures.py` - добавлен set_margin_mode()
- `aila/scripts/run_web.py` - передача новых полей в create_engine_config_for_bot()

#### ПЕРВАЯ ЧАСТЬ СЕССИИ:
- **[ИСПРАВЛЕНО]** Проблема с загрузкой старого интерфейса - нужно брать main.py из ветки IODYI
- **[ДОБАВЛЕНО]** Position Sizing Mode (fixed_amount / risk_percent / kelly)
- **[ДОБАВЛЕНО]** Margin Mode (cross / isolated) с alias leverage_mode → margin_mode
- **[ДОБАВЛЕНО]** Break-even настройки (enabled, activation %, offset %)
- **[ДОБАВЛЕНО]** Cache-Control заголовки для предотвращения кэширования браузера
- **[ИСПРАВЛЕНО]** Risk % - добавлено поле risk_per_trade (было в backend, не было в UI)
- **[ИСПРАВЛЕНО]** Trailing TP, Partial TP, tp_mode, tp_fixed_percent - добавлены в create_bot/update_bot
- **[ИСПРАВЛЕНО]** Отсутствовали toggle вызовы при редактировании бота:
  - toggleSlOptions('edit') - для показа правильных полей SL
  - toggleTrailingOptions('edit') - для Trailing SL опций
  - toggleTrailingTpOptions('edit') - для Trailing TP опций
  - toggleRiskPercentInput('edit') - для поля Risk %
- **[ВАЖНО]** Сессионные ветки могут иметь устаревший main.py - всегда начинать с IODYI
- **[ВАЖНО]** main.py это монолит ~6000 строк - НЕ создавать отдельные файлы для фронтенда
- **[УРОК]** При изменении UI всегда напоминать про Ctrl+Shift+R
- **[УРОК]** При добавлении настроек с toggle - обязательно вызывать toggle при загрузке формы редактирования!

#### УРОК: Как добавить настройку которая реально работает в торговле
1. UI (main.py HTML) - input field в new и edit формах
2. JavaScript (main.py JS) - createBot и saveEditBot, loadEditBot
3. Backend (main.py Python) - create_bot и update_bot endpoints
4. Bot Settings (main.py Python) - start_bot формирует bot_settings
5. **TradingEngineConfig (engine.py)** - добавить поле!
6. **run_web.py** - передать в create_engine_config_for_bot()
7. **TradingEngine.__init__** - использовать config при создании менеджеров!

### 2026-01-13 (предыдущие сессии)
- Создан полный UI интерфейс в ветке IODYI
- Добавлены фильтры авто-торговли (Vol 24h, Price, Change %, Volatility)
- Добавлен ранний вход (early entry)
- Реализована multi-bot архитектура (до 10 ботов)
- Настроен systemd сервис для автозапуска

---

**Последнее обновление:** 2026-01-14
**Текущая версия UI:** v2.3
**Последняя сессия:** claude/start-new-session-4XrKU
**Signal Entry:** Новая гибкая система ролей ST линий (заменила early_entry)
**Breakeven:** УДАЛЁН (использовать Partial TP с mode="entry")
