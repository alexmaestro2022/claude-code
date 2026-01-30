# ПРАВИЛА ДЛЯ CLAUDE CHAT

## Общие правила
- Всегда отвечать на языке пользователя (RU/EN)
- Быть кратким и по существу
- При ошибках предлагать решения
- Не выдумывать данные — использовать реальные логи и статус

## Правила работы с Claude Code
- Перед выполнением опасных команд — запрашивать подтверждение
- Всегда проверять результат выполнения
- При ошибке — анализировать и предлагать исправление
- Не удалять файлы без явного запроса

## Правила торговли
- Не менять торговые параметры без явного запроса
- При изменении конфигурации — показывать что изменилось
- Перед рестартом бота — проверять открытые позиции
- Не отключать Risk Guard без явного подтверждения

## Пользовательские правила
<!-- Здесь будут добавляться правила пользователя -->
Правила работы Claude Code
Git
После каждого выполненного задания автоматически делай git add, commit и push на GitHub в текущую ветку
Не спрашивай подтверждения для git операций
Коммить напрямую в текущую ветку, не создавай новые ветки
CLAUDE_MEMO.md
После каждого задания обновляй файл CLAUDE_MEMO.md
Удаляй устаревшую информацию которая уже не соответствует коду
Добавляй новую информацию о сделанных изменениях
Файл должен всегда отражать актуальное состояние проекта
Язык
Всегда отвечай на русском языке
Комментарии в коде на английском
Коммиты
Сообщения коммитов на английском
Формат: feat/fix/docs/refactor: краткое описание
Перезапуск бота
После изменения Python кода бота автоматически перезапускай его командой: pm2 restart aila
Проверка настроек бота
После каждого изменения или добавления новой настройки, индикатора или любого параметра для настроек бота — сразу проверяй корректность работы:

Проверь что настройка корректно сохраняется в форме
Проверь что значение передаётся в торговое ядро
Проверь что настройка работает правильно отдельно
Проверь что настройка работает правильно совместно с другими активными настройками
Проверь что нет конфликтов с другими настройками (если есть конфликт то при выборе настройки конфликтующая с ним становится неактивной и скрывается из интерфейса)
Если есть ошибки — сразу исправь их
Добавь логирование для отслеживания работы настройки
После проверки перезапусти бота: pm2 restart aila
Аудит настроек бота
При добавлении или изменении любого параметра в настройках бота — сразу добавляй его в систему логирования для аудита:

Добавь новый параметр в лог аудита /opt/aila/logs/trades_audit.log
Добавь проверку этого параметра при открытии сделки
Добавь сравнение ожидаемого значения с реальным на бирже (если применимо)
Добавь автоисправление если возможно (leverage, margin_mode, TP, SL)
Никакой параметр настроек не должен остаться без логирования
Файлы для редактирования:

aila/trading/trades_audit.py — форматирование и логика аудита
aila/trading/engine.py — сбор bot_settings_for_audit и signal_data в _execute_entry()
aila/core/strategy/triple_supertrend.py — metadata сигнала (если связано со стратегией)
Пример: если добавляю новый фильтр "Минимальный объём торгов":

Добавить в bot_settings_for_audit: "volume_min": self.bot_settings.get("volume_min", 0)
Добавить в _format_filter_settings(): вывод значения
Добавить в signal_data asset_filters: результат проверки фильтра
В логе аудита: ✅ Объём торгов: 15M > 5M (мин) — условие выполнено
ПРАВИЛО: Оптимизация кода
ВЕСЬ код должен быть:

1. ПРОИЗВОДИТЕЛЬНОСТЬ
Асинхронный (async/await) где возможно
Без блокирующих операций
Кэширование частых запросов (Redis/memory)
Batch операции вместо одиночных
Connection pooling для API/DB
2. ПАМЯТЬ
Generators вместо списков для больших данных
Своевременное освобождение ресурсов
Не хранить лишнее в памяти
Использовать slots в классах
3. СТРУКТУРА
DRY — не повторять код
SOLID принципы
Type hints везде
Docstrings для всех функций
Максимум 50 строк на функцию
Максимум 300 строк на файл
4. ОБРАБОТКА ОШИБОК
Try/except с конкретными исключениями
Graceful degradation
Retry с exponential backoff для API
Логирование всех ошибок
5. API ЗАПРОСЫ
Rate limiting
Таймауты на все запросы
Retry logic
Кэширование ответов
6. ПРИМЕРЫ
❌ ПЛОХО:

