# Текущая задача — Alor History Integration V3

**Дата:** 2026-05-08  
**Ветка:** `V3_prod` (запушена)  
**Статус:** ✅ Production-деплой завершён, данные BRM6/BRN6 восстановлены

---

## Что было сделано

### Phase 1 — Backend Integration
- **`backend/alor_history.py`**
  - `get_periods()` — вычисляет периоды загрузки для Brent (2 месяца) и квартальных контрактов
  - `fetch_alor_ohlcv()` — запросы к Alor `/md/v2/history` с `instrumentGroup=RFUD`
  - `load_full_history()` — загружает предыдущий контракт (`untraded=true`) + текущий (`untraded=false`), мёрджит в `alor_candles`

- **`backend/database.py`**
  - `has_alor_candles()` — проверка наличия данных Alor
  - `get_alor_candles_recent()` — выборка последних N свечей (DESC LIMIT + reverse)
  - `get_last_alor_timestamp()` — timestamp последней свечи
  - `insert_alor_candles_batch()` — batch INSERT
  - `delete_alor_candles()` — очистка перед перезагрузкой

- **`backend/main.py`**
  - `_history_loop()` — для каждого активного контракта: Alor → HL historical
  - `_load_alor_history()` — lazy-load: если `has_alor_candles` = False → `load_full_history()`
  - `_get_moex_series()` — предпочитает `alor_candles`, fallback на legacy `candles`
  - 5 endpoint'ов обновлены (`/api/historical/`, `/api/prices/`, `/api/zscore/`, `/api/stats/`, `/api/signal/`)
  - **limit увеличен с 1500 до 10000** — полная история на графике
  - `POST /api/history/load/{cid}?timeframe={tf}` — ручная загрузка/перезагрузка истории

### Phase 2 — Frontend Integration
- **`frontend/index.html`** — кнопка reload ↻ в хедере, cache busting `v=multiasset8`
- **`frontend/app.js`** — обработчик кнопки: POST → очистка кэша → `refreshAll()` → alert с результатом
- **`frontend/style.css`** — стили `.reload-btn` + keyframes `.spin`

### Phase 3 — VPS Deploy & Data Fix
- Загружены 6 файлов на сервер (`155.212.183.185`)
- **Критический фикс деплоя:** сервис запускается из `/opt/dashboard/backend/backend/`, а файлы грузились в `/opt/dashboard/backend/` → скопированы в правильную директорию
- Перезапущен `dashboard.service`
- Активированы `brm6/brk6/brn6` (full Brent), деактивированы `bmm6/bmk6/bmn6` (mini Brent)

### Data Gap Fix — BRM6 (April → May)
- **Проблема:** Alor API возвращает BRM6 только с ~1 мая. Предыдущий контракт BRK6 — пусто даже с `untraded=true`.
- **Решение:**
  1. Скопировали legacy `bmm6` MOEX свечи (апрель) → `brm6` `alor_candles` (`is_prev_contract=1`)
  2. Скопировали legacy `bmm6` HL свечи (апрель) → `brm6` `candles` (source='hyperliquid')
- **Результат:** BRM6 15m — **1888 свечей** (2 апреля → 8 мая), без разрывов

### Data Gap Fix — BRN6 (April → May)
- **Проблема:** BRN6 alor данные только с 1 мая.
- **Решение:** аналогично через legacy `bmn6` (mini Brent July)
- **Результат:** BRN6 15m — **1284 свечи** (15 апреля → 8 мая)

---

## Production-статус (проверено 2026-05-08)

| Контракт | ТФ | Свечей | Период | Статус |
|----------|-----|--------|--------|--------|
| **BRM6** | 5m | 5211 | 5 апр → 8 мая | ✅ |
| **BRM6** | 15m | 1888 | 2 апр → 8 мая | ✅ |
| **BRM6** | 60m | 493 | 2 апр → 8 мая | ✅ |
| **BRN6** | 5m | 3022 | 17 апр → 8 мая | ✅ |
| **BRN6** | 15m | 1284 | 15 апр → 8 мая | ✅ |
| **BRN6** | 60m | 336 | 15 апр → 8 мая | ✅ |
| GNM6 | 15m | 2043 | есть | ✅ |
| S1M6 | 15m | 2073 | есть | ✅ |
| BRK6 | 15m | 172 | 29 апр → 30 апр | ⚠️ expired |

