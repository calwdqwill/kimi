# Текущая задача — Docker-контейнер + PostgreSQL V4.0

**Дата:** 2026-06-11  
**Ветка:** `V2.0_prod`  
**Статус:** 🔄 В процессе — код готов, ожидается деплой на сервер

---

## Что делаем
Docker-контейнеризация проекта `mo-ex.online` с тремя сервисами:
1. `backend` — FastAPI/uvicorn (порт `8001`).
2. `frontend` — nginx со статикой (порт `8080` на хосте для тестов).
3. `db` — PostgreSQL 16 в именованном volume.

Так как БД выносится в отдельный контейнер, параллельно выполняется миграция с SQLite на PostgreSQL.

---

## Что сделано

### Backend
- `requirements.txt` — добавлен `psycopg2-binary`.
- `backend/config.py` — добавлена поддержка `DATABASE_URL` и авто-сборка URL из `POSTGRES_*`.
- `backend/database.py` — переписан для работы с SQLite **и** PostgreSQL:
  - единый слой с `_placeholder()` для `?` / `%s`;
  - `ON CONFLICT` вместо `INSERT OR IGNORE/REPLACE`;
  - `RETURNING id` для PostgreSQL;
  - сохранён SQLite fallback.
- `backend/backup.py` — теперь делает `pg_dump` для Postgres или копирует SQLite-файл.

### Docker
- `Dockerfile` — образ бэкенда (Python 3.12 slim, порт `8001`, healthcheck).
- `.dockerignore` — исключены секреты, venv, `.git`, данные, ad-hoc скрипты.
- `frontend/Dockerfile` + `frontend/nginx.conf` — контейнер фронтенда.
- `docker-compose.yml` — сервисы `db`, `backend`, `frontend`, volume `pgdata`, сеть `moex`.

### Миграция
- `scripts/migrate_sqlite_to_postgres.py` — переносит все таблицы из `data/dashboard.db` в PostgreSQL с сохранением id и обновлением sequence.

### Deploy
- `deploy/nginx/mo-ex` — `/api/`, `/docs`, `/openapi.json` теперь идут на `127.0.0.1:8001`.
- `deploy/systemd/mo-ex-docker.service` — systemd unit для автозапуска compose.

### Документация
- `CHANGELOG.md` — добавлена запись V4.0.
- `BACKLOG.md` — задачи Docker-контейнер и PostgreSQL отмечены.

---

## Следующий шаг
Деплой на сервер `2.25.143.143`:
1. Передать/обновить `backend/.env` (добавить `POSTGRES_*` + старые токены).
2. Собрать и запустить `docker compose up -d --build`.
3. Выполнить миграцию SQLite → PostgreSQL.
4. Проверить `curl http://localhost:8001/api/health`.
5. Обновить/перезапустить host nginx.

---

*Последнее обновление: 2026-06-11*
