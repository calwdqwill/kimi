# Changelog

## 2026-06-11 — Docker + PostgreSQL V4.0

### Infrastructure
- **Docker-контейнеризация**: backend, frontend/nginx, PostgreSQL — 3 сервиса в `docker-compose.yml`
  - `Dockerfile` — Python 3.12 + FastAPI/uvicorn на порту `8001`
  - `frontend/Dockerfile` + `frontend/nginx.conf` — статический nginx
  - `docker-compose.yml` — `db`, `backend`, `frontend` с healthcheck и volume `pgdata`
- **PostgreSQL миграция**: `backend/database.py` теперь поддерживает SQLite (legacy) и PostgreSQL (Docker)
  - `psycopg2-binary` добавлен в `requirements.txt`
  - `backend/config.py` умеет собирать `DATABASE_URL` из `POSTGRES_*` переменных
  - `backend/backup.py` поддерживает `pg_dump` для Postgres и копирование файла для SQLite
- **Миграция данных**: `scripts/migrate_sqlite_to_postgres.py` переносит существующую SQLite-БД в PostgreSQL
- **Deploy**:
  - `deploy/nginx/mo-ex` теперь проксирует `/api/` на `127.0.0.1:8001`
  - `deploy/systemd/mo-ex-docker.service` — systemd unit для `docker compose up -d`

---

## 2026-06-12 — WebSocket Real-Time V4.1

### Backend
- **`backend/main.py`** — добавлен WebSocket endpoint `GET /api/ws`:
  - Подписка клиента на `contract_id` + `timeframe` через сообщение `{"action":"subscribe",...}`
  - Фоновый broadcast loop пушит:
    - `current` — цены/спред каждые 2 сек
    - `signal` — сигнал каждые 10 сек
    - `rapira` — USDT/RUB каждые 10 сек
    - `ticks` — тики каждые 5 сек
    - `ohlc` — исторические данные, prices, zscore, stats каждые 30 сек
  - Все тяжёлые DB-вызовы обернуты в `asyncio.to_thread()` для неблокирующей работы event loop

### Frontend
- **`frontend/app.js`** — заменён 5-секундный HTTP polling на WebSocket:
  - `connectWebSocket()` / `sendWsSubscribe()` / `handleWsMessage()`
  - UI обновляется из WebSocket сообщений той же логикой, что и при HTTP polling
  - Fallback: при обрыве соединения автоматически включается HTTP polling каждые 5 сек, при восстановлении — отключается
  - Paper equity recording вынесен в отдельный 60-секундный interval (ранее зависел от polling)

### Deploy
- Nginx `/api/` уже проксирует WebSocket Upgrade headers — изменений не требуется

---

## 2026-06-10 — Telegram Signals V3.8 + UI Cleanup V3.9

### Telegram Bot Integration
- **`backend/clients/telegram_client.py`** — новый клиент для Telegram Bot API (sendMessage, getUpdates)
- **`backend/main.py`** — интеграция сигналов и команд:
  - 3 уровня сигналов по модулю спреда: 🟡 0.5%, 🟢 1.0%, 🔴 1.5%
  - Антиспам: 1 сигнал на уровень в 5 минут (`_SIGNAL_COOLDOWN_MS`)
  - Сигналы только для выбранного контракта (`_selected_contract_id`)
  - API: `GET /api/test-telegram`, `GET/POST /api/selected-contract`
  - Telegram bot commands: `/spread`, `/all`, `/select <id>`, `/help` (polling loop)
- **Deploy**: mo-ex.service на `2.25.143.143`, `.env` с `TELEGRAM_BOT_TOKEN` и `TELEGRAM_CHAT_ID`

### Contract Cleanup (DB + Config)
- Добавлены: `BRU6` (Brent Sep), `GNU6` (Gold Sep), `S1U6` (Silver Sep)
- Отключены (expired): `BRK6`, `GNN6`, `S1N6`, `GNM6`, `S1M6`
- Удалены дубликаты: `BRQ6` (uppercase), `test123`
- Активные контракты: BRM6, BRN6, BRQ6, BRU6, GNU6, S1U6

