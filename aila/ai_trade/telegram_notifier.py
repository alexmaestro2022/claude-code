"""Telegram notifier for AI Trade module."""

import os
import logging
from datetime import datetime
from typing import Any, Optional

import aiohttp

logger = logging.getLogger("ai_trade.telegram")

# Load from environment
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


class TelegramNotifier:
    """Sends notifications to Telegram for AI Trade events."""

    __slots__ = ("_token", "_chat_id", "_enabled", "_session")

    def __init__(
        self,
        token: str = None,
        chat_id: str = None,
    ) -> None:
        self._token = token or TELEGRAM_BOT_TOKEN
        self._chat_id = chat_id or TELEGRAM_CHAT_ID
        self._enabled = bool(self._token and self._chat_id)
        self._session: Optional[aiohttp.ClientSession] = None

        if not self._enabled:
            logger.warning("Telegram notifier disabled: missing token or chat_id")

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        """Send message to Telegram."""
        if not self._enabled:
            return False

        try:
            session = await self._get_session()
            url = f"https://api.telegram.org/bot{self._token}/sendMessage"
            payload = {
                "chat_id": self._chat_id,
                "text": text,
                "parse_mode": parse_mode,
            }

            async with session.post(url, json=payload, timeout=10) as resp:
                if resp.status == 200:
                    logger.debug("Telegram message sent")
                    return True
                else:
                    logger.error(f"Telegram error: {resp.status}")
                    return False

        except Exception as e:
            logger.error(f"Telegram send failed: {e}")
            return False

    async def send_alert(self, message: str) -> bool:
        """Alias for send_message (compatibility with LoggerAgent)."""
        return await self.send_message(message)

    async def notify_position_opened(
        self,
        symbol: str,
        direction: str,
        size_usdt: float,
        entry_price: float,
        stop_loss: Optional[float],
        take_profit: Optional[float],
        leverage: int = 1,
        confidence: int = 0,
    ) -> bool:
        """Notify about new position opened."""
        sl_str = f"${stop_loss:.6f}" if stop_loss else "N/A"
        tp_str = f"${take_profit:.6f}" if take_profit else "N/A"

        text = f"""<b>AILA AI Trade - Position Opened</b>

<b>{direction}</b> {symbol}

Entry: <code>${entry_price:.6f}</code>
Size: <code>${size_usdt:.2f}</code> ({leverage}x)
SL: <code>{sl_str}</code>
TP: <code>{tp_str}</code>
Confidence: {confidence}%

<i>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</i>"""

        return await self.send_message(text)

    async def notify_position_closed(
        self,
        symbol: str,
        direction: str,
        pnl_usdt: float,
        pnl_pct: float,
        reason: str,
        duration: str = "",
    ) -> bool:
        """Notify about position closed."""
        emoji = "" if pnl_usdt >= 0 else ""
        pnl_sign = "+" if pnl_usdt >= 0 else ""

        text = f"""<b>{emoji} AILA AI Trade - Position Closed</b>

{direction} {symbol}

PnL: <code>{pnl_sign}${pnl_usdt:.2f}</code> ({pnl_sign}{pnl_pct:.2f}%)
Reason: {reason}
Duration: {duration or 'N/A'}

<i>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</i>"""

        return await self.send_message(text)

    async def notify_stop_loss_hit(
        self,
        symbol: str,
        direction: str,
        loss_usdt: float,
    ) -> bool:
        """Notify about stop loss triggered."""
        text = f"""<b> STOP LOSS - {symbol}</b>

{direction} position stopped out
Loss: <code>-${abs(loss_usdt):.2f}</code>

<i>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</i>"""

        return await self.send_message(text)

    async def notify_take_profit_hit(
        self,
        symbol: str,
        direction: str,
        profit_usdt: float,
    ) -> bool:
        """Notify about take profit reached."""
        text = f"""<b> TAKE PROFIT - {symbol}</b>

{direction} position closed at target
Profit: <code>+${profit_usdt:.2f}</code>

<i>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</i>"""

        return await self.send_message(text)

    async def notify_error(self, error_type: str, message: str) -> bool:
        """Notify about critical error."""
        text = f"""<b> AILA AI Trade - Error</b>

Type: {error_type}
Message: {message}

<i>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</i>"""

        return await self.send_message(text)

    async def notify_mode_change(
        self,
        old_mode: str,
        new_mode: str,
    ) -> bool:
        """Notify about mode change."""
        emoji = "" if new_mode == "AUTOPILOT" else ""

        text = f"""<b>{emoji} AILA AI Trade - Mode Changed</b>

{old_mode} -> <b>{new_mode}</b>

<i>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</i>"""

        return await self.send_message(text)

    async def notify_daily_summary(
        self,
        trades_count: int,
        pnl_usdt: float,
        win_rate: float,
        balance: float,
    ) -> bool:
        """Send daily trading summary."""
        emoji = "" if pnl_usdt >= 0 else ""
        pnl_sign = "+" if pnl_usdt >= 0 else ""

        text = f"""<b> AILA AI Trade - Daily Summary</b>

Trades: {trades_count}
Win Rate: {win_rate:.1f}%
PnL: <code>{pnl_sign}${pnl_usdt:.2f}</code>
Balance: <code>${balance:.2f}</code>

<i>{datetime.now().strftime('%Y-%m-%d')}</i>"""

        return await self.send_message(text)

    async def send_startup_message(self) -> bool:
        """Send startup notification."""
        text = """ <b>AILA AI Trade</b>

Notifications connected!
Mode: OBSERVER
Status: Ready

<i>{}</i>""".format(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))

        return await self.send_message(text)

    async def close(self) -> None:
        """Close aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()


# Global instance
_notifier: Optional[TelegramNotifier] = None


def get_telegram_notifier() -> TelegramNotifier:
    """Get or create global TelegramNotifier instance."""
    global _notifier
    if _notifier is None:
        _notifier = TelegramNotifier()
    return _notifier
