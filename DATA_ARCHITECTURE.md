# Архитектура исторических данных

> **Ключевой документ для агента.** Прочитай этот файл в начале сессии, если работаешь с историей, графиками, API или базой данных.

---

## 1. Источники данных

| Источник | Что даёт | API | Где хранится |
|----------|----------|-----|--------------|
| **Alor** | MOEX фьючерсы (OHLCV) | `https://api.alor.ru/md/v2/history` | Таблица `alor_candles` |
| **Hyperliquid** | Крипто-фьючерсы (close) | REST API `/candles` | Таблица `candles` (source='hyperliquid') |
| **Legacy MOEX** | Исторические close (Finam/старое) | — | Таблица `candles` (source='moex') |

**Важно:** Сейчас активный источник MOEX-данных — **Alor**. Legacy `candles` (source='moex') содержит старые данные (Finam), которые использовались до перехода на Alor. Они оставлены как резерв и для мёрджа апрельских пробелов (mini→full).

---

## 2. Структура таблиц БД

База: `/opt/dashboard/backend/data/dashboard.db` (SQLite, WAL mode)

### `contracts` — справочник контрактов
```sql
id              TEXT PRIMARY KEY   -- 'brm6', 'gnm6', 's1m6'
name            TEXT               -- 'BRM6', 'GNM6'
moex_symbol     TEXT               -- 'BRM6@RTSX', 'GNM6@RTSX'
hl_coin         TEXT               -- 'xyz:BRENTOIL', 'xyz:GOLD'
is_active       INTEGER            -- 1 = активен, 0 = архив
asset           TEXT               -- 'brent', 'gold', 'silver'
contract_start_date TEXT           -- для квартальных (YYYY-MM-DD)
```

**Активные контракты (Brent):**
- `brm6` — June (активный)
- `brn6` — July (следующий)
- `brk6` — May (expired, но is_active=1)

**Деактивированные (mini):**
- `bmm6`, `bmk6`, `bmn6` — mini Brent, больше не используются в UI, но данные в БД остались

---

### `alor_candles` — основной источник MOEX OHLCV
```sql
contract_id     TEXT
symbol          TEXT               -- 'BRM6', 'GNM6' (без @RTSX)
timeframe       TEXT               -- '5m', '15m', '60m'
timestamp_ms    INTEGER            -- Unix timestamp в миллисекундах
open            REAL
high            REAL
low             REAL
close           REAL
volume          INTEGER
is_prev_contract INTEGER           -- 1 = данные из предыдущего контракта, 0 = текущий
```

**Ключевые особенности:**
- Данные загружаются **lazy**: при первом запросе проверяем `has_alor_candles()`, если False — вызываем `load_full_history()`
- Можно **перезагрузить** через `POST /api/history/load/{cid}?timeframe={tf}`
- Для Brent `is_prev_contract=1` означает, что свеча взята из предыдущего месяца (например, BRK6→BRM6), потому что текущий контракт начал торговаться позже

---

### `candles` — legacy + Hyperliquid
```sql
contract_id     TEXT
source          TEXT               -- 'moex' | 'hyperliquid'
symbol          TEXT               -- 'BRM6@RTSX' или 'xyz:BRENTOIL'
timeframe       TEXT               -- '5m', '15m', '60m'
timestamp_ms    INTEGER
close           REAL               -- только close, OHLCV нет
```

**Использование:**
- `source='hyperliquid'` — данные HL, используются для расчёта спреда
- `source='moex'` — legacy Finam, больше не пополняется, но использовалась для мёрджа апрельских пробелов

---

## 3. Как работает загрузка истории

### 3.1. Фоновый цикл (`_history_loop`)

Каждые ~5 минут (при polling) для каждого **активного** контракта:

```
1. _load_alor_history(cid, moex_symbol, asset, tf)
   └─ если has_alor_candles(cid, symbol, tf) == False
      └─ load_full_history(cid, symbol, asset, tf)  ← Alor API

2. _load_hl_historical(cid, hl_coin, tf)
   └─ загружает новые свечи с Hyperliquid
```

