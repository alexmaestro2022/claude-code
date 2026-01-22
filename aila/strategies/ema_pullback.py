"""
3 EMA Pullback Scalping Strategy

Scalping strategy based on pullbacks in trend using 3 EMAs (50/100/150).

LONG logic:
1. Uptrend: EMA50 > EMA100 > EMA150, positive slope, EMAs spread apart
2. Pullback: price goes below EMA50 (continuous window)
3. Test: price touches EMA100 as support, doesn't breach (close stays above)
4. Forbid: pullback does NOT BREACH EMA150 (touch OK, breach NOT OK)
5. Entry: FIRST candle that opens above EMA50 (previous candle must be in pullback)
6. SL: below pullback low + buffer
7. TP: R:R 1.5 or trailing (BE + trail on new highs)

SHORT logic: Mirror of LONG.
"""

import logging
from datetime import datetime, date
from typing import Optional, Dict, List, Any
import numpy as np

from .ema_pullback_settings import DEFAULT_EMA_PULLBACK_SETTINGS

# Setup dedicated logger for this strategy
strategy_logger = logging.getLogger("strategy_3ema")
strategy_logger.setLevel(logging.DEBUG)

# File handler for audit log
_file_handler = logging.FileHandler("/opt/aila/logs/strategy_3ema_audit.log")
_file_handler.setFormatter(logging.Formatter(
    "%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
))
strategy_logger.addHandler(_file_handler)


def log_audit(message: str, level: str = "info"):
    """Log to strategy audit file."""
    if level == "debug":
        strategy_logger.debug(message)
    elif level == "warning":
        strategy_logger.warning(message)
    elif level == "error":
        strategy_logger.error(message)
    else:
        strategy_logger.info(message)


