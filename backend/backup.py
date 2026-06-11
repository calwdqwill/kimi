"""
Database backup script with rotation.

Supports both SQLite (legacy) and PostgreSQL (Docker).

Run manually:
    python backup.py

Or via cron / systemd timer.
"""

import os
import shutil
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from config import DB_PATH, BASE_DIR, DATABASE_URL

BACKUP_DIR = BASE_DIR / "data" / "backups"
KEEP_DAYS = 7


def _backup_path(extension: str) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    return BACKUP_DIR / f"dashboard_{ts}.{extension}"


def _rotate(glob_pattern: str) -> int:
    if not BACKUP_DIR.exists():
        return 0
    cutoff = datetime.now() - timedelta(days=KEEP_DAYS)
    deleted = 0
    for file in BACKUP_DIR.glob(glob_pattern):
        try:
            ts_str = file.stem.split("_", 1)[1]
            file_ts = datetime.strptime(ts_str, "%Y-%m-%d_%H%M%S")
            if file_ts < cutoff:
                file.unlink()
                deleted += 1
                print(f"Deleted old backup: {file.name}")
        except (ValueError, IndexError):
            continue
    return deleted


def _backup_sqlite() -> Path:
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Database not found: {DB_PATH}")
    backup_path = _backup_path("db")
    shutil.copy2(DB_PATH, backup_path)
    print(f"SQLite backup created: {backup_path}")
    return backup_path


def _backup_postgres() -> Path:
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set")
    parsed = urlparse(DATABASE_URL)
    host = parsed.hostname or "localhost"
    port = parsed.port or 5432
    user = parsed.username or "postgres"
    password = parsed.password or ""
    dbname = parsed.path.lstrip("/") or "postgres"

    backup_path = _backup_path("dump")
    env = os.environ.copy()
    env["PGPASSWORD"] = password

    cmd = [
        "pg_dump",
        "-h", host,
        "-p", str(port),
        "-U", user,
        "-Fc",
        "-f", str(backup_path),
        dbname,
    ]
    subprocess.run(cmd, env=env, check=True)
    print(f"PostgreSQL backup created: {backup_path}")
    return backup_path


def run_backup() -> Path:
    """Create a timestamped backup of the active database."""
    if DATABASE_URL:
        backup = _backup_postgres()
        deleted = _rotate("dashboard_*.dump")
    else:
        backup = _backup_sqlite()
        deleted = _rotate("dashboard_*.db")
    print(f"Backup complete. Kept last {KEEP_DAYS} days, deleted {deleted} old backups.")
    return backup


if __name__ == "__main__":
    run_backup()
