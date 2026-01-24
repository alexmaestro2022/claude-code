import logging
from datetime import datetime
from typing import Optional


class BaseAgent:
    """Base class for all AI trading agents."""

    def __init__(self, name: str, claude_client, knowledge_base, log_path: Optional[str] = None):
        self.name = name
        self.claude_client = claude_client
        self.knowledge_base = knowledge_base
        self.logger = logging.getLogger(f"ai_trade.{name.lower()}")

        # Setup file handler if log_path provided
        if log_path:
            handler = logging.FileHandler(log_path)
            handler.setLevel(logging.INFO)
            formatter = logging.Formatter(
                "%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S"
            )
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
            self.logger.setLevel(logging.INFO)

    async def think(self, context: dict) -> dict:
        """Each agent implements its own logic."""
        raise NotImplementedError(f"{self.name} must implement think()")

    def log(self, message: str, level: str = "info"):
        """Log with agent name prefix."""
        log_func = getattr(self.logger, level, self.logger.info)
        log_func(f"[{self.name}] {message}")

    def create_event(self, action: str, data: dict = None, message: str = "") -> dict:
        """Create a structured event for the web interface."""
        return {
            "timestamp": datetime.now().isoformat(),
            "agent": self.name,
            "action": action,
            "data": data or {},
            "message": message,
        }