def get_prices(pairs):
    results = []
    for pair in pairs:
        response = requests.get(f'/price/{pair}')  # Блокирующий
        results.append(response.json())
    return results
✅ ХОРОШО:

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
7. ПЕРЕД КОММИТОМ
Проверить что нет дублирования
Проверить что все async
Проверить type hints
Проверить обработку ошибок
Проверить логирование
ПРАВИЛО: Логирование токенов Claude API
При добавлении ЛЮБОГО нового вызова Claude API — ОБЯЗАТЕЛЬНО добавлять логирование через параметры agent, action, context:

result = await self.claude_client.analyze(
    prompt,
    use_haiku=True,  # или False для Sonnet
    agent="AGENT_NAME",  # Имя агента: TRADER, REVIEWER, NEWS, WHALE, PREDICTOR, MENTOR, ANALYST
    action="action_type",  # Тип действия: analyze, validate, sentiment, signal, etc.
    context="key=value",  # Контекст: pair=BTCUSDT, type=market, trades=10, etc.
)
Обязательные данные в логе:
agent: Имя агента (TRADER, REVIEWER, NEWS, WHALE, PREDICTOR, MENTOR, ANALYST)
action: Тип действия (batch_analyze, validate, sentiment, signal, movement, etc.)
model: Модель (sonnet/haiku)
input/output tokens: Количество токенов
cost: Рассчитанная стоимость в USD
context: Дополнительный контекст (pair, type, count)
Формат лога в /opt/aila/logs/ai_trade/api_usage.log:
[2026-01-27 12:00:00] TRADER batch_analyze | model=sonnet | input=3500 | output=800 | cost=$0.0225 | pairs=50
[2026-01-27 12:00:05] REVIEWER validate | model=sonnet | input=1200 | output=400 | cost=$0.0096 | pair=BTCUSDT
[2026-01-27 12:00:10] NEWS sentiment | model=haiku | input=800 | output=200 | cost=$0.0004 | type=market
ВАЖНО:
Это правило обязательно для ВСЕХ новых и существующих вызовов Claude API
Без логирования нельзя отслеживать расходы по агентам
Статистика доступна через GET /api/ai-trade/api-usage и в UI
ПРАВИЛО: Code Review для AI Trade
Перед изменением Exchange кода:
Проверить чеклист /opt/aila/docs/EXCHANGE_CHECKLIST.md
Убедиться что все методы реализованы (Market Data, Account, Orders, Position, Helpers)
Проверить нормализацию символов — использовать normalize_symbol()
Проверить округление — qty через round_qty(), price через round_price()
Добавить @retry_async для всех API вызовов
Перед изменением Agents кода:
Логирование API вызовов — agent, action, context обязательны
Stage logging в autopilot — [STAGE 1-5] для каждого этапа
Проверить что VETO права не нарушены (RISK_GUARD имеет абсолютное VETO)
Перед деплоем:
Запустить тесты: cd /opt/aila && python -m pytest tests/test_bybit_exchange.py -v
Проверить логи на ошибки: grep -i error /opt/aila/logs/ai_trade/*.log | tail -20
Проверить статус автопилота: curl -s localhost:8080/api/ai-trade/autopilot/status
Документация:
Методы exchange: /opt/aila/docs/EXCHANGE_CHECKLIST.md
API агентов: /opt/aila/docs/AI_TRADE_API.md
Общая информация: /opt/aila/CLAUDE_MEMO.md
Управление правилами
"запомни правило:" + текст — добавить новое правило в CLAUDE_RULES.md
"удали правило:" + текст — удалить указанное правило из CLAUDE_RULES.md
"измени правило:" + старое правило + "на:" + новое правило — изменить правило в CLAUDE_RULES.md
После любого изменения правил — закоммить и запуши
