# Changelog

## 2026-04-30 — Фикс производительности API

### Проблема
- `/api/historical` и `/api/prices` отвечали **8–30 секунд**.
- Фронтенд показывал "$—", "Загрузка..." из-за таймаутов.
- Причина: отсутствие WAL mode в SQLite, отсутствие LIMIT на выдаче, отсутствие кэширования, тяжёлые вычисления Z-Score при каждом тике.

### Изменения
1. **SQLite WAL mode** (`database.py`)
   - `PRAGMA journal_mode=WAL`
   - `PRAGMA synchronous=NORMAL`
   - Чтение больше не блокируется записью.

2. **LIMIT на выдачу свечей** (`database.py` + `main.py`)
   - `get_candles()` получил параметр `limit`.
   - Графические endpoint'ы (`/api/historical`, `/api/prices`, `/api/zscore`) ограничены **7 днями / 2000 свечей**.
   - Статистика (`/api/stats`, `/api/signal`) ограничена **20 днями / 5000 свечей**.

3. **In-memory кэш** (`main.py`)
   - `_TimedCache` с TTL 30 секунд.
   - Кэшируются `/api/historical`, `/api/prices`, `/api/zscore`, `/api/stats`, `/api/signal`.
   - Снижает нагрузку на БД и ускоряет повторные запросы.

4. **Оптимизация tick logging** (`main.py`)
   - Убран пересчёт Z-Score при каждом тике (каждые 2 сек).
   - Раньше: `get_candles` + `strict_sync` + `compute_zscore` для 5m при каждом poll.
   - Теперь: `zscore=None` в `insert_tick`.

5. **Таймауты HTTP-клиентов** (`alor_client.py`, `hl_client.py`)
   - Read timeout: 30 → **10 сек**
   - Connect timeout: 10 → **5 сек**

### Результат
- Все endpoint'ы отвечают **< 500 мс** (было 8–30 сек).
- Сайт остаётся работоспособным при сбоях внешних API.
