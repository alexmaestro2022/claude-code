"""
API endpoints for AI Trade module.
"""

import os
from datetime import datetime, timedelta
from typing import Any, Optional

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/ai-trade", tags=["AI Trade"])

# Lazy orchestrator initialization
_orchestrator = None

# Cache for prediction/whale data when autopilot is inactive
_prediction_cache: dict[str, dict[str, Any]] = {}
_whale_cache: dict[str, dict[str, Any]] = {}
_sentiment_cache: dict[str, Any] = {}
CACHE_TTL_SECONDS = 300  # 5 minutes cache TTL


def _is_cache_valid(cache_entry: dict[str, Any]) -> bool:
    """Check if cache entry is still valid."""
    if not cache_entry or "timestamp" not in cache_entry:
        return False
    cached_time = datetime.fromisoformat(cache_entry["timestamp"])
    return datetime.utcnow() - cached_time < timedelta(seconds=CACHE_TTL_SECONDS)


async def _is_autopilot_running() -> bool:
    """Check if autopilot is currently running."""
    try:
        orch = await get_orchestrator()
        if hasattr(orch, "autopilot") and orch.autopilot:
            return orch.autopilot._running
    except Exception:
        pass
    return False


async def get_orchestrator():
    """Get or create orchestrator instance (creates its own BybitExchange)."""
    global _orchestrator
    if _orchestrator is None:
        from ...ai_trade.orchestrator import AgentOrchestrator
        _orchestrator = AgentOrchestrator()

    return _orchestrator


@router.get("/status")
async def get_status():
    """Get AI Trade system status."""
    try:
        orch = await get_orchestrator()
        return orch.get_status()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api-usage")
async def get_api_usage():
    """Get Claude API usage statistics."""
    try:
        from ...ai_trade.claude_client import get_api_usage, get_api_warnings, get_top_consumers, get_today_top_consumers
        stats = get_api_usage()
        stats["warnings"] = get_api_warnings()
        stats["top_consumers"] = get_top_consumers(10)
        stats["today_top_consumers"] = get_today_top_consumers(10)
        return stats
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/api-usage/limits")
async def set_api_usage_limits(daily: float = None, monthly: float = None):
    """Set API usage warning limits."""
    try:
        from ...ai_trade.claude_client import set_api_usage_limits
        set_api_usage_limits(daily=daily, monthly=monthly)
        return {"success": True, "daily_limit": daily, "monthly_limit": monthly}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api-usage/reset")
async def reset_api_usage(initial_balance: float = 50.0):
    """Reset API usage statistics with new initial balance."""
    try:
        from ...ai_trade.claude_client import reset_api_usage
        return reset_api_usage(initial_balance)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/api-usage/settings")
