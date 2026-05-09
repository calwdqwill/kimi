# Changelog

## 2026-05-05 — Multi-Asset Dashboard v2

### Stage 1: Backend Multi-Asset Support
- **Gold (GNM6, GNN6)** и **Silver (S1M6, S1N6)** добавлены как полноценные активы
- Конфигурация `ASSETS` в `config.py`: brent (monthly), gold (quarterly), silver (quarterly)
- `DEFAULT_CONTRACTS` расширен до 7 контрактов с полями `asset` и `contract_start_date`
- Endpoint `/api/assets` возвращает конфигурацию всех активов
- `_contract_start_ms()` поддерживает два режима: `contract_start_date` (квартальные) и `contract_month/year` (ежемесячные)
- БД: новые колонки `asset` и `contract_start_date` в таблице `contracts`

### Stage 2: Frontend Asset Tabs (Two-Level Navigation)
- **Asset Bar (Level 1)** — отдельная плашка над хедером: Brent Oil / Gold / Silver
- **Contract Tabs (Level 2)** — в хедере: контракты выбранного актива + timeframe
- Динамические лейблы: `logoAsset`, `kpiMoexName`, `kpiMoexUnit`, `kpiHlName` меняются per asset
- Стили: asset tabs в теме дашборда (тёмный фон, цветная подсветка active — синий/золотой/серебряный)
- Иконки активов: ⛳ Brent, ◆ Gold, ◈ Silver

### Stage 3: State Isolation Per Asset+Contract
- Убраны глобальные `state.historicalData`, `state.pricesData` и т.д.
- Добавлен `state.cache` с ключом `contractId|tf` — каждый контракт+таймфрейм хранит данные отдельно
- `state.rangeState` — зум/ранж сохраняется per contract
- При переключении контракта данные мгновенно подгружаются из кэша
- `refreshAll()` обновляет UI только если пользователь всё ещё на том же контракте

### Fixes
- **Белые кнопки asset tabs** — убран `all: unset`, добавлен explicit reset + `!important` для active
- **Cache busting** — `style.css?v=4`, `app.js?v=multiasset6`
- **Динамический логотип** — "BRENT SPREAD" → "GOLD SPREAD" → "SILVER SPREAD"

### Git Branches
- `dashboard-bmk-bmr` — основная ветка разработки
- `V2_prod` — стабильная продакшн-ветка (все изменения от 2026-05-05)

---

## 2026-05-08 — Alor History Integration V3

### Phase 1: Backend Alor Integration
- **`backend/alor_history.py`** — новый модуль загрузки истории из Alor API
  - `get_periods()` — вычисляет периоды для Brent (2 месяца) и квартальных контрактов
  - `fetch_alor_ohlcv()` — запросы к `/md/v2/history` с `instrumentGroup=RFUD`, `format=Slim`
  - `load_full_history()` — lazy-load: предыдущий контракт (`untraded=true`) + текущий (`untraded=false`) → мёрдж в `alor_candles`
- **`backend/database.py`** — хелперы для `alor_candles`
  - `has_alor_candles()`, `get_alor_candles_recent()`, `get_last_alor_timestamp()`, `insert_alor_candles_batch()`, `delete_alor_candles()`
- **`backend/main.py`** — интеграция Alor fallback
  - `_get_moex_series()` — предпочитает `alor_candles`, fallback на legacy `candles`
  - 5 endpoint'ов обновлены: `/api/historical/`, `/api/prices/`, `/api/zscore/`, `/api/stats/`, `/api/signal/`
  - `POST /api/history/load/{cid}?timeframe={tf}` — ручная перезагрузка истории
  - **limit увеличен с 1500 до 10000** — полная история на графике без обрезки

### Phase 2: Frontend Reload Button
- **Кнопка ↻ Reload** в хедере — ручная загрузка/перезагрузка Alor-истории
- **Спиннер-анимация** при загрузке (`@keyframes spin`)
- **Cache invalidation** — `setCache(cid, tf, null)` + `refreshAll()`
- **Cache busting** обновлён: `app.js?v=multiasset8`, `style.css?v=5`

### Phase 3: VPS Deploy & Critical Fix
- Загружены 6 файлов на сервер `155.212.183.185`
- **Критический фикс:** `dashboard.service` запускается из `/opt/dashboard/backend/backend/`, файлы грузились в `/opt/dashboard/backend/` → скопированы в правильную директорию
- Активированы `brm6/brk6/brn6` (full Brent), деактивированы `bmm6/bmk6/bmn6` (mini Brent)

### Data Gap Fix — BRM6 (April → May)
- **Проблема:** Alor API возвращает BRM6 только с ~1 мая. Предыдущий контракт BRK6 — `[]` даже с `untraded=true`.
- **Решение:**
  - Legacy `bmm6` (mini Brent June) MOEX → `brm6` `alor_candles` (`is_prev_contract=1`)
  - Legacy `bmm6` HL → `brm6` `candles` (source='hyperliquid')
- **Результат:** BRM6 15m — **1888 свечей** (2 апреля → 8 мая), без разрывов

### Data Gap Fix — BRN6 (April → May)
- **Проблема:** BRN6 alor данные только с 1 мая.
- **Решение:** Legacy `bmn6` (mini Brent July) MOEX + HL → `brn6`
- **Результат:** BRN6 15m — **1284 свечи** (15 апреля → 8 мая)

### Production-статус
| Контракт | ТФ | Свечей | Период |
|----------|-----|--------|--------|
| BRM6 | 5m | 5211 | 5 апр → 8 мая |
| BRM6 | 15m | 1888 | 2 апр → 8 мая |
| BRM6 | 60m | 493 | 2 апр → 8 мая |
| BRN6 | 5m | 3022 | 17 апр → 8 мая |
| BRN6 | 15m | 1284 | 15 апр → 8 мая |
| BRN6 | 60m | 336 | 15 апр → 8 мая |

### Git Branches
- `V3_prod` — продакшн-ветка (Alor Integration + Data Gap Fixes)

---

## Pre-2026-05-05

### Performance & Reliability
- WAL mode для SQLite, LIMIT=2000, in-memory кэш (TTL 30 сек)
- HTTP таймауты 10 сек, retry-логика в JS, polling 5 сек
- Диагностика `ERR_CONNECTION_RESET` — VPN, retry-логика

### Chart Features
- Zoom колесиком: вверх=zoom in, правый край фиксирован, slider синхронизируется, min 96 точек
- История с 1-го числа месяца контракта (`_contract_start_ms`)

### Contracts Added
- BMN6 (Brent July 2026)
- GNM6/GNN6 (Gold quarterly)
- S1M6/S1N6 (Silver quarterly)

### Infrastructure
- V1_prod — первая стабильная ветка
- Деплой через paramiko SSH + `git pull` + `systemctl restart dashboard`