### 3.2. Alor: `load_full_history()`

Для **Brent** (2-месячные контракты):
- Загружаем **предыдущий** контракт с `untraded=true` (например, BRK6 для BRM6)
- Загружаем **текущий** контракт с `untraded=false` (BRM6)
- Мёрджим оба набора в `alor_candles` под `contract_id='brm6'`

**Проблема:** Alor API **не отдаёт expired контракты**. Даже с `untraded=true` BRK6 возвращает `[]`.

**Решение (Data Gap Fix):**
- Legacy `bmm6` (mini Brent June) содержит MOEX-данные с 2 апреля
- Копируем их в `brm6` `alor_candles` как `is_prev_contract=1`
- Аналогично для `brn6` через `bmn6`

Для **квартальных** контрактов (GNM6, S1M6):
- Загружаем полный квартал (3 месяца)
- Там такой проблемы нет — данные идут с начала квартала

### 3.3. Hyperliquid

- HL-данные загружаются в `candles` (source='hyperliquid')
- Схема упрощённая: только `close`, без OHLCV
- Для Brent контрактов HL-истормент один: `xyz:BRENTOIL`

---

## 4. Как endpoint'ы выбирают данные

### 4.1. MOEX-ряд (`_get_moex_series`)

```python
clean_sym = moex_symbol.split("@")[0]  # 'BRM6'

if database.has_alor_candles(contract_id, clean_sym, timeframe):
    # Предпочитаем Alor
    return database.get_alor_candles_recent(cid, clean_sym, tf, from_ms, limit)
else:
    # Fallback на legacy Finam
    return database.get_candles_recent(cid, "moex", moex_symbol, tf, from_ms, limit)
```

### 4.2. Синхронизация (`sync.strict_sync`)

```python
moex = _get_moex_series(...)   # список {timestamp_ms, close}
hl   = database.get_candles_recent(..., "hyperliquid", ...)  # список {timestamp_ms, close}

synced = strict_sync(moex, hl)
```

**Что делает `strict_sync`:**
1. Берёт **пересечение** timestamp'ов: только те моменты, где есть данные И в MOEX, И в HL
2. Сортирует по возрастанию
3. Возвращает список `{timestamp_ms, spread_pct}`

**Важно:** Если MOEX есть с 2 апреля, а HL только с 1 мая — апрельские MOEX-свечи **отбрасываются**, потому что для них нет HL-пары. Поэтому при мёрдже MOEX из mini-контрактов **обязательно** нужно также мёрджить HL-данные из того же mini-контракта.

### 4.3. Endpoint'ы

| Endpoint | MOEX | HL | Синхронизация |
|----------|------|-----|---------------|
| `/api/historical/{cid}/{tf}` | `_get_moex_series` | `get_candles_recent(..., "hyperliquid", ...)` | `strict_sync` |
| `/api/prices/{cid}/{tf}` | то же | то же | `strict_sync` |
| `/api/zscore/{cid}/{tf}` | то же | то же | `strict_sync` + Z-Score |
| `/api/stats/{cid}/{tf}` | то же | то же | `strict_sync` + статистика |
| `/api/signal/{cid}` | 5m ряд | 5m HL | `strict_sync` + Z-Score + сигнал |

---

## 5. Где что лежит на сервере

```
/opt/dashboard/
├── backend/backend/          ← рабочая директория сервиса (systemd)
│   ├── main.py
│   ├── database.py
│   ├── alor_history.py
│   ├── config.py
│   └── ...
├── backend/                  ← директория git-репы (НЕ рабочая!)
│   ├── main.py
│   ├── database.py
│   └── ...
├── backend/data/
│   └── dashboard.db          ← SQLite (WAL mode)
├── backend/frontend/         ← статика для nginx
│   ├── index.html
│   ├── app.js
│   └── style.css
└── venv/                     ← Python venv
```

