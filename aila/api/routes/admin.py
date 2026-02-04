"""
Admin panel API endpoints.

Handles authentication via Telegram code, Claude Chat/Code integration,
commands management, scheduled tasks, and knowledge base.
"""

import asyncio
import json
import logging
import os
import random
import secrets
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import re
import zipfile
from io import BytesIO

import aiohttp
from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import JSONResponse, Response, StreamingResponse

logger = logging.getLogger("admin")

router = APIRouter(prefix="/api/admin", tags=["Admin"])


_CREDS_SRC = Path("/home/aila/.claude/.credentials.json")
_CREDS_DST = Path("/opt/aila/.claude/.credentials.json")
_CREDS_LAST_SYNC: float = 0


def _sync_claude_credentials() -> None:
    """Sync OAuth credentials from /home/aila to /opt/aila if newer.

    systemd ProtectHome=true blocks /home/aila from the aila service,
    but this function may run from contexts where /home is accessible
    (e.g. Claude Code subprocess or cron).
    """
    global _CREDS_LAST_SYNC
    now = time.time()
    # Check at most every 60 seconds
    if now - _CREDS_LAST_SYNC < 60:
        return
    _CREDS_LAST_SYNC = now

    try:
        if not _CREDS_SRC.exists():
            return
        # Compare mtime — sync only if source is newer
        src_mtime = _CREDS_SRC.stat().st_mtime
        dst_mtime = _CREDS_DST.stat().st_mtime if _CREDS_DST.exists() else 0
        if src_mtime > dst_mtime:
            import shutil
            shutil.copy2(str(_CREDS_SRC), str(_CREDS_DST))
            logger.info("[AUTH] Synced OAuth credentials from /home/aila to /opt/aila")
    except PermissionError:
        # Expected when running under systemd with ProtectHome=true
        pass
    except Exception as e:
        logger.debug(f"[AUTH] Credentials sync skipped: {e}")


def _get_claude_env() -> dict[str, str]:
    """Build environment for claude subprocess using Max subscription.

    Claude Code is authorized via OAuth (Max subscription).
    ANTHROPIC_API_KEY must NOT be passed — it forces paid API billing.

    systemd uses ProtectHome=true so /home/aila/.claude/ is inaccessible.
    Credentials are copied to /opt/aila/.claude/.credentials.json,
    and HOME is set to /opt/aila so claude finds them there.
    """
    # Try to sync fresh credentials (no-op if /home is blocked)
    _sync_claude_credentials()

    env = os.environ.copy()
    # Remove API keys to force OAuth/subscription auth
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("CLAUDE_API_KEY", None)
    # Ensure claude is in PATH
    if "/usr/local/bin" not in env.get("PATH", ""):
        env["PATH"] = f"/usr/local/bin:{env.get('PATH', '/usr/bin')}"
    # Set HOME to /opt/aila where .claude/.credentials.json lives
    # (ProtectHome=true blocks /home/aila from systemd service)
    env["HOME"] = "/opt/aila"
    return env

# Claude model — Max subscription, always Opus 4.5
CLAUDE_MODEL = "opus"

# Paths
DATA_DIR = Path("/opt/aila/data/admin")
CHAT_DIR = Path("/opt/aila/claude_chat")
KNOWLEDGE_DIR = CHAT_DIR / "knowledge"
HISTORY_DIR = CHAT_DIR / "history"
LOGS_DIR = Path("/opt/aila/logs")
SETTINGS_FILE = DATA_DIR / "settings.json"

# Ensure directories exist
for d in [DATA_DIR, KNOWLEDGE_DIR, HISTORY_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# Telegram config
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Persistence paths
_SESSIONS_FILE = Path("/opt/aila/data/admin/sessions.json")
_AUTH_CODES_FILE = Path("/opt/aila/data/admin/auth_codes.json")

# Auth state (persisted to disk)
_auth_codes: dict[str, dict] = {}  # code -> {expires, attempts}
_admin_sessions: dict[str, dict] = {}  # token -> {created, last_active}
_failed_attempts: dict[str, dict] = {}  # ip -> {count, blocked_until}

# Running processes (async subprocess for streaming)
_running_process: Optional[asyncio.subprocess.Process] = None
_process_lock = asyncio.Lock()

# Scheduler state
_scheduler_task: Optional[asyncio.Task] = None
_task_queue: asyncio.Queue = asyncio.Queue()
_queue_running = False

# Knowledge base state
_knowledge_loaded = False
_knowledge_last_loaded: Optional[str] = None
_knowledge_cache: dict[str, str] = {}  # filename -> content

# Chat session state (removed globals — now in _admin_sessions[token])

# File type classification
_FILE_TYPE_MAP = {
    "RULES": "rules",
    "AILA_SESSION_MEMORY": "context",
    "SESSION_MEMORY": "context",
    "TRADING_STRATEGY": "strategy",
    "PROMPTS": "prompts",
    "FAQ": "faq",
}

# Priority order for knowledge files in prompt
_KNOWLEDGE_PRIORITY = ["RULES.md", "AILA_SESSION_MEMORY.md"]

# Constants
CODE_TTL = 300  # 5 minutes
SESSION_TTL = 1800  # 30 minutes
MAX_ATTEMPTS = 5
BLOCK_DURATION = 600  # 10 minutes
ALLOWED_EXTENSIONS = {".txt", ".md", ".json", ".py"}
MAX_FILE_SIZE = 1_000_000  # 1MB
RATE_LIMIT_COOLDOWN = 120  # 2 min pause for scheduled tasks on rate limit
COMMAND_HISTORY_FILE = Path("/opt/aila/data/admin/command_history.json")
COMMAND_HISTORY_MAX = 500
COMMAND_TIMEOUT_DEFAULT = 300  # 5 minutes default execution timeout

# Rate limit detection patterns
RATE_LIMIT_PATTERNS = (
    "rate limit",
    "rate_limit",
    "ratelimit",
    "429",
    "too many requests",
    "overloaded",
    "request limit exceeded",
)


# =============================================
# Helper functions
# =============================================

def _load_json(path: Path, default: Any = None) -> Any:
    """Load JSON file or return default."""
    try:
        if path.exists():
            return json.loads(path.read_text())
    except Exception as e:
        logger.error(f"Failed to load {path}: {e}")
    return default if default is not None else {}


def _save_json(path: Path, data: Any) -> None:
    """Save data to JSON file."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    except Exception as e:
        logger.error(f"Failed to save {path}: {e}")


def _record_command_history(
    command: str,
    source: str,
    risk_level: str,
    command_type: str,
    status: str,
    duration: float,
    output_preview: str = "",
    error: str | None = None,
) -> None:
    """Record command execution to history (FIFO, max 500)."""
    try:
        history = _load_json(COMMAND_HISTORY_FILE, [])
        if not isinstance(history, list):
            history = []
        entry = {
            "id": f"cmd_{int(time.time() * 1000)}",
            "timestamp": datetime.now().isoformat(),
            "command": command,
            "source": source,
            "risk_level": risk_level,
            "command_type": command_type,
            "status": status,
            "duration_seconds": round(duration, 1),
            "output_preview": output_preview[:200] if output_preview else "",
            "error": error,
        }
        history.insert(0, entry)
        if len(history) > COMMAND_HISTORY_MAX:
            history = history[:COMMAND_HISTORY_MAX]
        _save_json(COMMAND_HISTORY_FILE, history)
    except Exception as e:
        logger.error(f"Failed to record command history: {e}")


def _detect_command_type(command: str) -> str:
    """Detect command type: read-only, system, trading, modification."""
    c = command.lower()
    ro = ["cat ", "tail ", "head ", "grep ", "ls ", "find ", "ps ", "top ",
          "df ", "du ", "wc ", "echo ", "journalctl", "systemctl status",
          "git log", "git status", "git diff", "curl -s"]
    trade = ["position", "trade", "order", "leverage", "autopilot",
             "balance", "close_all", "open_position"]
    sys_cmds = ["systemctl restart", "systemctl stop", "service ", "chmod ",
                "chown ", "pip ", "npm ", "apt ", "git push", "git commit"]
    if any(p in c for p in trade):
        return "trading"
    if any(p in c for p in sys_cmds):
        return "system"
    if any(p in c for p in ro):
        return "read-only"
    return "modification"


def _audit_log(user: str, action: str, command: str = "", result: str = "") -> None:
    """Write to admin audit log."""
    try:
        log_path = LOGS_DIR / "admin_audit.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] [{user}] [{action}] {command}"
        if result:
            line += f" | {result[:200]}"
        with open(log_path, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _save_sessions() -> None:
    """Persist admin sessions to disk."""
    serializable = {}
    for token, sess in _admin_sessions.items():
        serializable[token] = {
            k: v for k, v in sess.items()
            if isinstance(v, (str, int, float, bool, type(None)))
        }
    _save_json(_SESSIONS_FILE, serializable)


def _load_sessions() -> None:
    """Load admin sessions from disk, filtering expired ones."""
    global _admin_sessions
    data = _load_json(_SESSIONS_FILE, {})
    now = time.time()
    for token, sess in data.items():
        if now - sess.get("last_active", 0) < SESSION_TTL:
            _admin_sessions[token] = sess
    if _admin_sessions:
        logger.info(f"[AUTH] Loaded {len(_admin_sessions)} active session(s) from disk")


def _save_auth_codes() -> None:
    """Persist auth codes to disk."""
    _save_json(_AUTH_CODES_FILE, _auth_codes)


def _load_auth_codes() -> None:
    """Load auth codes from disk, filtering expired ones."""
    global _auth_codes
    data = _load_json(_AUTH_CODES_FILE, {})
    now = time.time()
    for code, info in data.items():
        if info.get("expires", 0) > now:
            _auth_codes[code] = info
    if _auth_codes:
        logger.info(f"[AUTH] Loaded {len(_auth_codes)} pending code(s) from disk")


# Load persisted state on module import
_load_sessions()
_load_auth_codes()


async def _send_telegram(text: str) -> bool:
    """Send message via Telegram bot."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram not configured")
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
            }, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                return resp.status == 200
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")
        return False


def _verify_admin_session(request: Request) -> bool:
    """Verify admin session token from header."""
    token = request.headers.get("X-Admin-Token", "")
    if not token or token not in _admin_sessions:
        return False
    session = _admin_sessions[token]
    # Check session timeout (30 min inactivity)
    if time.time() - session["last_active"] > SESSION_TTL:
        del _admin_sessions[token]
        _save_sessions()
        return False
    # Update last active
    session["last_active"] = time.time()
    _save_sessions()
    return True


def _require_auth(request: Request) -> None:
    """Raise 401 if not authenticated."""
    if not _verify_admin_session(request):
        raise HTTPException(status_code=401, detail="Unauthorized")


def _get_session_token(request: Request) -> str:
    """Extract admin session token from request header."""
    return request.headers.get("X-Admin-Token", "")


def _get_client_ip(request: Request) -> str:
    """Get client IP address."""
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# =============================================
# Rate limit detection
# =============================================

def _is_rate_limit_error(stderr: str) -> bool:
    """Check if stderr indicates a rate limit error.

    Only pass stderr here, never stdout — stdout contains Claude's
    response text which may include words like 'overloaded' in context.
    """
    if not stderr:
        return False
    stderr_lower = stderr.lower()
    return any(p in stderr_lower for p in RATE_LIMIT_PATTERNS)



async def _handle_rate_limit(source: str, error_msg: str) -> dict:
    """Handle rate limit: log, notify, update stats. No blocking."""
    now = datetime.now()

    logger.warning(f"[ADMIN] Rate limit detected from {source}: {error_msg[:200]}")
    _audit_log("admin", "RATE_LIMIT", source, error_msg[:200])

    # Update rate limit stats in admin_stats.json
    stats = _load_admin_stats()
    rl = stats.get("rate_limits", {
        "last_hit": None,
        "last_hit_time": None,
        "hits_today": 0,
        "hits_total": 0,
    })
    rl["last_hit"] = now.isoformat()
    rl["last_hit_time"] = now.strftime("%H:%M:%S")
    rl["hits_today"] = rl.get("hits_today", 0) + 1
    rl["hits_total"] = rl.get("hits_total", 0) + 1
    stats["rate_limits"] = rl
    _save_json(ADMIN_STATS_FILE, stats)

    # Send Telegram notification (info only, no blocking)
    admin_stats = _load_admin_stats()
    total_today = admin_stats.get("total_requests", 0)
    await _send_telegram(
        f"⚠️ <b>Rate limit от Anthropic</b>\n"
        f"⏱ Время: {now.strftime('%H:%M:%S')}\n"
        f"📊 Запросов сегодня: {total_today}\n"
        f"💬 Сообщение: «{error_msg[:150]}»\n"
        f"ℹ️ Подписка Max — можно повторить запрос сразу."
    )

    return {
        "error": True,
        "error_type": "rate_limit_info",
        "message": "Anthropic вернул ошибку rate limit. Можно повторить запрос.",
        "message_en": "Anthropic returned rate limit error. You can retry immediately.",
        "timestamp": now.isoformat(),
    }


# =============================================
# Auth endpoints
# =============================================

@router.post("/request-code")
async def request_code(request: Request):
    """Generate 6-digit code and send via Telegram."""
    ip = _get_client_ip(request)

    # Check if IP is blocked
    if ip in _failed_attempts:
        fa = _failed_attempts[ip]
        if fa.get("blocked_until", 0) > time.time():
            remaining = int(fa["blocked_until"] - time.time())
            raise HTTPException(
                status_code=429,
                detail=f"Too many attempts. Blocked for {remaining}s",
            )

    # Generate code
    code = f"{random.randint(100000, 999999)}"
    _auth_codes[code] = {
        "expires": time.time() + CODE_TTL,
        "ip": ip,
    }
    _save_auth_codes()

    # Send to Telegram
    await _send_telegram(f"🔐 Код для входа в Админ: <b>{code}</b>\n⏱ Действителен 5 минут\n🌐 IP: {ip}")
    _audit_log(ip, "REQUEST_CODE")

    return {"success": True, "message": "Code sent to Telegram"}


