# Текущая задача — Mean-Reversion Backtest Engine V4.3

**Дата:** 2026-08-26  
**Ветка:** `V2.0_prod`  
**Статус:** 🔄 В процессе — движок готов, задеплоен, нужен UI и дальнейшая проверка на всех активах

---

## Что делаем
Строим и проверяем алгоритм торговли на схождение/расхождение спреда между MOEX и Hyperliquid:
- backtest endpoint, который проигрывает историю и считает P&L / winrate / max drawdown / Sharpe;
- optimize endpoint, который подбирает параметры по сетке;
- на основе результатов — улучшить paper trading и сигналы.

---

## Что сделано

### Backtest engine (`backend/domain/backtest.py`)
- Модель mean-reversion: long spread при Z ≤ -entry_z, short spread при Z ≥ +entry_z.
- Выход при возврате к среднему (|Z| ≤ exit_z), stop-loss при |Z| ≥ stop_z, hard time-stop после max_hold свечей.
- Rolling mean/stddev с окном lookback.
- Учёт комиссий и проскальзывания на обеих ногах.
- Метрики: total_pnl, num_trades, winrate, avg_pnl, best/worst trade, max_drawdown, sharpe.

### API endpoints (`backend/main.py`)
- `POST /api/backtest/{contract_id}/{timeframe}` — одиночный прогон.
- `POST /api/backtest/optimize/{contract_id}/{timeframe}` — grid-search оптимизация.

### Deploy
- Задеплоено на сервер `2.25.143.143` в Docker.
- Через SSH-tunnel endpoint'ы отвечают за 2–3 секунды.

### Первые результаты (BRU6, 5m, ~1 месяц истории)
- Дефолтные параметры (entry_z=2.0, exit_z=0.5, stop_z=3.0, max_hold=48, lookback=120): **P&L -712$, winrate 41%, sharpe -0.46**.
- Оптимизированные параметры (entry_z=2.5, exit_z=0.0, stop_z=3.5, max_hold=96, lookback=120): **P&L +825$, winrate 73%, sharpe 0.65, max DD 295$**.

### Репозиторий
- Убраны ad-hoc скрипты и legacy deploy-файлы из рабочей директории.
- Обновлён `.gitignore` (data/, скрипты с паролями, legacy deploy).

---

## Следующий шаг
1. Проверить бэктест на всех активах и таймфреймах (BRN6, BRQ6, GNU6, S1U6, 15m/60m).
2. Добавить UI-вкладку для запуска бэктеста и отображения equity curve / trades.
3. Перенести лучшие параметры в paper trading (или дать ему использовать параметры из optimize).
4. Рассмотреть улучшения: фильтр по режиму рынка, учёт funding, hedge ratio, асинхронные долгие расчёты.

---

*Последнее обновление: 2026-08-26*
