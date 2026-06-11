#!/usr/bin/env python3
"""
Deploy MO-EX Docker Compose to the production server.

Usage:
    set MOEX_ROOT_PASSWORD=...
    python scripts/deploy_docker_remote.py
"""

import os
import re
import sys
import time
from io import StringIO
from pathlib import Path

# Ensure UTF-8 output on Windows terminals when printing remote logs.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import paramiko

HOST = "2.25.143.143"
USER = "root"
PORT = 22
PROJECT_DIR = "/opt/mo-ex"
REPO = "https://github.com/calwdqwill/kimi.git"
BRANCH = "V2.0_prod"


def get_password() -> str:
    pw = os.getenv("MOEX_ROOT_PASSWORD", "").strip()
    if not pw:
        print("ERROR: set MOEX_ROOT_PASSWORD environment variable")
        sys.exit(1)
    return pw


def run(ssh: paramiko.SSHClient, cmd: str, timeout: int = 60) -> tuple[int, str, str]:
    print(f"$ {cmd}")
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    exit_code = stdout.channel.recv_exit_status()
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    if out.strip():
        print(out.strip())
    if err.strip():
        print("STDERR:", err.strip())
    return exit_code, out, err


def upload(ssh: paramiko.SSHClient, content: str, remote_path: str, mode: str = "0644") -> None:
    sftp = ssh.open_sftp()
    try:
        with sftp.file(remote_path, "w") as f:
            f.write(content)
        sftp.chmod(remote_path, int(mode, 8))
    finally:
        sftp.close()


def download(ssh: paramiko.SSHClient, remote_path: str) -> str:
    sftp = ssh.open_sftp()
    try:
        with sftp.file(remote_path, "r") as f:
            return f.read().decode("utf-8", errors="replace")
    finally:
        sftp.close()


def main() -> None:
    password = get_password()
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    print(f"Connecting to {HOST} as {USER}...")
    ssh.connect(HOST, port=PORT, username=USER, password=password, timeout=30)
    print("Connected.")

    # 1. Ensure project directory and repo
    run(ssh, f"mkdir -p {PROJECT_DIR}")
    exists = run(ssh, f"test -d {PROJECT_DIR}/.git && echo yes || echo no")[1].strip()
    if exists == "yes":
        run(ssh, f"cd {PROJECT_DIR} && git fetch origin && git checkout -f {BRANCH} && git reset --hard origin/{BRANCH}", timeout=120)
    else:
        run(ssh, f"cd {PROJECT_DIR} && git clone --branch {BRANCH} {REPO} .", timeout=120)

    # 2. Read existing .env (prefer backend/.env, fallback to parent .env)
    env_path = f"{PROJECT_DIR}/backend/.env"
    parent_env_path = f"{PROJECT_DIR}/.env"
    try:
        existing_env = download(ssh, env_path)
    except FileNotFoundError:
        existing_env = ""
    try:
        parent_env = download(ssh, parent_env_path)
    except FileNotFoundError:
        parent_env = ""

    def env_get(key: str, default: str = "") -> str:
        for src in (existing_env, parent_env):
            m = re.search(rf"^{re.escape(key)}=(.*)$", src, re.MULTILINE)
            if m:
                val = m.group(1).strip()
                if val:
                    return val
        return default

    alor_token = env_get("ALOR_REFRESH_TOKEN")
    telegram_token = env_get("TELEGRAM_BOT_TOKEN")
    telegram_chat = env_get("TELEGRAM_CHAT_ID")

    run(ssh, f"mkdir -p {PROJECT_DIR}/backend")

    new_env = f"""# PostgreSQL (Docker Compose)
POSTGRES_HOST=db
POSTGRES_DB=moex
POSTGRES_USER=moex
POSTGRES_PASSWORD={env_get("POSTGRES_PASSWORD", "moex")}
POSTGRES_PORT=5432

# Data providers
ALOR_REFRESH_TOKEN={alor_token}
ALOR_EXCHANGE=MOEX
ALOR_API_URL=https://api.alor.ru
ALOR_OAUTH_URL=https://oauth.alor.ru

# Telegram
TELEGRAM_BOT_TOKEN={telegram_token}
TELEGRAM_CHAT_ID={telegram_chat}
"""
    upload(ssh, new_env, env_path, "0600")
    print(f"Updated {env_path}")

    # 3. Install Docker if missing
    docker_ok = run(ssh, "docker --version && docker compose version")[0] == 0
    if not docker_ok:
        print("Installing Docker...")
        run(ssh, "apt-get update && apt-get install -y ca-certificates curl gnupg && install -m 0755 -d /etc/apt/keyrings && curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg && chmod a+r /etc/apt/keyrings/docker.gpg", timeout=180)
        run(ssh, 'echo "deb [arch="$(dpkg --print-architecture)" signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu "$(. /etc/os-release && echo "$VERSION_CODENAME")" stable" > /etc/apt/sources.list.d/docker.list')
        run(ssh, "apt-get update && apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin", timeout=180)
        run(ssh, "systemctl enable --now docker")

    # 4. Update host nginx config
    run(ssh, f"cp {PROJECT_DIR}/deploy/nginx/mo-ex /etc/nginx/sites-available/mo-ex")
    run(ssh, "ln -sf /etc/nginx/sites-available/mo-ex /etc/nginx/sites-enabled/mo-ex && nginx -t && systemctl reload nginx")

    # 5. Stop legacy service if running
    run(ssh, "systemctl stop mo-ex || true")
    run(ssh, "systemctl disable mo-ex || true")

    # 6. Build and start compose (backend not running yet to allow clean migration)
    run(ssh, f"cd {PROJECT_DIR} && docker compose down || true", timeout=120)
    run(ssh, f"cd {PROJECT_DIR} && docker compose up -d db", timeout=120)
    # Wait for Postgres healthy
    for _ in range(30):
        ok = run(ssh, f"cd {PROJECT_DIR} && docker compose ps db | grep healthy")[0] == 0
        if ok:
            break
        time.sleep(2)

    # 7. Migrate legacy SQLite data if present
    sqlite_path = f"{PROJECT_DIR}/data/dashboard.db"
    has_sqlite = run(ssh, f"test -f {sqlite_path} && echo yes || echo no")[1].strip() == "yes"
    if has_sqlite:
        print("Migrating SQLite data to PostgreSQL...")
        run(ssh, f"cd {PROJECT_DIR} && docker compose run --rm backend sh -c \"PYTHONPATH=/app/backend python /app/scripts/migrate_sqlite_to_postgres.py /app/data/dashboard.db\"", timeout=600)

    # 8. Start backend + frontend
    run(ssh, f"cd {PROJECT_DIR} && docker compose up -d --build", timeout=300)

    # 9. Install systemd unit for compose auto-start
    run(ssh, f"cp {PROJECT_DIR}/deploy/systemd/mo-ex-docker.service /etc/systemd/system/mo-ex-docker.service")
    run(ssh, "systemctl daemon-reload && systemctl enable --now mo-ex-docker")

    # 10. Verify
    for _ in range(20):
        code, out, _ = run(ssh, "curl -fsS http://127.0.0.1:8001/api/health")
        if code == 0:
            print("Health check:")
            print(out)
            break
        time.sleep(3)
    else:
        print("WARNING: health check did not pass")

    ssh.close()
    print("Deployment complete.")


if __name__ == "__main__":
    main()