@router.post("/verify-code")
async def verify_code(request: Request):
    """Verify the 6-digit code and create session."""
    ip = _get_client_ip(request)

    # Check if IP is blocked
    if ip in _failed_attempts:
        fa = _failed_attempts[ip]
        if fa.get("blocked_until", 0) > time.time():
            remaining = int(fa["blocked_until"] - time.time())
            raise HTTPException(status_code=429, detail=f"Blocked for {remaining}s")

    body = await request.json()
    code = str(body.get("code", "")).strip()

    if not code:
        raise HTTPException(status_code=400, detail="Code required")

    # Clean expired codes
    now = time.time()
    expired = [k for k, v in _auth_codes.items() if v["expires"] < now]
    for k in expired:
        del _auth_codes[k]

    # Verify code
    if code not in _auth_codes:
        # Track failed attempts
        if ip not in _failed_attempts:
            _failed_attempts[ip] = {"count": 0}
        _failed_attempts[ip]["count"] += 1

        if _failed_attempts[ip]["count"] >= MAX_ATTEMPTS:
            _failed_attempts[ip]["blocked_until"] = now + BLOCK_DURATION
            _audit_log(ip, "BLOCKED", f"attempts={_failed_attempts[ip]['count']}")
            raise HTTPException(status_code=429, detail="Too many attempts. Blocked for 10 minutes")

        raise HTTPException(status_code=403, detail="Invalid code")

    # Code valid - create session
    del _auth_codes[code]
    _save_auth_codes()
    if ip in _failed_attempts:
        del _failed_attempts[ip]

    token = secrets.token_urlsafe(32)
    _admin_sessions[token] = {
        "created": now,
        "last_active": now,
        "ip": ip,
        "session_context_sent": False,
        "auto_confirmed": False,
    }
    _save_sessions()

    await _send_telegram(f"🔓 Вход в Админ панель\n🌐 IP: {ip}")
    _audit_log(ip, "LOGIN")

    return {"success": True, "token": token}


@router.get("/session")
async def check_session(request: Request):
    """Check if current session is valid and return history count."""
    valid = _verify_admin_session(request)
    result: dict[str, Any] = {"valid": valid}
    if valid:
        history = _get_chat_history(200)
        result["history_count"] = len(history)
    return result


@router.post("/session/initialize")
async def initialize_session(request: Request):
    """Initialize chat session: load knowledge base, return session info."""
    _require_auth(request)

    token = _get_session_token(request)
    if not token or token not in _admin_sessions:
        raise HTTPException(status_code=401, detail="Unauthorized")

    # Generate session ID and timestamp
    session_id = secrets.token_urlsafe(8)
    session_started = time.time()

    # Save to admin sessions
    _admin_sessions[token]["session_id"] = session_id
    _admin_sessions[token]["session_started"] = session_started

    # Load knowledge base
    knowledge = _load_knowledge_base()
    kb_files = list(_knowledge_cache.keys())

    # Get history count
    history = _get_chat_history(200)
    history_count = len(history)

    logger.info(
        f"[SESSION] Initialized session {session_id}, "
        f"kb_files={len(kb_files)}, history={history_count}"
    )

    return {
        "session_id": session_id,
        "knowledge_loaded": _knowledge_loaded,
        "knowledge_files": kb_files,
        "knowledge_last_loaded": _knowledge_last_loaded,
        "history_count": history_count,
        "started": datetime.fromtimestamp(session_started).isoformat(),
    }


@router.get("/session/status")
async def session_status(request: Request):
    """Get current chat session status."""
    _require_auth(request)

    token = _get_session_token(request)
    if not token or token not in _admin_sessions:
        return {"active": False}

    session = _admin_sessions[token]
    session_id = session.get("session_id")
    session_started = session.get("session_started")

    history = _get_chat_history(200)
    history_count = len(history)

    uptime_sec = int(time.time() - session_started) if session_started else 0
    hours, remainder = divmod(uptime_sec, 3600)
    minutes, _ = divmod(remainder, 60)
    uptime_str = f"{hours}h {minutes}m" if hours else f"{minutes}m"

    return {
        "session_id": session_id,
        "active": session_started is not None,
        "started": (
            datetime.fromtimestamp(session_started).isoformat()
            if session_started else None
        ),
        "uptime": uptime_str,
        "knowledge_loaded": _knowledge_loaded,
        "knowledge_files": list(_knowledge_cache.keys()),
        "knowledge_last_loaded": _knowledge_last_loaded,
        "history_count": history_count,
        "code_running": (
            _running_process is not None
            and _running_process.returncode is None
        ),
    }


@router.post("/session/clear")
async def clear_session(request: Request):
    """Clear session: archive history, reset knowledge cache, start fresh."""
    _require_auth(request)
    global _knowledge_loaded, _knowledge_last_loaded, _knowledge_cache

    token = _get_session_token(request)
    if not token or token not in _admin_sessions:
        raise HTTPException(status_code=401, detail="Unauthorized")

    # Archive current history
    history_file = HISTORY_DIR / "current.json"
    history = _load_json(history_file, [])
    if isinstance(history, list) and history:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_file = HISTORY_DIR / f"archive_{ts}.json"
        _save_json(archive_file, history)
        logger.info(f"[SESSION] Archived {len(history)} messages to {archive_file.name}")

    # Clear current history
    _save_json(history_file, [])

    # Reset knowledge cache (will reload on next chat)
    _knowledge_loaded = False
    _knowledge_last_loaded = None
    _knowledge_cache = {}

    # Reset session
    old_id = _admin_sessions[token].get("session_id")
    new_session_id = secrets.token_urlsafe(8)
    new_session_started = time.time()

    _admin_sessions[token]["session_id"] = new_session_id
    _admin_sessions[token]["session_started"] = new_session_started
    _admin_sessions[token]["session_context_sent"] = False
    _admin_sessions[token]["auto_confirmed"] = False

    _audit_log("admin", "SESSION_CLEAR", f"old={old_id}, new={new_session_id}")

    return {
        "success": True,
        "new_session_id": new_session_id,
        "archived": len(history) if isinstance(history, list) else 0,
    }


# =============================================
# Security settings
# =============================================

_DEFAULT_SETTINGS: dict[str, Any] = {
    "restrictions": {
        "protect_ai_trade_logic": True,
        "protect_trading_strategy": True,
        "protect_learning_system": True,
        "protect_risk_management": True,
        "protect_trading_pairs": True,
        "protect_api_keys": True,
        "protect_position_management": True,
        "protect_capital_settings": True,
        "protect_agent_settings": True,
        "protect_cascade_logic": True,
        "allow_file_edit": True,
        "allow_bot_restart": True,
        "allow_git_push": True,
        "allow_file_delete": False,
        "allow_env_edit": False,
        "allow_database_edit": False,
        "allow_log_clear": True,
        "always_confirm_trade_open": True,
        "always_confirm_trade_close": True,
        "always_confirm_autopilot_toggle": True,
        "always_confirm_leverage_change": True,
        "always_confirm_api_key_change": True,
        "max_auto_iterations": 10,
        "auto_timeout_minutes": 30,
        "require_human_every_n_actions": 5,
        "command_timeout_seconds": 300,
    },
    "protected_paths": [
        "/opt/aila/aila/ai_trade/agents/",
        "/opt/aila/aila/ai_trade/autopilot_mode.py",
        "/opt/aila/aila/ai_trade/position_manager.py",
        "/opt/aila/aila/ai_trade/signal_queue.py",
        "/opt/aila/aila/ai_trade/strategies/",
        "/opt/aila/data/ai_trade/*_knowledge.json",
        "/opt/aila/data/ai_trade/*_stats.json",
        "/opt/aila/data/ai_trade/*_settings.json",
        "/opt/aila/data/ai_trade/bot_positions.json",
        "/opt/aila/data/ai_trade/trading_state.json",
        "/opt/aila/data/ai_trade/capital.json",
        "/opt/aila/.env",
    ],
    "dangerous_commands": [
        "rm -rf /", "rm -rf /*", "rm -rf .",
        "shutdown", "reboot", "halt",
        "DROP DATABASE", "DROP TABLE", "DELETE FROM", "TRUNCATE",
        "iptables -F", "ufw disable",
        "cat .env", "echo $BYBIT", "echo $API", "printenv | grep KEY",
        "chmod 777 /", "mkfs", "dd if=",
    ],
}

# Keyword categories for protection checks
_PROTECTION_KEYWORDS: dict[str, list[str]] = {
    "protect_trading_strategy": [
        "confidence", "strategy", "signal", "indicator",
        "ema", "supertrend", "entry", "exit",
    ],
    "protect_risk_management": [
        "leverage", "risk", "margin", "stop_loss", "take_profit",
        "set_leverage", "max_leverage",
    ],
    "protect_learning_system": [
        "knowledge_base", "learning", "xp", "level",
        "stats", "winrate", "best_pairs",
    ],
    "protect_position_management": [
        "open_position", "close_position", "close_all",
        "create_order", "cancel_order",
    ],
    "protect_capital_settings": [
        "balance", "capital", "reserve", "equity",
    ],
    "protect_agent_settings": [
        "trader_settings", "sniper_settings",
    ],
    "protect_cascade_logic": [
        "cascade", "priority", "vip_pairs",
    ],
    "protect_api_keys": [
        "api_key", "api_secret", "bybit",
    ],
    "protect_trading_pairs": [
        "trading_pairs", "whitelist", "blacklist",
    ],
}


def _load_security_settings() -> dict[str, Any]:
    """Load security settings with defaults."""
    saved = _load_json(SETTINGS_FILE, {})
    if not isinstance(saved, dict):
        saved = {}
    # Merge defaults
    result = dict(_DEFAULT_SETTINGS)
    if "restrictions" in saved:
        result["restrictions"] = {**_DEFAULT_SETTINGS["restrictions"], **saved["restrictions"]}
    if "protected_paths" in saved:
        result["protected_paths"] = saved["protected_paths"]
    if "dangerous_commands" in saved:
        result["dangerous_commands"] = saved["dangerous_commands"]
    return result


