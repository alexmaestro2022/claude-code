"""
AILA - Application Settings

Central configuration management using pydantic-settings.
"""

from functools import lru_cache
from typing import Optional

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    """Database configuration."""

    model_config = SettingsConfigDict(env_prefix="DB_")

    type: str = "sqlite"  # sqlite | postgresql
    path: str = "./data/aila.db"
    host: str = "localhost"
    port: int = 5432
    name: str = "aila"
    user: str = "aila"
    password: SecretStr = SecretStr("")

    @property
    def url(self) -> str:
        """Get database URL."""
        if self.type == "sqlite":
            return f"sqlite+aiosqlite:///{self.path}"
        else:
            pwd = self.password.get_secret_value()
            return f"postgresql+asyncpg://{self.user}:{pwd}@{self.host}:{self.port}/{self.name}"


class ExchangeSettings(BaseSettings):
    """Exchange API configuration."""

    model_config = SettingsConfigDict(
        env_prefix="BYBIT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    api_key: SecretStr = SecretStr("")
    api_secret: SecretStr = SecretStr("")
    testnet: bool = True
    account_type: str = "futures"  # spot | futures

    # Rate limiting
    max_requests_per_second: int = 10
    retry_attempts: int = 3
    retry_delay: float = 1.0


class StrategySettings(BaseSettings):
    """Strategy configuration."""

    model_config = SettingsConfigDict(
        env_prefix="STRATEGY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # SuperTrend parameters
    st1_period: int = 12
    st1_multiplier: float = 3.0
    st2_period: int = 11
    st2_multiplier: float = 2.0
    st3_period: int = 10
    st3_multiplier: float = 1.0

    # EMA filter
    ema_enabled: bool = True
    ema_period: int = 200
    ema_filter_mode: str = "strict"  # strict | soft

    # Timeframe and pairs
    timeframe: str = "1h"
    trading_pairs: list[str] = Field(default_factory=lambda: ["BTCUSDT", "ETHUSDT"])


class RiskSettings(BaseSettings):
    """Risk management configuration."""

    model_config = SettingsConfigDict(
        env_prefix="RISK_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Stop-loss
    sl_mode: str = "supertrend_line"  # supertrend_line | fixed_percent | atr
    sl_supertrend_line: int = 2
    sl_fixed_percent: float = 2.0
    sl_atr_multiplier: float = 1.5

    # Take-profit
    tp_mode: str = "risk_ratio"  # risk_ratio | fixed_percent | multi_target
    tp_risk_ratio: float = 2.0
    tp_fixed_percent: float = 4.0

    # Position sizing
    position_sizing_mode: str = "fixed_amount"  # fixed_amount | risk_percent | kelly
    risk_per_trade: float = 2.0
    max_position_percent: float = 20.0
    max_open_positions: int = 3

    # Trailing stop
    trailing_enabled: bool = True
    trailing_mode: str = "supertrend"  # supertrend | percent
    trailing_activation: float = 1.0
    trailing_step: float = 0.5

    # Break-even
    breakeven_enabled: bool = False
    breakeven_activation: float = 1.0  # % profit to move SL to entry
    breakeven_offset: float = 0.1  # % above entry for buffer

    # Safety limits
    max_daily_loss_percent: float = 5.0
    max_weekly_loss_percent: float = 10.0
    max_drawdown_percent: float = 15.0
    min_balance_usdt: float = 100.0


class FuturesSettings(BaseSettings):
    """Futures trading configuration."""

    model_config = SettingsConfigDict(
        env_prefix="FUTURES_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    default_leverage: int = 10
    leverage_mode: str = "cross"  # cross | isolated


class TelegramSettings(BaseSettings):
    """Telegram notification configuration."""

    model_config = SettingsConfigDict(env_prefix="TELEGRAM_")

    enabled: bool = False
    bot_token: SecretStr = SecretStr("")
    chat_id: str = ""

    # Notification events
    notify_on_signal: bool = True
    notify_on_entry: bool = True
    notify_on_exit: bool = True
    notify_on_error: bool = True
    notify_daily_report: bool = True


class WebSettings(BaseSettings):
    """Web server configuration."""

    model_config = SettingsConfigDict(env_prefix="WEB_")

    host: str = "0.0.0.0"
    port: int = 8080
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    debug: bool = False


class AppSettings(BaseSettings):
    """Main application settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Application info
    app_name: str = "AILA"
    version: str = "1.0.0"
    env: str = "development"  # development | production
    debug: bool = False
    log_level: str = "INFO"

    # Security
    secret_key: SecretStr = SecretStr("change-me-in-production")
    encryption_key: SecretStr = SecretStr("change-me-in-production")

    # Nested settings
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    exchange: ExchangeSettings = Field(default_factory=ExchangeSettings)
    strategy: StrategySettings = Field(default_factory=StrategySettings)
    risk: RiskSettings = Field(default_factory=RiskSettings)
    futures: FuturesSettings = Field(default_factory=FuturesSettings)
    telegram: TelegramSettings = Field(default_factory=TelegramSettings)
    web: WebSettings = Field(default_factory=WebSettings)

    @property
    def is_production(self) -> bool:
        """Check if running in production."""
        return self.env == "production"

    @property
    def is_testnet(self) -> bool:
        """Check if using testnet."""
        return self.exchange.testnet


@lru_cache
def get_settings() -> AppSettings:
    """
    Get cached application settings.

    Uses lru_cache to ensure settings are only loaded once.
    """
    return AppSettings()


# Global settings instance
settings = get_settings()
