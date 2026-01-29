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

import aiohttp
from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form

logger = logging.getLogger("admin")

router = APIRouter(prefix="/api/admin", tags=["Admin"])


def _get_claude_env() -> dict[str, str]:
    """Build environment for claude subprocess using Max subscription.

    Claude Code is authorized via OAuth (Max subscription).
    ANTHROPIC_API_KEY must NOT be passed — it forces paid API billing
    instead of the included subscription quota.
    """
    env = os.environ.copy()
    # Remove API key to force OAuth/subscription auth
    env.pop("ANTHROPIC_API_KEY", None)
    # Ensure claude is in PATH
    if "/usr/local/bin" not in env.get("PATH", ""):
        env["PATH"] = f"/usr/local/bin:{env.get('PATH', '/usr/bin')}"
    # Ensure HOME is set for .claude credentials
    if not env.get("HOME"):
        env["HOME"] = str(Path.home())
    return env

# Paths
DATA_DIR = Path("/opt/aila/data/admin")
CHAT_DIR = Path("/opt/aila/claude_chat")
KNOWLEDGE_DIR = CHAT_DIR / "knowledge"
HISTORY_DIR = CHAT_DIR / "history"
LOGS_DIR = Path("/opt/aila/logs")

# Ensure directories exist
for d in [DATA_DIR, KNOWLEDGE_DIR, HISTORY_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# Telegram config
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Auth state (in-memory)
_auth_codes: dict[str, dict] = {}  # code -> {expires, attempts}
_admin_sessions: dict[str, dict] = {}  # token -> {created, last_active}
_failed_attempts: dict[str, dict] = {}  # ip -> {count, blocked_until}

# Running processes
_running_process: Optional[subprocess.Popen] = None
_process_lock = asyncio.Lock()

# Scheduler state
_scheduler_task: Optional[asyncio.Task] = None
_task_queue: asyncio.Queue = asyncio.Queue()
_queue_running = False

# Constants
CODE_TTL = 300  # 5 minutes
SESSION_TTL = 1800  # 30 minutes
MAX_ATTEMPTS = 5
BLOCK_DURATION = 600  # 10 minutes
ALLOWED_EXTENSIONS = {".txt", ".md", ".json", ".py"}
MAX_FILE_SIZE = 1_000_000  # 1MB


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
        return False
    # Update last active
    session["last_active"] = time.time()
    return True


def _require_auth(request: Request) -> None:
    """Raise 401 if not authenticated."""
    if not _verify_admin_session(request):
        raise HTTPException(status_code=401, detail="Unauthorized")


def _get_client_ip(request: Request) -> str:
    """Get client IP address."""
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


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
    if ip in _failed_attempts:
        del _failed_attempts[ip]

    token = secrets.token_urlsafe(32)
    _admin_sessions[token] = {
        "created": now,
        "last_active": now,
        "ip": ip,
    }

    await _send_telegram(f"🔓 Вход в Админ панель\n🌐 IP: {ip}")
    _audit_log(ip, "LOGIN")

    return {"success": True, "token": token}


@router.get("/session")
async def check_session(request: Request):
    """Check if current session is valid."""
    valid = _verify_admin_session(request)
    return {"valid": valid}


# =============================================
# Claude Chat endpoints
# =============================================

def _load_knowledge_base() -> str:
    """Load all knowledge base files into context string."""
    parts = []
    if KNOWLEDGE_DIR.exists():
        for f in sorted(KNOWLEDGE_DIR.iterdir()):
            if f.is_file() and f.suffix in ALLOWED_EXTENSIONS:
                try:
                    content = f.read_text(errors="replace")[:50000]
                    parts.append(f"--- {f.name} ---\n{content}")
                except Exception:
                    pass
    return "\n\n".join(parts)


def _get_chat_history(limit: int = 20) -> list[dict]:
    """Get recent chat history."""
    history_file = HISTORY_DIR / "current.json"
    history = _load_json(history_file, [])
    return history[-limit:] if isinstance(history, list) else []


def _save_chat_message(role: str, content: str, msg_type: str = "text") -> None:
    """Save message to chat history."""
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


@router.post("/chat")
async def chat_message(request: Request):
    """Send message to Claude Chat."""
    _require_auth(request)
    body = await request.json()
    message = body.get("message", "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message required")

    # Save user message
    _save_chat_message("user", message)

    # Build context
    knowledge = _load_knowledge_base()
    history = _get_chat_history(10)
    history_text = "\n".join(
        f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['content']}"
        for m in history[:-1]  # exclude current message
    )

    # Read context.md if exists
    context_file = CHAT_DIR / "context.md"
    system_context = ""
    if context_file.exists():
        system_context = context_file.read_text(errors="replace")[:10000]

    prompt = f"""{system_context}

БАЗА ЗНАНИЙ:
{knowledge}

ИСТОРИЯ ЧАТА:
{history_text}

ТЕКУЩИЙ ЗАПРОС: {message}

Ты — Claude Chat, интеллектуальный помощник для управления AILA AI Trade ботом.
Твоя задача — помогать пользователю управлять торговым ботом.

Если нужно выполнить действие на сервере (проверить логи, перезапустить бот, изменить код и т.д.),
сформируй точную команду для Claude Code в формате:
[COMMAND_FOR_CODE]команда здесь[/COMMAND_FOR_CODE]

Отвечай на русском языке. Будь кратким и полезным."""

    try:
        # Run claude CLI for Chat with proper env
        claude_env = _get_claude_env()
        logger.info(f"[ADMIN_CHAT] Calling claude CLI, cwd={CHAT_DIR}")
        result = await asyncio.to_thread(
            subprocess.run,
            ["claude", "--print", prompt],
            cwd=str(CHAT_DIR),
            capture_output=True,
            text=True,
            timeout=120,
            env=claude_env,
        )
        response = result.stdout.strip() if result.stdout else ""
        stderr = result.stderr.strip() if result.stderr else ""

        if not response and stderr:
            response = f"Ошибка Claude: {stderr[:500]}"
            logger.error(f"[ADMIN_CHAT] stderr: {stderr[:300]}")
        elif not response:
            response = f"Ошибка: нет ответа от Claude (code={result.returncode})"
            logger.error(f"[ADMIN_CHAT] Empty output, returncode={result.returncode}")
        else:
            logger.info(f"[ADMIN_CHAT] Response received, {len(response)} chars")
    except subprocess.TimeoutExpired:
        response = "Превышено время ожидания ответа (120с)"
        logger.error("[ADMIN_CHAT] Timeout 120s")
    except FileNotFoundError:
        response = "Ошибка: claude CLI не найден. Проверьте установку."
        logger.error("[ADMIN_CHAT] claude CLI not found in PATH")
    except Exception as e:
        response = f"Ошибка: {str(e)}"
        logger.error(f"[ADMIN_CHAT] Exception: {e}")

    # Check if response contains command for Code
    has_command = "[COMMAND_FOR_CODE]" in response

    # Save assistant response
    _save_chat_message("assistant", response, "command" if has_command else "text")
    _audit_log("admin", "CHAT", message[:100], response[:200])

    return {
        "response": response,
        "has_command": has_command,
        "timestamp": datetime.now().isoformat(),
    }


