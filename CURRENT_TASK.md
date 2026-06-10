# Текущая задача — UI Cleanup V3.9 (завершена)

**Дата:** 2026-06-10  
**Ветка:** `V2.0_prod`  
**Статус:** ✅ Закоммичено, запушено, задеплоено на mo-ex.online

---

## Что было сделано

### Telegram Signals V3.8
- `backend/clients/telegram_client.py` — Bot API клиент (sendMessage + getUpdates)
- `backend/main.py` — сигналы по уровням спреда (0.5/1.0/1.5%) + антиспам 5 мин + команды бота
- Деплой на сервер `2.25.143.143` — mo-ex.service перезапущен, токен и chat_id в .env
- Тестовые сообщения отправлены успешно, бот отвечает на команды в ЛС и группах

### Contract Cleanup
- Добавлены: BRU6 (Brent Sep), GNU6 (Gold Sep), S1U6 (Silver Sep)
- Отключены: BRK6, GNN6, S1N6, GNM6, S1M6
- Удалены дубликаты: BRQ6 (uppercase), test123
- Итого активных: BRM6, BRN6, BRQ6, BRU6, GNU6, S1U6

### UI Cleanup V3.9
- **SPREAD STATS** — объединены KPI карточки Median + Min/Max в одну
  - Median: большой шрифт (17px)
  - Min/Max: компактный шрифт (13px)
  - Доллары: sub (10px)
- **Кнопка скрытия гайда** — `✕` в шапке гайда, сохранение состояния в localStorage
- **Фильтр контрактов** — `renderContractTabs()` теперь показывает только активные контракты (убраны мини/expired)
- Cache busting: `style.css?v=10`, `app.js?v=multiasset13`

---

## Следующая задача

См. `BACKLOG.md` — рекомендуется **Range selector на графике** (Задача 1, высокий приоритет, низкая сложность).

---

*Последнее обновление: 2026-06-10 23:59*
