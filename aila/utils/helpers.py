"""
AILA - Helper Functions

Common utility functions used throughout the application.
"""

from decimal import ROUND_DOWN, Decimal
from typing import Optional


def timeframe_to_seconds(timeframe: str) -> int:
    """
    Convert timeframe string to seconds.

    Args:
        timeframe: Timeframe string (e.g., '1m', '1h', '1d')

    Returns:
        Number of seconds in the timeframe
    """
    multipliers = {
        "m": 60,
        "h": 3600,
        "d": 86400,
        "w": 604800,
    }

    if timeframe[-1] in multipliers:
        value = int(timeframe[:-1])
        return value * multipliers[timeframe[-1]]

    return 3600  # Default to 1 hour


def timeframe_to_minutes(timeframe: str) -> int:
    """
    Convert timeframe string to minutes.

    Args:
        timeframe: Timeframe string

    Returns:
        Number of minutes in the timeframe
    """
    return timeframe_to_seconds(timeframe) // 60


def round_to_tick(value: Decimal, tick_size: Decimal) -> Decimal:
    """
    Round value to nearest tick size.

    Args:
        value: Value to round
        tick_size: Tick size

    Returns:
        Rounded value
    """
    return (value / tick_size).quantize(Decimal("1"), rounding=ROUND_DOWN) * tick_size


def round_quantity(quantity: Decimal, step_size: Decimal) -> Decimal:
    """
    Round quantity to valid step size.

    Args:
        quantity: Quantity to round
        step_size: Step size

    Returns:
        Rounded quantity
    """
    return (quantity / step_size).quantize(Decimal("1"), rounding=ROUND_DOWN) * step_size


def format_currency(
    value: Decimal | float,
    decimals: int = 2,
    symbol: str = "$",
) -> str:
    """
    Format value as currency.

    Args:
        value: Value to format
        decimals: Decimal places
        symbol: Currency symbol

    Returns:
        Formatted string
    """
    if isinstance(value, float):
        value = Decimal(str(value))

    formatted = f"{float(value):,.{decimals}f}"
    return f"{symbol}{formatted}"


def format_percent(
    value: float,
    decimals: int = 2,
    include_sign: bool = True,
) -> str:
    """
    Format value as percentage.

    Args:
        value: Value to format (already in percent form)
        decimals: Decimal places
        include_sign: Include +/- sign

    Returns:
        Formatted string
    """
    sign = ""
    if include_sign and value > 0:
        sign = "+"
    elif include_sign and value < 0:
        sign = ""  # Negative sign is automatic

    return f"{sign}{value:.{decimals}f}%"


def calculate_pnl(
    entry_price: Decimal,
    exit_price: Decimal,
    quantity: Decimal,
    side: str,
    leverage: int = 1,
) -> tuple[Decimal, float]:
    """
    Calculate PnL for a trade.

    Args:
        entry_price: Entry price
        exit_price: Exit price
        quantity: Position quantity
        side: Position side ('long' or 'short')
        leverage: Leverage used

    Returns:
        Tuple of (pnl_value, pnl_percent)
    """
    if side == "long":
        price_diff = exit_price - entry_price
    else:
        price_diff = entry_price - exit_price

    pnl_value = price_diff * quantity
    pnl_percent = float(price_diff / entry_price) * 100 * leverage

    return pnl_value, pnl_percent


def calculate_position_size(
    balance: Decimal,
    risk_percent: float,
    entry_price: Decimal,
    stop_loss_price: Decimal,
    leverage: int = 1,
) -> Decimal:
    """
    Calculate position size based on risk.

    Args:
        balance: Available balance
        risk_percent: Percentage of balance to risk
        entry_price: Entry price
        stop_loss_price: Stop-loss price
        leverage: Leverage

    Returns:
        Position size in base asset
    """
    risk_amount = balance * Decimal(str(risk_percent / 100))
    stop_distance = abs(entry_price - stop_loss_price) / entry_price

    if stop_distance == 0:
        return Decimal("0")

    position_value = risk_amount / stop_distance
    notional_value = position_value * Decimal(str(leverage))

    return notional_value / entry_price


def get_opposite_side(side: str) -> str:
    """
    Get opposite trading side.

    Args:
        side: Current side ('long', 'short', 'buy', 'sell')

    Returns:
        Opposite side
    """
    opposites = {
        "long": "short",
        "short": "long",
        "buy": "sell",
        "sell": "buy",
    }
    return opposites.get(side.lower(), side)


def is_valid_symbol(symbol: str) -> bool:
    """
    Check if symbol format is valid.

    Args:
        symbol: Trading pair symbol

    Returns:
        True if valid
    """
    # Basic validation: must end with common quote currencies
    valid_suffixes = ["USDT", "USD", "BTC", "ETH", "USDC"]
    return any(symbol.upper().endswith(suffix) for suffix in valid_suffixes)


def parse_symbol(symbol: str) -> tuple[str, str]:
    """
    Parse symbol into base and quote currencies.

    Args:
        symbol: Trading pair symbol (e.g., 'BTCUSDT')

    Returns:
        Tuple of (base, quote)
    """
    quote_currencies = ["USDT", "USDC", "USD", "BTC", "ETH"]

    for quote in quote_currencies:
        if symbol.upper().endswith(quote):
            base = symbol[: -len(quote)]
            return base, quote

    return symbol, ""


def truncate_string(s: str, max_length: int = 50) -> str:
    """
    Truncate string with ellipsis.

    Args:
        s: String to truncate
        max_length: Maximum length

    Returns:
        Truncated string
    """
    if len(s) <= max_length:
        return s
    return s[: max_length - 3] + "..."


def merge_dicts(*dicts: dict) -> dict:
    """
    Deep merge multiple dictionaries.

    Args:
        *dicts: Dictionaries to merge

    Returns:
        Merged dictionary
    """
    result = {}
    for d in dicts:
        for key, value in d.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = merge_dicts(result[key], value)
            else:
                result[key] = value
    return result
