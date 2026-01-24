"""WHALE TRACKER - monitors large players and exchange flows."""

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Optional

import aiohttp

from ...utils.common import TTLCache, retry_async
from ..api_keys import API_KEYS
from .base_agent import BaseAgent

logger = logging.getLogger("ai_trade")

WHALE_ALERT_API = "https://api.whale-alert.io/v1"
WHALE_THRESHOLDS = {"BTC": 100, "ETH": 1000, "USDT": 1_000_000, "SOL": 10000}


class WhaleTrackerAgent(BaseAgent):
    """Monitors whale transactions, exchange flows, and orderbook walls."""

    def __init__(self, claude_client: Any, knowledge_base: Any, exchange: Any = None) -> None:
        super().__init__(
            name="WHALE_TRACKER",
            claude_client=claude_client,
            knowledge_base=knowledge_base,
            log_path="/opt/aila/logs/ai_trade/whale_tracker.log",
        )
        self.exchange = exchange
        self._cache = TTLCache(default_ttl=60.0)
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create shared aiohttp session."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=10)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def think(self, context: dict[str, Any]) -> dict[str, Any]:
        """Process whale tracking context."""
        pair = context.get("pair")
        if not pair:
            return {"error": "No pair provided"}
        return await self.get_whale_signal(pair)

    @retry_async(max_attempts=2, base_delay=2.0)
    async def get_recent_whale_transactions(self, hours: int = 24) -> list[dict[str, Any]]:
        """Get large transactions from Whale Alert API."""
        cache_key = f"whale_txs_{hours}"
        cached = self._cache.get(cache_key, ttl=120.0)
        if cached is not None:
            return cached

        api_key = API_KEYS.get("whale_alert", "")
        if not api_key:
            self.log("Whale Alert API key not configured", "warning")
            return []

        try:
            now = int(datetime.now().timestamp())
            session = await self._get_session()
            async with session.get(
                f"{WHALE_ALERT_API}/transactions",
                params={"api_key": api_key, "min_value": 500000, "start": now - hours * 3600, "limit": 100},
            ) as resp:
                if resp.status != 200:
                    self.log(f"Whale Alert API error: {resp.status}", "error")
                    return []
                data = await resp.json()

            transactions = [
                {
                    "symbol": tx.get("symbol", "").upper(),
                    "amount": tx.get("amount", 0),
                    "amount_usd": tx.get("amount_usd", 0),
                    "from": tx.get("from", {}).get("owner_type", "unknown"),
                    "to": tx.get("to", {}).get("owner_type", "unknown"),
                    "timestamp": tx.get("timestamp", 0),
                }
                for tx in data.get("transactions", [])
            ]
            self._cache.set(cache_key, transactions)
            self.log(f"Found {len(transactions)} whale transactions in last {hours}h")
            return transactions

        except aiohttp.ClientError as e:
            self.log(f"Error fetching whale transactions: {e}", "error")
            return []

    async def analyze_exchange_flows(self, pair: str) -> dict[str, Any]:
        """Analyze exchange inflows/outflows for a pair."""
        base_asset = pair.replace("USDT", "").replace("/USDT", "").replace(":USDT", "")
        transactions = await self.get_recent_whale_transactions(hours=24)
        asset_txs = [t for t in transactions if t.get("symbol") == base_asset]

        if not asset_txs:
            return await self._ai_flow_analysis(base_asset)

        inflow = sum(t["amount_usd"] for t in asset_txs if t.get("to") == "exchange")
        outflow = sum(t["amount_usd"] for t in asset_txs if t.get("from") == "exchange")
        net_flow = inflow - outflow

        signal = "neutral"
        if net_flow > 100000:
            signal = "bearish"
        elif net_flow < -100000:
            signal = "bullish"

        return {
            "net_flow": "inflow" if net_flow > 0 else "outflow",
            "amount_usd": abs(net_flow),
            "inflow_usd": inflow,
            "outflow_usd": outflow,
            "transactions_count": len(asset_txs),
            "signal": signal,
            "confidence": min(80, 30 + len(asset_txs) * 5),
        }

    async def detect_accumulation(self, pair: str, market_data: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """Detect accumulation/distribution phase."""
        base_asset = pair.replace("USDT", "").replace("/USDT", "")
        context = ""
        if market_data:
            context = (
                f"Price: {market_data.get('price')}, RSI: {market_data.get('rsi')}, "
                f"Change 24h: {market_data.get('change_24h')}%"
            )

        prompt = f"""Determine accumulation/distribution phase for {base_asset}.
Market: {context}

Respond in JSON:
{{"phase": "accumulation"|"distribution"|"neutral", "confidence": 0-100,
"whale_activity": "low"|"medium"|"high", "expected_move": "up"|"down"|"sideways"}}"""
        return await self.claude_client.analyze(prompt)

    async def analyze_orderbook_whales(self, pair: str) -> dict[str, Any]:
        """Find large walls in the orderbook."""
        if not self.exchange:
            return {"resistance_walls": [], "support_walls": [], "imbalance": "neutral"}

        try:
            symbol = pair if "/" in pair else pair.replace("USDT", "/USDT")
            orderbook = await self.exchange.fetch_order_book(symbol, limit=100)
            asks = orderbook.get("asks", [])
            bids = orderbook.get("bids", [])

            if not asks or not bids:
                return {"resistance_walls": [], "support_walls": [], "imbalance": "neutral"}

            avg_ask = sum(a[1] for a in asks[:50]) / max(len(asks[:50]), 1)
            avg_bid = sum(b[1] for b in bids[:50]) / max(len(bids[:50]), 1)

            large_asks = [{"price": p, "size": s, "usd_value": p * s} for p, s in asks[:100] if avg_ask > 0 and s > avg_ask * 5]
            large_bids = [{"price": p, "size": s, "usd_value": p * s} for p, s in bids[:100] if avg_bid > 0 and s > avg_bid * 5]

            bid_total = sum(b["usd_value"] for b in large_bids)
            ask_total = sum(a["usd_value"] for a in large_asks)

            imbalance = "neutral"
            if bid_total > ask_total * 1.5:
                imbalance = "buyers"
            elif ask_total > bid_total * 1.5:
                imbalance = "sellers"

            return {
                "resistance_walls": large_asks[:5],
                "support_walls": large_bids[:5],
                "imbalance": imbalance,
                "bid_wall_total_usd": bid_total,
                "ask_wall_total_usd": ask_total,
            }
        except Exception as e:
            self.log(f"Orderbook analysis error for {pair}: {e}", "error")
            return {"resistance_walls": [], "support_walls": [], "imbalance": "neutral"}

    async def get_whale_signal(self, pair: str, market_data: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """Combined whale signal from all sources (parallel)."""
        cache_key = f"whale_signal:{pair}"
        cached = self._cache.get(cache_key, ttl=60.0)
        if cached is not None:
            return cached

        self.log(f"Getting whale signal for {pair}")
        results = await asyncio.gather(
            self.analyze_exchange_flows(pair),
            self.detect_accumulation(pair, market_data),
            self.analyze_orderbook_whales(pair),
            return_exceptions=True,
        )
        exchange_flow = results[0] if not isinstance(results[0], Exception) else {}
        accumulation = results[1] if not isinstance(results[1], Exception) else {}
        orderbook = results[2] if not isinstance(results[2], Exception) else {}

        prompt = f"""Final whale signal for {pair}:
FLOWS: {json.dumps(exchange_flow)}
ACCUMULATION: {json.dumps(accumulation)}
ORDERBOOK: {json.dumps(orderbook)}

Respond in JSON:
{{"whale_signal": "strong_buy"|"buy"|"neutral"|"sell"|"strong_sell",
"confidence": 0-100, "whale_activity_level": "low"|"medium"|"high"}}"""

        result = await self.claude_client.analyze(prompt)
        if "error" not in result:
            self._cache.set(cache_key, result)
            self.log(f"{pair} whale signal: {result.get('whale_signal')}")
        return result

    async def _ai_flow_analysis(self, base_asset: str) -> dict[str, Any]:
        """AI-based flow analysis when no transaction data available."""
        prompt = f"""Analyze likely exchange flows for {base_asset}.
Respond in JSON:
{{"net_flow": "inflow"|"outflow"|"neutral", "signal": "bullish"|"bearish"|"neutral", "confidence": 0-100}}"""
        return await self.claude_client.analyze(prompt)

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