def _check_command_security(
    command: str, mode: str, session: Optional[dict] = None,
) -> dict[str, Any]:
    """Check command against security restrictions.

    Args:
        command: Command string to check
        mode: Execution mode ("manual" or "auto")
        session: Admin session dict (for one-time permissions)

    Returns:
        {
            "allowed": bool,
            "needs_confirmation": bool,
            "needs_permission": bool,
            "permission_type": str or None,
            "permission_reason": str or None,
            "block_reason": str or None,
            "warnings": list[str],
            "affected_areas": list[str],
            "risk_level": "low" | "medium" | "high" | "critical"
        }
    """
    settings = _load_security_settings()
    restrictions = settings.get("restrictions", {})
    protected_paths = settings.get("protected_paths", [])
    dangerous_cmds = settings.get("dangerous_commands", [])

    # Human-readable reasons for permission types
    _perm_reasons: dict[str, str] = {
        "allow_file_delete": "File deletion is disabled in security settings",
        "allow_env_edit": "Environment file editing is disabled",
        "allow_database_edit": "Database file editing is disabled",
        "allow_bot_restart": "Bot restart is disabled",
        "allow_git_push": "Git push is disabled",
    }

    result: dict[str, Any] = {
        "allowed": True,
        "needs_confirmation": False,
        "needs_permission": False,
        "permission_type": None,
        "permission_reason": None,
        "block_reason": None,
        "warnings": [],
        "affected_areas": [],
        "risk_level": "low",
    }

    cmd_lower = command.lower()

    # 1. Dangerous commands — always hard-block (no permission dialog)
    for dc in dangerous_cmds:
        if dc.lower() in cmd_lower:
            result["allowed"] = False
            result["block_reason"] = f"Dangerous command: {dc}"
            result["risk_level"] = "critical"
            return result

    # Determine if the primary command is read-only (before Level 2).
    # Read-only commands bypass permission, protected-path, keyword,
    # always-confirm and risky-pattern checks.
    _READ_ONLY_CMDS = {
        "grep", "egrep", "fgrep", "rg", "cat", "tail", "head",
        "less", "more", "wc", "find", "ls", "du", "df", "ps",
        "top", "htop", "uptime", "free", "echo", "stat", "file",
        "diff", "sort", "uniq", "cut", "tr", "awk", "sed",
        "journalctl", "date", "whoami", "id", "hostname",
    }
    # Script launchers — running a .py/.sh/.js file is read-only
    _SCRIPT_LAUNCHERS = {"python3", "python", "bash", "sh", "node"}
    _DANGEROUS_PIPE_CMDS = {"rm", "tee", "mv", "cp", "dd", "truncate"}

    def _is_readonly_pipeline(cmd: str) -> bool:
        """Check if command is a read-only pipeline."""
        # Handle && chains: cd /opt/aila && python3 script.py
        # Split by && and check the LAST meaningful command
        chain_parts = [p.strip() for p in cmd.split("&&")]
        # Skip leading cd/export — find the main command
        main_cmd = cmd
        for cp in chain_parts:
            tokens_cp = cp.split()
            if not tokens_cp:
                continue
            base_cp = tokens_cp[0].split("/")[-1]
            if base_cp in ("cd", "export", "source"):
                continue
            main_cmd = cp
            break

        parts = [p.strip() for p in main_cmd.split("|")]
        if not parts:
            return False
        first_tokens = parts[0].split()
        if not first_tokens:
            return False
        base = first_tokens[0].split("/")[-1]  # handle /usr/bin/grep
        # sudo prefix — check next token
        if base == "sudo" and len(first_tokens) > 1:
            base = first_tokens[1].split("/")[-1]
        # curl is read-only only for GET (no -X POST, no -d, no --data)
        if base == "curl":
            first_seg = parts[0].lower()
            if any(x in first_seg for x in ["-x post", "-x put", "-x delete", "-d ", "--data"]):
                return False
            return True
        # Script launchers: python3 script.py, bash script.sh
        if base in _SCRIPT_LAUNCHERS:
            # python3 -c "print..." is read-only
            if "-c" in parts[0]:
                first_seg = parts[0].lower()
                if "print" in first_seg and not any(
                    x in first_seg for x in ["open(", "write(", "os.system", "subprocess"]
                ):
                    return True
                return False
            # Running a script file — read-only execution
            return True
        if base not in _READ_ONLY_CMDS:
            return False
        # Redirect in first segment means writing — not read-only
        if ">" in parts[0]:
            return False
        # sed -i is NOT read-only
        if base == "sed" and ("-i" in first_tokens or any(t.startswith("-i") for t in first_tokens)):
            return False
        # Check piped commands — if any pipe target is dangerous, not read-only
        for part in parts[1:]:
            tokens = part.strip().split()
            if not tokens:
                continue
            pipe_cmd = tokens[0].split("/")[-1]
            if pipe_cmd in _DANGEROUS_PIPE_CMDS:
                return False
            if ">" in part:
                return False
        return True

    is_readonly = _is_readonly_pipeline(command)

    # Helper: check one-time permission in session
    otp = (session or {}).get("one_time_permissions", {})

    # 2. Permission checks — block or request one-time permission
    #    (skip for read-only pipelines)
    if not is_readonly:
        perm_checks = [
            ("allow_file_delete", ["rm ", "unlink", "remove"]),
            ("allow_env_edit", [".env", "dotenv"]),
            ("allow_database_edit", ["_stats.json", "_knowledge.json", "trading_state.json"]),
        ]
        for perm, patterns in perm_checks:
            if not restrictions.get(perm, False):
                for pat in patterns:
                    if pat in cmd_lower:
                        # Check one-time permission
                        if otp.get(perm):
                            # Permission granted — allow and mark for consumption
                            result["_consume_permission"] = perm
                            break
                        else:
                            result["allowed"] = False
                            result["needs_permission"] = True
                            result["permission_type"] = perm
                            result["permission_reason"] = _perm_reasons.get(perm, perm)
                            result["block_reason"] = f"Blocked by setting: {perm}"
                            result["risk_level"] = "high"
                            return result
                if result.get("_consume_permission"):
                    break

        if not restrictions.get("allow_bot_restart", True):
            if any(x in cmd_lower for x in ["restart", "systemctl"]):
                if otp.get("allow_bot_restart"):
                    result["_consume_permission"] = "allow_bot_restart"
                else:
                    result["allowed"] = False
                    result["needs_permission"] = True
                    result["permission_type"] = "allow_bot_restart"
                    result["permission_reason"] = _perm_reasons["allow_bot_restart"]
                    result["block_reason"] = "Bot restart is disabled"
                    result["risk_level"] = "high"
                    return result

        if not restrictions.get("allow_git_push", True):
            if "git push" in cmd_lower:
                if otp.get("allow_git_push"):
                    result["_consume_permission"] = "allow_git_push"
                else:
                    result["allowed"] = False
                    result["needs_permission"] = True
                    result["permission_type"] = "allow_git_push"
                    result["permission_reason"] = _perm_reasons["allow_git_push"]
                    result["block_reason"] = "Git push is disabled"
                    result["risk_level"] = "high"
                    return result

    # 3. Protected paths (skip for read-only pipelines)
    if not is_readonly:
        for path in protected_paths:
            pattern = path.replace("*", ".*")
            if re.search(pattern, command, re.IGNORECASE):
                result["needs_confirmation"] = True
                result["affected_areas"].append(f"Protected path: {path}")
                result["risk_level"] = "high"

    # 4. Keyword-based protection (skip for read-only pipelines)
    if not is_readonly:
        for protection, keywords in _PROTECTION_KEYWORDS.items():
            if restrictions.get(protection, True):
                for kw in keywords:
                    if kw in cmd_lower:
                        result["needs_confirmation"] = True
                        result["affected_areas"].append(f"{protection}: {kw}")
                        if result["risk_level"] == "low":
                            result["risk_level"] = "medium"

    # 5. Always-confirm operations (skip for read-only pipelines)
    always_checks = [
        ("always_confirm_trade_open", ["open_position", "create_order"]),
        ("always_confirm_trade_close", ["close_position", "close_all", "cancel_order"]),
        ("always_confirm_autopilot_toggle", ["start_autopilot", "stop_autopilot", "autopilot"]),
        ("always_confirm_leverage_change", ["set_leverage"]),
    ]
    if not is_readonly:
        for setting, patterns in always_checks:
            if restrictions.get(setting, True):
                for pat in patterns:
                    if pat in cmd_lower:
                        result["needs_confirmation"] = True
                        result["warnings"].append(f"Critical: {pat}")
                        result["risk_level"] = "high"

    # 6. Risky patterns — warnings (skip for read-only pipelines)
    risky = [
        (r"rm\s+-", "File deletion"),
        (r"git\s+push\s+.*-f", "Force push"),
        (r"systemctl\s+(stop|restart)", "Service management"),
    ]
    if not is_readonly:
        for pat, warn in risky:
            if re.search(pat, cmd_lower):
                result["warnings"].append(warn)
                if result["risk_level"] == "low":
                    result["risk_level"] = "medium"

    return result


@router.get("/settings")
async def get_settings(request: Request):
    """Get admin security settings."""
    _require_auth(request)
    return _load_security_settings()


@router.put("/settings")
async def save_settings_api(request: Request):
    """Save admin security settings."""
    _require_auth(request)
    body = await request.json()
    _save_json(SETTINGS_FILE, body)
    _audit_log("admin", "SAVE_SETTINGS")
    return {"success": True}


@router.post("/check-command")
async def check_command_api(request: Request):
    """Check command security before execution."""
    _require_auth(request)
    token = _get_session_token(request)
    session = _admin_sessions.get(token)
    body = await request.json()
    command = body.get("command", "")
    mode = body.get("mode", "manual")
    result = _check_command_security(command, mode, session=session)
    # Remove internal key before returning
    result.pop("_consume_permission", None)
    return result


@router.post("/security/one-time-permission")
async def grant_one_time_permission(request: Request):
    """Grant a one-time permission for a blocked action."""
    _require_auth(request)
    token = _get_session_token(request)
    session = _admin_sessions.get(token)
    if not session:
        raise HTTPException(status_code=401, detail="No active session")

    body = await request.json()
    perm_type = body.get("permission_type", "")

    valid_perms = {
        "allow_file_delete", "allow_env_edit", "allow_database_edit",
        "allow_bot_restart", "allow_git_push",
    }
    if perm_type not in valid_perms:
        raise HTTPException(status_code=400, detail=f"Invalid permission: {perm_type}")

    # Store one-time permission in session
    if "one_time_permissions" not in session:
        session["one_time_permissions"] = {}
    session["one_time_permissions"][perm_type] = True

    _audit_log("admin", "ONE_TIME_PERMISSION", perm_type, "granted")
    logger.info(f"[SECURITY] One-time permission granted: {perm_type}")

    return {"success": True, "permission_type": perm_type}


# =============================================
# Claude Chat endpoints
# =============================================

def _load_knowledge_base() -> str:
    """Load all knowledge base files into prompt with priority ordering."""
    global _knowledge_loaded, _knowledge_last_loaded, _knowledge_cache

    files: dict[str, str] = {}
    if KNOWLEDGE_DIR.exists():
        for f in sorted(KNOWLEDGE_DIR.iterdir()):
            if f.is_file() and f.suffix in ALLOWED_EXTENSIONS:
                try:
                    content = f.read_text(errors="replace")[:50000]
                    files[f.name] = content
                except Exception:
                    pass

    _knowledge_cache = files
    _knowledge_loaded = True
    _knowledge_last_loaded = datetime.now().isoformat()

    # Build ordered output: priority files first, then the rest
    parts: list[str] = []
    added: set[str] = set()
    for name in _KNOWLEDGE_PRIORITY:
        if name in files:
            parts.append(f"## {name}\n{files[name]}")
            added.add(name)
    for name in sorted(files):
        if name not in added:
            parts.append(f"## {name}\n{files[name]}")
    return "\n\n".join(parts)


def _get_file_type(filename: str) -> str:
    """Classify knowledge file by name."""
    stem = Path(filename).stem.upper()
    for key, ftype in _FILE_TYPE_MAP.items():
        if key in stem:
            return ftype
    return "reference"


def _sanitize_filename(filename: str) -> str:
    """Sanitize filename for knowledge base."""
    return "".join(c for c in filename if c.isalnum() or c in "._- ")


def _get_chat_history(limit: int = 20, for_prompt: bool = False) -> list[dict]:
    """Get recent chat history.

    Args:
        limit: Max number of messages to return.
        for_prompt: If True, filter out code_result messages and trim content.
    """
    history_file = HISTORY_DIR / "current.json"
    history = _load_json(history_file, [])
    if not isinstance(history, list):
        return []
    if for_prompt:
        # Exclude code_result messages (can be very large) and trim content
        filtered = []
        for m in history:
            if m.get("type") == "code_result":
                continue
            if len(m.get("content", "")) > 1500:
                m = dict(m)
                m["content"] = m["content"][:1500] + "..."
            filtered.append(m)
        return filtered[-limit:]
    return history[-limit:]


def _save_chat_message(role: str, content: str, msg_type: str = "text") -> None:
    """Save message to chat history."""
    # Truncate oversized messages to prevent DOM rendering issues
    MAX_MSG_LEN = 5000
    if len(content) > MAX_MSG_LEN:
        content = content[:MAX_MSG_LEN] + f"\n\n... (truncated, {len(content)} chars total)"
    history_file = HISTORY_DIR / "current.json"
    history = _load_json(history_file, [])
    if not isinstance(history, list):
        history = []
    history.append({
        "role": role,
        "content": content,
        "type": msg_type,
        "timestamp": datetime.now().isoformat(),
    })
    # Keep last 200 messages
    if len(history) > 200:
        history = history[-200:]
    _save_json(history_file, history)


def _process_knowledge_updates(response: str) -> list[str]:
    """Process [UPDATE_KNOWLEDGE] tags in Claude response.

    Format: [UPDATE_KNOWLEDGE]filename|action|content[/UPDATE_KNOWLEDGE]
    Actions: append, remove
    Returns list of updated filenames.
    """
    updated: list[str] = []
    pattern = r"\[UPDATE_KNOWLEDGE\](.*?)\[/UPDATE_KNOWLEDGE\]"
    matches = re.findall(pattern, response, re.DOTALL)

    for match in matches:
        parts = match.split("|", 2)
        if len(parts) < 3:
            continue
        filename, action, content = parts[0].strip(), parts[1].strip(), parts[2].strip()

        # Sanitize filename
        safe_name = _sanitize_filename(filename)
        filepath = KNOWLEDGE_DIR / safe_name
        if not filepath.suffix or filepath.suffix not in ALLOWED_EXTENSIONS:
            continue

        try:
            if action == "append":
                existing = filepath.read_text(errors="replace") if filepath.exists() else ""
                filepath.write_text(existing.rstrip() + f"\n- {content}\n", encoding="utf-8")
                logger.info(f"[KNOWLEDGE] Appended to {safe_name}: {content[:80]}")
                updated.append(safe_name)
            elif action == "remove":
                if filepath.exists():
                    lines = filepath.read_text(errors="replace").splitlines()
                    content_lower = content.lower()
                    new_lines = [
                        ln for ln in lines
                        if content_lower not in ln.lower()
                    ]
                    filepath.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
                    logger.info(f"[KNOWLEDGE] Removed from {safe_name}: {content[:80]}")
                    updated.append(safe_name)
            _audit_log("admin", "KNOWLEDGE_UPDATE", f"{action} {safe_name}", content[:100])
        except Exception as e:
            logger.error(f"[KNOWLEDGE] Update failed {safe_name}: {e}")

    return updated


def _format_history_compact(
    history: list[dict], max_messages: int = 10
) -> str:
    """Format chat history compactly — only recent messages, truncated."""
    recent = history[-max_messages:] if history else []
    lines: list[str] = []
    for msg in recent:
        role = "U" if msg.get("role") == "user" else "A"
        content = msg.get("content", "")[:800]
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


