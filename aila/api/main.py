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
DASHBOARD_HTML = """
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
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>AILA Trading Bot</h1>
            <div class="status">
                <div class="status-dot" id="statusDot"></div>
                <span id="statusText">Connecting...</span>
            </div>
        </header>

        <div class="cards">
            <div class="card">
                <div class="card-title">Balance (USDT)</div>
                <div class="card-value" id="balance">--</div>
            </div>
            <div class="card">
                <div class="card-title">Open Positions</div>
                <div class="card-value" id="positions">--</div>
            </div>
            <div class="card">
                <div class="card-title">Today's Trades</div>
                <div class="card-value" id="trades">--</div>
            </div>
            <div class="card">
                <div class="card-title">Today's PnL</div>
                <div class="card-value" id="pnl">--</div>
            </div>
        </div>

        <div class="logs-container">
            <div class="logs-header">
                <div style="display: flex; align-items: center;">
                    <h2>Live Logs</h2>
                    <span class="auto-scroll-indicator active" id="autoScrollIndicator">Auto-scroll ON</span>
                </div>
                <div class="btn-group">
                    <button class="btn btn-secondary" onclick="clearLogs()">Clear</button>
                    <button class="btn btn-primary" onclick="copyLogs()">Copy Logs</button>
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
                document.getElementById('statusText').textContent = 'Connected';
                reconnectAttempts = 0;
            };

            ws.onmessage = function(event) {
                addLogLine(event.data);
            };

            ws.onclose = function() {
                console.log('WebSocket disconnected');
                document.getElementById('statusDot').classList.add('disconnected');
                document.getElementById('statusText').textContent = 'Disconnected';

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
            const lines = Array.from(logsDiv.querySelectorAll('.log-line'))
                .map(line => line.textContent)
                .join('\\n');

            navigator.clipboard.writeText(lines).then(() => {
                showToast('Logs copied to clipboard!');
            }).catch(err => {
                console.error('Failed to copy:', err);
                showToast('Failed to copy logs');
            });
        }

        function clearLogs() {
            document.getElementById('logs').innerHTML = '';
            addLogLine('--- Logs cleared ---');
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
                indicator.textContent = 'Auto-scroll ON';
                indicator.classList.add('active');
            } else {
                indicator.textContent = 'Auto-scroll OFF (scroll to bottom to enable)';
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