### UI Cleanup V3.9
- **`frontend/index.html`**:
  - Объединены KPI карточки Median + Min/Max → `SPREAD STATS` (единая карточка)
  - Добавлена кнопка `✕` для сворачивания гайда (`sidebarToggle`)
  - Cache busting: `style.css?v=10`, `app.js?v=multiasset13`
- **`frontend/app.js`**:
  - `renderContractTabs()` — фильтр только активных контрактов (убраны мини/expired)
  - `initSidebarToggle()` — toggle гайда с сохранением в `localStorage`
  - `updateStats()` — обновление объединённой карточки SPREAD STATS
- **`frontend/style.css`**:
  - Стили `.sidebar-toggle`, `.sidebar.collapsed`
  - Стили `.kpi-value.compact` для Min/Max в SPREAD STATS

---

## 2026-05-11 — UI Cleanup & Contract Tabs V3.7

### Frontend Improvements
- **`frontend/index.html`** — объединены дублирующие карточки:
  - Z-SCORE (LIVE) + ТОЧКА ВХОДА (±2σ) → одна карточка с разделителем
  - SPREAD (MID, %) + ARB SPREAD (BID/ASK) → одна карточка с Mid/Arb строками
  - Обновлён sidebar guide: убраны отдельные описания, добавлены описания объединённых блоков
  - Cache busting: `style.css?v=8`, `app.js?v=multiasset11`
- **`frontend/app.js`** — вкладки контрактов теперь показывают название месяца на русском + дату экспирации (экс: 01.ММ). Универсальный маппинг фьючерсных кодов (F=Январь...Z=Декабрь)
- **`frontend/style.css`** — новые стили `.kpi-zscore-top`, `.kpi-zscore-divider`, `.kpi-spread-line`, `.tab-label-wrap`, `.tab-month`, `.tab-expiry`

### Status
- Правка 5 (USDT/RUB межбиржевой спред) — отложена в BACKLOG до нахождения стабильного API TokenSpot/Rapira

---

## 2026-05-10 — Funding Calculator Module V3.6

### Phase 1: Backend Funding API
- **`backend/clients/hl_client.py`** — `fetch_funding_history_paginated()` для загрузки >500 записей (пагинация)
- **`backend/main.py`** — новые endpoints:
  - `GET /api/funding/summary/{cid}` — агрегаты для монитора (текущий rate, 24h, 7d, график, positive%)
  - `POST /api/funding/calc/{cid}` — калькулятор фандинга за период (long/short/auto, P&L по дням)
  - `GET /api/funding/analytics/{cid}` — аналитика (автокорреляция, корреляция со спредом, heatmap 24×7)
- **Helpers**: `_get_spread_for_funding()`, `_pearson()`, `_autocorr()`, `_aggregate_funding_by_day()`
- **Защита**: только Brent контракты (asset == 'brent'), иначе 400

### Phase 2: Frontend Funding Tab (Вкладка Фандинг)
- **Новая таба** `📊 ФАНДИНГ` рядом с ТИКИ / PAPER (видна только для Brent)
- **3 под-вкладки**: Монитор / Калькулятор / Аналитика

#### Под-вкладка 1: Монитор
- 3 карточки: текущий фандинг (+таймер), 24h кумулятивно, среднее 7 дней (+mini-bars)
- Селектор размера позиции ($5K / $9K / $12K / свой)
- График 24h area chart (Chart.js) с цветовой кодировкой положительных/отрицательных ставок
- Таблица влияния: ШОРТ HL vs ЛОНГ HL (текущая, 24ч, 7д, 30д)
- Автообновление каждые 60 секунд

#### Под-вкладка 2: Калькулятор
- Форма: дата от/до, сторона позиции (ШОРТ/ЛОНГ/АВТО), размер позиции
- Режим АВТО: определяет сторону по среднему спреду (> mean → шорт HL)
- 4 карточки результата: всего, среднее в день, лучший/худший день
- Кумулятивный график $ (Chart.js area chart)
- Таблица разбивки по дням с сигналом
- Экспорт CSV

