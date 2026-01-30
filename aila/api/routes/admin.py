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
from fastapi.responses import Response, StreamingResponse

logger = logging.getLogger("admin")

router = APIRouter(prefix="/api/admin", tags=["Admin"])


def _get_claude_env() -> dict[str, str]:
    """Build environment for claude subprocess using Max subscription.

    Claude Code is authorized via OAuth (Max subscription).
    ANTHROPIC_API_KEY must NOT be passed — it forces paid API billing.

    systemd uses ProtectHome=true so /home/aila/.claude/ is inaccessible.
    Credentials are copied to /opt/aila/.claude/.credentials.json,
    and HOME is set to /opt/aila so claude finds them there.
    """
    env = os.environ.copy()
    # Remove API key to force OAuth/subscription auth
    env.pop("ANTHROPIC_API_KEY", None)
    # Ensure claude is in PATH
    if "/usr/local/bin" not in env.get("PATH", ""):
        env["PATH"] = f"/usr/local/bin:{env.get('PATH', '/usr/bin')}"
    # Set HOME to /opt/aila where .claude/.credentials.json lives
    # (ProtectHome=true blocks /home/aila from systemd service)
    env["HOME"] = "/opt/aila"
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

# Rate limit detection patterns
RATE_LIMIT_PATTERNS = [
    "rate limit",
    "rate_limit",
    "too many requests",
    "429",
    "quota exceeded",
    "limit exceeded",
    "try again later",
    "overloaded",
    "capacity",
]


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
# Rate limit detection
# =============================================

def _is_rate_limit_error(output: str) -> bool:
    """Check if Claude output indicates a rate limit error."""
    if not output:
        return False
    output_lower = output.lower()
    return any(p in output_lower for p in RATE_LIMIT_PATTERNS)



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
        filtered = [
            m for m in history
            if m.get("type") != "code_result"
        ]
        for m in filtered:
            if len(m.get("content", "")) > 1000:
                m = dict(m)
                m["content"] = m["content"][:1000] + "..."
        return filtered[-limit:]
    return history[-limit:]


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
    _chat_start = time.time()
    knowledge = _load_knowledge_base()
    history = _get_chat_history(10, for_prompt=True)
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

# БАЗА ЗНАНИЙ
Ты изучил следующие файлы и ДОЛЖЕН следовать им:

{knowledge}

# ТВОЯ РОЛЬ
Ты — Claude Chat, интеллектуальный помощник для управления AILA AI Trade ботом.
- ВСЕГДА следуй правилам из RULES.md
- Используй контекст из AILA_SESSION_MEMORY.md
- Используй остальные файлы как справочную информацию

# УПРАВЛЕНИЕ ПРАВИЛАМИ
Если пользователь просит создать/добавить/удалить/изменить правило:
- Ответь что правило добавлено/удалено/изменено
- Добавь тег для обновления файла:
  [UPDATE_KNOWLEDGE]RULES.md|append|текст правила[/UPDATE_KNOWLEDGE]
  [UPDATE_KNOWLEDGE]RULES.md|remove|текст для удаления[/UPDATE_KNOWLEDGE]
Если пользователь просит показать правила, покажи содержимое RULES.md.

# ИСТОРИЯ ЧАТА
{history_text}

# ТЕКУЩИЙ ЗАПРОС
{message}

# ИНСТРУКЦИИ
- Отвечай на русском языке. Будь кратким и полезным.
- Если нужно выполнить действие на сервере, сформируй КОНКРЕТНУЮ команду:
  [COMMAND_FOR_CODE]конкретная bash команда или задача[/COMMAND_FOR_CODE]

# ПРАВИЛА ДЛЯ COMMAND_FOR_CODE
Claude Code имеет ПОЛНЫЙ доступ к /opt/aila/ и может:
- Читать/редактировать любые файлы проекта
- Выполнять bash команды (cat, grep, tail, ls и т.д.)
- Перезапускать сервисы (sudo systemctl restart aila)
- Читать логи, конфиги, код

ПРАВИЛЬНЫЕ примеры команд:
[COMMAND_FOR_CODE]cat /opt/aila/logs/ai_trade/trader.log | tail -50[/COMMAND_FOR_CODE]
[COMMAND_FOR_CODE]grep -n "error" /opt/aila/logs/ai_trade/engine.log | tail -20[/COMMAND_FOR_CODE]
[COMMAND_FOR_CODE]cat /opt/aila/data/ai_trade/trader_settings.json[/COMMAND_FOR_CODE]
[COMMAND_FOR_CODE]Прочитай файл /opt/aila/aila/trading/engine.py и найди функцию _execute_entry[/COMMAND_FOR_CODE]

НЕПРАВИЛЬНО (абстрактно):
[COMMAND_FOR_CODE]проверь логи[/COMMAND_FOR_CODE]
[COMMAND_FOR_CODE]посмотри настройки[/COMMAND_FOR_CODE]

