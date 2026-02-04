#!/bin/bash
# AILA AI Trade — Daily Backup

BACKUP_DIR="/opt/aila/backups"
DATE=$(date +%Y-%m-%d)
TARGET="$BACKUP_DIR/$DATE"
LOG="/opt/aila/logs/backup.log"

mkdir -p "$TARGET"

# Copy critical data
cp -r /opt/aila/data/ai_trade/ "$TARGET/ai_trade_data/" 2>/dev/null
cp /opt/aila/data/subscription.json "$TARGET/" 2>/dev/null
cp -r /opt/aila/data/admin/ "$TARGET/admin_data/" 2>/dev/null
cp /opt/aila/.env "$TARGET/dot_env_backup" 2>/dev/null
cp /opt/aila/AILA_SESSION_MEMORY.md "$TARGET/" 2>/dev/null
cp /opt/aila/CLAUDE_MEMO.md "$TARGET/" 2>/dev/null
cp /opt/aila/.claude/.credentials.json "$TARGET/credentials_backup.json" 2>/dev/null

# Backup size
SIZE=$(du -sh "$TARGET" | cut -f1)
echo "$(date): Backup created: $TARGET ($SIZE)" >> "$LOG"

# Rotation — keep last 7
cd "$BACKUP_DIR"
ls -dt */ 2>/dev/null | tail -n +8 | xargs rm -rf 2>/dev/null

echo "$(date): Rotation done. Keeping last 7 backups." >> "$LOG"
