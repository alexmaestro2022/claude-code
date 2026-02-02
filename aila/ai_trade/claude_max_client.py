"""Claude Max client — uses Claude CLI (Max subscription) instead of paid API.

Drop-in replacement for ClaudeClient.  Uses `claude --print` subprocess
with OAuth authentication from /home/aila/.claude/.credentials.json.
"""

import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("ai_trade")

# Dedicated log for Max CLI usage
_cli_usage_logger = logging.getLogger("ai_trade.cli_usage")
_cli_log_path = Path("/opt/aila/logs/ai_trade/cli_usage.log")
_cli_log_path.parent.mkdir(parents=True, exist_ok=True)
if not any(
    isinstance(h, logging.FileHandler)
    and getattr(h, "baseFilename", "").endswith("cli_usage.log")
    for h in _cli_usage_logger.handlers
):
    _cli_handler = logging.FileHandler(_cli_log_path, encoding="utf-8")
    _cli_handler.setFormatter(logging.Formatter(
        "[%(asctime)s] %(message)s", "%Y-%m-%d %H:%M:%S",
    ))
    _cli_usage_logger.addHandler(_cli_handler)
_cli_usage_logger.setLevel(logging.INFO)
_cli_usage_logger.propagate = False

# Claude CLI config
CLAUDE_CLI = "/usr/local/bin/claude"
CLAUDE_MODEL = "claude-opus-4-5-20251101"
CLI_TIMEOUT = 300  # seconds (Opus + large prompts need more time)
MAX_RETRIES = 3
BASE_DELAY = 2.0  # seconds for exponential backoff


