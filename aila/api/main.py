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


# Dashboard HTML with auto-refresh and copy button
DASHBOARD_HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AILA Trading Bot</title>
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
        }

        .container {
            max-width: 1600px;
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
            height: 300px;
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
            z-index: 1000;
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
            margin-bottom: 20px;
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
            justify-content: space-between;
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

        <!-- Tabs Navigation -->
        <div class="tabs">
            <button class="tab active" onclick="showTab('dashboard')" data-i18n="dashboard">Dashboard</button>
            <button class="tab" onclick="showTab('bots')" data-i18n="botsManager">Bots Manager</button>
            <button class="tab" onclick="showTab('charts')" data-i18n="charts">Charts</button>
            <button class="tab" onclick="showTab('pairs')" data-i18n="tradingPairs">Trading Pairs</button>
        </div>

        <!-- Dashboard Tab -->
        <div class="tab-content active" id="tab-dashboard">
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
        </div>

        <!-- Bots Manager Tab -->
        <div class="tab-content" id="tab-bots">
            <div class="bots-section">
                <div class="bots-header">
                    <h3 data-i18n="botsManager">Bots Manager</h3>
                    <button class="btn btn-primary" onclick="showCreateBotModal()" data-i18n="createBot">+ Create Bot</button>
                </div>
                <div class="bots-grid" id="botsGrid">
                    <!-- Bots will be loaded here -->
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

        <footer>
            AILA v2.0.0 | Triple SuperTrend + EMA200 Strategy | Multi-Bot Trading Platform
        </footer>
    </div>

    <div class="toast" id="toast">Logs copied to clipboard!</div>

    <!-- Create Bot Modal -->
    <div class="modal" id="createBotModal">
        <div class="modal-content" style="max-width: 700px;">
            <div class="modal-header">
                <h3 data-i18n="createBot">Create Bot</h3>
                <button class="modal-close" onclick="closeModal('createBotModal')">&times;</button>
            </div>
            <div class="settings-grid">
                <div class="setting-item">
                    <label data-i18n="botName">Bot Name</label>
                    <input type="text" id="newBotName" placeholder="My Bot">
                </div>
                <div class="setting-item">
                    <label data-i18n="tradingPair">Trading Pair</label>
                    <select id="newBotPair" style="max-height: 200px;">
                        <option value="BTCUSDT">BTCUSDT</option>
                    </select>
                </div>
                <div class="setting-item">
                    <label data-i18n="timeframe">Timeframe</label>
                    <select id="newBotTimeframe">
                        <option value="1m">1m</option>
                        <option value="3m">3m</option>
                        <option value="5m">5m</option>
                        <option value="15m" selected>15m</option>
                        <option value="30m">30m</option>
                        <option value="1h">1h</option>
                        <option value="2h">2h</option>
                        <option value="4h">4h</option>
                        <option value="1d">1d</option>
                    </select>
                </div>
                <div class="setting-item">
                    <label data-i18n="leverage">Leverage</label>
                    <input type="number" id="newBotLeverage" value="10" min="1" max="100">
                </div>
                <div class="setting-item">
                    <label data-i18n="riskPerTrade">Risk per Trade (%)</label>
                    <input type="number" id="newBotRisk" value="2" min="0.1" max="10" step="0.1">
                </div>
                <div class="setting-item">
                    <label data-i18n="tpRatio">Take Profit (R:R)</label>
                    <input type="number" id="newBotTpRatio" value="2" min="1" max="10" step="0.5">
                </div>
                <div class="setting-item">
                    <label data-i18n="slMode">Stop Loss Mode</label>
                    <select id="newBotSlMode">
                        <option value="supertrend_line" selected>SuperTrend Line</option>
                        <option value="fixed_percent">Fixed Percent</option>
                        <option value="atr">ATR</option>
                    </select>
                </div>
                <div class="setting-item">
                    <label data-i18n="maxPositions">Max Positions</label>
                    <input type="number" id="newBotMaxPositions" value="3" min="1" max="10">
                </div>
                <div class="setting-item">
                    <label data-i18n="emaFilter">EMA 200 Filter</label>
                    <select id="newBotEmaEnabled">
                        <option value="true" selected data-i18n="enabled">Enabled</option>
                        <option value="false" data-i18n="disabled">Disabled</option>
                    </select>
                </div>
            </div>
            <div class="settings-actions">
                <button class="btn btn-secondary" onclick="closeModal('createBotModal')" data-i18n="cancel">Cancel</button>
                <button class="btn btn-primary" onclick="createBot()" data-i18n="create">Create</button>
            </div>
        </div>
    </div>

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
                tradingPair: 'Trading Pair',
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
                logsCleared: '--- Logs cleared ---',
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
                start: 'Start',
                stop: 'Stop',
                delete: 'Delete',
                applyPairs: 'Apply Selected Pairs',
                noBots: 'No bots created yet. Click "Create Bot" to start.',
                botCreated: 'Bot created successfully!',
                botDeleted: 'Bot deleted successfully!'
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
                tradingPair: 'Торговая пара',
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
                logsCleared: '--- Логи очищены ---',
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
                start: 'Запуск',
                stop: 'Стоп',
                delete: 'Удалить',
                applyPairs: 'Применить выбранные пары',
                noBots: 'Ботов пока нет. Нажмите "Создать бот" для начала.',
                botCreated: 'Бот создан!',
                botDeleted: 'Бот удален!'
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
            if (tabName === 'bots') loadBots();
            if (tabName === 'charts') loadCharts();
            if (tabName === 'pairs') loadTradingPairs();
        }

        // Modal functions
        async function showCreateBotModal() {
            // Load trading pairs into dropdown
            try {
                const response = await fetch('/api/trading-pairs');
                const data = await response.json();
                const select = document.getElementById('newBotPair');
                select.innerHTML = '';

                if (data.pairs && data.pairs.length > 0) {
                    data.pairs.forEach(pair => {
                        const option = document.createElement('option');
                        option.value = pair.symbol;
                        option.textContent = pair.symbol;
                        select.appendChild(option);
                    });
                } else {
                    // Default pairs if API fails
                    ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'DOGEUSDT'].forEach(symbol => {
                        const option = document.createElement('option');
                        option.value = symbol;
                        option.textContent = symbol;
                        select.appendChild(option);
                    });
                }
            } catch (err) {
                console.error('Failed to load pairs for modal:', err);
            }

            document.getElementById('createBotModal').classList.add('show');
        }

        function closeModal(modalId) {
            document.getElementById(modalId).classList.remove('show');
        }

        // Bots Management
        let botsData = [];

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

        function renderBots() {
            const grid = document.getElementById('botsGrid');
            if (botsData.length === 0) {
                grid.innerHTML = '<p style="color: #888; text-align: center; padding: 40px;">' + t('noBots') + '</p>';
                return;
            }

            grid.innerHTML = botsData.map(bot => `
                <div class="bot-card ${bot.status === 'running' ? 'running' : ''}" style="min-width: 400px;">
                    <div class="bot-header">
                        <span class="bot-name">${bot.name}</span>
                        <span class="bot-status ${bot.status}">${bot.status === 'running' ? t('running') : t('stopped')}</span>
                    </div>
                    <div class="bot-details">
                        <div><strong>${t('tradingPair')}:</strong> ${Array.isArray(bot.trading_pairs) ? bot.trading_pairs[0] : bot.trading_pairs}</div>
                        <div><strong>${t('timeframe')}:</strong> ${bot.timeframe} | <strong>${t('leverage')}:</strong> ${bot.leverage}x</div>
                        <div><strong>R:R:</strong> ${bot.tp_risk_ratio} | <strong>${t('riskPerTrade')}:</strong> ${bot.risk_per_trade}%</div>
                        <div><strong>EMA200:</strong> ${bot.ema_enabled ? '✓' : '✗'} | <strong>SL:</strong> ${bot.sl_mode}</div>
                    </div>
                    ${bot.status === 'running' ? `
                        <div class="bot-chart-container" style="margin: 10px 0;">
                            <div class="chart-wrapper" id="bot-chart-${bot.id}" style="height: 200px;"></div>
                        </div>
                    ` : ''}
                    <div class="bot-actions">
                        ${bot.status === 'running' ?
                            `<button class="btn btn-danger" onclick="stopSpecificBot('${bot.id}')">${t('stop')}</button>` :
                            `<button class="btn btn-success" onclick="startSpecificBot('${bot.id}')">${t('start')}</button>`
                        }
                        <button class="btn btn-secondary" onclick="deleteBot('${bot.id}')" ${bot.status === 'running' ? 'disabled' : ''}>${t('delete')}</button>
                    </div>
                </div>
            `).join('');

            // Create charts for running bots
            botsData.filter(bot => bot.status === 'running').forEach(bot => {
                setTimeout(() => createBotChart(bot), 100);
            });
        }

        // Create chart for a specific bot with indicators
        async function createBotChart(bot) {
            const symbol = Array.isArray(bot.trading_pairs) ? bot.trading_pairs[0] : bot.trading_pairs;
            const chartContainer = document.getElementById('bot-chart-' + bot.id);
            if (!chartContainer || typeof LightweightCharts === 'undefined') return;

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

            // Load klines
            try {
                const klinesResponse = await fetch(`/api/klines/${symbol}?interval=${interval}&limit=100`);
                const klinesData = await klinesResponse.json();
                if (klinesData.klines && klinesData.klines.length > 0) {
                    candlestickSeries.setData(klinesData.klines);
                }
            } catch (err) {
                console.error('Failed to load klines for bot chart:', err);
            }

            // Load and add indicators
            try {
                const indResponse = await fetch(`/api/indicators/${symbol}?interval=${interval}&limit=200`);
                const indData = await indResponse.json();

                if (indData.indicators) {
                    // EMA 200 (if enabled)
                    if (bot.ema_enabled && indData.indicators.ema200 && indData.indicators.ema200.length > 0) {
                        const emaSeries = chart.addLineSeries({
                            color: '#ffcc00',
                            lineWidth: 2,
                            title: 'EMA200',
                        });
                        emaSeries.setData(indData.indicators.ema200);
                    }

                    // SuperTrend 1 (green/red)
                    if (indData.indicators.supertrend1 && indData.indicators.supertrend1.length > 0) {
                        const st1Series = chart.addLineSeries({
                            color: '#00ff88',
                            lineWidth: 1,
                            title: 'ST1',
                        });
                        st1Series.setData(indData.indicators.supertrend1.map(p => ({time: p.time, value: p.value})));
                    }

                    // SuperTrend 2 (cyan/orange)
                    if (indData.indicators.supertrend2 && indData.indicators.supertrend2.length > 0) {
                        const st2Series = chart.addLineSeries({
                            color: '#00d4ff',
                            lineWidth: 1,
                            title: 'ST2',
                        });
                        st2Series.setData(indData.indicators.supertrend2.map(p => ({time: p.time, value: p.value})));
                    }

                    // SuperTrend 3 (purple/pink)
                    if (indData.indicators.supertrend3 && indData.indicators.supertrend3.length > 0) {
                        const st3Series = chart.addLineSeries({
                            color: '#aa00ff',
                            lineWidth: 1,
                            title: 'ST3',
                        });
                        st3Series.setData(indData.indicators.supertrend3.map(p => ({time: p.time, value: p.value})));
                    }
                }
            } catch (err) {
                console.error('Failed to load indicators for bot chart:', err);
            }

            // Fit content
            chart.timeScale().fitContent();

            // Handle resize
            new ResizeObserver(() => {
                chart.applyOptions({ width: chartContainer.clientWidth });
            }).observe(chartContainer);
        }

        async function createBot() {
            const selectedPair = document.getElementById('newBotPair').value;
            const config = {
                name: document.getElementById('newBotName').value || 'Bot ' + (botsData.length + 1),
                trading_pairs: [selectedPair],
                timeframe: document.getElementById('newBotTimeframe').value,
                leverage: parseInt(document.getElementById('newBotLeverage').value),
                risk_per_trade: parseFloat(document.getElementById('newBotRisk').value),
                tp_risk_ratio: parseFloat(document.getElementById('newBotTpRatio').value),
                sl_mode: document.getElementById('newBotSlMode').value,
                max_positions: parseInt(document.getElementById('newBotMaxPositions').value),
                ema_enabled: document.getElementById('newBotEmaEnabled').value === 'true',
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
            }
        }

        async function startSpecificBot(botId) {
            try {
                const response = await fetch(`/api/bots/${botId}/start`, { method: 'POST' });
                const data = await response.json();
                if (data.success) {
                    showToast(t('botStarted'));
                    loadBots();
                } else {
                    showToast(data.message);
                }
            } catch (err) {
                console.error('Failed to start bot:', err);
            }
        }

        async function stopSpecificBot(botId) {
            try {
                const response = await fetch(`/api/bots/${botId}/stop`, { method: 'POST' });
                const data = await response.json();
                if (data.success) {
                    showToast(t('botStopped'));
                    loadBots();
                } else {
                    showToast(data.message);
                }
            } catch (err) {
                console.error('Failed to stop bot:', err);
            }
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
                        <span class="chart-symbol">${symbol}</span>
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
                const response = await fetch(`/api/klines/${symbol}?interval=${interval}&limit=100`);
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
                     onclick="togglePair('${pair.symbol}')">
                    ${pair.symbol}
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


# Trading pairs cache
trading_pairs_cache = {
    "pairs": [],
    "last_update": None,
}

# Multi-bot management (up to 10 bots)
MAX_BOTS = 10
bots_registry = {}  # bot_id -> bot_config


@app.get("/api/bots")
async def get_bots():
    """Get all configured bots."""
    return {"bots": list(bots_registry.values()), "max_bots": MAX_BOTS}


@app.post("/api/bots")
async def create_bot(config: dict):
    """Create a new bot configuration."""
    if len(bots_registry) >= MAX_BOTS:
        return {"success": False, "message": f"Maximum {MAX_BOTS} bots allowed"}

    import uuid
    bot_id = str(uuid.uuid4())[:8]

    bot_config = {
        "id": bot_id,
        "name": config.get("name", f"Bot {len(bots_registry) + 1}"),
        "trading_pairs": config.get("trading_pairs", ["BTCUSDT"]),
        "timeframe": config.get("timeframe", "15m"),
        "risk_per_trade": config.get("risk_per_trade", 2.0),
        "tp_risk_ratio": config.get("tp_risk_ratio", 2.0),
        "sl_mode": config.get("sl_mode", "supertrend_line"),
        "leverage": config.get("leverage", 10),
        "max_positions": config.get("max_positions", 3),
        "ema_enabled": config.get("ema_enabled", True),
        "status": "stopped",
        "created_at": datetime.now().isoformat(),
    }

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
    if "trading_pairs" in config:
        bot["trading_pairs"] = config["trading_pairs"]
    if "timeframe" in config:
        bot["timeframe"] = config["timeframe"]
    if "risk_per_trade" in config:
        bot["risk_per_trade"] = float(config["risk_per_trade"])
    if "tp_risk_ratio" in config:
        bot["tp_risk_ratio"] = float(config["tp_risk_ratio"])
    if "sl_mode" in config:
        bot["sl_mode"] = config["sl_mode"]
    if "leverage" in config:
        bot["leverage"] = int(config["leverage"])
    if "max_positions" in config:
        bot["max_positions"] = int(config["max_positions"])
    if "ema_enabled" in config:
        bot["ema_enabled"] = config["ema_enabled"]

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
    """Start a specific bot."""
    if bot_id not in bots_registry:
        return {"success": False, "message": "Bot not found"}

    bot = bots_registry[bot_id]
    if bot.get("status") == "running":
        return {"success": False, "message": "Bot is already running"}

    # Update runtime settings with this bot's config
    runtime_settings["trading_pairs"] = bot["trading_pairs"]
    runtime_settings["timeframe"] = bot["timeframe"]
    runtime_settings["risk_per_trade"] = bot["risk_per_trade"]
    runtime_settings["tp_risk_ratio"] = bot["tp_risk_ratio"]
    runtime_settings["sl_mode"] = bot["sl_mode"]
    runtime_settings["leverage"] = bot["leverage"]
    runtime_settings["max_open_positions"] = bot["max_positions"]
    runtime_settings["ema_enabled"] = bot["ema_enabled"]

    # Start the bot
    start_callback = bot_state.get("start_callback")
    if start_callback:
        try:
            await start_callback()
            bot["status"] = "running"
            return {"success": True, "message": f"Bot {bot['name']} started"}
        except Exception as e:
            return {"success": False, "message": str(e)}

    return {"success": False, "message": "Start callback not configured"}


@app.post("/api/bots/{bot_id}/stop")
async def stop_specific_bot(bot_id: str):
    """Stop a specific bot."""
    if bot_id not in bots_registry:
        return {"success": False, "message": "Bot not found"}

    bot = bots_registry[bot_id]
    if bot.get("status") != "running":
        return {"success": False, "message": "Bot is not running"}

    stop_callback = bot_state.get("stop_callback")
    if stop_callback:
        try:
            await stop_callback()
            bot["status"] = "stopped"
            return {"success": True, "message": f"Bot {bot['name']} stopped"}
        except Exception as e:
            return {"success": False, "message": str(e)}

    return {"success": False, "message": "Stop callback not configured"}


@app.get("/api/trading-pairs")
async def get_trading_pairs():
    """Get available trading pairs from exchange."""
    from datetime import datetime, timedelta

    # Return cached data if fresh (less than 5 minutes old)
    if trading_pairs_cache["pairs"] and trading_pairs_cache["last_update"]:
        if datetime.now() - trading_pairs_cache["last_update"] < timedelta(minutes=5):
            return {"pairs": trading_pairs_cache["pairs"]}

    client = bot_state.get("client")
    if not client:
        # Return default pairs if no client
        return {"pairs": [
            {"symbol": "BTCUSDT", "base": "BTC", "quote": "USDT"},
            {"symbol": "ETHUSDT", "base": "ETH", "quote": "USDT"},
            {"symbol": "SOLUSDT", "base": "SOL", "quote": "USDT"},
            {"symbol": "XRPUSDT", "base": "XRP", "quote": "USDT"},
            {"symbol": "DOGEUSDT", "base": "DOGE", "quote": "USDT"},
            {"symbol": "ADAUSDT", "base": "ADA", "quote": "USDT"},
            {"symbol": "AVAXUSDT", "base": "AVAX", "quote": "USDT"},
            {"symbol": "LINKUSDT", "base": "LINK", "quote": "USDT"},
            {"symbol": "MATICUSDT", "base": "MATIC", "quote": "USDT"},
            {"symbol": "LTCUSDT", "base": "LTC", "quote": "USDT"},
        ]}

    try:
        pairs = client.get_trading_pairs()
        result = []
        for pair in pairs:
            if pair.quote_asset == "USDT" and pair.status == "Trading":
                result.append({
                    "symbol": pair.symbol,
                    "base": pair.base_asset,
                    "quote": pair.quote_asset,
                    "minQty": str(pair.min_order_qty),
                    "maxLeverage": pair.max_leverage,
                })

        # Sort by symbol
        result.sort(key=lambda x: x["symbol"])

        # Cache the result
        trading_pairs_cache["pairs"] = result
        trading_pairs_cache["last_update"] = datetime.now()

        return {"pairs": result}
    except Exception as e:
        logger.error("Failed to get trading pairs", error=str(e))
        return {"pairs": [], "error": str(e)}


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

            if pd.notna(st1.loc[idx]):
                result["supertrend1"].append({
                    "time": timestamp,
                    "value": float(st1.loc[idx]),
                    "color": "#00ff88" if dir1.loc[idx] == 1 else "#ff4444"
                })

            if pd.notna(st2.loc[idx]):
                result["supertrend2"].append({
                    "time": timestamp,
                    "value": float(st2.loc[idx]),
                    "color": "#00d4ff" if dir2.loc[idx] == 1 else "#ff8800"
                })

            if pd.notna(st3.loc[idx]):
                result["supertrend3"].append({
                    "time": timestamp,
                    "value": float(st3.loc[idx]),
                    "color": "#aa00ff" if dir3.loc[idx] == 1 else "#ff0088"
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
