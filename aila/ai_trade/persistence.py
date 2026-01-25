"""
PERSISTENCE — full data persistence for AI Trade.
Local save every 60 seconds + Google Drive backup every hour.
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


class PersistenceManager:
    """Saves data locally and to Google Drive."""

    __slots__ = (
        "_data_dir",
        "_auto_save_interval",
        "_cloud_backup_interval",
        "_running",
        "_last_save",
        "_last_cloud_backup",
        "_drive_service",
        "_drive_folder_id",
    )

    def __init__(self, data_dir: str = "/opt/aila/data/ai_trade") -> None:
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._auto_save_interval = 60  # Local save every 60 seconds
        self._cloud_backup_interval = 3600  # Google Drive every hour
        self._running = False
        self._last_save: Optional[datetime] = None
        self._last_cloud_backup: Optional[datetime] = None
        self._drive_service: Any = None
        self._drive_folder_id = "1w9vCx3hscA_DxT5aMmilOmIVKIxNA3Zf"

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

    def _init_drive_service(self) -> Any:
        """Initialize Google Drive API."""
        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build

            creds_path = "/opt/aila/config/google_credentials.json"
            if not os.path.exists(creds_path):
                logger.warning("Google credentials not found")
                return None

            creds = service_account.Credentials.from_service_account_file(
                creds_path,
                scopes=["https://www.googleapis.com/auth/drive.file"],
            )

            self._drive_service = build("drive", "v3", credentials=creds)
            logger.info("Google Drive service initialized")
            return self._drive_service
        except ImportError:
            logger.warning("Google Drive libraries not installed")
            return None
        except Exception as e:
            logger.error(f"Google Drive init error: {e}")
            return None

    async def backup_to_cloud(self) -> bool:
        """Upload backup to Google Drive."""
        try:
            if not self._drive_service:
                self._init_drive_service()

            if not self._drive_service:
                return False

            from googleapiclient.http import MediaFileUpload

            # Create backup archive
            backup_data: dict[str, Any] = {}
            for json_file in self._data_dir.glob("*.json"):
                with open(json_file, "r", encoding="utf-8") as f:
                    backup_data[json_file.stem] = json.load(f)

            # Save to temp file
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            backup_filename = f"aila_backup_{timestamp}.json"
            backup_path = self._data_dir / backup_filename

            with open(backup_path, "w", encoding="utf-8") as f:
                json.dump(
                    {"backup_time": datetime.utcnow().isoformat(), "data": backup_data},
                    f,
                    indent=2,
                    ensure_ascii=False,
                    default=str,
                )

            # Upload to Google Drive
            file_metadata = {"name": backup_filename, "parents": [self._drive_folder_id]}
            media = MediaFileUpload(str(backup_path), mimetype="application/json")

            file = (
                self._drive_service.files()
                .create(body=file_metadata, media_body=media, fields="id")
                .execute()
            )

            # Remove temp file
            backup_path.unlink()

            self._last_cloud_backup = datetime.utcnow()
            logger.info(f"Cloud backup uploaded: {backup_filename} (ID: {file.get('id')})")

            # Cleanup old backups (keep last 24)
            await self._cleanup_old_backups()

            return True
        except Exception as e:
            logger.error(f"Cloud backup error: {e}")
            return False

    async def _cleanup_old_backups(self, keep_count: int = 24) -> None:
        """Delete old backups from Google Drive."""
        try:
            results = (
                self._drive_service.files()
                .list(
                    q=f"'{self._drive_folder_id}' in parents and name contains 'aila_backup_'",
                    orderBy="createdTime desc",
                    fields="files(id, name, createdTime)",
                )
                .execute()
            )

            files = results.get("files", [])

            if len(files) > keep_count:
                for file in files[keep_count:]:
                    self._drive_service.files().delete(fileId=file["id"]).execute()
                    logger.info(f"Deleted old backup: {file['name']}")
        except Exception as e:
            logger.error(f"Cleanup error: {e}")

    async def restore_from_cloud(self) -> bool:
        """Restore data from latest cloud backup."""
        try:
            if not self._drive_service:
                self._init_drive_service()

            if not self._drive_service:
                return False

            # Find latest backup
            results = (
                self._drive_service.files()
                .list(
                    q=f"'{self._drive_folder_id}' in parents and name contains 'aila_backup_'",
                    orderBy="createdTime desc",
                    pageSize=1,
                    fields="files(id, name)",
                )
                .execute()
            )

            files = results.get("files", [])
            if not files:
                logger.info("No cloud backups found")
                return False

            latest = files[0]
            logger.info(f"Restoring from cloud: {latest['name']}")

            # Download file
            from io import BytesIO

            from googleapiclient.http import MediaIoBaseDownload

            request = self._drive_service.files().get_media(fileId=latest["id"])
            content = BytesIO()
            downloader = MediaIoBaseDownload(content, request)

            done = False
            while not done:
                _, done = downloader.next_chunk()

            content.seek(0)
            backup_data = json.load(content)

            # Restore data
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
        self._init_drive_service()

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
            "drive_connected": self._drive_service is not None,
        }