Используй АБСОЛЮТНЫЕ пути от /opt/aila/."""

    try:
        # Run claude CLI for Chat with Sonnet (faster than default Opus)
        claude_env = _get_claude_env()
        prompt_len = len(prompt)
        logger.info(f"[ADMIN_CHAT] Calling claude CLI, prompt={prompt_len} chars")
        result = await asyncio.to_thread(
            subprocess.run,
            ["claude", "--print", "--model", "sonnet", prompt],
            cwd="/opt/aila",
            capture_output=True,
            text=True,
            env=claude_env,
        )
        elapsed = time.time() - _chat_start
        response = result.stdout.strip() if result.stdout else ""
        stderr = result.stderr.strip() if result.stderr else ""

        # Check for rate limit in stdout or stderr
        combined = f"{response} {stderr}"
        if _is_rate_limit_error(combined):
            rl_result = await _handle_rate_limit("chat", combined[:300])
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

    # Check if response contains command for Code
    has_command = "[COMMAND_FOR_CODE]" in response

    # Save assistant response
    _save_chat_message("assistant", response, "command" if has_command else "text")
    _audit_log("admin", "CHAT", message[:100], response[:200])

    return {
        "response": response,
        "has_command": has_command,
        "knowledge_updated": knowledge_updated,
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
        if _running_process and _running_process.returncode is None:
            raise HTTPException(status_code=409, detail="Another command is running")

        _audit_log("admin", "EXECUTE", command)

        try:
            cmd = [
                "claude", "--print",
                "--model", "sonnet",
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

            # Check for rate limit
            combined = f"{output} {error}"
            if _is_rate_limit_error(combined):
                rl_result = await _handle_rate_limit("code", combined[:300])
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

    body = await request.json()
    command = body.get("command", "").strip()

    if not command:
        raise HTTPException(status_code=400, detail="Command required")

    # Security: filter dangerous commands
    dangerous = ["rm -rf /", "cat .env", "echo $API_KEY", "DROP TABLE", "DELETE FROM"]
    for d in dangerous:
        if d.lower() in command.lower():
            _audit_log("admin", "BLOCKED_COMMAND", command)
            raise HTTPException(status_code=403, detail=f"Dangerous command blocked: {d}")

    if ".." in command and ("/etc/" in command or "/root/" in command):
        raise HTTPException(status_code=403, detail="Path restriction violated")

    _audit_log("admin", "EXECUTE_STREAM", command)

    async def event_stream():
        """Generate SSE events from Claude Code subprocess."""
        global _running_process

        async with _process_lock:
            if _running_process and _running_process.returncode is None:
                yield f"data: {json.dumps({'status': 'error', 'message': 'Another command is running'})}\n\n"
                return

            try:
                claude_env = _get_claude_env()
                logger.info(f"[ADMIN_CODE_STREAM] Executing: {command[:100]}")

                proc = await asyncio.create_subprocess_exec(
                    "claude", "--print",
                    "--model", "sonnet",
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
            # Client disconnected — kill process
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

        # Check for rate limit
        combined = f"{full_output} {full_error}"
        if _is_rate_limit_error(combined):
            await _handle_rate_limit("code_stream", combined[:300])
            _increment_admin_stats("code")
            yield f"data: {json.dumps({'status': 'error', 'message': 'Rate limit hit', 'rate_limit': True})}\n\n"
            return

        response = full_output or full_error or "Command completed (no output)"
        success = proc.returncode == 0

        _increment_admin_stats("code")
        _save_chat_message("code_result", response, "code_result")
        _audit_log("admin", "EXECUTE_STREAM_RESULT", command[:50], response[:200])

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
                ["claude", "--print", "--model", "sonnet", "--no-session-persistence", "--dangerously-skip-permissions", task["command"]],
                cwd="/opt/aila",
                capture_output=True,
                text=True,
                timeout=300,
                env=claude_env,
            )
            output = result.stdout.strip() if result.stdout else ""
            stderr = result.stderr.strip() if result.stderr else ""
            combined = f"{output} {stderr}"

            # Check for rate limit in scheduled task output
            if _is_rate_limit_error(combined):
                await _handle_rate_limit("scheduled", combined[:300])
                # Postpone task by RATE_LIMIT_COOLDOWN seconds
                scheduled = _load_json(DATA_DIR / "scheduled.json", [])
                if isinstance(scheduled, list):
                    for t in scheduled:
                        if t["id"] == task["id"]:
                            t["next_run"] = time.time() + RATE_LIMIT_COOLDOWN
                            t["last_run"] = datetime.now().isoformat()
                            t["last_result"] = "rate_limited"
                            break
                    _save_json(DATA_DIR / "scheduled.json", scheduled)
                logger.warning(
                    f"[SCHEDULER] Rate limit for task '{task['name']}', "
                    f"postponed {RATE_LIMIT_COOLDOWN}s"
                )
                _audit_log(
                    "scheduler", "RATE_LIMIT_PAUSE",
                    task["name"], f"postponed {RATE_LIMIT_COOLDOWN}s"
                )
                return

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
