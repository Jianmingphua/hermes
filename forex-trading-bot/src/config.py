"""
Forex Trading Bot - Configuration Loader
Loads environment variables from config/.env and provides typed access.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
PROJECT_ROOT = Path(__file__).parent.parent
ENV_PATH = PROJECT_ROOT / "config" / ".env"

load_dotenv(dotenv_path=ENV_PATH)


class Config:
    """Central configuration for the forex trading bot."""

    # OANDA
    OANDA_API_KEY: str = os.getenv("OANDA_API_KEY", "")
    OANDA_ACCOUNT_ID: str = os.getenv("OANDA_ACCOUNT_ID", "")
    OANDA_ENVIRONMENT: str = os.getenv("OANDA_ENVIRONMENT", "practice")

    # Telegram
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

    # Trading
    DEFAULT_INSTRUMENTS: list = os.getenv(
        "DEFAULT_INSTRUMENTS", "EUR_USD,GBP_USD,USD_JPY"
    ).split(",")
    DEFAULT_GRANULARITY: str = os.getenv("DEFAULT_GRANULARITY", "H1")
    RISK_PER_TRADE: float = float(os.getenv("RISK_PER_TRADE", "0.01"))
    MAX_DAILY_LOSS: float = float(os.getenv("MAX_DAILY_LOSS", "0.03"))
    MAX_OPEN_POSITIONS: int = int(os.getenv("MAX_OPEN_POSITIONS", "3"))

    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    LOG_DIR: Path = PROJECT_ROOT / os.getenv("LOG_DIR", "logs")

    @property
    def oanda_base_url(self) -> str:
        if self.OANDA_ENVIRONMENT == "live":
            return "https://api-fxtrade.oanda.com/v3/"
        return "https://api-fxpractice.oanda.com/v3/"

    def validate(self) -> list[str]:
        """Return list of missing required config values."""
        errors = []
        if not self.OANDA_API_KEY:
            errors.append("OANDA_API_KEY is required")
        if not self.OANDA_ACCOUNT_ID:
            errors.append("OANDA_ACCOUNT_ID is required")
        return errors


config = Config()