@router.get("/chat/history")
async def get_chat_history_api(request: Request):
    """Get chat history."""
    _require_auth(request)
    history = _get_chat_history(50)
    return {"history": history}


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

    body = await request.json()
    command = body.get("command", "").strip()
    auto_mode = body.get("auto_mode", False)

    if not command:
        raise HTTPException(status_code=400, detail="Command required")

    # Security: filter dangerous commands
    dangerous = ["rm -rf /", "cat .env", "echo $API_KEY", "DROP TABLE", "DELETE FROM"]
    for d in dangerous:
        if d.lower() in command.lower():
            _audit_log("admin", "BLOCKED_COMMAND", command)
            raise HTTPException(status_code=403, detail=f"Dangerous command blocked: {d}")

    # Check path restrictions
    if ".." in command and ("/etc/" in command or "/root/" in command):
        raise HTTPException(status_code=403, detail="Path restriction violated")

    async with _process_lock:
        if _running_process and _running_process.poll() is None:
            raise HTTPException(status_code=409, detail="Another command is running")

        _audit_log("admin", "EXECUTE", command)

        try:
            cmd = ["claude", "--print"]
            if auto_mode:
                cmd.append("--dangerously-skip-permissions")
            cmd.append(command)

            claude_env = _get_claude_env()
            logger.info(f"[ADMIN_CODE] Executing: {command[:100]}")
            result = await asyncio.to_thread(
                subprocess.run,
                cmd,
                cwd="/opt/aila",
                capture_output=True,
                text=True,
                timeout=300,
                env=claude_env,
            )

            output = result.stdout.strip() if result.stdout else ""
            error = result.stderr.strip() if result.stderr else ""

            if not output and error:
                logger.error(f"[ADMIN_CODE] stderr: {error[:300]}")
            response = output or error or "Command completed (no output)"

            # Save to chat history
            _save_chat_message("code_result", response, "code_result")
            _audit_log("admin", "EXECUTE_RESULT", command[:50], response[:200])

            return {
                "success": result.returncode == 0,
                "output": response,
                "return_code": result.returncode,
                "timestamp": datetime.now().isoformat(),
            }

        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "output": "Command timed out (300s)",
                "return_code": -1,
                "timestamp": datetime.now().isoformat(),
            }
        except Exception as e:
            return {
                "success": False,
                "output": f"Error: {str(e)}",
                "return_code": -1,
                "timestamp": datetime.now().isoformat(),
            }


