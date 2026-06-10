# Текущая задача — Telegram Signals V3.8 (завершена локально, деплой на сервер)

**Дата:** 2026-06-10  
**Ветка:** `V2.0_prod`  
**Статус:** ✅ Код закоммичен и запушен на GitHub, ждёт деплоя на сервер

---

## Что было сделано

### Backend
- **`backend/clients/telegram_client.py`** — клиент для отправки сообщений через Telegram Bot API (HTML parse mode, таймауты, обработка ошибок)
- **`backend/config.py`** — добавлены `TELEGRAM_BOT_TOKEN` и `TELEGRAM_CHAT_ID` из `.env`
- **`backend/main.py`** — полная интеграция сигналов:
  - 3 уровня сигналов по модулю спреда: 🟡 0.5%, 🟢 1.0%, 🔴 1.5%
  - Антиспам: максимум 1 сигнал на уровень в 5 минут (`_SIGNAL_COOLDOWN_MS`)
  - Сигналы отправляются только для **выбранного контракта** (`_selected_contract_id`)
  - Инициализация: первый активный контракт выбирается автоматически при старте
  - API:
    - `GET /api/test-telegram?chat_id=` — тестовое сообщение
    - `GET /api/selected-contract` — текущий контракт для сигналов
    - `POST /api/selected-contract?contract_id=` — смена контракта

### Конфиг
- Добавлен контракт **BRQ6** (Brent Aug 2026) в `DEFAULT_CONTRACTS`

---

## Деплой на production (чек-лист)

- [ ] Скопировать файлы на сервер `155.212.183.185`:
  - `backend/main.py`
  - `backend/config.py`
  - `backend/clients/telegram_client.py`
- [ ] Добавить в `/opt/dashboard/backend/backend/.env`:
  ```
  TELEGRAM_BOT_TOKEN=your_bot_token_here
  TELEGRAM_CHAT_ID=your_chat_id_here
  ```
- [ ] Перезапустить сервис: `sudo systemctl restart dashboard`
- [ ] Проверить логи: `sudo journalctl -u dashboard -f`
- [ ] Отправить тест: `curl https://mo-ex.online/api/test-telegram`

---

## Тестирование

### Локально
```bash
# 1. Запустить backend
cd backend
uvicorn main:app --reload --port 8000

# 2. Проверить текущий спред (разовая команда)
curl http://localhost:8000/api/current/brm6

# 3. Отправить тестовое сообщение в Telegram
curl http://localhost:8000/api/test-telegram

# 4. Посмотреть/сменить выбранный контракт
curl http://localhost:8000/api/selected-contract
curl -X POST "http://localhost:8000/api/selected-contract?contract_id=brn6"
```

### На сервере (после деплоя)
```bash
curl https://mo-ex.online/api/test-telegram
curl https://mo-ex.online/api/current/brm6
curl https://mo-ex.online/api/selected-contract
```

---

## Предыдущая задача — Paper Trading Module V3.5 (завершена)

*См. историю в предыдущих версиях файла.*

---

## План на следующие задачи

См. `BACKLOG.md` — приоритеты не изменились, Telegram (#3) теперь ✅.

---

*Последнее обновление: 2026-06-10*