def _get_mode_instructions(mode: str, auto_confirmed: bool = False) -> str:
    """Get mode-specific instructions for Chat+Code mode.

    Chat runs with --tools "" (no tools). Claude cannot execute
    commands itself — it must output [COMMAND_FOR_CODE] tags.
    """
    planner_rule = (
        "ТЫ — ПЛАНИРОВЩИК, НЕ ИСПОЛНИТЕЛЬ.\n"
        "У тебя НЕТ инструментов. Ты НЕ можешь читать файлы, "
        "выполнять bash, редактировать код.\n"
        "Единственный способ выполнить действие на сервере — "
        "сформировать [COMMAND_FOR_CODE]команда[/COMMAND_FOR_CODE].\n"
        "НИКОГДА не возвращай JSON tool_use формат. Только текстовый тег.\n"
    )
    if mode == "manual":
        return (
            "РЕЖИМ: MANUAL — СТРОГИЙ ПОРЯДОК!\n\n"
            f"{planner_rule}\n"
            "ШАГ 1:\n"
            "1. Напиши \"Понял задачу: [что будешь делать]\"\n"
            "2. Напиши \"Команда:\" и покажи команду в блоке кода\n"
            "3. Напиши \"Ожидаемый результат:\" и кратко опиши что получим\n"
            "4. Спроси \"Всё верно? Выполняю?\"\n"
            "5. СТОП — ЖДИ ОТВЕТА.\n\n"
            "ШАГ 2 (только после \"да/верно\"):\n"
            "        Сформируй [COMMAND_FOR_CODE]команда[/COMMAND_FOR_CODE]\n\n"
            "ПРИМЕР:\n"
            "User: \"покажи статус бота\"\n"
            "Assistant: \"Понял задачу: проверить статус сервиса AILA.\n\n"
            "Команда:\\n```bash\\nsudo systemctl status aila\\n```\\n\\n"
            "Ожидаемый результат: статус сервиса (active/inactive), "
            "время работы, PID, использование памяти.\\n\\n"
            "Всё верно? Выполняю?\"\n\n"
            "User: \"да\"\n"
            "Assistant: \"[COMMAND_FOR_CODE]sudo systemctl status aila"
            "[/COMMAND_FOR_CODE]\""
        )
    if mode == "scheduled":
        return (
            "РЕЖИМ: SCHEDULED (автозадача)\n\n"
            f"{planner_rule}\n"
            "Сразу формируй [COMMAND_FOR_CODE]. Без вопросов.\n"
            "Когда завершено — напиши \"Готово\" и краткий итог.\n"
            "НЕ предлагай улучшения, НЕ начинай новые задачи."
        )
    if auto_confirmed:
        return (
            "РЕЖИМ: AUTO (выполнение) — ЗАДАЧА ПОДТВЕРЖДЕНА\n\n"
            f"{planner_rule}\n"
            "ФОРМАТ ОТВЕТА — ТОЛЬКО ТАК:\n"
            "[COMMAND_FOR_CODE]твоя команда здесь[/COMMAND_FOR_CODE]\n\n"
            "ЗАПРЕЩЕНО:\n"
            "- JSON формат ({{\"name\": \"Bash\", \"arguments\": ...}})\n"
            "- Markdown блоки ```bash ... ```\n"
            "- tool_use, tool_result или любой другой формат\n"
            "ЕДИНСТВЕННЫЙ допустимый формат: [COMMAND_FOR_CODE]...[/COMMAND_FOR_CODE]\n\n"
            "После получения результата — напиши \"Готово\" и краткий итог.\n"
            "Фокусируйся ТОЛЬКО на текущей задаче.\n\n"
            "ПРИМЕР:\n"
            "[COMMAND_FOR_CODE]sudo systemctl status aila[/COMMAND_FOR_CODE]"
        )
    return (
        "РЕЖИМ: AUTO (новая задача) — СТРОГИЙ ПОРЯДОК!\n\n"
        f"{planner_rule}\n"
        "ШАГ 1 (этот ответ):\n"
        "1. Напиши \"Понял задачу: [краткое описание]\"\n"
        "2. Напиши \"Команда:\" и покажи команду в блоке кода\n"
        "3. Напиши \"Ожидаемый результат:\" и кратко опиши что получим\n"
        "4. Спроси \"Подтверждаете выполнение?\"\n"
        "5. СТОП — ЖДИ ОТВЕТА.\n\n"
        "ЗАПРЕЩЕНО: выполнять без подтверждения, давать анализ вместо плана, "
        "отвечать без показа команды.\n\n"
        "ПРИМЕР:\n"
        "User: \"проверь статус бота\"\n"
        "Assistant: \"Понял задачу: проверить статус сервиса AILA.\n\n"
        "Команда:\\n```bash\\nsudo systemctl status aila\\n```\\n\\n"
        "Ожидаемый результат: статус сервиса (active/inactive), "
        "время работы, PID, использование памяти.\\n\\n"
        "Подтверждаете выполнение?\""
    )


_AUTO_CONFIRM_WORDS = frozenset([
    "да", "верно", "подтверждаю", "выполняй", "делай", "погнали",
    "продолжай", "ок", "окей", "yes", "ok", "go", "confirm", "давай",
    "ага", "точно", "запускай", "поехали",
])


def _is_auto_confirmation(message: str) -> bool:
    """Check if message is a confirmation for auto mode."""
    if len(message) > 60:
        return False
    words = set(re.findall(r"\w+", message.lower()))
    return bool(words & _AUTO_CONFIRM_WORDS)


_TASK_COMPLETE_PATTERNS = (
    "готово", "выполнено", "завершено", "сделано",
    "done", "complete", "finished", "успешно",
    "задача выполнена", "изменения применены",
    "перезапустил", "закоммитил", "запушил",
)


def _is_task_complete(response: str) -> bool:
    """Check if Claude's response indicates task completion."""
    resp_lower = response.lower()
    return any(p in resp_lower for p in _TASK_COMPLETE_PATTERNS)


# --- First message prompts (full context) ---

def _build_first_prompt(
    message: str, knowledge: str, system_context: str,
    mode: str, chat_only: bool = False, auto_confirmed: bool = False,
) -> str:
    """First message — full context with knowledge base."""
    mode_instructions = _get_mode_instructions(mode, auto_confirmed)
    if chat_only:
        mode_instructions = "Только диалог, без команд на сервере."

    server_block = ""
    if not chat_only:
        server_block = """
# КОМАНДЫ НА СЕРВЕРЕ
Когда нужно что-то сделать на сервере (прочитать файл, изменить код, перезапустить сервис):
[COMMAND_FOR_CODE]конкретная команда с абсолютными путями /opt/aila/...[/COMMAND_FOR_CODE]
Сервер — реальный VPS Vultr Tokyo, полные права, sudo без пароля."""

    return f"""{system_context}

# КТО ТЫ
Ты — Claude (Opus 4.5), умный ИИ-ассистент от Anthropic.
Ты работаешь ТОЧНО ТАК ЖЕ как Claude в claude.ai:
- Умный и понимающий
- Краткий и полезный
- Понимаешь намерения, не добавляешь лишнего
- Отвечаешь структурированно когда нужно

# ПЛАТФОРМА
Ты помогаешь управлять AILA AI Trade — криптовалютный торговый бот на Bybit Futures.
Отвечай на русском.
{server_block}

# РЕЖИМ: {mode.upper() if not chat_only else 'CHAT-ONLY'}
{mode_instructions}

# ПРАВИЛА
- Делай ТОЛЬКО то, что просят. Не добавляй лишнего.
- Будь кратким — не пиши стены текста.
- Спрашивай если неясно — лучше уточнить чем сделать неправильно.

# УПРАВЛЕНИЕ ПРАВИЛАМИ
[UPDATE_KNOWLEDGE]RULES.md|append|текст[/UPDATE_KNOWLEDGE]
[UPDATE_KNOWLEDGE]RULES.md|remove|текст[/UPDATE_KNOWLEDGE]

# БАЗА ЗНАНИЙ (запомни на всю сессию)
{knowledge}

# ВАЖНО
Эта информация действует ВСЮ сессию. В следующих сообщениях будет только краткое напоминание кто ты, но базу знаний повторять не будем — ты её уже знаешь.

# ЗАПРОС
{message}"""


def _get_compact_context() -> str:
    """Compact context for follow-up prompts (~500 tokens instead of ~7000)."""
    return (
        "# КЛЮЧЕВОЙ КОНТЕКСТ\n"
        "AILA AI Trade — автономная торговая система на Bybit Futures. РЕАЛЬНЫЕ ДЕНЬГИ.\n"
        "Сервер: /opt/aila, ветка: claude/start-new-session-4XrKU\n\n"
        "Критичные команды:\n"
        "- Перезапуск: `sudo systemctl restart aila`\n"
        "- Логи: `sudo journalctl -u aila -f`\n"
        "- Статус: `sudo systemctl status aila`\n\n"
        "Безопасность: НЕ менять risk лимиты, Kelly параметры, min_confidence без запроса.\n"
        "RISK_GUARD — абсолютное VETO, защита капитала.\n\n"
        "Агенты: TRADER (тренды) + SNIPER (пробои) → REVIEWER → RISK_GUARD → Execute\n"
        "Все на Opus через Max подписку. Данные: /opt/aila/data/ai_trade/\n\n"
        "Для команд: [COMMAND_FOR_CODE]команда[/COMMAND_FOR_CODE]\n"
        "Для правил: [UPDATE_KNOWLEDGE]RULES.md|append|текст[/UPDATE_KNOWLEDGE]"
    )


def _build_followup_prompt(
    message: str, history_text: str, mode: str,
    chat_only: bool = False, auto_confirmed: bool = False,
) -> str:
    """Follow-up — compact context + history, no full knowledge base."""
    mode_instructions = _get_mode_instructions(mode, auto_confirmed)
    if chat_only:
        mode_instructions = "Только диалог, без команд."

    compact = "" if chat_only else f"\n{_get_compact_context()}\n"

    return f"""# НАПОМИНАНИЕ
Ты — Claude (Opus 4.5), ассистент для AILA AI Trade. Отвечай на русском, кратко, по делу.
Режим: {mode_instructions}
{compact}
# ИСТОРИЯ ДИАЛОГА
{history_text}

# НОВЫЙ ЗАПРОС
{message}"""


@router.post("/chat")
async def chat_message(request: Request):
    """Send message to Claude Chat."""
    _require_auth(request)
    body = await request.json()
    message = body.get("message", "").strip()
    mode = body.get("mode", "manual")  # manual | auto
    chat_only = body.get("chat_only", False)
    force_context = body.get("force_context", False)
    if not message:
        raise HTTPException(status_code=400, detail="Message required")

    # Save user message
    _save_chat_message("user", message)

    # Determine if this is a first message (needs full context)
    token = _get_session_token(request)
    session = _admin_sessions.get(token, {})
    context_sent = session.get("session_context_sent", False)
    is_first = not context_sent or force_context

    # Track auto_confirmed state in session
    auto_confirmed = session.get("auto_confirmed", False)
    if mode == "auto" and token in _admin_sessions:
        if _is_auto_confirmation(message):
            auto_confirmed = True
            _admin_sessions[token]["auto_confirmed"] = True
            logger.info(
                "[ADMIN_CHAT] Auto confirmed by user: %s", message[:50],
            )
        # New task (long message) — reset auto_confirmed, require new confirmation
        elif len(message) > 60:
            auto_confirmed = False
            _admin_sessions[token]["auto_confirmed"] = False
            logger.info("[ADMIN_CHAT] Auto mode — new task, awaiting confirmation")

    _chat_start = time.time()

    if is_first:
        # FIRST message — full prompt with knowledge base
        knowledge = _load_knowledge_base()
        context_file = CHAT_DIR / "context.md"
        system_context = ""
        if context_file.exists():
            system_context = context_file.read_text(errors="replace")[:10000]

        prompt = _build_first_prompt(
            message, knowledge, system_context, mode, chat_only, auto_confirmed,
        )

        # Mark context as sent
        if token in _admin_sessions:
            _admin_sessions[token]["session_context_sent"] = True

        prompt_type = "first (full context)"
    else:
        # FOLLOW-UP — minimal prompt, no knowledge
        history = _get_chat_history(10, for_prompt=True)
        history_text = _format_history_compact(history, 10)

        prompt = _build_followup_prompt(
            message, history_text, mode, chat_only, auto_confirmed,
        )

        prompt_type = "follow-up (minimal)"

    try:
        # Run claude CLI for Chat with Opus
        claude_env = _get_claude_env()
        prompt_len = len(prompt)
        est_tokens = prompt_len // 4
        logger.info(
            f"[ADMIN_CHAT] {prompt_type} | {prompt_len} chars (~{est_tokens} tokens)"
        )
        result = await asyncio.to_thread(
            subprocess.run,
            [
                "claude", "--print", "--model", CLAUDE_MODEL,
                "--tools", "",  # No tools — planner only
                "--", prompt,
            ],
            cwd="/opt/aila",
            capture_output=True,
            text=True,
            env=claude_env,
        )
        elapsed = time.time() - _chat_start
        response = result.stdout.strip() if result.stdout else ""
        stderr = result.stderr.strip() if result.stderr else ""

        # Check for rate limit in stderr only (stdout contains Claude response text)
        if _is_rate_limit_error(stderr):
            rl_result = await _handle_rate_limit("chat", stderr[:300])
            _increment_admin_stats("chat")
            return rl_result

        if not response and stderr:
            response = f"Ошибка Claude: {stderr[:500]}"
            logger.error(f"[ADMIN_CHAT] stderr: {stderr[:300]} ({elapsed:.1f}s)")
        elif not response:
            response = f"Ошибка: нет ответа от Claude (code={result.returncode})"
            logger.error(f"[ADMIN_CHAT] Empty output, returncode={result.returncode} ({elapsed:.1f}s)")
        else:
            logger.info(f"[ADMIN_CHAT] Response {len(response)} chars in {elapsed:.1f}s")
    except FileNotFoundError:
        response = "Ошибка: claude CLI не найден. Проверьте установку."
        logger.error("[ADMIN_CHAT] claude CLI not found in PATH")
    except Exception as e:
        response = f"Ошибка: {str(e)}"
        logger.error(f"[ADMIN_CHAT] Exception: {e}")

    # Track request
    _increment_admin_stats("chat")

    # Process knowledge update tags from response
    knowledge_updated = _process_knowledge_updates(response)
    # Strip update tags from displayed response
    clean_response = re.sub(
        r"\[UPDATE_KNOWLEDGE\].*?\[/UPDATE_KNOWLEDGE\]", "", response
    ).strip()
    if clean_response:
        response = clean_response

    # Fix tool_use JSON → [COMMAND_FOR_CODE] (Claude sometimes ignores --tools "")
    if not chat_only:
        response = _fix_tool_use_response(response)

    # In chat-only mode, never parse commands
    has_command = False if chat_only else "[COMMAND_FOR_CODE]" in response

    # Auto mode: reset auto_confirmed when task is complete (no more commands)
    if mode == "auto" and auto_confirmed and not has_command and _is_task_complete(response):
        auto_confirmed = False
        if token in _admin_sessions:
            _admin_sessions[token]["auto_confirmed"] = False
        logger.info("[ADMIN_CHAT] Task complete — auto_confirmed reset to False")

    # Save assistant response
    _save_chat_message("assistant", response, "command" if has_command else "text")
    _audit_log("admin", "CHAT", message[:100], response[:200])

    return {
        "response": response,
        "has_command": has_command,
        "chat_only": chat_only,
        "knowledge_updated": knowledge_updated,
        "auto_confirmed": auto_confirmed if mode == "auto" else None,
        "timestamp": datetime.now().isoformat(),
    }


@router.get("/chat/history")
async def get_chat_history_api(request: Request):
    """Get chat history."""
    _require_auth(request)
    history = _get_chat_history(50)
    return {"history": history}


@router.post("/chat/reset-auto")
async def reset_auto_mode(request: Request):
    """Reset auto_confirmed state."""
    _require_auth(request)
    token = _get_session_token(request)
    if token and token in _admin_sessions:
        _admin_sessions[token]["auto_confirmed"] = False
    return {"success": True}


