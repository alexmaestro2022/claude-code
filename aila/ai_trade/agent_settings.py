"""Agent settings management for TRADER and SNIPER."""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("ai_trade")

DATA_DIR = Path("/opt/aila/data/ai_trade")

# Default settings for TRADER
TRADER_DEFAULTS = {
    "enabled": True,
    "scan_interval_seconds": 60,
    "min_confidence": 70,
    "min_rr_ratio": 1.5,
    "min_sl_distance_pct": 2.0,
    "cooldown_seconds": 300,
    "max_trades_per_day": 10,
    "pause_after_losses": 3,
    "pause_duration_minutes": 60,
    "require_confirmations": True,
    "min_confirmations": 2,
    # Cascade analysis settings
    "cascade_enabled": True,
    "pause_on_position_limit": True,
    "priority_1_max_pairs": 10,
    "priority_2_max_pairs": 10,
    "priority_3_max_pairs": 10,
    "min_24h_change_pct": 3.0,
    "max_24h_change_pct": 50.0,
    "last_modified": None,
}

# Default settings for SNIPER
SNIPER_DEFAULTS = {
    "enabled": True,
    "scan_interval_seconds": 10,
    "pairs_to_scan": 20,
    # Triggers
    "trigger_breakout": True,
    "trigger_breakdown": True,
    "trigger_liquidation": False,
    # Trigger parameters
    "breakout_threshold_pct": 0.1,
    "rsi_breakout_min": 55,
    "rsi_breakout_max": 80,
    "rsi_breakdown_min": 20,
    "rsi_breakdown_max": 45,
    "atr_sl_multiplier": 1.5,
    "atr_tp_multiplier": 3.0,
    # Cooldown and protection
    "cooldown_seconds": 60,
    "max_trades_per_day": 20,
    "pause_after_losses": 3,
    "pause_duration_minutes": 60,
    "last_modified": None,
}

# Validation rules
TRADER_VALIDATION = {
    "scan_interval_seconds": {"min": 30, "max": 600},
    "min_confidence": {"min": 50, "max": 95},
    "min_rr_ratio": {"min": 1.0, "max": 5.0},
    "min_sl_distance_pct": {"min": 1.0, "max": 10.0},
    "cooldown_seconds": {"min": 60, "max": 1800},
    "max_trades_per_day": {"min": 1, "max": 50},
    "pause_after_losses": {"min": 2, "max": 10},
    "pause_duration_minutes": {"min": 15, "max": 240},
    "min_confirmations": {"min": 1, "max": 3},
    # Cascade validation
    "priority_1_max_pairs": {"min": 5, "max": 30},
    "priority_2_max_pairs": {"min": 5, "max": 30},
    "priority_3_max_pairs": {"min": 5, "max": 30},
    "min_24h_change_pct": {"min": 1.0, "max": 10.0},
    "max_24h_change_pct": {"min": 30.0, "max": 100.0},
}

SNIPER_VALIDATION = {
    "scan_interval_seconds": {"min": 5, "max": 60},
    "pairs_to_scan": {"min": 10, "max": 50},
    "breakout_threshold_pct": {"min": 0.05, "max": 1.0},
    "rsi_breakout_min": {"min": 40, "max": 70},
    "rsi_breakout_max": {"min": 60, "max": 90},
    "rsi_breakdown_min": {"min": 10, "max": 40},
    "rsi_breakdown_max": {"min": 30, "max": 60},
    "atr_sl_multiplier": {"min": 1.0, "max": 3.0},
    "atr_tp_multiplier": {"min": 2.0, "max": 6.0},
    "cooldown_seconds": {"min": 30, "max": 600},
    "max_trades_per_day": {"min": 1, "max": 100},
    "pause_after_losses": {"min": 2, "max": 10},
    "pause_duration_minutes": {"min": 15, "max": 240},
}


