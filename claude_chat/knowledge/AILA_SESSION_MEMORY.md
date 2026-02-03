## AILA AI TRADE — ПАМЯТЬ СЕССИИ

> **Загрузи этот файл в начале новой сессии для восстановления контекста**
> **Последнее обновление:** 2026-02-03 UTC

---

## 📋 ОБЩАЯ ИНФОРМАЦИЯ О ПРОЕКТЕ

| Параметр | Значение |
|----------|----------|
| Проект | AILA AI Trade — автономная торговая система |
| Биржа | Bybit Futures (РЕАЛЬНАЯ ТОРГОВЛЯ!) |
| Сервер | 149.28.16.83 (Vultr Tokyo) |
| Путь | /opt/aila |
| Web UI | https://aila.zone/ai-trade |
| Admin UI | https://aila.zone/admin |
| Репозиторий | https://github.com/alexmaestro2022/claude-code |
| Версия | 2.5.0+ |

---

## 🔧 ИНФРАСТРУКТУРА

### Systemd сервис:
```bash
sudo systemctl restart aila    # перезапуск
sudo systemctl status aila     # статус
sudo journalctl -u aila -f     # логи
```

### Claude Code:
- **Подписка:** Max (бесплатно, без лимита токенов)
- **Rate Limit Tier:** default_claude_max_20x
- **Модель:** claude-opus-4-5-20251101
- **Авторизация:** OAuth через ~/.claude/.credentials.json
- **ВАЖНО:** НЕ передавать ANTHROPIC_API_KEY — использовать подписку!

### Пользователь:
- User: `aila`
- Home: `/home/aila`
- Claude credentials: `~/.claude/.credentials.json`

---

## 🤖 АРХИТЕКТУРА АГЕНТОВ

### Два агента торговли:

| Агент | Роль | Таймфрейм | Интервал скана |
|-------|------|-----------|----------------|
| **TRADER** | Трендовая торговля | 15m | 60 сек |
| **SNIPER** | Пробои/ликвидации | 5m | 10 сек |

### 16 Агентов системы:
TRADER, SNIPER, REVIEWER, RISK_GUARD, ANALYST, MENTOR, RESEARCHER, WHALE_TRACKER, NEWS, PREDICTOR, ARBITRAGE, HEDGE_MASTER, WAR_ROOM, CAPITAL_MANAGER, STRATEGY_EVOLUTION, LOGGER

### Раздельные данные для агентов:
- `trader_stats.json` / `sniper_stats.json` — статистика
- `trader_knowledge.json` / `sniper_knowledge.json` — база знаний
- `trader_settings.json` / `sniper_settings.json` — настройки
- Отдельные уровни, XP, лимиты позиций

---

## 📊 КАСКАДНАЯ СИСТЕМА АНАЛИЗА

### Экономия токенов ~60-80%:

```
ПЕРЕД циклом: проверка лимита позиций
    ↓ если лимит достигнут → ПАУЗА (0 токенов)
    
ШАГ 1: VIP (BTC, ETH, SOL) + Приоритет 1 (+5-20%)
    ↓ если сигнал → СТОП
    
ШАГ 2: Приоритет 2 (+20-35%)
    ↓ если сигнал → СТОП
    
ШАГ 3: Приоритет 3 (+35-50%)
    ↓ сигнал или ждать следующий цикл
```

### Фильтрация пар:
- **Исключены:** >50% за 24ч (пампы), <3% за 24ч (флэт)
- **VIP пары:** BTC, ETH, SOL — всегда включены
- **Топ по объёму** внутри каждого приоритета

---

## 📈 СИСТЕМА УРОВНЕЙ

### TRADER Levels:
| Level | Max Leverage | Max Positions | Max Risk |
|-------|-------------|---------------|----------|
| 1 | 5x | 1 | 1% |
| 5 | 10x | 3 | 2% |
| 10 | 20x | 5 | 3% |

### SNIPER Levels (более консервативные):
| Level | Max Leverage | Max Positions | Max Risk |
|-------|-------------|---------------|----------|
| 1 | 3x | 1 | 0.5% |
| 2 | 5x | 1 | 1% |
| 10 | 15x | 3 | 2% |

### XP система:
- Win: +10 XP
- Loss: -5 XP (минимум 0)
- XP начисляется только агенту, открывшему сделку

---