@router.post("/chat/clear")
async def clear_chat(request: Request):
    """Clear chat history."""
    _require_auth(request)
    history_file = HISTORY_DIR / "current.json"
    _save_json(history_file, [])
    _audit_log("admin", "CLEAR_CHAT")
    return {"success": True}


# =============================================
# Claude Code execution
# =============================================

@router.post("/code/execute")
async def execute_code(request: Request):
    """Execute command via Claude Code CLI."""
    _require_auth(request)
    global _running_process

    token = _get_session_token(request)
    session = _admin_sessions.get(token)

    body = await request.json()
    command = body.get("command", "").strip()

    if not command:
        raise HTTPException(status_code=400, detail="Command required")

    # Security check via settings-based checker
    sec_check = _check_command_security(command, "manual", session=session)
    if not sec_check["allowed"]:
        if sec_check.get("needs_permission"):
            # Return 200 with needs_permission so frontend can show dialog
            return {
                "needs_permission": True,
                "permission_type": sec_check["permission_type"],
                "permission_reason": sec_check["permission_reason"],
                "command": command,
            }
        _audit_log("admin", "BLOCKED_COMMAND", command, sec_check["block_reason"] or "")
        raise HTTPException(status_code=403, detail=sec_check["block_reason"])

    # Consume one-time permission if used
    consumed = sec_check.pop("_consume_permission", None)
    if consumed and session and "one_time_permissions" in session:
        session["one_time_permissions"].pop(consumed, None)
        _audit_log("admin", "ONE_TIME_PERMISSION_USED", consumed, command[:80])

    async with _process_lock:
        if _running_process and _running_process.returncode is None:
            raise HTTPException(status_code=409, detail="Another command is running")

        _audit_log("admin", "EXECUTE", command)

        try:
            cmd = [
                "claude", "--print",
                "--model", CLAUDE_MODEL,
                "--no-session-persistence",
                "--dangerously-skip-permissions",
                command,
            ]

            claude_env = _get_claude_env()
            _code_start = time.time()
            logger.info(f"[ADMIN_CODE] Executing: {command[:100]}")
            result = await asyncio.to_thread(
                subprocess.run,
                cmd,
                cwd="/opt/aila",
                capture_output=True,
                text=True,
                env=claude_env,
            )

            output = result.stdout.strip() if result.stdout else ""
            error = result.stderr.strip() if result.stderr else ""

            # Check for rate limit in stderr only
            if _is_rate_limit_error(error):
                rl_result = await _handle_rate_limit("code", error[:300])
                _increment_admin_stats("code")
                return rl_result

            _code_elapsed = time.time() - _code_start
            if not output and error:
                logger.error(f"[ADMIN_CODE] stderr: {error[:300]} ({_code_elapsed:.1f}s)")
            response = output or error or "Command completed (no output)"
            logger.info(f"[ADMIN_CODE] Done {len(response)} chars in {_code_elapsed:.1f}s")

            # Track and save
            _increment_admin_stats("code")
            _save_chat_message("code_result", response, "code_result")
            _audit_log("admin", "EXECUTE_RESULT", command[:50], response[:200])

            _record_command_history(
                command=command,
                source="manual",
                risk_level=sec_check.get("risk_level", "low"),
                command_type=_detect_command_type(command),
                status="success" if result.returncode == 0 else "error",
                duration=_code_elapsed,
                output_preview=response[:200],
                error=error[:200] if result.returncode != 0 and error else None,
            )

            return {
                "success": result.returncode == 0,
                "output": response,
                "return_code": result.returncode,
                "timestamp": datetime.now().isoformat(),
            }

        except Exception as e:
            return {
                "success": False,
                "output": f"Error: {str(e)}",
                "return_code": -1,
                "timestamp": datetime.now().isoformat(),
            }


def _detect_claude_state(line: str) -> str:
    """Detect Claude Code execution state from output line."""
    lower = line.lower().strip()
    if not lower:
        return "executing"
    # Error patterns
    if any(p in lower for p in ["error", "traceback", "exception", "failed", "fatal"]):
        return "error"
    # Thinking patterns (Claude analyzing/planning)
    if any(p in lower for p in [
        "thinking", "analyzing", "planning", "considering",
        "reading", "searching", "looking", "exploring",
    ]):
        return "thinking"
    return "executing"


@router.post("/code/execute-stream")
async def execute_code_stream(request: Request):
    """Execute command via Claude Code CLI with SSE streaming."""
    _require_auth(request)
    global _running_process

    token = _get_session_token(request)
    session = _admin_sessions.get(token)

    body = await request.json()
    command = body.get("command", "").strip()

    if not command:
        raise HTTPException(status_code=400, detail="Command required")

    # Security check — block dangerous commands server-side
    sec_check = _check_command_security(command, "manual", session=session)
    if not sec_check["allowed"]:
        if sec_check.get("needs_permission"):
            # Return JSON (not SSE) with needs_permission so frontend shows dialog
            return JSONResponse({
                "needs_permission": True,
                "permission_type": sec_check["permission_type"],
                "permission_reason": sec_check["permission_reason"],
                "command": command,
            })
        _audit_log("admin", "BLOCKED_COMMAND", command, sec_check["block_reason"] or "")
        raise HTTPException(status_code=403, detail=sec_check["block_reason"])

    # Consume one-time permission if used
    consumed = sec_check.pop("_consume_permission", None)
    if consumed and session and "one_time_permissions" in session:
        session["one_time_permissions"].pop(consumed, None)
        _audit_log("admin", "ONE_TIME_PERMISSION_USED", consumed, command[:80])

    _audit_log("admin", "EXECUTE_STREAM", command)
    _stream_cmd_type = _detect_command_type(command)
    _stream_risk = sec_check.get("risk_level", "low")
    _stream_source = body.get("source", "manual")

    async def event_stream():
        """Generate SSE events from Claude Code subprocess."""
        global _running_process
        _stream_start = time.time()

        async with _process_lock:
            if _running_process and _running_process.returncode is None:
                yield f"data: {json.dumps({'status': 'error', 'message': 'Another command is running'})}\n\n"
                return

            try:
                claude_env = _get_claude_env()
                logger.info(f"[ADMIN_CODE_STREAM] Executing: {command[:100]}")

                proc = await asyncio.create_subprocess_exec(
                    "claude", "--print",
                    "--model", CLAUDE_MODEL,
                    "--no-session-persistence",
                    "--dangerously-skip-permissions",
                    command,
                    cwd="/opt/aila",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=claude_env,
                )
                _running_process = proc

            except FileNotFoundError:
                yield f"data: {json.dumps({'status': 'error', 'message': 'claude CLI not found'})}\n\n"
                return
            except Exception as e:
                yield f"data: {json.dumps({'status': 'error', 'message': str(e)})}\n\n"
                return

        # Get timeout from settings
        settings = _load_json(DATA_DIR / "settings.json", _DEFAULT_SETTINGS)
        cmd_timeout = settings.get("restrictions", {}).get(
            "command_timeout_seconds", COMMAND_TIMEOUT_DEFAULT
        )
        _timed_out = False

        # Send initial thinking state
        yield f"data: {json.dumps({'status': 'thinking', 'message': 'Claude is thinking...'})}\n\n"

        output_lines: list[str] = []
        error_lines: list[str] = []

        async def read_stderr():
            """Read stderr in background."""
            assert proc.stderr is not None
            async for raw_line in proc.stderr:
                line = raw_line.decode("utf-8", errors="replace").rstrip()
                if line:
                    error_lines.append(line)

        async def timeout_watcher():
            """Kill process if it exceeds timeout."""
            nonlocal _timed_out
            await asyncio.sleep(cmd_timeout)
            if proc.returncode is None:
                _timed_out = True
                logger.warning(f"[ADMIN_CODE] Timeout ({cmd_timeout}s): {command[:80]}")
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    proc.kill()

        stderr_task = asyncio.create_task(read_stderr())
        timeout_task = asyncio.create_task(timeout_watcher())

        try:
            assert proc.stdout is not None
            async for raw_line in proc.stdout:
                line = raw_line.decode("utf-8", errors="replace").rstrip()
                if not line:
                    continue
                output_lines.append(line)
                state = _detect_claude_state(line)
                yield f"data: {json.dumps({'status': state, 'output': line})}\n\n"

        except asyncio.CancelledError:
            # Client disconnected — kill process
            timeout_task.cancel()
            if proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=3)
                except asyncio.TimeoutError:
                    proc.kill()
            raise

        timeout_task.cancel()
        await stderr_task
        await proc.wait()

        _running_process = None
        full_output = "\n".join(output_lines)
        full_error = "\n".join(error_lines)

        # Handle timeout
        if _timed_out:
            _increment_admin_stats("code")
            timeout_msg = f"Command timed out after {cmd_timeout}s"
            _save_chat_message("code_result", timeout_msg, "code_result")
            _audit_log("admin", "EXECUTE_STREAM_TIMEOUT", command[:50])
            _record_command_history(
                command=command, source=_stream_source, risk_level=_stream_risk,
                command_type=_stream_cmd_type, status="timeout",
                duration=cmd_timeout, output_preview=full_output[:200],
            )
            yield f"data: {json.dumps({'status': 'error', 'message': timeout_msg, 'timeout': True})}\n\n"
            return

        # Check for rate limit in stderr only
        if _is_rate_limit_error(full_error):
            await _handle_rate_limit("code_stream", full_error[:300])
            _increment_admin_stats("code")
            yield f"data: {json.dumps({'status': 'error', 'message': 'Rate limit hit', 'rate_limit': True})}\n\n"
            return

        response = full_output or full_error or "Command completed (no output)"
        success = proc.returncode == 0

        _increment_admin_stats("code")
        _save_chat_message("code_result", response, "code_result")
        _audit_log("admin", "EXECUTE_STREAM_RESULT", command[:50], response[:200])

        _record_command_history(
            command=command,
            source=_stream_source,
            risk_level=_stream_risk,
            command_type=_stream_cmd_type,
            status="success" if success else "error",
            duration=time.time() - _stream_start,
            output_preview=response[:200],
            error=full_error[:200] if not success and full_error else None,
        )

        yield f"data: {json.dumps({'status': 'done' if success else 'error', 'message': 'Done' if success else 'Error', 'output': response[-500:] if len(response) > 500 else '', 'return_code': proc.returncode})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/code/stop")
async def stop_code(request: Request):
    """Stop running Claude Code process."""
    _require_auth(request)
    global _running_process

    if _running_process and _running_process.returncode is None:
        # Graceful: SIGTERM → wait → SIGKILL
        _running_process.terminate()
        try:
            await asyncio.wait_for(_running_process.wait(), timeout=5)
        except asyncio.TimeoutError:
            _running_process.kill()
            await _running_process.wait()
        _running_process = None
        _audit_log("admin", "STOP_CODE")
        return {"success": True, "message": "Process terminated"}

    return {"success": True, "message": "No running process"}


@router.get("/code/status")
async def code_status(request: Request):
    """Get Claude Code execution status."""
    _require_auth(request)
    running = _running_process is not None and _running_process.returncode is None
    return {"running": running}


