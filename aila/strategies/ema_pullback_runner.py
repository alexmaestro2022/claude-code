"""
3 EMA Pullback Strategy Runner

Handles the trading cycle for 3 EMA Pullback strategy:
- Scanning pairs for signals
- Opening positions
- Managing trailing stops
- Position monitoring
"""

import asyncio
import logging
from datetime import datetime, date
from typing import Optional, Dict, List, Any

from .ema_pullback import EmaPullbackStrategy, log_audit

# Setup logger
logger = logging.getLogger("strategy_3ema_runner")


class EmaPullbackRunner:
    """Runner for 3 EMA Pullback strategy."""

    def __init__(self, client, settings: dict):
        """
        Initialize runner.

        Args:
            client: BybitClient instance
            settings: Strategy settings from ema_pullback_settings
        """
        self.client = client
        self.settings = settings
        self.strategy = EmaPullbackStrategy(settings)
        self.running = False
        self._stop_requested = False
        self._main_task = None
        self._trailing_task = None
        self._positions: Dict[str, dict] = {}  # symbol -> position data
        self._add_log = None  # Log callback

        log_audit("EmaPullbackRunner initialized")

    def set_log_callback(self, callback):
        """Set callback for logging to web interface."""
        self._add_log = callback

    def _log(self, message: str):
        """Log message to both file and web interface."""
        log_audit(message)
        if self._add_log:
            self._add_log(f"[3EMA    ] {message}")

    async def start(self) -> bool:
        """Start the strategy runner."""
        if self.running:
            self._log("Runner already running")
            return False

        if not self.settings.get("enabled", False):
            self._log("Strategy is disabled in settings")
            return False

        self._stop_requested = False
        self.running = True

        self._log(f"Starting 3 EMA Pullback Strategy")
        self._log(f"Timeframe: {self.settings.get('timeframe', '1m')}")
        self._log(f"Leverage: {self.settings.get('leverage', 10)}x")
        self._log(f"Max pairs: {self.settings.get('max_pairs', 1)}")

        # Start main loop and trailing loop
        self._main_task = asyncio.create_task(self._main_loop())
        self._trailing_task = asyncio.create_task(self._trailing_loop())

        self._log("Strategy runner started")
        return True

    async def stop(self) -> None:
        """Stop the strategy runner."""
        if not self.running:
            return

        self._log("Stopping strategy runner...")
        self._stop_requested = True
        self.running = False

        # Cancel tasks
        if self._main_task:
            self._main_task.cancel()
            try:
                await self._main_task
            except asyncio.CancelledError:
                pass

        if self._trailing_task:
            self._trailing_task.cancel()
            try:
                await self._trailing_task
            except asyncio.CancelledError:
                pass

        self._log("Strategy runner stopped")

    def update_settings(self, new_settings: dict) -> None:
        """Update strategy settings."""
        self.settings.update(new_settings)
        self.strategy.update_settings(new_settings)
        self._log(f"Settings updated: enabled={self.settings.get('enabled')}")

    async def _main_loop(self) -> None:
        """Main scanning loop."""
        scan_count = 0
        timeframe = self.settings.get("timeframe", "1m")

        # Calculate scan interval based on timeframe
        scan_intervals = {
            "1m": 30,   # Scan every 30 seconds for 1m
            "3m": 60,   # Scan every 1 minute for 3m
            "5m": 120,  # Scan every 2 minutes for 5m
            "15m": 300, # Scan every 5 minutes for 15m
        }
        scan_interval = scan_intervals.get(timeframe, 60)

        while self.running and not self._stop_requested:
            try:
                if self._stop_requested:
                    break

                # Check if strategy is still enabled
                if not self.settings.get("enabled", False):
                    await asyncio.sleep(5)
                    continue

                # Check position limit
                max_pairs = self.settings.get("max_pairs", 1)
                current_positions = len(self._positions)

                if current_positions >= max_pairs:
                    await asyncio.sleep(5)
                    continue

                scan_count += 1

                # Get trading pairs to scan
                pairs_to_scan = await self._get_pairs_to_scan()

                if not pairs_to_scan:
                    self._log(f"Scan #{scan_count}: No pairs to scan")
                    await asyncio.sleep(scan_interval)
                    continue

                self._log(f"Scan #{scan_count}: Scanning {len(pairs_to_scan)} pairs...")

                # Scan each pair
                signals_found = 0
                for symbol in pairs_to_scan:
                    if self._stop_requested:
                        break

                    if symbol in self._positions:
                        continue  # Already have position

                    if current_positions + signals_found >= max_pairs:
                        break  # Max positions reached

                    signal = await self._scan_symbol(symbol)
                    if signal:
                        signals_found += 1
                        await self._execute_signal(signal)

                self._log(f"Scan #{scan_count} complete: {signals_found} signals")

                await asyncio.sleep(scan_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in main loop: {e}")
                self._log(f"Error: {e}")
                await asyncio.sleep(10)

    async def _trailing_loop(self) -> None:
        """Loop for managing trailing stops on open positions."""
        while self.running and not self._stop_requested:
            try:
                if self._stop_requested:
                    break

                if not self._positions:
                    await asyncio.sleep(5)
                    continue

                # Update trailing for each position
                for symbol, position in list(self._positions.items()):
                    if self._stop_requested:
                        break

                    await self._update_trailing(symbol, position)

                await asyncio.sleep(3)  # Check every 3 seconds

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in trailing loop: {e}")
                await asyncio.sleep(10)

    async def _get_pairs_to_scan(self) -> List[str]:
        """Get list of trading pairs to scan."""
        try:
            mode = self.settings.get("mode", "auto_search")

            # Manual mode - only scan the specified trading pair
            if mode == "manual":
                trading_pair = self.settings.get("trading_pair", "")
                if trading_pair:
                    return [trading_pair]
                return []

            # Auto search mode - scan all pairs with good volume
            all_tickers = self.client.get_all_tickers(use_cache=True)
            if not all_tickers:
                self._log("No tickers available")
                return []

            # Filter to USDT pairs with decent volume
            pairs = []
            min_volume = 1_000_000  # $1M minimum 24h volume

            for symbol, ticker in all_tickers.items():
                if not symbol.endswith("USDT"):
                    continue

                # Check volume (ticker is Ticker object, not dict)
                volume_24h = float(ticker.turnover_24h)
                if volume_24h < min_volume:
                    continue

                pairs.append(symbol)

            # Sort by volume descending and limit
            return pairs[:100]

        except Exception as e:
            self._log(f"Error getting pairs: {e}")
            logger.error(f"Error getting pairs: {e}")
            return []

    async def _scan_symbol(self, symbol: str) -> Optional[dict]:
        """
        Scan a symbol for trading signal.

        Args:
            symbol: Trading pair symbol

        Returns:
            Signal dict or None
        """
        try:
            timeframe = self.settings.get("timeframe", "1m")

            # Need enough candles for EMA calculation
            limit = self.settings.get("ema_slow", 150) + 50

            # Get klines (returns DataFrame)
            klines_df = self.client.get_klines(symbol, timeframe, limit=limit)
            if klines_df is None or klines_df.empty or len(klines_df) < limit - 10:
                return None

            # Convert DataFrame to candle list format
            candles = []
            for idx, row in klines_df.iterrows():
                candles.append({
                    "timestamp": idx,  # timestamp is the index
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row["volume"]),
                })

            # Process signal
            self.settings["symbol"] = symbol
            signal = self.strategy.process_signal(symbol, candles)

            return signal

        except Exception as e:
            logger.error(f"Error scanning {symbol}: {e}")
            return None

    async def _execute_signal(self, signal: dict) -> bool:
        """
        Execute a trading signal.

        Args:
            signal: Signal dict from strategy

        Returns:
            True if position opened successfully
        """
        try:
            symbol = signal["symbol"]
            direction = signal["direction"]
            entry_price = signal["entry_price"]
            sl = signal["sl"]
            tp = signal["tp"]

            self._log(f"Executing {direction} signal on {symbol}")
            log_audit(f"Executing signal: {signal}")

            # Get balance
            balance = self.client.get_balance("USDT")
            if not balance:
                self._log(f"Failed to get balance")
                return False

            available_balance = float(balance.available)

            # Calculate position size
            leverage = self.settings.get("leverage", 10)
            order_size_usdt = self.settings.get("order_size_usdt", 10)
            position_mode = self.settings.get("position_size_mode", "fixed")

            if position_mode == "percent":
                balance_usage = self.settings.get("balance_usage_pct", 30) / 100
                position_value = available_balance * balance_usage
            else:
                position_value = order_size_usdt

            # Apply leverage
            position_value = position_value * leverage

            # Calculate quantity
            qty = position_value / entry_price

            # Round to appropriate precision
            qty = round(qty, 3)

            if qty <= 0:
                self._log(f"Invalid quantity: {qty}")
                return False

            # Set leverage and margin mode
            margin_mode = self.settings.get("margin_mode", "isolated")
            self.client.set_leverage(symbol, leverage)
            self.client.set_margin_mode(symbol, margin_mode)

            # Place market order
            side = "Buy" if direction == "LONG" else "Sell"

            order = self.client.place_order(
                symbol=symbol,
                side=side,
                order_type="Market",
                qty=qty,
                reduce_only=False,
            )

            if not order:
                self._log(f"Failed to place order for {symbol}")
                return False

            self._log(f"Order placed: {symbol} {direction} qty={qty}")

            # Set TP/SL
            if tp:
                self.client.set_trading_stop(
                    symbol=symbol,
                    take_profit=str(tp),
                    stop_loss=str(sl),
                    position_idx=0,
                )
            else:
                # Only SL for trailing mode
                self.client.set_trading_stop(
                    symbol=symbol,
                    stop_loss=str(sl),
                    position_idx=0,
                )

            # Register position
            self._positions[symbol] = {
                "symbol": symbol,
                "direction": direction,
                "entry_price": entry_price,
                "qty": qty,
                "sl": sl,
                "tp": tp,
                "be_moved": False,
                "last_high": entry_price if direction == "LONG" else 0,
                "last_low": entry_price if direction == "SHORT" else float("inf"),
                "opened_at": datetime.utcnow(),
                "ema50": signal.get("ema50"),
            }

            self._log(f"Position opened: {symbol} {direction} entry={entry_price:.4f} SL={sl:.4f}")
            log_audit(f"Position registered: {self._positions[symbol]}")

            return True

        except Exception as e:
            logger.error(f"Error executing signal: {e}")
            self._log(f"Error executing signal: {e}")
            return False

    async def _update_trailing(self, symbol: str, position: dict) -> None:
        """
        Update trailing stop for a position.

        Args:
            symbol: Trading pair symbol
            position: Position data dict
        """
        try:
            if self.settings.get("tp_mode") != "trailing":
                return

            # Get current price
            ticker = self.client.get_ticker(symbol)
            if not ticker:
                return

            current_price = float(ticker.get("lastPrice", 0))
            if current_price <= 0:
                return

            direction = position["direction"]
            entry_price = position["entry_price"]
            current_sl = position["sl"]

            # Check for breakeven move
            if self.settings.get("trailing_be_enabled") and not position.get("be_moved"):
                be_threshold = self.settings.get("be_after_profit_pct", 0.3) / 100

                if direction == "LONG":
                    profit_pct = (current_price - entry_price) / entry_price
                    if profit_pct >= be_threshold:
                        new_sl = entry_price
                        if new_sl > current_sl:
                            self._update_sl_on_exchange(symbol, new_sl)
                            position["sl"] = new_sl
                            position["be_moved"] = True
                            self._log(f"{symbol}: SL moved to breakeven {new_sl:.4f}")
                else:
                    profit_pct = (entry_price - current_price) / entry_price
                    if profit_pct >= be_threshold:
                        new_sl = entry_price
                        if new_sl < current_sl:
                            self._update_sl_on_exchange(symbol, new_sl)
                            position["sl"] = new_sl
                            position["be_moved"] = True
                            self._log(f"{symbol}: SL moved to breakeven {new_sl:.4f}")

            # Trail SL
            trailing_mode = self.settings.get("trailing_mode", "new_high_low")
            buffer_pct = self.settings.get("trailing_buffer", 0.1) / 100

            if trailing_mode == "new_high_low":
                if direction == "LONG":
                    if current_price > position.get("last_high", 0):
                        position["last_high"] = current_price

                        # Get recent low for trailing
                        klines = self.client.get_klines(symbol, self.settings.get("timeframe", "1m"), limit=5)
                        if klines:
                            recent_low = min(float(k.get("low", 0)) for k in klines)
                            new_sl = recent_low * (1 - buffer_pct)
                            if new_sl > current_sl:
                                self._update_sl_on_exchange(symbol, new_sl)
                                position["sl"] = new_sl
                                self._log(f"{symbol}: SL trailed to {new_sl:.4f}")
                else:
                    if current_price < position.get("last_low", float("inf")):
                        position["last_low"] = current_price

                        klines = self.client.get_klines(symbol, self.settings.get("timeframe", "1m"), limit=5)
                        if klines:
                            recent_high = max(float(k.get("high", 0)) for k in klines)
                            new_sl = recent_high * (1 + buffer_pct)
                            if new_sl < current_sl:
                                self._update_sl_on_exchange(symbol, new_sl)
                                position["sl"] = new_sl
                                self._log(f"{symbol}: SL trailed to {new_sl:.4f}")

            elif trailing_mode == "ema50":
                # Get current EMA50 value
                klines = self.client.get_klines(symbol, self.settings.get("timeframe", "1m"), limit=60)
                if klines and len(klines) >= 50:
                    closes = [float(k.get("close", 0)) for k in klines]
                    ema50 = self.strategy.calculate_ema(closes, 50)[-1]

                    if direction == "LONG":
                        new_sl = ema50 * (1 - buffer_pct)
                        if new_sl > current_sl:
                            self._update_sl_on_exchange(symbol, new_sl)
                            position["sl"] = new_sl
                            self._log(f"{symbol}: SL trailed to EMA50 {new_sl:.4f}")
                    else:
                        new_sl = ema50 * (1 + buffer_pct)
                        if new_sl < current_sl:
                            self._update_sl_on_exchange(symbol, new_sl)
                            position["sl"] = new_sl
                            self._log(f"{symbol}: SL trailed to EMA50 {new_sl:.4f}")

            # Check if position still exists on exchange
            await self._check_position_closed(symbol)

        except Exception as e:
            logger.error(f"Error updating trailing for {symbol}: {e}")

    def _update_sl_on_exchange(self, symbol: str, new_sl: float) -> bool:
        """Update stop loss on exchange."""
        try:
            result = self.client.set_trading_stop(
                symbol=symbol,
                stop_loss=str(new_sl),
                position_idx=0,
            )
            return result is not None
        except Exception as e:
            logger.error(f"Failed to update SL on exchange: {e}")
            return False

    async def _check_position_closed(self, symbol: str) -> None:
        """Check if position was closed by SL/TP."""
        try:
            position = self.client.get_position(symbol)

            if not position or float(position.get("size", 0)) == 0:
                # Position closed
                if symbol in self._positions:
                    pos_data = self._positions.pop(symbol)
                    self._log(f"Position closed: {symbol}")
                    log_audit(f"Position closed: {symbol}")

                    # Update strategy stats
                    # Note: PnL would need to be calculated from exchange data
                    self.strategy.on_trade_closed(0)  # Placeholder

        except Exception as e:
            logger.error(f"Error checking position {symbol}: {e}")

    def get_positions(self) -> List[dict]:
        """Get list of open positions."""
        return list(self._positions.values())

    def get_stats(self) -> dict:
        """Get runner statistics."""
        return {
            "running": self.running,
            "positions": len(self._positions),
            "settings": {
                "enabled": self.settings.get("enabled"),
                "timeframe": self.settings.get("timeframe"),
                "leverage": self.settings.get("leverage"),
                "max_pairs": self.settings.get("max_pairs"),
            }
        }


# Global runner instance
_runner_instance: Optional[EmaPullbackRunner] = None


def get_runner() -> Optional[EmaPullbackRunner]:
    """Get the global runner instance."""
    return _runner_instance


def set_runner(runner: EmaPullbackRunner) -> None:
    """Set the global runner instance."""
    global _runner_instance
    _runner_instance = runner
