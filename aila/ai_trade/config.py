import os

# Anthropic API
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
CLAUDE_MODEL = "claude-sonnet-4-20250514"  # For critical agents (TRADER, REVIEWER)
CLAUDE_MODEL_HAIKU = "claude-haiku-4-5-20251001"  # For non-critical agents (NEWS, MENTOR, ANALYST)

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

# Bybit exchange limits
MIN_ORDER_SIZE_USDT = 10  # Minimum order size for Bybit Futures

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
    "top_pairs_count": 20,            # Top pairs for analysis (fallback if SCAN_ALL_PAIRS=False)
    "scan_all_pairs": True,           # Scan ALL USDT perpetual pairs dynamically
    "pairs_cache_ttl": 3600,          # Cache instruments list for 1 hour
}

# Timeframes for analysis
TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h"]

# Knowledge base paths
KNOWLEDGE_BASE_PATH = "/opt/aila/data/ai_trade/knowledge_base.json"
TRADER_KNOWLEDGE_PATH = "/opt/aila/data/ai_trade/trader_knowledge.json"
SNIPER_KNOWLEDGE_PATH = "/opt/aila/data/ai_trade/sniper_knowledge.json"
SHARED_KNOWLEDGE_PATH = "/opt/aila/data/ai_trade/shared_knowledge.json"
TRADER_STATS_PATH = "/opt/aila/data/ai_trade/trader_stats.json"
SNIPER_STATS_PATH = "/opt/aila/data/ai_trade/sniper_stats.json"
LOG_PATH = "/opt/aila/logs/ai_trade.log"

# TRADER levels (trend-based trading)
TRADER_LEVELS = {
    1: {"max_leverage": 3, "max_positions": 1, "max_risk_pct": 1.0, "max_daily_trades": 5},
    2: {"max_leverage": 5, "max_positions": 1, "max_risk_pct": 1.5, "max_daily_trades": 7},
    3: {"max_leverage": 7, "max_positions": 2, "max_risk_pct": 1.5, "max_daily_trades": 8},
    4: {"max_leverage": 10, "max_positions": 2, "max_risk_pct": 2.0, "max_daily_trades": 10},
    5: {"max_leverage": 12, "max_positions": 2, "max_risk_pct": 2.0, "max_daily_trades": 12},
    6: {"max_leverage": 15, "max_positions": 3, "max_risk_pct": 2.5, "max_daily_trades": 15},
    7: {"max_leverage": 17, "max_positions": 3, "max_risk_pct": 2.5, "max_daily_trades": 17},
    8: {"max_leverage": 20, "max_positions": 3, "max_risk_pct": 3.0, "max_daily_trades": 20},
    9: {"max_leverage": 22, "max_positions": 4, "max_risk_pct": 3.0, "max_daily_trades": 22},
    10: {"max_leverage": 25, "max_positions": 5, "max_risk_pct": 3.5, "max_daily_trades": 25},
}

# SNIPER levels (breakout trading - more conservative)
SNIPER_LEVELS = {
    1: {"max_leverage": 2, "max_positions": 1, "max_risk_pct": 0.5, "max_daily_trades": 10},
    2: {"max_leverage": 3, "max_positions": 1, "max_risk_pct": 0.75, "max_daily_trades": 12},
    3: {"max_leverage": 5, "max_positions": 1, "max_risk_pct": 1.0, "max_daily_trades": 15},
    4: {"max_leverage": 7, "max_positions": 2, "max_risk_pct": 1.0, "max_daily_trades": 17},
    5: {"max_leverage": 8, "max_positions": 2, "max_risk_pct": 1.25, "max_daily_trades": 20},
    6: {"max_leverage": 10, "max_positions": 2, "max_risk_pct": 1.5, "max_daily_trades": 22},
    7: {"max_leverage": 12, "max_positions": 3, "max_risk_pct": 1.5, "max_daily_trades": 25},
    8: {"max_leverage": 15, "max_positions": 3, "max_risk_pct": 2.0, "max_daily_trades": 27},
    9: {"max_leverage": 17, "max_positions": 3, "max_risk_pct": 2.0, "max_daily_trades": 30},
    10: {"max_leverage": 20, "max_positions": 4, "max_risk_pct": 2.5, "max_daily_trades": 35},
}

# XP thresholds for level up (same for both agents)
AGENT_XP_THRESHOLDS = {
    1: 0, 2: 100, 3: 250, 4: 500, 5: 1000,
    6: 2000, 7: 3500, 8: 5500, 9: 8000, 10: 12000,
}

# Agent cooldowns (seconds)
AGENT_COOLDOWNS = {
    "TRADER": 300,   # 5 min between trades
    "SNIPER": 60,    # 1 min between trades
}

# Performance limits
PERFORMANCE_LIMITS = {
    "min_winrate_week": 40,      # Min winrate over week (%)
    "loss_streak_pause": 3,      # Pause after N consecutive losses
    "loss_streak_pause_minutes": 60,  # Pause duration
}