@router.post("/confirm-code")
async def confirm_send_to_code(request: Request):
    """Execute confirmed command in Code with streaming."""
    _require_auth(request)
    body = await request.json()
    command = body.get("command", "").strip()

    if not command:
        raise HTTPException(status_code=400, detail="Command required")

    _audit_log("admin", "CONFIRM_CODE", command[:100])

    _confirm_cmd_type = _detect_command_type(command)

    # Reuse streaming execution
    async def confirmed_stream():
        """Stream confirmed command execution."""
        global _running_process
        _confirm_start = time.time()

        # Security check (final)
        check = _check_command_security(command, "manual")
        if not check["allowed"]:
            yield f"data: {json.dumps({'status': 'error', 'message': check['block_reason']})}\n\n"
            return

        async with _process_lock:
            if _running_process and _running_process.returncode is None:
                yield f"data: {json.dumps({'status': 'error', 'message': 'Another command is running'})}\n\n"
                return

            try:
                claude_env = _get_claude_env()
                proc = await asyncio.create_subprocess_exec(
                    "claude", "--print", "--model", CLAUDE_MODEL,
                    "--no-session-persistence", "--dangerously-skip-permissions",
                    command, cwd="/opt/aila",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=claude_env,
                )
                _running_process = proc
            except Exception as e:
                yield f"data: {json.dumps({'status': 'error', 'message': str(e)})}\n\n"
                return

        yield f"data: {json.dumps({'status': 'thinking', 'message': 'Claude Code thinking...'})}\n\n"

        output_lines: list[str] = []
        error_lines: list[str] = []

        async def read_stderr():
            assert proc.stderr is not None
            async for raw_line in proc.stderr:
                line = raw_line.decode("utf-8", errors="replace").rstrip()
                if line:
                    error_lines.append(line)

        stderr_task = asyncio.create_task(read_stderr())
        try:
            assert proc.stdout is not None
            async for raw_line in proc.stdout:
                line = raw_line.decode("utf-8", errors="replace").rstrip()
                if not line:
                    continue
                output_lines.append(line)
                state = _detect_claude_state(line)
                yield f"data: {json.dumps({'status': state, 'output': line})}\n\n"
        except asyncio.CancelledError:
            if proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=3)
                except asyncio.TimeoutError:
                    proc.kill()
            raise

        await stderr_task
        await proc.wait()
        _running_process = None

        full_output = "\n".join(output_lines)
        full_error = "\n".join(error_lines)

        if _is_rate_limit_error(full_error):
            await _handle_rate_limit("confirm_code", full_error[:300])
            _increment_admin_stats("code")
            yield f"data: {json.dumps({'status': 'error', 'message': 'Rate limit', 'rate_limit': True})}\n\n"
            return

        response = full_output or full_error or "Command completed (no output)"
        success = proc.returncode == 0
        _increment_admin_stats("code")
        _save_chat_message("code_result", response, "code_result")
        _audit_log("admin", "CONFIRM_CODE_RESULT", command[:50], response[:200])

        _record_command_history(
            command=command,
            source="manual",
            risk_level=check.get("risk_level", "low"),
            command_type=_confirm_cmd_type,
            status="success" if success else "error",
            duration=time.time() - _confirm_start,
            output_preview=response[:200],
            error=full_error[:200] if not success and full_error else None,
        )

        yield f"data: {json.dumps({'status': 'done' if success else 'error', 'message': 'Done' if success else 'Error', 'output': response[-500:] if len(response) > 500 else '', 'return_code': proc.returncode})}\n\n"

    return StreamingResponse(
        confirmed_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/confirm-chat")
async def confirm_send_to_chat(request: Request):
    """Send Code result to Chat for analysis."""
    _require_auth(request)
    body = await request.json()
    code_result = body.get("code_result", "").strip()

    if not code_result:
        raise HTTPException(status_code=400, detail="Code result required")

    _audit_log("admin", "CONFIRM_CHAT", code_result[:100])

    # Send to Chat for analysis (reuses chat endpoint logic)
    msg = (
        f"Результат выполнения команды:\n\n"
        f"{code_result[:3000]}\n\n"
        f"Проанализируй результат и сообщи пользователю."
    )

    # Save internal message
    _save_chat_message("user", msg, "internal")

    knowledge = _load_knowledge_base()
    history = _get_chat_history(10, for_prompt=True)
    history_text = "\n".join(
        f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['content']}"
        for m in history[:-1]
    )

    context_file = CHAT_DIR / "context.md"
    system_context = ""
    if context_file.exists():
        system_context = context_file.read_text(errors="replace")[:10000]

    prompt = f"""{system_context}

# БАЗА ЗНАНИЙ
{knowledge}

# ТВОЯ РОЛЬ
Ты — Claude Chat, интеллектуальный помощник AILA AI Trade бота.
Проанализируй результат выполнения команды Claude Code.

# ИСТОРИЯ ЧАТА
{history_text}

# РЕЗУЛЬТАТ КОМАНДЫ
{msg}

# ИНСТРУКЦИИ
- Отвечай на русском.
- Если нужны дополнительные действия: [COMMAND_FOR_CODE]команда[/COMMAND_FOR_CODE]
- Используй АБСОЛЮТНЫЕ пути от /opt/aila/."""

    try:
        claude_env = _get_claude_env()
        result = await asyncio.to_thread(
            subprocess.run,
            ["claude", "--print", "--model", CLAUDE_MODEL, prompt],
            cwd="/opt/aila", capture_output=True, text=True, env=claude_env,
        )
        response = result.stdout.strip() if result.stdout else ""
        stderr = result.stderr.strip() if result.stderr else ""

        if _is_rate_limit_error(stderr):
            rl_result = await _handle_rate_limit("confirm_chat", stderr[:300])
            _increment_admin_stats("chat")
            return rl_result

        if not response:
            response = f"Ошибка: {stderr[:500]}" if stderr else "No response"
    except Exception as e:
        response = f"Error: {str(e)}"

    _increment_admin_stats("chat")

    # Process knowledge updates
    knowledge_updated = _process_knowledge_updates(response)
    clean_response = re.sub(
        r"\[UPDATE_KNOWLEDGE\].*?\[/UPDATE_KNOWLEDGE\]", "", response
    ).strip()
    if clean_response:
        response = clean_response

    has_command = "[COMMAND_FOR_CODE]" in response
    _save_chat_message("assistant", response, "command" if has_command else "text")

    return {
        "response": response,
        "has_command": has_command,
        "knowledge_updated": knowledge_updated,
        "timestamp": datetime.now().isoformat(),
    }


# =============================================
# Commands management
# =============================================

def _get_commands() -> list[dict]:
    """Get all commands including defaults."""
    # Default commands
    defaults = [
        # Monitoring (8)
        {"id": "log_trader", "name": "Логи TRADER", "icon": "fa-file-lines", "category": "monitoring",
         "command": "Проверь логи TRADER на ошибки и покажи последние 50 строк"},
        {"id": "log_sniper", "name": "Логи SNIPER", "icon": "fa-crosshairs", "category": "monitoring",
         "command": "Проверь логи SNIPER на ошибки"},
        {"id": "errors", "name": "Ошибки", "icon": "fa-triangle-exclamation", "category": "monitoring",
         "command": "Найди все ошибки в логах за последний час и предложи исправления"},
        {"id": "status", "name": "Быстрый статус", "icon": "fa-circle-info", "category": "monitoring",
         "command": "Покажи полный статус системы: автопилот, позиции, баланс"},
        {"id": "sys_check", "name": "Full System Check", "icon": "fa-rocket", "category": "monitoring",
         "command": "Запусти полную диагностику системы: python3 /opt/aila/scripts/full_system_check.py и покажи результат. Если есть проблемы — предложи решения. Если автопилот не запущен и все системы OK — спроси нужно ли запустить."},
        {"id": "balance", "name": "Баланс", "icon": "fa-wallet", "category": "monitoring",
         "command": "Покажи текущий баланс Bybit USDT, открытые позиции и активные ордера. Кратко."},
        {"id": "oauth_status", "name": "OAuth", "icon": "fa-key", "category": "monitoring",
         "command": "Покажи статус OAuth токена и подписки Max: cat /opt/aila/.claude/.credentials.json | python3 -c \"import json,sys; from datetime import datetime,timezone; d=json.load(sys.stdin); exp=datetime.fromisoformat(d['expiresAt'].replace('Z','+00:00')); rem=(exp-datetime.now(timezone.utc)).total_seconds()/3600; print(f'OAuth: {rem:.1f}h remaining')\" && cat /opt/aila/data/subscription.json | python3 -c \"import json,sys; from datetime import datetime,date; d=json.load(sys.stdin); rem=(datetime.strptime(d['next_billing_date'],'%Y-%m-%d').date()-date.today()).days; print(f'Max подписка: {rem} дней до продления ({d[\"next_billing_date\"]})')\""},
        {"id": "market", "name": "Рынок", "icon": "fa-chart-line", "category": "monitoring",
         "command": "Покажи текущую рыночную ситуацию: цена BTC и ETH, тренд за 24ч, общий sentiment. Кратко в 5-7 строк."},
        # Management (6)
        {"id": "restart", "name": "Рестарт", "icon": "fa-rotate", "category": "management",
         "command": "Перезапусти бота и проверь что всё работает"},
        {"id": "stop", "name": "Стоп", "icon": "fa-stop", "category": "management",
         "command": "Останови автопилот безопасно"},
        {"id": "start", "name": "Старт", "icon": "fa-play", "category": "management",
         "command": "Запусти автопилот и проверь статус"},
        {"id": "config", "name": "Настройки", "icon": "fa-gear", "category": "management",
         "command": "Покажи текущие настройки TRADER и SNIPER"},
        {"id": "autopilot_start", "name": "Автопилот", "icon": "fa-rocket", "category": "management",
         "command": "Проверь готовность системы и запусти автопилот если он не запущен. Проверь: сервис active, OAuth OK, баланс достаточный. Если всё OK — запусти. Если нет — покажи что нужно исправить."},
        {"id": "autopilot_pause", "name": "Пауза", "icon": "fa-pause", "category": "management",
         "command": "Поставь автопилот на паузу. Не закрывай открытые позиции, просто останови новые сделки."},
        # Data (3)
        {"id": "backup", "name": "Бэкап", "icon": "fa-database", "category": "data",
         "command": "Сделай полный бэкап данных AI Trade"},
        {"id": "pnl", "name": "PnL Отчёт", "icon": "fa-chart-bar", "category": "data",
         "command": "Покажи детальный PnL за сегодня"},
        {"id": "stats", "name": "Статистика", "icon": "fa-chart-pie", "category": "data",
         "command": "Покажи полную статистику торговли"},
    ]

    # Load custom commands
    custom = _load_json(DATA_DIR / "commands.json", [])
    if not isinstance(custom, list):
        custom = []

    return defaults + custom


@router.get("/commands")
async def get_commands(request: Request):
    """Get all commands."""
    _require_auth(request)
    return {"commands": _get_commands()}


@router.post("/commands")
async def create_command(request: Request):
    """Create a custom command."""
    _require_auth(request)
    body = await request.json()

    cmd = {
        "id": f"custom_{int(time.time())}",
        "name": body.get("name", ""),
        "icon": body.get("icon", "fa-terminal"),
        "category": body.get("category", "custom"),
        "command": body.get("command", ""),
        "custom": True,
        "schedule": body.get("schedule"),  # {type: "once"|"recurring", value: int, unit: "min"|"hour"|"day"}
    }

    if not cmd["name"] or not cmd["command"]:
        raise HTTPException(status_code=400, detail="Name and command required")

    custom = _load_json(DATA_DIR / "commands.json", [])
    if not isinstance(custom, list):
        custom = []
    custom.append(cmd)
    _save_json(DATA_DIR / "commands.json", custom)

    # If scheduled, add to scheduled tasks
    if cmd.get("schedule"):
        await _add_scheduled_task(cmd)

    _audit_log("admin", "CREATE_COMMAND", cmd["name"])
    return {"success": True, "command": cmd}


@router.put("/commands/{cmd_id}")
async def update_command(cmd_id: str, request: Request):
    """Update a command."""
    _require_auth(request)
    body = await request.json()

    # Update defaults or custom
    custom = _load_json(DATA_DIR / "commands.json", [])
    if not isinstance(custom, list):
        custom = []

    found = False
    for c in custom:
        if c["id"] == cmd_id:
            c["name"] = body.get("name", c["name"])
            c["icon"] = body.get("icon", c["icon"])
            c["command"] = body.get("command", c["command"])
            c["category"] = body.get("category", c["category"])
            c["schedule"] = body.get("schedule", c.get("schedule"))
            found = True
            break

    if not found:
        raise HTTPException(status_code=404, detail="Command not found (default commands can't be edited)")

    _save_json(DATA_DIR / "commands.json", custom)
    _audit_log("admin", "UPDATE_COMMAND", cmd_id)
    return {"success": True}


@router.delete("/commands/{cmd_id}")
async def delete_command(cmd_id: str, request: Request):
    """Delete a custom command."""
    _require_auth(request)

    custom = _load_json(DATA_DIR / "commands.json", [])
    if not isinstance(custom, list):
        custom = []

    custom = [c for c in custom if c["id"] != cmd_id]
    _save_json(DATA_DIR / "commands.json", custom)

    # Also remove from scheduled
    scheduled = _load_json(DATA_DIR / "scheduled.json", [])
    if isinstance(scheduled, list):
        scheduled = [s for s in scheduled if s.get("command_id") != cmd_id]
        _save_json(DATA_DIR / "scheduled.json", scheduled)

    _audit_log("admin", "DELETE_COMMAND", cmd_id)
    return {"success": True}


# =============================================
# Command history
# =============================================

@router.get("/commands/history")
async def get_command_history(request: Request, limit: int = 50):
    """Get command execution history."""
    _require_auth(request)
    history = _load_json(COMMAND_HISTORY_FILE, [])
    if not isinstance(history, list):
        history = []
    return {"history": history[:min(limit, COMMAND_HISTORY_MAX)]}


@router.delete("/commands/history")
async def clear_command_history(request: Request):
    """Clear command execution history."""
    _require_auth(request)
    _save_json(COMMAND_HISTORY_FILE, [])
    _audit_log("admin", "CLEAR_COMMAND_HISTORY")
    return {"success": True}


# =============================================
# Scheduled tasks
# =============================================

async def _add_scheduled_task(cmd: dict) -> None:
    """Add a scheduled task from command."""
    schedule = cmd.get("schedule", {})
    if not schedule:
        return

    stype = schedule.get("type", "once")
    value = int(schedule.get("value", 1))
    unit = schedule.get("unit", "min")

    # Calculate next run
    multipliers = {"min": 60, "hour": 3600, "day": 86400}
    interval = value * multipliers.get(unit, 60)
    next_run = time.time() + interval

    task = {
        "id": f"task_{int(time.time())}",
        "command_id": cmd["id"],
        "name": cmd["name"],
        "command": cmd["command"],
        "type": stype,
        "interval": interval,
        "next_run": next_run,
        "status": "active",
        "created": datetime.now().isoformat(),
        "last_run": None,
        "last_result": None,
    }

    scheduled = _load_json(DATA_DIR / "scheduled.json", [])
    if not isinstance(scheduled, list):
        scheduled = []
    scheduled.append(task)
    _save_json(DATA_DIR / "scheduled.json", scheduled)


@router.get("/scheduled")
async def get_scheduled(request: Request):
    """Get all scheduled tasks."""
    _require_auth(request)
    scheduled = _load_json(DATA_DIR / "scheduled.json", [])
    return {"tasks": scheduled if isinstance(scheduled, list) else []}


@router.post("/scheduled")
async def create_scheduled(request: Request):
    """Create a scheduled task."""
    _require_auth(request)
    body = await request.json()

    stype = body.get("type", "once")
    value = int(body.get("value", 1))
    unit = body.get("unit", "min")

    multipliers = {"min": 60, "hour": 3600, "day": 86400}
    interval = value * multipliers.get(unit, 60)

    task = {
        "id": f"task_{int(time.time())}",
        "name": body.get("name", ""),
        "command": body.get("command", ""),
        "type": stype,
        "interval": interval,
        "next_run": time.time() + interval,
        "status": "active",
        "created": datetime.now().isoformat(),
        "last_run": None,
        "last_result": None,
    }

    scheduled = _load_json(DATA_DIR / "scheduled.json", [])
    if not isinstance(scheduled, list):
        scheduled = []
    scheduled.append(task)
    _save_json(DATA_DIR / "scheduled.json", scheduled)

    _audit_log("admin", "CREATE_SCHEDULED", task["name"])
    return {"success": True, "task": task}


