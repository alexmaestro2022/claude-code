"""OAuth token auto-refresh for Claude CLI credentials."""

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import aiohttp

logger = logging.getLogger("oauth_refresh")

CREDENTIALS_PATH = Path("/opt/aila/.claude/.credentials.json")
HOME_CREDENTIALS = Path("/home/aila/.claude/.credentials.json")
TOKEN_ENDPOINT = "https://platform.claude.com/v1/oauth/token"
CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
SCOPES = "user:inference user:mcp_servers user:profile user:sessions:claude_code"
MIN_REFRESH_INTERVAL = 300  # 5 min between attempts


class OAuthRefresher:
    """Auto-refresh OAuth tokens using refresh_token grant."""

    __slots__ = ("_last_refresh_attempt",)

    def __init__(self) -> None:
        self._last_refresh_attempt: Optional[datetime] = None

    def get_credentials(self) -> dict:
        """Read current credentials from file."""
        return json.loads(CREDENTIALS_PATH.read_text())

    def get_remaining_seconds(self) -> int:
        """Seconds until token expires. 0 if expired or unreadable."""
        try:
            data = self.get_credentials()
            expires_ms = data.get("claudeAiOauth", {}).get("expiresAt", 0)
            if not expires_ms:
                return 0
            now_ms = datetime.now(tz=timezone.utc).timestamp() * 1000
            return max(0, int((expires_ms - now_ms) / 1000))
        except Exception:
            return 0

    async def refresh_token(self) -> bool:
        """Refresh OAuth token via refresh_token grant."""
        try:
            data = self.get_credentials()
            oauth = data.get("claudeAiOauth", {})
            refresh_tok = oauth.get("refreshToken")
            if not refresh_tok:
                logger.error("[OAUTH] No refreshToken in credentials")
                return False

            self._last_refresh_attempt = datetime.now(tz=timezone.utc)

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    TOKEN_ENDPOINT,
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_tok,
                        "client_id": CLIENT_ID,
                        "scope": SCOPES,
                    },
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        err = await resp.text()
                        logger.error(f"[OAUTH] Refresh failed: {resp.status} {err[:200]}")
                        return False

                    result = await resp.json()

            # Update credentials
            if "access_token" in result:
                oauth["accessToken"] = result["access_token"]
            if "refresh_token" in result:
                oauth["refreshToken"] = result["refresh_token"]
            if "expires_in" in result:
                new_expiry = datetime.now(tz=timezone.utc) + timedelta(
                    seconds=result["expires_in"]
                )
                oauth["expiresAt"] = int(new_expiry.timestamp() * 1000)

            data["claudeAiOauth"] = oauth

            # Save to both locations
            for path in (CREDENTIALS_PATH, HOME_CREDENTIALS):
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps(data, indent=2))
                except PermissionError:
                    logger.warning(f"[OAUTH] Cannot write to {path}")

            remaining = self.get_remaining_seconds()
            hours = remaining // 3600
            mins = (remaining % 3600) // 60
            logger.info(f"[OAUTH] Token refreshed! Expires in {hours}h {mins}m")
            return True

        except Exception as e:
            logger.error(f"[OAUTH] Refresh error: {e}")
            return False

    async def ensure_valid_token(self, min_remaining: int = 7200) -> bool:
        """Check and refresh token if needed. Returns True if token is valid."""
        remaining = self.get_remaining_seconds()

        if remaining > min_remaining:
            return True  # Token still fresh

        # Rate-limit refresh attempts
        if self._last_refresh_attempt:
            elapsed = (
                datetime.now(tz=timezone.utc) - self._last_refresh_attempt
            ).total_seconds()
            if elapsed < MIN_REFRESH_INTERVAL:
                logger.debug("[OAUTH] Skipping refresh — too soon since last attempt")
                return remaining > 0

        logger.warning(f"[OAUTH] Token expires in {remaining}s, refreshing...")
        refreshed = await self.refresh_token()
        if refreshed:
            return True
        return remaining > 0  # Still valid but refresh failed
