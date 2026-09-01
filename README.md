# OKR Flywheel Agent

OKR-агент по принципу маховика данных из книги [Oper8 v0.8](https://oper8.ru).

## Чем отличается от исходного агента

| | OKR Agent (старый) | OKR Flywheel (этот) |
|---|---|---|
| Память | Только текущая сессия | SQLite: история команд + лог валидаций |
| Знание команды | Нет | Профиль: N валидаций, средний балл, частые ошибки |
| Контекст в промпте | Статический | Динамический — инжектируется история команды |
| Метрики | Нет | `/api/admin/metrics` — revision rate, top errors |

## Архитектура

```
okr_agent_core.py   — LLM-вызовы, валидация, извлечение оценок
agent_loop.py       — диалог, инструменты, команда, лог валидаций
memory.py           — SQLite: team_profiles, validation_log
web/server.py       — FastAPI: сессии, WebSocket, API команд
web/static/         — UI (index.html, app.js, style.css)
```

## Запуск локально

```bash
pip install -r requirements.txt
uvicorn web.server:app --host 0.0.0.0 --port 8000 --reload
```

Открыть: http://localhost:8000

Переменные окружения (`.env`):
```
OKR_PROVIDER=groq
GROQ_API_KEY=...
OKR_PASSWORD=        # необязательно
```

## Фазы развития маховика

- **Фаза 1 (текущая)** — SQLite лог + профиль команды + инжекция в промпт
- **Фаза 2** — `rules.json` (живые правила) + библиотека примеров (before/after)
- **Фаза 3** — `/admin/metrics` дашборд + тест-сьют качества + A3 автономия
