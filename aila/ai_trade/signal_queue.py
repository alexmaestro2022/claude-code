"""
SIGNAL QUEUE - Smart queue for trading signals from multiple agents.
Handles priority, deduplication, and correlation checks.
"""

import asyncio
import logging
from collections import deque
from datetime import datetime, timedelta
from enum import IntEnum
from typing import Any, Optional

logger = logging.getLogger("ai_trade.queue")


class SignalPriority(IntEnum):
    """Signal priority levels."""
    LOW = 1
    NORMAL = 2      # TRADER signals
    HIGH = 3        # SNIPER signals (breakouts need fast execution)
    CRITICAL = 4    # Emergency signals


class SignalQueue:
    """Smart queue for trading signals with priority and deduplication."""

    __slots__ = (
        "_queue", "_processed_pairs", "_position_pairs",
        "_cooldowns", "_config", "_stats"
    )

    # Correlated pairs (if one is open, don't open the other)
    CORRELATED_PAIRS: dict[str, list[str]] = {
        "BTCUSDT": ["ETHUSDT"],
        "ETHUSDT": ["BTCUSDT"],
        "SOLUSDT": ["AVAXUSDT", "NEARUSDT"],
        "AVAXUSDT": ["SOLUSDT", "NEARUSDT"],
        "NEARUSDT": ["SOLUSDT", "AVAXUSDT"],
        "DOGEUSDT": ["SHIBUSDT", "PEPEUSDT"],
        "SHIBUSDT": ["DOGEUSDT", "PEPEUSDT"],
        "PEPEUSDT": ["DOGEUSDT", "SHIBUSDT"],
    }

    def __init__(self) -> None:
        self._queue: deque[dict[str, Any]] = deque(maxlen=100)
        self._processed_pairs: set[str] = set()
        self._position_pairs: set[str] = set()
        self._cooldowns: dict[str, datetime] = {}  # agent -> cooldown_until
        self._config = {
            "trader_cooldown_seconds": 300,   # 5 min cooldown for TRADER
            "sniper_cooldown_seconds": 60,    # 1 min cooldown for SNIPER
            "max_queue_size": 50,
            "signal_ttl_seconds": 120,        # Signal expires after 2 min
        }
        self._stats = {
            "total_queued": 0,
            "total_processed": 0,
            "total_rejected": 0,
            "rejected_duplicate": 0,
            "rejected_cooldown": 0,
            "rejected_correlation": 0,
        }

    def add_signal(
        self,
        signal: dict[str, Any],
        agent: str,
        priority: SignalPriority = SignalPriority.NORMAL,
    ) -> dict[str, Any]:
        """Add signal to queue with validation."""
        pair = signal.get("pair", "")

        # Check cooldown for agent
        if self._is_on_cooldown(agent):
            cooldown_until = self._cooldowns.get(agent)
            remaining = (cooldown_until - datetime.utcnow()).total_seconds() if cooldown_until else 0
            self._stats["total_rejected"] += 1
            self._stats["rejected_cooldown"] += 1
            logger.info(f"[QUEUE] Rejected {pair} from {agent}: cooldown ({remaining:.0f}s remaining)")
            return {"added": False, "reason": "cooldown", "remaining_seconds": remaining}

        # Check for duplicate (same pair already in queue or has position)
        if pair in self._processed_pairs:
            self._stats["total_rejected"] += 1
            self._stats["rejected_duplicate"] += 1
            logger.info(f"[QUEUE] Rejected {pair} from {agent}: already processing")
            return {"added": False, "reason": "duplicate"}

        if pair in self._position_pairs:
            self._stats["total_rejected"] += 1
            self._stats["rejected_duplicate"] += 1
            logger.info(f"[QUEUE] Rejected {pair} from {agent}: position already open")
            return {"added": False, "reason": "position_exists"}

        # Check correlation
        correlated = self.CORRELATED_PAIRS.get(pair, [])
        for corr_pair in correlated:
            if corr_pair in self._position_pairs:
                self._stats["total_rejected"] += 1
                self._stats["rejected_correlation"] += 1
                logger.info(f"[QUEUE] Rejected {pair} from {agent}: correlated with open {corr_pair}")
                return {"added": False, "reason": "correlated_position", "correlated_with": corr_pair}

        # Add to queue
        queue_signal = {
            "signal": signal,
            "agent": agent,
            "priority": priority,
            "added_at": datetime.utcnow().isoformat(),
            "expires_at": (datetime.utcnow() + timedelta(seconds=self._config["signal_ttl_seconds"])).isoformat(),
        }

        # Insert by priority (higher priority first)
        inserted = False
        for i, existing in enumerate(self._queue):
            if priority > existing["priority"]:
                self._queue.insert(i, queue_signal)
                inserted = True
                break

        if not inserted:
            self._queue.append(queue_signal)

        self._processed_pairs.add(pair)
        self._stats["total_queued"] += 1

        logger.info(f"[QUEUE] Added {pair} from {agent} (priority={priority.name}, queue_size={len(self._queue)})")
        return {"added": True, "queue_position": len(self._queue)}

    def get_next(self) -> Optional[dict[str, Any]]:
        """Get next signal from queue (highest priority, not expired)."""
        now = datetime.utcnow()

        while self._queue:
            queue_signal = self._queue.popleft()
            expires_at = datetime.fromisoformat(queue_signal["expires_at"])

            if now > expires_at:
                # Signal expired, remove from processed
                pair = queue_signal["signal"].get("pair", "")
                self._processed_pairs.discard(pair)
                logger.info(f"[QUEUE] Signal expired: {pair}")
                continue

            self._stats["total_processed"] += 1
            return queue_signal

        return None

    def mark_processed(self, pair: str, agent: str, success: bool) -> None:
        """Mark signal as processed and set cooldown."""
        self._processed_pairs.discard(pair)

        # Set cooldown for agent
        if agent == "TRADER":
            cooldown = self._config["trader_cooldown_seconds"]
        elif agent == "SNIPER":
            cooldown = self._config["sniper_cooldown_seconds"]
        else:
            cooldown = 60

        self._cooldowns[agent] = datetime.utcnow() + timedelta(seconds=cooldown)
        logger.info(f"[QUEUE] Processed {pair} from {agent}, cooldown={cooldown}s")

    def set_position_open(self, pair: str) -> None:
        """Mark pair as having open position."""
        self._position_pairs.add(pair)
        logger.debug(f"[QUEUE] Position opened: {pair}")

    def set_position_closed(self, pair: str) -> None:
        """Mark pair as having no position."""
        self._position_pairs.discard(pair)
        logger.debug(f"[QUEUE] Position closed: {pair}")

    def sync_positions(self, open_pairs: list[str]) -> None:
        """Sync position pairs with actual open positions."""
        self._position_pairs = set(open_pairs)

    def _is_on_cooldown(self, agent: str) -> bool:
        """Check if agent is on cooldown."""
        cooldown_until = self._cooldowns.get(agent)
        if not cooldown_until:
            return False
        return datetime.utcnow() < cooldown_until

    def get_cooldown_remaining(self, agent: str) -> float:
        """Get remaining cooldown seconds for agent."""
        cooldown_until = self._cooldowns.get(agent)
        if not cooldown_until:
            return 0.0
        remaining = (cooldown_until - datetime.utcnow()).total_seconds()
        return max(0.0, remaining)

    def clear_cooldown(self, agent: str) -> None:
        """Clear cooldown for agent (admin override)."""
        self._cooldowns.pop(agent, None)
        logger.info(f"[QUEUE] Cooldown cleared for {agent}")

    def get_status(self) -> dict[str, Any]:
        """Get queue status."""
        return {
            "queue_size": len(self._queue),
            "processed_pairs": list(self._processed_pairs),
            "position_pairs": list(self._position_pairs),
            "cooldowns": {
                agent: {
                    "until": until.isoformat(),
                    "remaining_seconds": max(0, (until - datetime.utcnow()).total_seconds()),
                }
                for agent, until in self._cooldowns.items()
                if until > datetime.utcnow()
            },
            "stats": self._stats,
            "config": self._config,
        }

    def get_queue_items(self) -> list[dict[str, Any]]:
        """Get all items in queue."""
        return list(self._queue)

    def clear(self) -> None:
        """Clear queue."""
        self._queue.clear()
        self._processed_pairs.clear()
        self._stats["total_queued"] = 0
        self._stats["total_processed"] = 0
        logger.info("[QUEUE] Cleared")

    def update_config(self, config: dict[str, Any]) -> None:
        """Update queue configuration."""
        self._config.update(config)
        logger.info(f"[QUEUE] Config updated: {config}")
