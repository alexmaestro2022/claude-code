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

---

## 3. Инфраструктура

| Компонент | Значение |
|-----------|----------|
| Сервер | 149.28.16.83 |
| Путь на сервере | /opt/aila |
| База данных | SQLite (./data/aila.db) |
| Веб-интерфейс | http://149.28.16.83:8080 |
| Логи | /var/log/aila.log |

### Команды для сервера:

**Проверка текущей ветки:**
```bash
cd /opt/aila && git branch -v
```

**Запуск бота:**
```bash
cd /opt/aila && nohup /opt/aila/venv/bin/python -m aila.scripts.run_web > /var/log/aila.log 2>&1 &
```

**Перезапуск бота:**
```bash
pkill -f "aila.scripts.run_web"; sleep 2; nohup /opt/aila/venv/bin/python -m aila.scripts.run_web > /var/log/aila.log 2>&1 &
```

**Деплой новой версии:**
```bash
cd /opt/aila && git fetch origin claude/start-new-session-4XrKU && git reset --hard origin/claude/start-new-session-4XrKU
pkill -f "aila.scripts.run_web"; sleep 2; nohup /opt/aila/venv/bin/python -m aila.scripts.run_web > /var/log/aila.log 2>&1 &
```

---

## 3.1 Workflow: Claude → Сервер → GitHub

**Ограничение:** Каждая сессия Claude может пушить только в свою ветку (с ID сессии).

**Схема работы:**
```
1. Claude читает код из рабочей ветки
2. Claude делает изменения
3. Claude пушит в свою ветку сессии (claude/*-XXXXX)
4. Пользователь мержит на сервере
5. Сервер пушит в рабочую ветку на GitHub
```

**ВАЖНО: В начале каждой сессии Claude должен создать ветку ОТ рабочей ветки:**
```bash
git fetch origin claude/start-new-session-4XrKU
git checkout -B claude/новая-сессия-XXXXX origin/claude/start-new-session-4XrKU
```

Это гарантирует что мерж пройдёт без конфликтов.

**Команда для мержа изменений от Claude (выполняет пользователь на сервере):**
```bash
cd /opt/aila && git fetch origin && git merge origin/claude/ВЕТКА-СЕССИИ -m "Merge from Claude" && git push origin claude/start-new-session-4XrKU
```

**После мержа - перезапуск бота:**
```bash
pkill -f "aila.scripts.run_web"; sleep 2; nohup /opt/aila/venv/bin/python -m aila.scripts.run_web > /var/log/aila.log 2>&1 &
```

**Удаление ветки сессии после мержа (на GitHub):**
https://github.com/alexmaestro2022/claude-code/branches

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
- **confirm** - линия должна УЖЕ быть в направлении
- **trigger** - линия должна ТОЛЬКО ЧТО развернуться

### По умолчанию:
- ST1 (Fast) = confirm
- ST2 (Medium) = confirm
- ST3 (Slow) = **trigger** ← медленная разворачивается последней

---

## 5. Position Sizing

### Режим fixed_amount:
- `order_size` = размер позиции (notional value), НЕ маржа!
- Пример: `order_size=10 USDT` с leverage 10x = позиция 10 USDT, маржа 1 USDT

| Баланс | Leverage | order_size | Макс. позиций |
|--------|----------|------------|---------------|
| 4.5 USDT | 10x | 10 USDT | ~4 позиции (маржа 1 USDT каждая) |

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
│   ├── trading/             # Trading engine
│   ├── api/
│   │   └── main.py          # ГЛАВНЫЙ ФАЙЛ - FastAPI + UI (~6000 строк)
│   ├── config/              # Настройки
│   └── scripts/
│       └── run_web.py       # Запуск + API endpoints (/api/stats, /api/ping, /api/restart-server)
├── venv/                    # Python virtual environment
├── data/                    # SQLite база данных
└── .env                     # API ключи (НЕ ТРОГАТЬ!)
```

### Ключевые файлы:

| Файл | Описание |
|------|----------|
| `aila/api/main.py` | **ГЛАВНЫЙ** - FastAPI + весь UI (монолит) |
| `aila/scripts/run_web.py` | Запуск + /api/stats, /api/ping, /api/restart-server |
| `aila/core/strategy/triple_supertrend.py` | Логика стратегии |
| `aila/core/risk/position_sizing.py` | Расчет размера позиции |
| `aila/trading/engine.py` | TradingEngineConfig |

---

## 7. Добавление новых настроек - ЧЕКЛИСТ

При добавлении новой настройки нужно изменить **7 мест** в main.py:

1. HTML форма создания бота (`newBot...`)
2. HTML форма редактирования бота (`editBot...`)
3. JavaScript - createBot()
4. JavaScript - loadEditBot()
5. JavaScript - saveEditBot()
6. Backend - create_bot endpoint
7. Backend - update_bot endpoint
8. Backend - start_bot (bot_settings)
9. Переводы (translations.en и translations.ru)

**+ Если настройка влияет на торговлю:**
10. TradingEngineConfig (engine.py)
11. run_web.py - передача в create_engine_config_for_bot()

---

## 8. Частые ошибки и решения

| Ошибка | Причина | Решение |
|--------|---------|---------|
| Баланс "--" | Нет background client | Проверь /api/stats в run_web.py |
| Пинг не показывается | Нет background client | Проверь /api/ping в run_web.py |
| Restart зависает | Неправильный JS | Должен быть polling после restart |
| qty в 10x больше | position_sizing умножал на leverage | НЕ умножать на leverage! |
| ST1/ST3 перепутаны | Неправильные названия в UI | ST1=Fast, ST3=Slow |
| Старый интерфейс | Кэш браузера | Ctrl+Shift+R |
| Конфликты при мерже | Ветка сессии создана от старого кода | Создавать от рабочей ветки |

---

## 9. При проблемах

1. **НЕ** бери файлы из других веток!
2. **ЧИТАЙ** код перед изменением
3. **ПРОВЕРЯЙ** что все endpoints существуют
4. **СПРАШИВАЙ** пользователя перед сменой ветки на сервере
5. **НАПОМИНАЙ** про Ctrl+Shift+R
6. **ЗАПРАШИВАЙ** логи при ошибках

---

## 10. Рабочие функции в текущей ветке

Ветка `claude/start-new-session-4XrKU` содержит:
- Multi-bot архитектура (создание ботов через веб-интерфейс)
- Signal Entry - новая система ролей ST линий (off/confirm/trigger)
- /api/stats - баланс работает даже без запущенных ботов (background client)
- /api/ping - пинг биржи работает
- /api/restart-server - перезагрузка с остановкой ботов
- Правильный position sizing (order_size = notional, не умножается на leverage)
- Trailing SL по SuperTrend линиям
- Auto Search mode для ботов

---

**Последнее обновление:** 2026-01-15
**Рабочая ветка:** `claude/start-new-session-4XrKU`
