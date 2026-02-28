#!/bin/bash
# Убивает процессы Claude старше 24 часов
# Запускать через cron каждые 6 часов

LOG="/opt/aila/logs/cleanup.log"
echo "$(date): Starting Claude cleanup" >> $LOG

# Находим и убиваем процессы Claude старше 1 дня
OLD_PIDS=$(ps aux | grep -i claude | grep -v grep | awk '{print $2}' | while read pid; do
    # Проверяем возраст процесса
    if [ -d "/proc/$pid" ]; then
        age_seconds=$(( $(date +%s) - $(stat -c %Y /proc/$pid) ))
        if [ $age_seconds -gt 86400 ]; then  # Старше 24 часов
            echo $pid
        fi
    fi
done)

if [ -n "$OLD_PIDS" ]; then
    count=$(echo "$OLD_PIDS" | wc -w)
    echo "$(date): Killing $count old Claude processes" >> $LOG
    echo "$OLD_PIDS" | xargs -r kill -9 2>/dev/null
else
    echo "$(date): No old Claude processes found" >> $LOG
fi

# Показать текущую память
free -h | head -2 >> $LOG