class AgentSettings:
    """Manages settings for TRADER and SNIPER agents."""

    __slots__ = ("_trader", "_sniper", "_change_log")

    def __init__(self) -> None:
        self._trader: dict[str, Any] = {}
        self._sniper: dict[str, Any] = {}
        self._change_log: list[dict[str, Any]] = []
        self._load_all()

    def _load_all(self) -> None:
        """Load settings for all agents."""
        self._trader = self._load_file("trader_settings.json", TRADER_DEFAULTS)
        self._sniper = self._load_file("sniper_settings.json", SNIPER_DEFAULTS)

    def _load_file(self, filename: str, defaults: dict) -> dict[str, Any]:
        """Load settings from file or return defaults."""
        filepath = DATA_DIR / filename
        try:
            if filepath.exists():
                with open(filepath, "r") as f:
                    data = json.load(f)
                # Merge with defaults to add any missing keys
                merged = {**defaults, **data}
                return merged
        except Exception as e:
            logger.error(f"Error loading {filename}: {e}")
        return defaults.copy()

    def _save_file(self, filename: str, data: dict) -> bool:
        """Save settings to file."""
        filepath = DATA_DIR / filename
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(filepath, "w") as f:
                json.dump(data, f, indent=2, default=str)
            return True
        except Exception as e:
            logger.error(f"Error saving {filename}: {e}")
            return False

    def get_settings(self, agent: str) -> dict[str, Any]:
        """Get settings for an agent."""
        if agent.upper() == "TRADER":
            return self._trader.copy()
        elif agent.upper() == "SNIPER":
            return self._sniper.copy()
        return {}

    def update_settings(self, agent: str, settings: dict[str, Any]) -> dict[str, Any]:
        """Update settings for an agent with validation."""
        agent = agent.upper()

        if agent == "TRADER":
            current = self._trader
            defaults = TRADER_DEFAULTS
            validation = TRADER_VALIDATION
            filename = "trader_settings.json"
        elif agent == "SNIPER":
            current = self._sniper
            defaults = SNIPER_DEFAULTS
            validation = SNIPER_VALIDATION
            filename = "sniper_settings.json"
        else:
            return {"success": False, "error": f"Unknown agent: {agent}"}

        # Validate and apply settings
        warnings = []
        errors = []

        for key, value in settings.items():
            if key not in defaults:
                continue

            # Validate numeric values
            if key in validation:
                rules = validation[key]
                if value < rules["min"]:
                    errors.append(f"{key}: value {value} below minimum {rules['min']}")
                    continue
                if value > rules["max"]:
                    errors.append(f"{key}: value {value} above maximum {rules['max']}")
                    continue

            current[key] = value

        if errors:
            return {"success": False, "errors": errors}

        # Check for aggressive settings
        if agent == "TRADER":
            if current.get("min_rr_ratio", 1.5) < 1.2:
                warnings.append("Low R:R ratio increases risk")
            if current.get("min_confidence", 70) < 60:
                warnings.append("Low confidence threshold may lead to poor trades")

        if agent == "SNIPER":
            triggers = [
                current.get("trigger_breakout", False),
                current.get("trigger_breakdown", False),
                current.get("trigger_liquidation", False),
            ]
            if not any(triggers):
                warnings.append("All triggers disabled - SNIPER will not find opportunities")

        # Update timestamp
        current["last_modified"] = datetime.now().isoformat()

        # Save
        if agent == "TRADER":
            self._trader = current
            self._save_file(filename, current)
        else:
            self._sniper = current
            self._save_file(filename, current)

        # Log change
        self._log_change(agent, settings)

        return {
            "success": True,
            "settings": current,
            "warnings": warnings if warnings else None,
        }

    def reset_to_defaults(self, agent: str) -> dict[str, Any]:
        """Reset agent settings to defaults."""
        agent = agent.upper()

        if agent == "TRADER":
            self._trader = TRADER_DEFAULTS.copy()
            self._trader["last_modified"] = datetime.now().isoformat()
            self._save_file("trader_settings.json", self._trader)
            return {"success": True, "settings": self._trader}
        elif agent == "SNIPER":
            self._sniper = SNIPER_DEFAULTS.copy()
            self._sniper["last_modified"] = datetime.now().isoformat()
            self._save_file("sniper_settings.json", self._sniper)
            return {"success": True, "settings": self._sniper}

        return {"success": False, "error": f"Unknown agent: {agent}"}

    def set_enabled(self, agent: str, enabled: bool) -> dict[str, Any]:
        """Enable or disable an agent."""
        agent = agent.upper()

        if agent == "TRADER":
            self._trader["enabled"] = enabled
            self._trader["last_modified"] = datetime.now().isoformat()
            self._save_file("trader_settings.json", self._trader)
            logger.info(f"[SETTINGS] TRADER {'enabled' if enabled else 'disabled'}")
            return {"success": True, "agent": agent, "enabled": enabled}
        elif agent == "SNIPER":
            self._sniper["enabled"] = enabled
            self._sniper["last_modified"] = datetime.now().isoformat()
            self._save_file("sniper_settings.json", self._sniper)
            logger.info(f"[SETTINGS] SNIPER {'enabled' if enabled else 'disabled'}")
            return {"success": True, "agent": agent, "enabled": enabled}

        return {"success": False, "error": f"Unknown agent: {agent}"}

    def is_enabled(self, agent: str) -> bool:
        """Check if agent is enabled."""
        agent = agent.upper()
        if agent == "TRADER":
            return self._trader.get("enabled", True)
        elif agent == "SNIPER":
            return self._sniper.get("enabled", True)
        return False

    def get_validation_rules(self, agent: str) -> dict[str, Any]:
        """Get validation rules for an agent."""
        agent = agent.upper()
        if agent == "TRADER":
            return TRADER_VALIDATION.copy()
        elif agent == "SNIPER":
            return SNIPER_VALIDATION.copy()
        return {}

    def get_defaults(self, agent: str) -> dict[str, Any]:
        """Get default settings for an agent."""
        agent = agent.upper()
        if agent == "TRADER":
            return TRADER_DEFAULTS.copy()
        elif agent == "SNIPER":
            return SNIPER_DEFAULTS.copy()
        return {}

    def _log_change(self, agent: str, changes: dict) -> None:
        """Log settings change."""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "agent": agent,
            "changes": changes,
        }
        self._change_log.append(entry)
        # Keep only last 100 changes
        if len(self._change_log) > 100:
            self._change_log = self._change_log[-100:]

        logger.info(f"[SETTINGS] {agent} settings updated: {list(changes.keys())}")

    def get_change_log(self, limit: int = 20) -> list[dict[str, Any]]:
        """Get recent settings changes."""
        return self._change_log[-limit:]


# Singleton instance
_agent_settings: Optional[AgentSettings] = None


def get_agent_settings() -> AgentSettings:
    """Get the singleton AgentSettings instance."""
    global _agent_settings
    if _agent_settings is None:
        _agent_settings = AgentSettings()
    return _agent_settings