@router.put("/scheduled/{task_id}")
async def update_scheduled(task_id: str, request: Request):
    """Update a scheduled task."""
    _require_auth(request)
    body = await request.json()

    scheduled = _load_json(DATA_DIR / "scheduled.json", [])
    if not isinstance(scheduled, list):
        raise HTTPException(status_code=404, detail="Task not found")

    for t in scheduled:
        if t["id"] == task_id:
            for key in ["name", "command", "type", "interval", "status"]:
                if key in body:
                    t[key] = body[key]
            if "value" in body and "unit" in body:
                multipliers = {"min": 60, "hour": 3600, "day": 86400}
                t["interval"] = int(body["value"]) * multipliers.get(body["unit"], 60)
            _save_json(DATA_DIR / "scheduled.json", scheduled)
            return {"success": True}

    raise HTTPException(status_code=404, detail="Task not found")


@router.delete("/scheduled/{task_id}")
async def delete_scheduled(task_id: str, request: Request):
    """Delete a scheduled task."""
    _require_auth(request)
    scheduled = _load_json(DATA_DIR / "scheduled.json", [])
    if isinstance(scheduled, list):
        scheduled = [t for t in scheduled if t["id"] != task_id]
        _save_json(DATA_DIR / "scheduled.json", scheduled)
    _audit_log("admin", "DELETE_SCHEDULED", task_id)
    return {"success": True}


@router.post("/scheduled/{task_id}/pause")
async def pause_scheduled(task_id: str, request: Request):
    """Toggle pause/resume a scheduled task."""
    _require_auth(request)
    scheduled = _load_json(DATA_DIR / "scheduled.json", [])
    if not isinstance(scheduled, list):
        raise HTTPException(status_code=404, detail="Task not found")

    for t in scheduled:
        if t["id"] == task_id:
            t["status"] = "paused" if t["status"] == "active" else "active"
            _save_json(DATA_DIR / "scheduled.json", scheduled)
            return {"success": True, "status": t["status"]}

    raise HTTPException(status_code=404, detail="Task not found")


@router.post("/scheduled/{task_id}/run")
async def run_scheduled_now(task_id: str, request: Request):
    """Run a scheduled task immediately."""
    _require_auth(request)
    scheduled = _load_json(DATA_DIR / "scheduled.json", [])
    if not isinstance(scheduled, list):
        raise HTTPException(status_code=404, detail="Task not found")

    for t in scheduled:
        if t["id"] == task_id:
            # Add to queue
            await _task_queue.put(t)
            _audit_log("admin", "RUN_SCHEDULED", t["name"])
            return {"success": True, "message": "Added to queue"}

    raise HTTPException(status_code=404, detail="Task not found")


@router.get("/scheduled/queue")
async def get_queue(request: Request):
    """Get current execution queue."""
    _require_auth(request)
    return {"queue_size": _task_queue.qsize(), "running": _queue_running}


# =============================================
# Subscription usage tracking
# =============================================

STATS_FILE = Path("/opt/aila/.claude/stats-cache.json")
ADMIN_STATS_FILE = DATA_DIR / "admin_stats.json"
CREDENTIALS_FILE = Path("/opt/aila/.claude/.credentials.json")


def _load_admin_stats() -> dict:
    """Load local admin request stats."""
    stats = _load_json(ADMIN_STATS_FILE, {})
    if not isinstance(stats, dict):
        stats = {}
    today = datetime.now().strftime("%Y-%m-%d")
    if stats.get("date") != today:
        # Preserve total rate limit hits across days
        old_total_rl = stats.get("rate_limits", {}).get("hits_total", 0)
        stats = {
            "date": today,
            "chat_requests": 0,
            "code_requests": 0,
            "total_requests": 0,
            "rate_limits": {
                "last_hit": stats.get("rate_limits", {}).get("last_hit"),
                "last_hit_time": None,
                "hits_today": 0,
                "hits_total": old_total_rl,
            },
        }
    return stats


def _increment_admin_stats(req_type: str = "chat") -> None:
    """Increment daily request counter."""
    stats = _load_admin_stats()
    if req_type == "chat":
        stats["chat_requests"] = stats.get("chat_requests", 0) + 1
    else:
        stats["code_requests"] = stats.get("code_requests", 0) + 1
    stats["total_requests"] = stats.get("chat_requests", 0) + stats.get("code_requests", 0)
    _save_json(ADMIN_STATS_FILE, stats)


def _sync_stats_cache() -> None:
    """Copy stats-cache.json from home dir if accessible.

    Note: ProtectHome=true in systemd blocks /home/aila from service.
    To update stats, run manually: cp ~/.claude/stats-cache.json /opt/aila/.claude/
    """
    for src in [Path("/home/aila/.claude/stats-cache.json")]:
        try:
            if src.exists() and src.stat().st_size > 0:
                import shutil
                shutil.copy2(str(src), str(STATS_FILE))
                return
        except (PermissionError, OSError):
            pass


@router.get("/subscription-usage")
async def get_subscription_usage(request: Request):
    """Get subscription info and usage statistics."""
    _require_auth(request)

    result: dict[str, Any] = {
        "subscription_type": "unknown",
        "rate_limit_tier": "unknown",
        "model": "unknown",
    }

    # Read credentials for subscription info
    creds = _load_json(CREDENTIALS_FILE, {})
    oauth = creds.get("claudeAiOauth", {})
    if oauth:
        result["subscription_type"] = oauth.get("subscriptionType", "unknown")
        result["rate_limit_tier"] = oauth.get("rateLimitTier", "unknown")
        expires_ms = oauth.get("expiresAt", 0)
        if expires_ms:
            result["token_expires"] = datetime.fromtimestamp(
                expires_ms / 1000
            ).isoformat()

    # Read stats-cache for usage data
    stats = _load_json(STATS_FILE, {})
    if stats:
        result["model"] = "claude-opus-4-5-20251101"
        result["total_sessions"] = stats.get("totalSessions", 0)
        result["total_messages"] = stats.get("totalMessages", 0)
        result["first_session"] = stats.get("firstSessionDate", "")

        # Model usage totals
        model_usage = stats.get("modelUsage", {})
        for model_name, usage in model_usage.items():
            result["model"] = model_name
            result["total_input_tokens"] = usage.get("inputTokens", 0)
            result["total_output_tokens"] = usage.get("outputTokens", 0)
            result["total_cache_read"] = usage.get("cacheReadInputTokens", 0)
            result["total_cache_write"] = usage.get("cacheCreationInputTokens", 0)

        # Daily activity (last 7 days)
        daily = stats.get("dailyActivity", [])
        result["daily_activity"] = daily[-7:] if daily else []

        # Daily tokens (last 7 days)
        daily_tokens = stats.get("dailyModelTokens", [])
        result["daily_tokens"] = daily_tokens[-7:] if daily_tokens else []

        # Today's stats from daily activity
        today = datetime.now().strftime("%Y-%m-%d")
        today_activity = next(
            (d for d in daily if d.get("date") == today), None
        )
        if today_activity:
            result["today_messages"] = today_activity.get("messageCount", 0)
            result["today_sessions"] = today_activity.get("sessionCount", 0)
            result["today_tools"] = today_activity.get("toolCallCount", 0)

        today_tokens = next(
            (d for d in daily_tokens if d.get("date") == today), None
        )
        if today_tokens:
            tokens_by_model = today_tokens.get("tokensByModel", {})
            result["today_tokens_total"] = sum(tokens_by_model.values())

    # Local admin panel stats
    admin_stats = _load_admin_stats()
    result["admin_today"] = {
        "chat_requests": admin_stats.get("chat_requests", 0),
        "code_requests": admin_stats.get("code_requests", 0),
        "total_requests": admin_stats.get("total_requests", 0),
    }

    # Rate limit stats
    rl = admin_stats.get("rate_limits", {})
    result["rate_limits"] = {
        "last_hit": rl.get("last_hit"),
        "last_hit_time": rl.get("last_hit_time"),
        "hits_today": rl.get("hits_today", 0),
        "hits_total": rl.get("hits_total", 0),
    }

    return result


# =============================================
# Knowledge base
# =============================================

def _list_knowledge_files() -> list[dict]:
    """List all knowledge base files with metadata."""
    files = []
    if KNOWLEDGE_DIR.exists():
        for f in sorted(KNOWLEDGE_DIR.iterdir()):
            if f.is_file() and f.suffix in ALLOWED_EXTENSIONS:
                stat = f.stat()
                files.append({
                    "name": f.name,
                    "size": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    "type": _get_file_type(f.name),
                })
    return files


@router.get("/knowledge")
async def get_knowledge(request: Request):
    """Get list of knowledge base files with metadata."""
    _require_auth(request)
    files = _list_knowledge_files()
    total_size = sum(f["size"] for f in files)
    return {
        "files": files,
        "total_size": total_size,
        "last_loaded": _knowledge_last_loaded,
    }


@router.get("/knowledge/status")
async def knowledge_status(request: Request):
    """Get knowledge base loading status."""
    _require_auth(request)
    files = _list_knowledge_files()
    return {
        "loaded": _knowledge_loaded,
        "files_count": len(files),
        "last_loaded": _knowledge_last_loaded,
        "total_size": sum(f["size"] for f in files),
        "files": [f["name"] for f in files],
    }


@router.post("/knowledge/reload")
async def reload_knowledge(request: Request):
    """Reload all knowledge base files into cache."""
    _require_auth(request)
    _load_knowledge_base()  # updates cache and state
    files = _list_knowledge_files()
    _audit_log("admin", "RELOAD_KNOWLEDGE", f"{len(files)} files")
    return {
        "success": True,
        "files_count": len(files),
        "files": [f["name"] for f in files],
        "message": f"Knowledge base reloaded ({len(files)} files)",
    }


@router.get("/knowledge/download-all")
async def download_all_knowledge(request: Request, token: str = ""):
    """Download all knowledge files as a ZIP archive."""
    if token and token in _admin_sessions:
        _admin_sessions[token]["last_active"] = time.time()
    else:
        _require_auth(request)

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if KNOWLEDGE_DIR.exists():
            for f in sorted(KNOWLEDGE_DIR.iterdir()):
                if f.is_file() and f.suffix in ALLOWED_EXTENSIONS:
                    zf.write(f, f.name)

    buf.seek(0)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="knowledge_{ts}.zip"'
        },
    )


@router.post("/knowledge/upload")
async def upload_knowledge(request: Request, file: UploadFile = File(...)):
    """Upload a file to knowledge base."""
    _require_auth(request)

    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename required")

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported format. Allowed: {', '.join(ALLOWED_EXTENSIONS)}",
        )

    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="File too large (max 1MB)")

    safe_name = _sanitize_filename(file.filename)
    dest = KNOWLEDGE_DIR / safe_name
    dest.write_bytes(content)

    # Reload knowledge cache
    _load_knowledge_base()

    _audit_log("admin", "UPLOAD_KNOWLEDGE", safe_name)
    return {"success": True, "filename": safe_name}


# Parameterized routes MUST come after static routes
@router.get("/knowledge/{filename}/download")
async def download_knowledge_file(filename: str, request: Request, token: str = ""):
    """Download a single knowledge file."""
    # Accept token via query param for direct browser downloads
    if token and token in _admin_sessions:
        _admin_sessions[token]["last_active"] = time.time()
    else:
        _require_auth(request)
    safe_name = _sanitize_filename(filename)
    path = KNOWLEDGE_DIR / safe_name

    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    content = path.read_bytes()
    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
    )


@router.get("/knowledge/{filename}")
async def get_knowledge_file(filename: str, request: Request):
    """Get content of a specific knowledge file."""
    _require_auth(request)
    safe_name = _sanitize_filename(filename)
    path = KNOWLEDGE_DIR / safe_name

    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    content = path.read_text(errors="replace")
    stat = path.stat()
    return {
        "name": safe_name,
        "content": content,
        "size": stat.st_size,
        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        "type": _get_file_type(safe_name),
    }


@router.put("/knowledge/{filename}")
async def save_knowledge_file(filename: str, request: Request):
    """Save/update content of a knowledge file."""
    _require_auth(request)
    body = await request.json()
    content = body.get("content", "")

    safe_name = _sanitize_filename(filename)
    if not safe_name:
        raise HTTPException(status_code=400, detail="Invalid filename")

    ext = Path(safe_name).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Unsupported format")

    if len(content.encode("utf-8")) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="Content too large (max 1MB)")

    path = KNOWLEDGE_DIR / safe_name
    path.write_text(content, encoding="utf-8")

    _audit_log("admin", "SAVE_KNOWLEDGE", safe_name)
    return {"success": True, "filename": safe_name}


@router.delete("/knowledge/{filename}")
async def delete_knowledge(filename: str, request: Request):
    """Delete a knowledge base file."""
    _require_auth(request)

    safe_name = _sanitize_filename(filename)
    path = KNOWLEDGE_DIR / safe_name

    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    path.unlink()
    _knowledge_cache.pop(safe_name, None)

    _audit_log("admin", "DELETE_KNOWLEDGE", safe_name)
    return {"success": True}


# =============================================
# Claude Code update
# =============================================

def _get_claude_version() -> str:
    """Get current Claude Code CLI version."""
    try:
        result = subprocess.run(
            ["claude", "--version"],
            capture_output=True, text=True, timeout=10,
            env=_get_claude_env(),
        )
        return result.stdout.strip() if result.stdout else "unknown"
    except Exception:
        return "unknown"


@router.get("/claude-code/check-update")
async def check_claude_update(request: Request):
    """Check if Claude Code CLI has an update available."""
    _require_auth(request)

    current = await asyncio.to_thread(_get_claude_version)

    try:
        result = await asyncio.to_thread(
            subprocess.run,
            ["npm", "outdated", "-g", "@anthropic-ai/claude-code", "--json"],
            capture_output=True, text=True, timeout=30,
        )
        # npm outdated returns exit code 1 when updates exist
        output = result.stdout.strip()
        if output:
            data = json.loads(output)
            pkg = data.get("@anthropic-ai/claude-code", {})
            latest = pkg.get("latest", "")
            current_npm = pkg.get("current", current)
            if latest and latest != current_npm:
                return {
                    "update_available": True,
                    "current_version": current_npm,
                    "new_version": latest,
                    "message": f"Доступно обновление: {latest}",
                }
        # No output or package not in outdated list = up to date
        return {
            "update_available": False,
            "current_version": current,
            "message": "Установлена актуальная версия",
        }
    except json.JSONDecodeError:
        # npm outdated without --json or empty = up to date
        return {
            "update_available": False,
            "current_version": current,
            "message": "Установлена актуальная версия",
        }
    except Exception as e:
        logger.error(f"[UPDATE] Check failed: {e}")
        return {
            "error": True,
            "current_version": current,
            "message": str(e),
        }