#### Под-вкладка 3: Аналитика
- 4 карточки инсайтов: направленный сдвиг (doughnut), волатильность (sparkline), предсказуемость (autocorr), влияние на стратегию
- Scatter plot: корреляция фандинга и спреда
- Тепловая карта 24ч × 7 дней (HTML grid с цветовой интенсивностью)

### Phase 3: CSS
- Полный набор стилей `.funding-*` по аналогии с Paper Trade
- Адаптивность: 3→2→1 колонки, мобильная стопка

---

## 2026-05-09 — Paper Trading Module V3.5

### Phase 1: Backend Paper Trading
- **`backend/database.py`** — новые таблицы:
  - `paper_settings` — настройки симуляции (депозит, плечо, уровни входа, комиссии, стопы)
  - `paper_trades` — журнал сделок (entry/exit, P&L, фандинг, комиссии)
  - `paper_funding` — почасовой лог фандинга по открытым позам
  - `paper_equity` — точки кривой эквити (до 5000 на контракт)
- **`backend/main.py`** — новые endpoints:
  - `GET/POST /api/paper/settings` — чтение/запись настроек
  - `GET /api/paper/trades/{cid}` — список сделок
  - `POST /api/paper/trades/entry` — открытие позиции (Pydantic JSON)
  - `POST /api/paper/trades/exit/{id}` — закрытие позиции (Pydantic JSON)
  - `GET /api/paper/active/{cid}` — активная позиция
  - `GET/POST /api/paper/equity/{cid}` — кривая эквити
  - `GET /api/paper/summary/{cid}` — сводная статистика (winrate, P&L, комиссии)
  - `POST /api/paper/reset` — сброс данных
  - `GET /api/funding/{cid}` — funding history из Hyperliquid API
- **`backend/clients/hl_client.py`** — `fetch_funding_history(coin, start_ms, end_ms)` для HL fundingHistory

### Phase 2: Frontend Paper Trading (Вкладка Paper)
- **3 под-вкладки**: Живая торговля / История сделок / Настройки
- **Вкладка 1 (Живая торговля)**:
  - Статус-бар с индикатором авто-торговли
  - Селектор режима: АВТО / ПОЛУ / РУЧНОЙ
  - Карточка активной позиции с live P&L, днями удержания, сигналом выхода
  - Ручная панель входа (сторона, уровень, размер)
  - Мини-статистика (P&L сегодня, открытых, винрейт, всего)
  - Лента сделок с фильтрами (Всё/Входы/Выходы/Фандинг)
  - Мини-график эквити на Chart.js
- **Вкладка 2 (История)**:
  - Фильтры: период, сторона, результат
  - Сводные карточки: всего сделок, чистый P&L, винрейт, фандинг
  - Таблица сделок с сортировкой, цветовой кодировкой (зелёный/красный)
  - Экспорт CSV
- **Вкладка 3 (Настройки)**:
  - Капитал, плечо, уровни входа, правила выхода, затраты, лукбэк
  - Опасная зона: сброс всех данных
- **Логика авто-торговли на фронте**:
  - Каждый тик (5с polling) проверяет сигналы входа/выхода
  - Условие входа: |отклонение| >= порог по Z-Score
  - Условие выхода: возврат к среднему / макс удержание / хард-стоп
  - Кулдаун между сделками
  - Запись эквити каждые 60 секунд

### Deploy & Fixes
- **Production деплой** на `155.212.183.185` — сервис `dashboard` перезапущен
- **Критический фикс:** nginx отдавал старую статику из `backend/frontend/` — скопировали новые файлы, перезагрузили nginx
- **БД восстановлена** из production backup (30.5 МБ) — все исторические данные на месте
- **Paper Trading** доступен на mo-ex.online после Ctrl+F5

### Cache Busting
- `style.css?v=6`, `app.js?v=multiasset9`

---

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
- Загружены 6 файлы на сервер `155.212.183.185`
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
| BRN6 | 15m | 1284 | 15 апреля → 8 мая |
| BRN6 | 60m | 336 | 15 апреля → 8 мая |

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
