"""
WHALE TRACKER — monitors large players (whales).
Whales move the market, we follow them.
"""

import json
import logging
import aiohttp
from datetime import datetime
from .base_agent import BaseAgent
from ..api_keys import API_KEYS

logger = logging.getLogger("ai_trade")


class WhaleTrackerAgent(BaseAgent):
    """
    Whale tracker agent - monitors large transactions and exchange flows.
    Detects accumulation/distribution phases and orderbook walls.
    """

    def __init__(self, claude_client, knowledge_base, exchange=None):
        super().__init__(
            name="WHALE_TRACKER",
            claude_client=claude_client,
            knowledge_base=knowledge_base,
            log_path="/opt/aila/logs/ai_trade/whale_tracker.log"
        )
        self.exchange = exchange

        # Whale thresholds
        self.thresholds = {
            "BTC": 100,         # 100+ BTC = whale
            "ETH": 1000,        # 1000+ ETH = whale
            "USDT": 1_000_000,  # $1M+ USDT = whale
            "SOL": 10000,       # 10K+ SOL = whale
        }

        # API endpoints
        self.whale_alert_api = "https://api.whale-alert.io/v1"

    async def think(self, context: dict) -> dict:
        """Process whale tracking context."""
        pair = context.get("pair")
        if not pair:
            return {"error": "No pair provided"}
        return await self.get_whale_signal(pair)

    async def get_recent_whale_transactions(self, hours: int = 24) -> list:
        """
        Get large transactions in the last N hours.
        Sources: Whale Alert API.
        """
        transactions = []
        api_key = API_KEYS.get("whale_alert", "")

        if not api_key:
            self.log("Whale Alert API key not configured", "warning")
            return transactions

        try:
            now = int(datetime.now().timestamp())
            start = now - (hours * 3600)

            async with aiohttp.ClientSession() as session:
                url = f"{self.whale_alert_api}/transactions"
                params = {
                    "api_key": api_key,
                    "min_value": 500000,  # Min $500K
                    "start": start,
                    "limit": 100,
                }
                async with session.get(url, params=params, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        raw_txs = data.get("transactions", [])

                        for tx in raw_txs:
                            transactions.append({
                                "hash": tx.get("hash", ""),
                                "symbol": tx.get("symbol", "").upper(),
                                "amount": tx.get("amount", 0),
                                "amount_usd": tx.get("amount_usd", 0),
                                "from": tx.get("from", {}).get("owner_type", "unknown"),
                                "to": tx.get("to", {}).get("owner_type", "unknown"),
                                "timestamp": tx.get("timestamp", 0),
                            })

                        self.log(f"Found {len(transactions)} whale transactions in last {hours}h")
                    else:
                        self.log(f"Whale Alert API error: {resp.status}", "error")

        except Exception as e:
            self.log(f"Error fetching whale transactions: {e}", "error")

        return transactions

    async def analyze_exchange_flows(self, pair: str) -> dict:
        """
        Analyze exchange inflows/outflows:
        - Large inflow to exchange = preparing to sell (bearish)
        - Large outflow from exchange = preparing to hold (bullish)
        """
        base_asset = pair.replace("USDT", "").replace("/USDT", "").replace(":USDT", "")

        # Get recent whale transactions
        transactions = await self.get_recent_whale_transactions(hours=24)

        # Filter for this asset
        asset_txs = [t for t in transactions if t.get("symbol") == base_asset]

        # Calculate flows
        exchange_inflow = sum(
            t["amount_usd"] for t in asset_txs
            if t.get("to") == "exchange"
        )
        exchange_outflow = sum(
            t["amount_usd"] for t in asset_txs
            if t.get("from") == "exchange"
        )

        net_flow = exchange_inflow - exchange_outflow

        if not asset_txs:
            # No data — use AI analysis
            prompt = f"""Analyze likely exchange flows for {base_asset} based on current market conditions.

Consider:
- Current price action and volume
- Typical whale behavior in this market phase
- Historical patterns for {base_asset}

Respond in JSON:
{{
    "net_flow": "inflow" | "outflow" | "neutral",
    "estimated_amount_usd": number,
    "signal": "bullish" | "bearish" | "neutral",
    "confidence": 0-100,
    "interpretation": "explanation"
}}"""
            return await self.claude_client.analyze(prompt)

        signal = "neutral"
        if net_flow > 100000:
            signal = "bearish"  # Money flowing to exchanges = sell pressure
        elif net_flow < -100000:
            signal = "bullish"  # Money leaving exchanges = accumulation

        return {
            "net_flow": "inflow" if net_flow > 0 else "outflow",
            "amount_usd": abs(net_flow),
            "inflow_usd": exchange_inflow,
            "outflow_usd": exchange_outflow,
            "transactions_count": len(asset_txs),
            "signal": signal,
            "confidence": min(80, 30 + len(asset_txs) * 5),
            "interpretation": f"Net {'inflow' if net_flow > 0 else 'outflow'} of ${abs(net_flow):,.0f}",
        }

    async def detect_accumulation(self, pair: str, market_data: dict = None) -> dict:
        """
        Detect accumulation/distribution phase by whales.
        Accumulation = whales buying = bullish
        Distribution = whales selling = bearish
        """
        base_asset = pair.replace("USDT", "").replace("/USDT", "")

        context = ""
        if market_data:
            context = f"""
Current Price: {market_data.get('price')}
RSI: {market_data.get('rsi')}
Volume trend: {market_data.get('volume_trend', 'unknown')}
Price change 24h: {market_data.get('change_24h')}%
"""

        prompt = f"""Determine the accumulation/distribution phase for {base_asset}.

MARKET DATA:
{context}

ACCUMULATION SIGNS:
- Price in range but volume increasing
- Large buys on dips
- Exchange outflow increasing
- OTC deals increasing

DISTRIBUTION SIGNS:
- Price rising but volume declining
- Large sells on pumps
- Exchange inflow increasing
- Whale wallets transferring to exchanges

Respond in JSON:
{{
    "phase": "accumulation" | "distribution" | "neutral",
    "confidence": 0-100,
    "whale_activity": "low" | "medium" | "high",
    "evidence": ["evidence1", "evidence2"],
    "recommendation": "explanation",
    "expected_move": "up" | "down" | "sideways"
}}"""
        result = await self.claude_client.analyze(prompt)

        if "error" not in result:
            self.log(f"{pair} phase: {result.get('phase')} (confidence={result.get('confidence')}%)")

        return result

    async def analyze_orderbook_whales(self, pair: str) -> dict:
        """
        Find large walls in the orderbook.
        Large wall = resistance/support level.
        """
        if not self.exchange:
            return {"error": "Exchange not configured"}

        try:
            symbol = pair if "/" in pair else pair.replace("USDT", "/USDT")
            orderbook = await self.exchange.fetch_order_book(symbol, limit=100)

            asks = orderbook.get("asks", [])
            bids = orderbook.get("bids", [])

            if not asks or not bids:
                return {"resistance_walls": [], "support_walls": [], "imbalance": "neutral"}

            # Calculate average sizes
            avg_ask_size = sum(a[1] for a in asks[:50]) / max(len(asks[:50]), 1)
            avg_bid_size = sum(b[1] for b in bids[:50]) / max(len(bids[:50]), 1)

            # Find abnormally large orders (5x average)
            large_asks = []
            for price, size in asks[:100]:
                if avg_ask_size > 0 and size > avg_ask_size * 5:
                    large_asks.append({"price": price, "size": size, "usd_value": price * size})

            large_bids = []
            for price, size in bids[:100]:
                if avg_bid_size > 0 and size > avg_bid_size * 5:
                    large_bids.append({"price": price, "size": size, "usd_value": price * size})

            total_bid_walls = sum(b["usd_value"] for b in large_bids)
            total_ask_walls = sum(a["usd_value"] for a in large_asks)

            imbalance = "neutral"
            if total_bid_walls > total_ask_walls * 1.5:
                imbalance = "buyers"
            elif total_ask_walls > total_bid_walls * 1.5:
                imbalance = "sellers"

            self.log(f"{pair} orderbook: {len(large_bids)} support walls, "
                     f"{len(large_asks)} resistance walls, imbalance={imbalance}")

            return {
                "resistance_walls": large_asks[:5],  # Top 5
                "support_walls": large_bids[:5],
                "imbalance": imbalance,
                "bid_wall_total_usd": total_bid_walls,
                "ask_wall_total_usd": total_ask_walls,
            }

        except Exception as e:
            self.log(f"Orderbook analysis error for {pair}: {e}", "error")
            return {"resistance_walls": [], "support_walls": [], "imbalance": "neutral"}

    async def get_whale_signal(self, pair: str, market_data: dict = None) -> dict:
        """
        Combined whale signal.
        Merges exchange flows, accumulation, and orderbook data.
        """
        self.log(f"Getting whale signal for {pair}")

        exchange_flow = await self.analyze_exchange_flows(pair)
        accumulation = await self.detect_accumulation(pair, market_data)
        orderbook = await self.analyze_orderbook_whales(pair)

        prompt = f"""Give a final whale activity signal for {pair}:

EXCHANGE FLOWS:
{json.dumps(exchange_flow, indent=2)}

ACCUMULATION/DISTRIBUTION:
{json.dumps(accumulation, indent=2)}

ORDERBOOK WALLS:
{json.dumps(orderbook, indent=2)}

Respond in JSON:
{{
    "whale_signal": "strong_buy" | "buy" | "neutral" | "sell" | "strong_sell",
    "confidence": 0-100,
    "reasoning": "explanation",
    "key_levels": {{
        "whale_support": price or null,
        "whale_resistance": price or null
    }},
    "whale_activity_level": "low" | "medium" | "high",
    "risk_from_whales": "low" | "medium" | "high"
}}"""
        result = await self.claude_client.analyze(prompt)

        if "error" not in result:
            self.log(f"{pair} whale signal: {result.get('whale_signal')} "
                     f"(confidence={result.get('confidence')}%)")

        return result