@router.post("/claude-code/update")
async def update_claude_code(request: Request):
    """Update Claude Code CLI to the latest version."""
    _require_auth(request)

    old_version = await asyncio.to_thread(_get_claude_version)
    logger.info(f"[UPDATE] Claude Code update started. Current: {old_version}")
    _audit_log("admin", "UPDATE_START", f"current={old_version}")

    try:
        # Requires sudo permission for npm install -g
        # Add to sudoers: aila ALL=(ALL) NOPASSWD: /usr/bin/npm install -g @anthropic-ai/claude-code*
        result = await asyncio.to_thread(
            subprocess.run,
            ["sudo", "npm", "install", "-g", "@anthropic-ai/claude-code@latest"],
            capture_output=True, text=True, timeout=120,
        )

        if result.returncode == 0:
            new_version = await asyncio.to_thread(_get_claude_version)
            logger.info(f"[UPDATE] Claude Code updated: {old_version} → {new_version}")
            _audit_log("admin", "UPDATE_OK", f"{old_version} → {new_version}")

            await _send_telegram(
                f"✅ <b>Claude Code обновлён</b>\n"
                f"Версия: {old_version} → {new_version}\n"
                f"Время: {datetime.now().strftime('%H:%M:%S')}"
            )
            return {
                "success": True,
                "old_version": old_version,
                "new_version": new_version,
                "message": f"Обновлено: {old_version} → {new_version}",
            }
        else:
            error_msg = result.stderr.strip()[:500] if result.stderr else "Unknown error"
            logger.error(f"[UPDATE] Failed: {error_msg}")
            _audit_log("admin", "UPDATE_FAIL", error_msg[:200])

            await _send_telegram(
                f"❌ <b>Ошибка обновления Claude Code</b>\n"
                f"<pre>{error_msg[:300]}</pre>"
            )
            return {
                "success": False,
                "error": True,
                "message": error_msg,
            }
    except subprocess.TimeoutExpired:
        msg = "Timeout (120s)"
        logger.error(f"[UPDATE] {msg}")
        _audit_log("admin", "UPDATE_FAIL", msg)
        return {"success": False, "error": True, "message": msg}
    except Exception as e:
        logger.error(f"[UPDATE] Exception: {e}")
        _audit_log("admin", "UPDATE_FAIL", str(e))
        return {"success": False, "error": True, "message": str(e)}


# =============================================
# Scheduler background task
# =============================================

async def start_scheduler() -> None:
    """Start the background scheduler for auto tasks."""
    global _scheduler_task
    if _scheduler_task is None or _scheduler_task.done():
        _scheduler_task = asyncio.create_task(_scheduler_loop())
        logger.info("Admin scheduler started")


async def _scheduler_loop() -> None:
    """Main scheduler loop - checks scheduled tasks every 30s."""
    while True:
        try:
            await _check_scheduled_tasks()
            await _process_queue()
        except Exception as e:
            logger.error(f"Scheduler error: {e}")
        await asyncio.sleep(30)


async def _check_scheduled_tasks() -> None:
    """Check and queue due tasks."""
    scheduled = _load_json(DATA_DIR / "scheduled.json", [])
    if not isinstance(scheduled, list):
        return

    now = time.time()
    modified = False

    for task in scheduled:
        if task["status"] != "active":
            continue
        if task["next_run"] <= now:
            await _task_queue.put(dict(task))
            # Update next_run for recurring
            if task["type"] == "recurring":
                task["next_run"] = now + task["interval"]
            else:
                task["status"] = "completed"
            modified = True

    if modified:
        _save_json(DATA_DIR / "scheduled.json", scheduled)


_TOOL_USE_RE = re.compile(
    r'\{\s*"name"\s*:\s*"Bash"\s*,\s*"arguments"\s*:\s*\{[^}]*"command"\s*:\s*"((?:[^"\\]|\\.)*)"\s*\}',
    re.DOTALL,
)

_SCHEDULED_CMD_RE = re.compile(
    r"\[COMMAND_FOR_CODE\](.*?)\[/COMMAND_FOR_CODE\]", re.DOTALL,
)
_SCHEDULED_MAX_ITERATIONS = 10
_SCHEDULED_CODE_TIMEOUT = 300


def _fix_tool_use_response(response: str) -> str:
    """Convert JSON tool_use format to [COMMAND_FOR_CODE] tags.

    Claude sometimes returns {"name":"Bash","arguments":{"command":"..."}}
    instead of [COMMAND_FOR_CODE] tags despite --tools "". This extracts
    the command and wraps it properly.
    """
    if "[COMMAND_FOR_CODE]" in response:
        return response
    match = _TOOL_USE_RE.search(response)
    if not match:
        return response
    command = match.group(1).replace('\\"', '"').replace("\\n", "\n")
    # Replace the JSON block with proper tag
    before = response[:match.start()].rstrip()
    after = response[match.end():].lstrip()
    parts = [p for p in [before, f"[COMMAND_FOR_CODE]{command}[/COMMAND_FOR_CODE]", after] if p]
    fixed = "\n\n".join(parts)
    logger.info(f"[ADMIN_CHAT] Fixed tool_use → [COMMAND_FOR_CODE]: {command[:80]}")
    return fixed


async def _call_claude_cli(prompt: str, timeout: int = 120) -> tuple[str, str]:
    """Call Claude CLI with prompt for Chat (planner only, no tools)."""
    claude_env = _get_claude_env()
    result = await asyncio.to_thread(
        subprocess.run,
        [
            "claude", "--print", "--model", CLAUDE_MODEL,
            "--tools", "",  # No tools — planner only
            "--", prompt,
        ],
        cwd="/opt/aila",
        capture_output=True,
        text=True,
        timeout=timeout,
        env=claude_env,
    )
    stdout = result.stdout.strip() if result.stdout else ""
    stderr = result.stderr.strip() if result.stderr else ""
    return stdout, stderr


async def _execute_code_cli(command: str, timeout: int = 300) -> tuple[str, bool]:
    """Execute command via Claude Code CLI, return (output, success)."""
    claude_env = _get_claude_env()
    result = await asyncio.to_thread(
        subprocess.run,
        [
            "claude", "--print", "--model", CLAUDE_MODEL,
            "--no-session-persistence", "--dangerously-skip-permissions",
            command,
        ],
        cwd="/opt/aila",
        capture_output=True,
        text=True,
        timeout=timeout,
        env=claude_env,
    )
    stdout = result.stdout.strip() if result.stdout else ""
    stderr = result.stderr.strip() if result.stderr else ""
    output = stdout or stderr or "No output"
    return output, result.returncode == 0


async def _execute_scheduled_with_chat(task: dict) -> dict:
    """Execute scheduled task via Chat+Code cycle with KB.

    Flow: Chat(task.command) → [COMMAND_FOR_CODE] → Code → result →
          Chat analysis → loop until "Готово" or max iterations.
    """
    task_name = task.get("name", "unknown")
    history: list[dict] = []

    # First prompt — full KB
    knowledge = _load_knowledge_base()
    first_prompt = _build_first_prompt(
        task["command"], knowledge, "", "scheduled",
    )

    logger.info(
        f"[SCHEDULER] Starting chat cycle for '{task_name}' "
        f"(prompt {len(first_prompt)} chars)"
    )

    prompt = first_prompt
    for iteration in range(1, _SCHEDULED_MAX_ITERATIONS + 1):
        # --- Chat step ---
        chat_out, chat_err = await _call_claude_cli(prompt)

        # Rate limit check
        if _is_rate_limit_error(chat_err):
            return {
                "status": "rate_limited",
                "iterations": iteration,
                "summary": f"Rate limit on iteration {iteration}",
                "rate_limited": True,
            }

        response = chat_out or f"Error: {chat_err[:300]}"
        history.append({"role": "chat", "content": response[:2000]})

        logger.info(
            f"[SCHEDULER] '{task_name}' iter={iteration} "
            f"chat={len(response)} chars"
        )

        # Process knowledge updates
        _process_knowledge_updates(response)
        response = re.sub(
            r"\[UPDATE_KNOWLEDGE\].*?\[/UPDATE_KNOWLEDGE\]", "", response,
        ).strip() or response

        # Fix tool_use JSON → [COMMAND_FOR_CODE] if needed
        response = _fix_tool_use_response(response)

        # Extract command
        cmd_match = _SCHEDULED_CMD_RE.search(response)
        if not cmd_match:
            # No command — check if task is complete
            if _is_task_complete(response):
                logger.info(
                    f"[SCHEDULER] '{task_name}' complete at iter={iteration}"
                )
                return {
                    "status": "complete",
                    "iterations": iteration,
                    "summary": response[:1000],
                }
            # No command and not complete — return as-is
            logger.warning(
                f"[SCHEDULER] '{task_name}' no command at iter={iteration}"
            )
            return {
                "status": "no_command",
                "iterations": iteration,
                "summary": response[:1000],
            }

        command = cmd_match.group(1).strip()
        logger.info(
            f"[SCHEDULER] '{task_name}' iter={iteration} "
            f"executing: {command[:100]}"
        )

        # --- Code step ---
        code_output, code_success = await _execute_code_cli(
            command, timeout=_SCHEDULED_CODE_TIMEOUT,
        )
        history.append({"role": "code", "content": code_output[:2000]})

        logger.info(
            f"[SCHEDULER] '{task_name}' iter={iteration} "
            f"code={'OK' if code_success else 'FAIL'} "
            f"{len(code_output)} chars"
        )

        # Check if task is complete after code (response said "Готово" AND had command)
        if _is_task_complete(response):
            logger.info(
                f"[SCHEDULER] '{task_name}' complete at iter={iteration} "
                f"(after code)"
            )
            return {
                "status": "complete",
                "iterations": iteration,
                "summary": response[:500] + f"\n\nCode: {code_output[:500]}",
            }

        # --- Build follow-up prompt with code result ---
        history_text = "\n".join(
            f"[{h['role']}]: {h['content'][:500]}"
            for h in history[-4:]
        )
        truncated = code_output[:3000]
        prompt = _build_followup_prompt(
            f"Результат выполнения команды:\n```\n{truncated}\n```\n"
            "Проанализируй и продолжи задачу или напиши 'Готово' "
            "если задача завершена.",
            history_text,
            "scheduled",
        )

    # Max iterations reached
    logger.warning(
        f"[SCHEDULER] '{task_name}' reached max {_SCHEDULED_MAX_ITERATIONS} "
        f"iterations"
    )
    return {
        "status": "max_iterations",
        "iterations": _SCHEDULED_MAX_ITERATIONS,
        "summary": "Достигнут лимит итераций",
    }


async def _process_queue() -> None:
    """Process one task from the queue via Chat+Code cycle."""
    global _queue_running
    if _task_queue.empty() or _queue_running:
        return

    _queue_running = True
    try:
        task = await asyncio.wait_for(_task_queue.get(), timeout=1)
        task_name = task.get("name", "unknown")
        logger.info(f"[SCHEDULER] Starting task: {task_name}")

        try:
            result = await _execute_scheduled_with_chat(task)
        except Exception as e:
            result = {
                "status": "error",
                "iterations": 0,
                "summary": f"Error: {e}",
            }
            logger.error(f"[SCHEDULER] Task '{task_name}' error: {e}")

        status = result.get("status", "error")
        iterations = result.get("iterations", 0)
        summary = result.get("summary", "")

        # Handle rate limit — postpone task
        if result.get("rate_limited"):
            scheduled = _load_json(DATA_DIR / "scheduled.json", [])
            if isinstance(scheduled, list):
                for t in scheduled:
                    if t["id"] == task["id"]:
                        t["next_run"] = time.time() + RATE_LIMIT_COOLDOWN
                        t["last_run"] = datetime.now().isoformat()
                        t["last_result"] = "rate_limited"
                        break
                _save_json(DATA_DIR / "scheduled.json", scheduled)
            await _handle_rate_limit("scheduled", summary[:300])
            logger.warning(
                f"[SCHEDULER] Rate limit for '{task_name}', "
                f"postponed {RATE_LIMIT_COOLDOWN}s"
            )
            _audit_log(
                "scheduler", "RATE_LIMIT_PAUSE",
                task_name, f"postponed {RATE_LIMIT_COOLDOWN}s",
            )
            return

        # Update task in scheduled.json
        success = status == "complete"
        scheduled = _load_json(DATA_DIR / "scheduled.json", [])
        if isinstance(scheduled, list):
            for t in scheduled:
                if t["id"] == task["id"]:
                    t["last_run"] = datetime.now().isoformat()
                    t["last_result"] = "success" if success else status
                    t["last_output"] = summary[:1000]
                    t["iterations"] = iterations
                    break
            _save_json(DATA_DIR / "scheduled.json", scheduled)

        # Telegram notification
        tg_summary = summary[:500] if len(summary) > 500 else summary
        if success:
            await _send_telegram(
                f"⏰ Задача выполнена: <b>{task_name}</b>\n"
                f"Итераций: {iterations}\n"
                f"<pre>{tg_summary}</pre>"
            )
        else:
            await _send_telegram(
                f"⚠️ Задача завершена ({status}): <b>{task_name}</b>\n"
                f"Итераций: {iterations}\n"
                f"<pre>{tg_summary}</pre>"
            )

        _audit_log("scheduler", "TASK_DONE", task_name, summary[:200])

    except asyncio.TimeoutError:
        pass
    except Exception as e:
        logger.error(f"[SCHEDULER] Queue error: {e}")
    finally:
        _queue_running = False
