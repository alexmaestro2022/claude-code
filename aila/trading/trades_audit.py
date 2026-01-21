"""
AILA - Trades Audit Logger

Comprehensive logging and auto-correction for trade execution.
Logs ALL bot settings, signal verification, and position verification.
Logs to /opt/aila/logs/trades_audit.log
"""

import os
from datetime import datetime
from decimal import Decimal
from typing import Optional, TYPE_CHECKING, Any

import structlog

logger = structlog.get_logger(__name__)

if TYPE_CHECKING:
    from ..exchange.futures import FuturesTrader
    from ..exchange.models import Position, MarginMode


AUDIT_LOG_PATH = "/opt/aila/logs/trades_audit.log"


class TradesAuditLogger:
    """
    Comprehensive audit logger for trade execution.

    Logs:
    - All bot settings (basic, risk, strategy, entry, TP, SL, filters)
    - Signal verification (ST indicators, EMA, filters)
    - Position verification on exchange with auto-correction
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
        with open(AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{message}\n")

    def log_trade_open(
        self,
        symbol: str,
        side: str,
        bot_settings: dict,
        sent_params: dict,
        order_result: dict,
        signal_data: Optional[dict] = None,
    ) -> dict:
        """
        Log trade open with comprehensive settings and verification.

        Args:
            symbol: Trading pair (e.g., "BTCUSDT")
            side: "LONG" or "SHORT"
            bot_settings: Complete bot configuration dict
            sent_params: Parameters sent to exchange
            order_result: Result from order placement
            signal_data: Signal verification data (ST states, EMA, filters)

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

        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        lines = []

        # Header
        lines.append(f"[TRADE OPEN] {timestamp} {symbol} {side}")
        lines.append("")

        # === ALL BOT SETTINGS ===
        lines.append("=== ОСНОВНЫЕ ===")
        lines.extend(self._format_basic_settings(bot_settings))
        lines.append("")

        lines.append("=== РИСК-МЕНЕДЖМЕНТ ===")
        lines.extend(self._format_risk_settings(bot_settings))
        lines.append("")

        lines.append("=== НАСТРОЙКИ СТРАТЕГИИ ===")
        lines.extend(self._format_strategy_settings(bot_settings))
        lines.append("")

        lines.append("=== ТЕЙК-ПРОФИТ ===")
        lines.extend(self._format_tp_settings(bot_settings, sent_params))
        lines.append("")

        lines.append("=== СТОП-ЛОСС ===")
        lines.extend(self._format_sl_settings(bot_settings, sent_params))
        lines.append("")

        lines.append("=== ФИЛЬТРЫ СИГНАЛОВ ===")
        lines.extend(self._format_ema_filter_settings(bot_settings))
        lines.append("")

        lines.append("=== ФИЛЬТРЫ АКТИВОВ ===")
        lines.extend(self._format_asset_filter_settings(bot_settings))
        lines.append("")

        # === EXCHANGE VERIFICATION ===
        lines.append("=== ПРОВЕРКА НА БИРЖЕ ===")
        if self.trader:
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
            lines.append("⚠️ Нет доступа к trader для верификации")
        lines.append("")

        # === SIGNAL VERIFICATION ===
        if signal_data:
            lines.append("=== ПРОВЕРКА СИГНАЛА ===")
            lines.extend(self._format_signal_verification(signal_data, bot_settings))
            lines.append("")

        # Summary
        if audit_results["errors"]:
            lines.append(f"ИТОГ: ❌ Есть проблемы: {', '.join(audit_results['errors'])}")
            audit_results["success"] = False
        elif audit_results["corrections"]:
            lines.append("ИТОГ: ✅ Все настройки работают корректно после исправлений")
        else:
            lines.append("ИТОГ: ✅ Все настройки работают корректно")

        lines.append("=" * 70)
        lines.append("")

        # Write to log
        self._write_log("\n".join(lines))

        return audit_results

    def _format_basic_settings(self, s: dict) -> list[str]:
        """Format basic bot settings - ALWAYS show all."""
        lines = []

        # Bot name and mode
        bot_name = s.get("bot_name", "N/A")
        mode = s.get("mode", "manual")
        mode_display = "Автопоиск" if mode == "auto" else "Ручной"
        max_pairs = s.get("max_trading_pairs", "N/A")

        lines.append(f"Название бота: {bot_name}")
        lines.append(f"Режим бота: {mode_display}")
        lines.append(f"Макс. торговых пар: {max_pairs}")

        return lines

    def _format_risk_settings(self, s: dict) -> list[str]:
        """Format risk management settings - ALWAYS show all."""
        lines = []

        # Allocated balance / usage
        allocated = s.get("allocated_balance", 0)
        lines.append(f"Использование баланса: {allocated} USDT")

        # Max loss limit
        max_loss_enabled = s.get("max_loss_enabled", False)
        max_loss = s.get("max_daily_loss_percent", 5.0)
        if max_loss_enabled:
            lines.append(f"Лимит потерь %: {max_loss}% (вкл)")
        else:
            lines.append(f"Лимит потерь %: выкл")

        # Consecutive losses limit
        cons_loss_enabled = s.get("consecutive_losses_enabled", False)
        max_cons = s.get("max_consecutive_losses", 3)
        cooldown = s.get("cooldown_after_loss_streak", 60)
        if cons_loss_enabled:
            lines.append(f"Лимит убытков подряд: {max_cons} (пауза {cooldown} мин)")
        else:
            lines.append(f"Лимит убытков подряд: выкл")

        return lines

    def _format_strategy_settings(self, s: dict) -> list[str]:
        """Format strategy entry settings - ALWAYS show all."""
        lines = []

        # Basic strategy settings
        timeframe = s.get("timeframe", "N/A")
        leverage = s.get("leverage", "N/A")
        order_size = s.get("order_size", "N/A")
        margin_mode = s.get("margin_mode", "N/A")

        lines.append(f"Таймфрейм: {timeframe}")
        lines.append(f"Плечо: {leverage}x")

        # Position sizing
        sizing_mode = s.get("position_sizing_mode", "fixed_amount")
        risk_per_trade = s.get("risk_per_trade", "N/A")
        if sizing_mode == "fixed_amount":
            lines.append(f"Размер ордера: {order_size} USDT (Fixed)")
        elif sizing_mode == "risk_percent":
            lines.append(f"Размер позиции: {risk_per_trade}% от баланса")
        else:
            lines.append(f"Размер ордера: {order_size} USDT")

        # Margin mode
        margin_display = "Isolated" if margin_mode and "isolated" in str(margin_mode).lower() else "Cross"
        lines.append(f"Режим маржи: {margin_display}")

        lines.append("")

        # Strategy type
        strategy_type = s.get("strategy_type", "supertrend")

        if strategy_type == "heikinashi":
            lines.append("=== СИГНАЛ ВХОДА (Heikin Ashi) ===")
            ha_entry = s.get("ha_entry_candles", 2)
            ha_exit = s.get("ha_exit_on_color_change", True)
            ha_exit_candles = s.get("ha_exit_candles", 1)
            emergency_sl = s.get("ha_emergency_sl_percent", 5.0)
            lines.append(f"Подтверждение входа: {ha_entry} свечей")
            lines.append(f"Выход по смене цвета: {'вкл' if ha_exit else 'выкл'}")
            lines.append(f"Подтверждение выхода: {ha_exit_candles} свечей")
            lines.append(f"Аварийный SL: {emergency_sl}%")
        else:
            lines.append("=== СИГНАЛ ВХОДА (SuperTrend) ===")

            # ST1
            st1_period = s.get("st1_period", 10)
            st1_mult = s.get("st1_multiplier", 1.0)
            st1_role = s.get("st1_role", "confirm")
            st1_role_display = self._role_display(st1_role)
            lines.append(f"ST1: {st1_role_display} (период={st1_period}, множитель={st1_mult})")

            # ST2
            st2_period = s.get("st2_period", 11)
            st2_mult = s.get("st2_multiplier", 2.0)
            st2_role = s.get("st2_role", "confirm")
            st2_role_display = self._role_display(st2_role)
            lines.append(f"ST2: {st2_role_display} (период={st2_period}, множитель={st2_mult})")

            # ST3
            st3_period = s.get("st3_period", 12)
            st3_mult = s.get("st3_multiplier", 3.0)
            st3_role = s.get("st3_role", "trigger")
            st3_role_display = self._role_display(st3_role)
            lines.append(f"ST3: {st3_role_display} (период={st3_period}, множитель={st3_mult})")

            # Confirm candles
            confirm_candles = s.get("trigger_confirm_candles", 1)
            lines.append(f"Подтверждение свечей: {confirm_candles}")

            # Trigger confirm
            trigger_confirm = s.get("trigger_confirm_mode", "close")
            lines.append(f"Подтверждение триггера: {trigger_confirm}")

        return lines

    def _role_display(self, role: str) -> str:
        """Convert role code to display name."""
        roles = {
            "trigger": "Триггер",
            "confirm": "Подтверждение",
            "off": "Выкл"
        }
        return roles.get(role, role)

    def _format_tp_settings(self, s: dict, sent_params: dict) -> list[str]:
        """Format take-profit settings - ALWAYS show all."""
        lines = []

        tp_mode = s.get("tp_mode", "risk_ratio")
        tp_value = sent_params.get("take_profit", "N/A")
        tp_rr = s.get("tp_risk_ratio", 2.0)
        tp_percent = s.get("tp_fixed_percent", 4.0)

        # TP Mode
        if tp_mode == "risk_ratio" or tp_mode == "rr":
            lines.append(f"Режим TP: R:R {tp_rr}:1")
        elif tp_mode == "fixed_percent":
            lines.append(f"Режим TP: Фиксированный {tp_percent}%")
        else:
            lines.append(f"Режим TP: {tp_mode}")

        lines.append(f"Значение TP: {tp_value}")

        # Trailing TP - ALWAYS show
        trailing_tp_enabled = s.get("trailing_tp_enabled", False)
        trailing_mode = s.get("trailing_tp_mode", "st_line")
        trailing_st_line = s.get("trailing_tp_st_line", 2)
        trailing_activation = s.get("trailing_tp_activation", 0.5)
        trailing_step = s.get("trailing_tp_step", 1.0)

        lines.append(f"Trailing TP: {'вкл' if trailing_tp_enabled else 'выкл'}")
        if trailing_tp_enabled:
            lines.append(f"- Trailing режим: {trailing_mode}")
            if trailing_mode == "st_line":
                st_names = {1: "ST1 (быстрый)", 2: "ST2 (средний)", 3: "ST3 (медленный)"}
                lines.append(f"- Trailing ST линия: {st_names.get(trailing_st_line, trailing_st_line)}")
            else:
                lines.append(f"- Trailing активация: {trailing_activation}%")
                lines.append(f"- Trailing шаг: {trailing_step}%")

        # Partial TP - ALWAYS show
        partial_tp_enabled = s.get("partial_tp_enabled", False)
        partial_close_percent = s.get("partial_tp_close_percent", 50)
        partial_sl_move = s.get("partial_tp_sl_move", "entry")

        lines.append(f"Partial TP: {'вкл' if partial_tp_enabled else 'выкл'}")
        if partial_tp_enabled:
            sl_move_display = "на TP1" if partial_sl_move == "tp1" else "на безубыток"
            lines.append(f"- Partial % закрытия: {partial_close_percent}%")
            lines.append(f"- Partial SL: {sl_move_display}")

        return lines

    def _format_sl_settings(self, s: dict, sent_params: dict) -> list[str]:
        """Format stop-loss settings - ALWAYS show all."""
        lines = []

        sl_mode = s.get("sl_mode", "supertrend_line")
        sl_value = sent_params.get("stop_loss", "N/A")
        sl_line = s.get("sl_supertrend_line", 2)
        sl_percent = s.get("sl_fixed_percent", 2.0)

        # SL Mode
        if sl_mode == "supertrend_line":
            st_names = {1: "ST1 (быстрый)", 2: "ST2 (средний)", 3: "ST3 (медленный)"}
            lines.append(f"Режим SL: SuperTrend линия {st_names.get(sl_line, sl_line)}")
        elif sl_mode == "fixed_percent":
            lines.append(f"Режим SL: Фиксированный {sl_percent}%")
        else:
            lines.append(f"Режим SL: {sl_mode}")

        lines.append(f"Значение SL: {sl_value}")

        return lines

    def _format_ema_filter_settings(self, s: dict) -> list[str]:
        """Format EMA filter settings - ALWAYS show all."""
        lines = []

        ema_enabled = s.get("ema_enabled", False)
        ema_period = s.get("ema_period", 200)
        ema_mode = s.get("ema_filter_mode", "strict")
        mode_display = "строгий" if ema_mode == "strict" else "мягкий"

        lines.append(f"EMA фильтр: {'вкл' if ema_enabled else 'выкл'}")
        if ema_enabled:
            lines.append(f"- EMA период: {ema_period}")
            lines.append(f"- EMA режим: {mode_display}")

        return lines

    def _format_asset_filter_settings(self, s: dict) -> list[str]:
        """Format asset filter settings - ALWAYS show all."""
        lines = []

        asset_filters_enabled = s.get("asset_filters_enabled", False)
        lines.append(f"Фильтр активов: {'вкл' if asset_filters_enabled else 'выкл'}")

        if asset_filters_enabled:
            # Volume
            vol_min = s.get("volume_24h_min", 0)
            vol_max = s.get("volume_24h_max", 0)
            lines.append(f"- Объём 24ч мин: {self._format_volume(vol_min)}")
            lines.append(f"- Объём 24ч макс: {self._format_volume(vol_max)}")

            # Price
            price_min = s.get("price_min", 0)
            price_max = s.get("price_max", 0)
            lines.append(f"- Цена мин: {price_min}$" if price_min > 0 else "- Цена мин: не задан")
            lines.append(f"- Цена макс: {price_max}$" if price_max > 0 else "- Цена макс: не задан")

            # Change %
            change_min = s.get("change_24h_min", 0)
            change_max = s.get("change_24h_max", 0)
            lines.append(f"- Изм. % мин: {change_min}%" if change_min != 0 else "- Изм. % мин: не задан")
            lines.append(f"- Изм. % макс: {change_max}%" if change_max != 0 else "- Изм. % макс: не задан")

            # Volatility
            vol_period = s.get("volatility_period", 0)
            vol_min_pct = s.get("volatility_min", 0)
            vol_max_pct = s.get("volatility_max", 0)
            lines.append(f"- Период волат.: {vol_period} свечей" if vol_period > 0 else "- Период волат.: не задан")
            lines.append(f"- Волат. мин %: {vol_min_pct}%" if vol_min_pct > 0 else "- Волат. мин %: не задан")
            lines.append(f"- Волат. макс %: {vol_max_pct}%" if vol_max_pct > 0 else "- Волат. макс %: не задан")

        return lines

    def _format_volume(self, value: float) -> str:
        """Format volume value."""
        if value <= 0:
            return "не задан"
        if value >= 1_000_000_000:
            return f"{value / 1_000_000_000:.1f}B"
        if value >= 1_000_000:
            return f"{value / 1_000_000:.1f}M"
        if value >= 1_000:
            return f"{value / 1_000:.1f}K"
        return str(value)

    def _format_signal_verification(self, signal_data: dict, settings: dict) -> list[str]:
        """Format signal verification results."""
        lines = []

        strategy_type = settings.get("strategy_type", "supertrend")

        if strategy_type == "heikinashi":
            # Heikin Ashi verification
            ha_confirmed = signal_data.get("ha_confirmed", False)
            ha_candles = signal_data.get("ha_candles_count", 0)
            required = settings.get("ha_entry_candles", 2)

            if ha_confirmed:
                lines.append(f"✅ Heikin Ashi: {ha_candles} свечей подтверждения (>= {required})")
            else:
                lines.append(f"⚠️ Heikin Ashi: {ha_candles} свечей (нужно {required})")
        else:
            # SuperTrend verification
            st1_state = signal_data.get("st1_state", {})
            st2_state = signal_data.get("st2_state", {})
            st3_state = signal_data.get("st3_state", {})
            side = signal_data.get("side", "LONG")

            # ST1
            st1_role = settings.get("st1_role", "confirm")
            if st1_role != "off":
                st1_ok = st1_state.get("aligned", False)
                st1_dir = "выше" if st1_state.get("direction", 0) > 0 else "ниже"
                icon = "✅" if st1_ok else "❌"
                lines.append(f"{icon} ST1 ({self._role_display(st1_role)}): цена {st1_dir} линии")

            # ST2
            st2_role = settings.get("st2_role", "confirm")
            if st2_role != "off":
                st2_ok = st2_state.get("aligned", False)
                st2_dir = "выше" if st2_state.get("direction", 0) > 0 else "ниже"
                icon = "✅" if st2_ok else "❌"
                lines.append(f"{icon} ST2 ({self._role_display(st2_role)}): цена {st2_dir} линии")

            # ST3
            st3_role = settings.get("st3_role", "trigger")
            if st3_role != "off":
                st3_ok = st3_state.get("aligned", False)
                st3_dir = "выше" if st3_state.get("direction", 0) > 0 else "ниже"
                icon = "✅" if st3_ok else "❌"
                if st3_role == "trigger":
                    triggered = st3_state.get("just_triggered", False)
                    trigger_status = "сработал" if triggered else "подтвердил"
                    lines.append(f"{icon} ST3 ({self._role_display(st3_role)}): {trigger_status}")
                else:
                    lines.append(f"{icon} ST3 ({self._role_display(st3_role)}): цена {st3_dir} линии")

        # EMA filter
        ema_enabled = settings.get("ema_enabled", False)
        if ema_enabled:
            # If trade opened, EMA filter passed by definition (otherwise signal would be rejected)
            # Default to True for safety
            ema_ok = signal_data.get("ema_filter_passed", True)
            ema_period = settings.get("ema_period", 200)
            ema_position = signal_data.get("ema_position", "unknown")
            icon = "✅" if ema_ok else "❌"
            lines.append(f"{icon} EMA {ema_period}: цена {ema_position}")

        # Asset filters
        asset_filters = signal_data.get("asset_filters", {})

        # Volume
        if "volume_24h" in asset_filters:
            vol = asset_filters["volume_24h"]
            vol_ok = vol.get("passed", True)
            vol_value = vol.get("value", 0)
            vol_min = settings.get("volume_24h_min", 0)
            vol_display = self._format_volume(vol_value)
            vol_min_display = self._format_volume(vol_min)
            icon = "✅" if vol_ok else "❌"
            lines.append(f"{icon} Объём 24ч: {vol_display} > {vol_min_display} (мин)")

        # Price
        if "price" in asset_filters:
            price = asset_filters["price"]
            price_ok = price.get("passed", True)
            price_value = price.get("value", 0)
            icon = "✅" if price_ok else "❌"
            lines.append(f"{icon} Цена: {price_value}$")

        # Change
        if "change_24h" in asset_filters:
            change = asset_filters["change_24h"]
            change_ok = change.get("passed", True)
            change_value = change.get("value", 0)
            icon = "✅" if change_ok else "❌"
            lines.append(f"{icon} Изменение 24ч: {change_value:.2f}%")

        # Volatility
        if "volatility" in asset_filters:
            vol = asset_filters["volatility"]
            vol_ok = vol.get("passed", True)
            vol_value = vol.get("value", 0)
            icon = "✅" if vol_ok else "❌"
            min_v = settings.get("volatility_min", 0)
            max_v = settings.get("volatility_max", 0)
            range_str = f"{min_v}-{max_v}%" if max_v > 0 else f">{min_v}%"
            if vol_ok:
                lines.append(f"{icon} Волатильность: {vol_value:.2f}% в диапазоне {range_str}")
            else:
                lines.append(f"{icon} Волатильность: {vol_value:.2f}% (вне диапазона {range_str})")

        return lines

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
                    lines.append(f"✅ Плечо: {actual_leverage}x")
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
                    lines.append(f"✅ Маржа: {actual_mode}")
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
                if actual_tp and abs(float(actual_tp) - float(expected_tp)) < float(expected_tp) * 0.001:
                    lines.append(f"✅ TP: {actual_tp}")
                    audit_results["checks"].append(("take_profit", "OK"))
                else:
                    tp_status = f"на бирже {actual_tp}" if actual_tp else "отсутствует"
                    lines.append(f"❌ TP: ожидалось {expected_tp}, {tp_status} → ИСПРАВЛЯЮ...")
                    try:
                        self.trader.update_take_profit(symbol, expected_tp)
                        lines.append(f"   ✅ TP исправлен на {expected_tp}")
                        audit_results["corrections"].append(("take_profit", str(expected_tp)))
                    except Exception as e:
                        lines.append(f"   🚨 КРИТИЧЕСКАЯ ОШИБКА: Не удалось выставить TP! {e}")
                        audit_results["errors"].append(f"tp_fix_failed: {e}")
            else:
                lines.append("⚠️ TP: не задан в настройках")

            # Check Stop Loss
            if expected_sl:
                actual_sl = position.stop_loss
                if actual_sl and abs(float(actual_sl) - float(expected_sl)) < float(expected_sl) * 0.001:
                    lines.append(f"✅ SL: {actual_sl}")
                    audit_results["checks"].append(("stop_loss", "OK"))
                else:
                    sl_status = f"на бирже {actual_sl}" if actual_sl else "отсутствует"
                    lines.append(f"❌ SL: ожидалось {expected_sl}, {sl_status} → ИСПРАВЛЯЮ...")
                    try:
                        self.trader.update_stop_loss(symbol, expected_sl)
                        lines.append(f"   ✅ SL исправлен на {expected_sl}")
                        audit_results["corrections"].append(("stop_loss", str(expected_sl)))
                    except Exception as e:
                        lines.append(f"   🚨 КРИТИЧЕСКАЯ ОШИБКА: Не удалось выставить SL! {e}")
                        audit_results["errors"].append(f"sl_fix_failed: {e}")
            else:
                lines.append("⚠️ SL: не задан в настройках")

            # Check quantity (can't be corrected, just log)
            if expected_qty:
                actual_qty = position.size
                qty_diff_percent = abs(float(actual_qty) - float(expected_qty)) / float(expected_qty) * 100 if float(expected_qty) > 0 else 0
                if qty_diff_percent < 1:
                    lines.append(f"✅ Размер: {actual_qty}")
                    audit_results["checks"].append(("quantity", "OK"))
                else:
                    lines.append(f"⚠️ Размер: округлён биржей ({expected_qty} → {actual_qty})")
                    audit_results["checks"].append(("quantity", "ROUNDED"))

        except Exception as e:
            lines.append(f"🚨 Ошибка при проверке позиции: {e}")
            audit_results["errors"].append(f"verification_error: {e}")

        return lines

    def log_critical_error(self, symbol: str, message: str):
        """Log critical error that needs attention."""
        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        self._write_log(f"[{timestamp}] 🚨 КРИТИЧЕСКАЯ ОШИБКА: {message} для {symbol}!")


# Global instance for easy access
_audit_logger: Optional[TradesAuditLogger] = None


def get_audit_logger(trader: Optional["FuturesTrader"] = None) -> TradesAuditLogger:
    """Get or create audit logger instance."""
    global _audit_logger
    if _audit_logger is None or (trader and _audit_logger.trader != trader):
        _audit_logger = TradesAuditLogger(trader)
    return _audit_logger
