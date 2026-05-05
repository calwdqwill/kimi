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