## 🔄 ОТСЛЕЖИВАНИЕ ЗАКРЫТЫХ ПОЗИЦИЙ

### Position Sync Loop (каждые 30 сек):
1. Сравнивает `bot_positions.json` с позициями на бирже
2. Если позиция исчезла → закрылась по SL/TP
3. Получает PnL с биржи через `get_closed_pnl()`
4. Обновляет статистику агента
5. Начисляет/снимает XP
6. Обновляет базу знаний (worst_pairs, mistakes_to_avoid)
7. Отправляет Telegram уведомление с оценкой (A/B/C/D/F)

### Хранение позиций бота:
```json
// /opt/aila/data/ai_trade/bot_positions.json
{
  "XAUTUSDT": {
    "symbol": "XAUTUSDT",
    "side": "LONG",
    "entry_price": 5538,
    "source": "TRADER",
    "opened_at": "2026-01-30T12:00:00Z"
  }
}
```

---

## 🎨 ИНТЕРФЕЙС AI TRADE

### Новая структура с вкладками:
```
┌─────────────────────────────────────────────────────────┐
│ Баланс: $9.04        │  Позиции (1): +$0.03            │
├─────────────────────────────────────────────────────────┤
│ [📈 TRADER] [🎯 SNIPER] [🤖 Claude API]     [⚙️] [🔧]  │
├─────────────────────────────────────────────────────────┤
│ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐            │
│ │ Статус │ │ Level  │ │ Stats  │ │Learning│            │
│ └────────┘ └────────┘ └────────┘ └────────┘            │
│                                                         │
│ 📋 Лента событий                         [▼ Свернуть]  │
│ 21:15  🔍 ETH/USDT → REJECT (R:R 1.2)                  │
│ 21:14  📚 Новый урок усвоен                             │
└─────────────────────────────────────────────────────────┘
```

### Мини-карточки (по клику → модалка):
- **Статус:** Scanning/Paused/Disabled, Cascade stage
- **Level:** Уровень + XP прогресс-бар
- **Статистика:** Winrate, PnL, сделки → История сделок
- **Обучение:** Уроки, best/worst pairs → База знаний

### Realtime обновление:
| Данные | Интервал |
|--------|----------|
| Баланс + PnL позиций | **1 сек** |
| Autopilot + Activity Feed | 10 сек |
| Статистика + API Usage | 30 сек |

### Иконка 🔧 в шапке:
- Переход на /admin (Админ панель)

---

## 🔧 АДМИН ПАНЕЛЬ (/admin)

### Архитектура Claude Chat + Claude Code:

```
Пользователь → Claude Chat → [подтверждение] → Claude Code → результат → Claude Chat → анализ
```

### Claude Chat:
- Работает в `/opt/aila/claude_chat/`
- Имеет свою базу знаний и историю
- Формирует команды для Claude Code
- Анализирует результаты

### Claude Code:
- Работает в `/opt/aila/`
- Выполняет команды
- Возвращает результаты

### Режимы:
- **Manual:** Запрашивает подтверждение перед отправкой в Code
- **Auto:** Выполняет автоматически, включая автозадачи

### Авторизация:
- 6-значный код в Telegram (действует 5 мин)
- Сессия 30 минут
- 5 неудачных попыток → блок 10 мин

### Быстрые команды:
- **Мониторинг:** Логи TRADER/SNIPER, Ошибки, Статус
- **Управление:** Рестарт, Стоп, Старт, Конфиг
- **Данные:** Бэкап, PnL, Очистка, Статистика
- **Мои команды:** Пользовательские команды

### Планирование команд:
- Разово через X мин/час/дней
- Циклично каждые X мин/час/дней
- Очередь автозадач (последовательное выполнение)

### Подписка Max:
- Виджет в top-bar: иконка crown + "Max" + "Today: X"
- Модалка Usage: статистика за 7 дней
- Локальный счётчик запросов
- Rate limit детекция и уведомления

### Локализация:
- Синхронизируется с языком ai_trade.html
- Полный словарь RU/EN

---

## 💰 CLAUDE API USAGE (для AI Trade агентов)

### Вкладка Claude API:
- Расход сегодня / Общий расход с прогресс-барами
- Остаток баланса
- Вызовов сегодня (S:X / H:X)
- Токены: in / out
- Топ потребителей по агентам
- Настройки в модалке (шестерёнка)