class EmaPullbackStrategy:
    """3 EMA Pullback Scalping Strategy implementation."""

    def __init__(self, settings: Optional[dict] = None):
        """Initialize strategy with settings."""
        self.settings = settings or DEFAULT_EMA_PULLBACK_SETTINGS.copy()
        self.positions = {}  # symbol -> position data
        self.pullback_state = {}  # symbol -> pullback tracking

        # Daily stats for risk management
        self.daily_stats = {
            "daily_loss_pct": 0.0,
            "consecutive_losses": 0,
            "trades_today": 0,
            "last_reset_date": None,
        }

        log_audit("="*60)
        log_audit("3 EMA Pullback Strategy initialized")
        log_audit(f"Settings: EMA periods={self.settings['ema_fast']}/{self.settings['ema_medium']}/{self.settings['ema_slow']}")
        log_audit(f"Risk: {self.settings['risk_per_trade']}% per trade, leverage={self.settings['leverage']}x")
        log_audit("="*60)

    def update_settings(self, new_settings: dict):
        """Update strategy settings."""
        self.settings.update(new_settings)
        log_audit(f"Settings updated: {new_settings}")

    def calculate_ema(self, closes: list, period: int) -> np.ndarray:
        """Calculate EMA for given closes and period."""
        closes_arr = np.array(closes, dtype=float)
        ema = np.zeros_like(closes_arr)
        multiplier = 2.0 / (period + 1)

        # Initialize with SMA
        if len(closes_arr) >= period:
            ema[period - 1] = np.mean(closes_arr[:period])

            # Calculate EMA
            for i in range(period, len(closes_arr)):
                ema[i] = (closes_arr[i] - ema[i - 1]) * multiplier + ema[i - 1]

        return ema

    def calculate_atr(self, candles: list, period: int = 14) -> float:
        """Calculate ATR (Average True Range)."""
        if len(candles) < period + 1:
            return 0.0

        true_ranges = []
        for i in range(1, len(candles)):
            high = candles[i]["high"]
            low = candles[i]["low"]
            prev_close = candles[i - 1]["close"]

            tr = max(
                high - low,
                abs(high - prev_close),
                abs(low - prev_close)
            )
            true_ranges.append(tr)

        if len(true_ranges) < period:
            return np.mean(true_ranges) if true_ranges else 0.0

        return np.mean(true_ranges[-period:])

    def calculate_slope_percent(self, ema_values: np.ndarray, period: int) -> float:
        """
        Calculate EMA slope in percent per candle.
        Normalized slope works the same for BTC ($40000) and EURUSD ($1.10).
        """
        if len(ema_values) < period + 1:
            return 0.0

        ema_current = ema_values[-1]
        ema_past = ema_values[-period]

        if ema_past == 0:
            return 0.0

        # Slope as percent change per candle
        slope_pct = ((ema_current - ema_past) / ema_past) * 100 / period
        return slope_pct

    def check_trend(self, candles: list, ema50: np.ndarray, ema100: np.ndarray,
                    ema150: np.ndarray, direction: str) -> tuple[bool, str]:
        """
        Check if trend conditions are met.
        Returns (is_valid, reason).
        """
        idx = -1  # Latest values

        # Check EMA order
        if direction == "LONG":
            ema_order = ema50[idx] > ema100[idx] > ema150[idx]
            order_desc = f"EMA50({ema50[idx]:.2f}) > EMA100({ema100[idx]:.2f}) > EMA150({ema150[idx]:.2f})"
        else:
            ema_order = ema50[idx] < ema100[idx] < ema150[idx]
            order_desc = f"EMA50({ema50[idx]:.2f}) < EMA100({ema100[idx]:.2f}) < EMA150({ema150[idx]:.2f})"

        if not ema_order:
            return False, f"EMA order not met: {order_desc}"

        # Check slope (normalized in percent)
        slope_period = self.settings["slope_candles"]
        slope_min = self.settings["slope_min"]

        if len(ema50) < slope_period + 1:
            return False, "Not enough data for slope calculation"

        ema50_slope = self.calculate_slope_percent(ema50, slope_period)
        ema100_slope = self.calculate_slope_percent(ema100, slope_period)

        if direction == "LONG":
            slopes_ok = ema50_slope > slope_min and ema100_slope > slope_min
        else:
            slopes_ok = ema50_slope < -slope_min and ema100_slope < -slope_min

        if not slopes_ok:
            return False, f"Slope not met: EMA50 slope={ema50_slope:.4f}%/candle, EMA100 slope={ema100_slope:.4f}%/candle, min={slope_min}%"

        # Check EMA distance (not flat) - use last values [-1]
        atr = self.calculate_atr(candles, self.settings["atr_period"])
        dist_min = atr * self.settings["ema_distance_atr_mult"]

        dist_50_100 = abs(ema50[-1] - ema100[-1])
        dist_100_150 = abs(ema100[-1] - ema150[-1])

        if dist_50_100 < dist_min or dist_100_150 < dist_min:
            return False, f"EMAs too close: dist(50-100)={dist_50_100:.4f}, dist(100-150)={dist_100_150:.4f}, min={dist_min:.4f}"

        log_audit(f"[{direction}] Trend OK: {order_desc}, slopes: EMA50={ema50_slope:.4f}%/candle, EMA100={ema100_slope:.4f}%/candle")
        return True, "Trend confirmed"

    def detect_continuous_pullback(self, candles: list, ema50: np.ndarray,
                                    direction: str) -> Dict[str, Any]:
        """
        Detect CONTINUOUS pullback window.

        Returns the LAST continuous pullback:
        - Start: first candle that went beyond EMA50
        - End: candle before the one that opened back beyond EMA50

        Returns dict with 'candles', 'indices', 'start', 'end'
        """
        pullback_candles = []
        pullback_indices = []
        lookback = min(20, len(candles) - 2)  # -2 to leave room for current candle

        pullback_start = None
        pullback_end = None
        in_pullback = False

        # Go backwards from the candle BEFORE current (candles[-2])
        # We're looking for continuous sequence where price was beyond EMA50
        for i in range(len(candles) - 2, max(len(candles) - 2 - lookback, 0), -1):
            candle = candles[i]
            ema50_val = ema50[i]

            if direction == "LONG":
                # Price below EMA50 = in pullback
                candle_in_pullback = candle["close"] < ema50_val or candle["low"] < ema50_val
            else:  # SHORT
                # Price above EMA50 = in pullback
                candle_in_pullback = candle["close"] > ema50_val or candle["high"] > ema50_val

            if candle_in_pullback:
                if not in_pullback:
                    # Start of pullback (going backwards, so this is the END of pullback)
                    pullback_end = i
                    in_pullback = True
                pullback_start = i
                pullback_candles.insert(0, candle)
                pullback_indices.insert(0, i)
            elif in_pullback:
                # Pullback ended (found continuous segment)
                break

        if pullback_candles:
            log_audit(f"[{direction}] Continuous pullback detected: {len(pullback_candles)} candles "
                      f"(indices {pullback_start} to {pullback_end})")

        return {
            "candles": pullback_candles,
            "indices": pullback_indices,
            "start": pullback_start,
            "end": pullback_end
        }

    def check_ema100_test(self, pullback_data: Dict[str, Any], ema100: np.ndarray,
                          direction: str) -> tuple[bool, str]:
        """
        Check if price tested EMA100 during pullback.
        Each candle is compared with EMA100 AT THAT CANDLE's INDEX.

        Returns (tested, details).
        """
        if not pullback_data["candles"]:
            return False, "No pullback candles"

        touch_tol = self.settings["touch_tolerance"]  # %
        touch_mode = self.settings["touch_mode"]

        for i, candle in enumerate(pullback_data["candles"]):
            ema_index = pullback_data["indices"][i]
            ema100_val = ema100[ema_index]

            if direction == "LONG":
                touch_level = ema100_val * (1 + touch_tol / 100)

                if touch_mode == "low_high":
                    touched = candle["low"] <= touch_level
                else:  # close
                    touched = candle["close"] <= touch_level

                # Touched but close stayed above (not breached)
                if touched and candle["close"] >= ema100_val:
                    log_audit(f"[{direction}] EMA100 tested: low={candle['low']:.4f}, "
                              f"ema100={ema100_val:.4f}, tolerance={touch_tol}%")
                    return True, f"Touched at {candle['low']:.4f}"
            else:  # SHORT
                touch_level = ema100_val * (1 - touch_tol / 100)

                if touch_mode == "low_high":
                    touched = candle["high"] >= touch_level
                else:  # close
                    touched = candle["close"] >= touch_level

                # Touched but close stayed below (not breached)
                if touched and candle["close"] <= ema100_val:
                    log_audit(f"[{direction}] EMA100 tested: high={candle['high']:.4f}, "
                              f"ema100={ema100_val:.4f}, tolerance={touch_tol}%")
                    return True, f"Touched at {candle['high']:.4f}"

        return False, "EMA100 not tested during pullback"

    def check_ema100_not_breached(self, pullback_data: Dict[str, Any], ema100: np.ndarray,
                                   direction: str) -> tuple[bool, str]:
        """
        Check that EMA100 was NOT BREACHED during pullback.
        Breach = close beyond EMA100.

        Returns (not_breached, details).
        """
        if not pullback_data["candles"]:
            return True, "No pullback candles"

        for i, candle in enumerate(pullback_data["candles"]):
            ema_index = pullback_data["indices"][i]
            ema100_val = ema100[ema_index]

            if direction == "LONG":
                # For LONG: close should not be BELOW EMA100
                if candle["close"] < ema100_val:
                    return False, f"Pullback breached EMA100: close={candle['close']:.4f} < EMA100={ema100_val:.4f}"
            else:  # SHORT
                # For SHORT: close should not be ABOVE EMA100
                if candle["close"] > ema100_val:
                    return False, f"Pullback breached EMA100: close={candle['close']:.4f} > EMA100={ema100_val:.4f}"

        log_audit(f"[{direction}] EMA100 not breached during pullback - OK")
        return True, "EMA100 not breached"

    def check_ema150_not_breached(self, pullback_data: Dict[str, Any], ema150: np.ndarray,
                                   direction: str) -> tuple[bool, str]:
        """
        Check that EMA150 was NOT BREACHED during pullback.
        Touch is OK, BREACH (shadow beyond EMA150) is NOT OK.

        Returns (not_breached, details).
        """
        if not self.settings["forbid_ema150_touch"]:
            return True, "EMA150 check disabled"

        if not pullback_data["candles"]:
            return True, "No pullback candles"

        for i, candle in enumerate(pullback_data["candles"]):
            ema_index = pullback_data["indices"][i]
            ema150_val = ema150[ema_index]

            if direction == "LONG":
                # For LONG: low should not be BELOW EMA150 (shadow breach)
                if candle["low"] < ema150_val:
                    return False, f"Pullback breached EMA150 at low={candle['low']:.4f} < EMA150={ema150_val:.4f}"
            else:  # SHORT
                # For SHORT: high should not be ABOVE EMA150 (shadow breach)
                if candle["high"] > ema150_val:
                    return False, f"Pullback breached EMA150 at high={candle['high']:.4f} > EMA150={ema150_val:.4f}"

        log_audit(f"[{direction}] EMA150 not breached during pullback - OK")
        return True, "EMA150 not breached"

    def check_entry_trigger(self, candles: list, ema50: np.ndarray,
                            direction: str) -> tuple[bool, str]:
        """
        Check if entry trigger is met:
        - Current candle opens beyond EMA50 in trend direction
        - Previous candle was IN PULLBACK (guarantee FIRST candle after pullback)

        Returns (triggered, details).
        """
        if len(candles) < 2:
            return False, "Not enough candles"

        current = candles[-1]
        previous = candles[-2]
        ema50_current = ema50[-1]
        ema50_previous = ema50[-2]

        if direction == "LONG":
            # Current candle opens ABOVE EMA50
            current_ok = current["open"] > ema50_current
            # Previous candle was IN PULLBACK (close or low below EMA50)
            previous_in_pullback = previous["close"] < ema50_previous or previous["low"] < ema50_previous
            desc = f"current_open({current['open']:.4f}) > EMA50({ema50_current:.4f}), " \
                   f"prev_in_pullback={previous_in_pullback}"
        else:  # SHORT
            # Current candle opens BELOW EMA50
            current_ok = current["open"] < ema50_current
            # Previous candle was IN PULLBACK (close or high above EMA50)
            previous_in_pullback = previous["close"] > ema50_previous or previous["high"] > ema50_previous
            desc = f"current_open({current['open']:.4f}) < EMA50({ema50_current:.4f}), " \
                   f"prev_in_pullback={previous_in_pullback}"

        triggered = current_ok and previous_in_pullback

        if triggered:
            log_audit(f"[{direction}] Entry trigger: FIRST candle after pullback - {desc}")
        elif current_ok and not previous_in_pullback:
            return False, f"Current candle OK but previous was NOT in pullback"

        return triggered, desc

    def calculate_sl(self, pullback_data: Dict[str, Any], entry_price: float,
                     direction: str) -> float:
        """Calculate stop loss price."""
        buffer_pct = self.settings["sl_buffer"]

        if self.settings["sl_mode"] == "pullback":
            if not pullback_data["candles"]:
                # Fallback to fixed SL if no pullback data
                sl_pct = self.settings["sl_fixed_pct"]
                if direction == "LONG":
                    return entry_price * (1 - sl_pct / 100)
                else:
                    return entry_price * (1 + sl_pct / 100)

            if direction == "LONG":
                pullback_low = min(c["low"] for c in pullback_data["candles"])
                sl = pullback_low * (1 - buffer_pct / 100)
                log_audit(f"[{direction}] SL calculated: pullback_low={pullback_low:.4f}, "
                          f"buffer={buffer_pct}%, SL={sl:.4f}")
            else:  # SHORT
                pullback_high = max(c["high"] for c in pullback_data["candles"])
                sl = pullback_high * (1 + buffer_pct / 100)
                log_audit(f"[{direction}] SL calculated: pullback_high={pullback_high:.4f}, "
                          f"buffer={buffer_pct}%, SL={sl:.4f}")
        else:  # fixed
            sl_pct = self.settings["sl_fixed_pct"]
            if direction == "LONG":
                sl = entry_price * (1 - sl_pct / 100)
            else:
                sl = entry_price * (1 + sl_pct / 100)
            log_audit(f"[{direction}] SL calculated: fixed {sl_pct}%, SL={sl:.4f}")

        return sl

    def calculate_tp(self, entry_price: float, sl_price: float,
                     direction: str) -> Optional[float]:
        """Calculate take profit price. Returns None if trailing mode."""
        if self.settings["tp_mode"] == "trailing":
            log_audit(f"[{direction}] TP mode: trailing (no fixed TP)")
            return None

        # Fixed R:R mode
        risk = abs(entry_price - sl_price)
        reward = risk * self.settings["rr_ratio"]

        if direction == "LONG":
            tp = entry_price + reward
        else:
            tp = entry_price - reward

        log_audit(f"[{direction}] TP calculated: R:R={self.settings['rr_ratio']}, "
                  f"risk={risk:.4f}, TP={tp:.4f}")
        return tp

    def calculate_position_size(self, entry_price: float, sl_price: float,
                                 balance: float) -> float:
        """Calculate position size based on risk."""
        risk_pct = self.settings["risk_per_trade"]
        risk_amount = balance * (risk_pct / 100)
        sl_distance_pct = abs(entry_price - sl_price) / entry_price * 100

        if sl_distance_pct == 0:
            return 0

        # Position size = risk_amount / (sl_distance_pct / 100)
        position_value = risk_amount / (sl_distance_pct / 100)

        # Apply leverage
        position_value = position_value * self.settings["leverage"]

        # Apply balance usage limit
        max_position = balance * (self.settings["balance_usage_pct"] / 100) * self.settings["leverage"]
        position_value = min(position_value, max_position)

        # Calculate quantity
        qty = position_value / entry_price

        log_audit(f"Position size: risk={risk_pct}%, risk_amount={risk_amount:.2f}, "
                  f"sl_dist={sl_distance_pct:.2f}%, qty={qty:.6f}")

        return qty

    def update_trailing_sl(self, position: dict, current_candle: dict,
                           ema50_current: float) -> float:
        """Update trailing stop loss."""
        direction = position["direction"]
        current_sl = position["sl"]

        if self.settings["tp_mode"] != "trailing":
            return current_sl

        # Check BE move
        if self.settings["trailing_be_enabled"] and not position.get("be_moved", False):
            current_price = current_candle["close"]
            entry = position["entry_price"]

            if direction == "LONG":
                profit_pct = (current_price - entry) / entry * 100
            else:
                profit_pct = (entry - current_price) / entry * 100

            if profit_pct >= self.settings["be_after_profit_pct"]:
                position["sl"] = entry
                position["be_moved"] = True
                log_audit(f"[{direction}] SL moved to breakeven at {entry:.4f}, profit was {profit_pct:.2f}%")
                return entry

        # Trail SL
        trailing_mode = self.settings["trailing_mode"]
        buffer = self.settings["trailing_buffer"]

        if trailing_mode == "new_high_low":
            if direction == "LONG":
                if current_candle["high"] > position.get("last_high", 0):
                    position["last_high"] = current_candle["high"]
                    new_sl = current_candle["low"] * (1 - buffer / 100)
                    if new_sl > current_sl:
                        log_audit(f"[{direction}] SL trailed to {new_sl:.4f} (new high: {current_candle['high']:.4f})")
                        return new_sl
            else:  # SHORT
                last_low = position.get("last_low", float("inf"))
                if current_candle["low"] < last_low:
                    position["last_low"] = current_candle["low"]
                    new_sl = current_candle["high"] * (1 + buffer / 100)
                    if new_sl < current_sl:
                        log_audit(f"[{direction}] SL trailed to {new_sl:.4f} (new low: {current_candle['low']:.4f})")
                        return new_sl

        elif trailing_mode == "ema50":
            if direction == "LONG":
                new_sl = ema50_current * (1 - buffer / 100)
                if new_sl > current_sl:
                    log_audit(f"[{direction}] SL trailed to EMA50: {new_sl:.4f}")
                    return new_sl
            else:  # SHORT
                new_sl = ema50_current * (1 + buffer / 100)
                if new_sl < current_sl:
                    log_audit(f"[{direction}] SL trailed to EMA50: {new_sl:.4f}")
                    return new_sl

        return current_sl

    def check_risk_limits(self) -> tuple[bool, str]:
        """Check if risk limits allow trading."""
        today = date.today()

        # Reset daily counters if new day
        if self.daily_stats.get("last_reset_date") != str(today):
            self.daily_stats["daily_loss_pct"] = 0.0
            self.daily_stats["trades_today"] = 0
            self.daily_stats["consecutive_losses"] = 0
            self.daily_stats["last_reset_date"] = str(today)
            log_audit(f"Daily stats reset for {today}")

        # Check daily loss limit
        if self.settings.get("daily_loss_limit_enabled", False):
            limit = self.settings.get("daily_loss_limit_pct", 5.0)
            if self.daily_stats["daily_loss_pct"] >= limit:
                msg = f"⛔ Daily loss limit reached: {self.daily_stats['daily_loss_pct']:.2f}% >= {limit}%"
                log_audit(msg)
                return False, msg

        # Check losing streak
        if self.settings.get("max_losing_streak_enabled", False):
            limit = self.settings.get("max_losing_streak", 3)
            if self.daily_stats["consecutive_losses"] >= limit:
                msg = f"⛔ Max losing streak reached: {self.daily_stats['consecutive_losses']} >= {limit}"
                log_audit(msg)
                return False, msg

        # Check max daily trades
        if self.settings.get("max_daily_trades_enabled", False):
            limit = self.settings.get("max_daily_trades", 10)
            if self.daily_stats["trades_today"] >= limit:
                msg = f"⛔ Max daily trades reached: {self.daily_stats['trades_today']} >= {limit}"
                log_audit(msg)
                return False, msg

        return True, "Risk limits OK"

    def process_signal(self, symbol: str, candles: list) -> Optional[dict]:
        """
        Main strategy logic - process candles and generate signal if conditions met.
        Returns signal dict or None.
        """
        if not self.settings.get("enabled", False):
            return None

        if len(candles) < self.settings["ema_slow"] + 20:
            log_audit(f"[{symbol}] Not enough candles: {len(candles)} < {self.settings['ema_slow'] + 20}")
            return None

        # Check risk limits BEFORE processing
        risk_ok, risk_msg = self.check_risk_limits()
        if not risk_ok:
            log_audit(f"[{symbol}] Risk limit: {risk_msg}")
            return None

        # Extract closes
        closes = [c["close"] for c in candles]

        # Calculate EMAs
        ema50 = self.calculate_ema(closes, self.settings["ema_fast"])
        ema100 = self.calculate_ema(closes, self.settings["ema_medium"])
        ema150 = self.calculate_ema(closes, self.settings["ema_slow"])

        current_candle = candles[-1]

        log_audit(f"\n{'='*60}")
        log_audit(f"[{symbol}] Processing signal at {current_candle.get('timestamp', 'N/A')}")
        log_audit(f"Price: O={current_candle['open']:.4f} H={current_candle['high']:.4f} "
                  f"L={current_candle['low']:.4f} C={current_candle['close']:.4f}")
        log_audit(f"EMAs: 50={ema50[-1]:.4f}, 100={ema100[-1]:.4f}, 150={ema150[-1]:.4f}")

        # Check both directions
        for direction in ["LONG", "SHORT"]:
            if direction == "LONG" and not self.settings["trade_long"]:
                continue
            if direction == "SHORT" and not self.settings["trade_short"]:
                continue

            log_audit(f"\n--- Checking {direction} ---")

            # 1. Trend filter
            trend_ok, trend_msg = self.check_trend(candles, ema50, ema100, ema150, direction)
            if not trend_ok:
                log_audit(f"[{direction}] SKIP: {trend_msg}")
                continue

            # 2. Detect CONTINUOUS pullback
            pullback = self.detect_continuous_pullback(candles, ema50, direction)
            if not pullback["candles"]:
                log_audit(f"[{direction}] SKIP: No continuous pullback detected")
                continue

            # 3. Check EMA100 test (touch but not breach)
            ema100_tested, test_msg = self.check_ema100_test(pullback, ema100, direction)
            if not ema100_tested:
                log_audit(f"[{direction}] SKIP: {test_msg}")
                continue

            # 4. Check EMA100 not breached (close stays on correct side)
            ema100_ok, ema100_msg = self.check_ema100_not_breached(pullback, ema100, direction)
            if not ema100_ok:
                log_audit(f"[{direction}] SKIP: {ema100_msg}")
                continue

            # 5. Check EMA150 not breached (shadows don't go beyond)
            ema150_ok, ema150_msg = self.check_ema150_not_breached(pullback, ema150, direction)
            if not ema150_ok:
                log_audit(f"[{direction}] SKIP: {ema150_msg}")
                continue

            # 6. Entry trigger - FIRST candle after pullback
            triggered, trigger_msg = self.check_entry_trigger(candles, ema50, direction)
            if not triggered:
                log_audit(f"[{direction}] SKIP: Entry trigger not met - {trigger_msg}")
                continue

            # 7. ALL CONDITIONS MET - GENERATE SIGNAL
            entry_price = current_candle["close"]  # Enter at current close
            sl = self.calculate_sl(pullback, entry_price, direction)
            tp = self.calculate_tp(entry_price, sl, direction)

            signal = {
                "symbol": symbol,
                "direction": direction,
                "entry_price": entry_price,
                "sl": sl,
                "tp": tp,
                "ema50": ema50[-1],
                "ema100": ema100[-1],
                "ema150": ema150[-1],
                "pullback_candles": len(pullback["candles"]),
                "strategy": "3EMA_PULLBACK",
                "settings": {
                    "rr_ratio": self.settings["rr_ratio"],
                    "sl_mode": self.settings["sl_mode"],
                    "tp_mode": self.settings["tp_mode"],
                    "leverage": self.settings["leverage"],
                    "risk_per_trade": self.settings["risk_per_trade"],
                }
            }

            log_audit(f"""
{'='*60}
SIGNAL {direction} GENERATED
{'='*60}
Symbol: {symbol}

ENTRY CONDITIONS:
✅ Trend: EMA50 {'>' if direction=='LONG' else '<'} EMA100 {'>' if direction=='LONG' else '<'} EMA150
✅ Slope: EMA slopes normalized (% per candle)
✅ EMAs spread: distance > {self.settings['ema_distance_atr_mult']}x ATR
✅ Continuous pullback: {len(pullback['candles'])} candles beyond EMA50
✅ EMA100 test: touched but not breached
✅ EMA100 not breached: close stayed on correct side
✅ EMA150: not breached (shadows OK)
✅ Trigger: FIRST candle opening {'above' if direction=='LONG' else 'below'} EMA50

TRADE PARAMETERS:
Entry: {entry_price:.4f}
SL: {sl:.4f} ({'pullback' if self.settings['sl_mode']=='pullback' else 'fixed'})
TP: {tp if tp else 'Trailing'}
R:R: {self.settings['rr_ratio'] if tp else 'dynamic'}
Risk: {self.settings['risk_per_trade']}%
Leverage: {self.settings['leverage']}x
{'='*60}
""")

            return signal

        return None

    def on_trade_closed(self, pnl_pct: float):
        """Update stats when trade is closed."""
        self.daily_stats["trades_today"] += 1

        if pnl_pct < 0:
            self.daily_stats["daily_loss_pct"] += abs(pnl_pct)
            self.daily_stats["consecutive_losses"] += 1
            log_audit(f"Trade closed with loss: {pnl_pct:.2f}%, "
                      f"daily_loss={self.daily_stats['daily_loss_pct']:.2f}%, "
                      f"consecutive_losses={self.daily_stats['consecutive_losses']}")
        else:
            self.daily_stats["consecutive_losses"] = 0
            log_audit(f"Trade closed with profit: {pnl_pct:.2f}%, consecutive_losses reset")