class ClaudeMaxClient:
    """Async Claude client via CLI (Max subscription, no API key needed).

    Compatible interface with ClaudeClient — drop-in replacement.
    """

    __slots__ = ("_model", "_env")

    def __init__(self) -> None:
        self._model = CLAUDE_MODEL
        # Environment for subprocess: use aila user's HOME for OAuth creds
        self._env = {
            **os.environ,
            "HOME": "/home/aila",
            "USER": "aila",
        }
        # Remove API keys so CLI uses OAuth
        self._env.pop("ANTHROPIC_API_KEY", None)
        self._env.pop("CLAUDE_API_KEY", None)

    async def analyze(
        self,
        prompt: str,
        max_tokens: int = 4096,
        use_haiku: bool = False,
        agent: str = "UNKNOWN",
        action: str = "analyze",
        context: str = "",
    ) -> dict[str, Any]:
        """Send prompt to Claude via CLI and return parsed JSON response.

        Args:
            prompt: The prompt to send
            max_tokens: Max output tokens (passed to CLI)
            use_haiku: Ignored — Max always uses Opus
            agent: Agent name for logging
            action: Action type for logging
            context: Additional context for logging

        Returns:
            Parsed JSON dict from Claude response
        """
        last_error = ""
        for attempt in range(MAX_RETRIES):
            try:
                result = await self._call_cli(prompt, max_tokens)

                if "error" in result:
                    last_error = result["error"]
                    if "OAuth" in last_error or "auth" in last_error.lower():
                        logger.error(f"[CLI] OAuth error: {last_error}")
                        # Don't retry auth errors
                        return result
                    if attempt < MAX_RETRIES - 1:
                        delay = BASE_DELAY * (2 ** attempt)
                        logger.warning(
                            f"[CLI] Retry {attempt + 1}/{MAX_RETRIES}: {last_error}"
                        )
                        await asyncio.sleep(delay)
                        continue
                    return result

                # Log usage
                self._log_usage(agent, action, context, result)
                return result.get("parsed", {"raw_response": result.get("text", "")})

            except asyncio.TimeoutError:
                last_error = f"CLI timeout ({CLI_TIMEOUT}s)"
                logger.error(f"[CLI] {last_error} for {agent}/{action}")
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(BASE_DELAY * (2 ** attempt))
                    continue
            except Exception as e:
                last_error = str(e)
                logger.error(f"[CLI] Error: {e}")
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(BASE_DELAY * (2 ** attempt))
                    continue

        return {"error": f"CLI failed after {MAX_RETRIES} attempts: {last_error}"}

    async def batch_analyze_market(
        self, pairs_data: list[dict[str, Any]], knowledge: dict[str, Any]
    ) -> dict[str, Any]:
        """Batch analyze all pairs in a single CLI call.

        Compatible with ClaudeClient.batch_analyze_market().
        """
        if not pairs_data:
            return {"decision": "WAIT", "reason": "No pairs to analyze"}

        prompt = self._build_batch_market_prompt(pairs_data, knowledge)
        return await self.analyze(
            prompt,
            max_tokens=4096,
            agent="TRADER",
            action="batch_analyze",
            context=f"pairs={len(pairs_data)}",
        )

    async def get_market_analysis(
        self, pair: str, market_data: dict[str, Any], knowledge: dict[str, Any]
    ) -> dict[str, Any]:
        """Analyze market for a single pair (legacy method)."""
        prompt = self._build_market_prompt(pair, market_data, knowledge)
        return await self.analyze(
            prompt,
            agent="TRADER",
            action="single_analyze",
            context=f"pair={pair}",
        )

    async def analyze_trade_result(self, trade: dict[str, Any]) -> dict[str, Any]:
        """Analyze completed trade for learning."""
        prompt = self._build_trade_analysis_prompt(trade)
        pair = trade.get("symbol", trade.get("pair", "unknown"))
        return await self.analyze(
            prompt,
            agent="ANALYST",
            action="trade_result",
            context=f"pair={pair}",
        )

    async def _call_cli(self, prompt: str, max_tokens: int) -> dict[str, Any]:
        """Call claude CLI and parse response.

        Returns dict with keys: text, parsed, usage, duration_ms.
        """
        cmd = [
            CLAUDE_CLI,
            "--print",
            "--model", self._model,
            "--output-format", "json",
            "--max-turns", "1",
            "-p", prompt,
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._env,
        )

        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=CLI_TIMEOUT
        )

        if proc.returncode != 0:
            err_msg = stderr.decode("utf-8", errors="replace").strip()
            return {"error": f"CLI exit code {proc.returncode}: {err_msg}"}

        raw_output = stdout.decode("utf-8", errors="replace").strip()

        # Parse CLI JSON output
        try:
            cli_result = json.loads(raw_output)
        except json.JSONDecodeError:
            # If not JSON, treat as plain text
            return {
                "text": raw_output,
                "parsed": self._extract_json(raw_output),
                "usage": {},
                "duration_ms": 0,
            }

        # Extract result text from CLI JSON envelope
        result_text = cli_result.get("result", "")
        usage = cli_result.get("usage", {})
        duration_ms = cli_result.get("duration_ms", 0)

        if cli_result.get("is_error"):
            return {"error": f"CLI error: {result_text}"}

        return {
            "text": result_text,
            "parsed": self._extract_json(result_text),
            "usage": usage,
            "duration_ms": duration_ms,
            "cost_usd": cli_result.get("total_cost_usd", 0),
        }

    def _log_usage(
        self, agent: str, action: str, context: str, result: dict[str, Any]
    ) -> None:
        """Log CLI usage to dedicated log file."""
        usage = result.get("usage", {})
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        cache_read = usage.get("cache_read_input_tokens", 0)
        duration_ms = result.get("duration_ms", 0)
        cost = result.get("cost_usd", 0)

        _cli_usage_logger.info(
            f"{agent} {action} | model=opus-max | "
            f"input={input_tokens} | output={output_tokens} | "
            f"cache_read={cache_read} | "
            f"duration={duration_ms}ms | cost=${cost:.4f} | {context}"
        )

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any]:
        """Extract JSON from response text (may be in markdown code block)."""
        # Strip markdown code fences
        clean = text
        if "```json" in clean:
            start = clean.find("```json") + 7
            end = clean.find("```", start)
            if end > start:
                clean = clean[start:end].strip()
        elif "```" in clean:
            start = clean.find("```") + 3
            end = clean.find("```", start)
            if end > start:
                clean = clean[start:end].strip()

        # Try to find JSON object
        start = clean.find("{")
        end = clean.rfind("}") + 1
        if start != -1 and end > start:
            try:
                return json.loads(clean[start:end])
            except json.JSONDecodeError:
                pass

        return {"raw_response": text}

    # --- Prompt builders (copied from ClaudeClient for compatibility) ---

    @staticmethod
    def _build_batch_market_prompt(
        pairs_data: list[dict[str, Any]], knowledge: dict[str, Any]
    ) -> str:
        """Build structured batch market analysis prompt."""
        setups = knowledge.get("successful_setups", [])[-3:]
        mistakes = knowledge.get("mistakes_to_avoid", [])[-3:]
        rules = knowledge.get("learned_rules", [])[-5:]
        recent_trades = knowledge.get("recent_trades", [])[-3:]
        btc_data = knowledge.get("btc_context", {})
        market_sentiment = knowledge.get("market_sentiment", {})

        # Build compact pairs data with all indicators
        pairs_summary = []
        for p in pairs_data:
            md = p.get("market_data", {})
            entry = {
                "symbol": p["symbol"],
                "price": md.get("price"),
                "change_24h": md.get("change_24h"),
                "volume_24h": md.get("volume_24h"),
                "trend": md.get("trend"),
                "rsi": md.get("rsi"),
                "ema50": md.get("ema50"),
                "ema200": md.get("ema200"),
                "atr": md.get("atr"),
            }
            # Add new indicators (compact)
            macd = md.get("macd")
            if macd:
                entry["macd_hist"] = macd.get("histogram")
                entry["macd_signal"] = "bullish" if (macd.get("histogram") or 0) > 0 else "bearish"
            bb = md.get("bollinger")
            if bb:
                entry["bb_pct_b"] = bb.get("pct_b")
                entry["bb_width_pct"] = bb.get("width_pct")
            vp = md.get("volume_profile")
            if vp:
                entry["vol_ratio"] = vp.get("ratio")
            srsi = md.get("stoch_rsi")
            if srsi:
                entry["stoch_rsi_k"] = srsi.get("k")
                entry["stoch_rsi_d"] = srsi.get("d")
            entry["support"] = md.get("support")
            entry["resistance"] = md.get("resistance")
            pairs_summary.append(entry)

        # BTC context section
        btc_section = ""
        if btc_data:
            btc_section = f"""
## MARKET CONTEXT
- BTC price: ${btc_data.get('price', 'N/A')}, trend: {btc_data.get('trend', 'N/A')}, 24h: {btc_data.get('change_24h', 'N/A')}%
- BTC RSI: {btc_data.get('rsi', 'N/A')}, MACD: {btc_data.get('macd_signal', 'N/A')}
- Market sentiment: {market_sentiment.get('health', 'N/A')} ({market_sentiment.get('bullish_pct', 50)}% bullish)
- RULE: When BTC is bearish, reduce confidence by 10-20% for altcoin LONG trades"""
        else:
            btc_section = "\n## MARKET CONTEXT\n- BTC data unavailable — be more conservative"

        # Trading history section
        trades_section = ""
        if recent_trades:
            trades_lines = []
            for t in recent_trades:
                trades_lines.append(
                    f"  - {t.get('symbol', '?')} {t.get('side', '?')}: "
                    f"PnL {t.get('pnl_pct', 0):.1f}%, reason: {t.get('close_reason', '?')}"
                )
            trades_section = "\n## YOUR RECENT TRADES\n" + "\n".join(trades_lines)

        return f"""## ROLE
You are an expert cryptocurrency futures trader. You analyze technical indicators across multiple pairs to find the single highest-probability trade setup.

## MARKET DATA ({len(pairs_data)} pairs)
{json.dumps(pairs_summary, indent=1)}
{btc_section}
{trades_section}

## YOUR EXPERIENCE
- Recent winning setups: {json.dumps(setups, indent=2) if setups else "None yet"}
- Mistakes to avoid: {json.dumps(mistakes, indent=2) if mistakes else "None yet"}
- Learned rules: {json.dumps(rules, indent=2) if rules else "No rules yet"}

## RISK RULES (MANDATORY — violations will be rejected)
1. R:R ratio >= 1.5:1 (stop_loss and take_profit REQUIRED)
2. Stop loss: min 2% for majors (BTC, ETH), min 3% for altcoins/meme
3. Leverage: max 3x for majors, max 2x for altcoins
4. Position size: 2-4% of capital
5. NEVER LONG in BEARISH trend, NEVER SHORT in BULLISH trend
6. If RSI > 75 do not LONG (overbought), if RSI < 25 do not SHORT (oversold)

## INDICATOR GUIDE
- Trend: BULLISH = price > EMA50 > EMA200, BEARISH = opposite
- RSI: 40-70 for LONG, 30-60 for SHORT. Extremes = reversal risk
- MACD histogram > 0 = bullish momentum, < 0 = bearish
- Bollinger %B: >0.8 = near upper band (overbought), <0.2 = near lower (oversold)
- Volume ratio > 1.5 = unusual activity (confirm breakout), < 0.5 = low interest
- StochRSI: K > 80 = overbought, K < 20 = oversold. K crossing D = signal
- Support/Resistance: entry near support (LONG) or resistance (SHORT) = better R:R

## TASK
Select ONE best trade or WAIT. Respond STRICTLY in JSON:
{{
    "decision": "LONG" | "SHORT" | "WAIT",
    "pair": "SYMBOL/USDT or null if WAIT",
    "confidence": 0-100,
    "strategy": "brief strategy description",
    "entry_price": number or null,
    "stop_loss": number or null,
    "take_profit": number or null,
    "leverage": 1-3,
    "position_size_pct": 2-4,
    "reasoning": "2-3 sentences: why this is the best setup right now",
    "risks": ["risk1", "risk2"],
    "expected_duration": "5m" | "1h" | "4h" | "1d",
    "pairs_analyzed": {len(pairs_data)},
    "runner_up": "second best pair or null"
}}

CRITICAL:
- If NO pair has R:R >= 1.5 with clear trend, choose WAIT
- Better to WAIT than take a mediocre setup
- Confidence >= 70% required for LONG/SHORT
- SHORT is valid for BEARISH trends — profit when price drops"""

    @staticmethod
    def _build_market_prompt(
        pair: str, market_data: dict[str, Any], knowledge: dict[str, Any]
    ) -> str:
        """Build market analysis prompt (legacy single-pair)."""
        pair_perf = knowledge.get("pair_performance", {}).get(pair, {})
        setups = knowledge.get("successful_setups", [])[-5:]
        mistakes = knowledge.get("mistakes_to_avoid", [])[-5:]
        rules = knowledge.get("learned_rules", [])[-5:]

        return f"""You are an expert cryptocurrency trader. Analyze the market.

## CURRENT SITUATION
Pair: {pair}
Price: {market_data.get('price')}
24h Change: {market_data.get('change_24h')}%
24h Volume: ${market_data.get('volume_24h', 0):,.0f}
Trend: {market_data.get('trend')}

Indicators:
- RSI(14): {market_data.get('rsi')}
- EMA50: {market_data.get('ema50')}
- EMA200: {market_data.get('ema200')}
- ATR: {market_data.get('atr')}

## PAIR HISTORY
{json.dumps(pair_perf, indent=2)}

## RECENT SUCCESSFUL TRADES
{json.dumps(setups, indent=2)}

## MISTAKES TO AVOID
{json.dumps(mistakes, indent=2)}

## LEARNED RULES FROM EXPERIENCE
{json.dumps(rules, indent=2) if rules else "No rules learned yet."}

## RISK MANAGEMENT RULES (MANDATORY)
1. Risk/Reward ratio MUST be >= 1.5:1 (take_profit distance / stop_loss distance)
2. Stop loss distance: minimum 3% from entry for volatile coins, 2% for stable
3. Leverage: max 2x for meme/volatile coins, max 3x for major coins (BTC, ETH, BNB)
4. Position size: 2-4% of capital
5. LONG: price > EMA50 > EMA200, trend=BULLISH
6. SHORT: price < EMA50 < EMA200, trend=BEARISH (profit when price DROPS)

## TASK
Respond STRICTLY in JSON:
{{
    "decision": "LONG" | "SHORT" | "WAIT",
    "confidence": 0-100,
    "strategy": "strategy name",
    "entry_price": number or null,
    "stop_loss": number or null (min 3% from entry),
    "take_profit": number or null (must give R/R >= 1.5),
    "leverage": 1-3 (2 for volatile, 3 for majors),
    "position_size_pct": 2-4,
    "reasoning": "detailed reasoning",
    "risks": ["risk1", "risk2"],
    "expected_duration": "5m" | "1h" | "4h" | "1d"
}}

CRITICAL: If R/R < 1.5 - choose WAIT. Better to miss a trade than lose money."""

    @staticmethod
    def _build_trade_analysis_prompt(trade: dict[str, Any]) -> str:
        """Build trade analysis prompt."""
        return f"""Analyze the completed trade and extract lessons.

## TRADE
{json.dumps(trade, indent=2)}

## TASK
Respond STRICTLY in JSON:
{{
    "grade": "A" | "B" | "C" | "D" | "F",
    "what_went_right": ["point1", "point2"],
    "what_went_wrong": ["point1", "point2"],
    "lesson_learned": "main lesson",
    "improvement_suggestion": "how to improve",
    "add_to_mistakes_to_avoid": "if there was an error, what to add"
}}"""
