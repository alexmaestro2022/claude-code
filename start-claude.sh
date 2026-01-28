#!/bin/bash
# Убить старые сессии Claude Code (кроме текущего процесса)
pkill -9 -f "claude" -o 2>/dev/null
sleep 1
# Запустить новую сессию
cd /opt/aila
exec claude