### Модели:
- **Sonnet:** TRADER, REVIEWER, RISK_GUARD
- **Haiku:** NEWS, MENTOR, ANALYST, PREDICTOR

---

## 💾 PERSISTENCE

### Локально:
- Путь: `/opt/aila/data/ai_trade/`
- Интервал: каждые 60 сек
- Файлы:
  - `trader_stats.json`, `sniper_stats.json`
  - `trader_knowledge.json`, `sniper_knowledge.json`
  - `trader_settings.json`, `sniper_settings.json`
  - `bot_positions.json`
  - `api_usage.json`
  - `trading_state.json`

### Админ панель:
- Путь: `/opt/aila/data/admin/`
- Файлы:
  - `commands.json` — пользовательские команды
  - `scheduled.json` — запланированные задачи
  - `sessions.json` — активные сессии
  - `admin_stats.json` — счётчик запросов

### Claude Chat:
- Путь: `/opt/aila/claude_chat/`
- `knowledge/` — база знаний
- `history/` — история чатов

---

## 🔄 PIPELINE СДЕЛКИ

```
[STAGE 1] TRADER/SNIPER → cascade_analyze → лучший сигнал
    ↓
[STAGE 2] Signal Queue → priority (SNIPER > TRADER)
    ↓
[STAGE 3] REVIEWER → APPROVE/REJECT/MODIFY
    ↓
[STAGE 4] RISK_GUARD → PASS/VETO
    ↓
[STAGE 5] Confirmations check
    ↓
[READY_TO_TRADE] → жёлтый лог
    ↓
[TRADE_OPENED] → зелёный лог + save to bot_positions
    ↓
... позиция открыта ...
    ↓
[POSITION_SYNC] → обнаружено закрытие на бирже
    ↓
[TRADE_CLOSED] → статистика + XP + обучение + Telegram
```

---

## ✅ ИСПРАВЛЕНИЯ В ЭТОЙ СЕССИИ

| # | Проблема | Решение |
|---|----------|---------|
| 1 | SNIPER не интегрирован | Параллельная работа с TRADER через Signal Queue |
| 2 | Общая статистика | Раздельные stats/knowledge/settings для агентов |
| 3 | Cooldown после reject | Cooldown только после успешного открытия |
| 4 | Confirmations hardcoded | Читаются из trader_settings.json |
| 5 | Лимит позиций из кэша | Реальные данные с биржи |
| 6 | Сканирование при лимите | Пауза каскада для экономии API |
| 7 | Не учитываются закрытия | Position Sync Loop каждые 30 сек |
| 8 | PnL % не совпадает | Берётся unrealisedPnlRoe с биржи |
| 9 | Claude через API | Переключено на подписку Max |
| 10 | Истории Chat/Code | Изолированы через разные cwd |

### 2026-02-02 — Диагностика и фиксы pipeline

| # | Проблема | Решение |
|---|----------|---------|
| 11 | `_processed_pairs` не очищался после reject | `mark_processed(pair, agent, success=False)` в обоих failure branches |
| 12 | Stage 4 пустые данные = "нет подтверждения" | Считает только available_checks, `effective_min = min(required, available)` |
| 13 | SNIPER игнорировал trigger_* настройки | Фильтрация по `sniper_settings.json` перед итерацией triggers |
| 14 | Kelly Criterion cold-start: win_rate=0 → 0% | Fallback на `max_risk_per_trade_pct` (2%) при нулевой статистике |
| 15 | False positive rate limit в admin chat | `_is_rate_limit_error()` проверяет только stderr, убраны broad patterns |
| 16 | RULES.md устаревший (pm2, дубли CLAUDE.md) | Очищен с 186→62 строк (-82%), pm2→systemctl |

### 2026-02-03 — Admin Panel стабилизация

| # | Проблема | Решение |
|---|----------|---------|
| 17 | Chat history DOM crash при большом тексте | Truncate oversized messages в `current.json` |
| 18 | Font Awesome CDN — внешняя зависимость | Все CSS/JS перенесены локально (zero external deps) |
| 19 | codeInput multiline ломал UX | Сделан single-line, onkeydown через addEventListener |
| 20 | Chat сессии не персистентные | Persistent sessions с архивацией истории |

---

## 📊 ПОЛЕЗНЫЕ API ENDPOINTS

