"""
Project configuration.

Time handling rules:
- All internal timestamps are stored and processed as Unix milliseconds (UTC).
- UI timezone conversion happens on the frontend (Europe/Moscow, UTC+3).
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Load tokens from the real .env provided by the user.
# ---------------------------------------------------------------------------
ENV_PATH = Path("C:/Users/viach/Kimi/.env")
if ENV_PATH.exists():
    load_dotenv(dotenv_path=ENV_PATH)
else:
    raise FileNotFoundError(f"Real .env not found at expected path: {ENV_PATH}")

# ---------------------------------------------------------------------------
# Secrets (loaded from .env)
# ---------------------------------------------------------------------------
FINAM_TOKEN: str = os.getenv("FINAM_TOKEN", "")
FINAM_CLIENT_ID: str = os.getenv("FINAM_CLIENT_ID", "")

# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------
FINAM_API_URL: str = "https://api.finam.ru/v1"
HL_API_URL: str = "https://api.hyperliquid.xyz/info"

# ---------------------------------------------------------------------------
# Symbols / instruments
# ---------------------------------------------------------------------------
MOEX_SYMBOL: str = "BMK6@RTSX"       # Finam v1 instrument id
HL_COIN: str = "xyz:BRENTOIL"        # Hyperliquid coin name

# ---------------------------------------------------------------------------
# Dashboard parameters
# ---------------------------------------------------------------------------
TIMEFRAMES: list[str] = ["5m", "15m", "60m"]
ZSCORE_WINDOW: int = 50            # number of candles

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR: Path = Path(__file__).resolve().parent.parent
DB_PATH: Path = BASE_DIR / "data" / "dashboard.db"
