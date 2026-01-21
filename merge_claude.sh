#!/bin/bash
# Скрипт для мержа изменений от Claude

if [ -z "$1" ]; then
    echo "Использование: ./merge_claude.sh claude/имя-ветки-сессии"
    echo "Пример: ./merge_claude.sh claude/review-memo-continue-MxMKP"
    exit 1
fi

BRANCH="$1"
cd /opt/aila

echo "=== Получаю изменения из $BRANCH ==="
git fetch origin "$BRANCH"

echo "=== Смотрю что будет смержено ==="
git log HEAD..origin/"$BRANCH" --oneline

echo ""
read -p "Продолжить мерж? (y/n): " confirm
if [ "$confirm" != "y" ]; then
    echo "Отменено"
    exit 0
fi

echo "=== Мержу ==="
git merge origin/"$BRANCH" -m "Merge from Claude: $BRANCH"

echo "=== Пушу на GitHub ==="
git push origin claude/start-new-session-4XrKU

echo "=== Перезапускаю бота ==="
pkill -f "aila.scripts.run_web"
sleep 2
nohup /opt/aila/venv/bin/python -m aila.scripts.run_web > /var/log/aila.log 2>&1 &

echo "=== Готово! ==="
sleep 2
tail -15 /var/log/aila.log