**Критически важно:** `dashboard.service` запускается из `/opt/dashboard/backend/backend/`, а не из `/opt/dashboard/backend/`. При деплое файлы нужно копировать в **обе** директории или менять `WorkingDirectory` в systemd.

---

## 6. Как обновить / перезагрузить данные

### 6.1. Ручная перезагрузка (через UI)

1. Открыть `https://mo-ex.online`
2. Выбрать контракт и timeframe
3. Нажать кнопку **↻ Reload**
4. Backend выполнит:
   ```
   delete_alor_candles(cid, symbol, tf)
   load_full_history(cid, symbol, asset, tf)
   ```

### 6.2. Ручная перезагрузка (через API)

```bash
curl -X POST "https://mo-ex.online/api/history/load/brm6?timeframe=15m"
```

### 6.3. Через SQLite напрямую (на сервере)

```bash
cd /opt/dashboard
source venv/bin/activate
python3 -c "
import database, alor_history
# Удалить
# database.delete_alor_candles('brm6', 'BRM6', '15m')
# Загрузить заново
# alor_history.load_full_history('brm6', 'BRM6', 'brent', '15m')
"
```

### 6.4. Мёрдж legacy данных (mini → full)

Если Alor API не отдаёт предыдущий контракт (типично для Brent):

```sql
-- Пример: копируем bmm6 MOEX → brm6 alor_candles
INSERT OR IGNORE INTO alor_candles 
(contract_id, symbol, timeframe, timestamp_ms, open, high, low, close, volume, is_prev_contract)
SELECT 
    'brm6', 'BRM6', timeframe, timestamp_ms,
    close, close, close, close, 0, 1
FROM candles
WHERE contract_id = 'bmm6' AND source = 'moex' AND timeframe = '15m';

-- Пример: копируем bmm6 HL → brm6 HL candles
INSERT OR IGNORE INTO candles 
(contract_id, source, symbol, timeframe, timestamp_ms, close)
SELECT 'brm6', source, symbol, timeframe, timestamp_ms, close
FROM candles
WHERE contract_id = 'bmm6' AND source = 'hyperliquid' AND timeframe = '15m';
```

---

## 7. Алгоритм вычисления Z-Score и спреда

```python
spread_pct = (moex_close - hl_close) / hl_close * 100

# Z-Score считается по всему синхронизированному ряду
mean = average(spread_values)
std  = stdev(spread_values)
zscore = (current_spread - mean) / std
```

**Ключевой момент:** Mean и Std считаются по **всему доступному ряду** (например, за 1.5 месяца для BRM6). Это значит, что при увеличении истории (мёрдж legacy данных) mean/std **меняются**, и Z-Score пересчитывается.

---

## 8. Параметры Alor API

```
Base URL: https://api.alor.ru
Endpoint: /md/v2/history
Params:
  - symbol = BRM6
  - exchange = MOEX
  - instrumentGroup = RFUD
  - tf = 900 (15m = 900 сек)
  - from = Unix timestamp ms (начало)
  - to = Unix timestamp ms (конец)
  - format = Slim
  - untraded = true/false
```

**OAuth:** требуется `Authorization: Bearer {token}`. Токен обновляется через `refresh_token` из `.env`.

---

## 9. Квик-справка для агента

**Если график пустой / нет данных:**
1. Проверить `has_alor_candles(cid, symbol, tf)`
2. Если False — вызвать `load_full_history()`
3. Если True, но данных мало — проверить `strict_sync` (возможно, нет HL-пар)
4. Проверить, что файлы backend'а лежат в `/opt/dashboard/backend/backend/`

**Если нужен мёрдж legacy (mini → full):**
1. Скопировать MOEX: `bmm6` → `brm6` в `alor_candles`
2. Скопировать HL: `bmm6` → `brm6` в `candles`
3. Перезапустить сервис (сброс кэша)

**Если frontend не обновляется:**
1. Проверить cache busting (`v=multiasset8` в index.html)
2. Нажать Ctrl+F5
3. Проверить nginx alias (`/opt/dashboard/backend/frontend`)

---

*Последнее обновление: 2026-05-08*
