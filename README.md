# AlfaAINews

Сервис новостной аналитики для руководителей крупного бизнеса (кейс Альфа-Банка). Он собирает новости и аналитику банка, склеивает их в сюжеты с проверенными фактами, сопоставляет с профилем компании и объясняет влияние события с разделением на факт, позицию банка и вывод AI.

> Черновик README. Полная версия с архитектурой и сценарием проверки появится к дню 5.

## Быстрый старт

```bash
cp .env.example .env
docker compose up --build
```

- `init` создаёт таблицы и расширение `vector`, загружает CSV кейса из `data/case/` (если они там лежат) и фикстуру «ставка 14%».
- `ingest` опрашивает RSS из `config/sources.yaml` и пишет материалы в `raw_items`.
- `backend` (FastAPI) работает на http://localhost:8000: `/health`, `/raw_items`, `/stats/ingest`, документация в `/docs`.
- `frontend` работает на http://localhost:3000 (пока это заглушка со свежими материалами).

CSV кейса (`df_Uid_data_hack.csv` и остальные) в репозиторий не коммитятся: положите их в `data/case/`. Повторный `up` не перезаливает CSV, если файлы не менялись; принудительно — `docker compose run --rm init python -m newsai.db.init --load-case --force-case`.

Postgres доступен с хоста на `localhost:55432` (порт задаёт `DB_HOST_PORT`), чтобы не конфликтовать с локально установленным PostgreSQL. PDF ЦМИ кладутся в `data/bank/pdf/`.

## Разработка без Docker

```bash
uv sync
uv run pytest
uv run ruff check .
uv run python -m newsai.ingest.run --once   # один проход сбора (нужен Postgres)
```

## Структура

| Путь | Что внутри | Владелец |
|---|---|---|
| `newsai/schemas/` | Pydantic-схемы всех объектов (13 + `market_knowledge`) | Р1 |
| `newsai/db/` | таблицы, инициализация, загрузка CSV | Р1 |
| `newsai/ingest/` | сбор RSS и Telegram → `raw_items` | Р1 |
| `newsai/stories/` | отсев, склейка в сюжеты, статусы | Р1 |
| `newsai/extract/` | извлечение фактов с цитатами | Р1 |
| `newsai/knowledge/` | знания аналитиков рынка | Р1 |
| `newsai/bank/` | реестр позиций ЦМИ и поиск по нему | Р1 |
| `newsai/profile/` | профиль клиента, граф платежей | Р1 и Р2 |
| `newsai/agents/` | агенты разбора | Р3 (Р1: `bank_market.py`, `statistician.py`) |
| `config/` | `sources.yaml`, `taxonomy.yaml` | Р1 |
| `fixtures/` | сквозные фикстуры («ставка 14%») | Р1 |

Любая правка схем делается через Р1 и сразу вместе с фикстурами.
