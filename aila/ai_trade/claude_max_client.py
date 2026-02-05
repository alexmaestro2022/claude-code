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
MAX_RETRIES = 2  # fewer retries = less total wait time
BASE_DELAY = 2.0  # seconds for exponential backoff
MAX_PAIRS_PER_BATCH = 3  # max pairs per single CLI call (Opus is slow)
BATCH_DELAY = 2.0  # seconds between batch calls


class ClaudeMaxClient:
    """Async Claude client via CLI (Max subscription, no API key needed).

    Compatible interface with ClaudeClient — drop-in replacement.
    """

    __slots__ = ("_model", "_env")

    def __init__(self) -> None:
        self._model = CLAUDE_MODEL
        # Environment for subprocess: use /opt/aila as HOME
        # (systemd ProtectHome=true blocks /home/aila)
        self._env = {
            **os.environ,
            "HOME": "/opt/aila",
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
        """Two-stage batch analysis: split into chunks, then pick best.

        Stage 1: Split pairs into batches of MAX_PAIRS_PER_BATCH,
                 each batch returns best candidate or WAIT.
        Stage 2: If multiple finalists, one final call picks the best.
        """
        if not pairs_data:
            return {"decision": "WAIT", "reason": "No pairs to analyze"}

        # Small batch — single call, no splitting needed
        if len(pairs_data) <= MAX_PAIRS_PER_BATCH:
            prompt = self._build_batch_market_prompt(pairs_data, knowledge)
            return await self.analyze(
                prompt, agent="TRADER",
                action="batch_analyze",
                context=f"pairs={len(pairs_data)}",
            )

        # Stage 1: split into chunks
        chunks = [
            pairs_data[i:i + MAX_PAIRS_PER_BATCH]
            for i in range(0, len(pairs_data), MAX_PAIRS_PER_BATCH)
        ]
        logger.info(
            f"[TRADER] Splitting {len(pairs_data)} pairs into "
            f"{len(chunks)} batches of max {MAX_PAIRS_PER_BATCH}"
        )

        finalists: list[dict[str, Any]] = []
        for idx, chunk in enumerate(chunks):
            logger.info(
                f"[TRADER] Batch {idx + 1}/{len(chunks)}: "
                f"{[p['symbol'] for p in chunk]}"
            )
            prompt = self._build_batch_market_prompt(chunk, knowledge)
            result = await self.analyze(
                prompt, agent="TRADER",
                action=f"batch_{idx + 1}of{len(chunks)}",
                context=f"pairs={len(chunk)}",
            )
            if "error" not in result:
                decision = result.get("decision", "WAIT")
                conf = result.get("confidence", 0)
                if decision != "WAIT" and conf >= 70:
                    finalists.append(result)
                    logger.info(
                        f"[TRADER] Batch {idx + 1} finalist: "
                        f"{result.get('pair')} {decision} conf={conf}%"
                    )
                else:
                    logger.info(
                        f"[TRADER] Batch {idx + 1}: WAIT (conf={conf}%)"
                    )
            else:
                logger.warning(
                    f"[TRADER] Batch {idx + 1} error: {result.get('error')}"
                )

            # Pause between batches to avoid overloading CLI
            if idx < len(chunks) - 1:
                await asyncio.sleep(BATCH_DELAY)

        # No finalists from any batch
        if not finalists:
            return {"decision": "WAIT", "confidence": 0, "reason": "No batch produced a candidate"}

        # Single finalist — return directly
        if len(finalists) == 1:
            return finalists[0]

        # Stage 2: pick best among finalists
        logger.info(f"[TRADER] Stage 2: choosing among {len(finalists)} finalists")
        return await self._pick_best_finalist(finalists, knowledge)

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

    async def _pick_best_finalist(
        self, finalists: list[dict[str, Any]], knowledge: dict[str, Any]
    ) -> dict[str, Any]:
        """Stage 2: pick best trade from batch finalists via short CLI call."""
        summaries = []
        for f in finalists:
            summaries.append({
                "pair": f.get("pair"),
                "decision": f.get("decision"),
                "confidence": f.get("confidence"),
                "entry_price": f.get("entry_price"),
                "stop_loss": f.get("stop_loss"),
                "take_profit": f.get("take_profit"),
                "leverage": f.get("leverage"),
                "reasoning": f.get("reasoning", ""),
            })
        prompt = (
            "You are a crypto futures trader. Pick the SINGLE BEST trade "
            "from these candidates. Consider R:R ratio, confidence, and "
            "trend alignment.\n\n"
            f"## CANDIDATES\n{json.dumps(summaries, indent=1)}\n\n"
            "Respond STRICTLY in JSON with the SAME fields as the winning "
            "candidate. Copy all its fields exactly, just pick the best one."
        )
        result = await self.analyze(
            prompt, agent="TRADER",
            action="pick_finalist",
            context=f"finalists={len(finalists)}",
        )
        if "error" in result:
            # Fallback: pick highest confidence
            best = max(finalists, key=lambda x: x.get("confidence", 0))
            logger.info(f"[TRADER] Finalist pick failed, using highest conf: {best.get('pair')}")
            return best
        return result

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
            # Also check stdout for error details (CLI may write errors there)
            if not err_msg:
                out_msg = stdout.decode("utf-8", errors="replace").strip()[:500]
                err_msg = out_msg or "no output"
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
        """Build ultra-compact batch market analysis prompt."""
        btc_data = knowledge.get("btc_context", {})
        recent_trades = knowledge.get("recent_trades", [])[-2:]

        # Build minimal pairs data — only key fields
        pairs_summary = []
        for p in pairs_data:
            md = p.get("market_data", {})
            entry: dict[str, Any] = {
                "s": p["symbol"],
                "p": md.get("price"),
                "chg": round(md.get("change_24h", 0), 1),
                "vol": md.get("volume_24h"),
                "trend": md.get("trend"),
                "rsi": md.get("rsi"),
            }
            # Only include EMA/ATR if present
            if md.get("ema50"):
                entry["ema50"] = md["ema50"]
                entry["ema200"] = md.get("ema200")
            if md.get("atr"):
                entry["atr"] = md["atr"]
            # Compact indicators — only values, skip None
            macd = md.get("macd")
            if macd and macd.get("histogram") is not None:
                entry["macd"] = round(macd["histogram"], 4)
            bb = md.get("bollinger")
            if bb and bb.get("pct_b") is not None:
                entry["bb"] = round(bb["pct_b"], 2)
            vp = md.get("volume_profile")
            if vp and vp.get("ratio") is not None:
                entry["vr"] = round(vp["ratio"], 2)
            srsi = md.get("stoch_rsi")
            if srsi and srsi.get("k") is not None:
                entry["srsi"] = round(srsi["k"], 1)
            if md.get("support"):
                entry["sup"] = md["support"]
                entry["res"] = md.get("resistance")
            # Funding rate and open interest
            if md.get("funding_rate") is not None:
                entry["fr"] = round(md["funding_rate"], 4)
                entry["fs"] = md.get("funding_signal", "NEUTRAL")
            if md.get("open_interest"):
                entry["oi"] = md["open_interest"]
            pairs_summary.append(entry)

        # BTC context — one line
        btc_line = ""
        if btc_data:
            btc_line = (
                f"BTC: ${btc_data.get('price','?')} {btc_data.get('trend','?')} "
                f"RSI={btc_data.get('rsi','?')} chg={btc_data.get('change_24h','?')}%"
            )

        # Recent trades — one line each
        trades_line = ""
        if recent_trades:
            parts = [
                f"{t.get('symbol','?')} {t.get('side','?')} PnL={t.get('pnl_pct',0):.1f}%"
                for t in recent_trades
            ]
            trades_line = "Recent: " + " | ".join(parts)

        # Experience section — learned from past trades
        experience_lines = []

        # Mistakes to avoid (last 5)
        mistakes = knowledge.get("mistakes_to_avoid", [])[-5:]
        if mistakes:
            mistake_strs = []
            for m in mistakes:
                if isinstance(m, dict):
                    mistake_strs.append(m.get("mistake", m.get("lesson", str(m)))[:80])
                else:
                    mistake_strs.append(str(m)[:80])
            experience_lines.append("AVOID: " + "; ".join(mistake_strs))

        # Learned rules (last 5)
        rules = knowledge.get("learned_rules", [])[-5:]
        if rules:
            rule_strs = []
            for r in rules:
                if isinstance(r, dict):
                    rule_strs.append(r.get("rule", str(r))[:60])
                else:
                    rule_strs.append(str(r)[:60])
            experience_lines.append("RULES: " + "; ".join(rule_strs))

        # Successful setups (last 3)
        setups = knowledge.get("successful_setups", [])[-3:]
        if setups:
            setup_strs = []
            for s in setups:
                if isinstance(s, dict):
                    setup_strs.append(
                        f"{s.get('pair', s.get('symbol', '?'))} "
                        f"{s.get('strategy', '?')} grade={s.get('grade', '?')}"
                    )
            if setup_strs:
                experience_lines.append("GOOD: " + "; ".join(setup_strs))

        experience_section = "\n".join(experience_lines) + "\n" if experience_lines else ""

        return (
            f"Crypto futures trader. Pick ONE best trade or WAIT from {len(pairs_data)} pairs.\n"
            f"{json.dumps(pairs_summary, separators=(',',':'))}\n"
            f"{btc_line}\n{trades_line}\n"
            f"{experience_section}"
            "Rules: R:R>=1.5, SL min 2% majors/3% alts, lev max 3x/2x, "
            "no LONG if BEARISH/RSI>75, no SHORT if BULLISH/RSI<25. "
            "fr=funding rate: >0.05%=overleveraged long (SHORT bias), <-0.05%=overleveraged short (LONG bias). "
            "High OI + flat price = big move incoming.\n"
            'JSON: {"decision":"LONG|SHORT|WAIT","pair":"SYM/USDT","confidence":0-100,'
            '"strategy":"brief","entry_price":N,"stop_loss":N,"take_profit":N,'
            '"leverage":1-3,"position_size_pct":2-4,"reasoning":"why",'
            f'"risks":["r"],"expected_duration":"1h","pairs_analyzed":{len(pairs_data)},'
            '"runner_up":"pair"}'
        )

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
