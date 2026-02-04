#!/bin/bash
# OAuth token check + auto-refresh for Claude CLI with Telegram alerts

LOG="/opt/aila/logs/ai_trade/oauth_check.log"
CREDS="/opt/aila/.claude/.credentials.json"
HOME_CREDS="/home/aila/.claude/.credentials.json"
ENV_FILE="/opt/aila/.env"

TELEGRAM_BOT_TOKEN=$(grep "^TELEGRAM_BOT_TOKEN=" "$ENV_FILE" | tail -1 | cut -d= -f2)
TELEGRAM_CHAT_ID=$(grep "^TELEGRAM_CHAT_ID=" "$ENV_FILE" | tail -1 | cut -d= -f2)

send_telegram() {
    if [ -z "$TELEGRAM_BOT_TOKEN" ] || [ -z "$TELEGRAM_CHAT_ID" ]; then
        return 1
    fi
    curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        -d chat_id="${TELEGRAM_CHAT_ID}" \
        -d text="$1" \
        -d parse_mode="HTML" > /dev/null 2>&1
}

get_remaining_hours() {
    python3 -c "
import json, time
with open('$CREDS') as f:
    data = json.load(f)
exp = int(data.get('claudeAiOauth', {}).get('expiresAt', 0))
remaining = (exp - int(time.time() * 1000)) / 3600000
print(f'{remaining:.1f}')
" 2>/dev/null
}

try_refresh() {
    # Метод 1: Python OAuthRefresher
    RESULT=$(cd /opt/aila && /opt/aila/venv/bin/python3 -c "
import asyncio, sys
sys.path.insert(0, '/opt/aila')
from aila.utils.oauth_refresh import OAuthRefresher
r = OAuthRefresher()
ok = asyncio.run(r.refresh_token())
print('REFRESHED' if ok else 'FAILED')
" 2>/dev/null)

    if [ "$RESULT" = "REFRESHED" ]; then
        echo "REFRESHED_PYTHON"
        return 0
    fi

    # Метод 2: Claude CLI — запустить claude --version чтобы триггерить auto-refresh
    echo "$(date): Python refresh failed, trying CLI refresh..." >> "$LOG"
    su - aila -c "claude --version" >> "$LOG" 2>&1
    sleep 3

    # Синхронизировать обновлённые credentials
    if [ -f "$HOME_CREDS" ]; then
        cp "$HOME_CREDS" "$CREDS" 2>/dev/null && chmod 600 "$CREDS"
    fi

    # Проверить результат
    NEW_HOURS=$(get_remaining_hours)
    if [ "$(echo "$NEW_HOURS > 1" | bc -l 2>/dev/null)" = "1" ]; then
        echo "REFRESHED_CLI"
        return 0
    fi

    echo "FAILED"
    return 1
}

# === MAIN ===

# 1. Синхронизировать credentials от CLI
if [ -f "$HOME_CREDS" ]; then
    cp "$HOME_CREDS" "$CREDS" 2>/dev/null && chmod 600 "$CREDS"
    echo "$(date): Synced credentials from $HOME_CREDS" >> "$LOG"
fi

# 2. Проверить файл
if [ ! -f "$CREDS" ]; then
    echo "$(date): NO_CREDENTIALS" >> "$LOG"
    send_telegram "🔴 <b>AILA AI Trade</b>: OAuth credentials not found!

<code>su - aila -c 'claude /login'</code>"
    exit 1
fi

# 3. Проверить время жизни
HOURS=$(get_remaining_hours)
echo "$(date): Token expires in ${HOURS}h" >> "$LOG"

if [ "$(echo "$HOURS <= 0" | bc -l 2>/dev/null)" = "1" ]; then
    # EXPIRED
    echo "$(date): EXPIRED, attempting refresh..." >> "$LOG"
    RESULT=$(try_refresh)
    if [[ "$RESULT" == REFRESHED* ]]; then
        NEW_HOURS=$(get_remaining_hours)
        echo "$(date): $RESULT — now ${NEW_HOURS}h" >> "$LOG"
        send_telegram "🟢 <b>AILA AI Trade</b>: OAuth обновлён ($RESULT, ${NEW_HOURS}h)"
        exit 0
    fi
    echo "$(date): ALL REFRESH METHODS FAILED" >> "$LOG"
    send_telegram "🔴 <b>AILA AI Trade</b>: OAuth ИСТЁК! Все методы refresh не сработали!

<code>su - aila -c 'claude /login'</code>
<code>cp /home/aila/.claude/.credentials.json /opt/aila/.claude/.credentials.json</code>
<code>sudo systemctl restart aila</code>"
    exit 1

elif [ "$(echo "$HOURS < 3" | bc -l 2>/dev/null)" = "1" ]; then
    # < 3 hours — try refresh
    echo "$(date): WARNING (${HOURS}h left), attempting refresh..." >> "$LOG"
    RESULT=$(try_refresh)
    if [[ "$RESULT" == REFRESHED* ]]; then
        NEW_HOURS=$(get_remaining_hours)
        echo "$(date): $RESULT — now ${NEW_HOURS}h" >> "$LOG"
        send_telegram "🟢 <b>AILA AI Trade</b>: OAuth обновлён (${NEW_HOURS}h)"
        exit 0
    fi
    echo "$(date): WARNING — refresh failed, ${HOURS}h left" >> "$LOG"
    send_telegram "🟡 <b>AILA AI Trade</b>: OAuth истекает через ${HOURS}h! Refresh не удался.

<code>su - aila -c 'claude /login'</code>"
    exit 0

else
    echo "$(date): OK (${HOURS}h)" >> "$LOG"
    exit 0
fi
