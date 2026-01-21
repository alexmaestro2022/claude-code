"""
Default settings for 3 EMA Pullback Scalping strategy.
"""

DEFAULT_EMA_PULLBACK_SETTINGS = {
    # Block 1: Basic settings
    "name": "3 EMA Scalping",
    "enabled": False,
    "max_pairs": 1,
    "timeframe": "1m",
    "leverage": 10,
    "order_size_usdt": 10,
    "position_size_mode": "fixed",  # "fixed" or "percent"
    "margin_mode": "isolated",  # "isolated" or "cross"

    # Block 2: Risk management
    "balance_usage_pct": 30,
    "risk_per_trade": 2.0,
    "daily_loss_limit_enabled": True,
    "daily_loss_limit_pct": 5.0,
    "max_losing_streak_enabled": True,
    "max_losing_streak": 3,
    "max_daily_trades_enabled": False,
    "max_daily_trades": 10,

    # Block 3.1: EMA parameters
    "ema_fast": 50,
    "ema_medium": 100,
    "ema_slow": 150,

    # Block 3.2: Trend filter
    "slope_min": 0.1,
    "slope_candles": 5,
    "ema_distance_atr_mult": 1.0,
    "atr_period": 14,

    # Block 3.3: Entry conditions
    "touch_mode": "low_high",  # "low_high" or "close"
    "touch_tolerance": 0.1,  # %
    "forbid_ema150_touch": True,

    # Block 3.4: Stop loss
    "sl_mode": "pullback",  # "pullback" or "fixed"
    "sl_buffer": 0.1,  # %
    "sl_fixed_pct": 1.0,  # %

    # Block 3.5: Take profit
    "tp_mode": "trailing",  # "fixed_rr" or "trailing"
    "rr_ratio": 1.5,
    "trailing_be_enabled": True,
    "be_after_profit_pct": 0.3,
    "trailing_mode": "new_high_low",  # "new_high_low", "ema50", "candles"
    "trailing_buffer": 0.1,  # %

    # Block 3.6: Trade direction
    "trade_long": True,
    "trade_short": True,

    # Runtime state
    "daily_loss": 0.0,
    "losing_streak": 0,
    "daily_trades": 0,
    "last_reset_date": None,
}


def get_ema_pullback_settings() -> dict:
    """Return a copy of default settings."""
    return DEFAULT_EMA_PULLBACK_SETTINGS.copy()


def validate_settings(settings: dict) -> tuple[bool, str]:
    """Validate strategy settings."""
    errors = []

    # Validate EMA periods
    if settings.get("ema_fast", 50) >= settings.get("ema_medium", 100):
        errors.append("EMA Fast must be less than EMA Medium")
    if settings.get("ema_medium", 100) >= settings.get("ema_slow", 150):
        errors.append("EMA Medium must be less than EMA Slow")

    # Validate risk
    if settings.get("risk_per_trade", 2.0) > 5.0:
        errors.append("Risk per trade cannot exceed 5%")

    # Validate leverage
    if settings.get("leverage", 10) > 50:
        errors.append("Leverage cannot exceed 50x")

    if errors:
        return False, "; ".join(errors)
    return True, ""
