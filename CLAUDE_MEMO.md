# ПАМЯТКА ДЛЯ CLAUDE - AILA Trading Bot

> **ПРОЧИТАЙ ПОЛНОСТЬЮ ПЕРЕД НАЧАЛОМ РАБОТЫ!**

## КРИТИЧЕСКИ ВАЖНО

### 1. Рабочая ветка
```
claude/aila-trading-system-IODYI
```
**Эта ветка содержит ПОЛНЫЙ UI интерфейс!**

### 2. Главный файл - МОНОЛИТ
**`aila/api/main.py`** (~6000 строк) содержит ВСЁ:
- FastAPI сервер
- HTML разметка (DASHBOARD_HTML)
- CSS стили
- JavaScript код
- Переводы EN/RU

**НЕ создавай отдельные файлы для фронтенда!**

### 3. При работе с сессионными ветками
Сессионные ветки (`claude/start-new-session-*`) могут иметь УСТАРЕВШИЙ main.py!

**ВСЕГДА начинай с:**
```bash
git checkout origin/claude/aila-trading-system-IODYI -- aila/api/main.py
```
Затем добавляй новые функции.

---

## Сервер пользователя

| Параметр | Значение |
|----------|----------|
| IP | 149.28.16.83 |
| Путь | /opt/aila |
| Веб-интерфейс | http://149.28.16.83:8080 |
| Systemd сервис | aila.service |

### Команда обновления (systemd):
```bash
cd /opt/aila && git fetch origin <branch> && git reset --hard origin/<branch> && sudo systemctl restart aila
```

### Проверка статуса:
```bash
systemctl status aila
```

---

## Структура проекта

```
/opt/aila/
├── aila/
│   ├── api/main.py      # ГЛАВНЫЙ ФАЙЛ - сервер + UI (монолит)
│   ├── trading/engine.py # TradingEngineConfig
│   ├── exchange/futures.py # Bybit API
│   └── scripts/run_web.py  # Точка входа
├── venv/                 # Python виртуальное окружение
└── data/                 # SQLite база данных
```

---

## Добавление новых настроек - ЧЕКЛИСТ

При добавлении новой настройки в UI нужно изменить **6 мест** в main.py:

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
    ...
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
    ...
};
```

### 6. Backend - три места:
```python
# В create_bot:
"новое_поле": config.get("новое_поле", default),

# В update_bot:
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

## Важные алиасы

| UI поле | Backend поле | Причина |
|---------|--------------|---------|
| leverage_mode | margin_mode | TradingEngineConfig использует margin_mode |

```python
# Обязательный alias:
bot_settings["margin_mode"] = bot.get("leverage_mode", "cross")
```

---

## Кэш браузера - ВАЖНО!

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

## Функции интерфейса IODYI

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

## Частые ошибки

| Ошибка | Причина | Решение |
|--------|---------|---------|
| ImportError: cannot import name 'X' | main.py из неправильной ветки | `git checkout origin/claude/aila-trading-system-IODYI -- aila/api/main.py` |
| Старый интерфейс в браузере | Кэш | `Ctrl+Shift+R` или режим инкогнито |
| Настройки не сохраняются | Нет в JS config | Добавить в createBot и saveEditBot |
| Настройки не применяются | Нет в bot_settings | Добавить в start_bot |
| UI поля не отображаются | Нет в HTML | Добавить в обе формы (new + edit) |

---

## GitHub

- Репозиторий: https://github.com/alexmaestro2022/claude-code
- Рабочая ветка: `claude/aila-trading-system-IODYI`

---

## При проблемах

1. **НЕ** переключай ветки без подтверждения пользователя
2. **ВСЕГДА** бери main.py из ветки IODYI
3. **ПРОВЕРЯЙ** что все 6 мест обновлены при добавлении настроек
4. **ДОБАВЛЯЙ** Cache-Control заголовки
5. **НАПОМИНАЙ** про Ctrl+Shift+R

---

**Последнее обновление:** 2026-01-14
**Текущая версия UI:** v2.1
