"""
PERSISTENCE — full data persistence for AI Trade.
Local save every 60 seconds + Yandex Object Storage backup every hour.
On server restart, ALL data is restored automatically.
"""

import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger("ai_trade.persistence")

# Load .env file if not already loaded
_env_path = Path("/opt/aila/.env")
if _env_path.exists() and not os.getenv("YANDEX_ACCESS_KEY"):
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _key, _, _value = _line.partition("=")
                os.environ[_key.strip()] = _value.strip()

# Yandex Object Storage configuration (from environment variables)
YANDEX_ACCESS_KEY = os.getenv("YANDEX_ACCESS_KEY", "")
YANDEX_SECRET_KEY = os.getenv("YANDEX_SECRET_KEY", "")
YANDEX_BUCKET = os.getenv("YANDEX_BUCKET", "aila-backups")
YANDEX_ENDPOINT = os.getenv("YANDEX_ENDPOINT", "https://storage.yandexcloud.net")
YANDEX_REGION = os.getenv("YANDEX_REGION", "ru-central1")


class PersistenceManager:
    """Saves data locally and to Yandex Object Storage."""

    __slots__ = (
        "_data_dir",
        "_auto_save_interval",
        "_cloud_backup_interval",
        "_running",
        "_last_save",
        "_last_cloud_backup",
        "_s3_client",
    )

    def __init__(self, data_dir: str = "/opt/aila/data/ai_trade") -> None:
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._auto_save_interval = 60  # Local save every 60 seconds
        self._cloud_backup_interval = 3600  # Cloud backup every hour
        self._running = False
        self._last_save: Optional[datetime] = None
        self._last_cloud_backup: Optional[datetime] = None
        self._s3_client: Any = None

    def _get_path(self, name: str) -> Path:
        return self._data_dir / f"{name}.json"

    async def save(self, name: str, data: Any) -> bool:
        """Save data locally with atomic write."""
        try:
            path = self._get_path(name)
            temp_path = path.with_suffix(".tmp")

            container = {
                "data": data,
                "saved_at": datetime.utcnow().isoformat(),
                "version": "2.0",
            }

            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(container, f, indent=2, ensure_ascii=False, default=str)

            temp_path.replace(path)
            return True
        except Exception as e:
            logger.error(f"Save error ({name}): {e}")
            return False

    async def load(self, name: str, default: Any = None) -> Any:
        """Load data from local file."""
        try:
            path = self._get_path(name)
            if not path.exists():
                return default

            with open(path, "r", encoding="utf-8") as f:
                container = json.load(f)
                return container.get("data", default)
        except Exception as e:
            logger.error(f"Load error ({name}): {e}")
            return default

    async def save_all(self, data_dict: dict[str, Any]) -> int:
        """Save all data locally."""
        saved = 0
        for name, data in data_dict.items():
            if await self.save(name, data):
                saved += 1
        self._last_save = datetime.utcnow()
        return saved

    def _init_s3_client(self) -> Any:
        """Initialize Yandex Object Storage S3 client."""
        try:
            import boto3
            from botocore.config import Config

            config = Config(
                region_name=YANDEX_REGION,
                retries={"max_attempts": 3, "mode": "adaptive"},
            )

            self._s3_client = boto3.client(
                "s3",
                endpoint_url=YANDEX_ENDPOINT,
                aws_access_key_id=YANDEX_ACCESS_KEY,
                aws_secret_access_key=YANDEX_SECRET_KEY,
                config=config,
            )

            logger.info("Yandex Object Storage client initialized")
            return self._s3_client
        except ImportError:
            logger.warning("boto3 not installed: pip install boto3")
            return None
        except Exception as e:
            logger.error(f"S3 client init error: {e}")
            return None

    # Files to backup (whitelist)
    BACKUP_FILES = [
        "knowledge_base.json",
        "trading_state.json",
        "paper_trading.json",
        "observer.json",
        "evolution.json",
        "risk_stats.json",
        "autopilot.json",
        "war_room.json",
        "capital.json",
    ]

    async def backup_to_cloud(self) -> bool:
        """Upload backup to Yandex Object Storage."""
        try:
            if not self._s3_client:
                self._init_s3_client()

            if not self._s3_client:
                return False

            # Create backup data from whitelist files only
            backup_data: dict[str, Any] = {}
            for filename in self.BACKUP_FILES:
                json_file = self._data_dir / filename
                if json_file.exists():
                    with open(json_file, "r", encoding="utf-8") as f:
                        backup_data[json_file.stem] = json.load(f)

            # Backup filename format: ai_trade_backup_YYYY-MM-DD_HH-MM.json
            timestamp = datetime.utcnow().strftime("%Y-%m-%d_%H-%M")
            backup_filename = f"ai_trade_backup_{timestamp}.json"

            # Create backup content
            backup_content = json.dumps(
                {"backup_time": datetime.utcnow().isoformat(), "data": backup_data},
                indent=2,
                ensure_ascii=False,
                default=str,
            )

            # Upload to Yandex Object Storage
            self._s3_client.put_object(
                Bucket=YANDEX_BUCKET,
                Key=backup_filename,
                Body=backup_content.encode("utf-8"),
                ContentType="application/json",
            )

            self._last_cloud_backup = datetime.utcnow()
            logger.info(f"Cloud backup uploaded: {backup_filename}")

            # Cleanup old backups (keep last 24)
            await self._cleanup_old_backups()

            return True
        except Exception as e:
            logger.error(f"Cloud backup error: {e}")
            return False

    async def _cleanup_old_backups(self, keep_count: int = 24) -> None:
        """Delete old backups from Yandex Object Storage, keep last 24."""
        try:
            # List all backup files
            response = self._s3_client.list_objects_v2(
                Bucket=YANDEX_BUCKET,
                Prefix="ai_trade_backup_",
            )

            objects = response.get("Contents", [])
            if len(objects) <= keep_count:
                return

            # Sort by LastModified (newest first)
            objects.sort(key=lambda x: x["LastModified"], reverse=True)

            # Delete old backups
            for obj in objects[keep_count:]:
                self._s3_client.delete_object(
                    Bucket=YANDEX_BUCKET,
                    Key=obj["Key"],
                )
                logger.info(f"Deleted old backup: {obj['Key']}")

        except Exception as e:
            logger.error(f"Cleanup error: {e}")

    async def restore_from_cloud(self) -> bool:
        """Restore data from latest cloud backup."""
        try:
            if not self._s3_client:
                self._init_s3_client()

            if not self._s3_client:
                return False

            # List all backup files
            response = self._s3_client.list_objects_v2(
                Bucket=YANDEX_BUCKET,
                Prefix="ai_trade_backup_",
            )

            objects = response.get("Contents", [])
            if not objects:
                logger.info("No cloud backups found")
                return False

            # Get latest backup (sort by LastModified)
            objects.sort(key=lambda x: x["LastModified"], reverse=True)
            latest = objects[0]

            logger.info(f"Restoring from cloud: {latest['Key']}")

            # Download backup
            response = self._s3_client.get_object(
                Bucket=YANDEX_BUCKET,
                Key=latest["Key"],
            )

            backup_data = json.loads(response["Body"].read().decode("utf-8"))

            # Restore all data files
            for name, container in backup_data.get("data", {}).items():
                path = self._get_path(name)
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(container, f, indent=2, ensure_ascii=False)
                logger.info(f"Restored: {name}")

            return True
        except Exception as e:
            logger.error(f"Cloud restore error: {e}")
            return False

    async def start_auto_save(self, get_data_func: Callable[[], Any]) -> None:
        """Start auto-save locally and to cloud."""
        self._running = True
        self._init_s3_client()

        logger.info(
            f"Persistence started - Local: {self._auto_save_interval}s, "
            f"Cloud: {self._cloud_backup_interval}s"
        )

        last_cloud_time = datetime.utcnow()

        while self._running:
            try:
                # Local save
                data = await get_data_func()
                saved = await self.save_all(data)
                logger.debug(f"Local save: {saved} files")

                # Cloud backup every hour
                elapsed = (datetime.utcnow() - last_cloud_time).total_seconds()
                if elapsed >= self._cloud_backup_interval:
                    await self.backup_to_cloud()
                    last_cloud_time = datetime.utcnow()

                await asyncio.sleep(self._auto_save_interval)
            except Exception as e:
                logger.error(f"Auto-save error: {e}")
                await asyncio.sleep(60)

    def stop_auto_save(self) -> None:
        """Stop auto-save loop."""
        self._running = False

    def get_status(self) -> dict[str, Any]:
        """Get persistence status."""
        files = list(self._data_dir.glob("*.json"))
        return {
            "data_dir": str(self._data_dir),
            "files_count": len(files),
            "files": [f.name for f in files],
            "auto_save_running": self._running,
            "last_local_save": self._last_save.isoformat() if self._last_save else None,
            "last_cloud_backup": (
                self._last_cloud_backup.isoformat() if self._last_cloud_backup else None
            ),
            "s3_connected": self._s3_client is not None,
            "cloud_provider": "Yandex Object Storage",
        }
