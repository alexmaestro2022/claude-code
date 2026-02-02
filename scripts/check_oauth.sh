#!/bin/bash
# OAuth token check + auto-refresh for Claude CLI with Telegram alerts

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

try_refresh() {
    # Try auto-refresh via Python module
    REFRESH_RESULT=$(cd /opt/aila && /opt/aila/venv/bin/python3 -c "
import asyncio, sys
sys.path.insert(0, '/opt/aila')
from aila.utils.oauth_refresh import OAuthRefresher
r = OAuthRefresher()
ok = asyncio.run(r.refresh_token())
print('REFRESHED' if ok else 'FAILED')
" 2>/dev/null)
    echo "$REFRESH_RESULT"
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
import json
with open('$CREDS') as f:
    data = json.load(f)
print(int(data.get('claudeAiOauth', {}).get('expiresAt', 0)))
" 2>/dev/null)

NOW_MS=$(python3 -c "import time; print(int(time.time() * 1000))")
REMAINING_MS=$((EXPIRES_MS - NOW_MS))
REMAINING_HOURS=$((REMAINING_MS / 3600000))

if [ "$REMAINING_MS" -le 0 ]; then
    # Expired — try refresh first
    echo "$(date): EXPIRED, attempting auto-refresh..." >> "$LOG"
    RESULT=$(try_refresh)
    if [ "$RESULT" = "REFRESHED" ]; then
        echo "$(date): AUTO-REFRESHED after expiry" >> "$LOG"
        send_telegram "🟢 <b>AILA AI Trade</b>: OAuth токен был истёкшим — автоматически обновлён!"
        exit 0
    fi
    echo "$(date): EXPIRED and refresh FAILED" >> "$LOG"
    send_telegram "🔴 <b>AILA AI Trade</b>: OAuth токен ИСТЁК!

Auto-refresh FAILED. Бот НЕ может использовать Claude API!

Зайди на сервер и выполни:
<code>su - aila -c 'claude auth login'</code>
Затем:
<code>cp /home/aila/.claude/.credentials.json /opt/aila/.claude/.credentials.json</code>
<code>sudo systemctl restart aila</code>"
    exit 1

elif [ "$REMAINING_MS" -le 7200000 ]; then
    # Less than 2h — try refresh
    echo "$(date): WARNING (${REMAINING_HOURS}h left), attempting auto-refresh..." >> "$LOG"
    RESULT=$(try_refresh)
    if [ "$RESULT" = "REFRESHED" ]; then
        echo "$(date): AUTO-REFRESHED (was ${REMAINING_HOURS}h left)" >> "$LOG"
        send_telegram "🟢 <b>AILA AI Trade</b>: OAuth токен автоматически обновлён (оставалось ~${REMAINING_HOURS}ч)"
        exit 0
    fi
    echo "$(date): WARNING - refresh failed, expires in ${REMAINING_HOURS}h" >> "$LOG"
    send_telegram "🟡 <b>AILA AI Trade</b>: OAuth токен истекает через ~${REMAINING_HOURS}ч!

Auto-refresh не удался.

Обнови вручную:
<code>su - aila -c 'claude auth login'</code>
<code>cp /home/aila/.claude/.credentials.json /opt/aila/.claude/.credentials.json</code>"
    exit 0

else
    echo "$(date): OK (expires in ${REMAINING_HOURS}h)" >> "$LOG"
    exit 0
fi
