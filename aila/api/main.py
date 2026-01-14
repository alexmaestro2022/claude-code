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
                <div class="card">
                    <div class="card-title" data-i18n="activeBots">Active Bots</div>
                    <div class="card-value" id="activeBots">0</div>
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
                    <label data-i18n="botMode">Bot Mode</label>
                    <select id="newBotMode" onchange="toggleBotMode('new')">
                        <option value="manual" selected data-i18n="manualMode">Manual (Single Pair)</option>
                        <option value="auto_search" data-i18n="autoSearchMode">Auto Search (All Pairs)</option>
                    </select>
                </div>
                <div class="setting-item" id="newPairContainer">
                    <label data-i18n="tradingPair">Trading Pair</label>
                    <div class="pair-search-container" style="position: relative; display: flex; align-items: center;">
                        <input type="text" id="newBotPairSearch" placeholder="Search pair... (e.g. BTC, ETH)"
                               oninput="filterTradingPairs('new')" onfocus="showPairDropdown('new')"
                               autocomplete="off" style="width: 100%; padding-right: 25px;">
                        <span onclick="clearPairSearch('new')" style="position: absolute; right: 10px; cursor: pointer; color: #888; font-size: 16px; font-weight: bold;">&times;</span>
                        <div id="newPairDropdown" class="pair-dropdown" style="display: none; position: absolute; top: 100%; left: 0; right: 0; max-height: 250px; overflow-y: auto; background: #1a1a2e; border: 1px solid #333; border-radius: 5px; z-index: 1000;">
                        </div>
                        <input type="hidden" id="newBotPair" value="BTCUSDT">
                    </div>
                </div>
                <div class="setting-item" id="newMaxOrdersContainer" style="display: none;">
                    <label data-i18n="maxSimultaneousOrders">Max Simultaneous Orders</label>
                    <input type="number" id="newBotMaxOrders" value="3" min="1" max="20">
                    <div style="margin-top: 5px; font-size: 11px; color: #888;" data-i18n="autoSearchHint">
                        Bot will scan all pairs and open orders when strategy conditions match
                    </div>
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
                        <option value="6h">6h</option>
                        <option value="12h">12h</option>
                        <option value="1d">1d</option>
                        <option value="1w">1w</option>
                    </select>
                </div>
                <div class="setting-item" id="newPairInfoContainer" style="display: none;">
                    <label data-i18n="pairInfo">Pair Info</label>
                    <div id="newPairInfo" style="padding: 8px; background: rgba(0,255,136,0.1); border-radius: 5px; font-size: 12px;">
                        <div><span data-i18n="minOrder">Min Order:</span> <span id="newPairMinOrder">-</span> USDT</div>
                        <div><span data-i18n="maxLeverage">Max Leverage:</span> <span id="newPairMaxLeverage">-</span>x</div>
                    </div>
                </div>
                <div class="setting-item">
                    <label data-i18n="leverage">Leverage</label>
                    <select id="newBotLeverage" onchange="updateDepositInfo('new')">
                        <option value="1">1x</option>
                        <option value="2">2x</option>
                        <option value="3">3x</option>
                        <option value="5">5x</option>
                        <option value="10" selected>10x</option>
                        <option value="15">15x</option>
                        <option value="20">20x</option>
                        <option value="25">25x</option>
                        <option value="50">50x</option>
                        <option value="75">75x</option>
                        <option value="100">100x</option>
                    </select>
                </div>
                <div class="setting-item">
                    <label data-i18n="orderSize">Order Size (USDT)</label>
                    <input type="number" id="newBotOrderSize" value="100" min="5" max="100000" step="1" oninput="updateDepositInfo('new')">
                    <div id="newDepositInfo" style="margin-top: 5px; padding: 5px; background: rgba(255,204,0,0.1); border-radius: 3px; font-size: 11px; color: #ffcc00;">
                        <span data-i18n="depositUsed">Deposit used:</span> <span id="newDepositUsed">10</span> USDT
                    </div>
                </div>
                <div class="setting-item">
                    <label data-i18n="positionSizingMode">Position Sizing Mode</label>
                    <select id="newBotPositionSizingMode" onchange="togglePositionSizingOptions('new')">
                        <option value="fixed_amount" selected data-i18n="fixedAmount">Fixed Amount</option>
                        <option value="risk_percent" data-i18n="riskPercent">Risk Percent</option>
                        <option value="kelly" data-i18n="kellyCriterion">Kelly Criterion</option>
                    </select>
                </div>
                <div class="setting-item" id="newRiskPercentContainer" style="display: none;">
                    <label data-i18n="riskPerTrade">Risk per Trade (%)</label>
                    <input type="number" id="newBotRisk" value="2" min="0.1" max="10" step="0.1">
                </div>
                <div class="setting-item">
                    <label data-i18n="tpMode">Take Profit Mode</label>
                    <select id="newBotTpMode" onchange="toggleTpOptions('new')">
                        <option value="risk_ratio" selected data-i18n="riskRatio">Risk:Reward Ratio</option>
                        <option value="fixed_percent" data-i18n="fixedPercent">Fixed Percent</option>
                    </select>
                </div>
                <div class="setting-item" id="newTpRatioContainer">
                    <label data-i18n="tpRatio">Take Profit (R:R)</label>
                    <input type="number" id="newBotTpRatio" value="2" min="1" max="10" step="0.5">
                </div>
                <div class="setting-item" id="newTpPercentContainer" style="display: none;">
                    <label data-i18n="tpPercent">Take Profit (%)</label>
                    <input type="number" id="newBotTpPercent" value="4" min="0.5" max="20" step="0.5">
                </div>
                <div class="setting-item">
                    <label data-i18n="slMode">Stop Loss Mode</label>
                    <select id="newBotSlMode" onchange="toggleSlOptions('new')">
                        <option value="supertrend_line" selected>SuperTrend Line</option>
                        <option value="fixed_percent">Fixed Percent</option>
                        <option value="atr">ATR</option>
                    </select>
                </div>
                <div class="setting-item" id="newSlLineContainer">
                    <label data-i18n="slLine">SL SuperTrend Line</label>
                    <select id="newBotSlLine">
                        <option value="1">Line 1 (Fast)</option>
                        <option value="2" selected>Line 2 (Medium)</option>
                        <option value="3">Line 3 (Slow)</option>
                    </select>
                </div>
                <div class="setting-item" id="newSlPercentContainer" style="display: none;">
                    <label data-i18n="slPercent">SL Fixed Percent (%)</label>
                    <input type="number" id="newBotSlPercent" value="2" min="0.5" max="10" step="0.5">
                </div>
                <div class="setting-item" id="newSlAtrContainer" style="display: none;">
                    <label data-i18n="slAtrMult">SL ATR Multiplier</label>
                    <input type="number" id="newBotSlAtrMult" value="1.5" min="0.5" max="5" step="0.1">
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
                <div class="setting-item">
                    <label data-i18n="emaMode">EMA Filter Mode</label>
                    <select id="newBotEmaMode">
                        <option value="strict" selected data-i18n="strict">Strict</option>
                        <option value="soft" data-i18n="soft">Soft (50% size)</option>
                    </select>
                </div>
                <div class="setting-item">
                    <label data-i18n="leverageMode">Leverage Mode</label>
                    <select id="newBotLeverageMode">
                        <option value="cross" selected data-i18n="crossMargin">Cross Margin</option>
                        <option value="isolated" data-i18n="isolatedMargin">Isolated Margin</option>
                    </select>
                </div>
                <div class="setting-item">
                    <label data-i18n="trailingStop">Trailing Stop</label>
                    <select id="newBotTrailingEnabled" onchange="toggleTrailingOptions('new')">
                        <option value="true" selected data-i18n="enabled">Enabled</option>
                        <option value="false" data-i18n="disabled">Disabled</option>
                    </select>
                </div>
                <div id="newTrailingOptionsContainer">
                    <div class="setting-item">
                        <label data-i18n="trailingMode">Trailing Mode</label>
                        <select id="newBotTrailingMode" onchange="toggleTrailingModeOptions('new')">
                            <option value="supertrend" selected data-i18n="superTrendBased">SuperTrend Based</option>
                            <option value="percent" data-i18n="percentBased">Percent Based</option>
                        </select>
                    </div>
                    <div class="setting-item">
                        <label data-i18n="trailingActivation">Trailing Activation (%)</label>
                        <input type="number" id="newBotTrailingActivation" value="1.0" min="0.1" max="10" step="0.1">
                    </div>
                    <div class="setting-item" id="newTrailingStepContainer" style="display: none;">
                        <label data-i18n="trailingStep">Trailing Step (%)</label>
                        <input type="number" id="newBotTrailingStep" value="0.5" min="0.1" max="5" step="0.1">
                    </div>
                </div>
                <div class="setting-item">
                    <label data-i18n="breakeven">Break-even</label>
                    <select id="newBotBreakevenEnabled" onchange="toggleBreakevenOptions('new')">
                        <option value="false" selected data-i18n="disabled">Disabled</option>
                        <option value="true" data-i18n="enabled">Enabled</option>
                    </select>
                </div>
                <div id="newBreakevenOptionsContainer" style="display: none;">
                    <div class="setting-item">
                        <label data-i18n="breakevenActivation">Break-even Activation (%)</label>
                        <input type="number" id="newBotBreakevenActivation" value="1.0" min="0.5" max="5" step="0.1">
                    </div>
                    <div class="setting-item">
                        <label data-i18n="breakevenOffset">Break-even Offset (%)</label>
                        <input type="number" id="newBotBreakevenOffset" value="0.1" min="0" max="1" step="0.05">
                    </div>
                </div>
            </div>
            <div class="settings-actions">
                <button class="btn btn-secondary" onclick="closeModal('createBotModal')" data-i18n="cancel">Cancel</button>
                <button class="btn btn-primary" onclick="createBot()" data-i18n="create">Create</button>
            </div>
        </div>
    </div>

    <!-- Edit Bot Modal -->
    <div class="modal" id="editBotModal">
        <div class="modal-content" style="max-width: 700px;">
            <div class="modal-header">
                <h3 data-i18n="editBot">Edit Bot</h3>
                <button class="modal-close" onclick="closeModal('editBotModal')">&times;</button>
            </div>
            <input type="hidden" id="editBotId">
            <div class="settings-grid">
                <div class="setting-item">
                    <label data-i18n="botName">Bot Name</label>
                    <input type="text" id="editBotName">
                </div>
                <div class="setting-item">
                    <label data-i18n="botMode">Bot Mode</label>
                    <select id="editBotMode" onchange="toggleBotMode('edit')">
                        <option value="manual" data-i18n="manualMode">Manual (Single Pair)</option>
                        <option value="auto_search" data-i18n="autoSearchMode">Auto Search (All Pairs)</option>
                    </select>
                </div>
                <div class="setting-item" id="editPairContainer">
                    <label data-i18n="tradingPair">Trading Pair</label>
                    <div class="pair-search-container" style="position: relative; display: flex; align-items: center;">
                        <input type="text" id="editBotPairSearch" placeholder="Search pair... (e.g. BTC, ETH)"
                               oninput="filterTradingPairs('edit')" onfocus="showPairDropdown('edit')"
                               autocomplete="off" style="width: 100%; padding-right: 25px;">
                        <span onclick="clearPairSearch('edit')" style="position: absolute; right: 10px; cursor: pointer; color: #888; font-size: 16px; font-weight: bold;">&times;</span>
                        <div id="editPairDropdown" class="pair-dropdown" style="display: none; position: absolute; top: 100%; left: 0; right: 0; max-height: 250px; overflow-y: auto; background: #1a1a2e; border: 1px solid #333; border-radius: 5px; z-index: 1000;">
                        </div>
                        <input type="hidden" id="editBotPair" value="">
                    </div>
                </div>
                <div class="setting-item" id="editMaxOrdersContainer" style="display: none;">
                    <label data-i18n="maxSimultaneousOrders">Max Simultaneous Orders</label>
                    <input type="number" id="editBotMaxOrders" value="3" min="1" max="20">
                    <div style="margin-top: 5px; font-size: 11px; color: #888;" data-i18n="autoSearchHint">
                        Bot will scan all pairs and open orders when strategy conditions match
                    </div>
                </div>
                <div class="setting-item">
                    <label data-i18n="timeframe">Timeframe</label>
                    <select id="editBotTimeframe">
                        <option value="1m">1m</option>
                        <option value="3m">3m</option>
                        <option value="5m">5m</option>
                        <option value="15m">15m</option>
                        <option value="30m">30m</option>
                        <option value="1h">1h</option>
                        <option value="2h">2h</option>
                        <option value="4h">4h</option>
                        <option value="6h">6h</option>
                        <option value="12h">12h</option>
                        <option value="1d">1d</option>
                        <option value="1w">1w</option>
                    </select>
                </div>
                <div class="setting-item" id="editPairInfoContainer" style="display: none;">
                    <label data-i18n="pairInfo">Pair Info</label>
                    <div id="editPairInfo" style="padding: 8px; background: rgba(0,255,136,0.1); border-radius: 5px; font-size: 12px;">
                        <div><span data-i18n="minOrder">Min Order:</span> <span id="editPairMinOrder">-</span> USDT</div>
                        <div><span data-i18n="maxLeverage">Max Leverage:</span> <span id="editPairMaxLeverage">-</span>x</div>
                    </div>
                </div>
                <div class="setting-item">
                    <label data-i18n="leverage">Leverage</label>
                    <select id="editBotLeverage" onchange="updateDepositInfo('edit')">
                        <option value="1">1x</option>
                        <option value="2">2x</option>
                        <option value="3">3x</option>
                        <option value="5">5x</option>
                        <option value="10">10x</option>
                        <option value="15">15x</option>
                        <option value="20">20x</option>
                        <option value="25">25x</option>
                        <option value="50">50x</option>
                        <option value="75">75x</option>
                        <option value="100">100x</option>
                    </select>
                </div>
                <div class="setting-item">
                    <label data-i18n="orderSize">Order Size (USDT)</label>
                    <input type="number" id="editBotOrderSize" value="100" min="5" max="100000" step="1" oninput="updateDepositInfo('edit')">
                    <div id="editDepositInfo" style="margin-top: 5px; padding: 5px; background: rgba(255,204,0,0.1); border-radius: 3px; font-size: 11px; color: #ffcc00;">
                        <span data-i18n="depositUsed">Deposit used:</span> <span id="editDepositUsed">10</span> USDT
                    </div>
                </div>
                <div class="setting-item">
                    <label data-i18n="positionSizingMode">Position Sizing Mode</label>
                    <select id="editBotPositionSizingMode" onchange="togglePositionSizingOptions('edit')">
                        <option value="fixed_amount" data-i18n="fixedAmount">Fixed Amount</option>
                        <option value="risk_percent" data-i18n="riskPercent">Risk Percent</option>
                        <option value="kelly" data-i18n="kellyCriterion">Kelly Criterion</option>
                    </select>
                </div>
                <div class="setting-item" id="editRiskPercentContainer" style="display: none;">
                    <label data-i18n="riskPerTrade">Risk per Trade (%)</label>
                    <input type="number" id="editBotRisk" min="0.1" max="100" step="0.1">
                </div>
                <div class="setting-item">
                    <label data-i18n="tpMode">Take Profit Mode</label>
                    <select id="editBotTpMode" onchange="toggleTpOptions('edit')">
                        <option value="risk_ratio" data-i18n="riskRatio">Risk:Reward Ratio</option>
                        <option value="fixed_percent" data-i18n="fixedPercent">Fixed Percent</option>
                    </select>
                </div>
                <div class="setting-item" id="editTpRatioContainer">
                    <label data-i18n="tpRatio">Take Profit (R:R)</label>
                    <input type="number" id="editBotTpRatio" min="1" max="10" step="0.5">
                </div>
                <div class="setting-item" id="editTpPercentContainer" style="display: none;">
                    <label data-i18n="tpPercent">Take Profit (%)</label>
                    <input type="number" id="editBotTpPercent" value="4" min="0.5" max="20" step="0.5">
                </div>
                <div class="setting-item">
                    <label data-i18n="slMode">Stop Loss Mode</label>
                    <select id="editBotSlMode" onchange="toggleSlOptions('edit')">
                        <option value="supertrend_line">SuperTrend Line</option>
                        <option value="fixed_percent">Fixed Percent</option>
                        <option value="atr">ATR</option>
                    </select>
                </div>
                <div class="setting-item" id="editSlLineContainer">
                    <label data-i18n="slLine">SL SuperTrend Line</label>
                    <select id="editBotSlLine">
                        <option value="1">Line 1 (Fast)</option>
                        <option value="2">Line 2 (Medium)</option>
                        <option value="3">Line 3 (Slow)</option>
                    </select>
                </div>
                <div class="setting-item" id="editSlPercentContainer" style="display: none;">
                    <label data-i18n="slPercent">SL Fixed Percent (%)</label>
                    <input type="number" id="editBotSlPercent" value="2" min="0.5" max="10" step="0.5">
                </div>
                <div class="setting-item" id="editSlAtrContainer" style="display: none;">
                    <label data-i18n="slAtrMult">SL ATR Multiplier</label>
                    <input type="number" id="editBotSlAtrMult" value="1.5" min="0.5" max="5" step="0.1">
                </div>
                <div class="setting-item">
                    <label data-i18n="maxPositions">Max Positions</label>
                    <input type="number" id="editBotMaxPositions" min="1" max="10">
                </div>
                <div class="setting-item">
                    <label data-i18n="emaFilter">EMA 200 Filter</label>
                    <select id="editBotEmaEnabled">
                        <option value="true" data-i18n="enabled">Enabled</option>
                        <option value="false" data-i18n="disabled">Disabled</option>
                    </select>
                </div>
                <div class="setting-item">
                    <label data-i18n="emaMode">EMA Filter Mode</label>
                    <select id="editBotEmaMode">
                        <option value="strict" data-i18n="strict">Strict</option>
                        <option value="soft" data-i18n="soft">Soft (50% size)</option>
                    </select>
                </div>
                <div class="setting-item">
                    <label data-i18n="leverageMode">Leverage Mode</label>
                    <select id="editBotLeverageMode">
                        <option value="cross" data-i18n="crossMargin">Cross Margin</option>
                        <option value="isolated" data-i18n="isolatedMargin">Isolated Margin</option>
                    </select>
                </div>
                <div class="setting-item">
                    <label data-i18n="trailingStop">Trailing Stop</label>
                    <select id="editBotTrailingEnabled" onchange="toggleTrailingOptions('edit')">
                        <option value="true" data-i18n="enabled">Enabled</option>
                        <option value="false" data-i18n="disabled">Disabled</option>
                    </select>
                </div>
                <div id="editTrailingOptionsContainer">
                    <div class="setting-item">
                        <label data-i18n="trailingMode">Trailing Mode</label>
                        <select id="editBotTrailingMode" onchange="toggleTrailingModeOptions('edit')">
                            <option value="supertrend" data-i18n="superTrendBased">SuperTrend Based</option>
                            <option value="percent" data-i18n="percentBased">Percent Based</option>
                        </select>
                    </div>
                    <div class="setting-item">
                        <label data-i18n="trailingActivation">Trailing Activation (%)</label>
                        <input type="number" id="editBotTrailingActivation" value="1.0" min="0.1" max="10" step="0.1">
                    </div>
                    <div class="setting-item" id="editTrailingStepContainer" style="display: none;">
                        <label data-i18n="trailingStep">Trailing Step (%)</label>
                        <input type="number" id="editBotTrailingStep" value="0.5" min="0.1" max="5" step="0.1">
                    </div>
                </div>
                <div class="setting-item">
                    <label data-i18n="breakeven">Break-even</label>
                    <select id="editBotBreakevenEnabled" onchange="toggleBreakevenOptions('edit')">
                        <option value="false" data-i18n="disabled">Disabled</option>
                        <option value="true" data-i18n="enabled">Enabled</option>
                    </select>
                </div>
                <div id="editBreakevenOptionsContainer" style="display: none;">
                    <div class="setting-item">
                        <label data-i18n="breakevenActivation">Break-even Activation (%)</label>
                        <input type="number" id="editBotBreakevenActivation" value="1.0" min="0.5" max="5" step="0.1">
                    </div>
                    <div class="setting-item">
                        <label data-i18n="breakevenOffset">Break-even Offset (%)</label>
                        <input type="number" id="editBotBreakevenOffset" value="0.1" min="0" max="1" step="0.05">
                    </div>
                </div>
            </div>
            <div class="settings-actions">
                <button class="btn btn-secondary" onclick="closeModal('editBotModal')" data-i18n="cancel">Cancel</button>
                <button class="btn btn-primary" onclick="saveEditBot()" data-i18n="save">Save</button>
            </div>
        </div>
    </div>

    <!-- Fullscreen Chart Modal -->
    <div class="modal" id="fullscreenChartModal" style="background: rgba(0,0,0,0.95);">
        <div class="modal-content" style="max-width: 95%; width: 95%; height: 90vh; padding: 15px;">
            <div class="modal-header" style="margin-bottom: 10px;">
                <h3 id="fullscreenChartTitle">Chart</h3>
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
                slLine: 'SL SuperTrend Line',
                slPercent: 'SL Fixed Percent (%)',
                slAtrMult: 'SL ATR Multiplier',
                leverage: 'Leverage',
                orderSize: 'Order Size (USDT)',
                depositUsed: 'Deposit used:',
                pairInfo: 'Pair Info',
                minOrder: 'Min Order:',
                maxLeverage: 'Max Leverage:',
                maxPositions: 'Max Open Positions',
                emaFilter: 'EMA Filter',
                emaMode: 'EMA Filter Mode',
                strict: 'Strict',
                soft: 'Soft (50% size)',
                trailingStop: 'Trailing Stop',
                trailingMode: 'Trailing Mode',
                trailingActivation: 'Trailing Activation (%)',
                trailingStep: 'Trailing Step (%)',
                superTrendBased: 'SuperTrend Based',
                percentBased: 'Percent Based',
                positionSizingMode: 'Position Sizing Mode',
                fixedAmount: 'Fixed Amount',
                riskPercent: 'Risk Percent',
                kellyCriterion: 'Kelly Criterion',
                tpMode: 'Take Profit Mode',
                riskRatio: 'Risk:Reward Ratio',
                tpPercent: 'Take Profit (%)',
                leverageMode: 'Leverage Mode',
                crossMargin: 'Cross Margin',
                isolatedMargin: 'Isolated Margin',
                breakeven: 'Break-even',
                breakevenActivation: 'Break-even Activation (%)',
                breakevenOffset: 'Break-even Offset (%)',
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
                maxSimultaneousOrders: 'Max Simultaneous Orders',
                autoSearchHint: 'Bot will scan all pairs and open orders when strategy conditions match'
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
                slLine: 'Линия SuperTrend для SL',
                slPercent: 'SL фикс. процент (%)',
                slAtrMult: 'SL ATR множитель',
                leverage: 'Плечо',
                orderSize: 'Размер ордера (USDT)',
                depositUsed: 'Используется депозит:',
                pairInfo: 'Информация о паре',
                minOrder: 'Мин. ордер:',
                maxLeverage: 'Макс. плечо:',
                maxPositions: 'Макс. позиций',
                emaFilter: 'EMA фильтр',
                emaMode: 'Режим EMA фильтра',
                strict: 'Строгий',
                soft: 'Мягкий (50% размер)',
                trailingStop: 'Трейлинг-стоп',
                trailingMode: 'Режим трейлинга',
                trailingActivation: 'Активация трейлинга (%)',
                trailingStep: 'Шаг трейлинга (%)',
                superTrendBased: 'По SuperTrend',
                percentBased: 'По проценту',
                positionSizingMode: 'Режим размера позиции',
                fixedAmount: 'Фикс. сумма',
                riskPercent: 'Процент риска',
                kellyCriterion: 'Критерий Келли',
                tpMode: 'Режим Take Profit',
                riskRatio: 'Соотношение R:R',
                tpPercent: 'Take Profit (%)',
                leverageMode: 'Режим маржи',
                crossMargin: 'Кросс-маржа',
                isolatedMargin: 'Изолир. маржа',
                breakeven: 'Безубыток',
                breakevenActivation: 'Активация безубытка (%)',
                breakevenOffset: 'Отступ безубытка (%)',
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
                maxSimultaneousOrders: 'Макс. одновременных ордеров',
                autoSearchHint: 'Бот сканирует все пары и открывает ордера при совпадении условий стратегии'
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
        let allTradingPairs = [];

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

        // Load pairs on page load (only once)
        if (allTradingPairs.length === 0) {
            loadTradingPairsCache();
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
                item.style.cssText = 'padding: 8px 12px; cursor: pointer; border-bottom: 1px solid #333;';
                item.innerHTML = `<strong>${pair.symbol}</strong>`;
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

            // Show pair info container
            const container = document.getElementById(prefix + 'PairInfoContainer');
            if (container) container.style.display = 'block';

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

            document.getElementById('createBotModal').classList.add('show');
        }

        function closeModal(modalId) {
            document.getElementById(modalId).classList.remove('show');
            // Hide dropdowns
            const dropdowns = document.querySelectorAll('.pair-dropdown');
            dropdowns.forEach(d => d.style.display = 'none');
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

            grid.innerHTML = botsData.map(bot => `
                <div class="bot-card ${bot.status === 'running' ? 'running' : ''}" style="min-width: 400px;" data-bot-id="${bot.id}">
                    <div class="bot-header">
                        <span class="bot-name">${bot.name}</span>
                        <span class="bot-status ${bot.status}">${bot.status === 'running' ? t('running') : t('stopped')}</span>
                    </div>
                    <div class="bot-details">
                        <div><strong>${t('botMode')}:</strong> ${bot.bot_mode === 'auto_search' ? '<span style="color: #ffcc00;">' + t('autoSearchMode') + '</span>' : t('manualMode')}</div>
                        <div><strong>${bot.bot_mode === 'auto_search' ? t('maxSimultaneousOrders') : t('tradingPair')}:</strong> ${bot.bot_mode === 'auto_search' ? bot.max_simultaneous_orders || 3 : (Array.isArray(bot.trading_pairs) ? bot.trading_pairs[0] : bot.trading_pairs)}</div>
                        <div><strong>${t('timeframe')}:</strong> ${bot.timeframe} | <strong>${t('leverage')}:</strong> ${bot.leverage}x</div>
                        <div><strong>R:R:</strong> ${bot.tp_risk_ratio} | <strong>${t('riskPerTrade')}:</strong> ${bot.risk_per_trade}%</div>
                        <div><strong>EMA200:</strong> ${bot.ema_enabled ? '✓' : '✗'} | <strong>SL:</strong> ${bot.sl_mode}</div>
                    </div>
                    ${bot.status === 'running' ? `
                        <div class="bot-chart-container" style="margin: 10px 0; cursor: pointer;" ondblclick="openFullscreenChart('${bot.id}')" title="${t('doubleClickChart')}">
                            <div class="chart-wrapper" id="bot-chart-${bot.id}" style="height: 200px;"></div>
                            <div style="display: flex; justify-content: space-between; align-items: center; font-size: 11px; color: #666; margin-top: 5px;">
                                <span>${t('doubleClickChart')}</span>
                                <span class="bot-countdown" data-timeframe="${bot.timeframe}" style="color: #ffcc00; font-weight: bold;">--:--</span>
                                <span class="update-time" style="color: #00ff88;">Loading...</span>
                            </div>
                        </div>
                    ` : ''}
                    <div class="bot-actions">
                        ${bot.status === 'running' ?
                            `<button class="btn btn-danger" onclick="stopSpecificBot('${bot.id}')">${t('stop')}</button>` :
                            `<button class="btn btn-success" onclick="startSpecificBot('${bot.id}')">${t('start')}</button>`
                        }
                        <button class="btn btn-primary" onclick="showEditBotModal('${bot.id}')" ${bot.status === 'running' ? 'disabled' : ''}>${t('edit')}</button>
                        <button class="btn btn-secondary" onclick="deleteBot('${bot.id}')" ${bot.status === 'running' ? 'disabled' : ''}>${t('delete')}</button>
                    </div>
                </div>
            `).join('');

            // Create charts for running bots
            botsData.filter(bot => bot.status === 'running').forEach(bot => {
                setTimeout(() => createBotChart(bot), 100);
            });
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
            const maxOrdersContainer = document.getElementById(prefix + 'MaxOrdersContainer');
            const pairInfoContainer = document.getElementById(prefix + 'PairInfoContainer');

            if (mode === 'auto_search') {
                if (pairContainer) pairContainer.style.display = 'none';
                if (pairInfoContainer) pairInfoContainer.style.display = 'none';
                if (maxOrdersContainer) maxOrdersContainer.style.display = 'block';
            } else {
                if (pairContainer) pairContainer.style.display = 'block';
                if (maxOrdersContainer) maxOrdersContainer.style.display = 'none';
            }
        }

        // Toggle SL options based on selected mode
        function toggleSlOptions(prefix) {
            const slMode = document.getElementById(prefix + 'BotSlMode').value;
            const lineContainer = document.getElementById(prefix + 'SlLineContainer');
            const percentContainer = document.getElementById(prefix + 'SlPercentContainer');
            const atrContainer = document.getElementById(prefix + 'SlAtrContainer');

            lineContainer.style.display = slMode === 'supertrend_line' ? 'block' : 'none';
            percentContainer.style.display = slMode === 'fixed_percent' ? 'block' : 'none';
            atrContainer.style.display = slMode === 'atr' ? 'block' : 'none';
        }

        // Toggle Position Sizing options based on selected mode
        function togglePositionSizingOptions(prefix) {
            const mode = document.getElementById(prefix + 'BotPositionSizingMode').value;
            const riskContainer = document.getElementById(prefix + 'RiskPercentContainer');
            const orderSizeItem = document.getElementById(prefix + 'BotOrderSize');

            // Show order size for fixed_amount, hide for risk-based modes
            if (orderSizeItem) {
                orderSizeItem.parentElement.style.display = mode === 'fixed_amount' ? 'block' : 'none';
            }
            // Show risk percent for risk_percent and kelly modes
            if (riskContainer) {
                riskContainer.style.display = (mode === 'risk_percent' || mode === 'kelly') ? 'block' : 'none';
            }
        }

        // Toggle TP options based on selected mode
        function toggleTpOptions(prefix) {
            const tpMode = document.getElementById(prefix + 'BotTpMode').value;
            const ratioContainer = document.getElementById(prefix + 'TpRatioContainer');
            const percentContainer = document.getElementById(prefix + 'TpPercentContainer');

            if (ratioContainer) ratioContainer.style.display = tpMode === 'risk_ratio' ? 'block' : 'none';
            if (percentContainer) percentContainer.style.display = tpMode === 'fixed_percent' ? 'block' : 'none';
        }

        // Toggle Trailing options based on enabled/disabled
        function toggleTrailingOptions(prefix) {
            const enabled = document.getElementById(prefix + 'BotTrailingEnabled').value === 'true';
            const optionsContainer = document.getElementById(prefix + 'TrailingOptionsContainer');

            if (optionsContainer) optionsContainer.style.display = enabled ? 'block' : 'none';
        }

        // Toggle Trailing Mode options (step only for percent mode)
        function toggleTrailingModeOptions(prefix) {
            const mode = document.getElementById(prefix + 'BotTrailingMode').value;
            const stepContainer = document.getElementById(prefix + 'TrailingStepContainer');

            if (stepContainer) stepContainer.style.display = mode === 'percent' ? 'block' : 'none';
        }

        // Toggle Break-even options based on enabled/disabled
        function toggleBreakevenOptions(prefix) {
            const enabled = document.getElementById(prefix + 'BotBreakevenEnabled').value === 'true';
            const optionsContainer = document.getElementById(prefix + 'BreakevenOptionsContainer');

            if (optionsContainer) optionsContainer.style.display = enabled ? 'block' : 'none';
        }

        async function createBot() {
            const botMode = document.getElementById('newBotMode').value;
            const selectedPair = document.getElementById('newBotPair').value;
            const slMode = document.getElementById('newBotSlMode').value;
            const config = {
                name: document.getElementById('newBotName').value || 'Bot ' + (botsData.length + 1),
                bot_mode: botMode,
                trading_pairs: botMode === 'auto_search' ? [] : [selectedPair],
                max_simultaneous_orders: parseInt(document.getElementById('newBotMaxOrders').value) || 3,
                timeframe: document.getElementById('newBotTimeframe').value,
                leverage: parseInt(document.getElementById('newBotLeverage').value),
                leverage_mode: document.getElementById('newBotLeverageMode').value,
                order_size: parseFloat(document.getElementById('newBotOrderSize').value),
                position_sizing_mode: document.getElementById('newBotPositionSizingMode').value,
                risk_per_trade: parseFloat(document.getElementById('newBotRisk').value),
                tp_mode: document.getElementById('newBotTpMode').value,
                tp_risk_ratio: parseFloat(document.getElementById('newBotTpRatio').value),
                tp_fixed_percent: parseFloat(document.getElementById('newBotTpPercent').value),
                sl_mode: slMode,
                sl_supertrend_line: parseInt(document.getElementById('newBotSlLine').value),
                sl_fixed_percent: parseFloat(document.getElementById('newBotSlPercent').value),
                sl_atr_multiplier: parseFloat(document.getElementById('newBotSlAtrMult').value),
                max_positions: parseInt(document.getElementById('newBotMaxPositions').value),
                ema_enabled: document.getElementById('newBotEmaEnabled').value === 'true',
                ema_filter_mode: document.getElementById('newBotEmaMode').value,
                trailing_enabled: document.getElementById('newBotTrailingEnabled').value === 'true',
                trailing_mode: document.getElementById('newBotTrailingMode').value,
                trailing_activation: parseFloat(document.getElementById('newBotTrailingActivation').value),
                trailing_step: parseFloat(document.getElementById('newBotTrailingStep').value),
                breakeven_enabled: document.getElementById('newBotBreakevenEnabled').value === 'true',
                breakeven_activation: parseFloat(document.getElementById('newBotBreakevenActivation').value),
                breakeven_offset: parseFloat(document.getElementById('newBotBreakevenOffset').value),
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
            document.getElementById('editBotMaxOrders').value = bot.max_simultaneous_orders || 3;
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
            document.getElementById('editBotLeverageMode').value = bot.leverage_mode || 'cross';
            document.getElementById('editBotOrderSize').value = bot.order_size || 100;
            document.getElementById('editBotPositionSizingMode').value = bot.position_sizing_mode || 'fixed_amount';
            document.getElementById('editBotRisk').value = bot.risk_per_trade;
            document.getElementById('editBotTpMode').value = bot.tp_mode || 'risk_ratio';
            document.getElementById('editBotTpRatio').value = bot.tp_risk_ratio;
            document.getElementById('editBotTpPercent').value = bot.tp_fixed_percent || 4;
            document.getElementById('editBotSlMode').value = bot.sl_mode;
            document.getElementById('editBotSlLine').value = bot.sl_supertrend_line || 2;
            document.getElementById('editBotSlPercent').value = bot.sl_fixed_percent || 2;
            document.getElementById('editBotSlAtrMult').value = bot.sl_atr_multiplier || 1.5;
            document.getElementById('editBotMaxPositions').value = bot.max_positions;
            document.getElementById('editBotEmaEnabled').value = bot.ema_enabled ? 'true' : 'false';
            document.getElementById('editBotEmaMode').value = bot.ema_filter_mode || 'strict';
            document.getElementById('editBotTrailingEnabled').value = bot.trailing_enabled !== false ? 'true' : 'false';
            document.getElementById('editBotTrailingMode').value = bot.trailing_mode || 'supertrend';
            document.getElementById('editBotTrailingActivation').value = bot.trailing_activation || 1.0;
            document.getElementById('editBotTrailingStep').value = bot.trailing_step || 0.5;
            document.getElementById('editBotBreakevenEnabled').value = bot.breakeven_enabled ? 'true' : 'false';
            document.getElementById('editBotBreakevenActivation').value = bot.breakeven_activation || 1.0;
            document.getElementById('editBotBreakevenOffset').value = bot.breakeven_offset || 0.1;

            // Toggle options visibility
            toggleSlOptions('edit');
            togglePositionSizingOptions('edit');
            toggleTpOptions('edit');
            toggleTrailingOptions('edit');
            toggleTrailingModeOptions('edit');
            toggleBreakevenOptions('edit');

            // Update deposit info
            updateDepositInfo('edit');

            document.getElementById('editBotModal').classList.add('show');
        }

        async function saveEditBot() {
            const botId = document.getElementById('editBotId').value;
            const botMode = document.getElementById('editBotMode').value;
            const slMode = document.getElementById('editBotSlMode').value;
            const config = {
                name: document.getElementById('editBotName').value,
                bot_mode: botMode,
                trading_pairs: botMode === 'auto_search' ? [] : [document.getElementById('editBotPair').value],
                max_simultaneous_orders: parseInt(document.getElementById('editBotMaxOrders').value) || 3,
                timeframe: document.getElementById('editBotTimeframe').value,
                leverage: parseInt(document.getElementById('editBotLeverage').value),
                leverage_mode: document.getElementById('editBotLeverageMode').value,
                order_size: parseFloat(document.getElementById('editBotOrderSize').value),
                position_sizing_mode: document.getElementById('editBotPositionSizingMode').value,
                risk_per_trade: parseFloat(document.getElementById('editBotRisk').value),
                tp_mode: document.getElementById('editBotTpMode').value,
                tp_risk_ratio: parseFloat(document.getElementById('editBotTpRatio').value),
                tp_fixed_percent: parseFloat(document.getElementById('editBotTpPercent').value),
                sl_mode: slMode,
                sl_supertrend_line: parseInt(document.getElementById('editBotSlLine').value),
                sl_fixed_percent: parseFloat(document.getElementById('editBotSlPercent').value),
                sl_atr_multiplier: parseFloat(document.getElementById('editBotSlAtrMult').value),
                max_positions: parseInt(document.getElementById('editBotMaxPositions').value),
                ema_enabled: document.getElementById('editBotEmaEnabled').value === 'true',
                ema_filter_mode: document.getElementById('editBotEmaMode').value,
                trailing_enabled: document.getElementById('editBotTrailingEnabled').value === 'true',
                trailing_mode: document.getElementById('editBotTrailingMode').value,
                trailing_activation: parseFloat(document.getElementById('editBotTrailingActivation').value),
                trailing_step: parseFloat(document.getElementById('editBotTrailingStep').value),
                breakeven_enabled: document.getElementById('editBotBreakevenEnabled').value === 'true',
                breakeven_activation: parseFloat(document.getElementById('editBotBreakevenActivation').value),
                breakeven_offset: parseFloat(document.getElementById('editBotBreakevenOffset').value),
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
            document.getElementById('fullscreenChartTitle').textContent = `${bot.name} - ${symbol} (${bot.timeframe})`;

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
bot_engines = {}  # bot_id -> TradingEngine instance
bot_clients = {}  # bot_id -> BybitClient instance
bot_runtime_settings = {}  # bot_id -> settings dict (per-bot runtime settings)

# Trading positions registry - tracks positions per bot
positions_registry = {}


def get_running_bot_id():
    """Get the ID of first currently running or paused bot."""
    for bot_id, bot in bots_registry.items():
        if bot.get("status") in ("running", "paused"):
            return bot_id
    return None


def get_bot_for_symbol(symbol: str):
    """Get the bot_id that is trading a specific symbol."""
    for bot_id, bot in bots_registry.items():
        if bot.get("status") not in ("running", "paused"):
            continue
        bot_pairs = bot.get("trading_pairs", [])
        if bot.get("bot_mode") == "auto_search" or symbol in bot_pairs:
            return bot_id
    return None


def get_bot_runtime_settings(bot_id: str):
    """Get runtime settings for a specific bot."""
    if bot_id in bot_runtime_settings:
        return bot_runtime_settings[bot_id]
    return runtime_settings


def register_position(symbol: str, side: str, entry_price: float, size: float, sl: float, tp: float, bot_id: str = None):
    """Register a new position opened by a bot."""
    import uuid

    if not bot_id:
        bot_id = get_bot_for_symbol(symbol)
    if not bot_id:
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


def close_position_record(symbol: str, reason: str, pnl_usdt: float = None, pnl_percent: float = None):
    """Mark position as closed."""
    for pos_id, pos in positions_registry.items():
        if pos["symbol"] == symbol and pos["status"] == "open":
            pos["status"] = "closed"
            pos["closed_at"] = datetime.now().isoformat()
            pos["close_reason"] = reason
            if pnl_usdt is not None:
                pos["pnl_usdt"] = pnl_usdt
            if pnl_percent is not None:
                pos["pnl_percent"] = pnl_percent
            return pos_id
    return None


def get_bot_open_positions(bot_id: str):
    """Get open positions for a specific bot."""
    return [pos for pos in positions_registry.values()
            if pos["bot_id"] == bot_id and pos["status"] == "open"]


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
        "bot_mode": config.get("bot_mode", "manual"),
        "trading_pairs": config.get("trading_pairs", ["BTCUSDT"]),
        "max_simultaneous_orders": config.get("max_simultaneous_orders", 3),
        "timeframe": config.get("timeframe", "15m"),
        # Position sizing
        "position_sizing_mode": config.get("position_sizing_mode", "fixed_amount"),
        "risk_per_trade": config.get("risk_per_trade", 2.0),
        "order_size": config.get("order_size", 100.0),
        # Take profit
        "tp_mode": config.get("tp_mode", "risk_ratio"),
        "tp_risk_ratio": config.get("tp_risk_ratio", 2.0),
        "tp_fixed_percent": config.get("tp_fixed_percent", 4.0),
        # Stop loss
        "sl_mode": config.get("sl_mode", "supertrend_line"),
        "sl_supertrend_line": config.get("sl_supertrend_line", 2),
        "sl_fixed_percent": config.get("sl_fixed_percent", 2.0),
        "sl_atr_multiplier": config.get("sl_atr_multiplier", 1.5),
        # Leverage
        "leverage": config.get("leverage", 10),
        "leverage_mode": config.get("leverage_mode", "cross"),
        "max_positions": config.get("max_positions", 3),
        # EMA filter
        "ema_enabled": config.get("ema_enabled", True),
        "ema_filter_mode": config.get("ema_filter_mode", "strict"),
        # Trailing stop
        "trailing_enabled": config.get("trailing_enabled", True),
        "trailing_mode": config.get("trailing_mode", "supertrend"),
        "trailing_activation": config.get("trailing_activation", 1.0),
        "trailing_step": config.get("trailing_step", 0.5),
        # Break-even
        "breakeven_enabled": config.get("breakeven_enabled", False),
        "breakeven_activation": config.get("breakeven_activation", 1.0),
        "breakeven_offset": config.get("breakeven_offset", 0.1),
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
    if "bot_mode" in config:
        bot["bot_mode"] = config["bot_mode"]
    if "trading_pairs" in config:
        bot["trading_pairs"] = config["trading_pairs"]
    if "max_simultaneous_orders" in config:
        bot["max_simultaneous_orders"] = int(config["max_simultaneous_orders"])
    if "timeframe" in config:
        bot["timeframe"] = config["timeframe"]
    # Position sizing
    if "position_sizing_mode" in config:
        bot["position_sizing_mode"] = config["position_sizing_mode"]
    if "risk_per_trade" in config:
        bot["risk_per_trade"] = float(config["risk_per_trade"])
    if "order_size" in config:
        bot["order_size"] = float(config["order_size"])
    # Take profit
    if "tp_mode" in config:
        bot["tp_mode"] = config["tp_mode"]
    if "tp_risk_ratio" in config:
        bot["tp_risk_ratio"] = float(config["tp_risk_ratio"])
    if "tp_fixed_percent" in config:
        bot["tp_fixed_percent"] = float(config["tp_fixed_percent"])
    # Stop loss
    if "sl_mode" in config:
        bot["sl_mode"] = config["sl_mode"]
    if "sl_supertrend_line" in config:
        bot["sl_supertrend_line"] = int(config["sl_supertrend_line"])
    if "sl_fixed_percent" in config:
        bot["sl_fixed_percent"] = float(config["sl_fixed_percent"])
    if "sl_atr_multiplier" in config:
        bot["sl_atr_multiplier"] = float(config["sl_atr_multiplier"])
    # Leverage
    if "leverage" in config:
        bot["leverage"] = int(config["leverage"])
    if "leverage_mode" in config:
        bot["leverage_mode"] = config["leverage_mode"]
    if "max_positions" in config:
        bot["max_positions"] = int(config["max_positions"])
    # EMA filter
    if "ema_enabled" in config:
        bot["ema_enabled"] = config["ema_enabled"]
    if "ema_filter_mode" in config:
        bot["ema_filter_mode"] = config["ema_filter_mode"]
    # Trailing stop
    if "trailing_enabled" in config:
        bot["trailing_enabled"] = config["trailing_enabled"]
    if "trailing_mode" in config:
        bot["trailing_mode"] = config["trailing_mode"]
    if "trailing_activation" in config:
        bot["trailing_activation"] = float(config["trailing_activation"])
    if "trailing_step" in config:
        bot["trailing_step"] = float(config["trailing_step"])
    # Break-even
    if "breakeven_enabled" in config:
        bot["breakeven_enabled"] = config["breakeven_enabled"]
    if "breakeven_activation" in config:
        bot["breakeven_activation"] = float(config["breakeven_activation"])
    if "breakeven_offset" in config:
        bot["breakeven_offset"] = float(config["breakeven_offset"])

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
    runtime_settings["bot_mode"] = bot.get("bot_mode", "manual")
    runtime_settings["max_simultaneous_orders"] = bot.get("max_simultaneous_orders", 3)

    # For auto_search mode, get all trading pairs
    if bot.get("bot_mode") == "auto_search":
        # Get all perpetual USDT pairs from cache or fetch
        if trading_pairs_cache["pairs"]:
            # Filter out any undefined/empty symbols
            runtime_settings["trading_pairs"] = [
                p["symbol"] for p in trading_pairs_cache["pairs"]
                if p.get("symbol") and p["symbol"] not in ("undefined", "null", "")
            ]
        else:
            runtime_settings["trading_pairs"] = ["BTCUSDT"]  # Fallback
        runtime_settings["auto_search_active"] = True
    else:
        runtime_settings["trading_pairs"] = bot["trading_pairs"]
        runtime_settings["auto_search_active"] = False

    runtime_settings["timeframe"] = bot["timeframe"]
    # Position sizing
    runtime_settings["position_sizing_mode"] = bot.get("position_sizing_mode", "fixed_amount")
    runtime_settings["risk_per_trade"] = bot["risk_per_trade"]
    runtime_settings["order_size"] = bot.get("order_size", 100.0)
    # Take profit
    runtime_settings["tp_mode"] = bot.get("tp_mode", "risk_ratio")
    runtime_settings["tp_risk_ratio"] = bot["tp_risk_ratio"]
    runtime_settings["tp_fixed_percent"] = bot.get("tp_fixed_percent", 4.0)
    # Stop loss
    runtime_settings["sl_mode"] = bot["sl_mode"]
    runtime_settings["sl_supertrend_line"] = bot.get("sl_supertrend_line", 2)
    runtime_settings["sl_fixed_percent"] = bot.get("sl_fixed_percent", 2.0)
    runtime_settings["sl_atr_multiplier"] = bot.get("sl_atr_multiplier", 1.5)
    # Leverage
    runtime_settings["leverage"] = bot["leverage"]
    runtime_settings["leverage_mode"] = bot.get("leverage_mode", "cross")
    runtime_settings["margin_mode"] = bot.get("leverage_mode", "cross")  # alias for TradingEngineConfig
    runtime_settings["max_open_positions"] = bot["max_positions"]
    # EMA filter
    runtime_settings["ema_enabled"] = bot["ema_enabled"]
    runtime_settings["ema_filter_mode"] = bot.get("ema_filter_mode", "strict")
    # Trailing stop
    runtime_settings["trailing_enabled"] = bot.get("trailing_enabled", True)
    runtime_settings["trailing_mode"] = bot.get("trailing_mode", "supertrend")
    runtime_settings["trailing_activation"] = bot.get("trailing_activation", 1.0)
    runtime_settings["trailing_step"] = bot.get("trailing_step", 0.5)
    # Break-even
    runtime_settings["breakeven_enabled"] = bot.get("breakeven_enabled", False)
    runtime_settings["breakeven_activation"] = bot.get("breakeven_activation", 1.0)
    runtime_settings["breakeven_offset"] = bot.get("breakeven_offset", 0.1)

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

                logger.info(f"Loaded {len(result)} trading pairs from Bybit API")
                return {"pairs": result}

    except Exception as e:
        logger.error("Failed to fetch trading pairs from Bybit API", error=str(e))

    # Return cached data if available (even if stale)
    if trading_pairs_cache["pairs"]:
        return {"pairs": trading_pairs_cache["pairs"]}

    return {"pairs": [], "error": "Failed to load trading pairs"}


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


# ============== Log Messages ==============
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


def run_web_server(host: str = "0.0.0.0", port: int = 8080):
    """Run the web server."""
    import uvicorn
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run_web_server()