async def update_api_usage_settings(
    use_budget_limit: bool = None,
    daily_limit: float = None,
    total_limit: float = None,
):
    """Update API usage budget limit settings."""
    try:
        from ...ai_trade.claude_client import update_budget_settings
        return update_budget_settings(
            use_budget_limit=use_budget_limit,
            daily_limit=daily_limit,
            total_limit=total_limit,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api-usage/settings")
async def get_api_usage_settings():
    """Get API usage budget settings."""
    try:
        from ...ai_trade.claude_client import get_budget_settings
        return get_budget_settings()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/agents")
async def get_agents():
    """List all agents and their status."""
    try:
        agents = [
            {"name": "TRADER", "status": "active", "description": "Scans market, finds opportunities"},
            {"name": "REVIEWER", "status": "active", "description": "Reviews TRADER decisions"},
            {"name": "RISK_GUARD", "status": "active", "description": "Risk control, VETO"},
            {"name": "ANALYST", "status": "active", "description": "Trade analysis"},
            {"name": "MENTOR", "status": "active", "description": "Training and correction"},
            {"name": "RESEARCHER", "status": "active", "description": "Market research"},
            {"name": "WHALE_TRACKER", "status": "active", "description": "Whale monitoring"},
            {"name": "NEWS", "status": "active", "description": "News monitoring"},
            {"name": "PREDICTOR", "status": "active", "description": "Price prediction"},
            {"name": "SNIPER", "status": "active", "description": "Sniper entries"},
            {"name": "ARBITRAGE", "status": "active", "description": "Arbitrage opportunities"},
            {"name": "HEDGE_MASTER", "status": "active", "description": "Hedging"},
            {"name": "WAR_ROOM", "status": "active", "description": "Crisis management"},
            {"name": "CAPITAL_MANAGER", "status": "active", "description": "Capital management"},
            {"name": "STRATEGY_EVOLUTION", "status": "active", "description": "Strategy optimization"},
            {"name": "LOGGER", "status": "active", "description": "Logging"},
        ]
        return {"agents": agents, "total": len(agents)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/profile")
async def get_trader_profile():
    """AI trader profile (level, XP, skills)."""
    try:
        orch = await get_orchestrator()
        profile = orch.knowledge_base.data.get("trader_profile", {})
        return profile
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/portfolio")
async def get_portfolio():
    """Portfolio analysis."""
    try:
        orch = await get_orchestrator()
        return await orch.analyze_portfolio()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/opportunities")
async def get_opportunities():
    """Current trading opportunities."""
    try:
        orch = await get_orchestrator()
        opportunities = await orch.trader.find_opportunity()
        arbitrage = await orch.scan_arbitrage_opportunities()
        snipes = await orch.sniper.scan_for_snipes(["BTCUSDT", "ETHUSDT", "SOLUSDT"])
        return {
            "trading": opportunities,
            "arbitrage": arbitrage,
            "snipes": snipes,
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/market-context/{pair}")
async def get_market_context(pair: str):
    """Full market context for a pair."""
    try:
        orch = await get_orchestrator()
        return await orch.get_market_context(pair)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/prediction/{pair}")
async def get_prediction(pair: str):
    """Price movement prediction. Returns cached data if autopilot is inactive."""
    try:
        autopilot_running = await _is_autopilot_running()

        # If autopilot is not running, return cached data
        if not autopilot_running:
            if pair in _prediction_cache and _is_cache_valid(_prediction_cache[pair]):
                cached = _prediction_cache[pair].copy()
                cached["cached"] = True
                cached["autopilot_running"] = False
                return cached
            # Return empty response if no cache
            return {
                "pair": pair,
                "prediction": None,
                "reversal": None,
                "cached": True,
                "autopilot_running": False,
                "timestamp": datetime.utcnow().isoformat()
            }

        # Autopilot running - fetch fresh data
        orch = await get_orchestrator()
        prediction = await orch.predictor.predict_movement(pair)
        reversal = await orch.predictor.detect_reversal(pair)

        result = {
            "pair": pair,
            "prediction": prediction,
            "reversal": reversal,
            "cached": False,
            "autopilot_running": True,
            "timestamp": datetime.utcnow().isoformat()
        }

        # Update cache
        _prediction_cache[pair] = result
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sentiment")
async def get_sentiment():
    """Market sentiment. Returns cached data if autopilot is inactive."""
    global _sentiment_cache
    try:
        autopilot_running = await _is_autopilot_running()

        # If autopilot is not running, return cached data
        if not autopilot_running:
            if _sentiment_cache and _is_cache_valid(_sentiment_cache):
                cached = _sentiment_cache.copy()
                cached["cached"] = True
                cached["autopilot_running"] = False
                return cached
            # Return empty response if no cache
            return {
                "market_sentiment": None,
                "breaking_news": None,
                "cached": True,
                "autopilot_running": False,
                "timestamp": datetime.utcnow().isoformat()
            }

        # Autopilot running - fetch fresh data
        orch = await get_orchestrator()
        market = await orch.news_agent.get_market_sentiment()
        breaking = await orch.news_agent.detect_breaking_news()

        result = {
            "market_sentiment": market,
            "breaking_news": breaking,
            "cached": False,
            "autopilot_running": True,
            "timestamp": datetime.utcnow().isoformat()
        }

        # Update cache
        _sentiment_cache = result
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/whale/{pair}")
async def get_whale_signal(pair: str):
    """Whale signals. Returns cached data if autopilot is inactive."""
    try:
        autopilot_running = await _is_autopilot_running()

        # If autopilot is not running, return cached data
        if not autopilot_running:
            if pair in _whale_cache and _is_cache_valid(_whale_cache[pair]):
                cached = _whale_cache[pair].copy()
                cached["cached"] = True
                cached["autopilot_running"] = False
                return cached
            # Return empty response if no cache
            return {
                "pair": pair,
                "signal": None,
                "confidence": 0,
                "reasoning": "Autopilot inactive",
                "cached": True,
                "autopilot_running": False,
                "timestamp": datetime.utcnow().isoformat()
            }

        # Autopilot running - fetch fresh data
        orch = await get_orchestrator()
        result = await orch.whale_tracker.get_whale_signal(pair)

        # Add metadata and cache
        if isinstance(result, dict):
            result["cached"] = False
            result["autopilot_running"] = True
            result["timestamp"] = datetime.utcnow().isoformat()
            _whale_cache[pair] = result

        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/capital")
async def get_capital():
    """Capital management."""
    try:
        orch = await get_orchestrator()
        allocation = orch.capital_manager.get_allocation()
        phase = orch.capital_manager.get_scaling_phase()
        return {
            "allocation": allocation,
            "phase": phase,
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/capital/plan")
async def calculate_growth_plan(initial: float, target: float, monthly_return: float = 30):
    """Calculate capital growth plan."""
    try:
        orch = await get_orchestrator()
        plan = orch.capital_manager.calculate_compound_plan(initial, target, monthly_return)
        return plan
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/health")
async def get_health():
    """System health check."""
    try:
        orch = await get_orchestrator()
        market_safety = await orch.check_market_safety()
        system_health = await orch.war_room.run_health_check()
        return {
            "market": market_safety,
            "system": system_health,
            "crisis_mode": orch.war_room.is_crisis_mode(),
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/evolution")
async def get_evolution_stats():
    """Strategy evolution statistics."""
    try:
        orch = await get_orchestrator()
        return orch.strategy_evolution.get_evolution_stats()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/evolution/run")
async def run_evolution(generations: int = 5):
    """Run strategy evolution."""
    try:
        orch = await get_orchestrator()
        return await orch.evolve_strategies(generations)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/logs")
async def get_recent_logs(agent: Optional[str] = None, limit: int = 50):
    """Get recent logs."""
    try:
        logs_dir = "/opt/aila/logs/ai_trade"
        result: list = []
        if agent:
            log_file = os.path.join(logs_dir, f"{agent.lower()}.log")
            if os.path.exists(log_file):
                with open(log_file, 'r') as f:
                    lines = f.readlines()[-limit:]
                    result = [line.strip() for line in lines if line.strip()]
        else:
            if os.path.exists(logs_dir):
                for filename in sorted(os.listdir(logs_dir)):
                    if filename.endswith('.log'):
                        log_file = os.path.join(logs_dir, filename)
                        with open(log_file, 'r') as f:
                            lines = f.readlines()[-10:]
                            for line in lines:
                                if line.strip():
                                    result.append({
                                        "agent": filename.replace('.log', ''),
                                        "log": line.strip()
                                    })
        return {"logs": result[-limit:], "count": len(result)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/settings")
async def get_settings():
    """Get AI Trade settings."""
    try:
        from ...ai_trade.config import SCANNER_CONFIG, RISK_LIMITS, MODES
        return {
            "trading": SCANNER_CONFIG,
            "risk_limits": RISK_LIMITS,
            "modes": MODES
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/settings/full")
async def get_full_settings():
    """Get ALL AI Trade settings and parameters in one call."""
    try:
        from ...ai_trade.config import RISK_LIMITS, SCANNER_CONFIG, MODES, MIN_ORDER_SIZE_USDT, CLAUDE_MODEL

        orch = await get_orchestrator()

        # Get current status
        status = orch.get_status()
        profile = status.get("trader_profile", {})
        risk_status = status.get("risk_status", {})
        current_limits = risk_status.get("current_limits", {})

        # Autopilot config
        autopilot_status = orch.autopilot.get_status()
        autopilot_config = autopilot_status.get("config", {})

        # Capital
        capital = orch.capital_manager.get_allocation()
        capital_config = orch.capital_manager._config
        phase = orch.capital_manager.get_scaling_phase()

        # Persistence
        persistence = orch.get_persistence_status()

        # Balance
        try:
            balance = await orch.exchange.get_balance("USDT")
        except Exception:
            balance = 0

        return {
            "trading": {
                "min_confidence": autopilot_config.get("min_confidence", 70),
                "scan_interval_seconds": SCANNER_CONFIG.get("scan_interval_seconds", 60),
                "scan_all_pairs": SCANNER_CONFIG.get("scan_all_pairs", True),
                "pairs_cache_ttl": SCANNER_CONFIG.get("pairs_cache_ttl", 3600),
                "top_pairs_count": SCANNER_CONFIG.get("top_pairs_count", 20),
                "timeframes": ["1m", "5m", "15m", "1h", "4h"],
                "claude_model": CLAUDE_MODEL,
            },
            "risk_limits": {
                "max_leverage": RISK_LIMITS.get("max_leverage", 20),
                "max_position_size_pct": RISK_LIMITS.get("max_position_size_pct", 10),
                "max_daily_loss_pct": RISK_LIMITS.get("max_daily_loss_pct", 5),
                "max_drawdown_pct": RISK_LIMITS.get("max_drawdown_pct", 15),
                "min_balance_usdt": RISK_LIMITS.get("min_balance_usdt", 10),
                "max_open_positions": RISK_LIMITS.get("max_open_positions", 3),
                "default_risk_per_trade_pct": RISK_LIMITS.get("default_risk_per_trade_pct", 2),
                "min_order_size_usdt": MIN_ORDER_SIZE_USDT,
            },
            "level_limits": {
                "current_level": profile.get("level", 1),
                "xp": profile.get("xp", 0),
                "next_level_xp": profile.get("next_level_xp", 100),
                "current_max_leverage": current_limits.get("max_leverage", 5),
                "current_max_positions": current_limits.get("max_positions", 1),
                "current_risk_per_trade": current_limits.get("risk_per_trade", 1),
            },
            "autopilot": {
                "running": autopilot_status.get("running", False),
                "scan_interval_seconds": autopilot_config.get("scan_interval_seconds", 60),
                "min_confidence": autopilot_config.get("min_confidence", 70),
                "max_trades_per_hour": autopilot_config.get("max_trades_per_hour", 5),
                "max_trades_per_day": autopilot_config.get("max_trades_per_day", 20),
                "cooldown_after_loss_minutes": autopilot_config.get("cooldown_after_loss_minutes", 30),
                "require_multiple_confirmations": autopilot_config.get("require_multiple_confirmations", True),
                "trades_this_hour": autopilot_status.get("stats", {}).get("trades_this_hour", 0),
                "trades_today": autopilot_status.get("stats", {}).get("trades_today", 0),
            },
            "filters": {
                "volume_filter": {
                    "enabled": True,
                    "min_volume_24h": SCANNER_CONFIG.get("min_volume_24h", 5_000_000),
                },
                "volatility_filter": {
                    "enabled": True,
                    "min_volatility_pct": SCANNER_CONFIG.get("min_volatility_pct", 1),
                    "max_volatility_pct": SCANNER_CONFIG.get("max_volatility_pct", 15),
                },
                "trend_filter": {
                    "enabled": True,
                    "description": "EMA50/EMA200 crossover",
                },
            },
            "indicators": {
                "rsi": {"enabled": True, "period": 14},
                "ema50": {"enabled": True, "period": 50},
                "ema200": {"enabled": True, "period": 200, "issue": "requires 200+ candles"},
                "atr": {"enabled": True, "period": 14},
                "supertrend": {"enabled": False, "note": "Not used in AI Trade"},
            },
            "integrations": {
                "news_sentiment": {
                    "enabled": True,
                    "source": "Fear & Greed Index",
                    "weight": 5,
                },
                "whale_tracker": {
                    "enabled": True,
                    "source": "Orderbook analysis",
                    "weight": 5,
                    "whale_alert_api": False,
                },
                "predictor": {
                    "enabled": True,
                    "method": "TA + Claude analysis",
                    "weight": 10,
                },
            },
            "capital": {
                "total": capital.get("total", 0),
                "trading": capital.get("trading", 0),
                "reserve": capital.get("reserve", 0),
                "reserve_pct": capital_config.get("reserve_pct", 20),
                "kelly_fraction": capital_config.get("kelly_fraction", 0.5),
                "compound_pct": capital_config.get("compound_pct", 50),
                "phase": phase.get("phase", "Starter"),
                "recommended_leverage": phase.get("recommended_leverage", 10),
                "recommended_risk_pct": phase.get("recommended_risk_pct", 2),
            },
            "persistence": {
                "local_save_interval": 60,
                "cloud_backup_interval": 3600,
                "auto_save_running": persistence.get("auto_save_running", False),
                "s3_connected": persistence.get("s3_connected", False),
                "last_local_save": persistence.get("last_local_save"),
                "last_cloud_backup": persistence.get("last_cloud_backup"),
                "files_count": persistence.get("files_count", 0),
            },
            "current_status": {
                "mode": status.get("mode", "IDLE"),
                "balance": balance,
                "open_positions": status.get("open_positions", 0),
                "daily_pnl": risk_status.get("daily_pnl", 0),
                "daily_trades": risk_status.get("daily_trades", 0),
                "can_trade": risk_status.get("can_trade", True),
                "crisis_mode": status.get("agents", {}).get("war_room", {}).get("crisis_mode", False),
                "learning_cycles_running": status.get("learning_cycles", {}).get("running", False),
            },
            "timestamp": datetime.utcnow().isoformat(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/mode/{mode}")
async def set_mode(mode: str):
    """Set working mode (IDLE/AUTOPILOT)."""
    if mode.upper() not in ["IDLE", "AUTOPILOT"]:
        raise HTTPException(status_code=400, detail="Invalid mode. Use IDLE or AUTOPILOT")
    try:
        orch = await get_orchestrator()
        orch.mode = mode.upper()
        return {"mode": mode.upper(), "status": "set"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- Paper Trading ---

@router.get("/paper/stats")
async def get_paper_stats():
    """Get paper trading statistics."""
    try:
        orch = await get_orchestrator()
        return orch.paper_trader.get_stats()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/paper/positions")
async def get_paper_positions():
    """Get paper trading open positions."""
    try:
        orch = await get_orchestrator()
        positions = orch.paper_trader.get_positions()
        return {"positions": [vars(p) for p in positions]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/paper/trades")
async def get_paper_trades():
    """Get paper trading history."""
    try:
        orch = await get_orchestrator()
        trades = orch.paper_trader.get_trades()
        return {"trades": [vars(t) for t in trades]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- Backtesting ---

@router.post("/backtest")
async def run_backtest(strategy_name: str, pair: str = "BTCUSDT"):
    """Run strategy backtest."""
    try:
        orch = await get_orchestrator()
        params = {"fast_ema": 9, "slow_ema": 21}
        return await orch.run_backtest(strategy_name, params, pair)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- Autopilot Mode ---

@router.post("/autopilot/start")
async def start_autopilot():
    """Start autopilot mode."""
    try:
        orch = await get_orchestrator()
        return await orch.start_autopilot()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/autopilot/stop")
async def stop_autopilot():
    """Stop autopilot mode."""
    try:
        orch = await get_orchestrator()
        return await orch.stop_autopilot()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/autopilot/stop-and-close")
async def stop_autopilot_and_close():
    """Stop autopilot and close all positions + cancel all orders."""
    try:
        orch = await get_orchestrator()

        # 1. Stop autopilot
        stop_result = await orch.stop_autopilot()

        # 2. Close all positions
        positions_result = await orch.exchange.close_all_positions()

        # 3. Cancel all orders
        orders_result = await orch.exchange.cancel_all_orders()

        # 4. Send Telegram notification
        total_pnl = positions_result.get("total_pnl", 0)
        pnl_sign = "+" if total_pnl >= 0 else ""
        message = f"""<b>AUTOPILOT STOPPED</b>

Closed positions: <b>{positions_result.get('closed_count', 0)}</b>
Cancelled orders: <b>{orders_result.get('cancelled_count', 0)}</b>
Total PnL: <code>{pnl_sign}${total_pnl:.2f}</code>

<i>{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC</i>"""

        if orch.telegram:
            await orch.telegram.send_message(message)

        return {
            "status": "stopped",
            "positions_closed": positions_result.get("closed_count", 0),
            "orders_cancelled": orders_result.get("cancelled_count", 0),
            "total_pnl": total_pnl,
            "positions": positions_result.get("closed", []),
            "orders": orders_result.get("cancelled", []),
            "errors": {
                "positions": positions_result.get("errors", []),
                "orders": orders_result.get("errors", []),
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/autopilot/status")
async def get_autopilot_status():
    """Get autopilot mode status."""
    try:
        orch = await get_orchestrator()
        return orch.autopilot.get_status()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/autopilot/config")
async def update_autopilot_config(config: dict):
    """Update autopilot configuration."""
    try:
        orch = await get_orchestrator()
        return orch.autopilot.update_config(config)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/autopilot/heartbeat")
async def get_autopilot_heartbeat():
    """Get real-time autopilot heartbeat for UI activity indicator."""
    try:
        orch = await get_orchestrator()
        return orch.autopilot.get_heartbeat()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- Scaling Manager ---

@router.get("/scaling")
async def get_scaling_info():
    """Get comprehensive scaling information."""
    try:
        orch = await get_orchestrator()
        return await orch.get_scaling_info()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/scaling/projection")
async def get_growth_projection(months: int = 12):
    """Get capital growth projection."""
    try:
        orch = await get_orchestrator()
        return await orch.scaling_manager.calculate_growth_projection(months)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/scaling/phases")
async def get_all_phases():
    """Get all scaling phases."""
    try:
        orch = await get_orchestrator()
        return {"phases": orch.scaling_manager.get_all_phases()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==================== PERSISTENCE ====================


@router.get("/persistence/status")
async def get_persistence_status():
    """Get persistence system status."""
    try:
        orch = await get_orchestrator()
        return orch.get_persistence_status()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/persistence/save")
async def force_save():
    """Force immediate local save."""
    try:
        orch = await get_orchestrator()
        return await orch.save_now()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/persistence/backup")
async def force_cloud_backup():
    """Force immediate Yandex Object Storage backup."""
    try:
        orch = await get_orchestrator()
        return await orch.backup_to_cloud_now()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==================== AGENT STATS ====================


@router.get("/agent-stats")
async def get_agent_stats():
    """Get stats for both TRADER and SNIPER agents."""
    try:
        orch = await get_orchestrator()
        return orch.autopilot.get_agent_stats()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/agent-stats/{agent}")
async def get_single_agent_stats(agent: str):
    """Get stats for specific agent (TRADER or SNIPER)."""
    if agent.upper() not in ["TRADER", "SNIPER"]:
        raise HTTPException(status_code=400, detail="Invalid agent. Use TRADER or SNIPER")
    try:
        orch = await get_orchestrator()
        return orch.autopilot._agent_stats.get_stats(agent.upper())
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/agent-stats/{agent}/clear-pause")
async def clear_agent_pause(agent: str):
    """Clear pause for agent (TRADER or SNIPER)."""
    if agent.upper() not in ["TRADER", "SNIPER"]:
        raise HTTPException(status_code=400, detail="Invalid agent. Use TRADER or SNIPER")
    try:
        orch = await get_orchestrator()
        orch.autopilot.clear_agent_pause(agent.upper())
        return {"success": True, "agent": agent.upper(), "paused": False}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/agent-stats/{agent}/clear-cooldown")
async def clear_agent_cooldown(agent: str):
    """Clear cooldown for agent (TRADER or SNIPER)."""
    if agent.upper() not in ["TRADER", "SNIPER"]:
        raise HTTPException(status_code=400, detail="Invalid agent. Use TRADER or SNIPER")
    try:
        orch = await get_orchestrator()
        orch.autopilot.clear_agent_cooldown(agent.upper())
        return {"success": True, "agent": agent.upper(), "cooldown": 0}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==================== SIGNAL QUEUE ====================


@router.get("/queue/status")
async def get_queue_status():
    """Get signal queue status."""
    try:
        orch = await get_orchestrator()
        return orch.autopilot.get_queue_status()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/queue/items")
async def get_queue_items():
    """Get all items in signal queue."""
    try:
        orch = await get_orchestrator()
        items = orch.autopilot._signal_queue.get_queue_items()
        return {"items": items, "count": len(items)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/queue/clear")
async def clear_queue():
    """Clear signal queue."""
    try:
        orch = await get_orchestrator()
        orch.autopilot._signal_queue.clear()
        return {"success": True, "message": "Queue cleared"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/queue/config")
async def update_queue_config(config: dict):
    """Update signal queue configuration."""
    try:
        orch = await get_orchestrator()
        orch.autopilot._signal_queue.update_config(config)
        return {"success": True, "config": config}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==================== SNIPER ====================


@router.get("/sniper/scan")
async def scan_sniper_opportunities():
    """Manually scan for sniper opportunities."""
    try:
        orch = await get_orchestrator()
        snipes = await orch.scan_snipe_opportunities()
        return {
            "snipes": snipes,
            "count": len(snipes),
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sniper/pending")
async def get_pending_snipes():
    """Get pending snipe opportunities."""
    try:
        orch = await get_orchestrator()
        pending = await orch.sniper.get_pending_snipes()
        return {"pending": pending, "count": len(pending)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sniper/cancel/{pair}")
async def cancel_snipe(pair: str):
    """Cancel pending snipe for a pair."""
    try:
        orch = await get_orchestrator()
        cancelled = await orch.sniper.cancel_snipe(pair)
        return {"success": cancelled, "pair": pair}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==================== AGENT LEVELS CONFIG ====================


@router.get("/levels/config")
async def get_levels_config():
    """Get TRADER and SNIPER level configurations."""
    try:
        from ...ai_trade.config import TRADER_LEVELS, SNIPER_LEVELS, AGENT_XP_THRESHOLDS
        return {
            "trader_levels": TRADER_LEVELS,
            "sniper_levels": SNIPER_LEVELS,
            "xp_thresholds": AGENT_XP_THRESHOLDS,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