- Сервис `dashboard` — `active`
- API `mo-ex.online` — отвечает
- Frontend — `v=multiasset8` cache busting

---

## Известные ограничения

1. **Alor API не отдаёт expired контракты.** Даже с `untraded=true` BRK6/GNH6/S1H6 возвращают `[]`. Это ограничение брокера, не наш код.
2. **Mini→Full merge.** Для заполнения апрельских пробелов использованы данные mini-контрактов (`bmm6`→`brm6`, `bmn6`→`brn6`). Цены идентичны (один и тот же underlying), но формально это разные инструменты.
3. **BRK6 (May expired).** Данные только с 29 апреля. Legacy `bmk6` можно было бы мёрджнуть аналогично, но контракт уже expired и малополезен.

---

## План на следующие задачи

### 🔴 Высокий приоритет

#### 1. Переключатель range на графике
- **Задача:** Добавить на фронтенд переключатель "Весь месяц / 7 дней / 3 дня / 1 день"
- **Почему:** Сейчас график всегда показывает всю историю (для 15m ~1888 свечей = ~1.5 месяца). На мобильных устройствах это перегружено.
- **Сложность:** Низкая
- **Подход:**
  - Добавить `<select>` или кнопки в UI
  - При смене range вычислять `from_ms = now - range_ms`
  - Передавать `from_ms` в запросы `/api/historical/{cid}/{tf}?from_ms={ms}`
  - Обновить `refreshAll()` — учитывать выбранный range

### 🟡 Средний приоритет

#### 2. Funding Rates (Hyperliquid)
- **Задача:** Карточка с текущим фандингом + модалка с историей
- **API:** Hyperliquid `fundingHistory`
- **Элементы:**
  - Карточка: 8h rate, annualized %, 24h accumulated, sparkline
  - Модалка: график фандинга за месяц, таблица
- **Backend:** новый endpoint `/api/funding/{contract_id}`, таблица `funding_history`
- **Сложность:** Средняя

#### 3. Telegram-бот (сигналы Z-Score)
- **Задача:** Автоотправка сигналов при |Z-Score| > 2.0
- **Формат:** Текст (контракт, спред, Z, рекомендация) + скриншот графика
- **Backend:** Bot API, webhook, рендер графика (Playwright или headless Chrome)
- **Сложность:** Средняя

#### 4. Paper Trade (симулятор)
- **Задача:** Виртуальный портфель + история сделок по сигналам дашборда
- **Элементы:**
  - Вкладка "Paper Trade"
  - Таблица сделок (вход/выход, P&L)
  - Статистика (win rate, avg P&L, max drawdown)
- **Backend:** таблица `trades`, логика входа/выхода по сигналам
- **Сложность:** Средняя

### 🟢 Низкий приоритет

#### 5. WebSocket вместо polling
- Замена HTTP-polling 5s на WebSocket для real-time цен и графика

#### 6. Экспорт CSV
- Endpoint `/api/export/{contract_id}/{tf}.csv`

#### 7. Docker + CI/CD
- Dockerfile, docker-compose, GitHub Actions для автодеплоя

---

## Технический долг

- [ ] Унифицировать структуру директорий на сервере (`backend/backend/` → `backend/`)
- [ ] Добавить тесты для `alor_history.py` (mock Alor API)
- [ ] Добавить health-check endpoint (`/api/health`)
- [ ] Логирование загрузки истории (сколько свечей, сколько времени, ошибки)
- [ ] Удалить временные скрипты `check_*.py`, `merge_*.py`, `deploy_*.py` из репы

---

*Последнее обновление: 2026-05-08 12:00 UTC*
