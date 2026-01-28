"""
Position conflict checker - prevents both AI Trade and main bot from opening
positions on the same symbol simultaneously.
"""

import logging
import os
from typing import Optional

from pybit.unified_trading import HTTP

logger = logging.getLogger("position_conflict")

# Singleton client for position checking
_bybit_client: Optional[HTTP] = None


def _get_client() -> Optional[HTTP]:
    """Get or create Bybit client for position checking."""
    global _bybit_client
    if _bybit_client is None:
        api_key = os.getenv("BYBIT_API_KEY", "")
        api_secret = os.getenv("BYBIT_API_SECRET", "")
        testnet = os.getenv("BYBIT_TESTNET", "false").lower() == "true"
        if api_key and api_secret:
            _bybit_client = HTTP(
                api_key=api_key,
                api_secret=api_secret,
                testnet=testnet,
            )
    return _bybit_client


def check_position_conflict(symbol: str) -> bool:
    """
    Check if there's an existing position for the symbol.
    Returns True if position exists (conflict), False if safe to open.

    Used by BOTH AI Trade and main bot before opening positions.
    """
    try:
        client = _get_client()
        if not client:
            logger.warning("No Bybit client available for conflict check")
            return False  # Allow trade if can't check

        # Normalize symbol: JTO/USDT -> JTOUSDT
        bybit_symbol = symbol.replace("/", "") if "/" in symbol else symbol

        result = client.get_positions(
            category="linear",
            symbol=bybit_symbol,
        )

        if result["retCode"] == 0:
            positions = result["result"]["list"]
            for pos in positions:
                size = float(pos.get("size", 0))
                if size > 0:
                    logger.warning(
                        f"Position conflict: {symbol} already has position "
                        f"(size={size}, side={pos.get('side')})"
                    )
                    return True  # Conflict found

        return False  # No conflict

    except Exception as e:
        logger.error(f"Error checking position conflict for {symbol}: {e}")
        return False  # Allow trade if check fails


def get_all_open_positions() -> list[dict]:
    """Get all open positions from Bybit."""
    try:
        client = _get_client()
        if not client:
            return []

        result = client.get_positions(
            category="linear",
            settleCoin="USDT",
        )

        positions = []
        if result["retCode"] == 0:
            for pos in result["result"]["list"]:
                size = float(pos.get("size", 0))
                if size > 0:
                    positions.append({
                        "symbol": pos["symbol"],
                        "side": pos["side"],
                        "size": size,
                        "entry_price": float(pos.get("avgPrice", 0)),
                        "unrealized_pnl": float(pos.get("unrealisedPnl", 0)),
                        "leverage": pos.get("leverage"),
                    })

        return positions

    except Exception as e:
        logger.error(f"Error getting open positions: {e}")
        return []
