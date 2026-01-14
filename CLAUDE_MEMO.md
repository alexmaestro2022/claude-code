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
- Break-even настройки (enabled, activation %, offset %)
- Фильтры авто-торговли (Vol 24h, Price, Change %, Volatility)
- Ранний вход (вход в начале тренда)
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

> **ОБЯЗАТЕЛЬНО**: При каждой сессии, если узнал что-то важное о проекте:
> 1. Добавь информацию в соответствующий раздел
> 2. Добавь запись в CHANGELOG внизу файла
> 3. Обнови дату "Последнее обновление"
> 4. Закоммить и запушь изменения

**Что считается важной информацией:**
- Новые файлы/модули в проекте
- Изменения в архитектуре
- Новые настройки/функции
- Решения проблем (добавить в "Частые ошибки")
- Изменения в инфраструктуре
- Новые правила работы

---

## CHANGELOG (История изменений)

> При конфликте информации - использовать ПОСЛЕДНЮЮ запись!

### 2026-01-14 (сессия 4XrKU)
- **[ИСПРАВЛЕНО]** Проблема с загрузкой старого интерфейса - нужно брать main.py из ветки IODYI
- **[ДОБАВЛЕНО]** Position Sizing Mode (fixed_amount / risk_percent / kelly)
- **[ДОБАВЛЕНО]** Margin Mode (cross / isolated) с alias leverage_mode → margin_mode
- **[ДОБАВЛЕНО]** Break-even настройки (enabled, activation %, offset %)
- **[ДОБАВЛЕНО]** Cache-Control заголовки для предотвращения кэширования браузера
- **[ВАЖНО]** Сессионные ветки могут иметь устаревший main.py - всегда начинать с IODYI
- **[ВАЖНО]** main.py это монолит ~6000 строк - НЕ создавать отдельные файлы для фронтенда
- **[УРОК]** При изменении UI всегда напоминать про Ctrl+Shift+R

### 2026-01-13 (предыдущие сессии)
- Создан полный UI интерфейс в ветке IODYI
- Добавлены фильтры авто-торговли (Vol 24h, Price, Change %, Volatility)
- Добавлен ранний вход (early entry)
- Реализована multi-bot архитектура (до 10 ботов)
- Настроен systemd сервис для автозапуска

---

**Последнее обновление:** 2026-01-14
**Текущая версия UI:** v2.1
**Последняя сессия:** claude/start-new-session-4XrKU
