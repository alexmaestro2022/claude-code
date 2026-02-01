"""Base agent class for AI trading agents."""

import logging
import os
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
            self._setup_file_handler(log_path)

        # Always ensure logger level is set
        if not self.logger.level:
            self.logger.setLevel(logging.INFO)

    def _setup_file_handler(self, log_path: str) -> None:
        """Setup file handler, avoiding duplicates and ensuring directory exists."""
        # Ensure log directory exists
        log_dir = os.path.dirname(log_path)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)

        # Avoid adding duplicate handlers for the same file
        for h in self.logger.handlers:
            if isinstance(h, logging.FileHandler) and h.baseFilename == os.path.abspath(log_path):
                return

        handler = logging.FileHandler(log_path)
        handler.setLevel(logging.INFO)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        self.logger.addHandler(handler)
        self.logger.setLevel(logging.INFO)
        self.logger.info(f"[{self.name}] Agent logger initialized")

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
