"""
HUNTER Agent - Detects market inefficiencies after liquidation cascades.

Strategy: "Охота на ликвидации"
- Enter AFTER liquidation cascades, not before
- R:R minimum 1:5
- Only A+ setups
- Редкие сделки, но точные

Triggers:
1. liquidation_cascade - массовые ликвидации + extreme RSI
2. funding_flip - резкая смена funding rate
3. extreme_fear - F&G < 15 + RSI < 20
4. oi_divergence - OI растёт, цена падает (или наоборот)
"""

import asyncio
from datetime import datetime, timedelta
from typing import Optional
from .base_agent import BaseAgent
from ..agent_settings import get_agent_settings


class HunterAgent(BaseAgent):
    """
    HUNTER - ловит развороты после ликвидаций.

    Философия:
    - 80% времени ЖДАТЬ
    - Только A+ сетапы
    - R:R минимум 1:5
    - Вход ПОСЛЕ ликвидаций
    """

    __slots__ = (
        "scanner",
        "_cache",
        "_agent_stats",
        "_hunt_cooldowns",
        "_triggers",
        "_last_scan",
        "_pending_hunts",
        "_liquidation_history",
        "_settings",
    )

    def __init__(
        self,
        claude_client,
        knowledge_base,
        scanner=None,
        agent_stats=None,
    ):
        super().__init__(
            name="HUNTER",
            claude_client=claude_client,
            knowledge_base=knowledge_base,
            log_path="/opt/aila/logs/ai_trade/hunter.log",
        )

        self.scanner = scanner
        self._cache = {}
        self._agent_stats = agent_stats or {}
        self._hunt_cooldowns = {}  # pair -> datetime (когда можно снова)
        self._last_scan = None
        self._pending_hunts = []
        self._liquidation_history = []  # История ликвидаций для анализа

        # Load settings from hunter_settings.json
        self._settings = get_agent_settings().get_settings("HUNTER")

        # Триггеры HUNTER
        self._triggers = {
            "liquidation_cascade": self._check_liquidation_cascade,
            "funding_flip": self._check_funding_flip,
            "extreme_fear": self._check_extreme_fear,
            # "oi_divergence": self._check_oi_divergence,  # DISABLED - not part of liquidation strategy
        }

        self.log("HUNTER agent initialized — waiting for A+ setups only")

    # ==================== MAIN SCAN ====================

    async def scan(self, pairs: list[str]) -> list[dict]:
        """
        Сканирует рынок на A+ сетапы.
        Возвращает список сигналов (обычно 0-1, редко больше).
        """
        signals = []
        self._last_scan = datetime.utcnow()

        for pair in pairs:
            # Проверяем cooldown
            if self._is_on_cooldown(pair):
                continue

            try:
                # Получаем данные
                market_data = await self._get_market_data(pair)
                if not market_data:
                    continue

                # Проверяем все триггеры
                for trigger_name, trigger_func in self._triggers.items():
                    result = await trigger_func(pair, market_data)

                    if result and result.get("triggered"):
                        signal = self._build_signal(pair, trigger_name, result, market_data)
                        if signal:
                            signals.append(signal)
                            self.log(f"🎯 A+ SETUP: {pair} via {trigger_name} — {signal['direction']} @ {signal['confidence']}%")
                            # Только один сигнал на пару
                            break

            except Exception as e:
                self.log(f"Error scanning {pair}: {e}", level="error")

        return signals

    # ==================== TRIGGERS ====================

    async def _check_liquidation_cascade(self, pair: str, data: dict) -> dict:
        """
        Триггер: Каскадные ликвидации.

        Условия для LONG:
        - Цена упала >5% за 2 часа
        - RSI < rsi_oversold (из настроек)
        - Funding отрицательный
        - Fear & Greed < fear_greed_extreme_low (из настроек)

        Условия для SHORT (обратные):
        - Цена выросла >5% за 2 часа
        - RSI > rsi_overbought
        - Funding > 0.05%
        - Fear & Greed > fear_greed_extreme_high
        """
        # Use change_24h if change_2h not available
        change_2h = data.get("change_2h") or data.get("change_24h", 0) or 0
        rsi = data.get("rsi") or 50  # Default 50 if None
        funding = data.get("funding_rate") or 0
        fear_greed = data.get("fear_greed") or 50

        # Get thresholds from settings
        liq_threshold = self._settings.get("liquidation_threshold_pct", 5.0)
        rsi_oversold = self._settings.get("rsi_oversold", 20)
        rsi_overbought = self._settings.get("rsi_overbought", 80)
        fg_low = self._settings.get("fear_greed_extreme_low", 15)
        fg_high = self._settings.get("fear_greed_extreme_high", 85)
        funding_high = self._settings.get("funding_extreme_high", 0.05)

        # LONG после массовых ликвидаций лонгов
        if (change_2h <= -liq_threshold and
            rsi < rsi_oversold and
            funding < 0 and
            fear_greed < fg_low):

            return {
                "triggered": True,
                "direction": "LONG",
                "reason": f"Liquidation cascade: -{abs(change_2h):.1f}% drop, RSI={rsi:.0f}, F&G={fear_greed}",
                "confidence_boost": 15,
                "urgency": "high",
            }

        # SHORT после массовых ликвидаций шортов
        if (change_2h >= liq_threshold and
            rsi > rsi_overbought and
            funding > funding_high and
            fear_greed > fg_high):

            return {
                "triggered": True,
                "direction": "SHORT",
                "reason": f"Liquidation cascade: +{change_2h:.1f}% pump, RSI={rsi:.0f}, F&G={fear_greed}",
                "confidence_boost": 15,
                "urgency": "high",
            }

        return {"triggered": False}

    async def _check_funding_flip(self, pair: str, data: dict) -> dict:
        """
        Триггер: Резкая смена Funding Rate.

        Логика:
        - Funding был экстремально высоким → упал до нуля/отрицательного
        - Это значит лонги ликвиднулись
        - Вход в LONG
        """
        funding = data.get("funding_rate") or 0
        funding_prev = data.get("funding_rate_prev") or funding
        rsi = data.get("rsi") or 50

        # Get thresholds from settings
        funding_high = self._settings.get("funding_extreme_high", 0.05)
        funding_low = self._settings.get("funding_extreme_low", -0.03)

        # Funding флипнулся с положительного на отрицательный
        if funding_prev > funding_high and funding < 0 and rsi < 35:
            return {
                "triggered": True,
                "direction": "LONG",
                "reason": f"Funding flip: {funding_prev:.3f}% → {funding:.3f}%, RSI={rsi:.0f}",
                "confidence_boost": 10,
                "urgency": "medium",
            }

        # Funding флипнулся с отрицательного на положительный
        if funding_prev < funding_low and funding > 0 and rsi > 65:
            return {
                "triggered": True,
                "direction": "SHORT",
                "reason": f"Funding flip: {funding_prev:.3f}% → {funding:.3f}%, RSI={rsi:.0f}",
                "confidence_boost": 10,
                "urgency": "medium",
            }

        return {"triggered": False}

    async def _check_extreme_fear(self, pair: str, data: dict) -> dict:
        """
        Триггер: Extreme Fear + Oversold.

        Условия:
        - Fear & Greed < fear_greed_extreme_low (из настроек)
        - RSI < rsi_oversold (из настроек)
        - Цена ниже EMA200
        - Volume spike > 1.5x (снижен с 2.0 для большей чувствительности)
        """
        fear_greed = data.get("fear_greed") or 50
        rsi = data.get("rsi") or 50
        price = data.get("price") or 0
        ema200 = data.get("ema200") or price or 1

        # Get volume_ratio from volume_profile if available
        vol_profile = data.get("volume_profile") or {}
        volume_ratio = vol_profile.get("ratio") or data.get("volume_ratio") or 1.0

        # Get thresholds from settings
        fg_low = self._settings.get("fear_greed_extreme_low", 15)
        fg_high = self._settings.get("fear_greed_extreme_high", 85)
        rsi_oversold = self._settings.get("rsi_oversold", 20)
        rsi_overbought = self._settings.get("rsi_overbought", 80)

        # Extreme fear + oversold = LONG (volume spike 1.5x instead of 2.0x)
        if (fear_greed < fg_low and
            rsi < rsi_oversold and
            price < ema200 * 0.95 and
            volume_ratio > 1.5):

            return {
                "triggered": True,
                "direction": "LONG",
                "reason": f"Extreme fear: F&G={fear_greed}, RSI={rsi:.0f}, price {((price/ema200-1)*100):.1f}% below EMA200, vol_spike={volume_ratio:.1f}x",
                "confidence_boost": 12,
                "urgency": "medium",
            }

        # Extreme greed + overbought = SHORT
        if (fear_greed > fg_high and
            rsi > rsi_overbought and
            price > ema200 * 1.10 and
            volume_ratio > 1.5):

            return {
                "triggered": True,
                "direction": "SHORT",
                "reason": f"Extreme greed: F&G={fear_greed}, RSI={rsi:.0f}, price {((price/ema200-1)*100):.1f}% above EMA200, vol_spike={volume_ratio:.1f}x",
                "confidence_boost": 12,
                "urgency": "medium",
            }

        return {"triggered": False}

    async def _check_oi_divergence(self, pair: str, data: dict) -> dict:
        """
        Триггер: Дивергенция Open Interest и цены.

        Логика:
        - OI растёт, цена падает → шорты накапливаются → потенциальный short squeeze
        - OI растёт, цена растёт → лонги накапливаются → потенциальный long squeeze
        """
        oi_change = data.get("oi_change_pct") or 0
        price_change = data.get("change_24h") or 0
        rsi = data.get("rsi") or 50

        # Get threshold from settings
        oi_threshold = self._settings.get("oi_change_threshold_pct", 5.0)

        # OI растёт, цена падает — шорты накапливаются
        if (oi_change > oi_threshold and
            price_change < -3 and
            rsi < 30):

            return {
                "triggered": True,
                "direction": "LONG",
                "reason": f"OI divergence: OI +{oi_change:.1f}%, price {price_change:.1f}% — short squeeze potential",
                "confidence_boost": 8,
                "urgency": "low",
            }

        # OI растёт, цена растёт — лонги накапливаются
        if (oi_change > oi_threshold and
            price_change > 5 and
            rsi > 70):

            return {
                "triggered": True,
                "direction": "SHORT",
                "reason": f"OI divergence: OI +{oi_change:.1f}%, price +{price_change:.1f}% — long squeeze potential",
                "confidence_boost": 8,
                "urgency": "low",
            }

        return {"triggered": False}

    # ==================== SIGNAL BUILDING ====================

    def _build_signal(self, pair: str, trigger: str, result: dict, data: dict) -> Optional[dict]:
        """
        Строит сигнал с R:R минимум из настроек.
        """
        direction = result["direction"]
        price = data.get("price") or 0
        atr = data.get("atr") or (price * 0.02)  # Fallback 2%

        if not price or price <= 0:
            return None

        # Рассчитываем SL и TP
        sl_pct, tp_pct = self._calc_sl_tp(data, direction)

        if direction == "LONG":
            stop_loss = price * (1 - sl_pct / 100)
            take_profit = price * (1 + tp_pct / 100)
        else:
            stop_loss = price * (1 + sl_pct / 100)
            take_profit = price * (1 - tp_pct / 100)

        # Проверяем R:R
        risk = abs(price - stop_loss)
        reward = abs(take_profit - price)
        rr_ratio = reward / risk if risk > 0 else 0

        min_rr = self._settings.get("min_rr_ratio", 5.0)
        if rr_ratio < min_rr:
            self.log(f"Skip {pair}: R:R {rr_ratio:.1f} < {min_rr}")
            return None

        # Confidence
        base_confidence = 70
        confidence = min(95, base_confidence + result.get("confidence_boost", 0))

        signal = {
            "pair": pair,
            "symbol": pair.replace("/", ""),
            "direction": direction,
            "entry_price": price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "confidence": confidence,
            "source": "HUNTER",
            "trigger": trigger,
            "reason": result.get("reason", ""),
            "rr_ratio": rr_ratio,
            "sl_pct": sl_pct,
            "tp_pct": tp_pct,
            "urgency": result.get("urgency", "medium"),
            "timestamp": datetime.utcnow().isoformat(),

            # HUNTER специфичные параметры для управления позицией
            "partial_close_1": {"pct": 30, "at_profit": 5.0},   # 30% при +5%
            "partial_close_2": {"pct": 30, "at_profit": 8.0},   # ещё 30% при +8%
            "trailing_start": 5.0,    # Trailing с +5%
            "trailing_distance": 2.0,  # Trailing distance 2%
            "breakeven_at": 3.0,      # BE при +3%
        }

        return signal

    def _calc_sl_tp(self, data: dict, direction: str) -> tuple[float, float]:
        """
        Рассчитывает SL и TP для R:R из настроек.

        Использует ATR для динамического SL.
        """
        atr_pct = data.get("atr_pct") or 2.0

        # Get limits from settings
        min_sl = self._settings.get("min_sl_pct", 2.0)
        max_sl = self._settings.get("max_sl_pct", 3.0)
        min_rr = self._settings.get("min_rr_ratio", 5.0)
        min_tp = self._settings.get("min_tp_pct", 10.0)

        # SL = ATR × 1.5, но в пределах MIN/MAX
        sl_pct = max(min_sl, min(max_sl, atr_pct * 1.5))

        # TP = SL × R:R ratio
        tp_pct = sl_pct * min_rr

        # Минимум из настроек
        tp_pct = max(min_tp, tp_pct)

        return sl_pct, tp_pct

    # ==================== HELPERS ====================

    async def _get_market_data(self, pair: str) -> Optional[dict]:
        """Получает данные рынка через scanner."""
        if not self.scanner:
            return None

        try:
            # Use scanner's get_market_data method
            data = await self.scanner.get_market_data(pair)
            return data
        except Exception as e:
            self.log(f"Error getting data for {pair}: {e}", level="error")
            return None

    def _is_on_cooldown(self, pair: str) -> bool:
        """Проверяет cooldown после сделки."""
        if pair not in self._hunt_cooldowns:
            return False

        cooldown_until = self._hunt_cooldowns[pair]
        if datetime.utcnow() < cooldown_until:
            return True

        del self._hunt_cooldowns[pair]
        return False

    def set_cooldown(self, pair: str, hours: int = 4):
        """Устанавливает cooldown на пару после сделки."""
        self._hunt_cooldowns[pair] = datetime.utcnow() + timedelta(hours=hours)
        self.log(f"Cooldown set for {pair}: {hours} hours")

    def get_max_leverage(self) -> int:
        """HUNTER использует минимальное плечо для безопасности."""
        return 2  # Максимум 2x

    def reload_settings(self) -> dict:
        """Reload settings from hunter_settings.json without restart."""
        self._settings = get_agent_settings().get_settings("HUNTER")
        self.log(f"Settings reloaded: rsi_oversold={self._settings.get('rsi_oversold')}, "
                 f"fg_low={self._settings.get('fear_greed_extreme_low')}")
        return self._settings

    def get_current_settings(self) -> dict:
        """Return current settings for debugging."""
        return {
            "rsi_oversold": self._settings.get("rsi_oversold", 20),
            "rsi_overbought": self._settings.get("rsi_overbought", 80),
            "fear_greed_extreme_low": self._settings.get("fear_greed_extreme_low", 15),
            "fear_greed_extreme_high": self._settings.get("fear_greed_extreme_high", 85),
            "liquidation_threshold_pct": self._settings.get("liquidation_threshold_pct", 5.0),
            "min_rr_ratio": self._settings.get("min_rr_ratio", 5.0),
        }

    # ==================== THINK (для Claude) ====================

    async def think(self, context: dict) -> dict:
        """
        Анализ ситуации через Claude для подтверждения A+ сетапа.
        """
        prompt = f"""
You are HUNTER - a patient trader who waits for A+ setups after liquidation cascades.

CURRENT SITUATION:
{context}

HUNTER RULES:
1. Only enter AFTER liquidation cascades, never before
2. Minimum R:R 1:5
3. Small position size (max 2x leverage)
4. Wait for confirmation - first green candle for LONG, first red for SHORT
5. If unsure, SKIP - there will be other opportunities

Should we take this trade?

Respond in JSON:
{{
    "action": "ENTER" or "SKIP",
    "confidence": 0-100,
    "reasoning": "brief explanation",
    "adjustments": {{
        "entry": null or adjusted price,
        "stop_loss": null or adjusted SL,
        "take_profit": null or adjusted TP
    }}
}}
"""

        try:
            response = await self.claude_client.ask(prompt, max_tokens=500)
            # Parse response...
            return {"action": "SKIP", "confidence": 0}  # Default safe
        except Exception as e:
            self.log(f"Think error: {e}", level="error")
            return {"action": "SKIP", "confidence": 0}


# Экспорт
__all__ = ["HunterAgent"]
