"""
AILA - Web API and Dashboard

FastAPI-based web interface for monitoring and controlling the trading bot.
"""

import asyncio
import hashlib
import hmac
import os
import secrets
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Response, Request, Cookie
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

import structlog

# Load environment variables
load_dotenv()

# Telegram Auth Config
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_AUTH_BOT_TOKEN", "")
TELEGRAM_BOT_USERNAME = os.getenv("TELEGRAM_BOT_USERNAME", "aila_zone_bot")
ALLOWED_TELEGRAM_IDS = set(
    int(x.strip()) for x in os.getenv("ALLOWED_TELEGRAM_IDS", "").split(",") if x.strip()
)

# Session storage (in-memory, simple approach)
active_sessions: dict[str, dict] = {}

logger = structlog.get_logger(__name__)

# Store logs in memory (last 500 lines)
log_buffer: deque = deque(maxlen=500)

# Connected WebSocket clients
connected_clients: set[WebSocket] = set()


def add_log(message: str):
    """Add a log message and broadcast to all connected clients."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"{timestamp} | {message}"
    log_buffer.append(log_entry)

    # Broadcast to all connected clients (only if event loop is running)
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(broadcast_log(log_entry))
    except RuntimeError:
        # No running event loop - skip broadcast (will be sent on next WS poll)
        pass


async def broadcast_log(message: str):
    """Broadcast log message to all connected WebSocket clients."""
    disconnected = set()
    for client in connected_clients:
        try:
            await client.send_text(message)
        except Exception:
            disconnected.add(client)

    # Remove disconnected clients
    for client in disconnected:
        connected_clients.discard(client)


# Create FastAPI app
app = FastAPI(
    title="AILA Trading Bot",
    description="AI-powered algorithmic trading system",
    version="1.0.0",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files
app.mount("/static", StaticFiles(directory="/opt/aila/static"), name="static")

# Clear logs on startup
@app.on_event("startup")
async def startup_event():
    """Clear logs when server restarts."""
    log_buffer.clear()
    log_buffer.append(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | --- Сервер перезапущен / Server restarted ---")


# =============================================
# Authentication Functions
# =============================================

def verify_telegram_auth(auth_data: dict) -> bool:
    """Verify Telegram Login Widget data."""
    if not TELEGRAM_BOT_TOKEN:
        return False

    # Extract hash and prepare data
    received_hash = auth_data.get("hash", "")
    data_check_arr = []

    for key, value in sorted(auth_data.items()):
        if key != "hash":
            data_check_arr.append(f"{key}={value}")

    data_check_string = "\n".join(data_check_arr)

    # Create secret key from bot token
    secret_key = hashlib.sha256(TELEGRAM_BOT_TOKEN.encode()).digest()

    # Calculate hash
    calculated_hash = hmac.new(
        secret_key,
        data_check_string.encode(),
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(calculated_hash, received_hash)


def create_session(telegram_id: int, username: str = "") -> str:
    """Create a new session and return session token."""
    session_token = secrets.token_urlsafe(32)
    active_sessions[session_token] = {
        "telegram_id": telegram_id,
        "username": username,
        "created_at": time.time(),
    }
    return session_token


def verify_session(session_token: str) -> bool:
    """Verify if session is valid (exists and not expired - 7 days)."""
    if not session_token or session_token not in active_sessions:
        return False

    session = active_sessions[session_token]
    # Check if session is expired (7 days = 604800 seconds)
    if time.time() - session["created_at"] > 604800:
        del active_sessions[session_token]
        return False

    return True


def get_session_user(session_token: str) -> Optional[dict]:
    """Get user info from session."""
    if verify_session(session_token):
        return active_sessions.get(session_token)
    return None


# Login page HTML
LOGIN_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="theme-color" content="#1a1a2e">
    <link rel="icon" type="image/png" href="/static/aila-icon.png">
    <title>AILA - Login</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        html, body {
            height: 100%;
        }

        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: #000;
            color: #e0e0e0;
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }

        .login-container {
            background: rgba(255, 255, 255, 0.05);
            border-radius: 16px;
            padding: 40px;
            text-align: center;
            backdrop-filter: blur(10px);
            border: 1px solid rgba(255, 255, 255, 0.1);
            max-width: 450px;
            width: 100%;
        }

        .logo {
            width: 300px;
            height: auto;
            margin-bottom: 20px;
        }

        .title {
            font-size: 28px;
            font-weight: 600;
            color: #fff;
            margin-bottom: 16px;
        }

        .description {
            color: #888;
            margin-bottom: 30px;
            font-size: 15px;
            line-height: 1.5;
        }

        .telegram-login {
            display: flex;
            justify-content: center;
            margin-top: 10px;
        }

        .error-message {
            background: rgba(255, 68, 68, 0.2);
            border: 1px solid #ff4444;
            color: #ff6b6b;
            padding: 12px 20px;
            border-radius: 8px;
            margin-bottom: 20px;
            display: none;
        }

        .error-message.show {
            display: block;
        }

        #bg-video {
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            object-fit: cover;
            z-index: -2;
        }

        .video-overlay {
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0, 0, 0, 0.5);
            z-index: -1;
        }
    </style>
</head>
<body>
    <video autoplay loop muted playsinline id="bg-video">
        <source src="/static/bg-video.mp4" type="video/mp4">
    </video>
    <div class="video-overlay"></div>

    <div class="login-container">
        <img src="/static/logo.png" alt="AILA" class="logo">
        <h1 class="title">AI Trading Platform</h1>
        <p class="description" id="description"></p>

        <div class="error-message" id="errorMessage"></div>

        <div class="telegram-login">
            <script async src="https://telegram.org/js/telegram-widget.js?22"
                    data-telegram-login="BOT_USERNAME_PLACEHOLDER"
                    data-size="large"
                    data-auth-url="https://aila.zone/auth/telegram"
                    data-request-access="write">
            </script>
        </div>
    </div>

    <script>
        // Detect language
        const browserLang = (navigator.language || navigator.userLanguage || 'en').toLowerCase();
        const isRussian = browserLang.startsWith('ru');
        const detectedLang = isRussian ? 'ru' : 'en';

        // Save detected language to cookie and localStorage for dashboard
        document.cookie = 'ailaLang=' + detectedLang + '; path=/; max-age=31536000; SameSite=Lax';
        localStorage.setItem('ailaLang', detectedLang);

        // Set texts based on language
        const texts = {
            ru: {
                description: 'Автоматическая торговля криптовалютой с искусственным интеллектом. AI анализирует рынок 24/7, самообучается на каждой сделке и постоянно улучшает результаты. Ранний доступ для первых пользователей.',
                accessDenied: 'Доступ запрещён',
                authError: 'Ошибка авторизации'
            },
            en: {
                description: 'Automated crypto trading powered by AI. The system analyzes markets 24/7, learns from every trade, and continuously improves results. Early access for first users.',
                accessDenied: 'Access denied',
                authError: 'Authentication error'
            }
        };

        const t = isRussian ? texts.ru : texts.en;
        document.getElementById('description').textContent = t.description;

        // Check for error in URL
        const urlParams = new URLSearchParams(window.location.search);
        const error = urlParams.get('error');
        if (error) {
            const errorEl = document.getElementById('errorMessage');
            if (error === 'access_denied') {
                errorEl.textContent = t.accessDenied;
            } else if (error === 'invalid_auth') {
                errorEl.textContent = t.authError;
            } else {
                errorEl.textContent = error;
            }
            errorEl.classList.add('show');
        }
    </script>
</body>
</html>
""".replace("BOT_USERNAME_PLACEHOLDER", TELEGRAM_BOT_USERNAME)


# Dashboard HTML with auto-refresh and copy button
DASHBOARD_HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
    <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
    <meta http-equiv="Pragma" content="no-cache">
    <meta http-equiv="Expires" content="0">
    <!-- iOS PWA -->
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
    <meta name="apple-mobile-web-app-title" content="AILA Bot">
    <meta name="mobile-web-app-capable" content="yes">
    <meta name="theme-color" content="#1a1a2e">
    <link rel="manifest" href="/static/manifest.json">
    <link rel="icon" type="image/png" href="/static/aila-icon.png">
    <link rel="apple-touch-icon" href="/static/aila-icon.png">
    <title>AILA Trading Bot v2.1</title>
    <script src="https://unpkg.com/lightweight-charts@4.1.0/dist/lightweight-charts.standalone.production.js"></script>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            color: #e0e0e0;
            min-height: 100vh;
            padding: 20px;
            padding-top: calc(20px + env(safe-area-inset-top, 0px));
            padding-bottom: 50px; /* Space for fixed bottom panel */
        }

        .container {
            max-width: 1600px;
            margin: 0 auto;
        }

        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 12px 20px;
            background: rgba(255, 255, 255, 0.05);
            border-radius: 12px;
            margin-bottom: 20px;
            backdrop-filter: blur(10px);
            border: 1px solid rgba(255, 255, 255, 0.1);
        }

        .header-left {
            display: flex;
            align-items: center;
            gap: 10px;
        }

        .lang-selector {
            background: transparent;
            border: none;
            color: #888;
            font-size: 14px;
            font-weight: 600;
            cursor: pointer;
            padding: 5px;
            letter-spacing: 1px;
        }

        .lang-selector:focus {
            outline: none;
        }

        h1 {
            font-size: 28px;
            background: linear-gradient(90deg, #00d4ff, #00ff88);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }

        .status {
            display: flex;
            align-items: center;
            gap: 10px;
        }

        .status-dot {
            width: 10px;
            height: 10px;
            border-radius: 50%;
            background: #00ff88;
            animation: pulse 2s infinite;
        }

        .status-dot.disconnected {
            background: #ff4444;
            animation: none;
        }

        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }

        .stats-row {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 12px;
            margin-bottom: 12px;
        }

        .stat-card {
            background: rgba(255, 255, 255, 0.05);
            border-radius: 10px;
            padding: 12px 15px;
            backdrop-filter: blur(10px);
            border: 1px solid rgba(255, 255, 255, 0.1);
            text-align: center;
            height: 65px;
            display: flex;
            flex-direction: column;
            justify-content: center;
        }

        .stat-title {
            font-size: 10px;
            color: #888;
            margin-bottom: 4px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }

        .stat-value {
            font-size: 16px;
            font-weight: bold;
            color: #00d4ff;
            white-space: nowrap;
        }

        .stat-value.positive { color: #00ff88; }
        .stat-value.negative { color: #ff4444; }

        /* Legacy cards - keep for bots section */
        .cards {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            margin-bottom: 20px;
        }

        .card {
            background: rgba(255, 255, 255, 0.05);
            border-radius: 12px;
            padding: 20px;
            backdrop-filter: blur(10px);
            border: 1px solid rgba(255, 255, 255, 0.1);
        }

        .card-title {
            font-size: 14px;
            color: #888;
            margin-bottom: 8px;
        }

        .card-value {
            font-size: 24px;
            font-weight: bold;
            color: #00d4ff;
        }

        .card-value.positive { color: #00ff88; }
        .card-value.negative { color: #ff4444; }

        .btn-group {
            display: flex;
            gap: 10px;
        }

        .btn {
            padding: 8px 16px;
            border: none;
            border-radius: 6px;
            cursor: pointer;
            font-size: 14px;
            transition: all 0.3s;
        }

        .btn-primary {
            background: linear-gradient(90deg, #00d4ff, #00ff88);
            color: #1a1a2e;
            font-weight: bold;
        }

        .btn-primary:hover {
            transform: translateY(-2px);
            box-shadow: 0 4px 15px rgba(0, 212, 255, 0.3);
        }

        .btn-secondary {
            background: rgba(255, 255, 255, 0.1);
            color: #e0e0e0;
        }

        .btn-secondary:hover {
            background: rgba(255, 255, 255, 0.2);
        }

        .btn-success {
            background: #00ff88;
            color: #1a1a2e;
        }

        .logs {
            background: #0d0d0d;
            border-radius: 8px;
            padding: 12px;
            height: 200px;
            overflow-y: auto;
            font-family: 'Consolas', 'Monaco', monospace;
            font-size: 12px;
            line-height: 1.5;
        }

        .log-line {
            padding: 2px 0;
            border-bottom: 1px solid rgba(255, 255, 255, 0.03);
        }

        .log-line:hover {
            background: rgba(255, 255, 255, 0.05);
        }

        .log-info { color: #00d4ff; }
        .log-warning { color: #ffaa00; }
        .log-error { color: #ff4444; }
        .log-success { color: #00ff88; }
        .log-trade-open { color: #ffcc00; font-weight: bold; }
        .log-trade-profit { color: #00ff88; font-weight: bold; }
        .log-trade-loss { color: #ff4444; font-weight: bold; }

        /* Fullscreen Logs Modal */
        .logs-modal {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(10, 10, 20, 0.98);
            z-index: 2000;
            flex-direction: column;
        }

        .logs-modal.show {
            display: flex;
        }

        .logs-modal-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 15px 20px;
            padding-top: calc(15px + env(safe-area-inset-top, 0px));
            background: rgba(26, 26, 46, 0.95);
            border-bottom: 1px solid rgba(0, 212, 255, 0.3);
        }

        .logs-modal-left,
        .logs-modal-right {
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .logs-modal-title {
            color: #00d4ff;
            font-size: 16px;
            margin: 0;
            display: flex;
            align-items: center;
        }

        .logs-modal-btn {
            width: 36px;
            height: 36px;
            display: flex;
            align-items: center;
            justify-content: center;
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 8px;
            cursor: pointer;
            color: #888;
            transition: all 0.2s;
        }

        .logs-modal-btn:hover {
            background: rgba(255, 255, 255, 0.1);
            color: #fff;
            border-color: rgba(255, 255, 255, 0.2);
        }

        .logs-modal-btn.active {
            color: #00ff88;
            border-color: rgba(0, 255, 136, 0.3);
            background: rgba(0, 255, 136, 0.1);
        }

        .logs-modal-btn.active:hover {
            background: rgba(0, 255, 136, 0.2);
        }

        .logs-modal-close {
            color: #ff6666;
            border-color: rgba(255, 100, 100, 0.3);
        }

        .logs-modal-close:hover {
            background: rgba(255, 100, 100, 0.2);
            color: #ff8888;
            border-color: rgba(255, 100, 100, 0.5);
        }

        .logs-modal-body {
            flex: 1;
            padding: 15px 20px 20px;
            overflow: hidden;
            display: flex;
            flex-direction: column;
        }

        .logs-modal-body .logs {
            flex: 1;
            height: auto;
            border-radius: 12px;
            padding: 15px;
        }

        /* Mobile: move header to bottom */
        @media (max-width: 767px) {
            .logs-modal {
                flex-direction: column-reverse;
            }

            .logs-modal-header {
                border-bottom: none;
                border-top: 1px solid rgba(0, 212, 255, 0.3);
                padding: 8px 15px;
                padding-bottom: calc(8px + env(safe-area-inset-bottom, 0px));
            }

            .logs-modal-body {
                padding: 15px 15px 10px;
                padding-top: calc(env(safe-area-inset-top, 0px) + 20px);
            }

            .logs-modal-title {
                font-size: 14px;
            }

            .logs-modal-title svg {
                width: 16px;
                height: 16px;
                margin-right: 6px;
            }

            .logs-modal-btn {
                width: 32px;
                height: 32px;
            }

            .logs-modal-btn svg {
                width: 14px;
                height: 14px;
            }
        }

        .toast {
            position: fixed;
            bottom: 20px;
            right: 20px;
            background: #00ff88;
            color: #1a1a2e;
            padding: 12px 24px;
            border-radius: 8px;
            font-weight: bold;
            transform: translateY(100px);
            opacity: 0;
            transition: all 0.3s;
            z-index: 3000;
        }

        .toast.show {
            transform: translateY(0);
            opacity: 1;
        }

        .controls {
            margin-bottom: 20px;
        }

        .control-group {
            display: flex;
            gap: 15px;
            justify-content: center;
            flex-wrap: wrap;
        }

        .btn-large {
            padding: 15px 30px;
            font-size: 16px;
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .btn-icon {
            font-size: 14px;
        }

        .btn-danger {
            background: #ff4444;
            color: white;
        }

        .btn-danger:hover:not(:disabled) {
            background: #ff6666;
            transform: translateY(-2px);
        }

        .btn:disabled {
            opacity: 0.5;
            cursor: not-allowed;
            transform: none !important;
        }

        .settings-panel {
            background: rgba(255, 255, 255, 0.05);
            border-radius: 12px;
            padding: 25px;
            margin-bottom: 20px;
            border: 1px solid rgba(255, 255, 255, 0.1);
            backdrop-filter: blur(10px);
        }

        .settings-panel h3 {
            margin-bottom: 20px;
            color: #00d4ff;
            font-size: 18px;
        }

        .settings-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            margin-bottom: 20px;
        }

        .setting-item {
            display: flex;
            flex-direction: column;
            gap: 8px;
        }

        .setting-item label {
            font-size: 13px;
            color: #888;
        }

        .setting-item input,
        .setting-item select {
            padding: 10px 12px;
            border-radius: 6px;
            border: 1px solid rgba(255, 255, 255, 0.1);
            background: rgba(0, 0, 0, 0.3);
            color: #e0e0e0;
            font-size: 14px;
        }

        .setting-item input:focus,
        .setting-item select:focus {
            outline: none;
            border-color: #00d4ff;
        }

        .settings-actions {
            display: flex;
            gap: 10px;
            justify-content: space-between;
            align-items: center;
        }

        /* Input with clear button */
        .input-clearable {
            position: relative;
            display: flex;
            align-items: center;
            width: 100%;
        }
        .input-clearable input {
            padding-left: 10px !important;
            padding-right: 24px !important;
            flex: 1;
            width: 100%;
        }
        .input-clear-btn {
            position: absolute;
            right: 6px;
            background: none;
            border: none;
            cursor: pointer;
            color: #666;
            font-size: 14px;
            line-height: 1;
            padding: 2px;
            transition: color 0.2s;
            z-index: 1;
        }
        .input-clear-btn:hover {
            color: #999;
        }

        .running .btn-success {
            opacity: 0.5;
        }

        .lang-selector option {
            background: #1a1a2e;
            color: #e0e0e0;
        }

        .header-right {
            display: flex;
            align-items: center;
            gap: 12px;
        }

        .logout-btn {
            background: transparent;
            border: 1px solid rgba(255, 68, 68, 0.3);
            color: #ff6b6b;
            font-size: 12px;
            font-weight: 600;
            cursor: pointer;
            padding: 6px 12px;
            border-radius: 6px;
            transition: all 0.2s;
        }

        .logout-btn:hover {
            background: rgba(255, 68, 68, 0.2);
            border-color: #ff4444;
        }

        /* Charts Section */
        .charts-section {
            margin-bottom: 20px;
        }

        .charts-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 15px;
        }

        .charts-header h3 {
            color: #00d4ff;
            font-size: 18px;
        }

        .chart-selector {
            display: flex;
            gap: 10px;
            align-items: center;
        }

        .chart-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(400px, 1fr));
            gap: 20px;
        }

        .chart-container {
            background: rgba(0, 0, 0, 0.3);
            border-radius: 12px;
            padding: 15px;
            border: 1px solid rgba(255, 255, 255, 0.1);
        }

        .chart-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 10px;
        }

        .chart-symbol {
            font-size: 16px;
            font-weight: bold;
            color: #fff;
        }

        .chart-price {
            font-size: 14px;
            color: #00d4ff;
        }

        .chart-change {
            font-size: 12px;
            margin-left: 8px;
        }

        .chart-change.positive { color: #00ff88; }
        .chart-change.negative { color: #ff4444; }

        .chart-wrapper {
            height: 250px;
            border-radius: 8px;
            overflow: hidden;
        }

        /* Bots Manager Section */
        .bots-section {
            margin-top: 20px;
            padding-top: 20px;
            margin-bottom: 20px;
            border-top: 1px solid rgba(255, 255, 255, 0.1);
        }

        .bots-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 15px;
        }

        .bots-header h3 {
            color: #00d4ff;
            font-size: 18px;
        }

        .bots-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 15px;
        }

        .bot-card {
            background: rgba(255, 255, 255, 0.05);
            border-radius: 12px;
            padding: 20px;
            border: 1px solid rgba(255, 255, 255, 0.1);
        }

        .bot-card.running {
            border-color: #00ff88;
        }

        .bot-header {
            display: flex;
            justify-content: flex-start;
            align-items: center;
            margin-bottom: 15px;
        }

        .bot-name {
            font-size: 16px;
            font-weight: bold;
            color: #fff;
        }

        .bot-status {
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 12px;
            font-weight: bold;
        }

        .bot-status.running {
            background: #00ff88;
            color: #1a1a2e;
        }

        .bot-status.stopped {
            background: rgba(255, 255, 255, 0.2);
            color: #888;
        }

        .bot-status.paused {
            background: #ffcc00;
            color: #1a1a2e;
        }

        .bot-status.starting {
            background: #ffcc00;
            color: #1a1a2e;
            animation: pulse 1s infinite;
        }

        .bot-status.stopping {
            background: #ff6b6b;
            color: #1a1a2e;
            animation: pulse 1s infinite;
        }

        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }

        /* Blinking status indicator */
        @keyframes blink {
            0%, 100% { opacity: 1; box-shadow: 0 0 8px currentColor; }
            50% { opacity: 0.4; box-shadow: 0 0 2px currentColor; }
        }

        /* Spinning animation */
        @keyframes spin {
            from { transform: rotate(0deg); }
            to { transform: rotate(360deg); }
        }

        .spin {
            animation: spin 1s linear infinite;
        }

        .status-indicator {
            width: 10px;
            height: 10px;
            border-radius: 50%;
            display: inline-block;
            margin-right: 8px;
            animation: blink 1s infinite;
        }

        .status-indicator.running {
            background: #00ff88;
            color: #00ff88;
        }

        .status-indicator.paused {
            background: #ffcc00;
            color: #ffcc00;
        }

        .status-indicator.error, .status-indicator.stopping {
            background: #ff4444;
            color: #ff4444;
        }

        .status-indicator.stopped, .status-indicator.starting {
            background: #888;
            color: #888;
            animation: none;
        }

        .bot-card.processing {
            opacity: 0.8;
            pointer-events: none;
        }

        .bot-details {
            font-size: 13px;
            color: #888;
            margin-bottom: 15px;
        }

        .bot-details div {
            margin-bottom: 5px;
        }

        .bot-actions {
            display: flex;
            gap: 10px;
        }

        .bot-actions .btn {
            flex: 1;
        }

        /* Trading Pairs Selector */
        .pairs-selector {
            background: rgba(255, 255, 255, 0.05);
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 20px;
            border: 1px solid rgba(255, 255, 255, 0.1);
        }

        .pairs-selector h3 {
            color: #00d4ff;
            font-size: 18px;
            margin-bottom: 15px;
        }

        .pairs-grid {
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
            max-height: 200px;
            overflow-y: auto;
        }

        .pair-chip {
            padding: 8px 15px;
            background: rgba(0, 0, 0, 0.3);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 20px;
            cursor: pointer;
            font-size: 13px;
            transition: all 0.3s;
        }

        .pair-chip:hover {
            border-color: #00d4ff;
        }

        .pair-chip.selected {
            background: linear-gradient(90deg, #00d4ff, #00ff88);
            color: #1a1a2e;
            font-weight: bold;
        }

        .pairs-search {
            margin-bottom: 15px;
        }

        .pairs-search input {
            width: 100%;
            padding: 10px 15px;
            border-radius: 6px;
            border: 1px solid rgba(255, 255, 255, 0.1);
            background: rgba(0, 0, 0, 0.3);
            color: #e0e0e0;
            font-size: 14px;
        }

        /* Modal */
        .modal {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0, 0, 0, 0.8);
            z-index: 1000;
            justify-content: center;
            align-items: center;
        }

        .modal.show {
            display: flex;
        }

        .modal-content {
            background: #1a1a2e;
            border-radius: 12px;
            padding: 30px;
            padding-top: calc(30px + env(safe-area-inset-top, 0px));
            max-width: 500px;
            width: 90%;
            border: 1px solid rgba(255, 255, 255, 0.1);
        }

        .modal-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
        }

        .modal-header h3 {
            color: #00d4ff;
        }

        .modal-close {
            background: none;
            border: none;
            color: #888;
            font-size: 24px;
            cursor: pointer;
        }

        .modal-close:hover {
            color: #fff;
        }

        /* Toggle Switch */
        .toggle-switch {
            position: relative;
            width: 36px;
            height: 18px;
            flex-shrink: 0;
        }
        .toggle-switch input {
            opacity: 0;
            width: 0;
            height: 0;
        }
        .toggle-slider {
            position: absolute;
            cursor: pointer;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(255,255,255,0.1);
            transition: 0.3s;
            border-radius: 18px;
        }
        .toggle-slider:before {
            position: absolute;
            content: "";
            height: 12px;
            width: 12px;
            left: 3px;
            bottom: 3px;
            background: #888;
            transition: 0.3s;
            border-radius: 50%;
        }
        .toggle-switch input:checked + .toggle-slider {
            background: linear-gradient(90deg, #00d4ff, #00ff88);
        }
        .toggle-switch input:checked + .toggle-slider:before {
            transform: translateX(18px);
            background: #1a1a2e;
        }

        /* Info Icon */
        .info-icon {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 16px;
            height: 16px;
            border-radius: 50%;
            background: rgba(255,255,255,0.1);
            color: #888;
            font-size: 11px;
            font-weight: bold;
            cursor: pointer;
            margin-left: 6px;
            transition: all 0.2s;
            font-style: italic;
            font-family: Georgia, serif;
        }
        .info-icon:hover {
            background: rgba(0,212,255,0.3);
            color: #00d4ff;
        }
        .info-tooltip {
            display: none;
            position: absolute;
            background: linear-gradient(135deg, #1a1a2e 0%, #252542 100%);
            border: 1px solid rgba(0,212,255,0.3);
            border-radius: 8px;
            padding: 10px 14px;
            width: calc(100% - 20px);
            left: 10px;
            font-size: 13px;
            color: #ccc;
            line-height: 1.5;
            z-index: 1000;
            box-shadow: 0 4px 15px rgba(0,0,0,0.4);
            top: 100%;
            margin-top: 5px;
            white-space: normal;
        }
        .info-tooltip.active {
            display: block;
        }
        /* Desktop: show on hover */
        @media (hover: hover) {
            .info-wrapper:hover .info-tooltip {
                display: block;
            }
        }
        .info-wrapper {
            position: static;
        }
        .settings-row:has(.info-wrapper) {
            position: relative;
        }

        /* Range Slider */
        .range-container {
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .range-slider {
            -webkit-appearance: none;
            flex: 1;
            height: 8px;
            border-radius: 10px;
            background: rgba(255,255,255,0.1);
            outline: none;
        }
        .range-slider::-webkit-slider-thumb {
            -webkit-appearance: none;
            width: 20px;
            height: 20px;
            border-radius: 50%;
            background: linear-gradient(135deg, #00d4ff, #00ff88);
            cursor: pointer;
            transition: 0.2s;
            box-shadow: 0 2px 6px rgba(0,212,255,0.4);
        }
        .range-slider::-webkit-slider-thumb:hover {
            transform: scale(1.15);
        }
        .range-slider::-moz-range-thumb {
            width: 20px;
            height: 20px;
            border-radius: 50%;
            background: linear-gradient(135deg, #00d4ff, #00ff88);
            cursor: pointer;
            border: none;
            box-shadow: 0 2px 6px rgba(0,212,255,0.4);
        }
        .range-slider::-webkit-slider-runnable-track {
            border-radius: 10px;
        }
        .range-slider::-moz-range-track {
            border-radius: 10px;
            background: rgba(255,255,255,0.1);
        }
        .range-value {
            min-width: 50px;
            text-align: center;
            font-weight: bold;
            color: #00d4ff;
        }
        .range-slim {
            height: 5px !important;
        }
        .range-slim::-webkit-slider-thumb {
            width: 16px !important;
            height: 16px !important;
        }
        .range-slim::-moz-range-thumb {
            width: 16px !important;
            height: 16px !important;
        }

        /* Settings Blocks */
        .settings-block {
            margin-bottom: 12px;
            padding: 12px;
            border-radius: 8px;
            border: 1px solid;
        }
        .settings-block h4 {
            margin: 0 0 10px 0;
            font-size: 13px;
            font-weight: 600;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .settings-block h4 .icon {
            font-size: 14px;
            display: flex;
            align-items: center;
        }
        .settings-block h4 .icon svg {
            display: block;
        }
        .block-risk {
            background: rgba(255,68,68,0.08);
            border-color: rgba(255,68,68,0.3);
        }
        .block-risk h4 { color: #ff6b6b; }
        .block-strategy {
            background: rgba(0,212,255,0.08);
            border-color: rgba(0,212,255,0.3);
        }
        .block-strategy h4 { color: #00d4ff; }
        .block-filters {
            background: rgba(0,255,136,0.08);
            border-color: rgba(0,255,136,0.3);
        }
        .block-filters h4 { color: #00ff88; }
        .block-signal {
            background: rgba(0,212,255,0.08);
            border-color: rgba(0,212,255,0.3);
        }
        .block-signal h4 { color: #00d4ff; }
        .block-profit {
            background: rgba(255,204,0,0.08);
            border-color: rgba(255,204,0,0.3);
        }
        .block-profit h4 { color: #ffcc00; }
        .block-auto {
            background: rgba(255,204,0,0.08);
            border-color: rgba(255,204,0,0.3);
        }
        .block-auto h4 { color: #ffcc00; }
        .block-basic {
            background: rgba(255,255,255,0.03);
            border-color: rgba(255,255,255,0.1);
        }
        .block-basic h4 { color: #888; }
        .block-sl {
            background: rgba(255,68,68,0.08);
            border-color: rgba(255,68,68,0.3);
        }
        .block-sl h4 { color: #ff6b6b; }
        .block-tp {
            background: rgba(0,255,136,0.08);
            border-color: rgba(0,255,136,0.3);
        }
        .block-tp h4 { color: #00ff88; }
        .block-direction {
            background: rgba(138,43,226,0.08);
            border-color: rgba(138,43,226,0.3);
        }
        .block-direction h4 { color: #ba55d3; }

        /* Compact Settings Grid */
        .settings-row {
            display: flex;
            gap: 10px;
            margin-bottom: 8px;
            align-items: center;
        }
        .settings-row:last-child {
            margin-bottom: 0;
        }
        .setting-compact {
            flex: 1;
            display: flex;
            flex-direction: column;
            gap: 4px;
        }
        .setting-compact label {
            font-size: 11px;
            color: #888;
        }
        .setting-compact input,
        .setting-compact select {
            padding: 8px 10px;
            border-radius: 5px;
            border: 1px solid rgba(255,255,255,0.1);
            background: rgba(0,0,0,0.3);
            color: #e0e0e0;
            font-size: 13px;
            height: 36px;
        }
        .setting-inline {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 10px;
        }
        .setting-inline label {
            font-size: 12px;
            color: #ccc;
        }

        /* Compact Modal */
        .modal-compact {
            max-width: 480px !important;
            max-height: 95vh !important;
            padding: 20px !important;
            padding-top: calc(20px + env(safe-area-inset-top, 0px)) !important;
        }
        .modal-compact .modal-header {
            margin-bottom: 12px;
        }
        .modal-compact .modal-header h3 {
            font-size: 18px;
        }
        .modal-compact .settings-actions {
            margin-top: 12px;
            padding-top: 12px;
            border-top: 1px solid rgba(255,255,255,0.1);
        }

        /* Info Badge */
        .info-badge {
            display: inline-block;
            padding: 3px 8px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 500;
        }
        .info-badge.success {
            background: rgba(0,255,136,0.15);
            color: #00ff88;
        }
        .info-badge.warning {
            background: rgba(255,204,0,0.15);
            color: #ffcc00;
        }

        /* Reset Button */
        .btn-reset {
            background: transparent;
            border: 1px solid rgba(255,255,255,0.2);
            color: #888;
            padding: 6px 10px;
            border-radius: 5px;
            cursor: pointer;
            font-size: 14px;
            transition: 0.2s;
        }
        .btn-reset:hover {
            background: rgba(255,255,255,0.1);
            color: #fff;
        }

        /* Block Header with Reset */
        .block-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 10px;
        }
        .block-header h4 {
            margin: 0 !important;
        }

        /* RR Selector */
        .rr-selector {
            display: flex;
            gap: 5px;
            flex-wrap: wrap;
        }
        .rr-option {
            padding: 5px 10px;
            border: 1px solid rgba(255,255,255,0.2);
            border-radius: 5px;
            cursor: pointer;
            font-size: 12px;
            transition: 0.2s;
            background: rgba(0,0,0,0.3);
            color: #888;
        }
        .rr-option:hover {
            border-color: rgba(0,212,255,0.5);
            color: #ccc;
        }
        .rr-option.active {
            background: linear-gradient(135deg, rgba(0,212,255,0.2), rgba(0,255,136,0.2));
            border-color: #00d4ff;
            color: #00d4ff;
        }

        /* API Stats Panel (Bottom Bar) */
        .api-stats-panel {
            position: fixed;
            bottom: 0;
            left: 0;
            right: 0;
            background: rgba(26, 26, 46, 0.95);
            border-top: 1px solid rgba(0, 212, 255, 0.3);
            padding: 8px 20px;
            padding-bottom: calc(8px + env(safe-area-inset-bottom, 0px));
            font-size: 12px;
            color: #888;
            z-index: 1000;
            backdrop-filter: blur(10px);
        }
        .api-stats-inner {
            max-width: 1600px;
            margin: 0 auto;
            display: flex;
            align-items: center;
            justify-content: space-between;
            min-height: 32px;
        }
        .api-stats-left {
            display: flex;
            align-items: center;
            gap: 15px;
        }
        .api-stats-right {
            display: flex;
            align-items: center;
            gap: 10px;
        }
        @media (max-width: 768px) {
            .api-stats-panel {
                padding: 8px 15px;
                padding-bottom: calc(8px + env(safe-area-inset-bottom, 0px));
            }
            .api-stats-inner {
                min-height: 32px;
                gap: 8px;
            }
            .api-stats-left {
                gap: 10px;
                flex-wrap: wrap;
            }
            .api-stats-right {
                gap: 8px;
            }
            body {
                padding-bottom: calc(48px + env(safe-area-inset-bottom, 0px)) !important;
            }
        }

        /* Responsive */
        @media (max-width: 520px) {
            .modal-compact {
                max-width: 100% !important;
                width: 100% !important;
                max-height: 100vh !important;
                border-radius: 0 !important;
                padding: 15px !important;
                padding-top: calc(env(safe-area-inset-top, 0px) + 20px) !important;
            }
            .settings-row {
                flex-wrap: wrap;
            }
            .setting-compact {
                min-width: calc(50% - 5px);
            }
            .settings-block {
                padding: 10px;
            }
            /* ST1+ST2+ST3 on one line on mobile */
            .block-signal .settings-row:first-child .setting-compact {
                min-width: calc(33.33% - 7px);
                flex: 1;
            }
            .block-signal .settings-row:first-child .setting-compact select {
                font-size: 11px;
                padding: 6px 4px;
            }
            .block-signal .settings-row:first-child .setting-compact label {
                font-size: 11px;
            }
            /* Signal preview smaller on mobile */
            .signal-preview {
                font-size: 11px !important;
                padding: 6px !important;
            }
            /* Volatility row - 3 columns on mobile */
            .row-volatility .setting-compact {
                min-width: calc(33.33% - 7px) !important;
                flex: 1;
            }
            .row-volatility .setting-compact input {
                font-size: 11px;
                padding: 6px 4px;
            }
            .row-volatility .setting-compact label {
                font-size: 11px;
            }
            /* Modal bottom panel - fixed at bottom, same height as logs panel */
            .modal-compact .settings-actions {
                position: fixed;
                bottom: 0;
                left: 0;
                right: 0;
                margin: 0;
                padding: 8px 15px;
                padding-bottom: calc(8px + env(safe-area-inset-bottom, 0px));
                background: rgba(26, 26, 46, 0.98);
                border-top: 1px solid rgba(0, 212, 255, 0.3);
                z-index: 10;
                display: flex;
                gap: 15px;
                justify-content: center;
            }
            .modal-compact .settings-actions .btn {
                min-height: 36px;
                flex: 1;
                max-width: 45%;
                font-size: 14px;
            }
            /* Add padding to content so it's not hidden behind fixed panel */
            .modal-compact {
                padding-bottom: calc(48px + env(safe-area-inset-bottom, 0px)) !important;
            }
            /* Safari browser (not PWA) - additional top padding */
            body.safari-browser .modal-compact {
                padding-top: calc(100px + env(safe-area-inset-top, 0px)) !important;
            }
            /* Safari browser (not PWA) - additional bottom padding for buttons */
            body.safari-browser .modal-compact .settings-actions {
                padding-bottom: calc(90px + env(safe-area-inset-bottom, 0px));
            }
            body.safari-browser .modal-compact {
                padding-bottom: calc(130px + env(safe-area-inset-bottom, 0px)) !important;
            }
        }

        /* Tabs */
        .tabs {
            display: flex;
            gap: 10px;
            margin-bottom: 20px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.1);
            padding-bottom: 10px;
        }

        .tab {
            padding: 10px 20px;
            background: none;
            border: none;
            color: #888;
            cursor: pointer;
            font-size: 14px;
            transition: all 0.3s;
        }

        .tab:hover {
            color: #fff;
        }

        .tab.active {
            color: #00d4ff;
            border-bottom: 2px solid #00d4ff;
        }

        .tab-content {
            display: none;
        }

        .tab-content.active {
            display: block;
        }

        /* Strategy dropdown */
        .strategy-dropdown {
            position: relative;
            display: inline-block;
        }

        .strategy-dropdown-content {
            display: none;
            position: absolute;
            top: 100%;
            left: 0;
            background: rgba(30, 30, 50, 0.98);
            min-width: 200px;
            border-radius: 8px;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.5);
            z-index: 1000;
            border: 1px solid rgba(255, 255, 255, 0.1);
            margin-top: 5px;
        }

        .strategy-dropdown-content.show {
            display: block;
        }

        .strategy-dropdown-content a {
            color: #e0e0e0;
            padding: 12px 16px;
            text-decoration: none;
            display: block;
            transition: all 0.2s;
        }

        .strategy-dropdown-content a:hover {
            background: rgba(0, 212, 255, 0.1);
            color: #00d4ff;
        }

        .strategy-dropdown-content a:first-child {
            border-radius: 8px 8px 0 0;
        }

        .strategy-dropdown-content a:last-child {
            border-radius: 0 0 8px 8px;
        }

        .strategy-dropdown-content a:only-child {
            border-radius: 8px;
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="header-left">
                <div class="status-dot" id="statusDot"></div>
                <img src="/static/logo.png" alt="AILA" style="height: 30px; cursor: pointer;" onclick="location.reload()" title="Refresh">
            </div>
            <div class="header-right">
                <select class="lang-selector" id="langSelector" onchange="changeLanguage(this.value)">
                    <option value="en">EN</option>
                    <option value="ru">RU</option>
                </select>
                <button class="logout-btn" onclick="window.location.href='/logout'" title="Logout">⏻</button>
            </div>
        </header>

        <!-- Tabs Navigation -->
        <div class="tabs">
            <button class="tab active" onclick="showTab('dashboard')" data-i18n="dashboard">Dashboard</button>
            <button class="tab" onclick="showTab('charts')" data-i18n="charts">Charts</button>
            <button class="tab" onclick="showTab('pairs')" data-i18n="tradingPairs">Trading Pairs</button>
            <div class="strategy-dropdown">
                <button class="tab" onclick="toggleStrategyDropdown(event)" data-i18n="strategies">Strategies</button>
                <div class="strategy-dropdown-content" id="strategyDropdown">
                    <a href="#" onclick="openStrategySettings('3ema_pullback'); return false;">3 EMA Pullback</a>
                </div>
            </div>
        </div>

        <!-- Dashboard Tab -->
        <div class="tab-content active" id="tab-dashboard">
            <!-- Stats Row 1: Balance, PnL, Winrate -->
            <div class="stats-row">
                <div class="stat-card">
                    <div class="stat-title" data-i18n="balance">Balance</div>
                    <div class="stat-value" id="balance">--</div>
                </div>
                <div class="stat-card">
                    <div class="stat-title" data-i18n="sessionPnl">Session PnL</div>
                    <div class="stat-value" id="pnl">--</div>
                </div>
                <div class="stat-card">
                    <div class="stat-title" data-i18n="winrate">Winrate</div>
                    <div class="stat-value" id="winrate">--%</div>
                </div>
            </div>

            <!-- Stats Row 2: Open Positions, Active Bots, Closed Trades -->
            <div class="stats-row">
                <div class="stat-card">
                    <div class="stat-title" data-i18n="openPositions">Open Positions</div>
                    <div class="stat-value" id="positions">--</div>
                </div>
                <div class="stat-card">
                    <div class="stat-title" data-i18n="activeBots">Active Bots</div>
                    <div class="stat-value" id="activeBots">0</div>
                </div>
                <div class="stat-card">
                    <div class="stat-title" data-i18n="closedTrades">Closed Trades</div>
                    <div class="stat-value" id="closedTrades">0</div>
                </div>
            </div>

            <!-- Bots Section (integrated into Dashboard) -->
            <div class="bots-section">
                <div class="bots-header">
                    <h3 data-i18n="botsManager">Bots</h3>
                    <button class="btn btn-primary" onclick="showCreateBotModal()" data-i18n="createBot">+ Create Bot</button>
                </div>
                <div class="bots-grid" id="botsGrid">
                    <!-- Bots will be loaded here -->
                </div>
            </div>

            <!-- Open Positions Section -->
            <div class="positions-section" id="positionsSection" style="margin-bottom: 20px; display: none;">
                <div class="bots-header">
                    <h3 data-i18n="openPositions">Open Positions</h3>
                </div>
                <div class="positions-grid" id="positionsGrid" style="display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 15px;">
                    <!-- Positions will be loaded here -->
                </div>
            </div>

        </div>

        <!-- Charts Tab -->
        <div class="tab-content" id="tab-charts">
            <div class="charts-section">
                <div class="charts-header">
                    <h3 data-i18n="priceCharts">Price Charts</h3>
                    <div class="chart-selector">
                        <select id="chartTimeframe" onchange="updateAllCharts()">
                            <option value="1">1m</option>
                            <option value="5">5m</option>
                            <option value="15" selected>15m</option>
                            <option value="60">1h</option>
                            <option value="240">4h</option>
                            <option value="D">1d</option>
                        </select>
                    </div>
                </div>
                <div class="chart-grid" id="chartsGrid">
                    <!-- Charts will be loaded here -->
                </div>
            </div>
        </div>

        <!-- Trading Pairs Tab -->
        <div class="tab-content" id="tab-pairs">
            <div class="pairs-selector">
                <h3 data-i18n="availablePairs">Available Trading Pairs</h3>
                <div class="pairs-search">
                    <input type="text" id="pairsSearch" placeholder="Search pairs..." oninput="filterPairs()">
                </div>
                <div class="pairs-grid" id="pairsGrid">
                    <!-- Pairs will be loaded here -->
                </div>
                <div class="settings-actions" style="margin-top: 15px;">
                    <button class="btn btn-primary" onclick="applySelectedPairs()" data-i18n="applyPairs">Apply Selected Pairs</button>
                </div>
            </div>
        </div>

    </div>

    <!-- API Stats Panel (bottom, aligned with container) -->
    <div id="apiStatsPanel" class="api-stats-panel">
        <div class="api-stats-inner">
            <div class="api-stats-left">
                <span style="color: #00d4ff; font-weight: bold;" id="versionDisplay">v2.1.0</span>
                <div title="Ping to Bybit API" style="display: flex; align-items: center; gap: 4px;">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#00d4ff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <circle cx="12" cy="12" r="10"/>
                        <polyline points="12 6 12 12 16 14"/>
                    </svg>
                    <span id="apiPing" style="color: #00d4ff;">--</span> <span style="color: #888;">ms</span>
                </div>
                <div title="API Requests per 5 sec / Bybit Limit" style="display: flex; align-items: center; gap: 4px;">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#00ff88" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>
                    </svg>
                    <span id="apiRequestsDisplay"><span id="apiRequests" style="color: #00ff88;">--</span>/<span id="apiLimit" style="color: #888;">600</span>/5s</span>
                </div>
            </div>
            <div class="api-stats-right">
                <button id="restartServerBtn" onclick="restartServer()" title="Restart Server" style="
                    background: transparent;
                    border: 1px solid rgba(255, 100, 100, 0.5);
                    border-radius: 4px;
                    padding: 4px 8px;
                    cursor: pointer;
                    color: #ff6666;
                    font-size: 11px;
                    transition: all 0.2s;
                " onmouseover="this.style.background='rgba(255,100,100,0.2)'" onmouseout="this.style.background='transparent'">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align: middle; margin-right: 4px;">
                        <path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8"/>
                        <path d="M21 3v5h-5"/>
                    </svg>
                    Restart
                </button>
                <button id="openLogsBtn" onclick="openLogsModal()" title="Open Logs" style="
                    background: transparent;
                    border: 1px solid rgba(0, 212, 255, 0.5);
                    border-radius: 4px;
                    padding: 4px 8px;
                    cursor: pointer;
                    color: #00d4ff;
                    font-size: 11px;
                    transition: all 0.2s;
                    display: flex;
                    align-items: center;
                    gap: 4px;
                " onmouseover="this.style.background='rgba(0,212,255,0.2)'" onmouseout="this.style.background='transparent'">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                        <polyline points="14 2 14 8 20 8"/>
                        <line x1="16" y1="13" x2="8" y2="13"/>
                        <line x1="16" y1="17" x2="8" y2="17"/>
                        <polyline points="10 9 9 9 8 9"/>
                    </svg>
                    Logs
                </button>
            </div>
        </div>
    </div>

    <div class="toast" id="toast">Logs copied to clipboard!</div>

    <!-- Fullscreen Logs Modal -->
    <div class="logs-modal" id="logsModal">
        <div class="logs-modal-header">
            <div class="logs-modal-left">
                <button class="logs-modal-btn" id="modalAutoScrollBtn" onclick="toggleAutoScroll()" title="Auto-scroll">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M12 5v14"/>
                        <path d="M19 12l-7 7-7-7"/>
                    </svg>
                </button>
            </div>
            <h3 class="logs-modal-title">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align: middle; margin-right: 8px;">
                    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                    <polyline points="14 2 14 8 20 8"/>
                    <line x1="16" y1="13" x2="8" y2="13"/>
                    <line x1="16" y1="17" x2="8" y2="17"/>
                    <polyline points="10 9 9 9 8 9"/>
                </svg>
                <span data-i18n="systemLogs">System Logs</span>
            </h3>
            <div class="logs-modal-right">
                <button class="logs-modal-btn" onclick="clearLogs()" title="Clear logs">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <polyline points="3 6 5 6 21 6"/>
                        <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
                        <line x1="10" y1="11" x2="10" y2="17"/>
                        <line x1="14" y1="11" x2="14" y2="17"/>
                    </svg>
                </button>
                <button class="logs-modal-btn" onclick="copyLogs()" title="Copy logs">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>
                        <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
                    </svg>
                </button>
                <button class="logs-modal-btn logs-modal-close" onclick="closeLogsModal()" title="Close">
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <line x1="18" y1="6" x2="6" y2="18"/>
                        <line x1="6" y1="6" x2="18" y2="18"/>
                    </svg>
                </button>
            </div>
        </div>
        <div class="logs-modal-body">
            <div class="logs" id="logs"></div>
        </div>
    </div>

    <!-- Create Bot Modal -->
    <div class="modal" id="createBotModal">
        <div class="modal-content modal-compact" style="overflow-y: auto;">
            <div class="modal-header">
                <h3 data-i18n="createBot">+ Create Bot</h3>
                <button class="modal-close" onclick="closeModal('createBotModal')">&times;</button>
            </div>

            <!-- Basic Settings Block -->
            <div class="settings-block block-basic">
                <h4><span class="icon"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg></span> <span data-i18n="basicSettings">Basic</span></h4>
                <div class="settings-row">
                    <div class="setting-compact">
                        <label data-i18n="botName">Name</label>
                        <input type="text" id="newBotName" placeholder="My Bot">
                    </div>
                    <div class="setting-compact">
                        <label data-i18n="botMode">Mode</label>
                        <select id="newBotMode" onchange="toggleBotMode('new')">
                            <option value="manual" data-i18n="manualMode">Manual</option>
                            <option value="auto_search" selected data-i18n="autoSearchMode">Auto Search</option>
                        </select>
                    </div>
                </div>
                <div class="settings-row" id="newPairContainer" style="display: none;">
                    <div class="setting-compact" style="position: relative;">
                        <label data-i18n="tradingPair">Trading Pair</label>
                        <input type="text" id="newBotPairSearch" placeholder="BTCUSDT" oninput="filterTradingPairs('new')" onfocus="showPairDropdown('new')" autocomplete="off">
                        <div id="newPairDropdown" class="pair-dropdown" style="display: none; position: absolute; top: 100%; left: 0; right: 0; max-height: 200px; overflow-y: auto; background: #1a1a2e; border: 1px solid #333; border-radius: 5px; z-index: 1000;"></div>
                        <input type="hidden" id="newBotPair" value="BTCUSDT">
                    </div>
                </div>
                <div class="settings-row" id="newMaxPairsContainer">
                    <div class="setting-compact">
                        <label data-i18n="maxTradingPairs">Max Trading Pairs</label>
                        <input type="number" id="newBotMaxPairs" value="1" min="1" max="20">
                    </div>
                </div>
            </div>

            <!-- Risk Management Block -->
            <div class="settings-block block-risk">
                <h4><span class="icon"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg></span> <span data-i18n="riskManagement">Risk Management</span></h4>
                <div class="settings-row">
                    <div class="setting-compact" style="flex: 1;">
                        <label data-i18n="balanceUsage">Balance Usage (%)</label>
                        <div class="range-container">
                            <span id="newBalanceUsageInfo" style="min-width: 90px; font-size: 12px; color: #00ff88;"><span id="newAllocatedBalance">-</span> / <span id="newTotalBalance">-</span></span>
                            <input type="range" class="range-slider range-slim" id="newBotBalanceUsage" value="100" min="1" max="100" oninput="updateSliderValue(this, 'newBalanceValue'); updateBalanceUsageInfo('new'); updateMaxLossInfo('new')">
                            <span class="range-value" id="newBalanceValue">100%</span>
                        </div>
                    </div>
                </div>
                <div class="settings-row">
                    <div class="setting-compact" style="flex: 0 0 auto;">
                        <label data-i18n="maxLossLimit">Max Loss Limit (%)</label>
                        <label class="toggle-switch">
                            <input type="checkbox" id="newBotMaxLossEnabled" onchange="toggleMaxLossSlider('new')">
                            <span class="toggle-slider"></span>
                        </label>
                    </div>
                </div>
                <div class="settings-row" id="newMaxLossContainer" style="display: none;">
                    <div class="setting-compact" style="flex: 1;">
                        <div class="range-container">
                            <span id="newMaxLossInfo" style="min-width: 90px; font-size: 12px; color: #ff6b6b;"><span id="newMaxLossAmount">-</span> USDT</span>
                            <input type="range" class="range-slider range-slim" id="newBotMaxLoss" value="50" min="1" max="100" oninput="updateSliderValue(this, 'newMaxLossValue'); updateMaxLossInfo('new')">
                            <span class="range-value" id="newMaxLossValue">50%</span>
                        </div>
                    </div>
                </div>
                <div class="settings-row">
                    <div class="setting-compact" style="flex: 0 0 auto;">
                        <label data-i18n="consecutiveLossesLimit">Consecutive Losses Limit</label>
                        <label class="toggle-switch">
                            <input type="checkbox" id="newBotConsecutiveLossesEnabled" onchange="toggleConsecutiveLossesSelect('new')">
                            <span class="toggle-slider"></span>
                        </label>
                    </div>
                    <div class="setting-compact" id="newConsecutiveLossesContainer" style="display: none;">
                        <label data-i18n="maxConsecutiveLosses">Max Losses</label>
                        <select id="newBotMaxConsecutiveLosses">
                            <option value="2">2</option>
                            <option value="3" selected>3</option>
                            <option value="4">4</option>
                            <option value="5">5</option>
                            <option value="6">6</option>
                            <option value="7">7</option>
                            <option value="8">8</option>
                            <option value="9">9</option>
                            <option value="10">10</option>
                        </select>
                    </div>
                    <div class="setting-compact" id="newCooldownContainer" style="display: none;">
                        <label data-i18n="cooldownMinutes">Cooldown (min)</label>
                        <select id="newBotCooldownMinutes">
                            <option value="5">5</option>
                            <option value="10">10</option>
                            <option value="15">15</option>
                            <option value="30" selected>30</option>
                            <option value="60">60</option>
                            <option value="120">120</option>
                        </select>
                    </div>
                </div>
            </div>

            <!-- Strategy Block -->
            <div class="settings-block block-strategy">
                <h4><span class="icon"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg></span> <span data-i18n="strategySettings">Strategy</span></h4>
                <div class="settings-row">
                    <div class="setting-compact">
                        <label data-i18n="timeframe">Timeframe</label>
                        <select id="newBotTimeframe">
                            <option value="1m" selected>1m</option>
                            <option value="3m">3m</option>
                            <option value="5m">5m</option>
                            <option value="15m">15m</option>
                            <option value="30m">30m</option>
                            <option value="1h">1h</option>
                            <option value="4h">4h</option>
                            <option value="1d">1d</option>
                        </select>
                    </div>
                    <div class="setting-compact">
                        <label data-i18n="leverage">Leverage</label>
                        <select id="newBotLeverage" onchange="updateDepositInfo('new')">
                            <option value="1">1x</option>
                            <option value="2">2x</option>
                            <option value="5">5x</option>
                            <option value="10" selected>10x</option>
                            <option value="20">20x</option>
                            <option value="50">50x</option>
                            <option value="100">100x</option>
                        </select>
                    </div>
                    <div class="setting-compact">
                        <label data-i18n="orderSize">Order (USDT)</label>
                        <input type="number" id="newBotOrderSize" value="10" min="5" max="100000" step="1" oninput="updateDepositInfo('new')">
                    </div>
                    <div class="setting-compact" style="flex: 0.8;">
                        <label>&nbsp;</label>
                        <div id="newDepositInfo" class="info-badge warning" style="height: 36px; display: flex; align-items: center;">
                            <span id="newDepositUsed">1</span> USDT
                        </div>
                    </div>
                </div>
                <div class="settings-row">
                    <div class="setting-compact">
                        <label data-i18n="positionSizingMode">Position Sizing</label>
                        <select id="newBotPositionSizingMode" onchange="toggleRiskPercentInput('new')">
                            <option value="fixed_amount" selected>Fixed USDT</option>
                            <option value="risk_percent">Risk %</option>
                            <option value="kelly">Kelly</option>
                        </select>
                    </div>
                    <div class="setting-compact" id="newRiskPerTradeContainer" style="display: none;">
                        <label data-i18n="riskPerTrade">Risk per Trade (%)</label>
                        <input type="number" id="newBotRiskPerTrade" value="2" min="0.1" max="10" step="0.1">
                    </div>
                    <div class="setting-compact">
                        <label data-i18n="leverageMode">Margin Mode</label>
                        <select id="newBotLeverageMode">
                            <option value="cross">Cross</option>
                            <option value="isolated" selected>Isolated</option>
                        </select>
                    </div>
                </div>
            </div>

            <!-- Take Profit Block -->
            <div class="settings-block block-profit" id="newTpBlock">
                <h4><span class="icon"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="1" x2="12" y2="23"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg></span> <span data-i18n="takeProfitSettings">Take Profit</span></h4>
                <div class="settings-row">
                    <div class="setting-compact">
                        <label data-i18n="tpMode">TP Mode</label>
                        <select id="newBotTpMode" onchange="toggleTpOptions('new')">
                            <option value="rr" selected>R:R</option>
                            <option value="fixed">Fix %</option>
                        </select>
                    </div>
                    <div class="setting-compact" id="newTpRrContainer">
                        <label data-i18n="tpRatio">Take Profit (R:R)</label>
                        <select id="newBotTpRatio">
                            <option value="1">1:1</option>
                            <option value="2" selected>2:1</option>
                            <option value="3">3:1</option>
                            <option value="4">4:1</option>
                            <option value="5">5:1</option>
                            <option value="6">6:1</option>
                            <option value="7">7:1</option>
                            <option value="8">8:1</option>
                            <option value="9">9:1</option>
                            <option value="10">10:1</option>
                        </select>
                    </div>
                    <div class="setting-compact" id="newTpFixedContainer" style="display: none;">
                        <label data-i18n="tpPercent">TP %</label>
                        <input type="number" id="newBotTpPercent" value="2" min="0.5" max="50" step="0.5">
                    </div>
                </div>
                <div class="settings-row">
                    <div class="setting-inline" style="flex: 0 0 auto;">
                        <label data-i18n="trailingTp" style="min-width: 80px;">Trailing TP</label>
                        <label class="toggle-switch">
                            <input type="checkbox" id="newBotTrailingTpEnabled" onchange="toggleTrailingTpOptions('new')" checked>
                            <span class="toggle-slider"></span>
                        </label>
                        <span class="info-wrapper">
                            <span class="info-icon" onclick="toggleInfo(this)">i</span>
                            <span class="info-tooltip" data-i18n-tooltip="trailingTpInfo">No fixed TP, follows trend. ST Line: exits on reversal. Trailing %: moves TP as price approaches.</span>
                        </span>
                    </div>
                </div>
                <div class="settings-row" id="newTrailingTpOptionsRow" style="display: flex;">
                    <div class="setting-compact" id="newTrailingTpModeContainer">
                        <label data-i18n="trailingTpMode">Mode</label>
                        <select id="newBotTrailingTpMode" onchange="toggleTrailingTpMode('new')">
                            <option value="st_line" selected>ST Line</option>
                            <option value="trailing_percent">Trailing %</option>
                        </select>
                    </div>
                    <div class="setting-compact" id="newTrailingTpStLineContainer">
                        <label data-i18n="trailingTpStLine">ST Line</label>
                        <select id="newBotTrailingTpStLine">
                            <option value="1" selected data-i18n="fast">Fast</option>
                            <option value="2" data-i18n="mediumSpeed">Medium</option>
                            <option value="3" data-i18n="slowSpeed">Slow</option>
                        </select>
                    </div>
                    <div class="setting-compact" id="newTrailingTpActivationContainer" style="display: none;">
                        <label data-i18n="trailingTpActivation">Activation %</label>
                        <input type="number" id="newBotTrailingTpActivation" value="0.5" min="0.1" max="2" step="0.1">
                    </div>
                    <div class="setting-compact" id="newTrailingTpStepContainer" style="display: none;">
                        <label data-i18n="trailingTpStep">Step %</label>
                        <input type="number" id="newBotTrailingTpStep" value="1.0" min="0.5" max="3" step="0.1">
                    </div>
                </div>
                <div class="settings-row">
                    <div class="setting-inline" style="flex: 0 0 auto;">
                        <label data-i18n="partialTp" style="min-width: 80px;">Partial TP</label>
                        <label class="toggle-switch">
                            <input type="checkbox" id="newBotPartialTpEnabled" checked onchange="togglePartialTpOptions('new')">
                            <span class="toggle-slider"></span>
                        </label>
                        <span class="info-wrapper">
                            <span class="info-icon" onclick="toggleInfo(this)">i</span>
                            <span class="info-tooltip" data-i18n-tooltip="partialTpInfo">Closes part of position at TP1, moves SL to TP1/Entry to lock profit on remaining.</span>
                        </span>
                    </div>
                </div>
                <div class="settings-row" id="newPartialTpOptionsRow">
                    <div class="setting-compact" id="newPartialTpCloseContainer">
                        <label data-i18n="partialTpClose">Close %</label>
                        <select id="newBotPartialTpClose">
                            <option value="25">25%</option>
                            <option value="50" selected>50%</option>
                            <option value="75">75%</option>
                        </select>
                    </div>
                    <div class="setting-compact" id="newPartialTpSlMoveContainer">
                        <label data-i18n="partialTpSlMove">SL Move</label>
                        <select id="newBotPartialTpSlMove" onchange="togglePartialTpOffset('new')">
                            <option value="tp1" selected>TP1</option>
                            <option value="entry">Entry</option>
                        </select>
                    </div>
                    <div class="setting-compact" id="newPartialTpOffsetContainer">
                        <label data-i18n="partialTpOffset">Offset %</label>
                        <input type="number" id="newBotPartialTpOffset" value="0.2" min="0.1" max="1" step="0.1">
                    </div>
                </div>
            </div>

            <!-- Stop Loss Block -->
            <div class="settings-block block-risk" id="newSlBlock">
                <h4><span class="icon"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="4.93" y1="4.93" x2="19.07" y2="19.07"/></svg></span> <span data-i18n="stopLossSettings">Stop Loss</span></h4>
                <div class="settings-row">
                    <div class="setting-compact">
                        <label data-i18n="slMode">SL Mode</label>
                        <select id="newBotSlMode" onchange="toggleSlOptions('new')">
                            <option value="supertrend_line" selected>ST Line</option>
                            <option value="fixed_percent">Fixed %</option>
                            <option value="atr">ATR</option>
                        </select>
                    </div>
                    <div class="setting-compact" id="newSlLineContainer">
                        <label data-i18n="slLine">SL Line</label>
                        <select id="newBotSlLine" style="min-width: 120px;">
                            <option value="1" data-i18n="fast">Fast</option>
                            <option value="2" selected data-i18n="mediumSpeed">Medium</option>
                            <option value="3" data-i18n="slowSpeed">Slow</option>
                        </select>
                    </div>
                    <div class="setting-compact" id="newSlPercentContainer" style="display: none;">
                        <label data-i18n="slPercent">SL %</label>
                        <input type="number" id="newBotSlPercent" value="2" min="0.5" max="10" step="0.5" style="min-width: 120px;">
                    </div>
                    <div class="setting-compact" id="newSlAtrContainer" style="display: none;">
                        <label data-i18n="slAtrMult">ATR Mult</label>
                        <input type="number" id="newBotSlAtrMult" value="1.5" min="0.5" max="5" step="0.1" style="min-width: 120px;">
                    </div>
                </div>
                <div class="settings-row">
                    <div class="setting-inline" style="flex: 0 0 auto;">
                        <label data-i18n="trailingSl" style="min-width: 80px;">Trailing SL</label>
                        <label class="toggle-switch">
                            <input type="checkbox" id="newBotTrailingEnabled" checked onchange="toggleTrailingOptions('new')">
                            <span class="toggle-slider"></span>
                        </label>
                        <span class="info-wrapper">
                            <span class="info-icon" onclick="toggleInfo(this)">i</span>
                            <span class="info-tooltip" data-i18n-tooltip="trailingSlInfo">SL moves in profit direction. Fix %: by steps. ST Line: follows SuperTrend.</span>
                        </span>
                    </div>
                </div>
                <div class="settings-row" id="newTrailingOptionsRow">
                    <div class="setting-compact" id="newTrailingModeContainer">
                        <label data-i18n="trailingMode">Mode</label>
                        <select id="newBotTrailingMode" onchange="toggleTrailingMode('new')">
                            <option value="fix_percent">Fix %</option>
                            <option value="st_line" selected>ST Line</option>
                        </select>
                    </div>
                    <div class="setting-compact" id="newTrailingActivationContainer" style="display: none;">
                        <label data-i18n="trailingActivation">Activation %</label>
                        <input type="number" id="newBotTrailingActivation" value="1.0" min="0.5" max="5" step="0.1">
                    </div>
                    <div class="setting-compact" id="newTrailingStepContainer" style="display: none;">
                        <label data-i18n="trailingStep">Step %</label>
                        <input type="number" id="newBotTrailingStep" value="0.5" min="0.1" max="2" step="0.1">
                    </div>
                    <div class="setting-compact" id="newTrailingStLineContainer">
                        <label data-i18n="trailingStLine">ST Line</label>
                        <select id="newBotTrailingStLine">
                            <option value="1" selected data-i18n="fast">Fast</option>
                            <option value="2" data-i18n="mediumSpeed">Medium</option>
                            <option value="3" data-i18n="slowSpeed">Slow</option>
                        </select>
                    </div>
                    <div class="setting-compact" id="newTrailingConfirmContainer">
                        <label data-i18n="trailingConfirm">Confirm Candles</label>
                        <select id="newBotTrailingConfirm">
                            <option value="1" selected>1</option>
                            <option value="2">2</option>
                            <option value="3">3</option>
                        </select>
                    </div>
                </div>
            </div>

            <!-- Signal Entry Block -->
            <div class="settings-block block-signal">
                <h4><span class="icon"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg></span> <span data-i18n="signalEntry">Signal Entry</span></h4>

                <!-- Strategy Selection -->
                <div class="settings-row" style="margin-bottom: 10px;">
                    <div class="setting-inline" style="flex: 1; padding: 8px; background: rgba(0,212,255,0.1); border-radius: 5px; border: 2px solid transparent;" id="newStrategySupertrend">
                        <label style="font-weight: bold; color: #00d4ff;">SuperTrend</label>
                        <label class="toggle-switch">
                            <input type="checkbox" id="newBotStrategySupertrendEnabled" checked onchange="toggleStrategy('new', 'supertrend')">
                            <span class="toggle-slider"></span>
                        </label>
                    </div>
                    <div class="setting-inline" style="flex: 1; padding: 8px; background: rgba(255,255,255,0.05); border-radius: 5px; border: 2px solid transparent;" id="newStrategyHeikinAshi">
                        <label style="font-weight: bold; color: #888;">Heikin Ashi</label>
                        <label class="toggle-switch">
                            <input type="checkbox" id="newBotStrategyHeikinAshiEnabled" onchange="toggleStrategy('new', 'heikinashi')">
                            <span class="toggle-slider"></span>
                        </label>
                    </div>
                </div>

                <!-- SuperTrend Settings -->
                <div id="newSupertrendSettings">
                    <div class="settings-row">
                        <div class="setting-compact">
                            <label>ST1 (Fast)</label>
                            <select id="newBotSt1Role" onchange="updateSignalPreview('new')">
                                <option value="off">— Off</option>
                                <option value="confirm" selected>● Conf</option>
                                <option value="trigger">◎ Trig</option>
                            </select>
                        </div>
                        <div class="setting-compact">
                            <label>ST2 (Medium)</label>
                            <select id="newBotSt2Role" onchange="updateSignalPreview('new')">
                                <option value="off">— Off</option>
                                <option value="confirm" selected>● Conf</option>
                                <option value="trigger">◎ Trig</option>
                            </select>
                        </div>
                        <div class="setting-compact">
                            <label>ST3 (Slow)</label>
                            <select id="newBotSt3Role" onchange="updateSignalPreview('new')">
                                <option value="off">— Off</option>
                                <option value="confirm">● Conf</option>
                                <option value="trigger" selected>◎ Trig</option>
                            </select>
                        </div>
                    </div>
                    <div class="settings-row">
                        <div class="setting-compact">
                            <label data-i18n="triggerConfirmCandles">Trigger Confirm</label>
                            <select id="newBotTriggerConfirmCandles">
                                <option value="1" selected data-i18n="candle1">1 candle</option>
                                <option value="2" data-i18n="candles2">2 candles</option>
                                <option value="3" data-i18n="candles3">3 candles</option>
                            </select>
                        </div>
                        <div class="signal-preview" id="newSignalPreview" style="flex: 2; padding: 8px; background: rgba(0,212,255,0.1); border-radius: 5px; font-size: 12px;">
                            <div id="signalFormulaPreview" style="color: #888;">ST1● + ST2● + ST3◎</div>
                            <div id="signalDescPreview" style="color: #00d4ff;">Entry when ST3 reverses</div>
                        </div>
                    </div>
                </div>

                <!-- Heikin Ashi Settings -->
                <div id="newHeikinAshiSettings" style="display: none;">
                    <!-- Entry/Exit Settings -->
                    <div class="settings-row">
                        <div class="setting-compact">
                            <label data-i18n="haEntryConfirm">Entry Confirm</label>
                            <select id="newBotHaEntryCandles">
                                <option value="1">1</option>
                                <option value="2" selected>2</option>
                                <option value="3">3</option>
                            </select>
                            <span style="color: #888; font-size: 11px;" data-i18n="candles">candles</span>
                        </div>
                    </div>
                    <div class="settings-row" style="align-items: center;">
                        <div class="setting-inline" style="flex: 1;">
                            <label data-i18n="haExitOnColorChange">Exit on Color Change</label>
                            <label class="toggle-switch">
                                <input type="checkbox" id="newBotHaExitOnColorChange" checked onchange="toggleHaExitSettings('new')">
                                <span class="toggle-slider"></span>
                            </label>
                        </div>
                    </div>
                    <div class="settings-row" id="newHaExitConfirmRow">
                        <div class="setting-compact">
                            <label data-i18n="haExitConfirm">Exit Confirm</label>
                            <select id="newBotHaExitCandles">
                                <option value="1" selected>1</option>
                                <option value="2">2</option>
                                <option value="3">3</option>
                            </select>
                            <span style="color: #888; font-size: 11px;" data-i18n="candles">candles</span>
                        </div>
                    </div>

                    <!-- EMA Filter -->
                    <div class="settings-row" style="align-items: center; margin-top: 10px; padding-top: 10px; border-top: 1px solid rgba(255,255,255,0.1);">
                        <div class="setting-inline" style="flex: 1;">
                            <label data-i18n="haEmaFilter">EMA Filter</label>
                            <label class="toggle-switch">
                                <input type="checkbox" id="newBotHaEmaEnabled" checked onchange="toggleHaEmaSettings('new')">
                                <span class="toggle-slider"></span>
                            </label>
                        </div>
                    </div>
                    <div class="settings-row" id="newHaEmaSettingsRow">
                        <div class="setting-compact">
                            <label data-i18n="haEmaPeriod">EMA Period</label>
                            <input type="number" id="newBotHaEmaPeriod" value="200" min="20" max="500" style="width: 70px;">
                        </div>
                        <div class="setting-compact">
                            <label data-i18n="haEmaMode">Mode</label>
                            <select id="newBotHaEmaMode">
                                <option value="strict" selected data-i18n="strict">Strict</option>
                            </select>
                        </div>
                    </div>

                    <!-- Volatility Filter -->
                    <div class="settings-row" style="align-items: center; margin-top: 10px; padding-top: 10px; border-top: 1px solid rgba(255,255,255,0.1);">
                        <div class="setting-inline" style="flex: 1;">
                            <label data-i18n="haVolatilityFilter">Volatility Filter (ATR)</label>
                            <label class="toggle-switch">
                                <input type="checkbox" id="newBotHaVolatilityEnabled" checked onchange="toggleHaVolatilitySettings('new')">
                                <span class="toggle-slider"></span>
                            </label>
                        </div>
                    </div>
                    <div class="settings-row" id="newHaVolatilitySettingsRow">
                        <div class="setting-compact">
                            <label data-i18n="haMinAtr">Min ATR %</label>
                            <input type="number" id="newBotHaMinAtr" value="0.5" min="0.1" max="5" step="0.1" style="width: 70px;">
                        </div>
                    </div>

                    <!-- Emergency Stop-Loss (always active) -->
                    <div class="settings-row" style="align-items: center; margin-top: 10px; padding-top: 10px; border-top: 1px solid rgba(255,255,255,0.1);">
                        <div class="setting-compact" style="flex: 1;">
                            <label data-i18n="haEmergencySl">Emergency SL %</label>
                            <input type="number" id="newBotHaEmergencySl" value="2" min="0.5" max="10" step="0.5" style="width: 70px;">
                            <span class="info-wrapper">
                                <span class="info-icon" onclick="toggleInfo(this)">i</span>
                                <span class="info-tooltip" data-i18n-tooltip="haEmergencySlInfo">Always active even with color change exit. Safety net for flash crashes.</span>
                            </span>
                        </div>
                    </div>

                    <!-- Trailing Stop -->
                    <div class="settings-row" style="align-items: center; margin-top: 10px; padding-top: 10px; border-top: 1px solid rgba(255,255,255,0.1);">
                        <div class="setting-inline" style="flex: 1;">
                            <label data-i18n="haTrailingStop">Trailing Stop</label>
                            <label class="toggle-switch">
                                <input type="checkbox" id="newBotHaTrailingEnabled" checked onchange="toggleHaTrailingSettings('new')">
                                <span class="toggle-slider"></span>
                            </label>
                        </div>
                    </div>
                    <div class="settings-row" id="newHaTrailingSettingsRow">
                        <div class="setting-compact">
                            <label data-i18n="haTrailingActivation">Activation %</label>
                            <input type="number" id="newBotHaTrailingActivation" value="1" min="0.5" max="5" step="0.5" style="width: 70px;">
                        </div>
                        <div class="setting-compact">
                            <label data-i18n="haTrailingDistance">Distance %</label>
                            <input type="number" id="newBotHaTrailingDistance" value="0.5" min="0.1" max="3" step="0.1" style="width: 70px;">
                        </div>
                    </div>

                    <div class="signal-preview" style="padding: 8px; background: rgba(255,170,0,0.1); border-radius: 5px; font-size: 12px; margin-top: 10px;">
                        <div style="color: #ffaa00;" data-i18n="haDescription">🟢 LONG: Green candle confirmed | 🔴 SHORT: Red candle confirmed</div>
                    </div>
                </div>
            </div>

            <!-- Signal Filters Block -->
            <div class="settings-block block-filters">
                <h4><span class="icon"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/></svg></span> <span data-i18n="signalFilters">Signal Filters</span></h4>
                <div class="settings-row" style="align-items: center;">
                    <div class="setting-inline" style="flex: 1;">
                        <label data-i18n="emaFilter">EMA Filter</label>
                        <label class="toggle-switch">
                            <input type="checkbox" id="newBotEmaEnabled" checked onchange="toggleEmaMode('new')">
                            <span class="toggle-slider"></span>
                        </label>
                    </div>
                </div>
                <div class="settings-row" id="newEmaModeContainer">
                    <div class="setting-compact">
                        <label data-i18n="emaMode">EMA Filter Mode</label>
                        <select id="newBotEmaMode">
                            <option value="strict" selected data-i18n="strict">Strict</option>
                            <option value="soft" data-i18n="soft">Soft 50%</option>
                        </select>
                    </div>
                </div>
            </div>

            <!-- Asset Filters (only for auto mode) -->
            <div id="newAssetFiltersContainer">
                <div class="settings-block block-auto">
                    <div class="block-header">
                        <h4><span class="icon"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/></svg></span> <span data-i18n="assetFilters">Asset Filters</span></h4>
                        <label class="toggle-switch">
                            <input type="checkbox" id="newBotAssetFiltersEnabled" checked onchange="toggleAssetFiltersInputs('new')">
                            <span class="toggle-slider"></span>
                        </label>
                    </div>
                    <div id="newAssetFiltersInputs">
                        <div class="settings-row">
                            <div class="setting-compact">
                                <label data-i18n="volume24hMin">Vol 24h Min</label>
                                <div class="input-clearable">
                                    <button type="button" class="input-clear-btn" onclick="clearInput(this)">&times;</button>
                                    <input type="text" id="newBotMinVolume" value="3 000 000" oninput="formatMoneyInput(this)" onblur="handleEmptyInput(this)">
                                </div>
                            </div>
                            <div class="setting-compact">
                                <label data-i18n="volume24hMax">Vol 24h Max</label>
                                <div class="input-clearable">
                                    <button type="button" class="input-clear-btn" onclick="clearInput(this)">&times;</button>
                                    <input type="text" id="newBotMaxVolume" value="-" oninput="formatMoneyInput(this)" onblur="handleEmptyInput(this)">
                                </div>
                            </div>
                        </div>
                        <div class="settings-row">
                            <div class="setting-compact">
                                <label data-i18n="priceMin">Price Min $</label>
                                <div class="input-clearable">
                                    <button type="button" class="input-clear-btn" onclick="clearInput(this)">&times;</button>
                                    <input type="text" id="newBotMinPrice" value="-" oninput="formatMoneyInput(this)" onblur="handleEmptyInput(this)">
                                </div>
                            </div>
                            <div class="setting-compact">
                                <label data-i18n="priceMax">Price Max $</label>
                                <div class="input-clearable">
                                    <button type="button" class="input-clear-btn" onclick="clearInput(this)">&times;</button>
                                    <input type="text" id="newBotMaxPrice" value="-" oninput="formatMoneyInput(this)" onblur="handleEmptyInput(this)">
                                </div>
                            </div>
                        </div>
                        <div class="settings-row">
                            <div class="setting-compact">
                                <label data-i18n="changeMin">Change Min %</label>
                                <div class="input-clearable">
                                    <button type="button" class="input-clear-btn" onclick="clearInput(this)">&times;</button>
                                    <input type="text" id="newBotMinChange" value="-30" oninput="normalizeNumericInput(this)" onblur="handleEmptyInput(this)">
                                </div>
                            </div>
                            <div class="setting-compact">
                                <label data-i18n="changeMax">Change Max %</label>
                                <div class="input-clearable">
                                    <button type="button" class="input-clear-btn" onclick="clearInput(this)">&times;</button>
                                    <input type="text" id="newBotMaxChange" value="20" oninput="normalizeNumericInput(this)" onblur="handleEmptyInput(this)">
                                </div>
                            </div>
                        </div>
                        <div class="settings-row row-volatility">
                            <div class="setting-compact">
                                <label data-i18n="volatilityPeriod">Volat Period</label>
                                <div class="input-clearable">
                                    <button type="button" class="input-clear-btn" onclick="clearInput(this)">&times;</button>
                                    <input type="text" id="newBotVolatilityPeriod" value="12" oninput="normalizeNumericInput(this)" onblur="handleEmptyInput(this)">
                                </div>
                            </div>
                            <div class="setting-compact">
                                <label data-i18n="volatilityMin">Volat Min %</label>
                                <div class="input-clearable">
                                    <button type="button" class="input-clear-btn" onclick="clearInput(this)">&times;</button>
                                    <input type="text" id="newBotMinVolatility" value="0.5" oninput="normalizeNumericInput(this)" onblur="handleEmptyInput(this)">
                                </div>
                            </div>
                            <div class="setting-compact">
                                <label data-i18n="volatilityMax">Volat Max %</label>
                                <div class="input-clearable">
                                    <button type="button" class="input-clear-btn" onclick="clearInput(this)">&times;</button>
                                    <input type="text" id="newBotMaxVolatility" value="3" oninput="normalizeNumericInput(this)" onblur="handleEmptyInput(this)">
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Hidden pair info container -->
            <div id="newPairInfoContainer" style="display: none;">
                <div id="newPairInfo">
                    <span id="newPairMinOrder">-</span>
                    <span id="newPairMaxLeverage">-</span>
                </div>
            </div>

            <div class="settings-actions">
                <button class="btn btn-secondary" onclick="closeModal('createBotModal')" data-i18n="cancel">Cancel</button>
                <button id="createBotBtn" class="btn btn-primary" onclick="createBot()" data-i18n="create">Create</button>
            </div>
        </div>
    </div>

    <!-- Edit Bot Modal -->
    <div class="modal" id="editBotModal">
        <div class="modal-content modal-compact" style="overflow-y: auto;">
            <div class="modal-header">
                <h3 data-i18n="editBot">Edit Bot</h3>
                <button class="modal-close" onclick="closeModal('editBotModal')">&times;</button>
            </div>
            <input type="hidden" id="editBotId">

            <!-- Basic Settings Block -->
            <div class="settings-block block-basic">
                <h4><span class="icon"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg></span> <span data-i18n="basicSettings">Basic</span></h4>
                <div class="settings-row">
                    <div class="setting-compact">
                        <label data-i18n="botName">Name</label>
                        <input type="text" id="editBotName">
                    </div>
                    <div class="setting-compact">
                        <label data-i18n="botMode">Mode</label>
                        <select id="editBotMode" onchange="toggleBotMode('edit')">
                            <option value="manual" data-i18n="manualMode">Manual</option>
                            <option value="auto_search" data-i18n="autoSearchMode">Auto Search</option>
                        </select>
                    </div>
                </div>
                <div class="settings-row" id="editPairContainer">
                    <div class="setting-compact" style="position: relative;">
                        <label data-i18n="tradingPair">Trading Pair</label>
                        <input type="text" id="editBotPairSearch" placeholder="BTCUSDT" oninput="filterTradingPairs('edit')" onfocus="showPairDropdown('edit')" autocomplete="off">
                        <div id="editPairDropdown" class="pair-dropdown" style="display: none; position: absolute; top: 100%; left: 0; right: 0; max-height: 200px; overflow-y: auto; background: #1a1a2e; border: 1px solid #333; border-radius: 5px; z-index: 1000;"></div>
                        <input type="hidden" id="editBotPair" value="">
                    </div>
                </div>
                <div class="settings-row" id="editMaxPairsContainer" style="display: none;">
                    <div class="setting-compact">
                        <label data-i18n="maxTradingPairs">Max Trading Pairs</label>
                        <input type="number" id="editBotMaxPairs" value="1" min="1" max="20">
                    </div>
                </div>
            </div>

            <!-- Risk Management Block -->
            <div class="settings-block block-risk">
                <h4><span class="icon"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg></span> <span data-i18n="riskManagement">Risk Management</span></h4>
                <div class="settings-row">
                    <div class="setting-compact" style="flex: 1;">
                        <label data-i18n="balanceUsage">Balance Usage (%)</label>
                        <div class="range-container">
                            <span id="editBalanceUsageInfo" style="min-width: 90px; font-size: 12px; color: #00ff88;"><span id="editAllocatedBalance">-</span> / <span id="editTotalBalance">-</span></span>
                            <input type="range" class="range-slider range-slim" id="editBotBalanceUsage" value="100" min="1" max="100" oninput="updateSliderValue(this, 'editBalanceValue'); updateBalanceUsageInfo('edit'); updateMaxLossInfo('edit')">
                            <span class="range-value" id="editBalanceValue">100%</span>
                        </div>
                    </div>
                </div>
                <div class="settings-row">
                    <div class="setting-compact" style="flex: 0 0 auto;">
                        <label data-i18n="maxLossLimit">Max Loss Limit (%)</label>
                        <label class="toggle-switch">
                            <input type="checkbox" id="editBotMaxLossEnabled" onchange="toggleMaxLossSlider('edit')">
                            <span class="toggle-slider"></span>
                        </label>
                    </div>
                </div>
                <div class="settings-row" id="editMaxLossContainer" style="display: none;">
                    <div class="setting-compact" style="flex: 1;">
                        <div class="range-container">
                            <span id="editMaxLossInfo" style="min-width: 90px; font-size: 12px; color: #ff6b6b;"><span id="editMaxLossAmount">-</span> USDT</span>
                            <input type="range" class="range-slider range-slim" id="editBotMaxLoss" value="50" min="1" max="100" oninput="updateSliderValue(this, 'editMaxLossValue'); updateMaxLossInfo('edit')">
                            <span class="range-value" id="editMaxLossValue">50%</span>
                        </div>
                    </div>
                </div>
                <div class="settings-row">
                    <div class="setting-compact" style="flex: 0 0 auto;">
                        <label data-i18n="consecutiveLossesLimit">Consecutive Losses Limit</label>
                        <label class="toggle-switch">
                            <input type="checkbox" id="editBotConsecutiveLossesEnabled" onchange="toggleConsecutiveLossesSelect('edit')">
                            <span class="toggle-slider"></span>
                        </label>
                    </div>
                    <div class="setting-compact" id="editConsecutiveLossesContainer" style="display: none;">
                        <label data-i18n="maxConsecutiveLosses">Max Losses</label>
                        <select id="editBotMaxConsecutiveLosses">
                            <option value="2">2</option>
                            <option value="3" selected>3</option>
                            <option value="4">4</option>
                            <option value="5">5</option>
                            <option value="6">6</option>
                            <option value="7">7</option>
                            <option value="8">8</option>
                            <option value="9">9</option>
                            <option value="10">10</option>
                        </select>
                    </div>
                    <div class="setting-compact" id="editCooldownContainer" style="display: none;">
                        <label data-i18n="cooldownMinutes">Cooldown (min)</label>
                        <select id="editBotCooldownMinutes">
                            <option value="5">5</option>
                            <option value="10">10</option>
                            <option value="15">15</option>
                            <option value="30" selected>30</option>
                            <option value="60">60</option>
                            <option value="120">120</option>
                        </select>
                    </div>
                </div>
            </div>

            <!-- Strategy Block -->
            <div class="settings-block block-strategy">
                <h4><span class="icon"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg></span> <span data-i18n="strategySettings">Strategy</span></h4>
                <div class="settings-row">
                    <div class="setting-compact">
                        <label data-i18n="timeframe">Timeframe</label>
                        <select id="editBotTimeframe">
                            <option value="1m">1m</option>
                            <option value="3m">3m</option>
                            <option value="5m">5m</option>
                            <option value="15m">15m</option>
                            <option value="30m">30m</option>
                            <option value="1h">1h</option>
                            <option value="4h">4h</option>
                            <option value="1d">1d</option>
                        </select>
                    </div>
                    <div class="setting-compact">
                        <label data-i18n="leverage">Leverage</label>
                        <select id="editBotLeverage" onchange="updateDepositInfo('edit')">
                            <option value="1">1x</option>
                            <option value="2">2x</option>
                            <option value="5">5x</option>
                            <option value="10">10x</option>
                            <option value="20">20x</option>
                            <option value="50">50x</option>
                            <option value="100">100x</option>
                        </select>
                    </div>
                    <div class="setting-compact">
                        <label data-i18n="orderSize">Order (USDT)</label>
                        <input type="number" id="editBotOrderSize" value="10" min="5" step="1" oninput="updateDepositInfo('edit')">
                    </div>
                    <div class="setting-compact" style="flex: 0.8;">
                        <label>&nbsp;</label>
                        <div id="editDepositInfo" class="info-badge warning" style="height: 36px; display: flex; align-items: center;">
                            <span id="editDepositUsed">1</span> USDT
                        </div>
                    </div>
                </div>
                <div class="settings-row">
                    <div class="setting-compact">
                        <label data-i18n="positionSizingMode">Position Sizing</label>
                        <select id="editBotPositionSizingMode" onchange="toggleRiskPercentInput('edit')">
                            <option value="fixed_amount" selected>Fixed USDT</option>
                            <option value="risk_percent">Risk %</option>
                            <option value="kelly">Kelly</option>
                        </select>
                    </div>
                    <div class="setting-compact" id="editRiskPerTradeContainer" style="display: none;">
                        <label data-i18n="riskPerTrade">Risk per Trade (%)</label>
                        <input type="number" id="editBotRiskPerTrade" value="2" min="0.1" max="10" step="0.1">
                    </div>
                    <div class="setting-compact">
                        <label data-i18n="leverageMode">Margin Mode</label>
                        <select id="editBotLeverageMode">
                            <option value="cross" selected>Cross</option>
                            <option value="isolated">Isolated</option>
                        </select>
                    </div>
                </div>
            </div>

            <!-- Take Profit Block -->
            <div class="settings-block block-profit" id="editTpBlock">
                <h4><span class="icon"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="1" x2="12" y2="23"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg></span> <span data-i18n="takeProfitSettings">Take Profit</span></h4>
                <div class="settings-row">
                    <div class="setting-compact">
                        <label data-i18n="tpMode">TP Mode</label>
                        <select id="editBotTpMode" onchange="toggleTpOptions('edit')">
                            <option value="rr">R:R</option>
                            <option value="fixed">Fix %</option>
                        </select>
                    </div>
                    <div class="setting-compact" id="editTpRrContainer">
                        <label data-i18n="tpRatio">Take Profit (R:R)</label>
                        <select id="editBotTpRatio">
                            <option value="1">1:1</option>
                            <option value="2" selected>2:1</option>
                            <option value="3">3:1</option>
                            <option value="4">4:1</option>
                            <option value="5">5:1</option>
                            <option value="6">6:1</option>
                            <option value="7">7:1</option>
                            <option value="8">8:1</option>
                            <option value="9">9:1</option>
                            <option value="10">10:1</option>
                        </select>
                    </div>
                    <div class="setting-compact" id="editTpFixedContainer" style="display: none;">
                        <label data-i18n="tpPercent">TP %</label>
                        <input type="number" id="editBotTpPercent" value="2" min="0.5" max="50" step="0.5">
                    </div>
                </div>
                <div class="settings-row">
                    <div class="setting-inline" style="flex: 0 0 auto;">
                        <label data-i18n="trailingTp" style="min-width: 80px;">Trailing TP</label>
                        <label class="toggle-switch">
                            <input type="checkbox" id="editBotTrailingTpEnabled" onchange="toggleTrailingTpOptions('edit')">
                            <span class="toggle-slider"></span>
                        </label>
                        <span class="info-wrapper">
                            <span class="info-icon" onclick="toggleInfo(this)">i</span>
                            <span class="info-tooltip" data-i18n-tooltip="trailingTpInfo">No fixed TP, follows trend. ST Line: exits on reversal. Trailing %: moves TP as price approaches.</span>
                        </span>
                    </div>
                </div>
                <div class="settings-row" id="editTrailingTpOptionsRow" style="display: none;">
                    <div class="setting-compact" id="editTrailingTpModeContainer">
                        <label data-i18n="trailingTpMode">Mode</label>
                        <select id="editBotTrailingTpMode" onchange="toggleTrailingTpMode('edit')">
                            <option value="st_line" selected>ST Line</option>
                            <option value="trailing_percent">Trailing %</option>
                        </select>
                    </div>
                    <div class="setting-compact" id="editTrailingTpStLineContainer">
                        <label data-i18n="trailingTpStLine">ST Line</label>
                        <select id="editBotTrailingTpStLine">
                            <option value="1" data-i18n="fast">Fast</option>
                            <option value="2" selected data-i18n="mediumSpeed">Medium</option>
                            <option value="3" data-i18n="slowSpeed">Slow</option>
                        </select>
                    </div>
                    <div class="setting-compact" id="editTrailingTpActivationContainer" style="display: none;">
                        <label data-i18n="trailingTpActivation">Activation %</label>
                        <input type="number" id="editBotTrailingTpActivation" value="0.5" min="0.1" max="2" step="0.1">
                    </div>
                    <div class="setting-compact" id="editTrailingTpStepContainer" style="display: none;">
                        <label data-i18n="trailingTpStep">Step %</label>
                        <input type="number" id="editBotTrailingTpStep" value="1.0" min="0.5" max="3" step="0.1">
                    </div>
                </div>
                <div class="settings-row">
                    <div class="setting-inline" style="flex: 0 0 auto;">
                        <label data-i18n="partialTp" style="min-width: 80px;">Partial TP</label>
                        <label class="toggle-switch">
                            <input type="checkbox" id="editBotPartialTpEnabled" checked onchange="togglePartialTpOptions('edit')">
                            <span class="toggle-slider"></span>
                        </label>
                        <span class="info-wrapper">
                            <span class="info-icon" onclick="toggleInfo(this)">i</span>
                            <span class="info-tooltip" data-i18n-tooltip="partialTpInfo">Closes part of position at TP1, moves SL to TP1/Entry to lock profit on remaining.</span>
                        </span>
                    </div>
                </div>
                <div class="settings-row" id="editPartialTpOptionsRow">
                    <div class="setting-compact" id="editPartialTpCloseContainer">
                        <label data-i18n="partialTpClose">Close %</label>
                        <select id="editBotPartialTpClose">
                            <option value="25">25%</option>
                            <option value="50" selected>50%</option>
                            <option value="75">75%</option>
                        </select>
                    </div>
                    <div class="setting-compact" id="editPartialTpSlMoveContainer">
                        <label data-i18n="partialTpSlMove">SL Move</label>
                        <select id="editBotPartialTpSlMove" onchange="togglePartialTpOffset('edit')">
                            <option value="tp1" selected>TP1</option>
                            <option value="entry">Entry</option>
                        </select>
                    </div>
                    <div class="setting-compact" id="editPartialTpOffsetContainer">
                        <label data-i18n="partialTpOffset">Offset %</label>
                        <input type="number" id="editBotPartialTpOffset" value="0.2" min="0.1" max="1" step="0.1">
                    </div>
                </div>
            </div>

            <!-- Stop Loss Block -->
            <div class="settings-block block-risk" id="editSlBlock">
                <h4><span class="icon"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="4.93" y1="4.93" x2="19.07" y2="19.07"/></svg></span> <span data-i18n="stopLossSettings">Stop Loss</span></h4>
                <div class="settings-row">
                    <div class="setting-compact">
                        <label data-i18n="slMode">SL Mode</label>
                        <select id="editBotSlMode" onchange="toggleSlOptions('edit')">
                            <option value="supertrend_line">ST Line</option>
                            <option value="fixed_percent">Fixed %</option>
                            <option value="atr">ATR</option>
                        </select>
                    </div>
                    <div class="setting-compact" id="editSlLineContainer">
                        <label data-i18n="slLine">SL Line</label>
                        <select id="editBotSlLine" style="min-width: 120px;">
                            <option value="1" data-i18n="fast">Fast</option>
                            <option value="2" data-i18n="mediumSpeed">Medium</option>
                            <option value="3" data-i18n="slowSpeed">Slow</option>
                        </select>
                    </div>
                    <div class="setting-compact" id="editSlPercentContainer" style="display: none;">
                        <label data-i18n="slPercent">SL %</label>
                        <input type="number" id="editBotSlPercent" value="2" min="0.5" max="10" step="0.5" style="min-width: 120px;">
                    </div>
                    <div class="setting-compact" id="editSlAtrContainer" style="display: none;">
                        <label data-i18n="slAtrMult">ATR Mult</label>
                        <input type="number" id="editBotSlAtrMult" value="1.5" min="0.5" max="5" step="0.1" style="min-width: 120px;">
                    </div>
                </div>
                <div class="settings-row">
                    <div class="setting-inline" style="flex: 0 0 auto;">
                        <label data-i18n="trailingSl" style="min-width: 80px;">Trailing SL</label>
                        <label class="toggle-switch">
                            <input type="checkbox" id="editBotTrailingEnabled" checked onchange="toggleTrailingOptions('edit')">
                            <span class="toggle-slider"></span>
                        </label>
                        <span class="info-wrapper">
                            <span class="info-icon" onclick="toggleInfo(this)">i</span>
                            <span class="info-tooltip" data-i18n-tooltip="trailingSlInfo">SL moves in profit direction. Fix %: by steps. ST Line: follows SuperTrend.</span>
                        </span>
                    </div>
                </div>
                <div class="settings-row" id="editTrailingOptionsRow">
                    <div class="setting-compact" id="editTrailingModeContainer">
                        <label data-i18n="trailingMode">Mode</label>
                        <select id="editBotTrailingMode" onchange="toggleTrailingMode('edit')">
                            <option value="fix_percent" selected>Fix %</option>
                            <option value="st_line">ST Line</option>
                        </select>
                    </div>
                    <div class="setting-compact" id="editTrailingActivationContainer">
                        <label data-i18n="trailingActivation">Activation %</label>
                        <input type="number" id="editBotTrailingActivation" value="1.0" min="0.5" max="5" step="0.1">
                    </div>
                    <div class="setting-compact" id="editTrailingStepContainer">
                        <label data-i18n="trailingStep">Step %</label>
                        <input type="number" id="editBotTrailingStep" value="0.5" min="0.1" max="2" step="0.1">
                    </div>
                    <div class="setting-compact" id="editTrailingStLineContainer" style="display: none;">
                        <label data-i18n="trailingStLine">ST Line</label>
                        <select id="editBotTrailingStLine">
                            <option value="1" data-i18n="fast">Fast</option>
                            <option value="2" selected data-i18n="mediumSpeed">Medium</option>
                            <option value="3" data-i18n="slowSpeed">Slow</option>
                        </select>
                    </div>
                    <div class="setting-compact" id="editTrailingConfirmContainer" style="display: none;">
                        <label data-i18n="trailingConfirm">Confirm Candles</label>
                        <select id="editBotTrailingConfirm">
                            <option value="1" selected>1</option>
                            <option value="2">2</option>
                            <option value="3">3</option>
                        </select>
                    </div>
                </div>
            </div>

            <!-- Signal Entry Block -->
            <div class="settings-block block-signal">
                <h4><span class="icon"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg></span> <span data-i18n="signalEntry">Signal Entry</span></h4>

                <!-- Strategy Selection -->
                <div class="settings-row" style="margin-bottom: 10px;">
                    <div class="setting-inline" style="flex: 1; padding: 8px; background: rgba(0,212,255,0.1); border-radius: 5px; border: 2px solid transparent;" id="editStrategySupertrend">
                        <label style="font-weight: bold; color: #00d4ff;">SuperTrend</label>
                        <label class="toggle-switch">
                            <input type="checkbox" id="editBotStrategySupertrendEnabled" checked onchange="toggleStrategy('edit', 'supertrend')">
                            <span class="toggle-slider"></span>
                        </label>
                    </div>
                    <div class="setting-inline" style="flex: 1; padding: 8px; background: rgba(255,255,255,0.05); border-radius: 5px; border: 2px solid transparent;" id="editStrategyHeikinAshi">
                        <label style="font-weight: bold; color: #888;">Heikin Ashi</label>
                        <label class="toggle-switch">
                            <input type="checkbox" id="editBotStrategyHeikinAshiEnabled" onchange="toggleStrategy('edit', 'heikinashi')">
                            <span class="toggle-slider"></span>
                        </label>
                    </div>
                </div>

                <!-- SuperTrend Settings -->
                <div id="editSupertrendSettings">
                    <div class="settings-row">
                        <div class="setting-compact">
                            <label>ST1 (Fast)</label>
                            <select id="editBotSt1Role" onchange="updateSignalPreview('edit')">
                                <option value="off">— Off</option>
                                <option value="confirm" selected>● Conf</option>
                                <option value="trigger">◎ Trig</option>
                            </select>
                        </div>
                        <div class="setting-compact">
                            <label>ST2 (Medium)</label>
                            <select id="editBotSt2Role" onchange="updateSignalPreview('edit')">
                                <option value="off">— Off</option>
                                <option value="confirm" selected>● Conf</option>
                                <option value="trigger">◎ Trig</option>
                            </select>
                        </div>
                        <div class="setting-compact">
                            <label>ST3 (Slow)</label>
                            <select id="editBotSt3Role" onchange="updateSignalPreview('edit')">
                                <option value="off">— Off</option>
                                <option value="confirm">● Conf</option>
                                <option value="trigger" selected>◎ Trig</option>
                            </select>
                        </div>
                    </div>
                    <div class="settings-row">
                        <div class="setting-compact">
                            <label data-i18n="triggerConfirmCandles">Trigger Confirm</label>
                            <select id="editBotTriggerConfirmCandles">
                                <option value="1" selected data-i18n="candle1">1 candle</option>
                                <option value="2" data-i18n="candles2">2 candles</option>
                                <option value="3" data-i18n="candles3">3 candles</option>
                            </select>
                        </div>
                        <div class="signal-preview" id="editSignalPreview" style="flex: 2; padding: 8px; background: rgba(0,212,255,0.1); border-radius: 5px; font-size: 12px;">
                            <div id="editSignalFormulaPreview" style="color: #888;">ST1● + ST2● + ST3◎</div>
                            <div id="editSignalDescPreview" style="color: #00d4ff;">Entry when ST3 reverses</div>
                        </div>
                    </div>
                </div>

                <!-- Heikin Ashi Settings -->
                <div id="editHeikinAshiSettings" style="display: none;">
                    <!-- Entry/Exit Settings -->
                    <div class="settings-row">
                        <div class="setting-compact">
                            <label data-i18n="haEntryConfirm">Entry Confirm</label>
                            <select id="editBotHaEntryCandles">
                                <option value="1">1</option>
                                <option value="2" selected>2</option>
                                <option value="3">3</option>
                            </select>
                            <span style="color: #888; font-size: 11px;" data-i18n="candles">candles</span>
                        </div>
                    </div>
                    <div class="settings-row" style="align-items: center;">
                        <div class="setting-inline" style="flex: 1;">
                            <label data-i18n="haExitOnColorChange">Exit on Color Change</label>
                            <label class="toggle-switch">
                                <input type="checkbox" id="editBotHaExitOnColorChange" checked onchange="toggleHaExitSettings('edit')">
                                <span class="toggle-slider"></span>
                            </label>
                        </div>
                    </div>
                    <div class="settings-row" id="editHaExitConfirmRow">
                        <div class="setting-compact">
                            <label data-i18n="haExitConfirm">Exit Confirm</label>
                            <select id="editBotHaExitCandles">
                                <option value="1" selected>1</option>
                                <option value="2">2</option>
                                <option value="3">3</option>
                            </select>
                            <span style="color: #888; font-size: 11px;" data-i18n="candles">candles</span>
                        </div>
                    </div>

                    <!-- EMA Filter -->
                    <div class="settings-row" style="align-items: center; margin-top: 10px; padding-top: 10px; border-top: 1px solid rgba(255,255,255,0.1);">
                        <div class="setting-inline" style="flex: 1;">
                            <label data-i18n="haEmaFilter">EMA Filter</label>
                            <label class="toggle-switch">
                                <input type="checkbox" id="editBotHaEmaEnabled" checked onchange="toggleHaEmaSettings('edit')">
                                <span class="toggle-slider"></span>
                            </label>
                        </div>
                    </div>
                    <div class="settings-row" id="editHaEmaSettingsRow">
                        <div class="setting-compact">
                            <label data-i18n="haEmaPeriod">EMA Period</label>
                            <input type="number" id="editBotHaEmaPeriod" value="200" min="20" max="500" style="width: 70px;">
                        </div>
                        <div class="setting-compact">
                            <label data-i18n="haEmaMode">Mode</label>
                            <select id="editBotHaEmaMode">
                                <option value="strict" selected data-i18n="strict">Strict</option>
                            </select>
                        </div>
                    </div>

                    <!-- Volatility Filter -->
                    <div class="settings-row" style="align-items: center; margin-top: 10px; padding-top: 10px; border-top: 1px solid rgba(255,255,255,0.1);">
                        <div class="setting-inline" style="flex: 1;">
                            <label data-i18n="haVolatilityFilter">Volatility Filter (ATR)</label>
                            <label class="toggle-switch">
                                <input type="checkbox" id="editBotHaVolatilityEnabled" checked onchange="toggleHaVolatilitySettings('edit')">
                                <span class="toggle-slider"></span>
                            </label>
                        </div>
                    </div>
                    <div class="settings-row" id="editHaVolatilitySettingsRow">
                        <div class="setting-compact">
                            <label data-i18n="haMinAtr">Min ATR %</label>
                            <input type="number" id="editBotHaMinAtr" value="0.5" min="0.1" max="5" step="0.1" style="width: 70px;">
                        </div>
                    </div>

                    <!-- Emergency Stop-Loss (always active) -->
                    <div class="settings-row" style="align-items: center; margin-top: 10px; padding-top: 10px; border-top: 1px solid rgba(255,255,255,0.1);">
                        <div class="setting-compact" style="flex: 1;">
                            <label data-i18n="haEmergencySl">Emergency SL %</label>
                            <input type="number" id="editBotHaEmergencySl" value="2" min="0.5" max="10" step="0.5" style="width: 70px;">
                            <span class="info-wrapper">
                                <span class="info-icon" onclick="toggleInfo(this)">i</span>
                                <span class="info-tooltip" data-i18n-tooltip="haEmergencySlInfo">Always active even with color change exit. Safety net for flash crashes.</span>
                            </span>
                        </div>
                    </div>

                    <!-- Trailing Stop -->
                    <div class="settings-row" style="align-items: center; margin-top: 10px; padding-top: 10px; border-top: 1px solid rgba(255,255,255,0.1);">
                        <div class="setting-inline" style="flex: 1;">
                            <label data-i18n="haTrailingStop">Trailing Stop</label>
                            <label class="toggle-switch">
                                <input type="checkbox" id="editBotHaTrailingEnabled" checked onchange="toggleHaTrailingSettings('edit')">
                                <span class="toggle-slider"></span>
                            </label>
                        </div>
                    </div>
                    <div class="settings-row" id="editHaTrailingSettingsRow">
                        <div class="setting-compact">
                            <label data-i18n="haTrailingActivation">Activation %</label>
                            <input type="number" id="editBotHaTrailingActivation" value="1" min="0.5" max="5" step="0.5" style="width: 70px;">
                        </div>
                        <div class="setting-compact">
                            <label data-i18n="haTrailingDistance">Distance %</label>
                            <input type="number" id="editBotHaTrailingDistance" value="0.5" min="0.1" max="3" step="0.1" style="width: 70px;">
                        </div>
                    </div>

                    <div class="signal-preview" style="padding: 8px; background: rgba(255,170,0,0.1); border-radius: 5px; font-size: 12px; margin-top: 10px;">
                        <div style="color: #ffaa00;" data-i18n="haDescription">🟢 LONG: Green candle confirmed | 🔴 SHORT: Red candle confirmed</div>
                    </div>
                </div>
            </div>

            <!-- Signal Filters Block -->
            <div class="settings-block block-filters">
                <h4><span class="icon"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/></svg></span> <span data-i18n="signalFilters">Signal Filters</span></h4>
                <div class="settings-row" style="align-items: center;">
                    <div class="setting-inline" style="flex: 1;">
                        <label data-i18n="emaFilter">EMA Filter</label>
                        <label class="toggle-switch">
                            <input type="checkbox" id="editBotEmaEnabled" checked onchange="toggleEmaMode('edit')">
                            <span class="toggle-slider"></span>
                        </label>
                    </div>
                </div>
                <div class="settings-row" id="editEmaModeContainer">
                    <div class="setting-compact">
                        <label data-i18n="emaMode">EMA Filter Mode</label>
                        <select id="editBotEmaMode">
                            <option value="strict" data-i18n="strict">Strict</option>
                            <option value="soft" data-i18n="soft">Soft 50%</option>
                        </select>
                    </div>
                </div>
            </div>

            <!-- Asset Filters (only for auto mode) -->
            <div id="editAssetFiltersContainer" style="display: none;">
                <div class="settings-block block-auto">
                    <div class="block-header">
                        <h4><span class="icon"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/></svg></span> <span data-i18n="assetFilters">Asset Filters</span></h4>
                        <label class="toggle-switch">
                            <input type="checkbox" id="editBotAssetFiltersEnabled" checked onchange="toggleAssetFiltersInputs('edit')">
                            <span class="toggle-slider"></span>
                        </label>
                    </div>
                    <div id="editAssetFiltersInputs">
                        <div class="settings-row">
                            <div class="setting-compact">
                                <label data-i18n="volume24hMin">Vol 24h Min</label>
                                <div class="input-clearable">
                                    <button type="button" class="input-clear-btn" onclick="clearInput(this)">&times;</button>
                                    <input type="text" id="editBotMinVolume" value="3 000 000" oninput="formatMoneyInput(this)" onblur="handleEmptyInput(this)">
                                </div>
                            </div>
                            <div class="setting-compact">
                                <label data-i18n="volume24hMax">Vol 24h Max</label>
                                <div class="input-clearable">
                                    <button type="button" class="input-clear-btn" onclick="clearInput(this)">&times;</button>
                                    <input type="text" id="editBotMaxVolume" value="-" oninput="formatMoneyInput(this)" onblur="handleEmptyInput(this)">
                                </div>
                            </div>
                        </div>
                        <div class="settings-row">
                            <div class="setting-compact">
                                <label data-i18n="priceMin">Price Min $</label>
                                <div class="input-clearable">
                                    <button type="button" class="input-clear-btn" onclick="clearInput(this)">&times;</button>
                                    <input type="text" id="editBotMinPrice" value="-" oninput="formatMoneyInput(this)" onblur="handleEmptyInput(this)">
                                </div>
                            </div>
                            <div class="setting-compact">
                                <label data-i18n="priceMax">Price Max $</label>
                                <div class="input-clearable">
                                    <button type="button" class="input-clear-btn" onclick="clearInput(this)">&times;</button>
                                    <input type="text" id="editBotMaxPrice" value="-" oninput="formatMoneyInput(this)" onblur="handleEmptyInput(this)">
                                </div>
                            </div>
                        </div>
                        <div class="settings-row">
                            <div class="setting-compact">
                                <label data-i18n="changeMin">Change Min %</label>
                                <div class="input-clearable">
                                    <button type="button" class="input-clear-btn" onclick="clearInput(this)">&times;</button>
                                    <input type="text" id="editBotMinChange" value="-30" oninput="normalizeNumericInput(this)" onblur="handleEmptyInput(this)">
                                </div>
                            </div>
                            <div class="setting-compact">
                                <label data-i18n="changeMax">Change Max %</label>
                                <div class="input-clearable">
                                    <button type="button" class="input-clear-btn" onclick="clearInput(this)">&times;</button>
                                    <input type="text" id="editBotMaxChange" value="20" oninput="normalizeNumericInput(this)" onblur="handleEmptyInput(this)">
                                </div>
                            </div>
                        </div>
                        <div class="settings-row row-volatility">
                            <div class="setting-compact">
                                <label data-i18n="volatilityPeriod">Volat Period</label>
                                <div class="input-clearable">
                                    <button type="button" class="input-clear-btn" onclick="clearInput(this)">&times;</button>
                                    <input type="text" id="editBotVolatilityPeriod" value="12" oninput="normalizeNumericInput(this)" onblur="handleEmptyInput(this)">
                                </div>
                            </div>
                            <div class="setting-compact">
                                <label data-i18n="volatilityMin">Volat Min %</label>
                                <div class="input-clearable">
                                    <button type="button" class="input-clear-btn" onclick="clearInput(this)">&times;</button>
                                    <input type="text" id="editBotMinVolatility" value="0.5" oninput="normalizeNumericInput(this)" onblur="handleEmptyInput(this)">
                                </div>
                            </div>
                            <div class="setting-compact">
                                <label data-i18n="volatilityMax">Volat Max %</label>
                                <div class="input-clearable">
                                    <button type="button" class="input-clear-btn" onclick="clearInput(this)">&times;</button>
                                    <input type="text" id="editBotMaxVolatility" value="3" oninput="normalizeNumericInput(this)" onblur="handleEmptyInput(this)">
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Hidden pair info container -->
            <div id="editPairInfoContainer" style="display: none;">
                <div id="editPairInfo">
                    <span id="editPairMinOrder">-</span>
                    <span id="editPairMaxLeverage">-</span>
                </div>
            </div>

            <div class="settings-actions">
                <button class="btn btn-secondary" onclick="closeModal('editBotModal')" data-i18n="cancel">Cancel</button>
                <div style="display: flex; gap: 10px; align-items: center;">
                    <button class="btn-reset" onclick="resetEditBotForm()" title="Reset">↺</button>
                    <button id="saveEditBotBtn" class="btn btn-primary" onclick="saveEditBot()" data-i18n="save">Save</button>
                </div>
            </div>
        </div>
    </div>

    <!-- Fullscreen Chart Modal -->
    <div class="modal" id="fullscreenChartModal" style="background: rgba(0,0,0,0.95);">
        <div class="modal-content" style="max-width: 95%; width: 95%; height: 90vh; padding: 15px; padding-top: calc(15px + env(safe-area-inset-top, 0px));">
            <div class="modal-header" style="margin-bottom: 10px;">
                <h3 id="fullscreenChartTitle" style="display: flex; align-items: center;">Chart</h3>
                <button class="modal-close" onclick="closeFullscreenChart()">&times;</button>
            </div>
            <div id="fullscreenChartInfo" style="display: flex; justify-content: space-between; align-items: center; padding: 8px 15px; background: rgba(0,255,136,0.1); border-radius: 5px; margin-bottom: 10px; font-family: monospace;">
                <span id="fsPrice" style="color: #00ff88; font-size: 18px; font-weight: bold;">--</span>
                <span id="fsCountdown" style="color: #ffcc00; font-size: 16px;">--:--</span>
                <span id="fsUpdateTime" style="color: #888; font-size: 12px;">Loading...</span>
            </div>
            <div id="fullscreenChartContainer" style="height: calc(100% - 90px); width: 100%;"></div>
        </div>
    </div>

    <!-- Position Chart Modal -->
    <div class="modal" id="positionChartModal" style="background: rgba(0,0,0,0.98);">
        <div class="modal-content" style="max-width: 100%; width: 100%; height: 100vh; padding: 0; padding-top: env(safe-area-inset-top, 0px); border-radius: 0; margin: 0;">
            <!-- Compact Header -->
            <div style="display: flex; justify-content: space-between; align-items: center; padding: 8px 12px; background: #0a0a0a; border-bottom: 1px solid #1a1a1a;">
                <div style="display: flex; align-items: center; gap: 10px;">
                    <h3 id="posChartSymbol" style="margin: 0; font-size: 16px; font-weight: 600; display: flex; align-items: center;">BTCUSDT</h3>
                    <span id="posChartSide" style="padding: 3px 8px; border-radius: 3px; font-weight: bold; font-size: 11px;">LONG</span>
                    <span id="posChartTimeframe" style="color: #666; font-size: 12px;">1h</span>
                </div>
                <div style="display: flex; align-items: center; gap: 12px;">
                    <span id="posChartCurrentPrice" style="font-size: 15px; font-weight: bold; font-family: monospace;">--</span>
                    <div style="text-align: right;">
                        <span id="posChartPnlUsdt" style="font-size: 14px; font-weight: bold;">+0.00</span>
                        <span id="posChartPnlPercent" style="font-size: 11px; margin-left: 4px; opacity: 0.8;">(+0.00%)</span>
                    </div>
                    <button class="modal-close" onclick="closePositionChart()" style="font-size: 24px; padding: 0 8px;">&times;</button>
                </div>
            </div>
            <!-- Price Levels Bar -->
            <div id="posChartPriceInfo" style="display: flex; justify-content: center; gap: 20px; padding: 6px 12px; background: #0d0d0d; font-family: monospace; font-size: 12px; border-bottom: 1px solid #1a1a1a;">
                <span>◆ Entry: <span id="posChartEntry" style="color: #00d4ff;">--</span></span>
                <span>▼ SL: <span id="posChartSL" style="color: #ff4444;">--</span></span>
                <span>▲ TP: <span id="posChartTP" style="color: #00ff88;">--</span></span>
            </div>
            <!-- Chart Container -->
            <div id="positionChartContainer" style="height: calc(100% - 70px); width: 100%; background: #000;"></div>
        </div>
    </div>

    <!-- 3 EMA Pullback Strategy Settings Modal -->
    <div class="modal" id="strategyModal">
        <div class="modal-content modal-compact" style="overflow-y: auto; max-width: 600px; max-height: 90vh;">
            <div class="modal-header">
                <h3 data-i18n="strategy3EMA">3 EMA Pullback Strategy</h3>
                <button class="modal-close" onclick="closeModal('strategyModal')">&times;</button>
            </div>

            <!-- Block 1: Basic Settings -->
            <div class="settings-block block-basic">
                <h4><span class="icon"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg></span> <span data-i18n="basicSettings">Basic</span></h4>
                <div class="settings-row">
                    <div class="setting-compact">
                        <label data-i18n="strategyName">Name</label>
                        <input type="text" id="strategyName" value="3 EMA Scalping">
                    </div>
                    <div class="setting-compact">
                        <label data-i18n="strategyStatus">Status</label>
                        <label class="toggle-switch">
                            <input type="checkbox" id="strategyEnabled">
                            <span class="toggle-slider"></span>
                        </label>
                    </div>
                </div>
                <div class="settings-row">
                    <div class="setting-compact">
                        <label data-i18n="maxPairs">Max Pairs</label>
                        <select id="strategyMaxPairs">
                            <option value="1">1</option>
                            <option value="2">2</option>
                            <option value="3">3</option>
                            <option value="5">5</option>
                            <option value="10">10</option>
                        </select>
                    </div>
                    <div class="setting-compact">
                        <label data-i18n="timeframe">Timeframe</label>
                        <select id="strategyTimeframe">
                            <option value="1m" selected>1m</option>
                            <option value="3m">3m</option>
                            <option value="5m">5m</option>
                            <option value="15m">15m</option>
                        </select>
                    </div>
                </div>
                <div class="settings-row">
                    <div class="setting-compact">
                        <label data-i18n="leverage">Leverage</label>
                        <select id="strategyLeverage">
                            <option value="1">1x</option>
                            <option value="2">2x</option>
                            <option value="3">3x</option>
                            <option value="5">5x</option>
                            <option value="10" selected>10x</option>
                            <option value="15">15x</option>
                            <option value="20">20x</option>
                            <option value="25">25x</option>
                            <option value="50">50x</option>
                        </select>
                    </div>
                    <div class="setting-compact">
                        <label data-i18n="orderSize">Order Size (USDT)</label>
                        <input type="number" id="strategyOrderSize" value="10" min="5" step="1">
                    </div>
                </div>
                <div class="settings-row">
                    <div class="setting-compact">
                        <label data-i18n="positionSizeMode">Position Size Mode</label>
                        <select id="strategyPositionMode">
                            <option value="fixed" data-i18n="fixedUsdt">Fixed USDT</option>
                            <option value="percent" data-i18n="percentBalance">% of Balance</option>
                        </select>
                    </div>
                    <div class="setting-compact">
                        <label data-i18n="marginMode">Margin Mode</label>
                        <select id="strategyMarginMode">
                            <option value="isolated" selected>Isolated</option>
                            <option value="cross">Cross</option>
                        </select>
                    </div>
                </div>
            </div>

            <!-- Block 2: Risk Management -->
            <div class="settings-block block-risk">
                <h4><span class="icon"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg></span> <span data-i18n="riskManagement">Risk Management</span></h4>
                <div class="settings-row">
                    <div class="setting-compact" style="flex: 1;">
                        <label data-i18n="balanceUsage">Balance Usage (%)</label>
                        <div class="range-container">
                            <input type="range" class="range-slider range-slim" id="strategyBalanceUsage" value="30" min="1" max="100" oninput="updateSliderValue(this, 'strategyBalanceValue')">
                            <span class="range-value" id="strategyBalanceValue">30%</span>
                        </div>
                    </div>
                </div>
                <div class="settings-row">
                    <div class="setting-compact">
                        <label data-i18n="riskPerTrade">Risk per Trade (%)</label>
                        <input type="number" id="strategyRiskPerTrade" value="2" min="0.5" max="5" step="0.1">
                    </div>
                </div>
                <div class="settings-row">
                    <div class="setting-compact" style="flex: 0 0 auto;">
                        <label data-i18n="dailyLossLimit">Daily Loss Limit (%)</label>
                        <label class="toggle-switch">
                            <input type="checkbox" id="strategyDailyLossEnabled" checked onchange="toggleStrategyDailyLoss()">
                            <span class="toggle-slider"></span>
                        </label>
                    </div>
                    <div class="setting-compact" id="strategyDailyLossContainer">
                        <input type="number" id="strategyDailyLoss" value="5" min="1" max="50" step="0.5">
                    </div>
                </div>
                <div class="settings-row">
                    <div class="setting-compact" style="flex: 0 0 auto;">
                        <label data-i18n="maxLosingStreak">Max Losing Streak</label>
                        <label class="toggle-switch">
                            <input type="checkbox" id="strategyLosingStreakEnabled" checked onchange="toggleStrategyLosingStreak()">
                            <span class="toggle-slider"></span>
                        </label>
                    </div>
                    <div class="setting-compact" id="strategyLosingStreakContainer">
                        <select id="strategyMaxLosingStreak">
                            <option value="1">1</option>
                            <option value="2">2</option>
                            <option value="3" selected>3</option>
                            <option value="5">5</option>
                            <option value="10">10</option>
                        </select>
                    </div>
                </div>
                <div class="settings-row">
                    <div class="setting-compact" style="flex: 0 0 auto;">
                        <label data-i18n="maxDailyTrades">Max Daily Trades</label>
                        <label class="toggle-switch">
                            <input type="checkbox" id="strategyDailyTradesEnabled" onchange="toggleStrategyDailyTrades()">
                            <span class="toggle-slider"></span>
                        </label>
                    </div>
                    <div class="setting-compact" id="strategyDailyTradesContainer" style="display: none;">
                        <input type="number" id="strategyMaxDailyTrades" value="10" min="1" max="100">
                    </div>
                </div>
            </div>

            <!-- Block 3.1: EMA Parameters -->
            <div class="settings-block block-strategy">
                <h4><span class="icon"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 3v18h18"/><path d="M18 17V9"/><path d="M13 17V5"/><path d="M8 17v-3"/></svg></span> <span data-i18n="emaParams">EMA Parameters</span></h4>
                <div class="settings-row">
                    <div class="setting-compact">
                        <label>EMA Fast</label>
                        <input type="number" id="strategyEmaFast" value="50" min="10" max="100">
                    </div>
                    <div class="setting-compact">
                        <label>EMA Medium</label>
                        <input type="number" id="strategyEmaMedium" value="100" min="50" max="200">
                    </div>
                    <div class="setting-compact">
                        <label>EMA Slow</label>
                        <input type="number" id="strategyEmaSlow" value="150" min="100" max="300">
                    </div>
                </div>
            </div>

            <!-- Block 3.2: Trend Filter -->
            <div class="settings-block block-strategy">
                <h4><span class="icon"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg></span> <span data-i18n="trendFilter">Trend Filter</span></h4>
                <div class="settings-row">
                    <div class="setting-compact">
                        <label data-i18n="minSlope">Min Slope (%)</label>
                        <input type="number" id="strategySlopeMin" value="0.1" min="0.01" max="1" step="0.01">
                    </div>
                    <div class="setting-compact">
                        <label data-i18n="slopeCandles">Candles for Slope</label>
                        <input type="number" id="strategySlopeCandles" value="5" min="3" max="20">
                    </div>
                </div>
                <div class="settings-row">
                    <div class="setting-compact">
                        <label data-i18n="emaDistanceAtr">EMA Distance (ATR mult)</label>
                        <select id="strategyEmaDistanceAtr">
                            <option value="0.5">0.5x</option>
                            <option value="1" selected>1x</option>
                            <option value="1.5">1.5x</option>
                            <option value="2">2x</option>
                            <option value="3">3x</option>
                        </select>
                    </div>
                    <div class="setting-compact">
                        <label data-i18n="atrPeriod">ATR Period</label>
                        <input type="number" id="strategyAtrPeriod" value="14" min="7" max="21">
                    </div>
                </div>
            </div>

            <!-- Block 3.3: Entry Conditions -->
            <div class="settings-block block-strategy">
                <h4><span class="icon"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg></span> <span data-i18n="entryConditions">Entry Conditions</span></h4>
                <div class="settings-row">
                    <div class="setting-compact">
                        <label data-i18n="ema100TouchMode">EMA100 Touch Mode</label>
                        <select id="strategyTouchMode">
                            <option value="low_high" selected data-i18n="lowHighTouch">Low/High touches</option>
                            <option value="close" data-i18n="closeTouch">Close touches</option>
                        </select>
                    </div>
                    <div class="setting-compact">
                        <label data-i18n="touchTolerance">Touch Tolerance (%)</label>
                        <input type="number" id="strategyTouchTolerance" value="0.1" min="0" max="0.5" step="0.01">
                    </div>
                </div>
                <div class="settings-row">
                    <div class="setting-compact">
                        <label data-i18n="forbidEma150Touch">Forbid EMA150 Touch</label>
                        <label class="toggle-switch">
                            <input type="checkbox" id="strategyForbidEma150" checked>
                            <span class="toggle-slider"></span>
                        </label>
                    </div>
                </div>
            </div>

            <!-- Block 3.4: Stop Loss -->
            <div class="settings-block block-sl">
                <h4><span class="icon"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/></svg></span> <span data-i18n="stopLoss">Stop Loss</span></h4>
                <div class="settings-row">
                    <div class="setting-compact">
                        <label data-i18n="slMode">SL Mode</label>
                        <select id="strategySlMode" onchange="toggleStrategySlMode()">
                            <option value="pullback" selected data-i18n="slPullback">Below/Above Pullback</option>
                            <option value="fixed" data-i18n="slFixed">Fixed %</option>
                        </select>
                    </div>
                </div>
                <div class="settings-row" id="strategySlBufferContainer">
                    <div class="setting-compact">
                        <label data-i18n="slBuffer">SL Buffer (%)</label>
                        <input type="number" id="strategySlBuffer" value="0.1" min="0.05" max="0.5" step="0.01">
                    </div>
                </div>
                <div class="settings-row" id="strategySlFixedContainer" style="display: none;">
                    <div class="setting-compact">
                        <label data-i18n="slFixedPct">Fixed SL (%)</label>
                        <input type="number" id="strategySlFixed" value="1" min="0.5" max="5" step="0.1">
                    </div>
                </div>
            </div>

            <!-- Block 3.5: Take Profit -->
            <div class="settings-block block-tp">
                <h4><span class="icon"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="1" x2="12" y2="23"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg></span> <span data-i18n="takeProfit">Take Profit</span></h4>
                <div class="settings-row">
                    <div class="setting-compact">
                        <label data-i18n="tpMode">TP Mode</label>
                        <select id="strategyTpMode" onchange="toggleStrategyTpMode()">
                            <option value="trailing" selected data-i18n="tpTrailing">Trailing</option>
                            <option value="fixed_rr" data-i18n="tpFixedRR">Fixed R:R</option>
                        </select>
                    </div>
                </div>
                <div class="settings-row" id="strategyRrContainer" style="display: none;">
                    <div class="setting-compact">
                        <label data-i18n="rrRatio">R:R Ratio</label>
                        <select id="strategyRrRatio">
                            <option value="1">1:1</option>
                            <option value="1.5" selected>1.5:1</option>
                            <option value="2">2:1</option>
                            <option value="2.5">2.5:1</option>
                            <option value="3">3:1</option>
                            <option value="5">5:1</option>
                        </select>
                    </div>
                </div>
                <div id="strategyTrailingContainer">
                    <div class="settings-row">
                        <div class="setting-compact">
                            <label data-i18n="trailingBe">Move to Breakeven</label>
                            <label class="toggle-switch">
                                <input type="checkbox" id="strategyTrailingBe" checked onchange="toggleStrategyTrailingBe()">
                                <span class="toggle-slider"></span>
                            </label>
                        </div>
                        <div class="setting-compact" id="strategyBeAfterContainer">
                            <label data-i18n="beAfterProfit">BE After Profit (%)</label>
                            <input type="number" id="strategyBeAfterProfit" value="0.3" min="0.1" max="1" step="0.1">
                        </div>
                    </div>
                    <div class="settings-row">
                        <div class="setting-compact">
                            <label data-i18n="trailingMode">Trailing Mode</label>
                            <select id="strategyTrailingMode">
                                <option value="new_high_low" selected data-i18n="trailNewHighLow">On New High/Low</option>
                                <option value="ema50" data-i18n="trailEma50">Follow EMA50</option>
                                <option value="candles" data-i18n="trailCandles">By Candles</option>
                            </select>
                        </div>
                        <div class="setting-compact">
                            <label data-i18n="trailingBuffer">Trail Buffer (%)</label>
                            <input type="number" id="strategyTrailingBuffer" value="0.1" min="0.05" max="0.3" step="0.01">
                        </div>
                    </div>
                </div>
            </div>

            <!-- Block 3.6: Trade Direction -->
            <div class="settings-block block-direction">
                <h4><span class="icon"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"/><polyline points="19 12 12 19 5 12"/></svg></span> <span data-i18n="tradeDirection">Trade Direction</span></h4>
                <div class="settings-row">
                    <div class="setting-compact">
                        <label data-i18n="tradeLong">Trade LONG</label>
                        <label class="toggle-switch">
                            <input type="checkbox" id="strategyTradeLong" checked>
                            <span class="toggle-slider"></span>
                        </label>
                    </div>
                    <div class="setting-compact">
                        <label data-i18n="tradeShort">Trade SHORT</label>
                        <label class="toggle-switch">
                            <input type="checkbox" id="strategyTradeShort" checked>
                            <span class="toggle-slider"></span>
                        </label>
                    </div>
                </div>
            </div>

            <!-- Strategy Status and Controls -->
            <div class="settings-block" style="margin-top: 15px; background: rgba(0,212,255,0.08); border-color: rgba(0,212,255,0.3);">
                <div class="settings-row" style="justify-content: space-between; align-items: center;">
                    <div style="display: flex; align-items: center; gap: 10px;">
                        <span data-i18n="strategyStatus">Strategy Status:</span>
                        <span id="strategyStatusIndicator" style="padding: 4px 12px; border-radius: 12px; font-size: 12px; background: #333; color: #888;">Stopped</span>
                    </div>
                    <div style="display: flex; gap: 10px;">
                        <button class="btn btn-success" id="strategyStartBtn" onclick="startStrategy()" style="padding: 8px 20px;" data-i18n="start">Start</button>
                        <button class="btn btn-danger" id="strategyStopBtn" onclick="stopStrategy()" style="padding: 8px 20px; display: none;" data-i18n="stop">Stop</button>
                    </div>
                </div>
                <div id="strategyPositionsInfo" style="margin-top: 10px; font-size: 12px; color: #888; display: none;">
                    <span data-i18n="openPositions">Open Positions:</span> <span id="strategyPositionsCount">0</span>
                </div>
            </div>

            <!-- Save Button -->
            <div class="modal-footer" style="margin-top: 20px; display: flex; gap: 10px;">
                <button class="btn btn-primary" onclick="saveStrategySettings()" style="flex: 1;" data-i18n="save">Save</button>
                <button class="btn btn-secondary" onclick="closeModal('strategyModal')" style="flex: 1;" data-i18n="cancel">Cancel</button>
            </div>
        </div>
    </div>

    <script>
        // Detect Safari browser (not PWA) for additional modal padding
        (function() {
            const isSafari = /^((?!chrome|android).)*safari/i.test(navigator.userAgent);
            const isStandalone = window.navigator.standalone === true ||
                                 window.matchMedia('(display-mode: standalone)').matches ||
                                 window.matchMedia('(display-mode: fullscreen)').matches;
            const isMobile = /iPhone|iPad|iPod/.test(navigator.userAgent);

            if (isSafari && isMobile && !isStandalone) {
                document.body.classList.add('safari-browser');
            }
        })();

        // Localization
        const i18n = {
            en: {
                connecting: 'Connecting...',
                connected: 'Connected',
                disconnected: 'Disconnected',
                startBot: 'Start Bot',
                stopBot: 'Stop Bot',
                running: 'Running',
                starting: 'Starting...',
                stopping: 'Stopping...',
                settings: 'Settings',
                strategySettings: 'Strategy Settings',
                takeProfitSettings: 'Take Profit',
                stopLossSettings: 'Stop Loss',
                timeframe: 'Timeframe',
                tradingPairs: 'Trading Pairs',
                tradingPair: 'Trading Pair',
                riskPerTrade: 'Risk per Trade (%)',
                tpRatio: 'Take Profit Ratio (R:R)',
                slMode: 'Stop Loss Mode',
                slLine: 'SL SuperTrend Line',
                slPercent: 'SL Fixed Percent (%)',
                slAtrMult: 'SL ATR Multiplier',
                leverage: 'Leverage',
                orderSize: 'Order Size (USDT)',
                positionSizingMode: 'Position Sizing',
                riskPerTrade: 'Risk per Trade (%)',
                leverageMode: 'Margin Mode',
                depositUsed: 'Deposit used:',
                pairInfo: 'Pair Info',
                minOrder: 'Min Order:',
                maxLeverage: 'Max Leverage:',
                maxPositions: 'Max Open Positions',
                emaFilter: 'EMA Filter',
                emaMode: 'EMA Filter Mode',
                strict: 'Strict',
                soft: 'Soft (50% size)',
                trailingSl: 'Trailing SL',
                trailingSlInfo: 'SL moves in profit direction. Fix %: by steps. ST Line: follows SuperTrend.',
                partialTpInfo: 'Closes part of position at TP1, moves SL to TP1/Entry to lock profit on remaining.',
                trailingTpInfo: 'No fixed TP, follows trend. ST Line: exits on reversal. Trailing %: moves TP as price approaches.',
                trailingMode: 'Mode',
                trailingActivation: 'Activation %',
                trailingStep: 'Step %',
                trailingStLine: 'ST Line',
                trailingConfirm: 'Confirm Candles',
                partialTp: 'Partial TP',
                partialTpClose: 'Close %',
                partialTpSlMove: 'SL Move',
                partialTpOffset: 'Offset %',
                trailingTp: 'Trailing TP',
                trailingTpMode: 'Mode',
                trailingTpStLine: 'ST Line',
                trailingTpActivation: 'Activation %',
                trailingTpStep: 'Step %',
                assetFilters: 'Asset Filters',
                volume24hMin: 'Volume 24h Min',
                volume24hMax: 'Volume 24h Max',
                priceMin: 'Price Min $',
                priceMax: 'Price Max $',
                changeMin: 'Change Min %',
                changeMax: 'Change Max %',
                volatilityPeriod: 'Volat Period',
                volatilityMin: 'Volat Min %',
                volatilityMax: 'Volat Max %',
                earlyEntry: 'Early Entry (ST3)',
                tpMode: 'TP Mode',
                tpPercent: 'TP %',
                enabled: 'Enabled',
                disabled: 'Disabled',
                fixedPercent: 'Fixed Percent',
                reset: 'Reset',
                saveSettings: 'Save Settings',
                balance: 'Balance',
                openPositions: 'Open Positions',
                sessionPnl: 'Session PnL',
                winrate: 'Winrate',
                closedTrades: 'Closed Trades',
                activeBots: 'Active Bots',
                liveLogs: 'Live Logs',
                systemLogs: 'System Logs',
                autoScrollOn: 'Auto-scroll ON',
                autoScrollOff: 'Auto-scroll OFF (scroll to bottom to enable)',
                clear: 'Clear',
                copyLogs: 'Copy Logs',
                logsCopied: 'Logs copied to clipboard!',
                copyFailed: 'Failed to copy logs',
                botStarted: 'Bot started successfully!',
                botStopped: 'Bot stopped successfully!',
                failedStart: 'Failed to start',
                failedStop: 'Failed to stop',
                settingsSaved: 'Settings saved! Restart bot to apply.',
                failedSave: 'Failed to save',
                logsCleared: '--- Logs cleared ---',
                // Strategy translations
                strategies: 'Strategies',
                strategy3EMA: '3 EMA Pullback Strategy',
                strategyName: 'Strategy Name',
                strategyStatus: 'Status',
                strategySaved: 'Strategy settings saved!',
                strategyStarted: 'Strategy started!',
                strategyStopped: 'Strategy stopped!',
                maxPairs: 'Max Pairs',
                emaParams: 'EMA Parameters',
                trendFilter: 'Trend Filter',
                minSlope: 'Min Slope (%)',
                slopeCandles: 'Candles for Slope',
                emaDistanceAtr: 'EMA Distance (ATR mult)',
                atrPeriod: 'ATR Period',
                entryConditions: 'Entry Conditions',
                ema100TouchMode: 'EMA100 Touch Mode',
                lowHighTouch: 'Low/High touches',
                closeTouch: 'Close touches',
                touchTolerance: 'Touch Tolerance (%)',
                forbidEma150Touch: 'Forbid EMA150 Touch',
                slPullback: 'Below/Above Pullback',
                slFixed: 'Fixed %',
                slBuffer: 'SL Buffer (%)',
                slFixedPct: 'Fixed SL (%)',
                tpTrailing: 'Trailing',
                tpFixedRR: 'Fixed R:R',
                rrRatio: 'R:R Ratio',
                trailingBe: 'Move to Breakeven',
                beAfterProfit: 'BE After Profit (%)',
                trailNewHighLow: 'On New High/Low',
                trailEma50: 'Follow EMA50',
                trailCandles: 'By Candles',
                trailingBuffer: 'Trail Buffer (%)',
                tradeDirection: 'Trade Direction',
                tradeLong: 'Trade LONG',
                tradeShort: 'Trade SHORT',
                dailyLossLimit: 'Daily Loss Limit (%)',
                maxLosingStreak: 'Max Losing Streak',
                maxDailyTrades: 'Max Daily Trades',
                fixedUsdt: 'Fixed USDT',
                percentBalance: '% of Balance',
                dashboard: 'Dashboard',
                botsManager: 'Bots Manager',
                charts: 'Charts',
                priceCharts: 'Price Charts',
                availablePairs: 'Available Trading Pairs',
                createBot: '+ Create Bot',
                botName: 'Bot Name',
                cancel: 'Cancel',
                create: 'Create',
                stopped: 'Stopped',
                paused: 'Paused',
                start: 'Start',
                stop: 'Stop',
                pause: 'Pause',
                resume: 'Resume',
                delete: 'Delete',
                applyPairs: 'Apply Selected Pairs',
                noBots: 'No bots created yet. Click "Create Bot" to start.',
                botCreated: 'Bot created successfully!',
                botDeleted: 'Bot deleted successfully!',
                editBot: 'Edit Bot',
                save: 'Save',
                edit: 'Edit',
                activeBots: 'Active Bots',
                botUpdated: 'Bot updated successfully!',
                doubleClickChart: 'Double-click for fullscreen',
                botMode: 'Bot Mode',
                manualMode: 'Manual (Single Pair)',
                autoSearchMode: 'Auto Search (All Pairs)',
                maxTradingPairs: 'Max Trading Pairs',
                autoSearchHint: 'Bot will scan all pairs and open orders when strategy conditions match',
                balanceUsage: 'Balance Usage (%)',
                allocatedBalance: 'Allocated:',
                ofTotal: 'of',
                maxLossLimit: 'Max Loss Limit (%)',
                maxLossHint: 'Bot stops when loss reaches this % of allocated balance',
                consecutiveLossesLimit: 'Consecutive Losses Limit',
                maxConsecutiveLosses: 'Max Losses',
                cooldownMinutes: 'Cooldown (min)',
                basicSettings: 'Basic',
                riskManagement: 'Risk Management',
                signalFilters: 'Signal Filters',
                signalEntry: 'Signal Entry',
                triggerConfirmCandles: 'Trigger Confirm',
                fast: 'Fast',
                mediumSpeed: 'Medium',
                slowSpeed: 'Slow',
                candle1: '1 candle',
                candles2: '2 candles',
                candles3: '3 candles',
                candles: 'candles',
                haEntryConfirm: 'Entry Confirm',
                haExitOnColorChange: 'Exit on Color Change',
                haExitConfirm: 'Exit Confirm',
                haDescription: '🟢 LONG: Green candle confirmed | 🔴 SHORT: Red candle confirmed',
                haEmaFilter: 'EMA Filter',
                haEmaPeriod: 'EMA Period',
                haEmaMode: 'Mode',
                haVolatilityFilter: 'Volatility Filter (ATR)',
                haMinAtr: 'Min ATR %',
                haEmergencySl: 'Emergency SL %',
                haEmergencySlInfo: 'Always active even with color change exit. Safety net for flash crashes.',
                haTrailingStop: 'Trailing Stop',
                haTrailingActivation: 'Activation %',
                haTrailingDistance: 'Distance %',
                haDefaultsApplied: 'Recommended settings applied',
                strict: 'Strict'
            },
            ru: {
                connecting: 'Подключение...',
                connected: 'Подключено',
                disconnected: 'Отключено',
                startBot: 'Запуск',
                stopBot: 'Стоп',
                running: 'Работает',
                starting: 'Запуск...',
                stopping: 'Остановка...',
                settings: 'Настройки',
                strategySettings: 'Настройки стратегии',
                takeProfitSettings: 'Тейк-профит',
                stopLossSettings: 'Стоп-лосс',
                timeframe: 'Таймфрейм',
                tradingPairs: 'Торговые пары',
                tradingPair: 'Торговая пара',
                riskPerTrade: 'Риск на сделку (%)',
                tpRatio: 'Тейк-профит (R:R)',
                slMode: 'Режим стоп-лосса',
                slLine: 'Линия SuperTrend для SL',
                slPercent: 'SL фикс. процент (%)',
                slAtrMult: 'SL ATR множитель',
                leverage: 'Плечо',
                orderSize: 'Размер ордера (USDT)',
                positionSizingMode: 'Размер позиции',
                riskPerTrade: 'Риск на сделку (%)',
                leverageMode: 'Режим маржи',
                depositUsed: 'Используется депозит:',
                pairInfo: 'Информация о паре',
                minOrder: 'Мин. ордер:',
                maxLeverage: 'Макс. плечо:',
                maxPositions: 'Макс. позиций',
                emaFilter: 'EMA фильтр',
                emaMode: 'Режим EMA фильтра',
                strict: 'Строгий',
                soft: 'Мягкий (50% размер)',
                trailingSl: 'Trailing SL',
                trailingSlInfo: 'SL двигается в направлении прибыли. Fix %: по шагам. ST Line: следует за SuperTrend.',
                partialTpInfo: 'Закрывает часть позиции на TP1, двигает SL на TP1/Entry для фиксации прибыли.',
                trailingTpInfo: 'Без фиксированного TP, следует тренду. ST Line: выход при развороте. Trailing %: двигает TP при приближении цены.',
                trailingMode: 'Режим',
                trailingActivation: 'Активация %',
                trailingStep: 'Шаг %',
                trailingStLine: 'ST линия',
                trailingConfirm: 'Подтв. свечей',
                partialTp: 'Partial TP',
                partialTpClose: 'Закрыть %',
                partialTpSlMove: 'SL на',
                partialTpOffset: 'Отступ %',
                trailingTp: 'Trailing TP',
                trailingTpMode: 'Режим',
                trailingTpStLine: 'ST линия',
                trailingTpActivation: 'Активация %',
                trailingTpStep: 'Шаг %',
                assetFilters: 'Фильтры активов',
                volume24hMin: 'Объем 24ч мин',
                volume24hMax: 'Объем 24ч макс',
                priceMin: 'Цена мин $',
                priceMax: 'Цена макс $',
                changeMin: 'Изм. мин %',
                changeMax: 'Изм. макс %',
                volatilityPeriod: 'Период волат.',
                volatilityMin: 'Волат. мин %',
                volatilityMax: 'Волат. макс %',
                earlyEntry: 'Ранний вход (ST3)',
                tpMode: 'Режим TP',
                tpPercent: 'TP %',
                enabled: 'Включен',
                disabled: 'Выключен',
                fixedPercent: 'Фикс. процент',
                reset: 'Сброс',
                saveSettings: 'Сохранить',
                balance: 'Баланс',
                openPositions: 'Открытые позиции',
                sessionPnl: 'PnL сессии',
                winrate: 'Винрейт',
                closedTrades: 'Закрытые сделки',
                activeBots: 'Активные боты',
                liveLogs: 'Логи',
                systemLogs: 'Системные логи',
                autoScrollOn: 'Авто-прокрутка ВКЛ',
                autoScrollOff: 'Авто-прокрутка ВЫКЛ (прокрутите вниз для включения)',
                clear: 'Очистить',
                copyLogs: 'Копировать',
                logsCopied: 'Логи скопированы!',
                copyFailed: 'Ошибка копирования',
                botStarted: 'Бот запущен!',
                botStopped: 'Бот остановлен!',
                failedStart: 'Ошибка запуска',
                failedStop: 'Ошибка остановки',
                settingsSaved: 'Настройки сохранены! Перезапустите бот.',
                failedSave: 'Ошибка сохранения',
                logsCleared: '--- Логи очищены ---',
                // Strategy translations
                strategies: 'Стратегии',
                strategy3EMA: 'Стратегия 3 EMA Pullback',
                strategyName: 'Название стратегии',
                strategyStatus: 'Статус',
                strategySaved: 'Настройки стратегии сохранены!',
                strategyStarted: 'Стратегия запущена!',
                strategyStopped: 'Стратегия остановлена!',
                maxPairs: 'Макс. пар',
                emaParams: 'Параметры EMA',
                trendFilter: 'Фильтр тренда',
                minSlope: 'Мин. наклон (%)',
                slopeCandles: 'Свечей для наклона',
                emaDistanceAtr: 'Расстояние EMA (ATR)',
                atrPeriod: 'Период ATR',
                entryConditions: 'Условия входа',
                ema100TouchMode: 'Режим касания EMA100',
                lowHighTouch: 'Low/High касается',
                closeTouch: 'Close касается',
                touchTolerance: 'Допуск касания (%)',
                forbidEma150Touch: 'Запрет касания EMA150',
                slPullback: 'Под/над откатом',
                slFixed: 'Фикс. %',
                slBuffer: 'Буфер SL (%)',
                slFixedPct: 'Фикс. SL (%)',
                tpTrailing: 'Трейлинг',
                tpFixedRR: 'Фикс. R:R',
                rrRatio: 'Соотношение R:R',
                trailingBe: 'Перенос в безубыток',
                beAfterProfit: 'BE после профита (%)',
                trailNewHighLow: 'На новых High/Low',
                trailEma50: 'По EMA50',
                trailCandles: 'По свечам',
                trailingBuffer: 'Буфер трейлинга (%)',
                tradeDirection: 'Направление торговли',
                tradeLong: 'Торговать LONG',
                tradeShort: 'Торговать SHORT',
                dailyLossLimit: 'Лимит потерь за день (%)',
                maxLosingStreak: 'Макс. убытков подряд',
                maxDailyTrades: 'Макс. сделок в день',
                fixedUsdt: 'Фикс. USDT',
                percentBalance: '% от баланса',
                dashboard: 'Панель',
                botsManager: 'Управление ботами',
                charts: 'Графики',
                priceCharts: 'Графики цен',
                availablePairs: 'Доступные торговые пары',
                createBot: '+ Создать бот',
                botName: 'Название бота',
                cancel: 'Отмена',
                create: 'Создать',
                stopped: 'Остановлен',
                paused: 'Пауза',
                start: 'Запуск',
                stop: 'Стоп',
                pause: 'Пауза',
                resume: 'Продолжить',
                delete: 'Удалить',
                applyPairs: 'Применить выбранные пары',
                noBots: 'Ботов пока нет. Нажмите "Создать бот" для начала.',
                botCreated: 'Бот создан!',
                botDeleted: 'Бот удален!',
                editBot: 'Редактировать бот',
                save: 'Сохранить',
                edit: 'Редакт.',
                activeBots: 'Активных ботов',
                botUpdated: 'Бот обновлен!',
                doubleClickChart: 'Двойной клик для полноэкранного режима',
                botMode: 'Режим бота',
                manualMode: 'Ручной (одна пара)',
                autoSearchMode: 'Автопоиск (все пары)',
                maxTradingPairs: 'Макс. торговых пар',
                autoSearchHint: 'Бот сканирует все пары и открывает ордера при совпадении условий стратегии',
                balanceUsage: 'Использование баланса (%)',
                allocatedBalance: 'Выделено:',
                ofTotal: 'из',
                maxLossLimit: 'Лимит потерь (%)',
                maxLossHint: 'Бот остановится когда убыток достигнет этого % от выделенного баланса',
                consecutiveLossesLimit: 'Лимит убыточных сделок подряд',
                maxConsecutiveLosses: 'Макс. сделок',
                cooldownMinutes: 'Пауза (мин)',
                basicSettings: 'Основные',
                riskManagement: 'Риск-менеджмент',
                signalFilters: 'Фильтры сигналов',
                signalEntry: 'Сигнал входа',
                triggerConfirmCandles: 'Подтв. триггера',
                fast: 'Быстрый',
                mediumSpeed: 'Средний',
                slowSpeed: 'Медленный',
                candle1: '1 свеча',
                candles2: '2 свечи',
                candles3: '3 свечи',
                candles: 'свечей',
                haEntryConfirm: 'Подтв. входа',
                haExitOnColorChange: 'Выход по смене цвета',
                haExitConfirm: 'Подтв. выхода',
                haDescription: '🟢 LONG: Подтв. зелёной свечи | 🔴 SHORT: Подтв. красной свечи',
                haEmaFilter: 'EMA Фильтр',
                haEmaPeriod: 'Период EMA',
                haEmaMode: 'Режим',
                haVolatilityFilter: 'Фильтр волатильности (ATR)',
                haMinAtr: 'Мин ATR %',
                haEmergencySl: 'Экстренный SL %',
                haEmergencySlInfo: 'Всегда активен даже при выходе по смене цвета. Защита от резких обвалов.',
                haTrailingStop: 'Трейлинг стоп',
                haTrailingActivation: 'Активация %',
                haTrailingDistance: 'Дистанция %',
                haDefaultsApplied: 'Применены рекомендуемые настройки',
                strict: 'Строгий'
            }
        };

        // Get language from cookie or localStorage
        function getCookie(name) {
            const match = document.cookie.match(new RegExp('(^| )' + name + '=([^;]+)'));
            return match ? match[2] : null;
        }

        function setCookie(name, value) {
            document.cookie = name + '=' + value + '; path=/; max-age=31536000; SameSite=Lax';
        }

        let currentLang = getCookie('ailaLang') || localStorage.getItem('ailaLang') || 'en';

        function t(key) {
            return i18n[currentLang][key] || i18n['en'][key] || key;
        }

        function changeLanguage(lang) {
            currentLang = lang;
            localStorage.setItem('ailaLang', lang);
            setCookie('ailaLang', lang);
            updateUI();
            // Send language to server for log messages
            fetch('/api/language', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ language: lang })
            }).catch(err => console.error('Failed to set language:', err));
        }

        function updateUI() {
            document.querySelectorAll('[data-i18n]').forEach(el => {
                const key = el.getAttribute('data-i18n');
                if (i18n[currentLang][key]) {
                    el.textContent = i18n[currentLang][key];
                }
            });
            // Translate tooltips
            document.querySelectorAll('[data-i18n-tooltip]').forEach(el => {
                const key = el.getAttribute('data-i18n-tooltip');
                if (i18n[currentLang][key]) {
                    el.textContent = i18n[currentLang][key];
                }
            });
            document.getElementById('langSelector').value = currentLang;
        }

        function updateBalanceUsageInfo(prefix) {
            try {
                const balanceUsageEl = document.getElementById(prefix + 'BotBalanceUsage');
                const allocatedBalanceEl = document.getElementById(prefix + 'AllocatedBalance');
                const totalBalanceEl = document.getElementById(prefix + 'TotalBalance');

                if (!balanceUsageEl || !allocatedBalanceEl || !totalBalanceEl) return;

                const balanceUsagePercent = parseFloat(balanceUsageEl.value) || 100;
                const balanceText = document.getElementById('balance')?.textContent || '0';
                const totalBalance = parseFloat(balanceText.replace('--', '0')) || 0;
                const allocatedBalance = (totalBalance * balanceUsagePercent / 100).toFixed(2);

                totalBalanceEl.textContent = totalBalance.toFixed(2);
                allocatedBalanceEl.textContent = allocatedBalance;
            } catch (e) {
                console.error('updateBalanceUsageInfo error:', e);
            }
        }

        function updateMaxLossInfo(prefix) {
            try {
                const balanceUsageEl = document.getElementById(prefix + 'BotBalanceUsage');
                const maxLossEl = document.getElementById(prefix + 'BotMaxLoss');
                const maxLossAmountEl = document.getElementById(prefix + 'MaxLossAmount');

                if (!balanceUsageEl || !maxLossEl || !maxLossAmountEl) return;

                const balanceUsagePercent = parseFloat(balanceUsageEl.value) || 100;
                const maxLossPercent = parseFloat(maxLossEl.value) || 100;
                const balanceText = document.getElementById('balance')?.textContent || '0';
                const totalBalance = parseFloat(balanceText.replace('--', '0')) || 0;
                const allocatedBalance = totalBalance * balanceUsagePercent / 100;
                const maxLossAmount = (allocatedBalance * maxLossPercent / 100).toFixed(2);

                maxLossAmountEl.textContent = maxLossAmount;
            } catch (e) {
                console.error('updateMaxLossInfo error:', e);
            }
        }

        function updateSliderValue(slider, displayId) {
            document.getElementById(displayId).textContent = slider.value + '%';
        }

        function toggleEmaMode(prefix) {
            const emaEnabled = document.getElementById(prefix + 'BotEmaEnabled').checked;
            const emaModeContainer = document.getElementById(prefix + 'EmaModeContainer');
            if (emaModeContainer) {
                emaModeContainer.style.display = emaEnabled ? 'flex' : 'none';
            }
        }

        // Strategy selection toggle (SuperTrend / Heikin Ashi - mutually exclusive)
        function toggleStrategy(prefix, strategy) {
            const stEnabled = document.getElementById(prefix + 'BotStrategySupertrendEnabled');
            const haEnabled = document.getElementById(prefix + 'BotStrategyHeikinAshiEnabled');
            const stSettings = document.getElementById(prefix + 'SupertrendSettings');
            const haSettings = document.getElementById(prefix + 'HeikinAshiSettings');
            const stContainer = document.getElementById(prefix + 'StrategySupertrend');
            const haContainer = document.getElementById(prefix + 'StrategyHeikinAshi');

            if (strategy === 'supertrend') {
                stEnabled.checked = true;
                haEnabled.checked = false;
                stSettings.style.display = 'block';
                haSettings.style.display = 'none';
                stContainer.style.background = 'rgba(0,212,255,0.1)';
                stContainer.querySelector('label').style.color = '#00d4ff';
                haContainer.style.background = 'rgba(255,255,255,0.05)';
                haContainer.querySelector('label').style.color = '#888';
                // Show TP/SL blocks
                toggleTpSlVisibility(prefix, true);
            } else if (strategy === 'heikinashi') {
                stEnabled.checked = false;
                haEnabled.checked = true;
                stSettings.style.display = 'none';
                haSettings.style.display = 'block';
                stContainer.style.background = 'rgba(255,255,255,0.05)';
                stContainer.querySelector('label').style.color = '#888';
                haContainer.style.background = 'rgba(255,170,0,0.1)';
                haContainer.querySelector('label').style.color = '#ffaa00';
                // Apply recommended defaults and show notification
                applyHaRecommendedDefaults(prefix);
            }
        }

        // Toggle Heikin Ashi exit settings (show/hide exit confirm + TP/SL blocks)
        function toggleHaExitSettings(prefix) {
            const exitOnColorChange = document.getElementById(prefix + 'BotHaExitOnColorChange').checked;
            const exitConfirmRow = document.getElementById(prefix + 'HaExitConfirmRow');

            // Show/hide exit confirm candles setting
            if (exitConfirmRow) {
                exitConfirmRow.style.display = exitOnColorChange ? 'flex' : 'none';
            }

            // If Heikin Ashi is active and exit on color change is enabled, hide TP/SL
            const haEnabled = document.getElementById(prefix + 'BotStrategyHeikinAshiEnabled');
            if (haEnabled && haEnabled.checked) {
                toggleTpSlVisibility(prefix, !exitOnColorChange);
            }
        }

        // Toggle TP and SL blocks visibility
        function toggleTpSlVisibility(prefix, visible) {
            const tpBlock = document.getElementById(prefix + 'TpBlock');
            const slBlock = document.getElementById(prefix + 'SlBlock');

            if (tpBlock) {
                tpBlock.style.display = visible ? 'block' : 'none';
            }
            if (slBlock) {
                slBlock.style.display = visible ? 'block' : 'none';
            }
        }

        // Toggle Heikin Ashi EMA filter settings
        function toggleHaEmaSettings(prefix) {
            const enabled = document.getElementById(prefix + 'BotHaEmaEnabled').checked;
            const settingsRow = document.getElementById(prefix + 'HaEmaSettingsRow');
            if (settingsRow) {
                settingsRow.style.display = enabled ? 'flex' : 'none';
            }
        }

        // Toggle Heikin Ashi Volatility filter settings
        function toggleHaVolatilitySettings(prefix) {
            const enabled = document.getElementById(prefix + 'BotHaVolatilityEnabled').checked;
            const settingsRow = document.getElementById(prefix + 'HaVolatilitySettingsRow');
            if (settingsRow) {
                settingsRow.style.display = enabled ? 'flex' : 'none';
            }
        }

        // Toggle Heikin Ashi Trailing Stop settings
        function toggleHaTrailingSettings(prefix) {
            const enabled = document.getElementById(prefix + 'BotHaTrailingEnabled').checked;
            const settingsRow = document.getElementById(prefix + 'HaTrailingSettingsRow');
            if (settingsRow) {
                settingsRow.style.display = enabled ? 'flex' : 'none';
            }
        }

        // Apply recommended defaults when selecting Heikin Ashi strategy
        function applyHaRecommendedDefaults(prefix) {
            // Recommended defaults for Heikin Ashi strategy
            const defaults = {
                haEntryCandles: 2,       // Confirmation candles for entry
                haExitOnColorChange: true,  // Exit on color change
                haExitCandles: 1,        // Exit confirmation candles
                haEmaEnabled: true,      // EMA filter enabled
                haEmaPeriod: 200,        // EMA period
                haEmaMode: 'strict',     // EMA mode
                haVolatilityEnabled: true,  // Volatility filter enabled
                haMinAtr: 0.5,           // Minimum ATR %
                haEmergencySl: 2,        // Emergency stop-loss %
                haTrailingEnabled: true, // Trailing stop enabled
                haTrailingActivation: 1, // Trailing activation %
                haTrailingDistance: 0.5  // Trailing distance %
            };

            // Apply defaults
            const setValueIfExists = (id, value) => {
                const el = document.getElementById(id);
                if (el) {
                    if (el.type === 'checkbox') {
                        el.checked = value;
                    } else {
                        el.value = value;
                    }
                    // Highlight the field briefly
                    el.style.transition = 'box-shadow 0.3s';
                    el.style.boxShadow = '0 0 8px #ffaa00';
                    setTimeout(() => {
                        el.style.boxShadow = '';
                    }, 1500);
                }
            };

            setValueIfExists(prefix + 'BotHaEntryCandles', defaults.haEntryCandles);
            setValueIfExists(prefix + 'BotHaExitOnColorChange', defaults.haExitOnColorChange);
            setValueIfExists(prefix + 'BotHaExitCandles', defaults.haExitCandles);
            setValueIfExists(prefix + 'BotHaEmaEnabled', defaults.haEmaEnabled);
            setValueIfExists(prefix + 'BotHaEmaPeriod', defaults.haEmaPeriod);
            setValueIfExists(prefix + 'BotHaEmaMode', defaults.haEmaMode);
            setValueIfExists(prefix + 'BotHaVolatilityEnabled', defaults.haVolatilityEnabled);
            setValueIfExists(prefix + 'BotHaMinAtr', defaults.haMinAtr);
            setValueIfExists(prefix + 'BotHaEmergencySl', defaults.haEmergencySl);
            setValueIfExists(prefix + 'BotHaTrailingEnabled', defaults.haTrailingEnabled);
            setValueIfExists(prefix + 'BotHaTrailingActivation', defaults.haTrailingActivation);
            setValueIfExists(prefix + 'BotHaTrailingDistance', defaults.haTrailingDistance);

            // Update toggle states
            toggleHaExitSettings(prefix);
            toggleHaEmaSettings(prefix);
            toggleHaVolatilitySettings(prefix);
            toggleHaTrailingSettings(prefix);

            // Show notification
            showHaDefaultsNotification();
        }

        // Show notification that HA defaults were applied
        function showHaDefaultsNotification() {
            // Remove existing notification if any
            const existing = document.querySelector('.ha-defaults-notification');
            if (existing) existing.remove();

            const notification = document.createElement('div');
            notification.className = 'ha-defaults-notification';
            notification.innerHTML = `
                <span style="margin-right: 8px;">✓</span>
                <span data-i18n="haDefaultsApplied">Recommended settings applied</span>
            `;
            notification.style.cssText = `
                position: fixed;
                top: 80px;
                left: 50%;
                transform: translateX(-50%);
                background: linear-gradient(135deg, #ffaa00, #ff8800);
                color: #000;
                padding: 12px 24px;
                border-radius: 8px;
                font-weight: 600;
                z-index: 10001;
                box-shadow: 0 4px 20px rgba(255, 170, 0, 0.4);
                animation: slideInDown 0.3s ease-out;
            `;
            document.body.appendChild(notification);

            // Translate if needed
            const span = notification.querySelector('[data-i18n]');
            if (span && i18n[currentLang] && i18n[currentLang]['haDefaultsApplied']) {
                span.textContent = i18n[currentLang]['haDefaultsApplied'];
            }

            // Remove after 3 seconds
            setTimeout(() => {
                notification.style.animation = 'fadeOut 0.3s ease-out';
                setTimeout(() => notification.remove(), 300);
            }, 3000);
        }

        // Signal Entry configuration
        function updateSignalPreview(prefix) {
            const st1Role = document.getElementById(prefix + 'BotSt1Role').value;
            const st2Role = document.getElementById(prefix + 'BotSt2Role').value;
            const st3Role = document.getElementById(prefix + 'BotSt3Role').value;
            const previewEl = document.getElementById(prefix + 'SignalPreview');

            // Count triggers
            const roles = [st1Role, st2Role, st3Role];
            const triggerCount = roles.filter(r => r === 'trigger').length;
            const activeCount = roles.filter(r => r !== 'off').length;

            // If more than one trigger, reset others to confirm
            if (triggerCount > 1) {
                // Find which one was just changed to trigger and keep it
                const selects = [
                    document.getElementById(prefix + 'BotSt1Role'),
                    document.getElementById(prefix + 'BotSt2Role'),
                    document.getElementById(prefix + 'BotSt3Role')
                ];
                let foundTrigger = false;
                selects.forEach((sel, i) => {
                    if (sel.value === 'trigger') {
                        if (foundTrigger) {
                            sel.value = 'confirm';
                        } else {
                            foundTrigger = true;
                        }
                    }
                });
                // Recursively update preview
                updateSignalPreview(prefix);
                return;
            }

            // Build preview text
            const roleIcons = { off: '—', confirm: '●', trigger: '◎' };
            const stNames = ['ST1', 'ST2', 'ST3'];
            const activeLines = [];
            let triggerName = '';

            roles.forEach((role, i) => {
                if (role !== 'off') {
                    activeLines.push(stNames[i] + roleIcons[role]);
                    if (role === 'trigger') triggerName = stNames[i];
                }
            });

            if (previewEl) {
                if (activeCount === 0) {
                    previewEl.innerHTML = `
                        <div style="color: #ff4444;">Select at least one line</div>
                    `;
                } else if (triggerCount === 0) {
                    previewEl.innerHTML = `
                        <div style="color: #ff4444;">Select a trigger (◎)</div>
                    `;
                } else {
                    previewEl.innerHTML = `
                        <div style="color: #888;">${activeLines.join(' + ')}</div>
                        <div style="color: #00d4ff;">Entry when ${triggerName} reverses</div>
                    `;
                }
            }
        }

        function toggleTrailingOptions(prefix) {
            const trailingEnabled = document.getElementById(prefix + 'BotTrailingEnabled').checked;
            const optionsRow = document.getElementById(prefix + 'TrailingOptionsRow');

            // Show/hide the entire options row
            if (optionsRow) optionsRow.style.display = trailingEnabled ? 'flex' : 'none';

            if (trailingEnabled) {
                toggleTrailingMode(prefix);
            }
        }

        function toggleTrailingMode(prefix) {
            const trailingMode = document.getElementById(prefix + 'BotTrailingMode').value;
            const activationContainer = document.getElementById(prefix + 'TrailingActivationContainer');
            const stepContainer = document.getElementById(prefix + 'TrailingStepContainer');
            const stLineContainer = document.getElementById(prefix + 'TrailingStLineContainer');
            const confirmContainer = document.getElementById(prefix + 'TrailingConfirmContainer');

            if (trailingMode === 'fix_percent') {
                if (activationContainer) activationContainer.style.display = 'flex';
                if (stepContainer) stepContainer.style.display = 'flex';
                if (stLineContainer) stLineContainer.style.display = 'none';
                if (confirmContainer) confirmContainer.style.display = 'none';
            } else {
                // ST Line mode
                if (activationContainer) activationContainer.style.display = 'none';
                if (stepContainer) stepContainer.style.display = 'none';
                if (stLineContainer) stLineContainer.style.display = 'flex';
                if (confirmContainer) confirmContainer.style.display = 'flex';
            }
        }

        // Toggle Partial TP options
        function togglePartialTpOptions(prefix) {
            const partialTpEnabled = document.getElementById(prefix + 'BotPartialTpEnabled').checked;
            const optionsRow = document.getElementById(prefix + 'PartialTpOptionsRow');

            if (optionsRow) optionsRow.style.display = partialTpEnabled ? 'flex' : 'none';

            if (partialTpEnabled) {
                togglePartialTpOffset(prefix);
            }
        }

        // Toggle Partial TP offset visibility based on SL Move selection
        function togglePartialTpOffset(prefix) {
            // Offset is always visible when Partial TP is enabled
            // For TP1 mode: offset from TP1 price
            // For Entry mode: offset from entry price (to lock small profit)
            const offsetContainer = document.getElementById(prefix + 'PartialTpOffsetContainer');
            if (offsetContainer) offsetContainer.style.display = 'flex';
        }

        // Toggle Trailing TP options
        function toggleTrailingTpOptions(prefix) {
            const trailingTpEnabled = document.getElementById(prefix + 'BotTrailingTpEnabled').checked;
            const optionsRow = document.getElementById(prefix + 'TrailingTpOptionsRow');

            if (optionsRow) optionsRow.style.display = trailingTpEnabled ? 'flex' : 'none';

            if (trailingTpEnabled) {
                toggleTrailingTpMode(prefix);
            }
        }

        function toggleRiskPercentInput(prefix) {
            const positionSizingMode = document.getElementById(prefix + 'BotPositionSizingMode').value;
            const riskContainer = document.getElementById(prefix + 'RiskPerTradeContainer');

            if (riskContainer) {
                riskContainer.style.display = positionSizingMode === 'risk_percent' ? 'flex' : 'none';
            }
        }

        // Toggle Max Loss Limit slider visibility
        function toggleMaxLossSlider(prefix) {
            const checkbox = document.getElementById(prefix + 'BotMaxLossEnabled');
            const container = document.getElementById(prefix + 'MaxLossContainer');

            if (container) {
                container.style.display = checkbox.checked ? 'flex' : 'none';
                // Update max loss info when enabled
                if (checkbox.checked) {
                    updateMaxLossInfo(prefix);
                }
            }
        }

        // Toggle Consecutive Losses settings visibility
        function toggleConsecutiveLossesSelect(prefix) {
            const checkbox = document.getElementById(prefix + 'BotConsecutiveLossesEnabled');
            const lossesContainer = document.getElementById(prefix + 'ConsecutiveLossesContainer');
            const cooldownContainer = document.getElementById(prefix + 'CooldownContainer');

            if (lossesContainer && cooldownContainer) {
                const isEnabled = checkbox.checked;
                lossesContainer.style.display = isEnabled ? 'flex' : 'none';
                cooldownContainer.style.display = isEnabled ? 'flex' : 'none';
            }
        }

        // Toggle Trailing TP mode-specific options
        function toggleTrailingTpMode(prefix) {
            const mode = document.getElementById(prefix + 'BotTrailingTpMode').value;
            const stLineContainer = document.getElementById(prefix + 'TrailingTpStLineContainer');
            const activationContainer = document.getElementById(prefix + 'TrailingTpActivationContainer');
            const stepContainer = document.getElementById(prefix + 'TrailingTpStepContainer');

            if (mode === 'st_line') {
                if (stLineContainer) stLineContainer.style.display = 'flex';
                if (activationContainer) activationContainer.style.display = 'none';
                if (stepContainer) stepContainer.style.display = 'none';
            } else {
                // Trailing % mode
                if (stLineContainer) stLineContainer.style.display = 'none';
                if (activationContainer) activationContainer.style.display = 'flex';
                if (stepContainer) stepContainer.style.display = 'flex';
            }
        }

        // Toggle info tooltip
        // Toggle info tooltip (mobile only - desktop uses CSS hover)
        function toggleInfo(icon) {
            // Only handle click on touch devices
            if (!window.matchMedia('(hover: hover)').matches) {
                const tooltip = icon.nextElementSibling;
                const isActive = tooltip.classList.contains('active');
                // Close all other tooltips
                document.querySelectorAll('.info-tooltip.active').forEach(t => t.classList.remove('active'));
                if (!isActive) {
                    tooltip.classList.add('active');
                }
            }
        }
        // Close tooltip on click outside (mobile)
        document.addEventListener('click', function(e) {
            if (!e.target.closest('.info-wrapper')) {
                document.querySelectorAll('.info-tooltip.active').forEach(t => t.classList.remove('active'));
            }
        });

        // Toggle TP mode options
        function toggleTpOptions(prefix) {
            const tpMode = document.getElementById(prefix + 'BotTpMode').value;
            const rrContainer = document.getElementById(prefix + 'TpRrContainer');
            const fixedContainer = document.getElementById(prefix + 'TpFixedContainer');
            if (rrContainer) rrContainer.style.display = tpMode === 'rr' ? 'flex' : 'none';
            if (fixedContainer) fixedContainer.style.display = tpMode === 'fixed' ? 'flex' : 'none';
        }

        // Select R:R ratio
        function selectRr(prefix, value) {
            const selector = document.getElementById(prefix + 'RrSelector');
            const hiddenInput = document.getElementById(prefix + 'BotTpRatio');
            if (selector) {
                selector.querySelectorAll('.rr-option').forEach(opt => {
                    opt.classList.toggle('active', parseInt(opt.dataset.value) === value);
                });
            }
            if (hiddenInput) hiddenInput.value = value;
        }

        // Normalize numeric input: replace comma with dot
        function normalizeNumericInput(input) {
            input.value = input.value.replace(/,/g, '.');
        }

        // Clear input field and set to "-"
        function clearInput(btn) {
            const input = btn.parentElement.querySelector('input');
            if (input) {
                input.value = '-';
                input.focus();
            }
        }

        // Handle empty input - replace with "-"
        function handleEmptyInput(input) {
            const val = input.value.trim();
            if (val === '' || val === '0') {
                input.value = '-';
            }
        }

        // Format money input with space as thousands separator, show "-" for 0
        function formatMoneyInput(input) {
            let value = input.value.replace(/[^\d]/g, '');
            if (value && parseInt(value) > 0) {
                value = parseInt(value).toLocaleString('ru-RU').replace(/\s/g, ' ');
            } else {
                value = '-';
            }
            input.value = value;
        }

        // Parse money input to number (handles "-" as 0)
        function parseMoneyValue(value) {
            if (value === '-' || value === '') return 0;
            return parseFloat(value.replace(/[^\d]/g, '')) || 0;
        }

        // Reset asset filters to defaults
        function resetAssetFilters(prefix) {
            document.getElementById(prefix + 'BotMinVolume').value = '3 000 000';
            document.getElementById(prefix + 'BotMaxVolume').value = '-';
            document.getElementById(prefix + 'BotMinPrice').value = '-';
            document.getElementById(prefix + 'BotMaxPrice').value = '-';
            document.getElementById(prefix + 'BotMinChange').value = '-30';
            document.getElementById(prefix + 'BotMaxChange').value = '20';
            document.getElementById(prefix + 'BotVolatilityPeriod').value = '12';
            document.getElementById(prefix + 'BotMinVolatility').value = '0.5';
            document.getElementById(prefix + 'BotMaxVolatility').value = '3';
        }

        // Toggle asset filters inputs visibility
        function toggleAssetFiltersInputs(prefix) {
            const checkbox = document.getElementById(prefix + 'BotAssetFiltersEnabled');
            const inputsContainer = document.getElementById(prefix + 'AssetFiltersInputs');
            if (inputsContainer) {
                inputsContainer.style.display = checkbox.checked ? 'block' : 'none';
            }
        }

        // Reset create bot form to defaults
        function resetCreateBotForm() {
            document.getElementById('newBotName').value = '';
            document.getElementById('newBotMode').value = 'manual';
            document.getElementById('newBotPairSearch').value = '';
            document.getElementById('newBotPair').value = 'BTCUSDT';
            document.getElementById('newBotMaxPairs').value = '1';
            document.getElementById('newBotBalanceUsage').value = '100';
            document.getElementById('newBalanceValue').textContent = '100%';
            document.getElementById('newBotMaxLoss').value = '100';
            document.getElementById('newMaxLossValue').textContent = '100%';
            document.getElementById('newBotTimeframe').value = '1m';
            document.getElementById('newBotLeverage').value = '10';
            document.getElementById('newBotOrderSize').value = '10';
            document.getElementById('newBotTpMode').value = 'rr';
            selectRr('new', 2);
            document.getElementById('newBotTpPercent').value = '2';
            document.getElementById('newBotSlMode').value = 'supertrend_line';
            document.getElementById('newBotSlLine').value = '2';
            document.getElementById('newBotSlPercent').value = '2';
            document.getElementById('newBotSlAtrMult').value = '1.5';
            document.getElementById('newBotTrailingEnabled').checked = true;
            document.getElementById('newBotTrailingMode').value = 'st_line';
            document.getElementById('newBotTrailingActivation').value = '1.0';
            document.getElementById('newBotTrailingStep').value = '0.5';
            document.getElementById('newBotTrailingStLine').value = '1';
            document.getElementById('newBotTrailingConfirm').value = '1';
            document.getElementById('newBotPartialTpEnabled').checked = true;
            document.getElementById('newBotPartialTpClose').value = '50';
            document.getElementById('newBotPartialTpSlMove').value = 'tp1';
            document.getElementById('newBotPartialTpOffset').value = '0.2';
            document.getElementById('newBotTrailingTpEnabled').checked = true;
            document.getElementById('newBotTrailingTpMode').value = 'st_line';
            document.getElementById('newBotTrailingTpStLine').value = '1';
            document.getElementById('newBotTrailingTpActivation').value = '0.5';
            document.getElementById('newBotTrailingTpStep').value = '1.0';
            document.getElementById('newBotLeverageMode').value = 'isolated';
            document.getElementById('newBotEarlyEntry').checked = false;
            document.getElementById('newBotEmaEnabled').checked = true;
            document.getElementById('newBotEmaMode').value = 'strict';
            resetAssetFilters('new');
            toggleBotMode('new');
            toggleTpOptions('new');
            toggleSlOptions('new');
            toggleEmaMode('new');
            toggleTrailingOptions('new');
            togglePartialTpOptions('new');
            toggleTrailingTpOptions('new');
            updateDepositInfo('new');
            updateBalanceUsageInfo('new');
            updateMaxLossInfo('new');
        }

        // Reset edit bot form (reload bot data)
        function resetEditBotForm() {
            const botId = document.getElementById('editBotId').value;
            if (botId) {
                showEditBotModal(botId);
            }
        }

        let ws;
        let autoScroll = true;
        let reconnectAttempts = 0;
        const maxReconnectAttempts = 10;

        function connectWebSocket() {
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            ws = new WebSocket(`${protocol}//${window.location.host}/ws/logs`);

            ws.onopen = function() {
                console.log('WebSocket connected');
                document.getElementById('statusDot').classList.remove('disconnected');
                reconnectAttempts = 0;
            };

            ws.onmessage = function(event) {
                addLogLine(event.data);
            };

            ws.onclose = function() {
                console.log('WebSocket disconnected');
                document.getElementById('statusDot').classList.add('disconnected');

                // Reconnect
                if (reconnectAttempts < maxReconnectAttempts) {
                    reconnectAttempts++;
                    setTimeout(connectWebSocket, 2000);
                }
            };

            ws.onerror = function(error) {
                console.error('WebSocket error:', error);
            };
        }

        function addLogLine(text) {
            const logsDiv = document.getElementById('logs');
            const line = document.createElement('div');
            line.className = 'log-line';

            // Color based on content - check trade events first
            if (text.includes('[TRADE_OPEN]') || text.includes('[ОТКРЫТИЕ]')) {
                line.classList.add('log-trade-open');
            } else if (text.includes('[TRADE_PROFIT]') || text.includes('[ПРИБЫЛЬ]')) {
                line.classList.add('log-trade-profit');
            } else if (text.includes('[TRADE_LOSS]') || text.includes('[УБЫТОК]')) {
                line.classList.add('log-trade-loss');
            } else if (text.includes('[error]') || text.includes('Error') || text.includes('Failed')) {
                line.classList.add('log-error');
            } else if (text.includes('[warning]') || text.includes('Warning')) {
                line.classList.add('log-warning');
            } else if (text.includes('success') || text.includes('Filled')) {
                line.classList.add('log-success');
            } else {
                line.classList.add('log-info');
            }

            line.textContent = text;
            logsDiv.appendChild(line);

            // Keep only last 500 lines
            while (logsDiv.children.length > 500) {
                logsDiv.removeChild(logsDiv.firstChild);
            }

            // Auto-scroll
            if (autoScroll) {
                logsDiv.scrollTop = logsDiv.scrollHeight;
            }
        }

        function copyLogs() {
            const logsDiv = document.getElementById('logs');
            const logLines = logsDiv.querySelectorAll('.log-line');
            let text = '';
            // Copy only last 100 lines
            const startIndex = Math.max(0, logLines.length - 100);
            for (let i = startIndex; i < logLines.length; i++) {
                text += logLines[i].textContent;
                if (i < logLines.length - 1) text += String.fromCharCode(10);
            }

            // Try modern clipboard API first
            if (navigator.clipboard && navigator.clipboard.writeText) {
                navigator.clipboard.writeText(text).then(function() {
                    showToast(t('logsCopied') + ` (${logLines.length - startIndex} lines)`);
                }).catch(function(err) {
                    console.error('Clipboard API failed:', err);
                    fallbackCopy(text, logLines.length - startIndex);
                });
            } else {
                fallbackCopy(text, logLines.length - startIndex);
            }
        }

        function fallbackCopy(text, lineCount) {
            const textArea = document.createElement('textarea');
            textArea.value = text;
            textArea.style.position = 'fixed';
            textArea.style.left = '-9999px';
            textArea.style.top = '0';
            document.body.appendChild(textArea);
            textArea.focus();
            textArea.select();
            try {
                const successful = document.execCommand('copy');
                if (successful) {
                    showToast(t('logsCopied') + (lineCount ? ` (${lineCount} lines)` : ''));
                } else {
                    showToast(t('copyFailed'));
                }
            } catch (err) {
                console.error('Fallback copy failed:', err);
                showToast(t('copyFailed'));
            }
            document.body.removeChild(textArea);
        }

        function clearLogs() {
            document.getElementById('logs').innerHTML = '';
            addLogLine(t('logsCleared'));
        }

        function showToast(message) {
            const toast = document.getElementById('toast');
            toast.textContent = message;
            toast.classList.add('show');
            setTimeout(() => {
                toast.classList.remove('show');
            }, 3000);
        }

        // Toggle auto-scroll when user scrolls manually
        document.getElementById('logs').addEventListener('scroll', function() {
            const logsDiv = this;
            const isAtBottom = logsDiv.scrollHeight - logsDiv.scrollTop <= logsDiv.clientHeight + 50;
            autoScroll = isAtBottom;
            updateAutoScrollBtn();
        });

        // Update auto-scroll button state
        function updateAutoScrollBtn() {
            const btn = document.getElementById('modalAutoScrollBtn');
            if (btn) {
                if (autoScroll) {
                    btn.classList.add('active');
                } else {
                    btn.classList.remove('active');
                }
            }
        }

        // Toggle auto-scroll on button click
        function toggleAutoScroll() {
            const logsDiv = document.getElementById('logs');
            autoScroll = true;
            logsDiv.scrollTop = logsDiv.scrollHeight;
            updateAutoScrollBtn();
        }

        // Open logs modal
        function openLogsModal() {
            const modal = document.getElementById('logsModal');
            modal.classList.add('show');
            document.body.style.overflow = 'hidden';
            // Scroll to bottom
            const logsDiv = document.getElementById('logs');
            if (autoScroll && logsDiv) {
                logsDiv.scrollTop = logsDiv.scrollHeight;
            }
            updateAutoScrollBtn();
        }

        // Close logs modal
        function closeLogsModal() {
            const modal = document.getElementById('logsModal');
            modal.classList.remove('show');
            document.body.style.overflow = '';
        }

        // Close modal on Escape key
        document.addEventListener('keydown', function(e) {
            if (e.key === 'Escape') {
                const logsModal = document.getElementById('logsModal');
                if (logsModal.classList.contains('show')) {
                    closeLogsModal();
                }
            }
        });

        // Fetch stats periodically
        async function fetchStats() {
            try {
                const response = await fetch('/api/stats');
                const data = await response.json();

                // Balance - always from exchange
                document.getElementById('balance').textContent =
                    data.balance ? data.balance.toFixed(2) : '--';

                // Open Positions
                document.getElementById('positions').textContent =
                    data.positions !== undefined ? data.positions : '--';

                // PnL (USDT and %) - session stats since server restart
                const pnlEl = document.getElementById('pnl');
                if (data.pnl_usdt !== undefined && data.pnl_usdt !== null) {
                    const pnlUsdt = data.pnl_usdt;
                    const pnlPercent = data.pnl_percent || 0;
                    const sign = pnlUsdt >= 0 ? '+' : '';
                    pnlEl.textContent = `${sign}${pnlUsdt.toFixed(2)} (${sign}${pnlPercent.toFixed(2)}%)`;
                    if (pnlUsdt > 0) {
                        pnlEl.className = 'stat-value positive';
                    } else if (pnlUsdt < 0) {
                        pnlEl.className = 'stat-value negative';
                    } else {
                        pnlEl.className = 'stat-value'; // cyan for zero
                    }
                } else {
                    pnlEl.textContent = '0.00 (0.00%)';
                    pnlEl.className = 'stat-value'; // cyan for zero
                }

                // Winrate %
                const winrateEl = document.getElementById('winrate');
                if (data.winrate !== undefined && data.winrate !== null) {
                    winrateEl.textContent = data.winrate.toFixed(1) + '%';
                } else {
                    winrateEl.textContent = '--%';
                }

                // Closed Trades - session stats since server restart
                document.getElementById('closedTrades').textContent =
                    data.closed_trades !== undefined ? data.closed_trades : '0';

            } catch (err) {
                console.error('Failed to fetch stats:', err);
            }
        }

        // Load initial logs
        async function loadInitialLogs() {
            try {
                const response = await fetch('/api/logs');
                const data = await response.json();
                data.logs.forEach(line => addLogLine(line));
            } catch (err) {
                console.error('Failed to load logs:', err);
            }
        }

        // Load version from API
        async function loadVersion() {
            try {
                const response = await fetch('/api/version');
                const data = await response.json();
                if (data.version) {
                    document.getElementById('versionDisplay').textContent = 'v' + data.version;
                }
            } catch (err) {
                console.error('Failed to load version:', err);
            }
        }

        // Initialize
        connectWebSocket();
        loadInitialLogs();
        loadVersion();
        fetchStats();
        loadBots();  // Load bots immediately on dashboard
        loadAllPositions();  // Load positions on startup
        // Send current language to server
        fetch('/api/language', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ language: currentLang })
        }).catch(err => console.error('Failed to set language:', err));

        // Refresh stats every 5 seconds
        setInterval(fetchStats, 5000);

        // Refresh positions every 2 seconds for real-time PnL updates
        setInterval(loadAllPositions, 1500);

        // Regular bot refresh every 1.5 seconds for real-time PnL
        setInterval(loadBots, 1500);

        // API stats refresh every 10 seconds
        async function updateApiStats() {
            try {
                const response = await fetch('/api/ping');
                const data = await response.json();
                if (!data.error) {
                    const pingEl = document.getElementById('apiPing');
                    const ping = data.ping_ms || 0;
                    pingEl.textContent = ping;

                    // Ping color coding
                    // < 100ms = excellent (green)
                    // 100-300ms = good (yellow)
                    // 300-500ms = average (orange)
                    // > 500ms = slow (red)
                    if (ping < 100) {
                        pingEl.style.color = '#00ff88';  // Green - Excellent
                    } else if (ping < 300) {
                        pingEl.style.color = '#ffcc00';  // Yellow - Good
                    } else if (ping < 500) {
                        pingEl.style.color = '#ff9900';  // Orange - Average
                    } else {
                        pingEl.style.color = '#ff4444';  // Red - Slow
                    }

                    const requestsEl = document.getElementById('apiRequests');
                    const requests = data.requests_per_5s || 0;
                    requestsEl.textContent = requests;
                    document.getElementById('apiLimit').textContent = data.rate_limit || 600;

                    // API usage color based on percentage
                    // < 50% = normal (green)
                    // 50-80% = medium (orange)
                    // > 80% = high load (red)
                    const usage = data.rate_usage || 0;
                    if (usage > 80) {
                        requestsEl.style.color = '#ff4444';  // Red - High load
                    } else if (usage > 50) {
                        requestsEl.style.color = '#ffaa00';  // Orange - Medium load
                    } else {
                        requestsEl.style.color = '#00ff88';  // Green - Normal
                    }
                }
            } catch (err) {
                console.debug('Failed to fetch API stats:', err);
            }
        }
        updateApiStats();
        setInterval(updateApiStats, 10000);

        // Restart server function
        async function restartServer() {
            const confirmMsg = currentLang === 'ru'
                ? 'Вы уверены, что хотите перезагрузить сервер?\n\nВсе боты будут остановлены.'
                : 'Are you sure you want to restart the server?\n\nAll bots will be stopped.';

            if (!confirm(confirmMsg)) {
                return;
            }

            const btn = document.getElementById('restartServerBtn');
            const originalContent = btn.innerHTML;
            btn.disabled = true;
            btn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="spin"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>';
            btn.style.color = '#ffcc00';

            try {
                const response = await fetch('/api/restart-server', { method: 'POST' });
                const data = await response.json();

                if (data.status === 'restarting') {
                    btn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
                    btn.style.color = '#00ff88';

                    // Show restart overlay
                    showRestartOverlay();
                } else {
                    throw new Error(data.error || 'Unknown error');
                }
            } catch (err) {
                console.error('Restart failed:', err);
                btn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
                btn.style.color = '#ff4444';

                const errMsg = currentLang === 'ru'
                    ? 'Ошибка перезагрузки: ' + err.message
                    : 'Restart error: ' + err.message;
                alert(errMsg);

                setTimeout(() => {
                    btn.innerHTML = originalContent;
                    btn.style.color = '#ff6666';
                    btn.disabled = false;
                }, 2000);
            }
        }

        // Show restart overlay with auto-refresh
        function showRestartOverlay() {
            const overlay = document.createElement('div');
            overlay.id = 'restartOverlay';
            overlay.style.cssText = `
                position: fixed;
                top: 0;
                left: 0;
                width: 100%;
                height: 100%;
                background: rgba(26, 26, 46, 0.95);
                display: flex;
                flex-direction: column;
                align-items: center;
                justify-content: center;
                z-index: 10000;
            `;

            const spinner = document.createElement('div');
            spinner.style.cssText = `
                width: 50px;
                height: 50px;
                border: 3px solid rgba(0, 212, 255, 0.3);
                border-top-color: #00d4ff;
                border-radius: 50%;
                animation: spin 1s linear infinite;
                margin-bottom: 20px;
            `;

            const message = document.createElement('div');
            message.id = 'restartMessage';
            message.style.cssText = `
                color: #fff;
                font-size: 18px;
                text-align: center;
                margin-bottom: 10px;
            `;
            message.textContent = currentLang === 'ru' ? 'Перезапуск сервера...' : 'Restarting server...';

            const countdown = document.createElement('div');
            countdown.id = 'restartCountdown';
            countdown.style.cssText = `
                color: #888;
                font-size: 14px;
            `;

            const refreshBtn = document.createElement('button');
            refreshBtn.id = 'manualRefreshBtn';
            refreshBtn.style.cssText = `
                display: none;
                margin-top: 20px;
                padding: 12px 24px;
                background: #00d4ff;
                color: #1a1a2e;
                border: none;
                border-radius: 8px;
                font-size: 16px;
                cursor: pointer;
                font-weight: 500;
            `;
            refreshBtn.textContent = currentLang === 'ru' ? 'Обновить страницу' : 'Refresh page';
            refreshBtn.onclick = () => location.reload();

            overlay.appendChild(spinner);
            overlay.appendChild(message);
            overlay.appendChild(countdown);
            overlay.appendChild(refreshBtn);
            document.body.appendChild(overlay);

            // Add spin animation if not exists
            if (!document.getElementById('restartSpinStyle')) {
                const style = document.createElement('style');
                style.id = 'restartSpinStyle';
                style.textContent = '@keyframes spin { to { transform: rotate(360deg); } }';
                document.head.appendChild(style);
            }

            // Countdown and auto-refresh
            let seconds = 5;
            const updateCountdown = () => {
                countdown.textContent = currentLang === 'ru'
                    ? `Обновление через ${seconds} сек...`
                    : `Refreshing in ${seconds} sec...`;
            };
            updateCountdown();

            const timer = setInterval(() => {
                seconds--;
                if (seconds > 0) {
                    updateCountdown();
                } else {
                    clearInterval(timer);
                    tryRefresh();
                }
            }, 1000);

            // Try to refresh and check if server is back
            async function tryRefresh() {
                countdown.textContent = currentLang === 'ru' ? 'Проверка сервера...' : 'Checking server...';

                try {
                    const response = await fetch('/api/status', {
                        method: 'GET',
                        cache: 'no-store'
                    });
                    if (response.ok) {
                        location.reload();
                    } else {
                        throw new Error('Server not ready');
                    }
                } catch (e) {
                    // Server not ready, show manual refresh button
                    message.textContent = currentLang === 'ru'
                        ? 'Сервер ещё перезапускается...'
                        : 'Server is still restarting...';
                    countdown.textContent = currentLang === 'ru'
                        ? 'Подождите или обновите вручную'
                        : 'Please wait or refresh manually';
                    refreshBtn.style.display = 'block';

                    // Keep trying every 3 seconds
                    setTimeout(tryRefresh, 3000);
                }
            }
        }

        // Fast refresh for starting/stopping states (every 500ms)
        setInterval(async () => {
            const needsRefresh = botsData.some(b => b.status === 'starting' || b.status === 'stopping');
            if (needsRefresh) {
                await loadBots();
            }
        }, 500);

        // Bot control functions
        async function startBot() {
            try {
                const btn = document.getElementById('startBtn');
                btn.disabled = true;
                btn.innerHTML = '<span class="btn-icon">⏳</span> ' + t('starting');

                const response = await fetch('/api/bot/start', { method: 'POST' });
                const data = await response.json();

                if (data.success) {
                    showToast(t('botStarted'));
                    document.getElementById('startBtn').disabled = true;
                    document.getElementById('stopBtn').disabled = false;
                    btn.innerHTML = '<span class="btn-icon">▶</span> ' + t('running');
                } else {
                    showToast(t('failedStart') + ': ' + data.message);
                    btn.disabled = false;
                    btn.innerHTML = '<span class="btn-icon">▶</span> ' + t('startBot');
                }
            } catch (err) {
                console.error('Failed to start bot:', err);
                showToast(t('failedStart'));
                document.getElementById('startBtn').disabled = false;
                document.getElementById('startBtn').innerHTML = '<span class="btn-icon">▶</span> ' + t('startBot');
            }
        }

        async function stopBot() {
            try {
                const btn = document.getElementById('stopBtn');
                btn.disabled = true;
                btn.innerHTML = '<span class="btn-icon">⏳</span> ' + t('stopping');

                const response = await fetch('/api/bot/stop', { method: 'POST' });
                const data = await response.json();

                if (data.success) {
                    showToast(t('botStopped'));
                    document.getElementById('startBtn').disabled = false;
                    document.getElementById('startBtn').innerHTML = '<span class="btn-icon">▶</span> ' + t('startBot');
                    btn.innerHTML = '<span class="btn-icon">■</span> ' + t('stopBot');
                } else {
                    showToast(t('failedStop') + ': ' + data.message);
                    btn.disabled = false;
                }
            } catch (err) {
                console.error('Failed to stop bot:', err);
                showToast(t('failedStop'));
                document.getElementById('stopBtn').disabled = false;
            }
        }

        function toggleSettings() {
            const panel = document.getElementById('settingsPanel');
            if (panel.style.display === 'none') {
                panel.style.display = 'block';
                loadSettings();
            } else {
                panel.style.display = 'none';
            }
        }

        async function loadSettings() {
            try {
                const response = await fetch('/api/settings');
                const data = await response.json();

                document.getElementById('timeframe').value = data.timeframe || '1h';
                document.getElementById('tradingPairs').value = (data.trading_pairs || []).join(',');
                document.getElementById('riskPerTrade').value = data.risk_per_trade || 2;
                document.getElementById('tpRatio').value = data.tp_risk_ratio || 2;
                document.getElementById('slMode').value = data.sl_mode || 'supertrend_line';
                document.getElementById('leverage').value = data.leverage || 10;
                document.getElementById('maxPositions').value = data.max_open_positions || 3;
                document.getElementById('emaEnabled').value = data.ema_enabled ? 'true' : 'false';
            } catch (err) {
                console.error('Failed to load settings:', err);
            }
        }

        async function saveSettings() {
            try {
                const settings = {
                    timeframe: document.getElementById('timeframe').value,
                    trading_pairs: document.getElementById('tradingPairs').value.split(',').map(s => s.trim()),
                    risk_per_trade: parseFloat(document.getElementById('riskPerTrade').value),
                    tp_risk_ratio: parseFloat(document.getElementById('tpRatio').value),
                    sl_mode: document.getElementById('slMode').value,
                    leverage: parseInt(document.getElementById('leverage').value),
                    max_open_positions: parseInt(document.getElementById('maxPositions').value),
                    ema_enabled: document.getElementById('emaEnabled').value === 'true'
                };

                const response = await fetch('/api/settings', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(settings)
                });

                const data = await response.json();
                if (data.success) {
                    showToast(t('settingsSaved'));
                } else {
                    showToast(t('failedSave') + ': ' + data.message);
                }
            } catch (err) {
                console.error('Failed to save settings:', err);
                showToast(t('failedSave'));
            }
        }

        // Check bot status on load
        async function checkBotStatus() {
            try {
                const response = await fetch('/api/bot/status');
                const data = await response.json();

                if (data.running) {
                    document.getElementById('startBtn').disabled = true;
                    document.getElementById('startBtn').innerHTML = '<span class="btn-icon">▶</span> ' + t('running');
                    document.getElementById('stopBtn').disabled = false;
                }
            } catch (err) {
                console.error('Failed to check bot status:', err);
            }
        }

        // Initialize UI
        updateUI();
        checkBotStatus();

        // Global countdown timer for bot cards
        function updateAllBotCountdowns() {
            const countdownElements = document.querySelectorAll('.bot-countdown');
            const now = Math.floor(Date.now() / 1000);

            countdownElements.forEach(el => {
                const timeframe = el.dataset.timeframe;
                if (!timeframe) return;

                const durations = {
                    '1m': 60, '3m': 180, '5m': 300, '15m': 900, '30m': 1800,
                    '1h': 3600, '2h': 7200, '4h': 14400, '1d': 86400
                };
                const tfDuration = durations[timeframe] || 900;
                const candleStart = Math.floor(now / tfDuration) * tfDuration;
                const candleEnd = candleStart + tfDuration;
                const remaining = candleEnd - now;

                // Format countdown
                const h = Math.floor(remaining / 3600);
                const m = Math.floor((remaining % 3600) / 60);
                const s = remaining % 60;
                if (h > 0) {
                    el.textContent = `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
                } else {
                    el.textContent = `${m}:${s.toString().padStart(2, '0')}`;
                }
            });
        }

        // Update countdowns every second
        setInterval(updateAllBotCountdowns, 1000);

        // Update bot runtime counters
        function updateAllBotRuntimes() {
            const runtimeElements = document.querySelectorAll('.bot-runtime');
            const now = new Date();

            runtimeElements.forEach(el => {
                const startedAt = el.dataset.started;
                if (!startedAt) return;

                const startTime = new Date(startedAt);
                const diff = Math.floor((now - startTime) / 1000);
                const hours = Math.floor(diff / 3600);
                const mins = Math.floor((diff % 3600) / 60);
                const secs = diff % 60;
                el.textContent = `${hours.toString().padStart(2,'0')}:${mins.toString().padStart(2,'0')}:${secs.toString().padStart(2,'0')}`;
            });
        }

        // Update runtimes every second
        setInterval(updateAllBotRuntimes, 1000);

        // Tab Navigation
        function showTab(tabName) {
            // Hide all tabs
            document.querySelectorAll('.tab-content').forEach(tab => {
                tab.classList.remove('active');
            });
            document.querySelectorAll('.tab').forEach(btn => {
                btn.classList.remove('active');
            });

            // Show selected tab
            document.getElementById('tab-' + tabName).classList.add('active');
            event.target.classList.add('active');

            // Load data for specific tabs
            if (tabName === 'charts') loadCharts();
            if (tabName === 'pairs') loadTradingPairs();
        }

        // Modal functions
        let allTradingPairs = [];
        let coinIcons = {};  // Cache for coin icons: symbol -> icon_url

        async function loadTradingPairsCache() {
            try {
                const response = await fetch('/api/trading-pairs');
                const data = await response.json();
                allTradingPairs = data.pairs || [];
            } catch (err) {
                console.error('Failed to load trading pairs:', err);
                allTradingPairs = [];
            }
        }

        async function loadCoinIcons() {
            try {
                const response = await fetch('/api/coin-icons');
                const data = await response.json();
                coinIcons = data.icons || {};
                console.log('Loaded', Object.keys(coinIcons).length, 'coin icons');
            } catch (err) {
                console.error('Failed to load coin icons:', err);
                coinIcons = {};
            }
        }

        // Get coin icon HTML (with fallback to first letter)
        function getCoinIconHtml(symbol, size = 16) {
            // Extract base coin from pair (e.g., "BTC" from "BTCUSDT")
            const baseCoin = symbol.replace(/USDT$|USDC$|BUSD$|USD$/, '');
            const iconUrl = coinIcons[baseCoin];

            if (iconUrl) {
                return `<img src="${iconUrl}" alt="${baseCoin}" style="width: ${size}px; height: ${size}px; border-radius: 50%; vertical-align: middle; margin-right: 6px; object-fit: cover;">`;
            } else {
                // Fallback: first letter in a circle
                const firstLetter = baseCoin.charAt(0).toUpperCase();
                const colors = ['#f7931a', '#627eea', '#26a17b', '#e84142', '#8247e5', '#00d4aa', '#ff6b35', '#3498db'];
                const colorIndex = baseCoin.charCodeAt(0) % colors.length;
                const bgColor = colors[colorIndex];
                return `<span style="display: inline-flex; align-items: center; justify-content: center; width: ${size}px; height: ${size}px; border-radius: 50%; background: ${bgColor}; color: #fff; font-size: ${Math.round(size * 0.6)}px; font-weight: bold; vertical-align: middle; margin-right: 6px;">${firstLetter}</span>`;
            }
        }

        // Load pairs and icons on page load (only once)
        if (allTradingPairs.length === 0) {
            loadTradingPairsCache();
        }
        if (Object.keys(coinIcons).length === 0) {
            loadCoinIcons();
        }

        function showPairDropdown(prefix) {
            const dropdown = document.getElementById(prefix + 'PairDropdown');
            dropdown.style.display = 'block';
            filterTradingPairs(prefix);
        }

        function hidePairDropdown(prefix) {
            setTimeout(() => {
                const dropdown = document.getElementById(prefix + 'PairDropdown');
                dropdown.style.display = 'none';
            }, 200);
        }

        function filterTradingPairs(prefix) {
            const searchInput = document.getElementById(prefix + 'BotPairSearch');
            const dropdown = document.getElementById(prefix + 'PairDropdown');
            const searchValue = searchInput.value.toUpperCase().trim();

            // Filter out dated futures (quarterly contracts like BTCUSDT-16JAN26)
            // Only show perpetual contracts (without date suffix)
            const perpetualPairs = allTradingPairs.filter(pair =>
                !/-\d{2}[A-Z]{3}\d{2}$/.test(pair.symbol)
            );

            // Then filter by search
            const matchingPairs = perpetualPairs.filter(pair =>
                pair.symbol.toUpperCase().includes(searchValue) ||
                pair.base.toUpperCase().includes(searchValue)
            );

            // Deduplicate by symbol using Map for guaranteed uniqueness
            const pairsMap = new Map();
            for (const pair of matchingPairs) {
                if (!pairsMap.has(pair.symbol)) {
                    pairsMap.set(pair.symbol, pair);
                }
            }
            const uniquePairs = Array.from(pairsMap.values()).slice(0, 50);

            // Clear dropdown completely
            dropdown.innerHTML = '';

            uniquePairs.forEach((pair, index) => {
                const item = document.createElement('div');
                item.className = 'pair-dropdown-item';
                item.id = `pair-item-${prefix}-${index}`;
                item.style.cssText = 'padding: 8px 12px; cursor: pointer; border-bottom: 1px solid #333; display: flex; align-items: center;';
                item.innerHTML = `${getCoinIconHtml(pair.symbol, 18)}<strong>${pair.symbol}</strong>`;
                item.onmouseover = () => item.style.background = '#2a2a4e';
                item.onmouseout = () => item.style.background = 'transparent';
                item.onclick = () => selectPair(prefix, pair.symbol);
                dropdown.appendChild(item);
            });

            if (uniquePairs.length === 0) {
                dropdown.innerHTML = '<div style="padding: 12px; color: #888; text-align: center;">No pairs found</div>';
            }
        }

        function selectPair(prefix, symbol) {
            document.getElementById(prefix + 'BotPairSearch').value = symbol;
            document.getElementById(prefix + 'BotPair').value = symbol;
            document.getElementById(prefix + 'PairDropdown').style.display = 'none';
            // Update pair info when pair is selected
            updatePairInfo(prefix, symbol);
        }

        function clearPairSearch(prefix) {
            document.getElementById(prefix + 'BotPairSearch').value = '';
            document.getElementById(prefix + 'BotPair').value = '';
            // Hide pair info when cleared
            document.getElementById(prefix + 'PairInfoContainer').style.display = 'none';
            showPairDropdown(prefix);
        }

        // Update pair info display (min order, max leverage)
        function updatePairInfo(prefix, symbol) {
            const pair = allTradingPairs.find(p => p.symbol === symbol);
            if (!pair) return;

            // Pair info container stays hidden - values are used programmatically
            // const container = document.getElementById(prefix + 'PairInfoContainer');
            // if (container) container.style.display = 'block';

            // Update min order
            const minOrderEl = document.getElementById(prefix + 'PairMinOrder');
            if (minOrderEl) minOrderEl.textContent = pair.minNotional || '5';

            // Update max leverage
            const maxLeverageEl = document.getElementById(prefix + 'PairMaxLeverage');
            if (maxLeverageEl) maxLeverageEl.textContent = pair.maxLeverage || '100';

            // Update leverage dropdown options based on max leverage
            updateLeverageOptions(prefix, pair.maxLeverage || 100);

            // Update deposit info
            updateDepositInfo(prefix);
        }

        // Update leverage dropdown based on max leverage for the pair
        function updateLeverageOptions(prefix, maxLeverage) {
            const leverageSelect = document.getElementById(prefix + 'BotLeverage');
            if (!leverageSelect) return;

            const currentValue = parseInt(leverageSelect.value) || 10;
            const leverageOptions = [1, 2, 3, 5, 10, 15, 20, 25, 50, 75, 100, 125, 150, 200];

            // Clear and rebuild options
            leverageSelect.innerHTML = '';
            leverageOptions.forEach(lev => {
                if (lev <= maxLeverage) {
                    const option = document.createElement('option');
                    option.value = lev;
                    option.textContent = lev + 'x';
                    if (lev === currentValue || (lev === 10 && currentValue > maxLeverage)) {
                        option.selected = true;
                    }
                    leverageSelect.appendChild(option);
                }
            });

            // If current value exceeds max, select the highest available
            if (currentValue > maxLeverage) {
                leverageSelect.value = leverageSelect.options[leverageSelect.options.length - 1].value;
            }
        }

        // Calculate and display deposit used based on order size and leverage
        function updateDepositInfo(prefix) {
            const orderSizeEl = document.getElementById(prefix + 'BotOrderSize');
            const leverageEl = document.getElementById(prefix + 'BotLeverage');
            const depositUsedEl = document.getElementById(prefix + 'DepositUsed');

            if (!orderSizeEl || !leverageEl || !depositUsedEl) return;

            const orderSize = parseFloat(orderSizeEl.value) || 100;
            const leverage = parseInt(leverageEl.value) || 10;
            const depositUsed = (orderSize / leverage).toFixed(2);

            depositUsedEl.textContent = depositUsed;
        }

        async function showCreateBotModal() {
            // Reload pairs if empty
            if (allTradingPairs.length === 0) {
                await loadTradingPairsCache();
            }

            // Reset search field
            document.getElementById('newBotPairSearch').value = 'BTCUSDT';
            document.getElementById('newBotPair').value = 'BTCUSDT';

            // Update pair info for default pair
            updatePairInfo('new', 'BTCUSDT');

            // Update balance usage info
            try { updateBalanceUsageInfo('new'); } catch(e) { console.error(e); }
            try { updateMaxLossInfo('new'); } catch(e) { console.error(e); }

            document.getElementById('createBotModal').classList.add('show');
        }

        function closeModal(modalId) {
            document.getElementById(modalId).classList.remove('show');
            // Hide dropdowns
            const dropdowns = document.querySelectorAll('.pair-dropdown');
            dropdowns.forEach(d => d.style.display = 'none');
        }

        // Strategy Dropdown and Settings
        function toggleStrategyDropdown(event) {
            event.stopPropagation();
            const dropdown = document.getElementById('strategyDropdown');
            dropdown.classList.toggle('show');
        }

        // Close dropdown when clicking outside
        document.addEventListener('click', function(e) {
            const dropdown = document.getElementById('strategyDropdown');
            if (dropdown && !e.target.closest('.strategy-dropdown')) {
                dropdown.classList.remove('show');
            }
        });

        function openStrategySettings(strategyType) {
            // Close dropdown
            document.getElementById('strategyDropdown').classList.remove('show');

            if (strategyType === '3ema_pullback') {
                // Load current settings and status
                loadStrategySettings();
                loadStrategyStatus();
                // Show modal
                document.getElementById('strategyModal').classList.add('show');
            }
        }

        async function loadStrategySettings() {
            try {
                const response = await fetch('/api/strategy/3ema_pullback/settings');
                const data = await response.json();
                if (data.settings) {
                    populateStrategyForm(data.settings);
                }
            } catch (err) {
                console.error('Failed to load strategy settings:', err);
            }
        }

        function populateStrategyForm(s) {
            // Basic
            document.getElementById('strategyName').value = s.name || '3 EMA Scalping';
            document.getElementById('strategyEnabled').checked = s.enabled || false;
            document.getElementById('strategyMaxPairs').value = s.max_pairs || 1;
            document.getElementById('strategyTimeframe').value = s.timeframe || '1m';
            document.getElementById('strategyLeverage').value = s.leverage || 10;
            document.getElementById('strategyOrderSize').value = s.order_size_usdt || 10;
            document.getElementById('strategyPositionMode').value = s.position_size_mode || 'fixed';
            document.getElementById('strategyMarginMode').value = s.margin_mode || 'isolated';

            // Risk
            document.getElementById('strategyBalanceUsage').value = s.balance_usage_pct || 30;
            document.getElementById('strategyBalanceValue').textContent = (s.balance_usage_pct || 30) + '%';
            document.getElementById('strategyRiskPerTrade').value = s.risk_per_trade || 2;
            document.getElementById('strategyDailyLossEnabled').checked = s.daily_loss_limit_enabled !== false;
            document.getElementById('strategyDailyLoss').value = s.daily_loss_limit_pct || 5;
            document.getElementById('strategyLosingStreakEnabled').checked = s.max_losing_streak_enabled !== false;
            document.getElementById('strategyMaxLosingStreak').value = s.max_losing_streak || 3;
            document.getElementById('strategyDailyTradesEnabled').checked = s.max_daily_trades_enabled || false;
            document.getElementById('strategyMaxDailyTrades').value = s.max_daily_trades || 10;

            // EMA
            document.getElementById('strategyEmaFast').value = s.ema_fast || 50;
            document.getElementById('strategyEmaMedium').value = s.ema_medium || 100;
            document.getElementById('strategyEmaSlow').value = s.ema_slow || 150;

            // Trend
            document.getElementById('strategySlopeMin').value = s.slope_min || 0.1;
            document.getElementById('strategySlopeCandles').value = s.slope_candles || 5;
            document.getElementById('strategyEmaDistanceAtr').value = s.ema_distance_atr_mult || 1;
            document.getElementById('strategyAtrPeriod').value = s.atr_period || 14;

            // Entry
            document.getElementById('strategyTouchMode').value = s.touch_mode || 'low_high';
            document.getElementById('strategyTouchTolerance').value = s.touch_tolerance || 0.1;
            document.getElementById('strategyForbidEma150').checked = s.forbid_ema150_touch !== false;

            // SL
            document.getElementById('strategySlMode').value = s.sl_mode || 'pullback';
            document.getElementById('strategySlBuffer').value = s.sl_buffer || 0.1;
            document.getElementById('strategySlFixed').value = s.sl_fixed_pct || 1;

            // TP
            document.getElementById('strategyTpMode').value = s.tp_mode || 'trailing';
            document.getElementById('strategyRrRatio').value = s.rr_ratio || 1.5;
            document.getElementById('strategyTrailingBe').checked = s.trailing_be_enabled !== false;
            document.getElementById('strategyBeAfterProfit').value = s.be_after_profit_pct || 0.3;
            document.getElementById('strategyTrailingMode').value = s.trailing_mode || 'new_high_low';
            document.getElementById('strategyTrailingBuffer').value = s.trailing_buffer || 0.1;

            // Direction
            document.getElementById('strategyTradeLong').checked = s.trade_long !== false;
            document.getElementById('strategyTradeShort').checked = s.trade_short !== false;

            // Update UI visibility
            toggleStrategyDailyLoss();
            toggleStrategyLosingStreak();
            toggleStrategyDailyTrades();
            toggleStrategySlMode();
            toggleStrategyTpMode();
            toggleStrategyTrailingBe();
        }

        async function saveStrategySettings() {
            const settings = {
                // Basic
                name: document.getElementById('strategyName').value,
                enabled: document.getElementById('strategyEnabled').checked,
                max_pairs: parseInt(document.getElementById('strategyMaxPairs').value),
                timeframe: document.getElementById('strategyTimeframe').value,
                leverage: parseInt(document.getElementById('strategyLeverage').value),
                order_size_usdt: parseFloat(document.getElementById('strategyOrderSize').value),
                position_size_mode: document.getElementById('strategyPositionMode').value,
                margin_mode: document.getElementById('strategyMarginMode').value,

                // Risk
                balance_usage_pct: parseInt(document.getElementById('strategyBalanceUsage').value),
                risk_per_trade: parseFloat(document.getElementById('strategyRiskPerTrade').value),
                daily_loss_limit_enabled: document.getElementById('strategyDailyLossEnabled').checked,
                daily_loss_limit_pct: parseFloat(document.getElementById('strategyDailyLoss').value),
                max_losing_streak_enabled: document.getElementById('strategyLosingStreakEnabled').checked,
                max_losing_streak: parseInt(document.getElementById('strategyMaxLosingStreak').value),
                max_daily_trades_enabled: document.getElementById('strategyDailyTradesEnabled').checked,
                max_daily_trades: parseInt(document.getElementById('strategyMaxDailyTrades').value),

                // EMA
                ema_fast: parseInt(document.getElementById('strategyEmaFast').value),
                ema_medium: parseInt(document.getElementById('strategyEmaMedium').value),
                ema_slow: parseInt(document.getElementById('strategyEmaSlow').value),

                // Trend
                slope_min: parseFloat(document.getElementById('strategySlopeMin').value),
                slope_candles: parseInt(document.getElementById('strategySlopeCandles').value),
                ema_distance_atr_mult: parseFloat(document.getElementById('strategyEmaDistanceAtr').value),
                atr_period: parseInt(document.getElementById('strategyAtrPeriod').value),

                // Entry
                touch_mode: document.getElementById('strategyTouchMode').value,
                touch_tolerance: parseFloat(document.getElementById('strategyTouchTolerance').value),
                forbid_ema150_touch: document.getElementById('strategyForbidEma150').checked,

                // SL
                sl_mode: document.getElementById('strategySlMode').value,
                sl_buffer: parseFloat(document.getElementById('strategySlBuffer').value),
                sl_fixed_pct: parseFloat(document.getElementById('strategySlFixed').value),

                // TP
                tp_mode: document.getElementById('strategyTpMode').value,
                rr_ratio: parseFloat(document.getElementById('strategyRrRatio').value),
                trailing_be_enabled: document.getElementById('strategyTrailingBe').checked,
                be_after_profit_pct: parseFloat(document.getElementById('strategyBeAfterProfit').value),
                trailing_mode: document.getElementById('strategyTrailingMode').value,
                trailing_buffer: parseFloat(document.getElementById('strategyTrailingBuffer').value),

                // Direction
                trade_long: document.getElementById('strategyTradeLong').checked,
                trade_short: document.getElementById('strategyTradeShort').checked
            };

            try {
                const response = await fetch('/api/strategy/3ema_pullback/settings', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(settings)
                });
                const data = await response.json();
                if (data.success) {
                    closeModal('strategyModal');
                    addLog(t('strategySaved') || 'Strategy settings saved');
                } else {
                    alert(data.error || 'Failed to save settings');
                }
            } catch (err) {
                console.error('Failed to save strategy settings:', err);
                alert('Failed to save strategy settings');
            }
        }

        // Strategy toggle functions
        function toggleStrategyDailyLoss() {
            const enabled = document.getElementById('strategyDailyLossEnabled').checked;
            document.getElementById('strategyDailyLossContainer').style.display = enabled ? 'block' : 'none';
        }

        function toggleStrategyLosingStreak() {
            const enabled = document.getElementById('strategyLosingStreakEnabled').checked;
            document.getElementById('strategyLosingStreakContainer').style.display = enabled ? 'block' : 'none';
        }

        function toggleStrategyDailyTrades() {
            const enabled = document.getElementById('strategyDailyTradesEnabled').checked;
            document.getElementById('strategyDailyTradesContainer').style.display = enabled ? 'block' : 'none';
        }

        function toggleStrategySlMode() {
            const mode = document.getElementById('strategySlMode').value;
            document.getElementById('strategySlBufferContainer').style.display = mode === 'pullback' ? 'flex' : 'none';
            document.getElementById('strategySlFixedContainer').style.display = mode === 'fixed' ? 'flex' : 'none';
        }

        function toggleStrategyTpMode() {
            const mode = document.getElementById('strategyTpMode').value;
            document.getElementById('strategyRrContainer').style.display = mode === 'fixed_rr' ? 'flex' : 'none';
            document.getElementById('strategyTrailingContainer').style.display = mode === 'trailing' ? 'block' : 'none';
        }

        function toggleStrategyTrailingBe() {
            const enabled = document.getElementById('strategyTrailingBe').checked;
            document.getElementById('strategyBeAfterContainer').style.display = enabled ? 'block' : 'none';
        }

        // Strategy Start/Stop functions
        async function startStrategy() {
            try {
                // Save settings first
                await saveStrategySettings();

                const response = await fetch('/api/strategy/3ema_pullback/start', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' }
                });
                const data = await response.json();

                if (data.success) {
                    addLog(t('strategyStarted') || 'Strategy started');
                    updateStrategyStatusUI(true);
                } else {
                    alert(data.error || 'Failed to start strategy');
                }
            } catch (err) {
                console.error('Failed to start strategy:', err);
                alert('Failed to start strategy');
            }
        }

        async function stopStrategy() {
            try {
                const response = await fetch('/api/strategy/3ema_pullback/stop', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' }
                });
                const data = await response.json();

                if (data.success) {
                    addLog(t('strategyStopped') || 'Strategy stopped');
                    updateStrategyStatusUI(false);
                } else {
                    alert(data.error || 'Failed to stop strategy');
                }
            } catch (err) {
                console.error('Failed to stop strategy:', err);
                alert('Failed to stop strategy');
            }
        }

        function updateStrategyStatusUI(running) {
            const indicator = document.getElementById('strategyStatusIndicator');
            const startBtn = document.getElementById('strategyStartBtn');
            const stopBtn = document.getElementById('strategyStopBtn');
            const posInfo = document.getElementById('strategyPositionsInfo');

            if (running) {
                indicator.textContent = t('running') || 'Running';
                indicator.style.background = 'rgba(0, 255, 136, 0.2)';
                indicator.style.color = '#00ff88';
                startBtn.style.display = 'none';
                stopBtn.style.display = 'block';
                posInfo.style.display = 'block';
            } else {
                indicator.textContent = t('stopped') || 'Stopped';
                indicator.style.background = '#333';
                indicator.style.color = '#888';
                startBtn.style.display = 'block';
                stopBtn.style.display = 'none';
                posInfo.style.display = 'none';
            }
        }

        async function loadStrategyStatus() {
            try {
                const response = await fetch('/api/strategy/3ema_pullback/status');
                const data = await response.json();
                updateStrategyStatusUI(data.running);
                if (data.positions !== undefined) {
                    document.getElementById('strategyPositionsCount').textContent = data.positions;
                }
            } catch (err) {
                console.error('Failed to load strategy status:', err);
            }
        }

        // Bots Management
        let botsData = [];

        // Chart storage for real-time updates (must be defined before renderBots)
        const botCharts = {};
        const chartUpdateIntervals = {};

        async function loadBots() {
            try {
                const response = await fetch('/api/bots');
                const data = await response.json();
                botsData = data.bots || [];
                renderBots();
            } catch (err) {
                console.error('Failed to load bots:', err);
            }
        }

        // Positions data storage
        let positionsData = [];

        async function loadAllPositions() {
            try {
                const response = await fetch('/api/positions');
                const data = await response.json();
                positionsData = (data.positions || []).filter(p => p.status === 'open');
                renderPositions();
            } catch (err) {
                console.error('Failed to load positions:', err);
            }
        }

        function renderPositions() {
            const section = document.getElementById('positionsSection');
            const grid = document.getElementById('positionsGrid');

            if (positionsData.length === 0) {
                section.style.display = 'none';
                return;
            }

            section.style.display = 'block';

            grid.innerHTML = positionsData.map(pos => {
                const pnlClass = pos.pnl_usdt >= 0 ? 'positive' : 'negative';
                const pnlSign = pos.pnl_usdt >= 0 ? '+' : '';
                const sideColor = pos.side.toUpperCase() === 'LONG' ? '#00ff88' : '#ff4444';
                const sideIcon = pos.side.toUpperCase() === 'LONG' ? '📈' : '📉';

                // Find parent bot name
                const parentBot = botsData.find(b => b.id === pos.bot_id);
                const botName = parentBot ? parentBot.name : 'Unknown';

                return `
                <div class="position-card" style="
                    background: rgba(255, 255, 255, 0.05);
                    border-radius: 10px;
                    padding: 15px;
                    border: 1px solid rgba(255, 255, 255, 0.1);
                    border-left: 3px solid ${sideColor};
                    cursor: pointer;
                " onclick="openPositionChart('${pos.id}')">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
                        <div style="font-size: 16px; font-weight: bold; color: #fff; display: flex; align-items: center;">
                            ${getCoinIconHtml(pos.symbol, 16)}${pos.symbol}
                        </div>
                        <span style="color: ${sideColor}; font-weight: bold;">${sideIcon} ${pos.side.toUpperCase()}</span>
                    </div>
                    <div style="display: flex; justify-content: space-between; margin-bottom: 8px;">
                        <span style="color: #888; font-size: 12px;">Entry: ${pos.entry_price.toFixed(6)}</span>
                        <span style="color: #00d4ff; font-size: 12px;">Current: ${pos.current_price.toFixed(6)}</span>
                    </div>
                    <div style="display: flex; justify-content: space-between; margin-bottom: 8px;">
                        <span style="color: #ff4444; font-size: 11px;">SL: ${pos.sl.toFixed(6)}</span>
                        <span style="color: #00ff88; font-size: 11px;">TP: ${pos.tp.toFixed(6)}</span>
                    </div>
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; padding: 8px; background: rgba(0,0,0,0.3); border-radius: 5px;">
                        <span class="${pnlClass}" style="font-size: 18px; font-weight: bold;">
                            ${pnlSign}${pos.pnl_usdt.toFixed(4)} USDT
                        </span>
                        <span class="${pnlClass}" style="font-size: 14px;">
                            (${pnlSign}${pos.pnl_percent.toFixed(2)}%)
                        </span>
                    </div>
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <span style="color: #666; font-size: 10px;">Bot: ${botName}</span>
                        <button class="btn btn-danger" style="padding: 5px 10px; font-size: 11px;" onclick="event.stopPropagation(); closePosition('${pos.id}')">
                            Close
                        </button>
                    </div>
                </div>
                `;
            }).join('');
        }

        async function closePosition(positionId) {
            if (!confirm(currentLang === 'ru' ? 'Закрыть позицию?' : 'Close this position?')) return;

            try {
                const response = await fetch('/api/positions/' + positionId + '/close', { method: 'POST' });
                const data = await response.json();

                if (data.success) {
                    showToast(currentLang === 'ru' ? 'Позиция закрыта' : 'Position closed');
                    loadAllPositions();
                    loadBots(); // Refresh bot stats
                } else {
                    showToast(data.message);
                }
            } catch (err) {
                console.error('Failed to close position:', err);
                showToast(currentLang === 'ru' ? 'Ошибка закрытия' : 'Close failed');
            }
        }

        function renderBots() {
            const grid = document.getElementById('botsGrid');

            // Clean up all existing bot charts and intervals before re-rendering
            Object.keys(chartUpdateIntervals).forEach(botId => {
                clearInterval(chartUpdateIntervals[botId]);
                delete chartUpdateIntervals[botId];
            });
            Object.keys(botCharts).forEach(botId => {
                try {
                    if (botCharts[botId] && botCharts[botId].chart) {
                        botCharts[botId].chart.remove();
                    }
                } catch(e) {}
                delete botCharts[botId];
            });

            if (botsData.length === 0) {
                grid.innerHTML = '<p style="color: #888; text-align: center; padding: 40px;">' + t('noBots') + '</p>';
                // Update active bots counter
                document.getElementById('activeBots').textContent = '0';
                return;
            }

            // Update active bots counter
            const activeBots = botsData.filter(b => b.status === 'running').length;
            document.getElementById('activeBots').textContent = activeBots.toString();

            grid.innerHTML = botsData.map(bot => {
                const openCount = bot.open_positions_count || 0;
                const totalPnl = bot.total_pnl || 0;
                const pnlClass = totalPnl >= 0 ? 'positive' : 'negative';
                const pnlSign = totalPnl >= 0 ? '+' : '';

                // Calculate runtime
                let runtimeStr = '--:--:--';
                if (bot.started_at && (bot.status === 'running' || bot.status === 'paused')) {
                    const startTime = new Date(bot.started_at);
                    const now = new Date();
                    const diff = Math.floor((now - startTime) / 1000);
                    const hours = Math.floor(diff / 3600);
                    const mins = Math.floor((diff % 3600) / 60);
                    const secs = diff % 60;
                    runtimeStr = `${hours.toString().padStart(2,'0')}:${mins.toString().padStart(2,'0')}:${secs.toString().padStart(2,'0')}`;
                }

                // Win rate
                const winRate = bot.win_rate || 0;
                const totalTrades = bot.total_trades || 0;
                const winningTrades = bot.winning_trades || 0;
                const losingTrades = bot.losing_trades || 0;

                // Strategy settings display
                const slModeDisplay = bot.sl_mode === 'supertrend_line' ? `ST${bot.sl_supertrend_line || 2}` : (bot.sl_mode === 'fixed_percent' ? `${bot.sl_fixed_percent}%` : 'ATR');
                const trailingSl = bot.trailing_enabled ? (bot.trailing_mode === 'st_line' ? `ST${bot.trailing_st_line || 2}` : `${bot.trailing_activation}%`) : '✗';
                const trailingTp = bot.trailing_tp_enabled ? (bot.trailing_tp_mode === 'st_line' ? `ST${bot.trailing_tp_st_line || 1}` : `${bot.trailing_tp_activation}%`) : '✗';
                const partialTp = bot.partial_tp_enabled ? `${bot.partial_tp_close_percent}%` : '✗';

                // PnL color: green if positive, red if negative, white if zero
                const pnlColor = totalPnl > 0 ? '#00ff88' : (totalPnl < 0 ? '#ff4444' : '#ffffff');

                // Calculate allocated deposit from balance_usage_percent
                // If initial_balance is set (bot was started), use it
                // Otherwise calculate from current exchange balance * balance_usage_percent
                let allocatedDeposit = bot.initial_balance;
                if (!allocatedDeposit || allocatedDeposit <= 0) {
                    const globalBalanceText = document.getElementById('balance')?.textContent || '0';
                    const globalBalance = parseFloat(globalBalanceText.replace('--', '0')) || 0;
                    const balanceUsagePercent = bot.balance_usage_percent || 100;
                    allocatedDeposit = globalBalance * balanceUsagePercent / 100;
                }

                const pnlPercent = allocatedDeposit > 0 ? (totalPnl / allocatedDeposit * 100) : 0;
                const pnlPercentSign = pnlPercent >= 0 ? '+' : '';

                // Current balance = initial deposit + total PnL (shows how balance changes during session)
                const currentBalance = allocatedDeposit + totalPnl;
                const balanceColor = totalPnl > 0 ? '#00ff88' : (totalPnl < 0 ? '#ff4444' : '#00d4ff');

                // Status indicator class
                const indicatorClass = bot.status === 'error' ? 'error' : bot.status;

                return `
                <div class="bot-card ${bot.status === 'running' ? 'running' : ''} ${bot.status === 'starting' || bot.status === 'stopping' ? 'processing' : ''}" style="min-width: 380px;" data-bot-id="${bot.id}">
                    <div class="bot-header">
                        <span class="status-indicator ${indicatorClass}"></span>
                        <span class="bot-name">${bot.name}</span>
                        <span style="font-size: 16px; font-weight: bold; color: ${balanceColor}; margin-left: 10px;">${currentBalance.toFixed(2)} USDT</span>
                    </div>
                    <div class="bot-details" style="font-size: 12px;">
                        <div><strong>${t('botMode')}:</strong> ${bot.bot_mode === 'auto_search' ? '<span style="color: #ffcc00;">' + t('autoSearchMode') + '</span>' : t('manualMode')} | <strong>${bot.bot_mode === 'auto_search' ? 'Max' : 'Pair'}:</strong> ${bot.bot_mode === 'auto_search' ? bot.max_trading_pairs || bot.max_simultaneous_orders || 1 : (Array.isArray(bot.trading_pairs) ? bot.trading_pairs[0] : bot.trading_pairs)}</div>
                        <div><strong>TF:</strong> ${bot.timeframe} | <strong>Lev:</strong> ${bot.leverage}x | <strong>Order:</strong> ${bot.order_size} USDT | <strong>Margin:</strong> ${bot.leverage_mode || 'cross'}</div>
                        <div><strong>TP:</strong> ${bot.tp_mode === 'fixed' ? bot.tp_fixed_percent + '%' : bot.tp_risk_ratio + ':1'} | <strong>SL:</strong> ${slModeDisplay} | <strong>EMA:</strong> ${bot.ema_enabled ? (bot.ema_filter_mode || 'strict') : '✗'}</div>
                        <div><strong>Trail SL:</strong> ${trailingSl} | <strong>Trail TP:</strong> ${trailingTp} | <strong>Partial:</strong> ${partialTp}</div>
                    </div>

                    <!-- Stats block - always visible -->
                    <div class="bot-stats" style="margin: 10px 0; padding: 10px; background: rgba(0,0,0,0.3); border-radius: 8px;">
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                            <div style="text-align: center; flex: 1;">
                                <div style="font-size: 20px; font-weight: bold; color: #00d4ff;">${openCount}</div>
                                <div style="font-size: 10px; color: #888;">Open</div>
                            </div>
                            <div style="text-align: center; flex: 1.5;">
                                <div style="font-size: 18px; font-weight: bold; color: ${pnlColor};">
                                    ${pnlSign}${totalPnl.toFixed(2)} (${pnlPercentSign}${pnlPercent.toFixed(2)}%)
                                </div>
                                <div style="font-size: 10px; color: #888;">PnL</div>
                            </div>
                            <div style="text-align: center; flex: 1;">
                                <div style="font-size: 20px; font-weight: bold; color: ${winRate >= 50 ? '#00ff88' : (winRate > 0 ? '#ff4444' : '#888')};">${winRate.toFixed(0)}%</div>
                                <div style="font-size: 10px; color: #888;">Win Rate</div>
                            </div>
                            <div style="text-align: center; flex: 1;">
                                <div style="font-size: 20px; font-weight: bold; color: #888;">${totalTrades}</div>
                                <div style="font-size: 10px; color: #888;">Trades</div>
                            </div>
                        </div>
                        ${bot.status === 'running' || bot.status === 'paused' ? `
                        <div style="display: flex; justify-content: space-between; align-items: center; border-top: 1px solid rgba(255,255,255,0.1); padding-top: 8px;">
                            <div style="font-size: 11px; color: #888;">
                                <span style="color: #00ff88;">W:${winningTrades}</span> / <span style="color: #ff4444;">L:${losingTrades}</span>
                            </div>
                            <div style="font-size: 11px;">
                                <span style="color: #ffcc00;" class="bot-runtime" data-started="${bot.started_at}">${runtimeStr}</span>
                                <span style="color: #666;"> runtime</span>
                            </div>
                        </div>
                        ` : ''}
                    </div>
                    <div class="bot-actions">
                        ${bot.status === 'running' ?
                            `<button class="btn btn-danger" onclick="stopSpecificBot('${bot.id}')">${t('stop')}</button>
                             <button class="btn btn-warning" onclick="pauseBot('${bot.id}')" style="background: #ffcc00; color: #1a1a2e;">${t('pause')}</button>` :
                         bot.status === 'paused' ?
                            `<button class="btn btn-danger" onclick="stopSpecificBot('${bot.id}')">${t('stop')}</button>
                             <button class="btn btn-success" onclick="resumeBot('${bot.id}')">${t('resume')}</button>` :
                         bot.status === 'stopping' ?
                            `<button class="btn btn-danger" disabled>${t('stopping')}</button>` :
                         bot.status === 'starting' ?
                            `<button class="btn btn-success" disabled>${t('starting')}</button>` :
                            `<button class="btn btn-success" onclick="startSpecificBot('${bot.id}')">${t('start')}</button>`
                        }
                        <button class="btn btn-primary" onclick="showEditBotModal('${bot.id}')" ${bot.status !== 'stopped' && bot.status !== 'paused' ? 'disabled' : ''}>${t('edit')}</button>
                        <button class="btn btn-secondary" onclick="deleteBot('${bot.id}')" ${bot.status !== 'stopped' ? 'disabled' : ''}>${t('delete')}</button>
                    </div>
                </div>
            `;
            }).join('');

            // Load and render positions for running bots
            loadAllPositions();
        }

        // Get update interval - fast 1 second updates for real-time feel
        function getUpdateInterval(timeframe) {
            return 1000; // 1 second for all timeframes
        }

        // Create chart for a specific bot with indicators
        async function createBotChart(bot) {
            // For auto_search mode, show BTCUSDT as default chart
            let symbol = 'BTCUSDT';
            if (bot.bot_mode !== 'auto_search') {
                symbol = Array.isArray(bot.trading_pairs) ? bot.trading_pairs[0] : bot.trading_pairs;
            }
            const chartContainer = document.getElementById('bot-chart-' + bot.id);
            if (!chartContainer || typeof LightweightCharts === 'undefined') return;

            // Clear previous chart and interval
            if (botCharts[bot.id]) {
                botCharts[bot.id].chart.remove();
                delete botCharts[bot.id];
            }
            if (chartUpdateIntervals[bot.id]) {
                clearInterval(chartUpdateIntervals[bot.id]);
                delete chartUpdateIntervals[bot.id];
            }

            chartContainer.innerHTML = '';

            const chart = LightweightCharts.createChart(chartContainer, {
                width: chartContainer.clientWidth,
                height: 200,
                layout: {
                    background: { type: 'solid', color: 'transparent' },
                    textColor: '#888',
                },
                grid: {
                    vertLines: { color: 'rgba(255, 255, 255, 0.05)' },
                    horzLines: { color: 'rgba(255, 255, 255, 0.05)' },
                },
                rightPriceScale: { borderColor: 'rgba(255, 255, 255, 0.1)' },
                timeScale: { borderColor: 'rgba(255, 255, 255, 0.1)', timeVisible: true },
            });

            // Candlestick series
            const candlestickSeries = chart.addCandlestickSeries({
                upColor: '#00ff88',
                downColor: '#ff4444',
                borderDownColor: '#ff4444',
                borderUpColor: '#00ff88',
                wickDownColor: '#ff4444',
                wickUpColor: '#00ff88',
            });

            // Get interval from timeframe
            const tfMap = {'1m':'1','3m':'3','5m':'5','15m':'15','30m':'30','1h':'60','2h':'120','4h':'240','1d':'D'};
            const interval = tfMap[bot.timeframe] || '15';

            // Create indicator series
            let emaSeries = null;
            let st1Series = null;
            let st2Series = null;
            let st3Series = null;

            if (bot.ema_enabled) {
                emaSeries = chart.addLineSeries({
                    color: '#ffcc00',
                    lineWidth: 2,
                    title: 'EMA200',
                });
            }

            // SuperTrend lines with different thickness
            st1Series = chart.addLineSeries({
                lineWidth: 1,
                title: 'ST1 Fast',
                lastValueVisible: false,
                priceLineVisible: false,
            });

            st2Series = chart.addLineSeries({
                lineWidth: 2,
                title: 'ST2 Medium',
                lastValueVisible: false,
                priceLineVisible: false,
            });

            st3Series = chart.addLineSeries({
                lineWidth: 3,
                title: 'ST3 Slow',
                lastValueVisible: false,
                priceLineVisible: false,
            });

            // Store chart references
            botCharts[bot.id] = {
                chart,
                candlestickSeries,
                emaSeries,
                st1Series,
                st2Series,
                st3Series,
                symbol,
                interval,
                bot
            };

            // Load initial data
            await updateBotChartData(bot.id);

            // Fit content
            chart.timeScale().fitContent();

            // Handle resize
            new ResizeObserver(() => {
                chart.applyOptions({ width: chartContainer.clientWidth });
            }).observe(chartContainer);

            // Set up real-time updates
            const updateMs = getUpdateInterval(bot.timeframe);
            chartUpdateIntervals[bot.id] = setInterval(() => {
                updateBotChartData(bot.id);
            }, updateMs);
        }

        // Update chart data (for real-time updates)
        async function updateBotChartData(botId) {
            const chartData = botCharts[botId];
            if (!chartData) {
                console.log('No chart data for bot:', botId);
                return;
            }

            const { candlestickSeries, emaSeries, st1Series, st2Series, st3Series, symbol, interval, bot } = chartData;

            // Show updating indicator
            const chartContainer = document.getElementById('bot-chart-' + botId);
            if (chartContainer) {
                const updateIndicator = chartContainer.parentElement.querySelector('.update-time');
                if (updateIndicator) {
                    updateIndicator.textContent = 'Updating...';
                }
            }

            // Load klines
            try {
                const klinesResponse = await fetch(`/api/klines/${symbol}?interval=${interval}&limit=500`);
                const klinesData = await klinesResponse.json();
                console.log(`Chart update for ${symbol}:`, klinesData.klines?.length || 0, 'candles');
                if (klinesData.klines && klinesData.klines.length > 0) {
                    candlestickSeries.setData(klinesData.klines);
                    // Update the last candle time display
                    const lastCandle = klinesData.klines[klinesData.klines.length - 1];
                    if (chartContainer) {
                        const updateIndicator = chartContainer.parentElement.querySelector('.update-time');
                        if (updateIndicator && lastCandle) {
                            const now = new Date();
                            updateIndicator.textContent = `Updated: ${now.toLocaleTimeString()} | Last: ${lastCandle.close.toFixed(2)}`;
                        }
                    }
                }
            } catch (err) {
                console.error('Failed to update klines:', err);
            }

            // Load indicators
            try {
                const indResponse = await fetch(`/api/indicators/${symbol}?interval=${interval}&limit=500`);
                const indData = await indResponse.json();

                if (indData.indicators) {
                    if (emaSeries && indData.indicators.ema200) {
                        emaSeries.setData(indData.indicators.ema200);
                    }
                    // SuperTrend with green/red colors based on direction
                    if (st1Series && indData.indicators.supertrend1) {
                        st1Series.setData(indData.indicators.supertrend1.map(p => ({time: p.time, value: p.value, color: p.color})));
                    }
                    if (st2Series && indData.indicators.supertrend2) {
                        st2Series.setData(indData.indicators.supertrend2.map(p => ({time: p.time, value: p.value, color: p.color})));
                    }
                    if (st3Series && indData.indicators.supertrend3) {
                        st3Series.setData(indData.indicators.supertrend3.map(p => ({time: p.time, value: p.value, color: p.color})));
                    }
                }
            } catch (err) {
                console.error('Failed to update indicators:', err);
            }
        }

        // Clean up chart when bot is stopped
        function cleanupBotChart(botId) {
            if (chartUpdateIntervals[botId]) {
                clearInterval(chartUpdateIntervals[botId]);
                delete chartUpdateIntervals[botId];
            }
            if (botCharts[botId]) {
                botCharts[botId].chart.remove();
                delete botCharts[botId];
            }
        }

        // Toggle bot mode (manual/auto_search)
        function toggleBotMode(prefix) {
            const mode = document.getElementById(prefix + 'BotMode').value;
            const pairContainer = document.getElementById(prefix + 'PairContainer');
            const maxPairsContainer = document.getElementById(prefix + 'MaxPairsContainer');
            const pairInfoContainer = document.getElementById(prefix + 'PairInfoContainer');
            const assetFiltersContainer = document.getElementById(prefix + 'AssetFiltersContainer');

            if (mode === 'auto_search') {
                if (pairContainer) pairContainer.style.display = 'none';
                if (pairInfoContainer) pairInfoContainer.style.display = 'none';
                if (maxPairsContainer) maxPairsContainer.style.display = 'block';
                if (assetFiltersContainer) assetFiltersContainer.style.display = 'block';
            } else {
                if (pairContainer) pairContainer.style.display = 'block';
                if (maxPairsContainer) maxPairsContainer.style.display = 'none';
                if (assetFiltersContainer) assetFiltersContainer.style.display = 'none';
            }
        }

        // Toggle SL options based on selected mode
        function toggleSlOptions(prefix) {
            const slMode = document.getElementById(prefix + 'BotSlMode').value;
            const lineContainer = document.getElementById(prefix + 'SlLineContainer');
            const percentContainer = document.getElementById(prefix + 'SlPercentContainer');
            const atrContainer = document.getElementById(prefix + 'SlAtrContainer');

            lineContainer.style.display = slMode === 'supertrend_line' ? 'flex' : 'none';
            percentContainer.style.display = slMode === 'fixed_percent' ? 'flex' : 'none';
            atrContainer.style.display = slMode === 'atr' ? 'flex' : 'none';
        }

        async function createBot() {
            // Prevent double-click - disable button immediately
            const createBtn = document.getElementById('createBotBtn');
            if (createBtn.disabled) return; // Already processing
            createBtn.disabled = true;
            createBtn.textContent = '...';

            const botMode = document.getElementById('newBotMode').value;
            const selectedPair = document.getElementById('newBotPair').value;
            const slMode = document.getElementById('newBotSlMode').value;
            const tpMode = document.getElementById('newBotTpMode').value;
            const config = {
                name: document.getElementById('newBotName').value || 'Bot ' + (botsData.length + 1),
                bot_mode: botMode,
                trading_pairs: botMode === 'auto_search' ? [] : [selectedPair],
                max_trading_pairs: parseInt(document.getElementById('newBotMaxPairs').value) || 1,
                timeframe: document.getElementById('newBotTimeframe').value,
                leverage: parseInt(document.getElementById('newBotLeverage').value),
                order_size: parseFloat(document.getElementById('newBotOrderSize').value),
                position_sizing_mode: document.getElementById('newBotPositionSizingMode').value,
                risk_per_trade: parseFloat(document.getElementById('newBotRiskPerTrade').value),
                leverage_mode: document.getElementById('newBotLeverageMode').value,
                tp_mode: tpMode,
                tp_risk_ratio: parseFloat(document.getElementById('newBotTpRatio').value),
                tp_fixed_percent: parseFloat(document.getElementById('newBotTpPercent').value) || 2,
                sl_mode: slMode,
                sl_supertrend_line: parseInt(document.getElementById('newBotSlLine').value),
                sl_fixed_percent: parseFloat(document.getElementById('newBotSlPercent').value),
                sl_atr_multiplier: parseFloat(document.getElementById('newBotSlAtrMult').value),
                ema_enabled: document.getElementById('newBotEmaEnabled').checked,
                ema_filter_mode: document.getElementById('newBotEmaMode').value,
                trailing_enabled: document.getElementById('newBotTrailingEnabled').checked,
                trailing_mode: document.getElementById('newBotTrailingMode').value,
                trailing_activation: parseFloat(document.getElementById('newBotTrailingActivation').value),
                trailing_step: parseFloat(document.getElementById('newBotTrailingStep').value),
                trailing_st_line: parseInt(document.getElementById('newBotTrailingStLine').value),
                trailing_confirm_candles: parseInt(document.getElementById('newBotTrailingConfirm').value),
                partial_tp_enabled: document.getElementById('newBotPartialTpEnabled').checked,
                partial_tp_close_percent: parseInt(document.getElementById('newBotPartialTpClose').value),
                partial_tp_sl_move: document.getElementById('newBotPartialTpSlMove').value,
                partial_tp_sl_offset: parseFloat(document.getElementById('newBotPartialTpOffset').value),
                trailing_tp_enabled: document.getElementById('newBotTrailingTpEnabled').checked,
                trailing_tp_mode: document.getElementById('newBotTrailingTpMode').value,
                trailing_tp_st_line: parseInt(document.getElementById('newBotTrailingTpStLine').value),
                trailing_tp_activation: parseFloat(document.getElementById('newBotTrailingTpActivation').value),
                trailing_tp_step: parseFloat(document.getElementById('newBotTrailingTpStep').value),
                // Signal Entry settings
                st1_role: document.getElementById('newBotSt1Role').value,
                st2_role: document.getElementById('newBotSt2Role').value,
                st3_role: document.getElementById('newBotSt3Role').value,
                trigger_confirm_candles: parseInt(document.getElementById('newBotTriggerConfirmCandles').value),
                // Strategy type selection
                strategy_type: document.getElementById('newBotStrategyHeikinAshiEnabled').checked ? 'heikinashi' : 'supertrend',
                // Heikin Ashi settings
                ha_entry_candles: parseInt(document.getElementById('newBotHaEntryCandles').value),
                ha_exit_on_color_change: document.getElementById('newBotHaExitOnColorChange').checked,
                ha_exit_candles: parseInt(document.getElementById('newBotHaExitCandles').value),
                ha_ema_enabled: document.getElementById('newBotHaEmaEnabled').checked,
                ha_ema_period: parseInt(document.getElementById('newBotHaEmaPeriod').value) || 200,
                ha_ema_mode: document.getElementById('newBotHaEmaMode').value,
                ha_volatility_enabled: document.getElementById('newBotHaVolatilityEnabled').checked,
                ha_min_atr: parseFloat(document.getElementById('newBotHaMinAtr').value) || 0.5,
                ha_emergency_sl: parseFloat(document.getElementById('newBotHaEmergencySl').value) || 2,
                ha_trailing_enabled: document.getElementById('newBotHaTrailingEnabled').checked,
                ha_trailing_activation: parseFloat(document.getElementById('newBotHaTrailingActivation').value) || 1,
                ha_trailing_distance: parseFloat(document.getElementById('newBotHaTrailingDistance').value) || 0.5,
                // Asset filters
                asset_filters_enabled: document.getElementById('newBotAssetFiltersEnabled').checked,
                filter_min_volume: parseMoneyValue(document.getElementById('newBotMinVolume').value),
                filter_max_volume: parseMoneyValue(document.getElementById('newBotMaxVolume').value),
                filter_min_price: parseFloat(document.getElementById('newBotMinPrice').value) || 0,
                filter_max_price: parseFloat(document.getElementById('newBotMaxPrice').value) || 0,
                filter_min_change: parseFloat(document.getElementById('newBotMinChange').value) || 0,
                filter_max_change: parseFloat(document.getElementById('newBotMaxChange').value) || 0,
                filter_volatility_period: parseInt(document.getElementById('newBotVolatilityPeriod').value) || 0,
                filter_min_volatility: parseFloat(document.getElementById('newBotMinVolatility').value) || 0,
                filter_max_volatility: parseFloat(document.getElementById('newBotMaxVolatility').value) || 0,
                balance_usage_percent: parseFloat(document.getElementById('newBotBalanceUsage').value) || 100,
                // Daily loss limit settings
                max_loss_enabled: document.getElementById('newBotMaxLossEnabled').checked,
                max_loss_percent: parseFloat(document.getElementById('newBotMaxLoss').value) || 50,
                // Consecutive losses settings
                consecutive_losses_enabled: document.getElementById('newBotConsecutiveLossesEnabled').checked,
                max_consecutive_losses: parseInt(document.getElementById('newBotMaxConsecutiveLosses').value) || 3,
                cooldown_after_loss_streak: parseInt(document.getElementById('newBotCooldownMinutes').value) || 30,
            };

            try {
                const response = await fetch('/api/bots', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(config)
                });
                const data = await response.json();

                if (data.success) {
                    showToast(t('botCreated'));
                    closeModal('createBotModal');
                    loadBots();
                } else {
                    showToast(data.message);
                }
            } catch (err) {
                console.error('Failed to create bot:', err);
                showToast('Error creating bot');
            } finally {
                // Re-enable button
                createBtn.disabled = false;
                createBtn.textContent = t('create');
            }
        }

        async function startSpecificBot(botId) {
            const bot = botsData.find(b => b.id === botId);
            showToast(t('starting'));

            try {
                const response = await fetch(`/api/bots/${botId}/start`, { method: 'POST' });
                const data = await response.json();
                if (data.success) {
                    // Update status immediately on success
                    if (bot) {
                        bot.status = 'running';
                        renderBots();
                    }
                    showToast(t('botStarted'));
                } else {
                    showToast(data.message);
                    await loadBots();
                }
            } catch (err) {
                console.error('Failed to start bot:', err);
                await loadBots();
            }
        }

        async function stopSpecificBot(botId) {
            const bot = botsData.find(b => b.id === botId);

            // Update UI immediately
            if (bot) {
                bot.status = 'stopping';
                renderBots();
            }
            showToast(t('stopping'));

            try {
                const response = await fetch(`/api/bots/${botId}/stop`, { method: 'POST' });
                const data = await response.json();
                if (data.success) {
                    showToast(t('botStopped'));
                } else {
                    showToast(data.message);
                }
            } catch (err) {
                console.error('Failed to stop bot:', err);
                showToast(t('failedStop'));
            }

            // Always reload bots after stop attempt
            await loadBots();
        }

        async function pauseBot(botId) {
            const bot = botsData.find(b => b.id === botId);
            showToast(t('pause') + '...');

            try {
                const response = await fetch(`/api/bots/${botId}/pause`, { method: 'POST' });
                const data = await response.json();
                if (data.success) {
                    showToast(currentLang === 'ru' ? 'Бот на паузе' : 'Bot paused');
                } else {
                    showToast(data.message);
                }
            } catch (err) {
                console.error('Failed to pause bot:', err);
            }
            await loadBots();
        }

        async function resumeBot(botId) {
            showToast(currentLang === 'ru' ? 'Возобновление...' : 'Resuming...');

            try {
                const response = await fetch(`/api/bots/${botId}/resume`, { method: 'POST' });
                const data = await response.json();
                if (data.success) {
                    showToast(currentLang === 'ru' ? 'Бот возобновлен' : 'Bot resumed');
                } else {
                    showToast(data.message);
                }
            } catch (err) {
                console.error('Failed to resume bot:', err);
            }
            await loadBots();
        }

        async function deleteBot(botId) {
            if (!confirm('Delete this bot?')) return;

            try {
                const response = await fetch(`/api/bots/${botId}`, { method: 'DELETE' });
                const data = await response.json();
                if (data.success) {
                    showToast(t('botDeleted'));
                    loadBots();
                } else {
                    showToast(data.message);
                }
            } catch (err) {
                console.error('Failed to delete bot:', err);
            }
        }

        // Edit Bot Functions
        async function showEditBotModal(botId) {
            const bot = botsData.find(b => b.id === botId);
            if (!bot) return;

            // Reload pairs if empty
            if (allTradingPairs.length === 0) {
                await loadTradingPairsCache();
            }

            // Fill form with bot data
            const currentPair = Array.isArray(bot.trading_pairs) ? bot.trading_pairs[0] : bot.trading_pairs;
            document.getElementById('editBotId').value = bot.id;
            document.getElementById('editBotName').value = bot.name;
            document.getElementById('editBotMode').value = bot.bot_mode || 'manual';
            document.getElementById('editBotMaxPairs').value = bot.max_trading_pairs || bot.max_simultaneous_orders || 1;
            document.getElementById('editBotPairSearch').value = currentPair || '';
            document.getElementById('editBotPair').value = currentPair || '';
            document.getElementById('editBotTimeframe').value = bot.timeframe;

            // Toggle bot mode visibility
            toggleBotMode('edit');

            // Update pair info first (sets leverage options) - only for manual mode
            if (bot.bot_mode !== 'auto_search' && currentPair) {
                updatePairInfo('edit', currentPair);
            }

            // Then set values
            document.getElementById('editBotLeverage').value = bot.leverage;
            document.getElementById('editBotOrderSize').value = bot.order_size || 10;
            document.getElementById('editBotPositionSizingMode').value = bot.position_sizing_mode || 'fixed_amount';
            document.getElementById('editBotRiskPerTrade').value = bot.risk_per_trade || 2;
            toggleRiskPercentInput('edit');
            document.getElementById('editBotLeverageMode').value = bot.leverage_mode || 'cross';

            // TP Mode and value
            const tpMode = bot.tp_mode || 'rr';
            document.getElementById('editBotTpMode').value = tpMode;
            document.getElementById('editBotTpRatio').value = bot.tp_risk_ratio || 2;
            document.getElementById('editBotTpPercent').value = bot.tp_fixed_percent || 2;
            toggleTpOptions('edit');

            document.getElementById('editBotSlMode').value = bot.sl_mode;
            document.getElementById('editBotSlLine').value = bot.sl_supertrend_line || 2;
            document.getElementById('editBotSlPercent').value = bot.sl_fixed_percent || 2;
            document.getElementById('editBotSlAtrMult').value = bot.sl_atr_multiplier || 1.5;
            toggleSlOptions('edit');
            document.getElementById('editBotEmaEnabled').checked = bot.ema_enabled || false;
            document.getElementById('editBotEmaMode').value = bot.ema_filter_mode || 'strict';
            document.getElementById('editBotTrailingEnabled').checked = bot.trailing_enabled !== false;
            document.getElementById('editBotTrailingMode').value = bot.trailing_mode || 'fix_percent';
            document.getElementById('editBotTrailingActivation').value = bot.trailing_activation || 1.0;
            document.getElementById('editBotTrailingStep').value = bot.trailing_step || 0.5;
            document.getElementById('editBotTrailingStLine').value = bot.trailing_st_line || 2;
            document.getElementById('editBotTrailingConfirm').value = bot.trailing_confirm_candles || 1;
            toggleTrailingOptions('edit');
            document.getElementById('editBotPartialTpEnabled').checked = bot.partial_tp_enabled !== false;
            document.getElementById('editBotPartialTpClose').value = bot.partial_tp_close_percent || 50;
            document.getElementById('editBotPartialTpSlMove').value = bot.partial_tp_sl_move || 'tp1';
            document.getElementById('editBotPartialTpOffset').value = bot.partial_tp_sl_offset || 0.2;
            document.getElementById('editBotTrailingTpEnabled').checked = bot.trailing_tp_enabled || false;
            document.getElementById('editBotTrailingTpMode').value = bot.trailing_tp_mode || 'st_line';
            document.getElementById('editBotTrailingTpStLine').value = bot.trailing_tp_st_line || 2;
            document.getElementById('editBotTrailingTpActivation').value = bot.trailing_tp_activation || 0.5;
            document.getElementById('editBotTrailingTpStep').value = bot.trailing_tp_step || 1.0;
            toggleTrailingTpOptions('edit');

            // Signal Entry settings
            document.getElementById('editBotSt1Role').value = bot.st1_role || 'confirm';
            document.getElementById('editBotSt2Role').value = bot.st2_role || 'confirm';
            document.getElementById('editBotSt3Role').value = bot.st3_role || 'trigger';
            document.getElementById('editBotTriggerConfirmCandles').value = bot.trigger_confirm_candles || 1;
            updateSignalPreview('edit');

            // Strategy type and Heikin Ashi settings
            const strategyType = bot.strategy_type || 'supertrend';
            document.getElementById('editBotHaEntryCandles').value = bot.ha_entry_candles || 2;
            document.getElementById('editBotHaExitOnColorChange').checked = bot.ha_exit_on_color_change !== false;
            document.getElementById('editBotHaExitCandles').value = bot.ha_exit_candles || 1;
            document.getElementById('editBotHaEmaEnabled').checked = bot.ha_ema_enabled !== false;
            document.getElementById('editBotHaEmaPeriod').value = bot.ha_ema_period || 200;
            document.getElementById('editBotHaEmaMode').value = bot.ha_ema_mode || 'strict';
            document.getElementById('editBotHaVolatilityEnabled').checked = bot.ha_volatility_enabled !== false;
            document.getElementById('editBotHaMinAtr').value = bot.ha_min_atr || 0.5;
            document.getElementById('editBotHaEmergencySl').value = bot.ha_emergency_sl || 2;
            document.getElementById('editBotHaTrailingEnabled').checked = bot.ha_trailing_enabled !== false;
            document.getElementById('editBotHaTrailingActivation').value = bot.ha_trailing_activation || 1;
            document.getElementById('editBotHaTrailingDistance').value = bot.ha_trailing_distance || 0.5;
            // Set the strategy toggle without applying defaults for edit mode
            if (strategyType === 'heikinashi') {
                // Manually set up the HA view without triggering applyHaRecommendedDefaults
                const stEnabled = document.getElementById('editBotStrategySupertrendEnabled');
                const haEnabled = document.getElementById('editBotStrategyHeikinAshiEnabled');
                const stSettings = document.getElementById('editSupertrendSettings');
                const haSettings = document.getElementById('editHeikinAshiSettings');
                const stContainer = document.getElementById('editStrategySupertrend');
                const haContainer = document.getElementById('editStrategyHeikinAshi');
                stEnabled.checked = false;
                haEnabled.checked = true;
                stSettings.style.display = 'none';
                haSettings.style.display = 'block';
                stContainer.style.background = 'rgba(255,255,255,0.05)';
                stContainer.querySelector('label').style.color = '#888';
                haContainer.style.background = 'rgba(255,170,0,0.1)';
                haContainer.querySelector('label').style.color = '#ffaa00';
                toggleHaExitSettings('edit');
                toggleHaEmaSettings('edit');
                toggleHaVolatilitySettings('edit');
                toggleHaTrailingSettings('edit');
            } else {
                toggleStrategy('edit', strategyType);
            }

            // Asset filters - format with spaces, show "-" for 0
            document.getElementById('editBotAssetFiltersEnabled').checked = bot.asset_filters_enabled !== false;
            document.getElementById('editBotMinVolume').value = (bot.filter_min_volume || 3000000) > 0 ? (bot.filter_min_volume || 3000000).toLocaleString('ru-RU').replace(/\s/g, ' ') : '-';
            document.getElementById('editBotMaxVolume').value = (bot.filter_max_volume || 0) > 0 ? (bot.filter_max_volume).toLocaleString('ru-RU').replace(/\s/g, ' ') : '-';
            document.getElementById('editBotMinPrice').value = (bot.filter_min_price || 0) > 0 ? (bot.filter_min_price).toLocaleString('ru-RU').replace(/\s/g, ' ') : '-';
            document.getElementById('editBotMaxPrice').value = (bot.filter_max_price || 0) > 0 ? (bot.filter_max_price).toLocaleString('ru-RU').replace(/\s/g, ' ') : '-';
            document.getElementById('editBotMinChange').value = bot.filter_min_change !== undefined ? bot.filter_min_change : -30;
            document.getElementById('editBotMaxChange').value = bot.filter_max_change !== undefined ? bot.filter_max_change : 20;
            document.getElementById('editBotVolatilityPeriod').value = bot.filter_volatility_period || 12;
            document.getElementById('editBotMinVolatility').value = bot.filter_min_volatility !== undefined ? bot.filter_min_volatility : 0.5;
            document.getElementById('editBotMaxVolatility').value = bot.filter_max_volatility !== undefined ? bot.filter_max_volatility : 3;
            toggleAssetFiltersInputs('edit');
            document.getElementById('editBotBalanceUsage').value = bot.balance_usage_percent || 100;
            // Daily loss limit settings
            document.getElementById('editBotMaxLossEnabled').checked = bot.max_loss_enabled || false;
            document.getElementById('editBotMaxLoss').value = bot.max_loss_percent || 50;
            // Consecutive losses settings
            document.getElementById('editBotConsecutiveLossesEnabled').checked = bot.consecutive_losses_enabled || false;
            document.getElementById('editBotMaxConsecutiveLosses').value = bot.max_consecutive_losses || 3;
            document.getElementById('editBotCooldownMinutes').value = bot.cooldown_after_loss_streak || 30;

            // Update slider displays
            document.getElementById('editBalanceValue').textContent = (bot.balance_usage_percent || 100) + '%';
            document.getElementById('editMaxLossValue').textContent = (bot.max_loss_percent || 50) + '%';

            // Toggle visibility of conditional fields
            toggleEmaMode('edit');
            toggleTrailingOptions('edit');
            togglePartialTpOptions('edit');
            toggleTrailingTpOptions('edit');
            toggleMaxLossSlider('edit');
            toggleConsecutiveLossesSelect('edit');

            // Toggle SL options visibility
            toggleSlOptions('edit');

            // Update deposit info
            updateDepositInfo('edit');
            updateBalanceUsageInfo('edit');
            updateMaxLossInfo('edit');

            document.getElementById('editBotModal').classList.add('show');
        }

        async function saveEditBot() {
            // Prevent double-click - disable button immediately
            const saveBtn = document.getElementById('saveEditBotBtn');
            if (saveBtn.disabled) return; // Already processing
            saveBtn.disabled = true;
            saveBtn.textContent = '...';

            const botId = document.getElementById('editBotId').value;
            const botMode = document.getElementById('editBotMode').value;
            const slMode = document.getElementById('editBotSlMode').value;
            const tpMode = document.getElementById('editBotTpMode').value;
            const config = {
                name: document.getElementById('editBotName').value,
                bot_mode: botMode,
                trading_pairs: botMode === 'auto_search' ? [] : [document.getElementById('editBotPair').value],
                max_trading_pairs: parseInt(document.getElementById('editBotMaxPairs').value) || 1,
                timeframe: document.getElementById('editBotTimeframe').value,
                leverage: parseInt(document.getElementById('editBotLeverage').value),
                order_size: parseFloat(document.getElementById('editBotOrderSize').value),
                position_sizing_mode: document.getElementById('editBotPositionSizingMode').value,
                risk_per_trade: parseFloat(document.getElementById('editBotRiskPerTrade').value),
                leverage_mode: document.getElementById('editBotLeverageMode').value,
                tp_mode: tpMode,
                tp_risk_ratio: parseFloat(document.getElementById('editBotTpRatio').value),
                tp_fixed_percent: parseFloat(document.getElementById('editBotTpPercent').value) || 2,
                sl_mode: slMode,
                sl_supertrend_line: parseInt(document.getElementById('editBotSlLine').value),
                sl_fixed_percent: parseFloat(document.getElementById('editBotSlPercent').value),
                sl_atr_multiplier: parseFloat(document.getElementById('editBotSlAtrMult').value),
                ema_enabled: document.getElementById('editBotEmaEnabled').checked,
                ema_filter_mode: document.getElementById('editBotEmaMode').value,
                trailing_enabled: document.getElementById('editBotTrailingEnabled').checked,
                trailing_mode: document.getElementById('editBotTrailingMode').value,
                trailing_activation: parseFloat(document.getElementById('editBotTrailingActivation').value),
                trailing_step: parseFloat(document.getElementById('editBotTrailingStep').value),
                trailing_st_line: parseInt(document.getElementById('editBotTrailingStLine').value),
                trailing_confirm_candles: parseInt(document.getElementById('editBotTrailingConfirm').value),
                partial_tp_enabled: document.getElementById('editBotPartialTpEnabled').checked,
                partial_tp_close_percent: parseInt(document.getElementById('editBotPartialTpClose').value),
                partial_tp_sl_move: document.getElementById('editBotPartialTpSlMove').value,
                partial_tp_sl_offset: parseFloat(document.getElementById('editBotPartialTpOffset').value),
                trailing_tp_enabled: document.getElementById('editBotTrailingTpEnabled').checked,
                trailing_tp_mode: document.getElementById('editBotTrailingTpMode').value,
                trailing_tp_st_line: parseInt(document.getElementById('editBotTrailingTpStLine').value),
                trailing_tp_activation: parseFloat(document.getElementById('editBotTrailingTpActivation').value),
                trailing_tp_step: parseFloat(document.getElementById('editBotTrailingTpStep').value),
                // Signal Entry settings
                st1_role: document.getElementById('editBotSt1Role').value,
                st2_role: document.getElementById('editBotSt2Role').value,
                st3_role: document.getElementById('editBotSt3Role').value,
                trigger_confirm_candles: parseInt(document.getElementById('editBotTriggerConfirmCandles').value),
                // Strategy type selection
                strategy_type: document.getElementById('editBotStrategyHeikinAshiEnabled').checked ? 'heikinashi' : 'supertrend',
                // Heikin Ashi settings
                ha_entry_candles: parseInt(document.getElementById('editBotHaEntryCandles').value),
                ha_exit_on_color_change: document.getElementById('editBotHaExitOnColorChange').checked,
                ha_exit_candles: parseInt(document.getElementById('editBotHaExitCandles').value),
                ha_ema_enabled: document.getElementById('editBotHaEmaEnabled').checked,
                ha_ema_period: parseInt(document.getElementById('editBotHaEmaPeriod').value) || 200,
                ha_ema_mode: document.getElementById('editBotHaEmaMode').value,
                ha_volatility_enabled: document.getElementById('editBotHaVolatilityEnabled').checked,
                ha_min_atr: parseFloat(document.getElementById('editBotHaMinAtr').value) || 0.5,
                ha_emergency_sl: parseFloat(document.getElementById('editBotHaEmergencySl').value) || 2,
                ha_trailing_enabled: document.getElementById('editBotHaTrailingEnabled').checked,
                ha_trailing_activation: parseFloat(document.getElementById('editBotHaTrailingActivation').value) || 1,
                ha_trailing_distance: parseFloat(document.getElementById('editBotHaTrailingDistance').value) || 0.5,
                // Asset filters
                asset_filters_enabled: document.getElementById('editBotAssetFiltersEnabled').checked,
                filter_min_volume: parseMoneyValue(document.getElementById('editBotMinVolume').value),
                filter_max_volume: parseMoneyValue(document.getElementById('editBotMaxVolume').value),
                filter_min_price: parseFloat(document.getElementById('editBotMinPrice').value) || 0,
                filter_max_price: parseFloat(document.getElementById('editBotMaxPrice').value) || 0,
                filter_min_change: parseFloat(document.getElementById('editBotMinChange').value) || 0,
                filter_max_change: parseFloat(document.getElementById('editBotMaxChange').value) || 0,
                filter_volatility_period: parseInt(document.getElementById('editBotVolatilityPeriod').value) || 0,
                filter_min_volatility: parseFloat(document.getElementById('editBotMinVolatility').value) || 0,
                filter_max_volatility: parseFloat(document.getElementById('editBotMaxVolatility').value) || 0,
                balance_usage_percent: parseFloat(document.getElementById('editBotBalanceUsage').value) || 100,
                // Daily loss limit settings
                max_loss_enabled: document.getElementById('editBotMaxLossEnabled').checked,
                max_loss_percent: parseFloat(document.getElementById('editBotMaxLoss').value) || 50,
                // Consecutive losses settings
                consecutive_losses_enabled: document.getElementById('editBotConsecutiveLossesEnabled').checked,
                max_consecutive_losses: parseInt(document.getElementById('editBotMaxConsecutiveLosses').value) || 3,
                cooldown_after_loss_streak: parseInt(document.getElementById('editBotCooldownMinutes').value) || 30,
            };

            try {
                const response = await fetch(`/api/bots/${botId}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(config)
                });
                const data = await response.json();

                if (data.success) {
                    showToast(t('botUpdated'));
                    closeModal('editBotModal');
                    loadBots();
                } else {
                    showToast(data.message);
                }
            } catch (err) {
                console.error('Failed to update bot:', err);
                showToast('Error updating bot');
            } finally {
                // Re-enable button
                saveBtn.disabled = false;
                saveBtn.textContent = t('save');
            }
        }

        // Fullscreen Chart Functions
        let fullscreenChart = null;
        let fullscreenChartData = null;
        let fullscreenUpdateInterval = null;
        let fullscreenCountdownInterval = null;

        // Get timeframe duration in seconds
        function getTimeframeDuration(timeframe) {
            const durations = {
                '1m': 60,
                '3m': 180,
                '5m': 300,
                '15m': 900,
                '30m': 1800,
                '1h': 3600,
                '2h': 7200,
                '4h': 14400,
                '1d': 86400,
            };
            return durations[timeframe] || 900;
        }

        // Format countdown time
        function formatCountdown(seconds) {
            if (seconds < 0) seconds = 0;
            const h = Math.floor(seconds / 3600);
            const m = Math.floor((seconds % 3600) / 60);
            const s = seconds % 60;
            if (h > 0) {
                return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
            }
            return `${m}:${s.toString().padStart(2, '0')}`;
        }

        // Update countdown timer
        function updateCountdown() {
            if (!fullscreenChartData) return;

            const tfDuration = getTimeframeDuration(fullscreenChartData.bot.timeframe);
            const now = Math.floor(Date.now() / 1000);
            const candleStart = Math.floor(now / tfDuration) * tfDuration;
            const candleEnd = candleStart + tfDuration;
            const remaining = candleEnd - now;

            const countdownEl = document.getElementById('fsCountdown');
            if (countdownEl) {
                countdownEl.textContent = formatCountdown(remaining);
            }
        }

        async function openFullscreenChart(botId) {
            const bot = botsData.find(b => b.id === botId);
            if (!bot) return;

            const symbol = Array.isArray(bot.trading_pairs) ? bot.trading_pairs[0] : bot.trading_pairs;
            document.getElementById('fullscreenChartTitle').innerHTML = `${bot.name} - ${getCoinIconHtml(symbol, 16)}${symbol} (${bot.timeframe})`;

            document.getElementById('fullscreenChartModal').classList.add('show');

            // Wait for modal to be visible
            await new Promise(resolve => setTimeout(resolve, 100));

            const container = document.getElementById('fullscreenChartContainer');
            container.innerHTML = '';

            if (typeof LightweightCharts === 'undefined') {
                container.innerHTML = '<p style="color: #888; text-align: center; padding: 40px;">Chart library not loaded</p>';
                return;
            }

            fullscreenChart = LightweightCharts.createChart(container, {
                width: container.clientWidth,
                height: container.clientHeight,
                layout: {
                    background: { type: 'solid', color: '#1a1a2e' },
                    textColor: '#888',
                },
                grid: {
                    vertLines: { color: 'rgba(255, 255, 255, 0.05)' },
                    horzLines: { color: 'rgba(255, 255, 255, 0.05)' },
                },
                rightPriceScale: { borderColor: 'rgba(255, 255, 255, 0.1)' },
                timeScale: { borderColor: 'rgba(255, 255, 255, 0.1)', timeVisible: true, secondsVisible: false },
            });

            const candlestickSeries = fullscreenChart.addCandlestickSeries({
                upColor: '#00ff88',
                downColor: '#ff4444',
                borderDownColor: '#ff4444',
                borderUpColor: '#00ff88',
                wickDownColor: '#ff4444',
                wickUpColor: '#00ff88',
            });

            const tfMap = {'1m':'1','3m':'3','5m':'5','15m':'15','30m':'30','1h':'60','2h':'120','4h':'240','1d':'D'};
            const interval = tfMap[bot.timeframe] || '15';

            // Store chart data for updates
            fullscreenChartData = {
                bot: bot,
                symbol: symbol,
                interval: interval,
                candlestickSeries: candlestickSeries,
                emaSeries: null,
                st1Series: null,
                st2Series: null,
                st3Series: null
            };

            // Load klines
            try {
                const klinesResponse = await fetch(`/api/klines/${symbol}?interval=${interval}&limit=500`);
                const klinesData = await klinesResponse.json();
                if (klinesData.klines && klinesData.klines.length > 0) {
                    candlestickSeries.setData(klinesData.klines);
                }
            } catch (err) {
                console.error('Failed to load klines:', err);
            }

            // Load indicators
            try {
                const indResponse = await fetch(`/api/indicators/${symbol}?interval=${interval}&limit=500`);
                const indData = await indResponse.json();

                if (indData.indicators) {
                    // EMA 200 (if enabled)
                    if (bot.ema_enabled && indData.indicators.ema200 && indData.indicators.ema200.length > 0) {
                        fullscreenChartData.emaSeries = fullscreenChart.addLineSeries({
                            color: '#ffcc00',
                            lineWidth: 2,
                            title: 'EMA200',
                        });
                        fullscreenChartData.emaSeries.setData(indData.indicators.ema200);
                    }

                    // SuperTrend 1 (Fast: period=10, mult=1.0) - thinnest line
                    if (indData.indicators.supertrend1 && indData.indicators.supertrend1.length > 0) {
                        fullscreenChartData.st1Series = fullscreenChart.addLineSeries({
                            lineWidth: 1,
                            title: 'ST1 Fast',
                            lastValueVisible: false,
                            priceLineVisible: false,
                        });
                        fullscreenChartData.st1Series.setData(indData.indicators.supertrend1.map(p => ({time: p.time, value: p.value, color: p.color})));
                    }

                    // SuperTrend 2 (Medium: period=11, mult=2.0) - medium line
                    if (indData.indicators.supertrend2 && indData.indicators.supertrend2.length > 0) {
                        fullscreenChartData.st2Series = fullscreenChart.addLineSeries({
                            lineWidth: 2,
                            title: 'ST2 Medium',
                            lastValueVisible: false,
                            priceLineVisible: false,
                        });
                        fullscreenChartData.st2Series.setData(indData.indicators.supertrend2.map(p => ({time: p.time, value: p.value, color: p.color})));
                    }

                    // SuperTrend 3 (Slow: period=12, mult=3.0) - thickest line
                    if (indData.indicators.supertrend3 && indData.indicators.supertrend3.length > 0) {
                        fullscreenChartData.st3Series = fullscreenChart.addLineSeries({
                            lineWidth: 3,
                            title: 'ST3 Slow',
                            lastValueVisible: false,
                            priceLineVisible: false,
                        });
                        fullscreenChartData.st3Series.setData(indData.indicators.supertrend3.map(p => ({time: p.time, value: p.value, color: p.color})));
                    }
                }
            } catch (err) {
                console.error('Failed to load indicators:', err);
            }

            fullscreenChart.timeScale().fitContent();

            // Handle resize
            const resizeObserver = new ResizeObserver(() => {
                if (fullscreenChart) {
                    fullscreenChart.applyOptions({
                        width: container.clientWidth,
                        height: container.clientHeight
                    });
                }
            });
            resizeObserver.observe(container);

            // Start real-time updates
            const updateMs = getUpdateInterval(bot.timeframe);
            fullscreenUpdateInterval = setInterval(() => updateFullscreenChart(), updateMs);

            // Start countdown timer (updates every second)
            updateCountdown();
            fullscreenCountdownInterval = setInterval(updateCountdown, 1000);

            // Initial chart update
            await updateFullscreenChart();
        }

        async function updateFullscreenChart() {
            if (!fullscreenChartData || !fullscreenChart) return;

            const { symbol, interval, candlestickSeries, emaSeries, st1Series, st2Series, st3Series, bot } = fullscreenChartData;

            try {
                // Update klines
                const klinesResponse = await fetch(`/api/klines/${symbol}?interval=${interval}&limit=500`);
                const klinesData = await klinesResponse.json();
                console.log(`Fullscreen chart update for ${symbol}:`, klinesData.klines?.length || 0, 'candles');

                if (klinesData.klines && klinesData.klines.length > 0) {
                    candlestickSeries.setData(klinesData.klines);

                    // Update price display
                    const lastCandle = klinesData.klines[klinesData.klines.length - 1];
                    const priceEl = document.getElementById('fsPrice');
                    if (priceEl && lastCandle) {
                        const priceChange = lastCandle.close - lastCandle.open;
                        const color = priceChange >= 0 ? '#00ff88' : '#ff4444';
                        const arrow = priceChange >= 0 ? '▲' : '▼';
                        priceEl.style.color = color;
                        priceEl.textContent = `${arrow} ${lastCandle.close.toFixed(2)}`;
                    }

                    // Update time display
                    const updateTimeEl = document.getElementById('fsUpdateTime');
                    if (updateTimeEl) {
                        const now = new Date();
                        updateTimeEl.textContent = `Updated: ${now.toLocaleTimeString()}`;
                    }
                }

                // Update indicators
                const indResponse = await fetch(`/api/indicators/${symbol}?interval=${interval}&limit=500`);
                const indData = await indResponse.json();

                if (indData.indicators) {
                    if (emaSeries && indData.indicators.ema200) {
                        emaSeries.setData(indData.indicators.ema200);
                    }
                    if (st1Series && indData.indicators.supertrend1) {
                        st1Series.setData(indData.indicators.supertrend1.map(p => ({time: p.time, value: p.value, color: p.color})));
                    }
                    if (st2Series && indData.indicators.supertrend2) {
                        st2Series.setData(indData.indicators.supertrend2.map(p => ({time: p.time, value: p.value, color: p.color})));
                    }
                    if (st3Series && indData.indicators.supertrend3) {
                        st3Series.setData(indData.indicators.supertrend3.map(p => ({time: p.time, value: p.value, color: p.color})));
                    }
                }
            } catch (err) {
                console.error('Failed to update fullscreen chart:', err);
            }
        }

        function closeFullscreenChart() {
            // Stop real-time updates
            if (fullscreenUpdateInterval) {
                clearInterval(fullscreenUpdateInterval);
                fullscreenUpdateInterval = null;
            }
            // Stop countdown timer
            if (fullscreenCountdownInterval) {
                clearInterval(fullscreenCountdownInterval);
                fullscreenCountdownInterval = null;
            }
            fullscreenChartData = null;

            document.getElementById('fullscreenChartModal').classList.remove('show');
            if (fullscreenChart) {
                fullscreenChart.remove();
                fullscreenChart = null;
            }
        }

        // Open fullscreen chart with position markers (Entry, SL, TP)
        async function openFullscreenChartWithPosition(pos, bot) {
            const symbol = pos.symbol;
            const sideEmoji = pos.side.toUpperCase() === 'LONG' ? '📈' : '📉';
            document.getElementById('fullscreenChartTitle').innerHTML = `${sideEmoji} ${getCoinIconHtml(symbol, 16)}${symbol} - ${pos.side.toUpperCase()} (${bot.timeframe})`;

            document.getElementById('fullscreenChartModal').classList.add('show');
            await new Promise(resolve => setTimeout(resolve, 100));

            const container = document.getElementById('fullscreenChartContainer');
            container.innerHTML = '';

            if (typeof LightweightCharts === 'undefined') {
                container.innerHTML = '<p style="color: #888; text-align: center; padding: 40px;">Chart library not loaded</p>';
                return;
            }

            fullscreenChart = LightweightCharts.createChart(container, {
                width: container.clientWidth,
                height: container.clientHeight,
                layout: {
                    background: { type: 'solid', color: '#1a1a2e' },
                    textColor: '#888',
                },
                grid: {
                    vertLines: { color: 'rgba(255, 255, 255, 0.05)' },
                    horzLines: { color: 'rgba(255, 255, 255, 0.05)' },
                },
                rightPriceScale: { borderColor: 'rgba(255, 255, 255, 0.1)' },
                timeScale: { borderColor: 'rgba(255, 255, 255, 0.1)', timeVisible: true, secondsVisible: false },
            });

            const candlestickSeries = fullscreenChart.addCandlestickSeries({
                upColor: '#00ff88',
                downColor: '#ff4444',
                borderDownColor: '#ff4444',
                borderUpColor: '#00ff88',
                wickDownColor: '#ff4444',
                wickUpColor: '#00ff88',
            });

            const tfMap = {'1m':'1','3m':'3','5m':'5','15m':'15','30m':'30','1h':'60','2h':'120','4h':'240','1d':'D'};
            const interval = tfMap[bot.timeframe] || '15';

            // Load klines
            try {
                const klinesResponse = await fetch(`/api/klines/${symbol}?interval=${interval}&limit=500`);
                const klinesData = await klinesResponse.json();
                if (klinesData.klines && klinesData.klines.length > 0) {
                    candlestickSeries.setData(klinesData.klines);
                }
            } catch (err) {
                console.error('Failed to load klines:', err);
            }

            // Add Entry Price line (cyan)
            candlestickSeries.createPriceLine({
                price: pos.entry_price,
                color: '#00d4ff',
                lineWidth: 2,
                lineStyle: LightweightCharts.LineStyle.Solid,
                axisLabelVisible: true,
                title: 'Entry',
            });

            // Add Stop Loss line (red)
            candlestickSeries.createPriceLine({
                price: pos.sl,
                color: '#ff4444',
                lineWidth: 2,
                lineStyle: LightweightCharts.LineStyle.Dashed,
                axisLabelVisible: true,
                title: 'SL',
            });

            // Add Take Profit line (green)
            candlestickSeries.createPriceLine({
                price: pos.tp,
                color: '#00ff88',
                lineWidth: 2,
                lineStyle: LightweightCharts.LineStyle.Dashed,
                axisLabelVisible: true,
                title: 'TP',
            });

            // Load indicators
            try {
                const indResponse = await fetch(`/api/indicators/${symbol}?interval=${interval}&limit=500`);
                const indData = await indResponse.json();

                if (indData.indicators) {
                    // EMA 200
                    if (bot.ema_enabled && indData.indicators.ema200 && indData.indicators.ema200.length > 0) {
                        const emaSeries = fullscreenChart.addLineSeries({
                            color: '#ffcc00',
                            lineWidth: 2,
                            title: 'EMA200',
                        });
                        emaSeries.setData(indData.indicators.ema200);
                    }

                    // SuperTrend lines
                    if (indData.indicators.supertrend1 && indData.indicators.supertrend1.length > 0) {
                        const st1Series = fullscreenChart.addLineSeries({ lineWidth: 1, lastValueVisible: false, priceLineVisible: false });
                        st1Series.setData(indData.indicators.supertrend1.map(p => ({time: p.time, value: p.value, color: p.color})));
                    }
                    if (indData.indicators.supertrend2 && indData.indicators.supertrend2.length > 0) {
                        const st2Series = fullscreenChart.addLineSeries({ lineWidth: 2, lastValueVisible: false, priceLineVisible: false });
                        st2Series.setData(indData.indicators.supertrend2.map(p => ({time: p.time, value: p.value, color: p.color})));
                    }
                    if (indData.indicators.supertrend3 && indData.indicators.supertrend3.length > 0) {
                        const st3Series = fullscreenChart.addLineSeries({ lineWidth: 3, lastValueVisible: false, priceLineVisible: false });
                        st3Series.setData(indData.indicators.supertrend3.map(p => ({time: p.time, value: p.value, color: p.color})));
                    }
                }
            } catch (err) {
                console.error('Failed to load indicators:', err);
            }

            // Update price info
            const priceEl = document.getElementById('fsPrice');
            priceEl.textContent = `${pos.current_price.toFixed(6)} | PnL: ${pos.pnl_usdt >= 0 ? '+' : ''}${pos.pnl_usdt.toFixed(4)} USDT (${pos.pnl_percent >= 0 ? '+' : ''}${pos.pnl_percent.toFixed(2)}%)`;
            priceEl.style.color = pos.pnl_usdt >= 0 ? '#00ff88' : '#ff4444';

            // Store data for updates
            fullscreenChartData = { bot, symbol, interval, candlestickSeries, position: pos };

            // Countdown timer
            updateCountdown();
            if (fullscreenCountdownInterval) clearInterval(fullscreenCountdownInterval);
            fullscreenCountdownInterval = setInterval(updateCountdown, 1000);

            // Fit content
            fullscreenChart.timeScale().fitContent();

            // Handle resize
            new ResizeObserver(() => {
                fullscreenChart.applyOptions({ width: container.clientWidth, height: container.clientHeight });
            }).observe(container);
        }

        // Charts
        let charts = {};
        let chartSymbols = ['BTCUSDT', 'ETHUSDT'];

        async function loadCharts() {
            const grid = document.getElementById('chartsGrid');
            grid.innerHTML = '';

            // Get current trading pairs
            try {
                const response = await fetch('/api/settings');
                const settings = await response.json();
                if (settings.trading_pairs && settings.trading_pairs.length > 0) {
                    chartSymbols = settings.trading_pairs;
                }
            } catch (err) {
                console.error('Failed to load settings for charts:', err);
            }

            // Create chart containers
            for (const symbol of chartSymbols) {
                const container = document.createElement('div');
                container.className = 'chart-container';
                container.innerHTML = `
                    <div class="chart-header">
                        <span class="chart-symbol" style="display: flex; align-items: center;">${getCoinIconHtml(symbol, 14)}${symbol}</span>
                        <span class="chart-price" id="price-${symbol}">--</span>
                    </div>
                    <div class="chart-wrapper" id="chart-${symbol}"></div>
                `;
                grid.appendChild(container);

                // Create chart
                await createChart(symbol);
            }
        }

        async function createChart(symbol) {
            const chartContainer = document.getElementById('chart-' + symbol);
            if (!chartContainer) return;

            // Clear existing chart
            chartContainer.innerHTML = '';

            // Check if LightweightCharts is available
            if (typeof LightweightCharts === 'undefined') {
                chartContainer.innerHTML = '<p style="color: #888; text-align: center; padding: 40px;">Chart library loading...</p>';
                return;
            }

            const chart = LightweightCharts.createChart(chartContainer, {
                width: chartContainer.clientWidth,
                height: 250,
                layout: {
                    background: { type: 'solid', color: 'transparent' },
                    textColor: '#888',
                },
                grid: {
                    vertLines: { color: 'rgba(255, 255, 255, 0.05)' },
                    horzLines: { color: 'rgba(255, 255, 255, 0.05)' },
                },
                crosshair: {
                    mode: LightweightCharts.CrosshairMode.Normal,
                },
                rightPriceScale: {
                    borderColor: 'rgba(255, 255, 255, 0.1)',
                },
                timeScale: {
                    borderColor: 'rgba(255, 255, 255, 0.1)',
                    timeVisible: true,
                },
            });

            const candlestickSeries = chart.addCandlestickSeries({
                upColor: '#00ff88',
                downColor: '#ff4444',
                borderDownColor: '#ff4444',
                borderUpColor: '#00ff88',
                wickDownColor: '#ff4444',
                wickUpColor: '#00ff88',
            });

            charts[symbol] = { chart, series: candlestickSeries };

            // Load data
            await updateChartData(symbol);

            // Handle resize
            new ResizeObserver(() => {
                chart.applyOptions({ width: chartContainer.clientWidth });
            }).observe(chartContainer);
        }

        async function updateChartData(symbol) {
            const interval = document.getElementById('chartTimeframe').value;
            try {
                const response = await fetch(`/api/klines/${symbol}?interval=${interval}&limit=500`);
                const data = await response.json();

                if (data.klines && data.klines.length > 0 && charts[symbol]) {
                    charts[symbol].series.setData(data.klines);

                    // Update price
                    const lastKline = data.klines[data.klines.length - 1];
                    const priceEl = document.getElementById('price-' + symbol);
                    if (priceEl) {
                        priceEl.textContent = '$' + parseFloat(lastKline.close).toFixed(2);
                    }
                }
            } catch (err) {
                console.error('Failed to load chart data for ' + symbol + ':', err);
            }
        }

        function updateAllCharts() {
            for (const symbol of chartSymbols) {
                updateChartData(symbol);
            }
        }

        // Trading Pairs
        let allPairs = [];
        let selectedPairs = new Set(['BTCUSDT', 'ETHUSDT']);

        async function loadTradingPairs() {
            try {
                const response = await fetch('/api/trading-pairs');
                const data = await response.json();
                allPairs = data.pairs || [];

                // Get current selected pairs
                const settingsResponse = await fetch('/api/settings');
                const settings = await settingsResponse.json();
                if (settings.trading_pairs) {
                    selectedPairs = new Set(settings.trading_pairs);
                }

                renderPairs();
            } catch (err) {
                console.error('Failed to load trading pairs:', err);
            }
        }

        function renderPairs() {
            const grid = document.getElementById('pairsGrid');
            const searchValue = document.getElementById('pairsSearch').value.toUpperCase();

            const filteredPairs = allPairs.filter(pair =>
                pair.symbol.toUpperCase().includes(searchValue) ||
                pair.base.toUpperCase().includes(searchValue)
            );

            grid.innerHTML = filteredPairs.map(pair => `
                <div class="pair-chip ${selectedPairs.has(pair.symbol) ? 'selected' : ''}"
                     onclick="togglePair('${pair.symbol}')"
                     style="display: inline-flex; align-items: center; gap: 4px;">
                    ${getCoinIconHtml(pair.symbol, 14)}${pair.symbol}
                </div>
            `).join('');
        }

        function filterPairs() {
            renderPairs();
        }

        function togglePair(symbol) {
            if (selectedPairs.has(symbol)) {
                selectedPairs.delete(symbol);
            } else {
                selectedPairs.add(symbol);
            }
            renderPairs();
        }

        async function applySelectedPairs() {
            const pairs = Array.from(selectedPairs);
            if (pairs.length === 0) {
                showToast('Select at least one pair');
                return;
            }

            try {
                const response = await fetch('/api/settings', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ trading_pairs: pairs })
                });
                const data = await response.json();

                if (data.success) {
                    showToast(t('settingsSaved'));
                    document.getElementById('tradingPairs').value = pairs.join(',');
                    chartSymbols = pairs;
                } else {
                    showToast(data.message);
                }
            } catch (err) {
                console.error('Failed to save pairs:', err);
            }
        }

        // Refresh charts every 30 seconds
        setInterval(() => {
            if (document.getElementById('tab-charts').classList.contains('active')) {
                updateAllCharts();
            }
        }, 30000);

        // ============== Position Chart (LightweightCharts) ==============
        let posChart = null;
        let posCandleSeries = null;
        let posChartInterval = null;
        let currentPositionId = null;
        let currentPositionData = null;
        let currentBot = null;

        // Price lines
        let entryPriceLine = null;
        let slPriceLine = null;
        let tpPriceLine = null;

        // Indicator series
        let emaLineSeries = null;
        let st1LineSeries = null;
        let st2LineSeries = null;
        let st3LineSeries = null;

        // Format price based on magnitude
        function formatPrice(price) {
            if (price >= 1000) return price.toFixed(2);
            if (price >= 1) return price.toFixed(4);
            return price.toFixed(6);
        }

        // Get interval in seconds
        function getIntervalSeconds(tf) {
            const map = { '1m': 60, '3m': 180, '5m': 300, '15m': 900, '30m': 1800,
                          '1h': 3600, '2h': 7200, '4h': 14400, '6h': 21600, '12h': 43200,
                          '1d': 86400, '1w': 604800 };
            return map[tf] || 3600;
        }

        async function openPositionChart(positionId) {
            currentPositionId = positionId;

            const position = positionsData.find(p => p.id === positionId);
            if (!position) {
                showToast('Position not found');
                return;
            }
            currentPositionData = position;

            const bot = botsData.find(b => b.id === position.bot_id);
            currentBot = bot;
            const timeframe = bot ? bot.timeframe : '1h';

            // Update header (with coin icon)
            document.getElementById('posChartSymbol').innerHTML = getCoinIconHtml(position.symbol, 16) + position.symbol;
            document.getElementById('posChartTimeframe').textContent = timeframe;

            const sideEl = document.getElementById('posChartSide');
            sideEl.textContent = position.side.toUpperCase();
            sideEl.style.background = position.side.toUpperCase() === 'LONG' ? '#00c853' : '#ff1744';
            sideEl.style.color = '#000';

            // Update price info
            document.getElementById('posChartEntry').textContent = formatPrice(position.entry_price);
            document.getElementById('posChartSL').textContent = formatPrice(position.sl);
            document.getElementById('posChartTP').textContent = formatPrice(position.tp);
            document.getElementById('posChartCurrentPrice').textContent = formatPrice(position.current_price);
            document.getElementById('posChartCurrentPrice').style.color = position.pnl_usdt >= 0 ? '#00c853' : '#ff1744';

            updatePositionPnl(position);

            // Show modal
            document.getElementById('positionChartModal').classList.add('show');

            // Create chart
            await createPositionChart(position, bot, timeframe);

            // Start real-time updates
            startChartUpdates(position.symbol, timeframe);
        }

        async function createPositionChart(position, bot, timeframe) {
            const container = document.getElementById('positionChartContainer');
            container.innerHTML = '';

            // Create chart with professional dark theme
            posChart = LightweightCharts.createChart(container, {
                width: container.clientWidth,
                height: container.clientHeight,
                layout: {
                    background: { type: 'solid', color: '#000000' },
                    textColor: '#666666',
                    fontSize: 11,
                },
                grid: {
                    vertLines: { color: 'rgba(42, 46, 57, 0.5)', style: 1 },
                    horzLines: { color: 'rgba(42, 46, 57, 0.5)', style: 1 },
                },
                rightPriceScale: {
                    borderColor: '#2a2e39',
                    scaleMargins: { top: 0.1, bottom: 0.1 },
                    borderVisible: true,
                    entireTextOnly: true,
                },
                timeScale: {
                    borderColor: '#2a2e39',
                    timeVisible: true,
                    secondsVisible: false,
                    borderVisible: true,
                },
                crosshair: {
                    mode: LightweightCharts.CrosshairMode.Normal,
                    vertLine: {
                        color: 'rgba(255, 255, 255, 0.2)',
                        width: 1,
                        style: LightweightCharts.LineStyle.Dashed,
                        labelBackgroundColor: '#2a2e39',
                    },
                    horzLine: {
                        color: 'rgba(255, 255, 255, 0.2)',
                        width: 1,
                        style: LightweightCharts.LineStyle.Dashed,
                        labelBackgroundColor: '#2a2e39',
                    },
                },
                handleScale: { axisPressedMouseMove: true },
                handleScroll: { mouseWheel: true, pressedMouseMove: true },
            });

            // Candlestick series with vibrant colors
            posCandleSeries = posChart.addCandlestickSeries({
                upColor: '#00c853',
                downColor: '#ff1744',
                borderUpColor: '#00c853',
                borderDownColor: '#ff1744',
                wickUpColor: '#00c853',
                wickDownColor: '#ff1744',
            });

            // Load candle data
            await loadChartData(position.symbol, timeframe);

            // Add price lines for Entry, SL, TP
            addPriceLines(position);

            // Add entry marker
            addEntryMarker(position);

            // Load and add indicators
            await loadIndicators(position.symbol, timeframe, bot);

            // Resize observer
            const resizeObserver = new ResizeObserver(() => {
                if (posChart) {
                    posChart.applyOptions({
                        width: container.clientWidth,
                        height: container.clientHeight,
                    });
                }
            });
            resizeObserver.observe(container);
        }

        async function loadChartData(symbol, timeframe) {
            try {
                const response = await fetch(`/api/klines/${symbol}?timeframe=${timeframe}&limit=300`);
                const data = await response.json();

                if (data.klines && data.klines.length > 0) {
                    posCandleSeries.setData(data.klines);
                    posChart.timeScale().fitContent();
                }
            } catch (err) {
                console.error('Failed to load chart data:', err);
            }
        }

        function addPriceLines(position) {
            const isLong = position.side.toUpperCase() === 'LONG';
            const size = position.size || 0;

            // Calculate PnL for labels
            const slPnl = isLong ? (position.sl - position.entry_price) * size : (position.entry_price - position.sl) * size;
            const tpPnl = isLong ? (position.tp - position.entry_price) * size : (position.entry_price - position.tp) * size;

            // Entry line (cyan, solid)
            entryPriceLine = posCandleSeries.createPriceLine({
                price: position.entry_price,
                color: '#00bcd4',
                lineWidth: 1,
                lineStyle: LightweightCharts.LineStyle.Solid,
                axisLabelVisible: true,
                title: 'Entry',
            });

            // Stop Loss line (red, dashed)
            slPriceLine = posCandleSeries.createPriceLine({
                price: position.sl,
                color: '#ff1744',
                lineWidth: 1,
                lineStyle: LightweightCharts.LineStyle.Dashed,
                axisLabelVisible: true,
                title: `SL ${slPnl >= 0 ? '+' : ''}${slPnl.toFixed(2)}`,
            });

            // Take Profit line (green, dashed)
            tpPriceLine = posCandleSeries.createPriceLine({
                price: position.tp,
                color: '#00c853',
                lineWidth: 1,
                lineStyle: LightweightCharts.LineStyle.Dashed,
                axisLabelVisible: true,
                title: `TP +${tpPnl.toFixed(2)}`,
            });
        }

        function addEntryMarker(position) {
            // Find the candle closest to entry time
            const entryTime = position.entry_time ? Math.floor(new Date(position.entry_time).getTime() / 1000) : null;

            if (entryTime) {
                const isLong = position.side.toUpperCase() === 'LONG';
                posCandleSeries.setMarkers([{
                    time: entryTime,
                    position: isLong ? 'belowBar' : 'aboveBar',
                    color: '#00bcd4',
                    shape: isLong ? 'arrowUp' : 'arrowDown',
                    text: isLong ? 'BUY' : 'SELL',
                }]);
            }
        }

        async function loadIndicators(symbol, timeframe, bot) {
            try {
                let url = `/api/indicators/${symbol}?timeframe=${timeframe}&limit=300`;
                if (bot) url += `&bot_id=${bot.id}`;

                const response = await fetch(url);
                const data = await response.json();

                if (data.error) {
                    console.error('Indicators error:', data.error);
                    return;
                }

                const ind = data.indicators || {};

                // EMA 200 - yellow thin line
                if (ind.ema200 && ind.ema200.length > 0 && (!bot || bot.ema_enabled !== false)) {
                    emaLineSeries = posChart.addLineSeries({
                        color: '#ffeb3b',
                        lineWidth: 1,
                        lineStyle: LightweightCharts.LineStyle.Solid,
                        priceLineVisible: false,
                        lastValueVisible: false,
                        crosshairMarkerVisible: false,
                    });
                    emaLineSeries.setData(ind.ema200);
                }

                // SuperTrend lines with direction-based coloring
                const addSuperTrend = (stData, lineWidth, enabled) => {
                    if (!stData || stData.length === 0 || enabled === false) return null;

                    const series = posChart.addLineSeries({
                        lineWidth: lineWidth,
                        priceLineVisible: false,
                        lastValueVisible: false,
                        crosshairMarkerVisible: false,
                    });

                    // Color based on direction
                    const coloredData = stData.map(d => ({
                        time: d.time,
                        value: d.value,
                        color: d.direction === 1 ? '#00c853' : '#ff1744'
                    }));
                    series.setData(coloredData);
                    return series;
                };

                // ST1 (fast) - thin
                if (ind.supertrend1) {
                    st1LineSeries = addSuperTrend(ind.supertrend1, 1, bot?.st1_enabled);
                }
                // ST2 (medium) - medium
                if (ind.supertrend2) {
                    st2LineSeries = addSuperTrend(ind.supertrend2, 2, bot?.st2_enabled);
                }
                // ST3 (slow) - thick
                if (ind.supertrend3) {
                    st3LineSeries = addSuperTrend(ind.supertrend3, 3, bot?.st3_enabled);
                }

            } catch (err) {
                console.error('Failed to load indicators:', err);
            }
        }

        function startChartUpdates(symbol, timeframe) {
            const intervalMs = 1000; // Update every 1 second for real-time feel

            posChartInterval = setInterval(async () => {
                if (!currentPositionId) return;

                // Update position data
                const position = positionsData.find(p => p.id === currentPositionId);
                if (position) {
                    currentPositionData = position;
                    updatePositionPnl(position);

                    // Update current price display
                    const priceEl = document.getElementById('posChartCurrentPrice');
                    priceEl.textContent = formatPrice(position.current_price);
                    priceEl.style.color = position.pnl_usdt >= 0 ? '#00c853' : '#ff1744';

                    // Update price level displays
                    document.getElementById('posChartEntry').textContent = formatPrice(position.entry_price);
                    document.getElementById('posChartSL').textContent = formatPrice(position.sl);
                    document.getElementById('posChartTP').textContent = formatPrice(position.tp);

                    // Update price lines if SL/TP changed
                    if (slPriceLine && position.sl !== slPriceLine.options().price) {
                        posCandleSeries.removePriceLine(slPriceLine);
                        posCandleSeries.removePriceLine(tpPriceLine);
                        posCandleSeries.removePriceLine(entryPriceLine);
                        addPriceLines(position);
                    }
                }

                // Update candle data
                try {
                    const response = await fetch(`/api/klines/${symbol}?timeframe=${timeframe}&limit=2`);
                    const data = await response.json();
                    if (data.klines && data.klines.length > 0) {
                        // Update last candle
                        data.klines.forEach(candle => {
                            posCandleSeries.update(candle);
                        });
                    }
                } catch (err) {
                    console.error('Chart update error:', err);
                }
            }, intervalMs);
        }

        function closePositionChart() {
            document.getElementById('positionChartModal').classList.remove('show');

            // Clean up
            if (posChartInterval) {
                clearInterval(posChartInterval);
                posChartInterval = null;
            }

            if (posChart) {
                posChart.remove();
                posChart = null;
            }

            // Reset state
            posCandleSeries = null;
            entryPriceLine = null;
            slPriceLine = null;
            tpPriceLine = null;
            emaLineSeries = null;
            st1LineSeries = null;
            st2LineSeries = null;
            st3LineSeries = null;
            currentPositionId = null;
            currentPositionData = null;
            currentBot = null;
        }

        function updatePositionPnl(position) {
            const pnlUsdt = document.getElementById('posChartPnlUsdt');
            const pnlPercent = document.getElementById('posChartPnlPercent');
            const pnlSign = position.pnl_usdt >= 0 ? '+' : '';
            const pnlColor = position.pnl_usdt >= 0 ? '#00c853' : '#ff1744';

            pnlUsdt.textContent = `${pnlSign}${position.pnl_usdt.toFixed(4)}`;
            pnlUsdt.style.color = pnlColor;
            pnlPercent.textContent = `(${pnlSign}${position.pnl_percent.toFixed(2)}%)`;
            pnlPercent.style.color = pnlColor;
        }

    </script>
</body>
</html>
"""


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, aila_session: Optional[str] = Cookie(None)):
    """Serve the login page."""
    # If already logged in, redirect to dashboard
    if verify_session(aila_session):
        return RedirectResponse(url="/", status_code=302)

    return Response(
        content=LOGIN_HTML,
        media_type="text/html",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        }
    )


@app.get("/auth/telegram")
async def telegram_auth(request: Request):
    """Handle Telegram Login Widget callback."""
    # Get all query parameters
    auth_data = dict(request.query_params)

    # Verify the authentication data
    if not verify_telegram_auth(auth_data):
        return RedirectResponse(url="/login?error=invalid_auth", status_code=302)

    # Check if user is allowed
    telegram_id = int(auth_data.get("id", 0))
    if telegram_id not in ALLOWED_TELEGRAM_IDS:
        return RedirectResponse(url="/login?error=access_denied", status_code=302)

    # Create session
    username = auth_data.get("username", "") or auth_data.get("first_name", "User")
    session_token = create_session(telegram_id, username)

    # Redirect to dashboard with session cookie
    response = RedirectResponse(url="/", status_code=302)
    response.set_cookie(
        key="aila_session",
        value=session_token,
        max_age=604800,  # 7 days
        httponly=True,
        secure=True,
        samesite="lax",
    )
    return response


@app.get("/logout")
async def logout(aila_session: Optional[str] = Cookie(None)):
    """Clear session and redirect to login."""
    # Remove session from storage
    if aila_session and aila_session in active_sessions:
        del active_sessions[aila_session]

    # Clear cookie and redirect
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie(key="aila_session")
    return response


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, aila_session: Optional[str] = Cookie(None)):
    """Serve the main dashboard (requires authentication)."""
    # Check authentication
    if not verify_session(aila_session):
        return RedirectResponse(url="/login", status_code=302)

    return Response(
        content=DASHBOARD_HTML,
        media_type="text/html",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        }
    )


# NOTE: /api/stats is defined in run_web.py with background client support for balance display
# This avoids duplicating the endpoint and allows balance to show even when no bot is running

@app.get("/api/logs")
async def get_logs():
    """Get buffered logs."""
    return {"logs": list(log_buffer)}


# Bot state (will be populated by run_web.py)
bot_state = {
    "running": False,
    "engine": None,
    "client": None,
    "start_callback": None,
    "stop_callback": None,
}

# Current settings (runtime)
runtime_settings = {
    "timeframe": "1h",
    "trading_pairs": ["BTCUSDT", "ETHUSDT"],
    "risk_per_trade": 2.0,
    "tp_risk_ratio": 2.0,
    "sl_mode": "supertrend_line",
    "leverage": 10,
    "max_open_positions": 3,
    "ema_enabled": True,
    "language": "en",  # en or ru
}


# Log messages in different languages
LOG_MESSAGES = {
    "en": {
        "trade_open": "[TRADE_OPEN] Position opened: {symbol} {side} qty={qty} entry={entry} SL={sl} TP={tp}",
        "trade_close_profit": "[TRADE_PROFIT] Position closed: {symbol} PnL: +{pnl:.2f}% (+{pnl_usdt:.2f} USDT)",
        "trade_close_loss": "[TRADE_LOSS] Position closed: {symbol} PnL: {pnl:.2f}% ({pnl_usdt:.2f} USDT)",
        "bot_started": "Bot started",
        "bot_stopped": "Bot stopped",
        "scanning_pairs": "Scanning {count} pairs...",
        "scan_complete": "Scan #{num} complete  signals={signals} positions={positions}",
        "signal_found": "Signal: {symbol} {side} price={price}",
        "order_placed": "Order placed: {symbol} {side} qty={qty}",
        "order_failed": "Order failed: {symbol} - {error}",
        "insufficient_balance": "Insufficient balance for order",
        "starting_engine": "Starting trading engine",
        "engine_started": "Trading engine started",
        "connected": "Connected to Bybit API",
        "balance": "Balance: {balance} USDT",
        "starting_bot": "Starting AILA Trading Bot...",
        "mode_auto": "Mode: AUTO SEARCH - scanning {pairs} pairs, max orders={max_orders}",
        "mode_manual": "Mode: MANUAL - trading {pairs} pairs",
        "config_info": "Config: timeframe={tf} leverage={lev}x ema={ema} order_size={size} USDT",
        "strategy_info": "Strategy: tp_ratio={tp} sl_mode={sl} risk={risk}%",
        "bot_running": "Bot running",
        "position_opened": "Position OPENED: {symbol} {side} qty={qty} @ {price}",
        "position_closed": "Position CLOSED: {symbol} PnL={pnl}",
    },
    "ru": {
        "trade_open": "[ОТКРЫТИЕ] Позиция открыта: {symbol} {side} кол-во={qty} вход={entry} SL={sl} TP={tp}",
        "trade_close_profit": "[ПРИБЫЛЬ] Позиция закрыта: {symbol} PnL: +{pnl:.2f}% (+{pnl_usdt:.2f} USDT)",
        "trade_close_loss": "[УБЫТОК] Позиция закрыта: {symbol} PnL: {pnl:.2f}% ({pnl_usdt:.2f} USDT)",
        "bot_started": "Бот запущен",
        "bot_stopped": "Бот остановлен",
        "scanning_pairs": "Сканирование {count} пар...",
        "scan_complete": "Скан #{num} завершён  сигналов={signals} позиций={positions}",
        "signal_found": "Сигнал: {symbol} {side} цена={price}",
        "order_placed": "Ордер размещён: {symbol} {side} кол-во={qty}",
        "order_failed": "Ошибка ордера: {symbol} - {error}",
        "insufficient_balance": "Недостаточно баланса для ордера",
        "starting_engine": "Запуск торгового движка",
        "engine_started": "Торговый движок запущен",
        "connected": "Подключено к Bybit API",
        "balance": "Баланс: {balance} USDT",
        "starting_bot": "Запуск AILA Trading Bot...",
        "mode_auto": "Режим: АВТОПОИСК - сканирование {pairs} пар, макс ордеров={max_orders}",
        "mode_manual": "Режим: РУЧНОЙ - торговля {pairs} парами",
        "config_info": "Конфиг: таймфрейм={tf} плечо={lev}x ema={ema} размер={size} USDT",
        "strategy_info": "Стратегия: tp_ratio={tp} sl_mode={sl} риск={risk}%",
        "bot_running": "Бот работает",
        "position_opened": "Позиция ОТКРЫТА: {symbol} {side} кол-во={qty} @ {price}",
        "position_closed": "Позиция ЗАКРЫТА: {symbol} PnL={pnl}",
    },
}


def get_log_message(key: str, **kwargs) -> str:
    """Get log message in current language."""
    lang = runtime_settings.get("language", "en")
    messages = LOG_MESSAGES.get(lang, LOG_MESSAGES["en"])
    template = messages.get(key, LOG_MESSAGES["en"].get(key, key))
    try:
        return template.format(**kwargs)
    except Exception:
        return template


@app.post("/api/language")
async def set_language(data: dict):
    """Set the log language."""
    lang = data.get("language", "en")
    if lang in ("en", "ru"):
        runtime_settings["language"] = lang
        return {"success": True, "language": lang}
    return {"success": False, "message": "Invalid language"}


@app.get("/api/bot/status")
async def get_bot_status():
    """Get current bot status."""
    return {"running": bot_state.get("running", False)}


@app.post("/api/bot/start")
async def start_bot():
    """Start the trading bot."""
    if bot_state.get("running"):
        return {"success": False, "message": "Bot is already running"}

    start_callback = bot_state.get("start_callback")
    if start_callback:
        try:
            await start_callback()
            return {"success": True, "message": "Bot started"}
        except Exception as e:
            return {"success": False, "message": str(e)}

    return {"success": False, "message": "Start callback not configured"}


@app.post("/api/bot/stop")
async def stop_bot():
    """Stop the trading bot."""
    if not bot_state.get("running"):
        return {"success": False, "message": "Bot is not running"}

    stop_callback = bot_state.get("stop_callback")
    if stop_callback:
        try:
            await stop_callback()
            return {"success": True, "message": "Bot stopped"}
        except Exception as e:
            return {"success": False, "message": str(e)}

    return {"success": False, "message": "Stop callback not configured"}


@app.get("/api/settings")
async def get_settings():
    """Get current strategy settings."""
    return runtime_settings


@app.post("/api/settings")
async def save_settings(settings: dict):
    """Save strategy settings."""
    try:
        if "timeframe" in settings:
            runtime_settings["timeframe"] = settings["timeframe"]
        if "trading_pairs" in settings:
            runtime_settings["trading_pairs"] = settings["trading_pairs"]
        if "risk_per_trade" in settings:
            runtime_settings["risk_per_trade"] = float(settings["risk_per_trade"])
        if "tp_risk_ratio" in settings:
            runtime_settings["tp_risk_ratio"] = float(settings["tp_risk_ratio"])
        if "sl_mode" in settings:
            runtime_settings["sl_mode"] = settings["sl_mode"]
        if "leverage" in settings:
            runtime_settings["leverage"] = int(settings["leverage"])
        if "max_open_positions" in settings:
            runtime_settings["max_open_positions"] = int(settings["max_open_positions"])
        if "ema_enabled" in settings:
            runtime_settings["ema_enabled"] = settings["ema_enabled"]

        return {"success": True, "message": "Settings saved"}
    except Exception as e:
        return {"success": False, "message": str(e)}


# 3 EMA Pullback Strategy settings storage
ema_pullback_settings = {
    "name": "3 EMA Scalping",
    "enabled": False,
    "max_pairs": 1,
    "timeframe": "1m",
    "leverage": 10,
    "order_size_usdt": 10,
    "position_size_mode": "fixed",
    "margin_mode": "isolated",
    "balance_usage_pct": 30,
    "risk_per_trade": 2.0,
    "daily_loss_limit_enabled": True,
    "daily_loss_limit_pct": 5.0,
    "max_losing_streak_enabled": True,
    "max_losing_streak": 3,
    "max_daily_trades_enabled": False,
    "max_daily_trades": 10,
    "ema_fast": 50,
    "ema_medium": 100,
    "ema_slow": 150,
    "slope_min": 0.1,
    "slope_candles": 5,
    "ema_distance_atr_mult": 1.0,
    "atr_period": 14,
    "touch_mode": "low_high",
    "touch_tolerance": 0.1,
    "forbid_ema150_touch": True,
    "sl_mode": "pullback",
    "sl_buffer": 0.1,
    "sl_fixed_pct": 1.0,
    "tp_mode": "trailing",
    "rr_ratio": 1.5,
    "trailing_be_enabled": True,
    "be_after_profit_pct": 0.3,
    "trailing_mode": "new_high_low",
    "trailing_buffer": 0.1,
    "trade_long": True,
    "trade_short": True,
}


@app.get("/api/strategy/3ema_pullback/settings")
async def get_strategy_settings():
    """Get 3 EMA Pullback strategy settings."""
    return {"settings": ema_pullback_settings}


@app.post("/api/strategy/3ema_pullback/settings")
async def save_strategy_settings(settings: dict):
    """Save 3 EMA Pullback strategy settings."""
    try:
        # Update settings
        for key, value in settings.items():
            if key in ema_pullback_settings:
                ema_pullback_settings[key] = value

        # Log settings change
        import logging
        logger = logging.getLogger("strategy_3ema")
        logger.info(f"Strategy settings updated: enabled={ema_pullback_settings['enabled']}, "
                    f"timeframe={ema_pullback_settings['timeframe']}, "
                    f"leverage={ema_pullback_settings['leverage']}x")

        return {"success": True, "message": "Strategy settings saved"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# Strategy state and callbacks
ema_strategy_state = {
    "running": False,
    "start_callback": None,
    "stop_callback": None,
}


@app.get("/api/strategy/3ema_pullback/status")
async def get_strategy_status():
    """Get 3 EMA Pullback strategy status."""
    from aila.strategies.ema_pullback_runner import get_runner
    runner = get_runner()
    if runner:
        return {
            "running": runner.running,
            "positions": len(runner.get_positions()),
            "stats": runner.get_stats()
        }
    return {
        "running": False,
        "positions": 0,
        "stats": None
    }


@app.post("/api/strategy/3ema_pullback/start")
async def start_strategy():
    """Start 3 EMA Pullback strategy."""
    try:
        if ema_strategy_state.get("start_callback"):
            success = await ema_strategy_state["start_callback"]()
            if success:
                ema_strategy_state["running"] = True
                return {"success": True, "message": "Strategy started"}
            else:
                return {"success": False, "error": "Failed to start strategy"}
        else:
            return {"success": False, "error": "Strategy runner not initialized"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/strategy/3ema_pullback/stop")
async def stop_strategy():
    """Stop 3 EMA Pullback strategy."""
    try:
        if ema_strategy_state.get("stop_callback"):
            await ema_strategy_state["stop_callback"]()
            ema_strategy_state["running"] = False
            return {"success": True, "message": "Strategy stopped"}
        else:
            return {"success": False, "error": "Strategy runner not initialized"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/api/strategy/3ema_pullback/positions")
async def get_strategy_positions():
    """Get open positions for 3 EMA Pullback strategy."""
    from aila.strategies.ema_pullback_runner import get_runner
    runner = get_runner()
    if runner:
        return {"positions": runner.get_positions()}
    return {"positions": []}


# Trading pairs cache
trading_pairs_cache = {
    "pairs": [],
    "last_update": None,
}

# Coin icons cache - stores icon URLs by coin symbol (e.g., "BTC" -> "https://...")
coin_icons_cache = {
    "icons": {},  # symbol -> icon_url
    "last_update": None,
}

# Multi-bot management (up to 10 bots)
MAX_BOTS = 10
bots_registry = {}  # bot_id -> bot_config

# Multi-bot engine instances - each bot has its own engine
bot_engines = {}  # bot_id -> TradingEngine instance
bot_clients = {}  # bot_id -> BybitClient instance
bot_runtime_settings = {}  # bot_id -> settings dict (per-bot runtime settings)

# Trading positions registry - tracks positions per bot
# position_id -> {bot_id, symbol, side, size, entry_price, sl, tp, pnl, status, opened_at}
positions_registry = {}


def get_running_bot_id():
    """Get the ID of first currently running or paused bot (for backwards compatibility)."""
    for bot_id, bot in bots_registry.items():
        if bot.get("status") in ("running", "paused"):
            return bot_id
    return None


def get_all_running_bot_ids():
    """Get list of all currently running or paused bot IDs."""
    return [bot_id for bot_id, bot in bots_registry.items()
            if bot.get("status") in ("running", "paused")]


def get_bot_for_symbol(symbol: str):
    """Get the bot_id that is trading a specific symbol.
    Returns the first running bot that has this symbol in its trading pairs.
    """
    for bot_id, bot in bots_registry.items():
        if bot.get("status") not in ("running", "paused"):
            continue
        # Check if bot trades this symbol
        bot_pairs = bot.get("trading_pairs", [])
        # In auto_search mode, bot trades all pairs
        if bot.get("bot_mode") == "auto_search" or symbol in bot_pairs:
            return bot_id
    return None


def _to_bool(val, default: bool = False) -> bool:
    """Convert value to boolean. Handles JS strings like "false", "0", etc."""
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    return str(val).lower() not in ('false', '0', '')


def get_bot_runtime_settings(bot_id: str):
    """Get runtime settings for a specific bot."""
    if bot_id in bot_runtime_settings:
        return bot_runtime_settings[bot_id]
    # Fallback to global runtime_settings for backwards compatibility
    return runtime_settings


def check_trading_pairs_conflict(bot_id: str, new_pairs: list) -> tuple[bool, list]:
    """Check if new bot's trading pairs conflict with running bots.

    Returns (has_conflict, conflicting_pairs).
    In manual mode, checks for exact pair overlaps.
    In auto_search mode, always conflicts if another auto_search bot is running.
    """
    conflicting_pairs = []
    new_pairs_set = set(new_pairs)

    for other_id, other_bot in bots_registry.items():
        if other_id == bot_id:
            continue
        if other_bot.get("status") not in ("running", "paused"):
            continue

        other_settings = bot_runtime_settings.get(other_id, {})
        other_pairs = set(other_settings.get("trading_pairs", other_bot.get("trading_pairs", [])))

        # Check for pair overlap
        overlap = new_pairs_set & other_pairs
        if overlap:
            conflicting_pairs.extend(list(overlap))

    return (len(conflicting_pairs) > 0, list(set(conflicting_pairs)))


def register_position(symbol: str, side: str, entry_price: float, size: float, sl: float, tp: float, bot_id: str = None):
    """Register a new position opened by a bot.

    Args:
        symbol: Trading pair symbol
        side: LONG or SHORT
        entry_price: Entry price
        size: Position size
        sl: Stop loss price
        tp: Take profit price
        bot_id: Optional bot ID. If not provided, uses get_bot_for_symbol() or get_running_bot_id()
    """
    import uuid

    # Determine which bot this position belongs to
    if not bot_id:
        # First try to find bot by symbol
        bot_id = get_bot_for_symbol(symbol)
    if not bot_id:
        # Fallback to any running bot
        bot_id = get_running_bot_id()
    if not bot_id:
        return None

    position_id = f"{symbol}_{str(uuid.uuid4())[:6]}"
    positions_registry[position_id] = {
        "id": position_id,
        "bot_id": bot_id,
        "symbol": symbol,
        "side": side,
        "size": size,
        "entry_price": entry_price,
        "current_price": entry_price,
        "sl": sl,
        "tp": tp,
        "pnl_usdt": 0.0,
        "pnl_percent": 0.0,
        "status": "open",
        "opened_at": datetime.now().isoformat(),
        "closed_at": None,
        "close_reason": None,
    }
    bot_name = bots_registry.get(bot_id, {}).get("name", bot_id)
    add_log(f"[info    ] Position registered: {symbol} {side} @ {entry_price} (Bot: {bot_name})")
    return position_id


def update_position_pnl(symbol: str, current_price: float, pnl_usdt: float, pnl_percent: float):
    """Update PnL for open positions of a symbol."""
    for pos_id, pos in positions_registry.items():
        if pos["symbol"] == symbol and pos["status"] == "open":
            pos["current_price"] = current_price
            pos["pnl_usdt"] = pnl_usdt
            pos["pnl_percent"] = pnl_percent


def close_position_record(symbol: str, reason: str, pnl_usdt: float = None, pnl_percent: float = None):
    """Mark position as closed and update bot stats."""
    for pos_id, pos in positions_registry.items():
        if pos["symbol"] == symbol and pos["status"] == "open":
            pos["status"] = "closed"
            pos["closed_at"] = datetime.now().isoformat()
            pos["close_reason"] = reason
            if pnl_usdt is not None:
                pos["pnl_usdt"] = pnl_usdt
            if pnl_percent is not None:
                pos["pnl_percent"] = pnl_percent

            # Update bot trading stats
            bot_id = pos.get("bot_id")
            if bot_id and bot_id in bots_registry:
                bot = bots_registry[bot_id]
                final_pnl = pnl_usdt if pnl_usdt is not None else pos.get("pnl_usdt", 0)
                bot["total_trades"] = bot.get("total_trades", 0) + 1
                bot["total_pnl_history"] = bot.get("total_pnl_history", 0) + final_pnl
                if final_pnl >= 0:
                    bot["winning_trades"] = bot.get("winning_trades", 0) + 1
                else:
                    bot["losing_trades"] = bot.get("losing_trades", 0) + 1

            return pos_id
    return None


def get_bot_positions(bot_id: str):
    """Get all positions for a specific bot."""
    return [pos for pos in positions_registry.values() if pos["bot_id"] == bot_id]


def get_bot_open_positions(bot_id: str):
    """Get open positions for a specific bot."""
    return [pos for pos in positions_registry.values()
            if pos["bot_id"] == bot_id and pos["status"] == "open"]


def get_bot_total_pnl(bot_id: str):
    """Calculate total PnL for a bot's open positions."""
    positions = get_bot_open_positions(bot_id)
    return sum(pos.get("pnl_usdt", 0) for pos in positions)


@app.get("/api/bots")
async def get_bots():
    """Get all configured bots with position stats."""
    # Sync positions with exchange to get fresh PnL
    sync_positions_from_exchange()

    bots_with_stats = []
    for bot in bots_registry.values():
        bot_copy = dict(bot)
        bot_id = bot["id"]
        # Add position stats
        open_positions = get_bot_open_positions(bot_id)
        open_pnl = sum(p.get("pnl_usdt", 0) for p in open_positions)
        bot_copy["open_positions_count"] = len(open_positions)
        bot_copy["open_pnl"] = open_pnl

        # Total PnL = closed positions history + open positions unrealized
        history_pnl = bot.get("total_pnl_history", 0)
        bot_copy["total_pnl"] = history_pnl + open_pnl
        bot_copy["closed_pnl"] = history_pnl

        # Win rate calculation
        total_trades = bot.get("total_trades", 0)
        winning_trades = bot.get("winning_trades", 0)
        bot_copy["win_rate"] = (winning_trades / total_trades * 100) if total_trades > 0 else 0

        # Add engine status
        bot_copy["has_engine"] = bot_id in bot_engines
        bot_copy["has_client"] = bot_id in bot_clients
        bots_with_stats.append(bot_copy)

    # Include count of running bots
    running_count = len([b for b in bots_registry.values() if b.get("status") == "running"])

    return {
        "bots": bots_with_stats,
        "max_bots": MAX_BOTS,
        "running_count": running_count,
        "total_engines": len(bot_engines),
    }


async def load_all_usdt_perpetual_pairs() -> list[str]:
    """Load all USDT perpetual pairs from cache or exchange API."""
    import httpx
    import re
    from datetime import datetime, timedelta

    dated_futures_pattern = re.compile(r'-\d{2}[A-Z]{3}\d{2}$')

    # Check if cache is fresh
    if trading_pairs_cache["pairs"] and trading_pairs_cache["last_update"]:
        if datetime.now() - trading_pairs_cache["last_update"] < timedelta(minutes=10):
            return [
                p["symbol"] for p in trading_pairs_cache["pairs"]
                if p.get("symbol")
                and p["symbol"] not in ("undefined", "null", "")
                and not dated_futures_pattern.search(p["symbol"])
            ]

    # Try to load from connected client first
    client = bot_state.get("client")
    if client:
        try:
            pairs = client.get_trading_pairs()
            result = []
            seen_symbols = set()
            for pair in pairs:
                if pair.quote_asset == "USDT" and pair.status == "Trading":
                    if pair.symbol not in seen_symbols and not dated_futures_pattern.search(pair.symbol):
                        seen_symbols.add(pair.symbol)
                        result.append({
                            "symbol": pair.symbol,
                            "base": pair.base_asset,
                            "quote": pair.quote_asset,
                            "minQty": str(pair.min_order_qty),
                            "minNotional": str(getattr(pair, 'min_notional', '5')),
                            "maxLeverage": pair.max_leverage,
                            "leverageStep": getattr(pair, 'leverage_step', 0.01),
                        })
            result.sort(key=lambda x: x["symbol"])
            trading_pairs_cache["pairs"] = result
            trading_pairs_cache["last_update"] = datetime.now()
            return [p["symbol"] for p in result]
        except Exception as e:
            logger.error("Failed to get trading pairs from client", error=str(e))

    # Fallback: fetch directly from Bybit public API
    try:
        async with httpx.AsyncClient(timeout=10.0) as http_client:
            response = await http_client.get(
                "https://api.bybit.com/v5/market/instruments-info",
                params={"category": "linear"}
            )
            data = response.json()

            if data.get("retCode") == 0:
                result = []
                for item in data.get("result", {}).get("list", []):
                    symbol = item.get("symbol", "")
                    if (item.get("quoteCoin") == "USDT"
                        and item.get("status") == "Trading"
                        and not dated_futures_pattern.search(symbol)):
                        lot_filter = item.get("lotSizeFilter", {})
                        leverage_filter = item.get("leverageFilter", {})
                        result.append({
                            "symbol": symbol,
                            "base": item.get("baseCoin"),
                            "quote": item.get("quoteCoin"),
                            "minQty": lot_filter.get("minOrderQty", "0.001"),
                            "minNotional": lot_filter.get("minNotionalValue", "5"),
                            "maxLeverage": int(float(leverage_filter.get("maxLeverage", "100"))),
                            "leverageStep": float(leverage_filter.get("leverageStep", "0.01")),
                        })
                result.sort(key=lambda x: x["symbol"])
                trading_pairs_cache["pairs"] = result
                trading_pairs_cache["last_update"] = datetime.now()
                logger.info(f"Loaded {len(result)} USDT perpetual pairs from Bybit API")
                return [p["symbol"] for p in result]
    except Exception as e:
        logger.error("Failed to fetch trading pairs from Bybit API", error=str(e))

    # Return from stale cache if available
    if trading_pairs_cache["pairs"]:
        return [
            p["symbol"] for p in trading_pairs_cache["pairs"]
            if p.get("symbol")
            and p["symbol"] not in ("undefined", "null", "")
            and not dated_futures_pattern.search(p["symbol"])
        ]

    # Last fallback
    return ["BTCUSDT", "ETHUSDT"]


@app.post("/api/bots")
async def create_bot(config: dict):
    """Create a new bot configuration."""
    if len(bots_registry) >= MAX_BOTS:
        return {"success": False, "message": f"Maximum {MAX_BOTS} bots allowed"}

    import uuid
    bot_id = str(uuid.uuid4())[:8]

    # Load all USDT perpetual pairs by default
    all_pairs = await load_all_usdt_perpetual_pairs()
    add_log(f"[info    ] Loaded {len(all_pairs)} USDT perpetual pairs for new bot")

    bot_config = {
        "id": bot_id,
        "name": config.get("name", f"Bot {len(bots_registry) + 1}"),
        "bot_mode": config.get("bot_mode", "manual"),
        "trading_pairs": config.get("trading_pairs", all_pairs),
        # max_trading_pairs from JS maps to max_simultaneous_orders
        "max_simultaneous_orders": config.get("max_trading_pairs", config.get("max_simultaneous_orders", 1)),
        "timeframe": config.get("timeframe", "15m"),
        "risk_per_trade": config.get("risk_per_trade", 2.0),
        "tp_risk_ratio": config.get("tp_risk_ratio", 2.0),
        "sl_mode": config.get("sl_mode", "supertrend_line"),
        "sl_supertrend_line": config.get("sl_supertrend_line", 2),
        "sl_fixed_percent": config.get("sl_fixed_percent", 2.0),
        "sl_atr_multiplier": config.get("sl_atr_multiplier", 1.5),
        "leverage": config.get("leverage", 10),
        "order_size": config.get("order_size", 100.0),
        "position_sizing_mode": config.get("position_sizing_mode", "fixed_amount"),
        "leverage_mode": config.get("leverage_mode", "cross"),
        "max_positions": config.get("max_positions", 3),
        # Ensure boolean type for all boolean fields (JS may send string "false")
        "ema_enabled": _to_bool(config.get("ema_enabled", True), True),
        "ema_filter_mode": config.get("ema_filter_mode", "strict"),
        "trailing_enabled": _to_bool(config.get("trailing_enabled", True), True),
        "trailing_mode": config.get("trailing_mode", "fix_percent"),
        "trailing_activation": config.get("trailing_activation", 1.0),
        "trailing_step": config.get("trailing_step", 0.5),
        "trailing_st_line": config.get("trailing_st_line", 2),
        "trailing_confirm_candles": config.get("trailing_confirm_candles", 1),
        # Take Profit settings
        "tp_mode": config.get("tp_mode", "rr"),
        "tp_fixed_percent": config.get("tp_fixed_percent", 2.0),
        # Partial TP settings
        "partial_tp_enabled": _to_bool(config.get("partial_tp_enabled", True), True),
        "partial_tp_close_percent": config.get("partial_tp_close_percent", 50),
        "partial_tp_sl_move": config.get("partial_tp_sl_move", "tp1"),
        "partial_tp_sl_offset": config.get("partial_tp_sl_offset", 0.2),
        # Trailing TP settings
        "trailing_tp_enabled": _to_bool(config.get("trailing_tp_enabled", False), False),
        "trailing_tp_mode": config.get("trailing_tp_mode", "st_line"),
        "trailing_tp_st_line": config.get("trailing_tp_st_line", 2),
        "trailing_tp_activation": config.get("trailing_tp_activation", 0.5),
        "trailing_tp_step": config.get("trailing_tp_step", 1.0),
        # Auto-trade filters
        "asset_filters_enabled": config.get("asset_filters_enabled", True),
        "filter_min_volume": config.get("filter_min_volume", 0),
        "filter_max_volume": config.get("filter_max_volume", 0),
        "filter_min_price": config.get("filter_min_price", 0),
        "filter_max_price": config.get("filter_max_price", 0),
        "filter_min_change": config.get("filter_min_change", 0),
        "filter_max_change": config.get("filter_max_change", 0),
        "filter_volatility_period": config.get("filter_volatility_period", 0),
        "filter_min_volatility": config.get("filter_min_volatility", 0),
        "filter_max_volatility": config.get("filter_max_volatility", 0),
        "st1_role": config.get("st1_role", "confirm"),
        "st2_role": config.get("st2_role", "confirm"),
        "st3_role": config.get("st3_role", "trigger"),
        "trigger_confirm_candles": config.get("trigger_confirm_candles", 1),
        # Strategy selection (supertrend / heikinashi)
        "strategy_type": config.get("strategy_type", "supertrend"),
        # Heikin Ashi settings
        "ha_entry_candles": config.get("ha_entry_candles", 2),
        "ha_exit_on_color_change": _to_bool(config.get("ha_exit_on_color_change", True), True),
        "ha_exit_candles": config.get("ha_exit_candles", 1),
        "ha_ema_enabled": _to_bool(config.get("ha_ema_enabled", True), True),
        "ha_ema_period": config.get("ha_ema_period", 200),
        "ha_ema_mode": config.get("ha_ema_mode", "strict"),
        "ha_volatility_enabled": _to_bool(config.get("ha_volatility_enabled", True), True),
        "ha_min_atr": config.get("ha_min_atr", 0.5),
        "ha_emergency_sl": config.get("ha_emergency_sl", 2.0),
        "ha_trailing_enabled": _to_bool(config.get("ha_trailing_enabled", True), True),
        "ha_trailing_activation": config.get("ha_trailing_activation", 1.0),
        "ha_trailing_distance": config.get("ha_trailing_distance", 0.5),
        "balance_usage_percent": config.get("balance_usage_percent", 100),
        # Daily loss limit settings
        "max_loss_enabled": config.get("max_loss_enabled", False),
        "max_loss_percent": config.get("max_loss_percent", 50),
        # Consecutive losses settings
        "consecutive_losses_enabled": config.get("consecutive_losses_enabled", False),
        "max_consecutive_losses": config.get("max_consecutive_losses", 3),
        "cooldown_after_loss_streak": config.get("cooldown_after_loss_streak", 30),
        "status": "stopped",
        "created_at": datetime.now().isoformat(),
        # Trading statistics (persistent)
        "started_at": None,  # Set when bot starts
        "total_trades": 0,
        "winning_trades": 0,
        "losing_trades": 0,
        "total_pnl_history": 0.0,  # Accumulated PnL from closed positions
        "initial_balance": 0.0,  # Balance when bot started (for % calculation)
    }

    # Calculate initial_balance from exchange balance and balance_usage_percent
    try:
        client = bot_state.get("client")
        if client:
            balance_obj = client.get_balance("USDT", use_cache=False)
            total_balance = float(balance_obj.total) if balance_obj else 0
            balance_usage_percent = bot_config.get("balance_usage_percent", 100)
            bot_config["initial_balance"] = total_balance * balance_usage_percent / 100
    except Exception:
        pass  # Will be calculated on bot start

    bots_registry[bot_id] = bot_config
    add_log(f"[info    ] Bot created: {bot_config['name']} (ID: {bot_id})")

    return {"success": True, "bot": bot_config}


@app.put("/api/bots/{bot_id}")
async def update_bot(bot_id: str, config: dict):
    """Update a bot configuration."""
    if bot_id not in bots_registry:
        return {"success": False, "message": "Bot not found"}

    bot = bots_registry[bot_id]

    # Update allowed fields
    if "name" in config:
        bot["name"] = config["name"]
    if "bot_mode" in config:
        bot["bot_mode"] = config["bot_mode"]
    if "trading_pairs" in config:
        bot["trading_pairs"] = config["trading_pairs"]
    # max_trading_pairs from JS maps to max_simultaneous_orders
    if "max_trading_pairs" in config:
        bot["max_simultaneous_orders"] = int(config["max_trading_pairs"])
    elif "max_simultaneous_orders" in config:
        bot["max_simultaneous_orders"] = int(config["max_simultaneous_orders"])
    if "timeframe" in config:
        bot["timeframe"] = config["timeframe"]
    if "risk_per_trade" in config:
        bot["risk_per_trade"] = float(config["risk_per_trade"])
    if "tp_risk_ratio" in config:
        bot["tp_risk_ratio"] = float(config["tp_risk_ratio"])
    if "sl_mode" in config:
        bot["sl_mode"] = config["sl_mode"]
    if "sl_supertrend_line" in config:
        bot["sl_supertrend_line"] = int(config["sl_supertrend_line"])
    if "sl_fixed_percent" in config:
        bot["sl_fixed_percent"] = float(config["sl_fixed_percent"])
    if "sl_atr_multiplier" in config:
        bot["sl_atr_multiplier"] = float(config["sl_atr_multiplier"])
    if "leverage" in config:
        bot["leverage"] = int(config["leverage"])
    if "order_size" in config:
        bot["order_size"] = float(config["order_size"])
    if "position_sizing_mode" in config:
        bot["position_sizing_mode"] = config["position_sizing_mode"]
    if "leverage_mode" in config:
        bot["leverage_mode"] = config["leverage_mode"]
    if "max_positions" in config:
        bot["max_positions"] = int(config["max_positions"])
    if "ema_enabled" in config:
        bot["ema_enabled"] = _to_bool(config["ema_enabled"])
    if "ema_filter_mode" in config:
        bot["ema_filter_mode"] = config["ema_filter_mode"]
    if "trailing_enabled" in config:
        bot["trailing_enabled"] = _to_bool(config["trailing_enabled"])
    if "trailing_mode" in config:
        bot["trailing_mode"] = config["trailing_mode"]
    if "trailing_activation" in config:
        bot["trailing_activation"] = float(config["trailing_activation"])
    if "trailing_step" in config:
        bot["trailing_step"] = float(config["trailing_step"])
    if "trailing_st_line" in config:
        bot["trailing_st_line"] = int(config["trailing_st_line"])
    if "trailing_confirm_candles" in config:
        bot["trailing_confirm_candles"] = int(config["trailing_confirm_candles"])
    # Take Profit settings
    if "tp_mode" in config:
        bot["tp_mode"] = config["tp_mode"]
    if "tp_fixed_percent" in config:
        bot["tp_fixed_percent"] = float(config["tp_fixed_percent"])
    # Partial TP settings
    if "partial_tp_enabled" in config:
        bot["partial_tp_enabled"] = _to_bool(config["partial_tp_enabled"])
    if "partial_tp_close_percent" in config:
        bot["partial_tp_close_percent"] = int(config["partial_tp_close_percent"])
    if "partial_tp_sl_move" in config:
        bot["partial_tp_sl_move"] = config["partial_tp_sl_move"]
    if "partial_tp_sl_offset" in config:
        bot["partial_tp_sl_offset"] = float(config["partial_tp_sl_offset"])
    # Trailing TP settings
    if "trailing_tp_enabled" in config:
        bot["trailing_tp_enabled"] = _to_bool(config["trailing_tp_enabled"])
    if "trailing_tp_mode" in config:
        bot["trailing_tp_mode"] = config["trailing_tp_mode"]
    if "trailing_tp_st_line" in config:
        bot["trailing_tp_st_line"] = int(config["trailing_tp_st_line"])
    if "trailing_tp_activation" in config:
        bot["trailing_tp_activation"] = float(config["trailing_tp_activation"])
    if "trailing_tp_step" in config:
        bot["trailing_tp_step"] = float(config["trailing_tp_step"])
    # Auto-trade filters
    if "asset_filters_enabled" in config:
        bot["asset_filters_enabled"] = _to_bool(config["asset_filters_enabled"], True)
    if "filter_min_volume" in config:
        bot["filter_min_volume"] = float(config["filter_min_volume"])
    if "filter_max_volume" in config:
        bot["filter_max_volume"] = float(config["filter_max_volume"])
    if "filter_min_price" in config:
        bot["filter_min_price"] = float(config["filter_min_price"])
    if "filter_max_price" in config:
        bot["filter_max_price"] = float(config["filter_max_price"])
    if "filter_min_change" in config:
        bot["filter_min_change"] = float(config["filter_min_change"])
    if "filter_max_change" in config:
        bot["filter_max_change"] = float(config["filter_max_change"])
    if "filter_volatility_period" in config:
        bot["filter_volatility_period"] = int(config["filter_volatility_period"])
    if "filter_min_volatility" in config:
        bot["filter_min_volatility"] = float(config["filter_min_volatility"])
    if "filter_max_volatility" in config:
        bot["filter_max_volatility"] = float(config["filter_max_volatility"])
    if "st1_role" in config:
        bot["st1_role"] = config["st1_role"]
    if "st2_role" in config:
        bot["st2_role"] = config["st2_role"]
    if "st3_role" in config:
        bot["st3_role"] = config["st3_role"]
    if "trigger_confirm_candles" in config:
        bot["trigger_confirm_candles"] = int(config["trigger_confirm_candles"])
    if "balance_usage_percent" in config:
        bot["balance_usage_percent"] = float(config["balance_usage_percent"])
        # Recalculate initial_balance when balance_usage_percent changes
        try:
            client = bot_state.get("client")
            if client:
                balance_obj = client.get_balance("USDT", use_cache=False)
                total_balance = float(balance_obj.total) if balance_obj else 0
                bot["initial_balance"] = total_balance * bot["balance_usage_percent"] / 100
        except Exception:
            pass
    # Daily loss limit settings
    if "max_loss_enabled" in config:
        bot["max_loss_enabled"] = _to_bool(config["max_loss_enabled"], False)
    if "max_loss_percent" in config:
        bot["max_loss_percent"] = float(config["max_loss_percent"])
    # Consecutive losses settings
    if "consecutive_losses_enabled" in config:
        bot["consecutive_losses_enabled"] = _to_bool(config["consecutive_losses_enabled"], False)
    if "max_consecutive_losses" in config:
        bot["max_consecutive_losses"] = int(config["max_consecutive_losses"])
    if "cooldown_after_loss_streak" in config:
        bot["cooldown_after_loss_streak"] = int(config["cooldown_after_loss_streak"])

    # If bot is running or paused, update engine's strategy config in real-time
    if bot.get("status") in ("running", "paused") and bot_id in bot_engines:
        engine = bot_engines[bot_id]
        if hasattr(engine, 'strategy') and hasattr(engine.strategy, 'config'):
            # Update Signal Entry settings
            if "st1_role" in config:
                engine.strategy.config.st1_role = config["st1_role"]
            if "st2_role" in config:
                engine.strategy.config.st2_role = config["st2_role"]
            if "st3_role" in config:
                engine.strategy.config.st3_role = config["st3_role"]
            if "trigger_confirm_candles" in config:
                engine.strategy.config.trigger_confirm_candles = int(config["trigger_confirm_candles"])
            # Update other strategy settings
            if "ema_enabled" in config:
                engine.strategy.config.ema_enabled = _to_bool(config["ema_enabled"])
            if "ema_filter_mode" in config:
                engine.strategy.config.ema_filter_mode = config["ema_filter_mode"]
            if "trailing_enabled" in config:
                engine.strategy.config.trailing_enabled = _to_bool(config["trailing_enabled"])
            if "trailing_mode" in config:
                engine.strategy.config.trailing_mode = config["trailing_mode"]
            if "trailing_activation" in config:
                engine.strategy.config.trailing_activation = float(config["trailing_activation"])
            if "trailing_step" in config:
                engine.strategy.config.trailing_step = float(config["trailing_step"])
            if "trailing_st_line" in config:
                engine.strategy.config.trailing_st_line = int(config["trailing_st_line"])
            if "trailing_confirm_candles" in config:
                engine.strategy.config.trailing_confirm_candles = int(config["trailing_confirm_candles"])
            if "partial_tp_enabled" in config:
                engine.strategy.config.partial_tp_enabled = _to_bool(config["partial_tp_enabled"])
            if "partial_tp_close_percent" in config:
                engine.strategy.config.partial_tp_close_percent = int(config["partial_tp_close_percent"])
            if "partial_tp_sl_move" in config:
                engine.strategy.config.partial_tp_sl_move = config["partial_tp_sl_move"]
            if "partial_tp_sl_offset" in config:
                engine.strategy.config.partial_tp_sl_offset = float(config["partial_tp_sl_offset"])
            if "trailing_tp_enabled" in config:
                engine.strategy.config.trailing_tp_enabled = _to_bool(config["trailing_tp_enabled"])
            if "trailing_tp_mode" in config:
                engine.strategy.config.trailing_tp_mode = config["trailing_tp_mode"]
            if "trailing_tp_st_line" in config:
                engine.strategy.config.trailing_tp_st_line = int(config["trailing_tp_st_line"])
            add_log(f"[info    ] Updated running engine strategy config for bot {bot['name']}")

        # Also update bot_runtime_settings
        if bot_id in bot_runtime_settings:
            for key, value in config.items():
                bot_runtime_settings[bot_id][key] = value

    add_log(f"[info    ] Bot updated: {bot['name']} (ID: {bot_id})")

    return {"success": True, "bot": bot}


@app.delete("/api/bots/{bot_id}")
async def delete_bot(bot_id: str):
    """Delete a bot configuration."""
    if bot_id not in bots_registry:
        return {"success": False, "message": "Bot not found"}

    bot = bots_registry[bot_id]
    if bot.get("status") == "running":
        return {"success": False, "message": "Cannot delete running bot. Stop it first."}

    del bots_registry[bot_id]
    add_log(f"[info    ] Bot deleted: {bot['name']} (ID: {bot_id})")

    return {"success": True}


@app.post("/api/bots/{bot_id}/start")
async def start_specific_bot(bot_id: str):
    """Start a specific bot with its own independent engine."""
    if bot_id not in bots_registry:
        return {"success": False, "message": "Bot not found"}

    bot = bots_registry[bot_id]
    if bot.get("status") == "running":
        return {"success": False, "message": "Bot is already running"}

    # Check if bot is already in bot_engines (shouldn't happen but safety check)
    if bot_id in bot_engines:
        return {"success": False, "message": "Bot engine already exists"}

    # Build per-bot runtime settings
    bot_settings = {}

    bot_settings["bot_mode"] = bot.get("bot_mode", "manual")
    bot_settings["max_simultaneous_orders"] = bot.get("max_simultaneous_orders", 1)

    # For auto_search mode, get all trading pairs
    if bot.get("bot_mode") == "auto_search":
        # Get all perpetual USDT pairs from cache or fetch
        if trading_pairs_cache["pairs"]:
            import re
            # Filter out any undefined/empty symbols and dated futures (e.g., BTCUSDT-23JAN26)
            dated_futures_pattern = re.compile(r'-\d{2}[A-Z]{3}\d{2}$')
            bot_settings["trading_pairs"] = [
                p["symbol"] for p in trading_pairs_cache["pairs"]
                if p.get("symbol")
                and p["symbol"] not in ("undefined", "null", "")
                and not dated_futures_pattern.search(p["symbol"])
            ]
        else:
            bot_settings["trading_pairs"] = ["BTCUSDT"]  # Fallback
        bot_settings["auto_search_active"] = True
    else:
        bot_settings["trading_pairs"] = bot["trading_pairs"]
        bot_settings["auto_search_active"] = False

    # Check for trading pair conflicts with other running bots
    has_conflict, conflicting = check_trading_pairs_conflict(bot_id, bot_settings["trading_pairs"])
    if has_conflict:
        # Log warning but allow start - user might want this intentionally
        add_log(f"[warning ] Bot {bot['name']} shares pairs with other running bots: {', '.join(conflicting[:5])}{'...' if len(conflicting) > 5 else ''}")

    bot_settings["timeframe"] = bot["timeframe"]
    bot_settings["risk_per_trade"] = bot["risk_per_trade"]
    bot_settings["tp_risk_ratio"] = bot["tp_risk_ratio"]
    bot_settings["sl_mode"] = bot["sl_mode"]
    bot_settings["sl_supertrend_line"] = bot.get("sl_supertrend_line", 2)
    bot_settings["sl_fixed_percent"] = bot.get("sl_fixed_percent", 2.0)
    bot_settings["sl_atr_multiplier"] = bot.get("sl_atr_multiplier", 1.5)
    bot_settings["leverage"] = bot["leverage"]
    bot_settings["order_size"] = bot.get("order_size", 100.0)
    bot_settings["position_sizing_mode"] = bot.get("position_sizing_mode", "fixed_amount")
    bot_settings["leverage_mode"] = bot.get("leverage_mode", "cross")
    bot_settings["margin_mode"] = bot.get("leverage_mode", "cross")  # alias for TradingEngineConfig
    bot_settings["max_open_positions"] = bot["max_positions"]
    bot_settings["ema_enabled"] = bot["ema_enabled"]
    bot_settings["ema_filter_mode"] = bot.get("ema_filter_mode", "strict")
    bot_settings["trailing_enabled"] = bot.get("trailing_enabled", True)
    bot_settings["trailing_mode"] = bot.get("trailing_mode", "fix_percent")
    bot_settings["trailing_activation"] = bot.get("trailing_activation", 1.0)
    bot_settings["trailing_step"] = bot.get("trailing_step", 0.5)
    bot_settings["trailing_st_line"] = bot.get("trailing_st_line", 2)
    bot_settings["trailing_confirm_candles"] = bot.get("trailing_confirm_candles", 1)
    # Take Profit settings
    bot_settings["tp_mode"] = bot.get("tp_mode", "rr")
    bot_settings["tp_fixed_percent"] = bot.get("tp_fixed_percent", 2.0)
    # Auto-trade filters
    bot_settings["asset_filters_enabled"] = _to_bool(bot.get("asset_filters_enabled", True), True)
    bot_settings["filter_min_volume"] = bot.get("filter_min_volume", 0)
    bot_settings["filter_max_volume"] = bot.get("filter_max_volume", 0)
    bot_settings["filter_min_price"] = bot.get("filter_min_price", 0)
    bot_settings["filter_max_price"] = bot.get("filter_max_price", 0)
    bot_settings["filter_min_change"] = bot.get("filter_min_change", 0)
    bot_settings["filter_max_change"] = bot.get("filter_max_change", 0)
    bot_settings["filter_volatility_period"] = bot.get("filter_volatility_period", 0)
    bot_settings["filter_min_volatility"] = bot.get("filter_min_volatility", 0)
    bot_settings["filter_max_volatility"] = bot.get("filter_max_volatility", 0)
    bot_settings["st1_role"] = bot.get("st1_role", "confirm")
    bot_settings["st2_role"] = bot.get("st2_role", "confirm")
    bot_settings["st3_role"] = bot.get("st3_role", "trigger")
    bot_settings["trigger_confirm_candles"] = bot.get("trigger_confirm_candles", 1)
    # Strategy type and Heikin Ashi settings
    bot_settings["strategy_type"] = bot.get("strategy_type", "supertrend")
    bot_settings["ha_entry_candles"] = bot.get("ha_entry_candles", 2)
    bot_settings["ha_exit_on_color_change"] = _to_bool(bot.get("ha_exit_on_color_change", True), True)
    bot_settings["ha_exit_candles"] = bot.get("ha_exit_candles", 1)
    bot_settings["ha_ema_enabled"] = _to_bool(bot.get("ha_ema_enabled", True), True)
    bot_settings["ha_ema_period"] = bot.get("ha_ema_period", 200)
    bot_settings["ha_ema_mode"] = bot.get("ha_ema_mode", "strict")
    bot_settings["ha_volatility_enabled"] = _to_bool(bot.get("ha_volatility_enabled", True), True)
    bot_settings["ha_min_atr"] = bot.get("ha_min_atr", 0.5)
    bot_settings["ha_emergency_sl"] = bot.get("ha_emergency_sl", 2.0)
    bot_settings["ha_trailing_enabled"] = _to_bool(bot.get("ha_trailing_enabled", True), True)
    bot_settings["ha_trailing_activation"] = bot.get("ha_trailing_activation", 1.0)
    bot_settings["ha_trailing_distance"] = bot.get("ha_trailing_distance", 0.5)
    # Partial TP settings
    bot_settings["partial_tp_enabled"] = _to_bool(bot.get("partial_tp_enabled", True), True)
    bot_settings["partial_tp_close_percent"] = bot.get("partial_tp_close_percent", 50)
    bot_settings["partial_tp_sl_move"] = bot.get("partial_tp_sl_move", "tp1")
    bot_settings["partial_tp_sl_offset"] = bot.get("partial_tp_sl_offset", 0.2)
    # Trailing TP settings
    bot_settings["trailing_tp_enabled"] = _to_bool(bot.get("trailing_tp_enabled", False), False)
    bot_settings["trailing_tp_mode"] = bot.get("trailing_tp_mode", "st_line")
    bot_settings["trailing_tp_st_line"] = bot.get("trailing_tp_st_line", 2)
    bot_settings["trailing_tp_activation"] = bot.get("trailing_tp_activation", 0.5)
    bot_settings["trailing_tp_step"] = bot.get("trailing_tp_step", 1.0)
    # Risk management - Daily loss limit
    bot_settings["max_loss_enabled"] = _to_bool(bot.get("max_loss_enabled", False), False)
    bot_settings["max_loss_percent"] = bot.get("max_loss_percent", 50)
    bot_settings["balance_usage_percent"] = bot.get("balance_usage_percent", 100)
    # Consecutive losses settings
    bot_settings["consecutive_losses_enabled"] = _to_bool(bot.get("consecutive_losses_enabled", False), False)
    bot_settings["max_consecutive_losses"] = bot.get("max_consecutive_losses", 3)
    bot_settings["cooldown_after_loss_streak"] = bot.get("cooldown_after_loss_streak", 30)

    # Store per-bot settings
    bot_runtime_settings[bot_id] = bot_settings

    # Also update global runtime_settings for backwards compatibility
    for key, value in bot_settings.items():
        runtime_settings[key] = value

    # Start the bot - use per-bot callback if available, fallback to global
    start_callback_for_bot = bot_state.get("start_callback_for_bot")
    start_callback = bot_state.get("start_callback")

    if start_callback_for_bot:
        # New multi-bot architecture - use per-bot callback
        bot["status"] = "running"
        bot["started_at"] = datetime.now().isoformat()

        # Calculate initial_balance from exchange balance and balance_usage_percent
        try:
            client = bot_state.get("client")
            if client:
                balance_obj = client.get_balance("USDT", use_cache=False)
                total_balance = float(balance_obj.total) if balance_obj else 0
                balance_usage_percent = bot.get("balance_usage_percent", 100)
                bot["initial_balance"] = total_balance * balance_usage_percent / 100
            else:
                bot["initial_balance"] = bot.get("order_size", 100.0)
        except Exception as e:
            bot["initial_balance"] = bot.get("order_size", 100.0)

        async def do_start():
            try:
                await start_callback_for_bot(bot_id)
            except Exception as e:
                bot["status"] = "stopped"
                bot_runtime_settings.pop(bot_id, None)
                add_log(f"[error   ] Failed to start bot {bot['name']}: {str(e)}")

        asyncio.create_task(do_start())
        return {"success": True, "message": f"Bot {bot['name']} started"}

    elif start_callback:
        # Legacy single-bot mode - use global callback
        bot["status"] = "running"
        bot["started_at"] = bot.get("started_at") or datetime.now().isoformat()

        # Calculate initial_balance from exchange balance and balance_usage_percent
        try:
            client = bot_state.get("client")
            if client:
                balance_obj = client.get_balance("USDT", use_cache=False)
                total_balance = float(balance_obj.total) if balance_obj else 0
                balance_usage_percent = bot.get("balance_usage_percent", 100)
                bot["initial_balance"] = total_balance * balance_usage_percent / 100
            else:
                bot["initial_balance"] = bot.get("order_size", 100.0)
        except Exception as e:
            bot["initial_balance"] = bot.get("order_size", 100.0)

        async def do_start():
            try:
                await start_callback()
            except Exception as e:
                bot["status"] = "stopped"
                bot_runtime_settings.pop(bot_id, None)
                add_log(f"[error   ] Failed to start bot: {str(e)}")

        asyncio.create_task(do_start())
        return {"success": True, "message": f"Bot {bot['name']} started"}

    return {"success": False, "message": "Start callback not configured"}


def close_all_bot_positions(bot_id: str):
    """Mark all positions for a bot as closed."""
    closed_count = 0
    for pos_id, pos in list(positions_registry.items()):
        if pos["bot_id"] == bot_id and pos["status"] == "open":
            pos["status"] = "closed"
            pos["closed_at"] = datetime.now().isoformat()
            pos["close_reason"] = "Bot stopped"
            closed_count += 1
    if closed_count > 0:
        add_log(f"[info    ] Closed {closed_count} position records")


@app.post("/api/bots/{bot_id}/stop")
async def stop_specific_bot(bot_id: str):
    """Stop a specific bot and its independent engine."""
    if bot_id not in bots_registry:
        return {"success": False, "message": "Bot not found"}

    bot = bots_registry[bot_id]
    if bot.get("status") not in ("running", "starting", "paused"):
        return {"success": False, "message": "Bot is not running"}

    # Try per-bot stop callback first (new multi-bot architecture)
    stop_callback_for_bot = bot_state.get("stop_callback_for_bot")
    stop_callback = bot_state.get("stop_callback")

    if stop_callback_for_bot and bot_id in bot_engines:
        # New multi-bot architecture - stop specific bot engine
        bot["status"] = "stopping"
        add_log(f"[info    ] Stopping bot {bot['name']}...")

        try:
            await asyncio.wait_for(stop_callback_for_bot(bot_id), timeout=12.0)
            bot["status"] = "stopped"
            # Close all position records for this bot
            close_all_bot_positions(bot_id)
            # Clean up per-bot settings
            bot_runtime_settings.pop(bot_id, None)
            return {"success": True, "message": "Bot stopped"}
        except asyncio.TimeoutError:
            bot["status"] = "stopped"
            close_all_bot_positions(bot_id)
            bot_runtime_settings.pop(bot_id, None)
            # Force cleanup
            bot_engines.pop(bot_id, None)
            bot_clients.pop(bot_id, None)
            add_log(f"[warning ] Stop timed out, forced stop")
            return {"success": True, "message": "Bot stopped (timeout)"}
        except Exception as e:
            bot["status"] = "stopped"
            close_all_bot_positions(bot_id)
            bot_runtime_settings.pop(bot_id, None)
            bot_engines.pop(bot_id, None)
            bot_clients.pop(bot_id, None)
            add_log(f"[error   ] Stop error: {str(e)}")
            return {"success": True, "message": f"Bot stopped with error: {str(e)}"}

    elif stop_callback:
        # Legacy single-bot mode - use global callback
        bot["status"] = "stopping"
        add_log(f"[info    ] Stopping bot {bot['name']}...")

        try:
            # Wait for stop with 3 second timeout
            await asyncio.wait_for(stop_callback(), timeout=3.0)
            bot["status"] = "stopped"
            # Close all position records for this bot
            close_all_bot_positions(bot_id)
            bot_runtime_settings.pop(bot_id, None)
            add_log(f"[info    ] Bot {bot['name']} stopped")
            return {"success": True, "message": "Bot stopped"}
        except asyncio.TimeoutError:
            bot["status"] = "stopped"
            close_all_bot_positions(bot_id)
            bot_runtime_settings.pop(bot_id, None)
            add_log(f"[warning ] Stop timed out, forced stop")
            return {"success": True, "message": "Bot stopped (timeout)"}
        except Exception as e:
            bot["status"] = "stopped"
            close_all_bot_positions(bot_id)
            bot_runtime_settings.pop(bot_id, None)
            add_log(f"[error   ] Stop error: {str(e)}")
            return {"success": True, "message": f"Bot stopped with error: {str(e)}"}

    # No callback - just mark as stopped and cleanup
    bot["status"] = "stopped"
    close_all_bot_positions(bot_id)
    bot_runtime_settings.pop(bot_id, None)
    bot_engines.pop(bot_id, None)
    bot_clients.pop(bot_id, None)
    add_log(f"[warning ] No stop callback, forcing stop")
    return {"success": True, "message": "Bot stopped (forced)"}


@app.post("/api/bots/{bot_id}/pause")
async def pause_specific_bot(bot_id: str):
    """Pause a specific bot (keeps positions open, allows editing)."""
    if bot_id not in bots_registry:
        return {"success": False, "message": "Bot not found"}

    bot = bots_registry[bot_id]
    if bot.get("status") != "running":
        return {"success": False, "message": "Bot is not running"}

    # Set paused state - engine will check this and skip scanning
    bot["status"] = "paused"

    # Set pause flag on per-bot engine if exists, otherwise global
    engine = bot_engines.get(bot_id) or bot_state.get("engine")
    if engine:
        engine.paused = True

    # Update per-bot settings
    if bot_id in bot_runtime_settings:
        bot_runtime_settings[bot_id]["paused"] = True
    runtime_settings["paused"] = True

    add_log(f"[info    ] Bot {bot['name']} paused - positions kept open")
    return {"success": True, "message": "Bot paused"}


@app.post("/api/bots/{bot_id}/resume")
async def resume_specific_bot(bot_id: str):
    """Resume a paused bot with updated settings."""
    if bot_id not in bots_registry:
        return {"success": False, "message": "Bot not found"}

    bot = bots_registry[bot_id]
    if bot.get("status") != "paused":
        return {"success": False, "message": "Bot is not paused"}

    # Update per-bot settings from bot config
    bot_settings = bot_runtime_settings.get(bot_id, {})
    bot_settings["bot_mode"] = bot.get("bot_mode", "manual")
    bot_settings["max_simultaneous_orders"] = bot.get("max_simultaneous_orders", 1)
    bot_settings["timeframe"] = bot["timeframe"]
    bot_settings["risk_per_trade"] = bot["risk_per_trade"]
    bot_settings["tp_risk_ratio"] = bot["tp_risk_ratio"]
    bot_settings["sl_mode"] = bot["sl_mode"]
    bot_settings["sl_supertrend_line"] = bot.get("sl_supertrend_line", 2)
    bot_settings["sl_fixed_percent"] = bot.get("sl_fixed_percent", 2.0)
    bot_settings["sl_atr_multiplier"] = bot.get("sl_atr_multiplier", 1.5)
    bot_settings["leverage"] = bot["leverage"]
    bot_settings["order_size"] = bot.get("order_size", 100.0)
    bot_settings["position_sizing_mode"] = bot.get("position_sizing_mode", "fixed_amount")
    bot_settings["leverage_mode"] = bot.get("leverage_mode", "cross")
    bot_settings["margin_mode"] = bot.get("leverage_mode", "cross")  # alias for TradingEngineConfig
    bot_settings["max_open_positions"] = bot["max_positions"]
    bot_settings["ema_enabled"] = bot["ema_enabled"]
    bot_settings["ema_filter_mode"] = bot.get("ema_filter_mode", "strict")
    bot_settings["trailing_enabled"] = bot.get("trailing_enabled", True)
    bot_settings["trailing_mode"] = bot.get("trailing_mode", "fix_percent")
    bot_settings["trailing_activation"] = bot.get("trailing_activation", 1.0)
    bot_settings["trailing_step"] = bot.get("trailing_step", 0.5)
    bot_settings["trailing_st_line"] = bot.get("trailing_st_line", 2)
    bot_settings["trailing_confirm_candles"] = bot.get("trailing_confirm_candles", 1)
    # Take Profit settings
    bot_settings["tp_mode"] = bot.get("tp_mode", "rr")
    bot_settings["tp_fixed_percent"] = bot.get("tp_fixed_percent", 2.0)
    # Auto-trade filters
    bot_settings["asset_filters_enabled"] = _to_bool(bot.get("asset_filters_enabled", True), True)
    bot_settings["filter_min_volume"] = bot.get("filter_min_volume", 0)
    bot_settings["filter_max_volume"] = bot.get("filter_max_volume", 0)
    bot_settings["filter_min_price"] = bot.get("filter_min_price", 0)
    bot_settings["filter_max_price"] = bot.get("filter_max_price", 0)
    bot_settings["filter_min_change"] = bot.get("filter_min_change", 0)
    bot_settings["filter_max_change"] = bot.get("filter_max_change", 0)
    bot_settings["filter_volatility_period"] = bot.get("filter_volatility_period", 0)
    bot_settings["filter_min_volatility"] = bot.get("filter_min_volatility", 0)
    bot_settings["filter_max_volatility"] = bot.get("filter_max_volatility", 0)
    # Signal Entry configuration
    bot_settings["st1_role"] = bot.get("st1_role", "confirm")
    bot_settings["st2_role"] = bot.get("st2_role", "confirm")
    bot_settings["st3_role"] = bot.get("st3_role", "trigger")
    bot_settings["trigger_confirm_candles"] = bot.get("trigger_confirm_candles", 1)
    # Strategy type and Heikin Ashi settings
    bot_settings["strategy_type"] = bot.get("strategy_type", "supertrend")
    bot_settings["ha_entry_candles"] = bot.get("ha_entry_candles", 2)
    bot_settings["ha_exit_on_color_change"] = _to_bool(bot.get("ha_exit_on_color_change", True), True)
    bot_settings["ha_exit_candles"] = bot.get("ha_exit_candles", 1)
    bot_settings["ha_ema_enabled"] = _to_bool(bot.get("ha_ema_enabled", True), True)
    bot_settings["ha_ema_period"] = bot.get("ha_ema_period", 200)
    bot_settings["ha_ema_mode"] = bot.get("ha_ema_mode", "strict")
    bot_settings["ha_volatility_enabled"] = _to_bool(bot.get("ha_volatility_enabled", True), True)
    bot_settings["ha_min_atr"] = bot.get("ha_min_atr", 0.5)
    bot_settings["ha_emergency_sl"] = bot.get("ha_emergency_sl", 2.0)
    bot_settings["ha_trailing_enabled"] = _to_bool(bot.get("ha_trailing_enabled", True), True)
    bot_settings["ha_trailing_activation"] = bot.get("ha_trailing_activation", 1.0)
    bot_settings["ha_trailing_distance"] = bot.get("ha_trailing_distance", 0.5)
    bot_settings["paused"] = False

    # Store updated per-bot settings
    bot_runtime_settings[bot_id] = bot_settings

    # Also update global runtime_settings for backwards compatibility
    for key, value in bot_settings.items():
        runtime_settings[key] = value

    # Update the running engine's strategy config
    update_callback = bot_state.get("update_settings_callback")
    if update_callback:
        update_callback()

    # Unpause the per-bot engine if exists, otherwise global
    engine = bot_engines.get(bot_id) or bot_state.get("engine")

    # Update per-bot engine's strategy config with new Signal Entry settings
    if engine and hasattr(engine, 'strategy') and hasattr(engine.strategy, 'config'):
        engine.strategy.config.st1_role = bot_settings.get("st1_role", "confirm")
        engine.strategy.config.st2_role = bot_settings.get("st2_role", "confirm")
        engine.strategy.config.st3_role = bot_settings.get("st3_role", "trigger")
        engine.strategy.config.trigger_confirm_candles = bot_settings.get("trigger_confirm_candles", 1)
        engine.strategy.config.ema_enabled = _to_bool(bot_settings.get("ema_enabled", True), True)
        engine.strategy.config.ema_filter_mode = bot_settings.get("ema_filter_mode", "strict")
        engine.strategy.config.trailing_enabled = _to_bool(bot_settings.get("trailing_enabled", True), True)
        engine.strategy.config.trailing_mode = bot_settings.get("trailing_mode", "fix_percent")
        engine.strategy.config.trailing_activation = bot_settings.get("trailing_activation", 1.0)
        engine.strategy.config.trailing_step = bot_settings.get("trailing_step", 0.5)
        engine.strategy.config.trailing_st_line = bot_settings.get("trailing_st_line", 2)
        engine.strategy.config.trailing_confirm_candles = bot_settings.get("trailing_confirm_candles", 1)
        engine.strategy.config.partial_tp_enabled = _to_bool(bot_settings.get("partial_tp_enabled", True), True)
        engine.strategy.config.partial_tp_close_percent = bot_settings.get("partial_tp_close_percent", 50)
        engine.strategy.config.partial_tp_sl_move = bot_settings.get("partial_tp_sl_move", "tp1")
        engine.strategy.config.partial_tp_sl_offset = bot_settings.get("partial_tp_sl_offset", 0.2)
        engine.strategy.config.trailing_tp_enabled = _to_bool(bot_settings.get("trailing_tp_enabled", False), False)
        engine.strategy.config.trailing_tp_mode = bot_settings.get("trailing_tp_mode", "st_line")
        engine.strategy.config.trailing_tp_st_line = bot_settings.get("trailing_tp_st_line", 2)
        add_log(f"[info    ] Updated strategy config for bot {bot['name']}")
    if engine:
        engine.paused = False

    bot["status"] = "running"
    runtime_settings["paused"] = False
    add_log(f"[info    ] Bot {bot['name']} resumed with updated settings")
    return {"success": True, "message": "Bot resumed"}


def sync_positions_from_exchange():
    """Sync positions with exchange data to update PnL and detect closed positions."""
    # Try to get any connected client - prefer per-bot clients, fallback to global
    client = None

    # First try per-bot clients
    for bid, c in bot_clients.items():
        if c and getattr(c, 'is_connected', False):
            client = c
            break

    # Fallback to global client
    if not client:
        client = bot_state.get("client")

    if not client or not getattr(client, 'is_connected', False):
        return

    try:
        exchange_positions = client.get_positions()
        # Get set of (symbol, side) tuples with open positions on exchange
        # This allows tracking both LONG and SHORT positions for the same symbol
        exchange_position_keys = set()
        for ex_pos in exchange_positions:
            if float(ex_pos.size) > 0:
                # Normalize side to uppercase for comparison
                if hasattr(ex_pos, 'side') and ex_pos.side:
                    side = ex_pos.side.name if hasattr(ex_pos.side, 'name') else str(ex_pos.side).upper()
                else:
                    side = "LONG"
                # Handle Bybit side naming: "Buy" = LONG, "Sell" = SHORT
                if side in ("BUY", "LONG"):
                    side = "LONG"
                elif side in ("SELL", "SHORT"):
                    side = "SHORT"
                exchange_position_keys.add((ex_pos.symbol, side))

        # Check each position in registry
        for pos_id, pos in list(positions_registry.items()):
            if pos["status"] != "open":
                continue

            symbol = pos["symbol"]
            pos_side = pos["side"].upper()

            # If position (symbol + side) not on exchange anymore - it was closed (TP/SL hit)
            if (symbol, pos_side) not in exchange_position_keys:
                pos["status"] = "closed"
                pos["closed_at"] = datetime.now().isoformat()
                pos["close_reason"] = "TP/SL"
                add_log(f"[info    ] Position auto-closed: {symbol} {pos_side}")
                continue

            # Update PnL for open positions
            for ex_pos in exchange_positions:
                if ex_pos.symbol == symbol and float(ex_pos.size) > 0:
                    # Check side matches
                    if hasattr(ex_pos, 'side') and ex_pos.side:
                        ex_side = ex_pos.side.name if hasattr(ex_pos.side, 'name') else str(ex_pos.side).upper()
                    else:
                        ex_side = "LONG"
                    if ex_side in ("BUY", "LONG"):
                        ex_side = "LONG"
                    elif ex_side in ("SELL", "SHORT"):
                        ex_side = "SHORT"

                    if ex_side != pos_side:
                        continue

                    current_price = float(ex_pos.mark_price or 0)
                    entry_price = float(ex_pos.entry_price or pos["entry_price"])
                    leverage = float(ex_pos.leverage or 1)

                    # Calculate PnL
                    if pos_side == "LONG":
                        pnl_percent = ((current_price - entry_price) / entry_price) * 100 * leverage
                    else:
                        pnl_percent = ((entry_price - current_price) / entry_price) * 100 * leverage

                    pnl_usdt = float(ex_pos.unrealized_pnl or 0)

                    pos["current_price"] = current_price
                    pos["pnl_usdt"] = pnl_usdt
                    pos["pnl_percent"] = pnl_percent
                    break
    except Exception as e:
        logger.debug(f"Sync positions error: {e}")


@app.get("/api/positions")
async def get_all_positions():
    """Get all positions with synced PnL."""
    # Sync with exchange before returning
    sync_positions_from_exchange()
    return {"positions": list(positions_registry.values())}


@app.get("/api/positions/{bot_id}")
async def get_positions_for_bot(bot_id: str):
    """Get positions for a specific bot."""
    positions = get_bot_positions(bot_id)
    open_positions = [p for p in positions if p["status"] == "open"]
    total_pnl = sum(p.get("pnl_usdt", 0) for p in open_positions)

    return {
        "positions": positions,
        "open_count": len(open_positions),
        "total_pnl": total_pnl,
    }


@app.post("/api/positions/{position_id}/close")
async def close_position(position_id: str):
    """Close a specific position manually."""
    if position_id not in positions_registry:
        return {"success": False, "message": "Position not found"}

    pos = positions_registry[position_id]
    if pos["status"] != "open":
        return {"success": False, "message": "Position already closed"}

    # Get client to close position - prefer per-bot client, fallback to global
    pos_bot_id = pos.get("bot_id")
    client = bot_clients.get(pos_bot_id) if pos_bot_id else None

    if not client or not getattr(client, 'is_connected', False):
        client = bot_state.get("client")

    if not client or not getattr(client, 'is_connected', False):
        return {"success": False, "message": "Not connected to exchange"}

    try:
        from aila.exchange.models import PositionSide
        side = PositionSide.LONG if pos["side"].upper() == "LONG" else PositionSide.SHORT

        # Close the position on exchange
        client.close_position(pos["symbol"], side)

        # Mark as closed in registry
        pos["status"] = "closed"
        pos["closed_at"] = datetime.now().isoformat()
        pos["close_reason"] = "Manual"

        add_log(f"[info    ] Position closed manually: {pos['symbol']} {pos['side']}")
        return {"success": True, "message": "Position closed"}

    except Exception as e:
        add_log(f"[error   ] Failed to close position: {str(e)}")
        return {"success": False, "message": str(e)}


@app.get("/api/trading-pairs")
async def get_trading_pairs():
    """Get available trading pairs from Bybit futures exchange."""
    import httpx
    from datetime import datetime, timedelta

    # Return cached data if fresh (less than 10 minutes old)
    if trading_pairs_cache["pairs"] and trading_pairs_cache["last_update"]:
        if datetime.now() - trading_pairs_cache["last_update"] < timedelta(minutes=10):
            return {"pairs": trading_pairs_cache["pairs"]}

    # First try using connected client
    client = bot_state.get("client")
    if client:
        try:
            pairs = client.get_trading_pairs()
            result = []
            seen_symbols = set()
            for pair in pairs:
                if pair.quote_asset == "USDT" and pair.status == "Trading":
                    if pair.symbol not in seen_symbols:
                        seen_symbols.add(pair.symbol)
                        result.append({
                            "symbol": pair.symbol,
                            "base": pair.base_asset,
                            "quote": pair.quote_asset,
                            "minQty": str(pair.min_order_qty),
                            "minNotional": str(getattr(pair, 'min_notional', '5')),
                            "maxLeverage": pair.max_leverage,
                            "leverageStep": getattr(pair, 'leverage_step', 0.01),
                        })

            # Sort by symbol
            result.sort(key=lambda x: x["symbol"])

            # Cache the result
            trading_pairs_cache["pairs"] = result
            trading_pairs_cache["last_update"] = datetime.now()

            return {"pairs": result}
        except Exception as e:
            logger.error("Failed to get trading pairs from client", error=str(e))

    # Fallback: fetch directly from Bybit public API
    try:
        async with httpx.AsyncClient(timeout=10.0) as http_client:
            response = await http_client.get(
                "https://api.bybit.com/v5/market/instruments-info",
                params={"category": "linear"}
            )
            data = response.json()

            if data.get("retCode") == 0:
                result = []
                for item in data.get("result", {}).get("list", []):
                    if item.get("quoteCoin") == "USDT" and item.get("status") == "Trading":
                        lot_filter = item.get("lotSizeFilter", {})
                        leverage_filter = item.get("leverageFilter", {})
                        result.append({
                            "symbol": item.get("symbol"),
                            "base": item.get("baseCoin"),
                            "quote": item.get("quoteCoin"),
                            "minQty": lot_filter.get("minOrderQty", "0.001"),
                            "minNotional": lot_filter.get("minNotionalValue", "5"),
                            "maxLeverage": int(float(leverage_filter.get("maxLeverage", "100"))),
                            "leverageStep": float(leverage_filter.get("leverageStep", "0.01")),
                        })

                # Sort by symbol
                result.sort(key=lambda x: x["symbol"])

                # Cache the result
                trading_pairs_cache["pairs"] = result
                trading_pairs_cache["last_update"] = datetime.now()

                lang = runtime_settings.get("language", "en")
                if lang == "ru":
                    logger.info(f"Загружено {len(result)} торговых пар с Bybit API")
                else:
                    logger.info(f"Loaded {len(result)} trading pairs from Bybit API")
                return {"pairs": result}

    except Exception as e:
        logger.error("Failed to fetch trading pairs from Bybit API", error=str(e))

    # Return cached data if available (even if stale)
    if trading_pairs_cache["pairs"]:
        return {"pairs": trading_pairs_cache["pairs"]}

    return {"pairs": [], "error": "Failed to load trading pairs"}


@app.get("/api/coin-icons")
async def get_coin_icons():
    """Get coin icons from CryptoCompare API with caching."""
    import httpx
    from datetime import datetime, timedelta

    # Return cached data if fresh (less than 24 hours old - icons don't change often)
    if coin_icons_cache["icons"] and coin_icons_cache["last_update"]:
        if datetime.now() - coin_icons_cache["last_update"] < timedelta(hours=24):
            return {"icons": coin_icons_cache["icons"]}

    # Fetch from CryptoCompare API (free, no API key required for basic usage)
    try:
        async with httpx.AsyncClient(timeout=15.0) as http_client:
            # Get coin list with images
            response = await http_client.get(
                "https://min-api.cryptocompare.com/data/all/coinlist",
                params={"summary": "true"}
            )
            data = response.json()

            if data.get("Response") == "Success":
                icons = {}
                base_url = "https://www.cryptocompare.com"
                coin_data = data.get("Data", {})

                for symbol, info in coin_data.items():
                    image_url = info.get("ImageUrl")
                    if image_url:
                        # Full URL for the icon
                        icons[symbol.upper()] = f"{base_url}{image_url}"

                # Cache the result
                coin_icons_cache["icons"] = icons
                coin_icons_cache["last_update"] = datetime.now()

                logger.info(f"Loaded {len(icons)} coin icons from CryptoCompare")
                return {"icons": icons}

    except Exception as e:
        logger.error("Failed to fetch coin icons from CryptoCompare", error=str(e))

    # Return cached data if available (even if stale)
    if coin_icons_cache["icons"]:
        return {"icons": coin_icons_cache["icons"]}

    return {"icons": {}}


@app.get("/api/coin-icon/{symbol}")
async def get_coin_icon(symbol: str):
    """Get icon URL for a specific coin symbol."""
    # Ensure cache is populated
    if not coin_icons_cache["icons"]:
        await get_coin_icons()

    symbol_upper = symbol.upper()
    icon_url = coin_icons_cache["icons"].get(symbol_upper)

    return {"symbol": symbol_upper, "icon": icon_url}


@app.get("/api/klines/{symbol}")
async def get_klines(symbol: str, interval: str = "15", limit: int = 100):
    """Get kline/candlestick data for charts."""
    client = bot_state.get("client")
    if not client:
        return {"error": "Not connected to exchange", "klines": []}

    try:
        df = client.get_klines(symbol=symbol, interval=interval, limit=limit)
        if df.empty:
            return {"klines": []}

        klines = []
        for idx, row in df.iterrows():
            klines.append({
                "time": int(idx.timestamp()),
                "open": row["open"],
                "high": row["high"],
                "low": row["low"],
                "close": row["close"],
                "volume": row["volume"],
            })

        return {"klines": klines}
    except Exception as e:
        logger.error("Failed to get klines", symbol=symbol, error=str(e))
        return {"klines": [], "error": str(e)}


@app.get("/api/ticker/{symbol}")
async def get_ticker(symbol: str):
    """Get current ticker for a symbol."""
    client = bot_state.get("client")
    if not client:
        return {"error": "Not connected to exchange"}

    try:
        ticker = client.get_ticker(symbol)
        if not ticker:
            return {"error": "Ticker not found"}

        return {
            "symbol": ticker.symbol,
            "lastPrice": str(ticker.last_price),
            "bidPrice": str(ticker.bid_price),
            "askPrice": str(ticker.ask_price),
            "high24h": str(ticker.high_24h),
            "low24h": str(ticker.low_24h),
            "volume24h": str(ticker.volume_24h),
            "change24h": ticker.change_24h,
        }
    except Exception as e:
        logger.error("Failed to get ticker", symbol=symbol, error=str(e))
        return {"error": str(e)}


@app.get("/api/indicators/{symbol}")
async def get_indicators(symbol: str, interval: str = "15", limit: int = 200):
    """Get indicator data (EMA200, SuperTrend) for charts."""
    client = bot_state.get("client")
    if not client:
        return {"error": "Not connected to exchange", "indicators": {}}

    try:
        import pandas as pd
        import numpy as np

        df = client.get_klines(symbol=symbol, interval=interval, limit=limit)
        if df.empty:
            return {"indicators": {}}

        # Calculate EMA 200
        ema_period = 200
        if len(df) >= ema_period:
            df['ema200'] = df['close'].ewm(span=ema_period, adjust=False).mean()
        else:
            df['ema200'] = df['close'].ewm(span=len(df), adjust=False).mean()

        # Calculate SuperTrend (3 different settings)
        def calculate_supertrend(df, period, multiplier):
            hl2 = (df['high'] + df['low']) / 2

            # ATR calculation
            tr1 = df['high'] - df['low']
            tr2 = abs(df['high'] - df['close'].shift(1))
            tr3 = abs(df['low'] - df['close'].shift(1))
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            atr = tr.rolling(window=period).mean()

            # SuperTrend
            upper_band = hl2 + (multiplier * atr)
            lower_band = hl2 - (multiplier * atr)

            supertrend = pd.Series(index=df.index, dtype=float)
            direction = pd.Series(index=df.index, dtype=int)

            for i in range(period, len(df)):
                if df['close'].iloc[i] > upper_band.iloc[i-1]:
                    direction.iloc[i] = 1
                elif df['close'].iloc[i] < lower_band.iloc[i-1]:
                    direction.iloc[i] = -1
                else:
                    direction.iloc[i] = direction.iloc[i-1] if i > period else 1

                if direction.iloc[i] == 1:
                    supertrend.iloc[i] = lower_band.iloc[i]
                else:
                    supertrend.iloc[i] = upper_band.iloc[i]

            return supertrend, direction

        # Three SuperTrend settings (from strategy)
        st1, dir1 = calculate_supertrend(df, 10, 1.0)
        st2, dir2 = calculate_supertrend(df, 11, 2.0)
        st3, dir3 = calculate_supertrend(df, 12, 3.0)

        # Prepare response
        result = {
            "ema200": [],
            "supertrend1": [],
            "supertrend2": [],
            "supertrend3": [],
        }

        for idx, row in df.iterrows():
            timestamp = int(idx.timestamp())

            if pd.notna(df.loc[idx, 'ema200']):
                result["ema200"].append({
                    "time": timestamp,
                    "value": float(df.loc[idx, 'ema200'])
                })

            # ST1 (fast, period=10, mult=1.0) - thinnest line
            if pd.notna(st1.loc[idx]):
                result["supertrend1"].append({
                    "time": timestamp,
                    "value": float(st1.loc[idx]),
                    "color": "#00ff88" if dir1.loc[idx] == 1 else "#ff4444"
                })

            # ST2 (medium, period=11, mult=2.0) - medium line
            if pd.notna(st2.loc[idx]):
                result["supertrend2"].append({
                    "time": timestamp,
                    "value": float(st2.loc[idx]),
                    "color": "#00ff88" if dir2.loc[idx] == 1 else "#ff4444"
                })

            # ST3 (slow, period=12, mult=3.0) - thickest line
            if pd.notna(st3.loc[idx]):
                result["supertrend3"].append({
                    "time": timestamp,
                    "value": float(st3.loc[idx]),
                    "color": "#00ff88" if dir3.loc[idx] == 1 else "#ff4444"
                })

        return {"indicators": result}

    except Exception as e:
        logger.error("Failed to calculate indicators", symbol=symbol, error=str(e))
        return {"indicators": {}, "error": str(e)}


@app.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket):
    """WebSocket endpoint for real-time logs."""
    await websocket.accept()
    connected_clients.add(websocket)

    try:
        # Don't send existing logs here - they're loaded via /api/logs HTTP endpoint
        # WebSocket only sends NEW logs that arrive after connection

        # Keep connection alive
        while True:
            try:
                # Wait for any message (ping/pong)
                await asyncio.wait_for(websocket.receive_text(), timeout=30)
            except asyncio.TimeoutError:
                # Send ping to keep connection alive
                await websocket.send_text("")
    except WebSocketDisconnect:
        pass
    finally:
        connected_clients.discard(websocket)


# Log handler to capture structlog output
class WebLogHandler:
    """Custom log handler that sends logs to the web interface."""

    def __call__(self, logger, method_name, event_dict):
        # Format the log message
        level = event_dict.get("level", method_name).upper()
        event = event_dict.get("event", "")

        # Build message with key-value pairs
        extras = " ".join(
            f"{k}={v}" for k, v in event_dict.items()
            if k not in ("level", "event", "timestamp", "logger")
        )

        message = f"[{level.lower():8}] {event}"
        if extras:
            message += f" {extras}"

        add_log(message)

        return event_dict


def run_web_server(host: str = "0.0.0.0", port: int = 8080):
    """Run the web server."""
    import uvicorn
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run_web_server()
