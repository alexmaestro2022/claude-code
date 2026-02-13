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

# HUNTER параметры — консервативные для R:R 1:5
MIN_SL_PERCENT = 2.0      # Максимальный SL
MAX_SL_PERCENT = 3.0      # Для волатильных
MIN_RR_RATIO = 5.0        # Минимум 1:5
MIN_TP_PERCENT = 10.0     # Минимум +10% тейк профит

# Пороги для триггеров
LIQUIDATION_THRESHOLD = 5.0      # Падение >5% за 2 часа
FUNDING_EXTREME_HIGH = 0.05      # Funding > 0.05% — перегрет
FUNDING_EXTREME_LOW = -0.03      # Funding < -0.03% — перепродан
FEAR_GREED_EXTREME_LOW = 15      # Extreme fear
FEAR_GREED_EXTREME_HIGH = 85     # Extreme greed
RSI_OVERSOLD = 20                # RSI для LONG
RSI_OVERBOUGHT = 80              # RSI для SHORT
OI_CHANGE_THRESHOLD = 5.0        # Изменение OI >5%


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

        # Триггеры HUNTER
        self._triggers = {
            "liquidation_cascade": self._check_liquidation_cascade,
            "funding_flip": self._check_funding_flip,
            "extreme_fear": self._check_extreme_fear,
            "oi_divergence": self._check_oi_divergence,
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
        - RSI < 20
        - Funding отрицательный
        - Fear & Greed < 20

        Условия для SHORT (обратные):
        - Цена выросла >5% за 2 часа
        - RSI > 80
        - Funding > 0.05%
        - Fear & Greed > 80
        """
        change_2h = data.get("change_2h", 0)
        rsi = data.get("rsi", 50)
        funding = data.get("funding_rate", 0)
        fear_greed = data.get("fear_greed", 50)
        liquidation_pressure = data.get("liquidation_pressure", "NEUTRAL")

        # LONG после массовых ликвидаций лонгов
        if (change_2h <= -LIQUIDATION_THRESHOLD and
            rsi < RSI_OVERSOLD and
            funding < 0 and
            fear_greed < FEAR_GREED_EXTREME_LOW):

            return {
                "triggered": True,
                "direction": "LONG",
                "reason": f"Liquidation cascade: -{abs(change_2h):.1f}% drop, RSI={rsi:.0f}, F&G={fear_greed}",
                "confidence_boost": 15,
                "urgency": "high",
            }

        # SHORT после массовых ликвидаций шортов
        if (change_2h >= LIQUIDATION_THRESHOLD and
            rsi > RSI_OVERBOUGHT and
            funding > FUNDING_EXTREME_HIGH and
            fear_greed > FEAR_GREED_EXTREME_HIGH):

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
        funding = data.get("funding_rate", 0)
        funding_prev = data.get("funding_rate_prev", funding)  # Предыдущий funding
        rsi = data.get("rsi", 50)

        # Funding флипнулся с положительного на отрицательный
        if funding_prev > FUNDING_EXTREME_HIGH and funding < 0 and rsi < 35:
            return {
                "triggered": True,
                "direction": "LONG",
                "reason": f"Funding flip: {funding_prev:.3f}% → {funding:.3f}%, RSI={rsi:.0f}",
                "confidence_boost": 10,
                "urgency": "medium",
            }

        # Funding флипнулся с отрицательного на положительный
        if funding_prev < FUNDING_EXTREME_LOW and funding > 0 and rsi > 65:
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
        - Fear & Greed < 15
        - RSI < 20
        - Цена ниже EMA200
        - Это классический "buy blood" момент
        """
        fear_greed = data.get("fear_greed", 50)
        rsi = data.get("rsi", 50)
        price = data.get("price", 0)
        ema200 = data.get("ema200", price)

        # Extreme fear + oversold = LONG
        if (fear_greed < FEAR_GREED_EXTREME_LOW and
            rsi < RSI_OVERSOLD and
            price < ema200 * 0.95):  # Цена на 5%+ ниже EMA200

            return {
                "triggered": True,
                "direction": "LONG",
                "reason": f"Extreme fear: F&G={fear_greed}, RSI={rsi:.0f}, price {((price/ema200-1)*100):.1f}% below EMA200",
                "confidence_boost": 12,
                "urgency": "medium",
            }

        # Extreme greed + overbought = SHORT
        if (fear_greed > FEAR_GREED_EXTREME_HIGH and
            rsi > RSI_OVERBOUGHT and
            price > ema200 * 1.10):  # Цена на 10%+ выше EMA200

            return {
                "triggered": True,
                "direction": "SHORT",
                "reason": f"Extreme greed: F&G={fear_greed}, RSI={rsi:.0f}, price {((price/ema200-1)*100):.1f}% above EMA200",
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
        oi_change = data.get("oi_change_pct", 0)
        price_change = data.get("change_24h", 0)
        rsi = data.get("rsi", 50)

        # OI растёт, цена падает — шорты накапливаются
        if (oi_change > OI_CHANGE_THRESHOLD and
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
        if (oi_change > OI_CHANGE_THRESHOLD and
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
        Строит сигнал с R:R минимум 1:5.
        """
        direction = result["direction"]
        price = data.get("price", 0)
        atr = data.get("atr", price * 0.02)  # Fallback 2%

        if not price or price <= 0:
            return None

        # Рассчитываем SL и TP для R:R 1:5
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

        if rr_ratio < MIN_RR_RATIO:
            self.log(f"Skip {pair}: R:R {rr_ratio:.1f} < {MIN_RR_RATIO}")
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
        Рассчитывает SL и TP для R:R минимум 1:5.

        Использует ATR для динамического SL.
        """
        atr_pct = data.get("atr_pct", 2.0)

        # SL = ATR × 1.5, но в пределах MIN/MAX
        sl_pct = max(MIN_SL_PERCENT, min(MAX_SL_PERCENT, atr_pct * 1.5))

        # TP = SL × 5 (для R:R 1:5)
        tp_pct = sl_pct * MIN_RR_RATIO

        # Минимум 10%
        tp_pct = max(MIN_TP_PERCENT, tp_pct)

        return sl_pct, tp_pct

    # ==================== HELPERS ====================

    async def _get_market_data(self, pair: str) -> Optional[dict]:
        """Получает данные рынка через scanner."""
        if not self.scanner:
            return None

        try:
            # Используем существующий scanner
            data = await self.scanner.scan_pair(pair)
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
