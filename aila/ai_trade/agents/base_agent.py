"""Base agent class for AI trading agents."""

import logging
from datetime import datetime
from typing import Any, Optional


class BaseAgent:
    """Base class for all AI trading agents."""

    __slots__ = ("name", "claude_client", "knowledge_base", "logger")

    def __init__(
        self,
        name: str,
        claude_client: Any,
        knowledge_base: Any,
        log_path: Optional[str] = None,
    ) -> None:
        self.name = name
        self.claude_client = claude_client
        self.knowledge_base = knowledge_base
        self.logger = logging.getLogger(f"ai_trade.{name.lower()}")

        if log_path:
            handler = logging.FileHandler(log_path)
            handler.setLevel(logging.INFO)
            handler.setFormatter(logging.Formatter(
                "%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            ))
            self.logger.addHandler(handler)
            self.logger.setLevel(logging.INFO)

    async def think(self, context: dict[str, Any]) -> dict[str, Any]:
        """Each agent implements its own logic."""
        raise NotImplementedError(f"{self.name} must implement think()")

    def log(self, message: str, level: str = "info") -> None:
        """Log with agent name prefix."""
        log_func = getattr(self.logger, level, self.logger.info)
        log_func(f"[{self.name}] {message}")

    def create_event(
        self, action: str, data: Optional[dict[str, Any]] = None, message: str = ""
    ) -> dict[str, Any]:
        """Create a structured event for the web interface."""
        return {
            "timestamp": datetime.now().isoformat(),
            "agent": self.name,
            "action": action,
            "data": data or {},
            "message": message,
        }
