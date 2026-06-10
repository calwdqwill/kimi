"""
SQLite backup script with rotation.

Run manually:
    python backup.py

Or via systemd timer (see /etc/systemd/system/dashboard-backup.*).
"""

import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path

from config import DB_PATH, BASE_DIR

BACKUP_DIR = BASE_DIR / "data" / "backups"
KEEP_DAYS = 7


def run_backup() -> Path:
    """Create a timestamped copy of the SQLite DB."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    if not DB_PATH.exists():
        raise FileNotFoundError(f"Database not found: {DB_PATH}")

    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    backup_path = BACKUP_DIR / f"dashboard_{ts}.db"

    # shutil.copy2 preserves metadata
    shutil.copy2(DB_PATH, backup_path)
    print(f"Backup created: {backup_path}")
    return backup_path


def rotate_backups() -> int:
    """Delete backups older than KEEP_DAYS. Returns number deleted."""
    if not BACKUP_DIR.exists():
        return 0

    cutoff = datetime.now() - timedelta(days=KEEP_DAYS)
    deleted = 0

    for file in BACKUP_DIR.glob("dashboard_*.db"):
        # Extract timestamp from filename
        try:
            # dashboard_YYYY-MM-DD_HHMMSS.db
            ts_str = file.stem.split("_", 1)[1]
            file_ts = datetime.strptime(ts_str, "%Y-%m-%d_%H%M%S")
            if file_ts < cutoff:
                file.unlink()
                deleted += 1
                print(f"Deleted old backup: {file.name}")
        except (ValueError, IndexError):
            continue

    return deleted


if __name__ == "__main__":
    backup = run_backup()
    deleted = rotate_backups()
    print(f"Backup complete. Kept last {KEEP_DAYS} days, deleted {deleted} old backups.")