```bash
# AI Trade
curl http://localhost:8080/api/ai-trade/autopilot/status
curl http://localhost:8080/api/ai-trade/realtime           # баланс + позиции (1 сек)
curl http://localhost:8080/api/ai-trade/agent-stats
curl http://localhost:8080/api/ai-trade/cascade/status
curl http://localhost:8080/api/ai-trade/activity-feed?agent=trader&limit=10
curl http://localhost:8080/api/ai-trade/learning/trader
curl http://localhost:8080/api/ai-trade/positions

# Админ панель
curl http://localhost:8080/api/admin/subscription-usage
curl http://localhost:8080/api/admin/commands
curl http://localhost:8080/api/admin/scheduled
```

---

## 📁 ВАЖНЫЕ ФАЙЛЫ

```
/opt/aila/
├── .env                          # API ключи (НЕ использовать для Claude!)
├── CLAUDE.md                     # Правила
├── CLAUDE_MEMO.md               # Документация
├── aila/ai_trade/
│   ├── autopilot_mode.py        # Каскадный анализ + sync
│   ├── bybit_exchange.py        # API биржи
│   ├── position_manager.py      # Bot positions tracking
│   ├── market_scanner.py        # Приоритеты пар
│   ├── signal_queue.py          # Очередь сигналов
│   ├── agent_stats.py           # XP и статистика
│   └── agents/                  # 16 агентов
├── aila/api/routes/ai_trade.py  # API endpoints
├── aila/api/templates/
│   ├── ai_trade.html            # Интерфейс AI Trade
│   └── admin.html               # Админ панель
├── claude_chat/                 # Claude Chat данные
│   ├── knowledge/               # База знаний
│   └── history/                 # История чатов
├── data/ai_trade/               # Данные AI Trade
├── data/admin/                  # Данные админ панели
└── logs/ai_trade/               # Логи
```

---

## 🚀 КОМАНДЫ ДЛЯ БЫСТРОГО СТАРТА

```bash
# Статус системы
curl -s http://localhost:8080/api/ai-trade/autopilot/status | jq '.'
curl -s http://localhost:8080/api/ai-trade/realtime | jq '.'

# Статистика агентов
curl -s http://localhost:8080/api/ai-trade/agent-stats | jq '.trader'
curl -s http://localhost:8080/api/ai-trade/agent-stats | jq '.sniper'

# Каскад
curl -s http://localhost:8080/api/ai-trade/cascade/status | jq '.'

# Логи
tail -50 /opt/aila/logs/ai_trade/trader.log
grep "CASCADE\|TRADE" /opt/aila/logs/ai_trade/*.log | tail -30

# Перезапуск
sudo systemctl restart aila
```

---

## 📝 ПРАВИЛА РАБОТЫ

1. **РЕАЛЬНЫЕ ДЕНЬГИ** — проверяй торговую логику!
2. **Claude Code работает через подписку Max** — НЕ передавать ANTHROPIC_API_KEY
3. **Каскадный анализ** — экономит 60-80% токенов
4. **Position Sync** — автоматически учитывает закрытия
5. **Раздельные данные** — TRADER и SNIPER независимы
6. **Всегда читай CLAUDE.md** — там все правила

---

## 🎯 ТЕКУЩИЙ СТАТУС

| Компонент | Статус |
|-----------|--------|
| Autopilot | ✅ RUNNING |
| TRADER | ✅ Level 1, каскадный анализ |
| SNIPER | ✅ Level 1, сканирует пробои |
| Position Sync | ✅ Каждые 30 сек |
| Каскад | ✅ Пауза при лимите |
| Realtime PnL | ✅ Каждую секунду |
| Админ панель | ✅ Через подписку Max |
| Rate Limit | ✅ Детекция только stderr (фикс false positive) |
| Ветка | `claude/start-new-session-4XrKU` (единственная) |
| Стабильный тег | `stable-admin-20260203` |

---

### Все теги:
| Тег | Описание |
|-----|----------|
| `working-20260131` | Первая рабочая версия |
| `stable-base-20260201` | Базовая стабильная |
| `stable-reform-20260202` | Pipeline фиксы |
| `stable-admin-20260203` | Admin panel: persistent sessions, local assets, DOM crash fix |

---

**Последняя сессия:** Admin panel стабилизация — persistent sessions, локальные CSS/JS (zero CDN), DOM crash fix, codeInput single-line