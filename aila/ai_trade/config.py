import os

# Anthropic API
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
CLAUDE_MODEL = "claude-sonnet-4-20250514"

# Hard risk limits (AI cannot override these)
RISK_LIMITS = {
    "max_leverage": 20,
    "max_position_size_pct": 10,      # Max 10% of deposit per trade
    "max_daily_loss_pct": 5,          # Stop trading at -5% daily loss
    "max_drawdown_pct": 15,           # Max drawdown
    "min_balance_usdt": 10,           # Minimum balance
    "max_open_positions": 3,          # Max concurrent positions
    "default_risk_per_trade_pct": 2,  # Default risk per trade
}

# Operating modes
MODES = {
    "OBSERVER": {"can_trade": False, "description": "Analysis only, no trades"},
    "ADVISOR": {"can_trade": False, "requires_approval": True, "description": "Suggests trades, user confirms"},
    "AUTOPILOT": {"can_trade": True, "description": "Fully autonomous trading"},
}

# Scanner settings
SCANNER_CONFIG = {
    "min_volume_24h": 5_000_000,      # Min 24h volume in USDT
    "min_volatility_pct": 1,          # Min volatility
    "max_volatility_pct": 15,         # Max volatility
    "scan_interval_seconds": 60,      # Scan interval
    "top_pairs_count": 20,            # Top pairs for analysis
}

# Timeframes for analysis
TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h"]

# Knowledge base path
KNOWLEDGE_BASE_PATH = "/opt/aila/data/ai_knowledge.json"
LOG_PATH = "/opt/aila/logs/ai_trade.log"
