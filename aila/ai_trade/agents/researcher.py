"""
RESEARCHER — explores market, discovers new patterns and strategies.
"""

import json
from .base_agent import BaseAgent


class ResearcherAgent(BaseAgent):
    """
    Researcher agent - analyzes market regimes, finds patterns,
    tests hypotheses on historical data.
    """

    def __init__(self, claude_client, knowledge_base, market_scanner=None):
        super().__init__(
            name="RESEARCHER",
            claude_client=claude_client,
            knowledge_base=knowledge_base,
            log_path="/opt/aila/logs/ai_trade/researcher.log"
        )
        self.scanner = market_scanner
        self.current_regime = None

    async def think(self, context: dict) -> dict:
        """Process research context."""
        action = context.get("action", "regime")
        if action == "regime":
            return await self.analyze_market_regime(context.get("market_data", {}))
        elif action == "patterns":
            return await self.find_new_patterns(context.get("trades", []))
        elif action == "hypothesis":
            return await self.test_hypothesis(
                context.get("hypothesis", ""),
                context.get("data", [])
            )
        return {"error": "Unknown action"}

    async def analyze_market_regime(self, market_data: dict) -> dict:
        """Determine current market regime."""
        self.log("Analyzing market regime...")

        prompt = f"""Analyze the current cryptocurrency market conditions.

MARKET DATA:
{json.dumps(market_data, indent=2)}

Respond STRICTLY in JSON:

{{
    "regime": "trending" | "ranging" | "volatile" | "calm",
    "direction": "bullish" | "bearish" | "neutral",
    "strength": 1-10,
    "risk_level": "low" | "medium" | "high" | "extreme",
    "best_strategy": "which strategy works best now",
    "worst_strategy": "which strategy to avoid",
    "pairs_to_watch": ["best pairs for trading"],
    "pairs_to_avoid": ["pairs to avoid"],
    "expected_duration": "how long this regime may last",
    "key_levels": {{"btc_support": 0, "btc_resistance": 0}},
    "sentiment": "fear" | "neutral" | "greed"
}}
"""
        result = await self.claude_client.analyze(prompt)

        if "error" not in result:
            self.current_regime = result
            self.log(f"Market regime: {result.get('regime')} / {result.get('direction')} "
                     f"(risk={result.get('risk_level')})")

        return result

    async def find_new_patterns(self, historical_trades: list) -> dict:
        """Find new patterns in trade history."""
        if len(historical_trades) < 10:
            return {"message": "Not enough data for pattern analysis", "patterns": []}

        self.log(f"Analyzing {len(historical_trades)} trades for patterns...")

        # Separate winners and losers
        winners = [t for t in historical_trades if t.get("pnl", 0) > 0]
        losers = [t for t in historical_trades if t.get("pnl", 0) <= 0]

        prompt = f"""Analyze these trading results and discover new patterns.

WINNING TRADES ({len(winners)}):
{json.dumps(winners[-15:], indent=2)}

LOSING TRADES ({len(losers)}):
{json.dumps(losers[-15:], indent=2)}

CURRENT MARKET REGIME:
{json.dumps(self.current_regime or {}, indent=2)}

Find patterns and respond in JSON:

{{
    "patterns_discovered": [
        {{
            "name": "pattern name",
            "description": "what it is",
            "conditions": ["condition1", "condition2"],
            "win_rate_estimate": 0-100,
            "best_timeframe": "1m/5m/15m/1h/4h",
            "risk_reward": 1.5,
            "confidence": 0-100
        }}
    ],
    "correlations": [
        {{"factor1": "name", "factor2": "name", "correlation": "positive/negative", "strength": 1-10}}
    ],
    "time_patterns": {{
        "best_hours_utc": [0, 1, 2],
        "worst_hours_utc": [0, 1, 2],
        "best_days": ["Monday", "Tuesday"]
    }},
    "pair_insights": [
        {{"pair": "BTCUSDT", "insight": "description", "recommendation": "action"}}
    ],
    "strategy_effectiveness": {{
        "trending_market": ["strategies that work"],
        "ranging_market": ["strategies that work"],
        "volatile_market": ["strategies that work"]
    }},
    "new_hypotheses": [
        {{"hypothesis": "description", "test_method": "how to test"}}
    ]
}}
"""
        result = await self.claude_client.analyze(prompt)

        if "error" not in result:
            patterns = result.get("patterns_discovered", [])
            self.log(f"Found {len(patterns)} new patterns")

            # Save patterns to knowledge base
            market_patterns = self.knowledge_base.data.get("market_patterns", [])
            for p in patterns:
                if p.get("confidence", 0) >= 60:
                    market_patterns.append({
                        "pattern": p,
                        "discovered_at": "auto",
                        "verified": False,
                    })
            self.knowledge_base.data["market_patterns"] = market_patterns[-100:]
            self.knowledge_base.save()

        return result

    async def test_hypothesis(self, hypothesis: str, data: list) -> dict:
        """Test a hypothesis against historical data."""
        if not hypothesis:
            return {"error": "No hypothesis provided"}

        self.log(f"Testing hypothesis: {hypothesis[:80]}...")

        prompt = f"""Test this trading hypothesis against the provided data.

HYPOTHESIS:
{hypothesis}

DATA SAMPLE ({len(data)} entries):
{json.dumps(data[-20:], indent=2)}

Respond in JSON:

{{
    "hypothesis": "{hypothesis}",
    "supported": true | false,
    "confidence": 0-100,
    "evidence_for": ["supporting evidence"],
    "evidence_against": ["contradicting evidence"],
    "sample_size_adequate": true | false,
    "statistical_significance": "low" | "medium" | "high",
    "recommendation": "adopt" | "reject" | "needs_more_data",
    "modified_hypothesis": "improved version if applicable or null"
}}
"""
        result = await self.claude_client.analyze(prompt)

        if "error" not in result:
            supported = result.get("supported", False)
            confidence = result.get("confidence", 0)
            self.log(f"Hypothesis {'SUPPORTED' if supported else 'REJECTED'} "
                     f"(confidence={confidence}%)")

        return result

    async def get_market_overview(self) -> dict:
        """Get broad market overview for regime analysis."""
        if not self.scanner:
            return {}

        pairs = await self.scanner.get_top_pairs()
        if not pairs:
            return {}

        # Calculate market-wide stats
        total_bullish = sum(1 for p in pairs if p.get("change_24h", 0) > 0)
        total_bearish = sum(1 for p in pairs if p.get("change_24h", 0) < 0)
        avg_change = sum(p.get("change_24h", 0) for p in pairs) / len(pairs) if pairs else 0
        avg_volatility = sum(p.get("volatility", 0) for p in pairs) / len(pairs) if pairs else 0
        total_volume = sum(p.get("volume_24h", 0) for p in pairs)

        return {
            "total_pairs_scanned": len(pairs),
            "bullish_pairs": total_bullish,
            "bearish_pairs": total_bearish,
            "avg_change_24h": round(avg_change, 2),
            "avg_volatility": round(avg_volatility, 2),
            "total_volume_24h": total_volume,
            "top_gainers": sorted(pairs, key=lambda x: x.get("change_24h", 0), reverse=True)[:5],
            "top_losers": sorted(pairs, key=lambda x: x.get("change_24h", 0))[:5],
            "highest_volume": sorted(pairs, key=lambda x: x.get("volume_24h", 0), reverse=True)[:5],
        }

    async def suggest_strategy_for_regime(self, regime: dict) -> dict:
        """Suggest optimal strategy parameters for current regime."""
        regime_type = regime.get("regime", "unknown")
        risk_level = regime.get("risk_level", "medium")

        suggestions = {
            "trending": {
                "strategy": "trend_following",
                "leverage_mult": 1.0,
                "position_size_mult": 1.0,
                "sl_mult": 1.2,  # Wider SL in trends
                "tp_mult": 1.5,  # Larger TP targets
            },
            "ranging": {
                "strategy": "mean_reversion",
                "leverage_mult": 0.7,
                "position_size_mult": 0.8,
                "sl_mult": 0.8,  # Tighter SL
                "tp_mult": 0.8,
            },
            "volatile": {
                "strategy": "breakout",
                "leverage_mult": 0.5,  # Lower leverage in volatility
                "position_size_mult": 0.5,
                "sl_mult": 1.5,  # Much wider SL
                "tp_mult": 2.0,  # Big TP targets
            },
            "calm": {
                "strategy": "wait",
                "leverage_mult": 0.3,
                "position_size_mult": 0.3,
                "sl_mult": 1.0,
                "tp_mult": 1.0,
            },
        }

        base = suggestions.get(regime_type, suggestions["calm"])

        # Adjust for risk level
        if risk_level == "high" or risk_level == "extreme":
            base["leverage_mult"] *= 0.5
            base["position_size_mult"] *= 0.5

        self.log(f"Strategy suggestion for {regime_type}/{risk_level}: {base['strategy']}")
        return base
