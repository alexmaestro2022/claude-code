"""
3 EMA Pullback Scalping Strategy

Scalping strategy based on pullbacks in trend using 3 EMAs (50/100/150).

LONG logic:
1. Uptrend: EMA50 > EMA100 > EMA150, positive slope, EMAs spread apart
2. Pullback: price goes below EMA50
3. Test: price touches EMA100 as support, doesn't break
4. Forbid: pullback does NOT touch EMA150
5. Entry: first candle that opens above EMA50
6. SL: below pullback low + buffer
7. TP: R:R 1.5 or trailing (BE + trail on new highs)

SHORT logic: Mirror of LONG.
"""

import logging
from datetime import datetime, date
from typing import Optional
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

        # Check slope
        slope_period = self.settings["slope_candles"]
        slope_min = self.settings["slope_min"]

        if len(ema50) < slope_period + 1:
            return False, "Not enough data for slope calculation"

        ema50_slope = (ema50[idx] - ema50[idx - slope_period]) / ema50[idx - slope_period] * 100
        ema100_slope = (ema100[idx] - ema100[idx - slope_period]) / ema100[idx - slope_period] * 100

        if direction == "LONG":
            slopes_ok = ema50_slope > slope_min and ema100_slope > slope_min
        else:
            slopes_ok = ema50_slope < -slope_min and ema100_slope < -slope_min

        if not slopes_ok:
            return False, f"Slope not met: EMA50 slope={ema50_slope:.3f}%, EMA100 slope={ema100_slope:.3f}%, min={slope_min}%"

        # Check EMA distance (not flat)
        atr = self.calculate_atr(candles, self.settings["atr_period"])
        dist_min = atr * self.settings["ema_distance_atr_mult"]

        dist_50_100 = abs(ema50[idx] - ema100[idx])
        dist_100_150 = abs(ema100[idx] - ema150[idx])

        if dist_50_100 < dist_min or dist_100_150 < dist_min:
            return False, f"EMAs too close: dist(50-100)={dist_50_100:.4f}, dist(100-150)={dist_100_150:.4f}, min={dist_min:.4f}"

        log_audit(f"[{direction}] Trend OK: {order_desc}, slopes: EMA50={ema50_slope:.3f}%, EMA100={ema100_slope:.3f}%")
        return True, "Trend confirmed"

    def detect_pullback(self, candles: list, ema50: np.ndarray, direction: str) -> list:
        """
        Detect pullback candles where price went beyond EMA50.
        Returns list of pullback candles.
        """
        pullback_candles = []
        lookback = min(20, len(candles) - 1)

        for i in range(-lookback, 0):
            candle = candles[i]
            ema50_val = ema50[i]

            if direction == "LONG":
                # Price below EMA50
                if candle["low"] < ema50_val or candle["close"] < ema50_val:
                    pullback_candles.append({
                        "candle": candle,
                        "index": i,
                        "ema50": ema50_val
                    })
            else:  # SHORT
                # Price above EMA50
                if candle["high"] > ema50_val or candle["close"] > ema50_val:
                    pullback_candles.append({
                        "candle": candle,
                        "index": i,
                        "ema50": ema50_val
                    })

        if pullback_candles:
            log_audit(f"[{direction}] Pullback detected: {len(pullback_candles)} candles beyond EMA50")

        return pullback_candles

    def check_ema100_test(self, pullback_candles: list, ema100: np.ndarray,
                          direction: str) -> tuple[bool, str]:
        """
        Check if price tested EMA100 during pullback.
        Returns (tested, details).
        """
        touch_tol = self.settings["touch_tolerance"]  # %
        touch_mode = self.settings["touch_mode"]

        for pb in pullback_candles:
            candle = pb["candle"]
            idx = pb["index"]
            ema100_val = ema100[idx]

            if direction == "LONG":
                touch_level = ema100_val * (1 + touch_tol / 100)

                if touch_mode == "low_high":
                    touched = candle["low"] <= touch_level
                else:  # close
                    touched = candle["close"] <= touch_level

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

                if touched and candle["close"] <= ema100_val:
                    log_audit(f"[{direction}] EMA100 tested: high={candle['high']:.4f}, "
                              f"ema100={ema100_val:.4f}, tolerance={touch_tol}%")
                    return True, f"Touched at {candle['high']:.4f}"

        return False, "EMA100 not tested during pullback"

    def check_ema150_not_touched(self, pullback_candles: list, ema150: np.ndarray,
                                  direction: str) -> tuple[bool, str]:
        """
        Check that EMA150 was NOT touched during pullback.
        Returns (not_touched, details).
        """
        if not self.settings["forbid_ema150_touch"]:
            return True, "EMA150 check disabled"

        for pb in pullback_candles:
            candle = pb["candle"]
            idx = pb["index"]
            ema150_val = ema150[idx]

            if direction == "LONG":
                if candle["low"] <= ema150_val:
                    return False, f"Pullback touched EMA150 at {candle['low']:.4f}"
            else:  # SHORT
                if candle["high"] >= ema150_val:
                    return False, f"Pullback touched EMA150 at {candle['high']:.4f}"

        log_audit(f"[{direction}] EMA150 not touched during pullback - OK")
        return True, "EMA150 not touched"

    def check_entry_trigger(self, current_candle: dict, ema50_current: float,
                            direction: str) -> tuple[bool, str]:
        """
        Check if entry trigger is met (candle opens beyond EMA50 in trend direction).
        Returns (triggered, details).
        """
        open_price = current_candle["open"]

        if direction == "LONG":
            triggered = open_price > ema50_current
            desc = f"open({open_price:.4f}) > EMA50({ema50_current:.4f})"
        else:  # SHORT
            triggered = open_price < ema50_current
            desc = f"open({open_price:.4f}) < EMA50({ema50_current:.4f})"

        if triggered:
            log_audit(f"[{direction}] Entry trigger: {desc}")

        return triggered, desc

    def calculate_sl(self, pullback_candles: list, entry_price: float,
                     direction: str) -> float:
        """Calculate stop loss price."""
        buffer_pct = self.settings["sl_buffer"]

        if self.settings["sl_mode"] == "pullback":
            if direction == "LONG":
                pullback_low = min(pb["candle"]["low"] for pb in pullback_candles)
                sl = pullback_low * (1 - buffer_pct / 100)
                log_audit(f"[{direction}] SL calculated: pullback_low={pullback_low:.4f}, "
                          f"buffer={buffer_pct}%, SL={sl:.4f}")
            else:  # SHORT
                pullback_high = max(pb["candle"]["high"] for pb in pullback_candles)
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
            else:
                new_sl = ema50_current * (1 + buffer / 100)
                if new_sl < current_sl:
                    log_audit(f"[{direction}] SL trailed to EMA50: {new_sl:.4f}")
                    return new_sl

        return current_sl

    def check_risk_limits(self) -> tuple[bool, str]:
        """Check if risk limits allow trading."""
        today = date.today()

        # Reset daily counters if new day
        if self.settings.get("last_reset_date") != str(today):
            self.settings["daily_loss"] = 0.0
            self.settings["daily_trades"] = 0
            self.settings["last_reset_date"] = str(today)

        # Check daily loss limit
        if self.settings["daily_loss_limit_enabled"]:
            if self.settings["daily_loss"] >= self.settings["daily_loss_limit_pct"]:
                return False, f"Daily loss limit reached: {self.settings['daily_loss']:.2f}%"

        # Check losing streak
        if self.settings["max_losing_streak_enabled"]:
            if self.settings["losing_streak"] >= self.settings["max_losing_streak"]:
                return False, f"Max losing streak reached: {self.settings['losing_streak']}"

        # Check max daily trades
        if self.settings["max_daily_trades_enabled"]:
            if self.settings["daily_trades"] >= self.settings["max_daily_trades"]:
                return False, f"Max daily trades reached: {self.settings['daily_trades']}"

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

        # Check risk limits
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

            # 2. Detect pullback
            pullback = self.detect_pullback(candles, ema50, direction)
            if not pullback:
                log_audit(f"[{direction}] SKIP: No pullback detected")
                continue

            # 3. Check EMA100 test
            ema100_tested, test_msg = self.check_ema100_test(pullback, ema100, direction)
            if not ema100_tested:
                log_audit(f"[{direction}] SKIP: {test_msg}")
                continue

            # 4. Check EMA150 not touched
            ema150_ok, ema150_msg = self.check_ema150_not_touched(pullback, ema150, direction)
            if not ema150_ok:
                log_audit(f"[{direction}] SKIP: {ema150_msg}")
                continue

            # 5. Entry trigger
            triggered, trigger_msg = self.check_entry_trigger(current_candle, ema50[-1], direction)
            if not triggered:
                log_audit(f"[{direction}] SKIP: Entry trigger not met - {trigger_msg}")
                continue

            # 6. ALL CONDITIONS MET - GENERATE SIGNAL
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
                "pullback_candles": len(pullback),
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
✅ Slope: confirmed
✅ EMAs spread: distance > {self.settings['ema_distance_atr_mult']}x ATR
✅ Pullback: price was {'below' if direction=='LONG' else 'above'} EMA50
✅ EMA100 test: {'low' if direction=='LONG' else 'high'} touched EMA100
✅ EMA150: not touched
✅ Trigger: open {'>' if direction=='LONG' else '<'} EMA50

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
        self.settings["daily_trades"] += 1

        if pnl_pct < 0:
            self.settings["daily_loss"] += abs(pnl_pct)
            self.settings["losing_streak"] += 1
            log_audit(f"Trade closed with loss: {pnl_pct:.2f}%, daily_loss={self.settings['daily_loss']:.2f}%, "
                      f"losing_streak={self.settings['losing_streak']}")
        else:
            self.settings["losing_streak"] = 0
            log_audit(f"Trade closed with profit: {pnl_pct:.2f}%, losing_streak reset")
