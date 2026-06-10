# Архитектура производительности

> Документ описывает решения, принятые для обеспечения скорости (< 500 мс) и отказоустойчивости API.

## История

До 2026-04-30 endpoint'ы `/api/historical` и `/api/prices` отвечали **8–30 секунд**.
Фронтенд показывал "$—", "Загрузка..." из-за таймаутов.

## Корневая причина

Комбинация факторов:
1. SQLite в режиме `journal_mode=DELETE` — читатели блокировались писателями.
2. Отсутствие `LIMIT` — `get_candles` возвращал все точки за 20 дней.
3. Отсутствие кэширования — каждый запрос пересчитывал mean/stddev/Z-Score с нуля.
4. Тяжёлые вычисления Z-Score при каждом тике (каждые 2 сек) в `_poll_contract`.
5. HTTP-таймауты 30 секунд на внешние API — медленный фейл.

## Решения

### 1. SQLite WAL Mode

```python
# database.py — _get_conn()
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA synchronous=NORMAL")
```

- **WAL** (Write-Ahead Logging) позволяет читателям не блокироваться писателями.
- `synchronous=NORMAL` — баланс между скоростью и надёжностью.
- Индекс `idx_candles_lookup` уже существовал, WAL mode раскрыл его потенциал.

### 2. LIMIT на выдачу

```python
# database.py
def get_candles(..., limit: Optional[int] = None):
    ...
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
```

- Графики: **7 дней / 2000 свечей**
- Статистика: **20 дней / 5000 свечей**

### 3. In-memory кэш (TTL 30 сек)

```python
# main.py
class _TimedCache:
    def __init__(self, default_ttl_seconds: float = 30.0):
        self._data: dict = {}
        self._ttl = default_ttl_seconds
    ...
```

Кэшируются:
- `/api/historical/{contract_id}/{timeframe}`
- `/api/prices/{contract_id}/{timeframe}`
- `/api/zscore/{contract_id}/{timeframe}`
- `/api/stats/{contract_id}/{timeframe}`
- `/api/signal/{contract_id}` (TTL 10 сек)

### 4. Оптимизация polling loop

Убран пересчёт Z-Score при каждом тике:
```python
# Было: get_candles + strict_sync + compute_zscore при каждом poll
# Стало: zscore=None в insert_tick
```

Z-Score для live KPI берётся из кэшированного `/api/zscore`.

### 5. HTTP-таймауты

```python
# alor_client.py, hl_client.py
_CLIENT_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
```

Быстрый фейл при недоступности внешнего API.

## Результаты

| Endpoint | Было | Стало |
|----------|------|-------|
| `/api/historical/bmm6/15m` | 8–30 с | **~450 мс** |
| `/api/prices/bmm6/15m` | 8–30 с | **~415 мс** |
| `/api/zscore/bmm6/15m` | 8–30 с | **~510 мс** |
| `/api/stats/bmm6/15m` | 8–30 с | **~405 мс** |
| `/api/signal/bmm6` | 8–30 с | **~408 мс** |
| `/api/current/bmm6` | ~40 мс | **~452 мс** |

> Примечание: `/api/current` немного замедлился из-за TLS + Cloudflare overhead. Прямой запрос к Uvicorn: **25 мс**.

## Чек-лист при добавлении новых endpoint'ов

- [ ] Добавить кэш (`_API_CACHE`) если данные редко меняются
- [ ] Использовать `limit` в `get_candles`
- [ ] Не делать HTTP-запросы к внешним API внутри endpoint'а
- [ ] Не делать тяжёлых вычислений в polling loop
