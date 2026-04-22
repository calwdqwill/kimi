# Brent Spread Dashboard (MVP)

Локальный дашборд спреда Brent между **MOEX (Finam Trade API)** и **Hyperliquid**.

## Возможности MVP

- Historical spread % — только на `close` свечей, синхронизация по строгому совпадению timestamp.
- Текущий spread % — на `mid` из best bid / best ask (live).
- Z-score (окно 50 свечей) на historical spread.
- Таймфреймы: **5m / 15m / 60m**.
- Zoom / pan на графиках (колесо мыши).
- Время в интерфейсе — **MSK (UTC+3)**.
- Инкрементальное хранение истории в **SQLite**.

## Стек

- **Backend:** Python, FastAPI, httpx, sqlite3, python-dotenv
- **Frontend:** HTML/CSS/JS, Chart.js, chartjs-plugin-zoom

## Структура

```
brent-spread-dashboard/
├── backend/
│   ├── main.py              # FastAPI + polling
│   ├── config.py            # константы, .env
│   ├── database.py          # SQLite
│   ├── clients/
│   │   ├── finam_client.py  # Finam Trade API
│   │   └── hl_client.py     # Hyperliquid API
│   └── domain/
│       ├── sync.py          # строгая синхронизация по ts
│       ├── spread.py        # расчёт spread
│       ├── zscore.py        # rolling Z-score
│       └── interfaces.py    # заглушки расширений
├── frontend/
│   ├── index.html
│   ├── style.css
│   └── app.js
├── data/                    # SQLite БД
├── requirements.txt
└── run.ps1                  # скрипт запуска
```

## Установка (PowerShell)

```powershell
cd brent-spread-dashboard
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Запуск

Вариант A — через скрипт:
```powershell
.\run.ps1
```

Вариант B — вручную:
```powershell
.\venv\Scripts\Activate.ps1
cd backend
uvicorn main:app --reload --port 8000
```

Открыть в браузере: **http://localhost:8000**

## Правила расчётов

| Данные | Формула |
|--------|---------|
| Historical spread % | `(HL_close - MOEX_close) / MOEX_close * 100` |
| Current spread % | `(HL_mid - MOEX_mid) / MOEX_mid * 100` |
| Z-score | `(current_spread - mean(window)) / std(window)` |
| Mid | `(best_bid + best_ask) / 2` |

- Historical и current spread **не смешиваются**.
- Z-score считается только на синхронизированном historical ряду.
- Если данных меньше 50 свечей — Z-score = `null`.

## Smoke-checks

Проверить загрузку `.env`:
```powershell
python -c "from backend.config import FINAM_TOKEN; print('OK' if FINAM_TOKEN else 'MISSING TOKEN')"
```

Проверить Finam API (должен вернуть список свечей или пустой массив без ошибок):
```powershell
python -c "from backend.clients.finam_client import fetch_historical; print(len(fetch_historical('5m', __import__('time').time()*1000 - 86400000, __import__('time').time()*1000)))"
```

Проверить Hyperliquid API:
```powershell
python -c "from backend.clients.hl_client import fetch_current; print(fetch_current())"
```

## Таймзоны

- Внутри приложения: **UTC (Unix ms)**.
- В UI: **Europe/Moscow (MSK, UTC+3)** — конвертация на фронте.
