# ПАМЯТКА ДЛЯ CLAUDE - AILA Trading Bot

## ВАЖНО: Правильная ветка
**Рабочая ветка с новым интерфейсом:**
```
claude/aila-trading-system-IODYI
```

**НЕ ПЕРЕКЛЮЧАЙ ветки без явного запроса пользователя!**

## Сервер
- **IP:** 149.28.16.83
- **Путь проекта:** `/opt/aila`
- **Веб-интерфейс:** http://149.28.16.83:8080
- **Логи:** `/var/log/aila.log`

## Команды управления ботом

### Остановить бота:
```bash
pkill -f "aila.scripts.run_web"
```

### Запустить бота в фоне:
```bash
cd /opt/aila
nohup /opt/aila/venv/bin/python -m aila.scripts.run_web > /var/log/aila.log 2>&1 &
```

### Перезапустить бота:
```bash
pkill -f "aila.scripts.run_web"; sleep 2; nohup /opt/aila/venv/bin/python -m aila.scripts.run_web > /var/log/aila.log 2>&1 &
```

### Проверить статус:
```bash
ps aux | grep -v grep | grep "aila"
tail -30 /var/log/aila.log
curl -s http://localhost:8080/ | head -c 200
```

## Проверка ветки на сервере
```bash
cd /opt/aila && git branch -v
```

Должна быть активна: `* claude/aila-trading-system-IODYI`

## Если интерфейс старый/неправильный

1. Проверь текущую ветку: `git branch -v`
2. Если ветка неправильная:
```bash
pkill -f "aila.scripts.run_web"
git fetch --all
git checkout claude/aila-trading-system-IODYI
git reset --hard origin/claude/aila-trading-system-IODYI
nohup /opt/aila/venv/bin/python -m aila.scripts.run_web > /var/log/aila.log 2>&1 &
```

## Функции нового интерфейса (ветка IODYI)
- Использование баланса (%) - слайдер
- Лимит потерь (%) - слайдер
- Настройки стратегии (отдельный блок)
- Фильтры сигналов (EMA фильтр, Трейлинг-стоп)
- Фильтры авто-торговли:
  - Vol 24h Min/Max
  - Price Min/Max $
  - Change Min/Max %
  - Volatility filters
- Ранний вход (вход в начале тренда)
- Trailing SL, Partial TP, Trailing TP с тултипами
- Multi-bot architecture

## Структура проекта
```
/opt/aila/
├── aila/
│   ├── api/          # FastAPI endpoints + main.py (веб-сервер)
│   ├── core/         # Стратегии, индикаторы, риск-менеджмент
│   ├── exchange/     # Bybit API интеграция
│   ├── trading/      # Торговый движок
│   ├── scripts/      # run_web.py - точка входа
│   └── static/       # HTML/CSS/JS интерфейс
├── venv/             # Python virtual environment
└── data/             # SQLite база данных
```

## GitHub репозиторий
https://github.com/alexmaestro2022/claude-code

## При проблемах
1. НЕ переключай ветки самостоятельно
2. Сначала спроси пользователя какая ветка нужна
3. Покажи `git branch -v` чтобы пользователь подтвердил
