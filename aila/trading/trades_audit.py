"""
AILA - Trades Audit Logger

Detailed logging and auto-correction for trade execution.
Logs to /opt/aila/logs/trades_audit.log
"""

import os
from datetime import datetime
from decimal import Decimal
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..exchange.futures import FuturesTrader
    from ..exchange.models import Position, MarginMode


AUDIT_LOG_PATH = "/opt/aila/logs/trades_audit.log"


class TradesAuditLogger:
    """
    Audit logger for trade execution.

    Logs bot settings, sent parameters, and verifies/corrects position on exchange.
    """

    def __init__(self, trader: Optional["FuturesTrader"] = None):
        self.trader = trader
        self._ensure_log_dir()

    def _ensure_log_dir(self):
        """Ensure log directory exists."""
        log_dir = os.path.dirname(AUDIT_LOG_PATH)
        if not os.path.exists(log_dir):
            os.makedirs(log_dir, exist_ok=True)

    def _write_log(self, message: str):
        """Write message to audit log file."""
        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        with open(AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {message}\n")

    def log_trade_open(
        self,
        symbol: str,
        side: str,
        bot_settings: dict,
        sent_params: dict,
        order_result: dict,
    ) -> dict:
        """
        Log trade open and verify/correct position.

        Args:
            symbol: Trading pair (e.g., "BTCUSDT")
            side: "LONG" or "SHORT"
            bot_settings: Bot configuration dict
            sent_params: Parameters sent to exchange
            order_result: Result from order placement

        Returns:
            Dict with audit results and corrections made
        """
        audit_results = {
            "symbol": symbol,
            "side": side,
            "checks": [],
            "corrections": [],
            "errors": [],
            "success": True
        }

        # Build log message
        lines = []
        lines.append(f"TRADE OPEN {symbol} {side}")
        lines.append("")
        lines.append("НАСТРОЙКИ БОТА:")
        lines.append(f"- Плечо: {bot_settings.get('leverage', 'N/A')}x")
        lines.append(f"- Размер: {bot_settings.get('order_size', 'N/A')} USDT")
        lines.append(f"- TP режим: {bot_settings.get('tp_mode', 'N/A')} (R:R {bot_settings.get('tp_risk_ratio', 'N/A')})")
        lines.append(f"- SL режим: {bot_settings.get('sl_mode', 'N/A')} ({bot_settings.get('sl_fixed_percent', 'N/A')}%)")
        lines.append(f"- Маржа: {bot_settings.get('margin_mode', 'N/A')}")
        lines.append("")
        lines.append("ОТПРАВЛЕНО НА BYBIT:")
        lines.append(f"- leverage: {sent_params.get('leverage', 'N/A')}")
        lines.append(f"- qty: {sent_params.get('quantity', 'N/A')}")
        lines.append(f"- takeProfit: {sent_params.get('take_profit', 'N/A')}")
        lines.append(f"- stopLoss: {sent_params.get('stop_loss', 'N/A')}")
        lines.append(f"- marginMode: {sent_params.get('margin_mode', 'N/A')}")
        lines.append("")

        # Verify and correct position
        if self.trader:
            lines.append("ПРОВЕРКА И ИСПРАВЛЕНИЕ:")
            verification = self._verify_and_correct_position(
                symbol=symbol,
                side=side,
                expected_leverage=sent_params.get('leverage'),
                expected_margin_mode=sent_params.get('margin_mode'),
                expected_tp=sent_params.get('take_profit'),
                expected_sl=sent_params.get('stop_loss'),
                expected_qty=sent_params.get('quantity'),
                audit_results=audit_results
            )
            lines.extend(verification)
        else:
            lines.append("ПРОВЕРКА: Нет доступа к trader для верификации")

        lines.append("")

        # Summary
        if audit_results["errors"]:
            lines.append(f"ИТОГ: ⚠️ Есть проблемы: {', '.join(audit_results['errors'])}")
            audit_results["success"] = False
        elif audit_results["corrections"]:
            lines.append(f"ИТОГ: ✅ Позиция открыта корректно после исправлений")
        else:
            lines.append(f"ИТОГ: ✅ Позиция открыта корректно")

        lines.append("=" * 60)
        lines.append("")

        # Write to log
        self._write_log("\n".join(lines))

        return audit_results

    def _verify_and_correct_position(
        self,
        symbol: str,
        side: str,
        expected_leverage: Optional[int],
        expected_margin_mode: Optional[str],
        expected_tp: Optional[Decimal],
        expected_sl: Optional[Decimal],
        expected_qty: Optional[Decimal],
        audit_results: dict
    ) -> list[str]:
        """Verify position parameters and auto-correct if needed."""
        lines = []

        try:
            # Get actual position from exchange
            import time
            time.sleep(0.5)  # Small delay to let order settle

            position = self.trader.get_position(symbol)

            if not position:
                lines.append("⚠️ Позиция не найдена на бирже (возможно ещё обрабатывается)")
                audit_results["errors"].append("position_not_found")
                return lines

            # Check leverage
            if expected_leverage:
                actual_leverage = position.leverage
                if actual_leverage == expected_leverage:
                    lines.append(f"✅ Плечо: {actual_leverage}x — совпадает")
                    audit_results["checks"].append(("leverage", "OK"))
                else:
                    lines.append(f"❌ Плечо: ожидалось {expected_leverage}x, на бирже {actual_leverage}x → ИСПРАВЛЯЮ...")
                    try:
                        self.trader.set_leverage(symbol, expected_leverage)
                        lines.append(f"   ✅ Плечо исправлено на {expected_leverage}x")
                        audit_results["corrections"].append(("leverage", expected_leverage))
                    except Exception as e:
                        lines.append(f"   🚨 Не удалось исправить плечо: {e}")
                        audit_results["errors"].append(f"leverage_fix_failed: {e}")

            # Check margin mode
            if expected_margin_mode:
                actual_mode = position.margin_mode.value if position.margin_mode else "unknown"
                if actual_mode.lower() == expected_margin_mode.lower():
                    lines.append(f"✅ Маржа: {actual_mode} — совпадает")
                    audit_results["checks"].append(("margin_mode", "OK"))
                else:
                    lines.append(f"❌ Маржа: ожидалось {expected_margin_mode}, на бирже {actual_mode} → ИСПРАВЛЯЮ...")
                    try:
                        from ..exchange.models import MarginMode
                        mode = MarginMode.ISOLATED if expected_margin_mode.lower() == "isolated" else MarginMode.CROSS
                        self.trader.set_margin_mode(symbol, mode)
                        lines.append(f"   ✅ Маржа исправлена на {expected_margin_mode}")
                        audit_results["corrections"].append(("margin_mode", expected_margin_mode))
                    except Exception as e:
                        lines.append(f"   🚨 Не удалось исправить маржу: {e}")
                        audit_results["errors"].append(f"margin_mode_fix_failed: {e}")

            # Check Take Profit
            if expected_tp:
                actual_tp = position.take_profit
                if actual_tp and abs(float(actual_tp) - float(expected_tp)) < float(expected_tp) * 0.001:  # 0.1% tolerance
                    lines.append(f"✅ TP: {actual_tp} — совпадает")
                    audit_results["checks"].append(("take_profit", "OK"))
                else:
                    tp_status = f"на бирже {actual_tp}" if actual_tp else "отсутствует"
                    lines.append(f"❌ TP: ожидалось {expected_tp}, {tp_status} → ИСПРАВЛЯЮ...")
                    try:
                        self.trader.update_take_profit(symbol, expected_tp)
                        lines.append(f"   ✅ TP исправлен, выставлен {expected_tp}")
                        audit_results["corrections"].append(("take_profit", str(expected_tp)))
                    except Exception as e:
                        lines.append(f"   🚨 КРИТИЧЕСКАЯ ОШИБКА: Не удалось выставить TP для {symbol}! {e}")
                        audit_results["errors"].append(f"tp_fix_failed: {e}")
            else:
                lines.append("⚠️ TP: не задан в настройках")

            # Check Stop Loss
            if expected_sl:
                actual_sl = position.stop_loss
                if actual_sl and abs(float(actual_sl) - float(expected_sl)) < float(expected_sl) * 0.001:  # 0.1% tolerance
                    lines.append(f"✅ SL: {actual_sl} — совпадает")
                    audit_results["checks"].append(("stop_loss", "OK"))
                else:
                    sl_status = f"на бирже {actual_sl}" if actual_sl else "отсутствует"
                    lines.append(f"❌ SL: ожидалось {expected_sl}, {sl_status} → ИСПРАВЛЯЮ...")
                    try:
                        self.trader.update_stop_loss(symbol, expected_sl)
                        lines.append(f"   ✅ SL исправлен, выставлен {expected_sl}")
                        audit_results["corrections"].append(("stop_loss", str(expected_sl)))
                    except Exception as e:
                        lines.append(f"   🚨 КРИТИЧЕСКАЯ ОШИБКА: Не удалось выставить SL для {symbol}! {e}")
                        audit_results["errors"].append(f"sl_fix_failed: {e}")
            else:
                lines.append("⚠️ SL: не задан в настройках")

            # Check quantity (can't be corrected, just log)
            if expected_qty:
                actual_qty = position.size
                qty_diff_percent = abs(float(actual_qty) - float(expected_qty)) / float(expected_qty) * 100 if float(expected_qty) > 0 else 0
                if qty_diff_percent < 1:  # 1% tolerance for rounding
                    lines.append(f"✅ Размер: {actual_qty} — совпадает")
                    audit_results["checks"].append(("quantity", "OK"))
                else:
                    lines.append(f"⚠️ Размер: ожидалось {expected_qty}, получено {actual_qty} — минимальный лот биржи")
                    audit_results["checks"].append(("quantity", "ROUNDED"))

        except Exception as e:
            lines.append(f"🚨 Ошибка при проверке позиции: {e}")
            audit_results["errors"].append(f"verification_error: {e}")

        return lines

    def log_critical_error(self, symbol: str, message: str):
        """Log critical error that needs attention."""
        self._write_log(f"🚨 КРИТИЧЕСКАЯ ОШИБКА: {message} для {symbol}!")


# Global instance for easy access
_audit_logger: Optional[TradesAuditLogger] = None


def get_audit_logger(trader: Optional["FuturesTrader"] = None) -> TradesAuditLogger:
    """Get or create audit logger instance."""
    global _audit_logger
    if _audit_logger is None or (trader and _audit_logger.trader != trader):
        _audit_logger = TradesAuditLogger(trader)
    return _audit_logger
