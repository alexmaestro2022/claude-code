"""
AILA - Web API and Dashboard

FastAPI-based web interface for monitoring and controlling the trading bot.
"""

import asyncio
import os
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

import structlog

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

    # Broadcast to all connected clients
    asyncio.create_task(broadcast_log(log_entry))


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


# Dashboard HTML with auto-refresh and copy button
DASHBOARD_HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AILA Trading Bot</title>
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
        }

        .container {
            max-width: 1400px;
            margin: 0 auto;
        }

        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 20px;
            background: rgba(255, 255, 255, 0.05);
            border-radius: 12px;
            margin-bottom: 20px;
            backdrop-filter: blur(10px);
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
            width: 12px;
            height: 12px;
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

        .logs-container {
            background: rgba(0, 0, 0, 0.3);
            border-radius: 12px;
            padding: 20px;
            border: 1px solid rgba(255, 255, 255, 0.1);
        }

        .logs-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 15px;
        }

        .logs-header h2 {
            font-size: 18px;
            color: #fff;
        }

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
            padding: 15px;
            height: 500px;
            overflow-y: auto;
            font-family: 'Consolas', 'Monaco', monospace;
            font-size: 13px;
            line-height: 1.6;
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
        }

        .toast.show {
            transform: translateY(0);
            opacity: 1;
        }

        .auto-scroll-indicator {
            font-size: 12px;
            color: #888;
            margin-left: 10px;
        }

        .auto-scroll-indicator.active {
            color: #00ff88;
        }

        footer {
            text-align: center;
            padding: 20px;
            color: #666;
            font-size: 12px;
        }

        .controls {
            margin-bottom: 20px;
        }

        .control-group {
            display: flex;
            gap: 15px;
            justify-content: center;
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
            justify-content: flex-end;
        }

        .running .btn-success {
            opacity: 0.5;
        }

        .header-right {
            display: flex;
            align-items: center;
            gap: 20px;
        }

        .lang-selector {
            padding: 8px 12px;
            border-radius: 6px;
            border: 1px solid rgba(255, 255, 255, 0.2);
            background: rgba(0, 0, 0, 0.3);
            color: #e0e0e0;
            font-size: 14px;
            cursor: pointer;
        }

        .lang-selector:focus {
            outline: none;
            border-color: #00d4ff;
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>AILA Trading Bot</h1>
            <div class="header-right">
                <select class="lang-selector" id="langSelector" onchange="changeLanguage(this.value)">
                    <option value="en">English</option>
                    <option value="ru">Русский</option>
                </select>
                <div class="status">
                    <div class="status-dot" id="statusDot"></div>
                    <span id="statusText" data-i18n="connecting">Connecting...</span>
                </div>
            </div>
        </header>

        <div class="controls">
            <div class="control-group">
                <button class="btn btn-success btn-large" id="startBtn" onclick="startBot()">
                    <span class="btn-icon">▶</span> <span data-i18n="startBot">Start Bot</span>
                </button>
                <button class="btn btn-danger btn-large" id="stopBtn" onclick="stopBot()" disabled>
                    <span class="btn-icon">■</span> <span data-i18n="stopBot">Stop Bot</span>
                </button>
                <button class="btn btn-primary btn-large" onclick="toggleSettings()">
                    <span class="btn-icon">⚙</span> <span data-i18n="settings">Settings</span>
                </button>
            </div>
        </div>

        <div class="settings-panel" id="settingsPanel" style="display: none;">
            <h3 data-i18n="strategySettings">Strategy Settings</h3>
            <div class="settings-grid">
                <div class="setting-item">
                    <label data-i18n="timeframe">Timeframe</label>
                    <select id="timeframe">
                        <option value="1m">1m</option>
                        <option value="5m">5m</option>
                        <option value="15m">15m</option>
                        <option value="30m">30m</option>
                        <option value="1h" selected>1h</option>
                        <option value="4h">4h</option>
                        <option value="1d">1d</option>
                    </select>
                </div>
                <div class="setting-item">
                    <label data-i18n="tradingPairs">Trading Pairs</label>
                    <input type="text" id="tradingPairs" value="BTCUSDT,ETHUSDT" placeholder="BTCUSDT,ETHUSDT">
                </div>
                <div class="setting-item">
                    <label data-i18n="riskPerTrade">Risk per Trade (%)</label>
                    <input type="number" id="riskPerTrade" value="2" min="0.1" max="10" step="0.1">
                </div>
                <div class="setting-item">
                    <label data-i18n="tpRatio">Take Profit Ratio (R:R)</label>
                    <input type="number" id="tpRatio" value="2" min="1" max="10" step="0.5">
                </div>
                <div class="setting-item">
                    <label data-i18n="slMode">Stop Loss Mode</label>
                    <select id="slMode">
                        <option value="supertrend_line" selected>SuperTrend Line</option>
                        <option value="fixed_percent" data-i18n="fixedPercent">Fixed Percent</option>
                        <option value="atr">ATR</option>
                    </select>
                </div>
                <div class="setting-item">
                    <label data-i18n="leverage">Leverage</label>
                    <input type="number" id="leverage" value="10" min="1" max="100" step="1">
                </div>
                <div class="setting-item">
                    <label data-i18n="maxPositions">Max Open Positions</label>
                    <input type="number" id="maxPositions" value="3" min="1" max="10" step="1">
                </div>
                <div class="setting-item">
                    <label data-i18n="emaFilter">EMA Filter</label>
                    <select id="emaEnabled">
                        <option value="true" selected data-i18n="enabled">Enabled</option>
                        <option value="false" data-i18n="disabled">Disabled</option>
                    </select>
                </div>
            </div>
            <div class="settings-actions">
                <button class="btn btn-secondary" onclick="loadSettings()" data-i18n="reset">Reset</button>
                <button class="btn btn-primary" onclick="saveSettings()" data-i18n="saveSettings">Save Settings</button>
            </div>
        </div>

        <div class="cards">
            <div class="card">
                <div class="card-title" data-i18n="balance">Balance (USDT)</div>
                <div class="card-value" id="balance">--</div>
            </div>
            <div class="card">
                <div class="card-title" data-i18n="openPositions">Open Positions</div>
                <div class="card-value" id="positions">--</div>
            </div>
            <div class="card">
                <div class="card-title" data-i18n="todayTrades">Today's Trades</div>
                <div class="card-value" id="trades">--</div>
            </div>
            <div class="card">
                <div class="card-title" data-i18n="todayPnl">Today's PnL</div>
                <div class="card-value" id="pnl">--</div>
            </div>
        </div>

        <div class="logs-container">
            <div class="logs-header">
                <div style="display: flex; align-items: center;">
                    <h2 data-i18n="liveLogs">Live Logs</h2>
                    <span class="auto-scroll-indicator active" id="autoScrollIndicator" data-i18n="autoScrollOn">Auto-scroll ON</span>
                </div>
                <div class="btn-group">
                    <button class="btn btn-secondary" onclick="clearLogs()" data-i18n="clear">Clear</button>
                    <button class="btn btn-primary" onclick="copyLogs()" data-i18n="copyLogs">Copy Logs</button>
                </div>
            </div>
            <div class="logs" id="logs"></div>
        </div>

        <footer>
            AILA v1.0.0 | Triple SuperTrend + EMA200 Strategy
        </footer>
    </div>

    <div class="toast" id="toast">Logs copied to clipboard!</div>

    <script>
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
                timeframe: 'Timeframe',
                tradingPairs: 'Trading Pairs',
                riskPerTrade: 'Risk per Trade (%)',
                tpRatio: 'Take Profit Ratio (R:R)',
                slMode: 'Stop Loss Mode',
                leverage: 'Leverage',
                maxPositions: 'Max Open Positions',
                emaFilter: 'EMA Filter',
                enabled: 'Enabled',
                disabled: 'Disabled',
                fixedPercent: 'Fixed Percent',
                reset: 'Reset',
                saveSettings: 'Save Settings',
                balance: 'Balance (USDT)',
                openPositions: 'Open Positions',
                todayTrades: "Today's Trades",
                todayPnl: "Today's PnL",
                liveLogs: 'Live Logs',
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
                logsCleared: '--- Logs cleared ---'
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
                timeframe: 'Таймфрейм',
                tradingPairs: 'Торговые пары',
                riskPerTrade: 'Риск на сделку (%)',
                tpRatio: 'Тейк-профит (R:R)',
                slMode: 'Режим стоп-лосса',
                leverage: 'Плечо',
                maxPositions: 'Макс. позиций',
                emaFilter: 'EMA фильтр',
                enabled: 'Включен',
                disabled: 'Выключен',
                fixedPercent: 'Фикс. процент',
                reset: 'Сброс',
                saveSettings: 'Сохранить',
                balance: 'Баланс (USDT)',
                openPositions: 'Открытые позиции',
                todayTrades: 'Сделок сегодня',
                todayPnl: 'PnL за день',
                liveLogs: 'Логи',
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
                logsCleared: '--- Логи очищены ---'
            }
        };

        let currentLang = localStorage.getItem('ailaLang') || 'en';

        function t(key) {
            return i18n[currentLang][key] || i18n['en'][key] || key;
        }

        function changeLanguage(lang) {
            currentLang = lang;
            localStorage.setItem('ailaLang', lang);
            updateUI();
        }

        function updateUI() {
            document.querySelectorAll('[data-i18n]').forEach(el => {
                const key = el.getAttribute('data-i18n');
                if (i18n[currentLang][key]) {
                    el.textContent = i18n[currentLang][key];
                }
            });
            document.getElementById('langSelector').value = currentLang;
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
                document.getElementById('statusText').textContent = t('connected');
                reconnectAttempts = 0;
            };

            ws.onmessage = function(event) {
                addLogLine(event.data);
            };

            ws.onclose = function() {
                console.log('WebSocket disconnected');
                document.getElementById('statusDot').classList.add('disconnected');
                document.getElementById('statusText').textContent = t('disconnected');

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

            // Color based on content
            if (text.includes('[error]') || text.includes('Error') || text.includes('Failed')) {
                line.classList.add('log-error');
            } else if (text.includes('[warning]') || text.includes('Warning')) {
                line.classList.add('log-warning');
            } else if (text.includes('success') || text.includes('Filled') || text.includes('opened')) {
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
            for (let i = 0; i < logLines.length; i++) {
                text += logLines[i].textContent;
                if (i < logLines.length - 1) text += String.fromCharCode(10);
            }

            // Try modern clipboard API first
            if (navigator.clipboard && navigator.clipboard.writeText) {
                navigator.clipboard.writeText(text).then(function() {
                    showToast(t('logsCopied'));
                }).catch(function(err) {
                    console.error('Clipboard API failed:', err);
                    fallbackCopy(text);
                });
            } else {
                fallbackCopy(text);
            }
        }

        function fallbackCopy(text) {
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
                    showToast(t('logsCopied'));
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

            const indicator = document.getElementById('autoScrollIndicator');
            if (autoScroll) {
                indicator.textContent = t('autoScrollOn');
                indicator.classList.add('active');
            } else {
                indicator.textContent = t('autoScrollOff');
                indicator.classList.remove('active');
            }
        });

        // Fetch stats periodically
        async function fetchStats() {
            try {
                const response = await fetch('/api/stats');
                const data = await response.json();

                document.getElementById('balance').textContent =
                    data.balance ? data.balance.toFixed(2) : '--';
                document.getElementById('positions').textContent =
                    data.positions !== undefined ? data.positions : '--';
                document.getElementById('trades').textContent =
                    data.trades !== undefined ? data.trades : '--';

                const pnlEl = document.getElementById('pnl');
                if (data.pnl !== undefined) {
                    pnlEl.textContent = (data.pnl >= 0 ? '+' : '') + data.pnl.toFixed(2) + '%';
                    pnlEl.className = 'card-value ' + (data.pnl >= 0 ? 'positive' : 'negative');
                } else {
                    pnlEl.textContent = '--';
                }
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

        // Initialize
        connectWebSocket();
        loadInitialLogs();
        fetchStats();

        // Refresh stats every 10 seconds
        setInterval(fetchStats, 10000);

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
    </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """Serve the main dashboard."""
    return DASHBOARD_HTML


@app.get("/api/stats")
async def get_stats():
    """Get current trading statistics."""
    # This will be populated by the trading engine
    return {
        "balance": None,
        "positions": 0,
        "trades": 0,
        "pnl": 0.0,
    }


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
}


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


@app.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket):
    """WebSocket endpoint for real-time logs."""
    await websocket.accept()
    connected_clients.add(websocket)

    try:
        # Send existing logs
        for log in log_buffer:
            await websocket.send_text(log)

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