@router.post("/code/stop")
async def stop_code(request: Request):
    """Stop running Claude Code process."""
    _require_auth(request)
    global _running_process
    if _running_process and _running_process.poll() is None:
        _running_process.terminate()
        _running_process = None
        _audit_log("admin", "STOP_CODE")
        return {"success": True, "message": "Process terminated"}
    return {"success": True, "message": "No running process"}


@router.get("/code/status")
async def code_status(request: Request):
    """Get Claude Code execution status."""
    _require_auth(request)
    running = _running_process is not None and _running_process.poll() is None
    return {"running": running}


# =============================================
# Commands management
# =============================================

def _get_commands() -> list[dict]:
    """Get all commands including defaults."""
    # Default commands
    defaults = [
        {"id": "log_trader", "name": "Логи TRADER", "icon": "fa-file-lines", "category": "monitoring",
         "command": "Проверь логи TRADER на ошибки и покажи последние 50 строк"},
        {"id": "log_sniper", "name": "Логи SNIPER", "icon": "fa-crosshairs", "category": "monitoring",
         "command": "Проверь логи SNIPER на ошибки"},
        {"id": "errors", "name": "Ошибки", "icon": "fa-triangle-exclamation", "category": "monitoring",
         "command": "Найди все ошибки в логах за последний час и предложи исправления"},
        {"id": "status", "name": "Статус", "icon": "fa-circle-info", "category": "monitoring",
         "command": "Покажи полный статус системы: автопилот, позиции, баланс"},
        {"id": "restart", "name": "Рестарт", "icon": "fa-rotate", "category": "management",
         "command": "Перезапусти бота и проверь что всё работает"},
        {"id": "stop", "name": "Стоп", "icon": "fa-stop", "category": "management",
         "command": "Останови автопилот безопасно"},
        {"id": "start", "name": "Старт", "icon": "fa-play", "category": "management",
         "command": "Запусти автопилот и проверь статус"},
        {"id": "config", "name": "Конфиг", "icon": "fa-gear", "category": "management",
         "command": "Покажи текущие настройки TRADER и SNIPER"},
        {"id": "backup", "name": "Бэкап", "icon": "fa-database", "category": "data",
         "command": "Сделай полный бэкап данных AI Trade"},
        {"id": "pnl", "name": "PnL", "icon": "fa-chart-line", "category": "data",
         "command": "Покажи детальный PnL за сегодня"},
        {"id": "cleanup", "name": "Очистка", "icon": "fa-broom", "category": "data",
         "command": "Очисти логи старше 7 дней"},
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
# Knowledge base
# =============================================

@router.get("/knowledge")
async def get_knowledge(request: Request):
    """Get list of knowledge base files."""
    _require_auth(request)
    files = []
    if KNOWLEDGE_DIR.exists():
        for f in sorted(KNOWLEDGE_DIR.iterdir()):
            if f.is_file() and f.suffix in ALLOWED_EXTENSIONS:
                stat = f.stat()
                files.append({
                    "name": f.name,
                    "size": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                })
    return {"files": files}


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

    # Sanitize filename
    safe_name = "".join(c for c in file.filename if c.isalnum() or c in "._-")
    dest = KNOWLEDGE_DIR / safe_name
    dest.write_bytes(content)

    _audit_log("admin", "UPLOAD_KNOWLEDGE", safe_name)
    return {"success": True, "filename": safe_name}


@router.delete("/knowledge/{filename}")
async def delete_knowledge(filename: str, request: Request):
    """Delete a knowledge base file."""
    _require_auth(request)

    # Sanitize
    safe_name = "".join(c for c in filename if c.isalnum() or c in "._-")
    path = KNOWLEDGE_DIR / safe_name

    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    path.unlink()
    _audit_log("admin", "DELETE_KNOWLEDGE", safe_name)
    return {"success": True}


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


async def _process_queue() -> None:
    """Process one task from the queue."""
    global _queue_running
    if _task_queue.empty() or _queue_running:
        return

    _queue_running = True
    try:
        task = await asyncio.wait_for(_task_queue.get(), timeout=1)
        logger.info(f"Executing scheduled task: {task['name']}")

        # Execute via Claude Code
        try:
            claude_env = _get_claude_env()
            result = await asyncio.to_thread(
                subprocess.run,
                ["claude", "--print", "--dangerously-skip-permissions", task["command"]],
                cwd="/opt/aila",
                capture_output=True,
                text=True,
                timeout=300,
                env=claude_env,
            )
            output = result.stdout.strip() if result.stdout else ""
            stderr = result.stderr.strip() if result.stderr else ""
            output = output or stderr or "No output"
            success = result.returncode == 0
        except Exception as e:
            output = f"Error: {e}"
            success = False

        # Update task last_run
        scheduled = _load_json(DATA_DIR / "scheduled.json", [])
        if isinstance(scheduled, list):
            for t in scheduled:
                if t["id"] == task["id"]:
                    t["last_run"] = datetime.now().isoformat()
                    t["last_result"] = "success" if success else "error"
                    break
            _save_json(DATA_DIR / "scheduled.json", scheduled)

        # Send Telegram notification
        summary = output[:300] if len(output) > 300 else output
        if success:
            await _send_telegram(
                f"⏰ Задача выполнена: <b>{task['name']}</b>\n"
                f"Результат:\n<pre>{summary}</pre>"
            )
        else:
            await _send_telegram(
                f"❌ Ошибка задачи: <b>{task['name']}</b>\n"
                f"<pre>{summary}</pre>"
            )

        _audit_log("scheduler", "TASK_DONE", task["name"], output[:200])

    except asyncio.TimeoutError:
        pass
    except Exception as e:
        logger.error(f"Queue processing error: {e}")
    finally:
        _queue_running = False
