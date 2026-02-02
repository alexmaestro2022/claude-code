#!/bin/bash
# OAuth token check for Claude CLI with Telegram alerts

LOG="/opt/aila/logs/ai_trade/oauth_check.log"
CREDS="/opt/aila/.claude/.credentials.json"
ENV_FILE="/opt/aila/.env"

TELEGRAM_BOT_TOKEN=$(grep "^TELEGRAM_BOT_TOKEN=" "$ENV_FILE" | tail -1 | cut -d= -f2)
TELEGRAM_CHAT_ID=$(grep "^TELEGRAM_CHAT_ID=" "$ENV_FILE" | tail -1 | cut -d= -f2)

send_telegram() {
    if [ -z "$TELEGRAM_BOT_TOKEN" ] || [ -z "$TELEGRAM_CHAT_ID" ]; then
        echo "$(date): Telegram not configured" >> "$LOG"
        return 1
    fi
    curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        -d chat_id="${TELEGRAM_CHAT_ID}" \
        -d text="$1" \
        -d parse_mode="HTML" > /dev/null 2>&1
}

# Check credentials file exists
if [ ! -f "$CREDS" ]; then
    echo "$(date): NO_CREDENTIALS - file not found" >> "$LOG"
    send_telegram "🔴 <b>AILA AI Trade</b>: OAuth credentials file not found!

<code>$CREDS</code>

Run:
<code>su - aila -c 'claude auth login'</code>
<code>cp /home/aila/.claude/.credentials.json /opt/aila/.claude/.credentials.json</code>"
    exit 1
fi

# Check token expiry from credentials
EXPIRES_MS=$(python3 -c "
import json, sys
try:
    with open('$CREDS') as f:
        data = json.load(f)
    oauth = data.get('claudeAiOauth', {})
    exp = oauth.get('expiresAt', 0)
    print(int(exp))
except Exception:
    print(0)
" 2>/dev/null)

NOW_MS=$(python3 -c "import time; print(int(time.time() * 1000))")
REMAINING_MS=$((EXPIRES_MS - NOW_MS))
REMAINING_HOURS=$((REMAINING_MS / 3600000))

if [ "$REMAINING_MS" -le 0 ]; then
    echo "$(date): EXPIRED (expired ${REMAINING_HOURS}h ago)" >> "$LOG"
    send_telegram "🔴 <b>AILA AI Trade</b>: OAuth токен ИСТЁК!

Бот НЕ может использовать Claude API!

Зайди на сервер и выполни:
<code>su - aila -c 'claude auth login'</code>
Затем:
<code>cp /home/aila/.claude/.credentials.json /opt/aila/.claude/.credentials.json</code>
<code>sudo systemctl restart aila</code>"
    exit 1
elif [ "$REMAINING_MS" -le 7200000 ]; then
    echo "$(date): WARNING - expires in ${REMAINING_HOURS}h" >> "$LOG"
    send_telegram "🟡 <b>AILA AI Trade</b>: OAuth токен истекает через ~${REMAINING_HOURS}ч!

Обнови токен:
<code>su - aila -c 'claude auth login'</code>
<code>cp /home/aila/.claude/.credentials.json /opt/aila/.claude/.credentials.json</code>"
    exit 0
else
    echo "$(date): OK (expires in ${REMAINING_HOURS}h)" >> "$LOG"
    exit 0
fi
