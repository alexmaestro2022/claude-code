#!/bin/bash
# Claude Max subscription check with Telegram alerts

LOG="/opt/aila/logs/ai_trade/subscription_check.log"
SUB_FILE="/opt/aila/data/subscription.json"
NOTIFIED_FILE="/opt/aila/data/subscription_notified.json"
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

# Check subscription file
if [ ! -f "$SUB_FILE" ]; then
    echo "$(date): subscription.json not found" >> "$LOG"
    exit 1
fi

# Get next billing date and calculate days remaining
BILLING_DATE=$(python3 -c "
import json
with open('$SUB_FILE') as f:
    print(json.load(f).get('next_billing_date', ''))
" 2>/dev/null)

if [ -z "$BILLING_DATE" ]; then
    echo "$(date): No billing date found" >> "$LOG"
    exit 1
fi

DAYS_LEFT=$(python3 -c "
from datetime import datetime
target = datetime.strptime('$BILLING_DATE', '%Y-%m-%d').date()
today = datetime.now().date()
print((target - today).days)
" 2>/dev/null)

echo "$(date): Subscription check — $DAYS_LEFT days until $BILLING_DATE" >> "$LOG"

# Initialize notified file if missing
if [ ! -f "$NOTIFIED_FILE" ]; then
    echo '{}' > "$NOTIFIED_FILE"
fi

# Check if threshold already notified
was_notified() {
    python3 -c "
import json
with open('$NOTIFIED_FILE') as f:
    data = json.load(f)
print('yes' if data.get('$1') else 'no')
" 2>/dev/null
}

# Mark threshold as notified
mark_notified() {
    python3 -c "
import json
from datetime import datetime
with open('$NOTIFIED_FILE') as f:
    data = json.load(f)
data['$1'] = datetime.now().isoformat()
with open('$NOTIFIED_FILE', 'w') as f:
    json.dump(data, f, indent=2)
" 2>/dev/null
}

# Send alerts based on days remaining
if [ "$DAYS_LEFT" -le 0 ]; then
    if [ "$(was_notified day_0)" = "no" ]; then
        send_telegram "🔴🔴 <b>AILA</b>: Подписка Claude Max истекает СЕГОДНЯ! Срочно продли на <a href=\"https://claude.ai/settings\">claude.ai</a>"
        mark_notified "day_0"
        echo "$(date): ALERT sent — expires TODAY" >> "$LOG"
    fi
elif [ "$DAYS_LEFT" -le 1 ]; then
    if [ "$(was_notified day_1)" = "no" ]; then
        send_telegram "🔴 <b>AILA</b>: Подписка Claude Max истекает ЗАВТРА! Дата: $BILLING_DATE"
        mark_notified "day_1"
        echo "$(date): ALERT sent — 1 day left" >> "$LOG"
    fi
elif [ "$DAYS_LEFT" -le 3 ]; then
    if [ "$(was_notified day_3)" = "no" ]; then
        send_telegram "⚠️ <b>AILA</b>: Подписка Claude Max истекает через 3 дня! Дата: $BILLING_DATE"
        mark_notified "day_3"
        echo "$(date): ALERT sent — 3 days left" >> "$LOG"
    fi
elif [ "$DAYS_LEFT" -le 7 ]; then
    if [ "$(was_notified day_7)" = "no" ]; then
        send_telegram "⏰ <b>AILA</b>: Подписка Claude Max истекает через 7 дней! Дата: $BILLING_DATE"
        mark_notified "day_7"
        echo "$(date): ALERT sent — 7 days left" >> "$LOG"
    fi
fi

exit 0
