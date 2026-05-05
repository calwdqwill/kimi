"""
Project configuration — multi-contract Brent Spread Dashboard.

Time handling rules:
- All internal timestamps are stored and processed as Unix milliseconds (UTC).
- UI timezone conversion happens on the frontend (Europe/Moscow, UTC+3).
"""

import os
import logging
from pathlib import Path
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Load tokens from .env
# Priority: 1) backend/.env  2) parent folder/.env
# ---------------------------------------------------------------------------
_backend_env = Path(__file__).resolve().parent / ".env"
_parent_env = Path(__file__).resolve().parent.parent / ".env"

env_loaded = False
env_path_used = None

if _backend_env.exists():
    load_dotenv(dotenv_path=_backend_env)
    env_loaded = True
    env_path_used = str(_backend_env)
    logger.info("Loaded .env from backend folder: %s", _backend_env)
elif _parent_env.exists():
    load_dotenv(dotenv_path=_parent_env)
    env_loaded = True
    env_path_used = str(_parent_env)
    logger.info("Loaded .env from parent folder: %s", _parent_env)
else:
    logger.warning("No .env file found! Checked: %s, %s", _backend_env, _parent_env)

# ---------------------------------------------------------------------------
# Secrets (loaded from .env)
# ---------------------------------------------------------------------------
FINAM_TOKEN: str = os.getenv("FINAM_TOKEN", "").strip()
FINAM_CLIENT_ID: str = os.getenv("FINAM_CLIENT_ID", "").strip()
ALOR_REFRESH_TOKEN: str = os.getenv("ALOR_REFRESH_TOKEN", "").strip()
ALOR_EXCHANGE: str = os.getenv("ALOR_EXCHANGE", "MOEX").strip()

# Diagnostic logging
if FINAM_TOKEN:
    logger.info("FINAM_TOKEN loaded: %s...%s (len=%d)", FINAM_TOKEN[:20], FINAM_TOKEN[-20:], len(FINAM_TOKEN))
else:
    logger.info("FINAM_TOKEN not set (Alor mode)")

if ALOR_REFRESH_TOKEN:
    logger.info("ALOR_REFRESH_TOKEN loaded: %s...%s (len=%d)", ALOR_REFRESH_TOKEN[:20], ALOR_REFRESH_TOKEN[-20:], len(ALOR_REFRESH_TOKEN))
else:
    logger.error("ALOR_REFRESH_TOKEN is EMPTY! Check your .env file at: %s", env_path_used or "NOT FOUND")

if not FINAM_CLIENT_ID:
    logger.warning("FINAM_CLIENT_ID not set in .env")

# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------
FINAM_API_URL: str = "https://api.finam.ru/v1"
ALOR_API_URL: str = os.getenv("ALOR_API_URL", "https://api.alor.ru")
ALOR_OAUTH_URL: str = os.getenv("ALOR_OAUTH_URL", "https://oauth.alor.ru")
HL_API_URL: str = "https://api.hyperliquid.xyz/info"

logger.info("Using ALOR_API_URL: %s", ALOR_API_URL)

# ---------------------------------------------------------------------------
# Asset configuration — multi-asset support
# ---------------------------------------------------------------------------
# expiry_type: "monthly" (Brent) or "quarterly" (Gold, Silver)
# history_mode: "month_start" (1st of month) or "contract_start" (listing date)
# ---------------------------------------------------------------------------
ASSETS = {
    "brent": {
        "name": "Brent Oil",
        "unit": "USD/bbl",
        "lot_size": 1,
        "price_step": 0.01,
        "expiry_type": "monthly",
        "expiry_day": 1,
        "history_mode": "month_start",
    },
    "gold": {
        "name": "Gold",
        "unit": "USD/oz",
        "lot_size": 2,
        "price_step": 0.1,
        "expiry_type": "quarterly",
        "expiry_day": 19,
        "history_mode": "contract_start",
    },
    "silver": {
        "name": "Silver",
        "unit": "USD/oz",
        "lot_size": 1,
        "price_step": 0.01,
        "expiry_type": "quarterly",
        "expiry_day": 19,
        "history_mode": "contract_start",
    },
}

# ---------------------------------------------------------------------------
# Default contracts — shipped with the app
# ---------------------------------------------------------------------------
# contract_start_date: ISO date for quarterly contracts (Gold/Silver)
#   when history_mode = "contract_start", history loads from this date
# contract_month / contract_year: for monthly contracts (Brent)
#   when history_mode = "month_start", history loads from 1st of this month
# ---------------------------------------------------------------------------
DEFAULT_CONTRACTS = [
    # Brent — monthly
    {
        "id": "bmm6",
        "name": "BMM6",
        "asset": "brent",
        "moex_symbol": "BMM6@RTSX",
        "hl_coin": "xyz:BRENTOIL",
        "is_active": True,
        "contract_month": 5,
        "contract_year": 2026,
    },
    {
        "id": "bmk6",
        "name": "BMK6",
        "asset": "brent",
        "moex_symbol": "BMK6@RTSX",
        "hl_coin": "xyz:BRENTOIL",
        "is_active": True,
        "contract_month": 4,
        "contract_year": 2026,
    },
    {
        "id": "bmn6",
        "name": "BMN6",
        "asset": "brent",
        "moex_symbol": "BMN6@RTSX",
        "hl_coin": "xyz:BRENTOIL",
        "is_active": True,
        "contract_month": 6,
        "contract_year": 2026,
    },
    # Gold — quarterly
    {
        "id": "gnm6",
        "name": "GNM6",
        "asset": "gold",
        "moex_symbol": "GNM6@RTSX",
        "hl_coin": "xyz:GOLD",
        "is_active": True,
        "contract_start_date": "2026-03-20",  # listing date approx
    },
    {
        "id": "gnn6",
        "name": "GNN6",
        "asset": "gold",
        "moex_symbol": "GNN6@RTSX",
        "hl_coin": "xyz:GOLD",
        "is_active": True,
        "contract_start_date": "2026-06-20",  # next quarter approx
    },
    # Silver — quarterly
    {
        "id": "s1m6",
        "name": "S1M6",
        "asset": "silver",
        "moex_symbol": "S1M6@RTSX",
        "hl_coin": "xyz:SILVER",
        "is_active": True,
        "contract_start_date": "2026-03-20",  # listing date approx
    },
    {
        "id": "s1n6",
        "name": "S1N6",
        "asset": "silver",
        "moex_symbol": "S1N6@RTSX",
        "hl_coin": "xyz:SILVER",
        "is_active": True,
        "contract_start_date": "2026-06-20",  # next quarter approx
    },
]

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