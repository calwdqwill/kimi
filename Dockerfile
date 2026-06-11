# MO-EX Backend (FastAPI + multi-contract spread dashboard)
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8001

# Install PostgreSQL client tools for pg_dump backups
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend

# Install Python dependencies first (better layer caching)
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r /app/requirements.txt

# Copy backend, frontend (static fallback), and utility scripts
COPY backend/ /app/backend/
COPY frontend/ /app/frontend/
COPY scripts/ /app/scripts/

# Create data directory for backups / SQLite fallback
# Placeholder .env suppresses "file not found" warning; real values come from env_file.
RUN mkdir -p /app/data && touch /app/backend/.env

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8001/api/health')"

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
