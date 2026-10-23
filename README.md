# AlfaAINews

Сервис новостной аналитики для руководителей крупного бизнеса (кейс Альфа-Банка). Он собирает новости и аналитику банка, склеивает их в сюжеты с проверенными фактами, сопоставляет с профилем компании и объясняет влияние события с разделением на факт, позицию банка и вывод AI.

> Рабочая ветка команды — `develop`. Что осталось каждому разработчику: `docs/next/dev1.md`, `dev2.md`, `dev3.md`.

## Быстрый старт

```bash
cp .env.example .env
docker compose up --build
```

- `init` создаёт таблицы и расширение `vector`, загружает CSV кейса из `data/case/` (если они там лежат) и фикстуру «ставка 14%».
- `ingest` опрашивает RSS из `config/sources.yaml` и пишет материалы в `raw_items`.
- `backend` (FastAPI) работает на http://localhost:8000, документация в `/docs`:
  - `/session`, `/feed`, `/card/{id}`, `/trace/{id}`, `/ask` — карточки для клиента из сессии;
  - `/stories?business_only=true` — свежие сюжеты с типом события и статусом подтверждения;
  - `/stories/{id}` — сюжет, факты с цитатами и материалы; `/stories/{id}/bank_market`, `/stories/{id}/statistician`;
  - `/labeling` — разметка пар «сюжет × клиент»;
  - `/companies/{id}/counterparties` — граф платежей компании;
  - `/raw_items`, `/stats/ingest`, `/health` — служебные.
- `ingest` раз в 5 минут склеивает материалы в сюжеты и извлекает факты (правила; LLM — через порт экстрактора).
- `frontend` работает на http://localhost:3000: макет главного экрана Альфа-Бизнеса, виджет «Важно для вашего бизнеса», лента и карточка. API доступен под `/api` (nginx проксирует на `backend`).

CSV кейса (`df_Uid_data_hack.csv` и остальные) в репозиторий не коммитятся: положите их в `data/case/`. Повторный `up` не перезаливает CSV, если файлы не менялись; принудительно — `docker compose run --rm init python -m backend.db.init --load-case --force-case`.

Postgres доступен с хоста на `localhost:55432` (порт задаёт `DB_HOST_PORT`), чтобы не конфликтовать с локально установленным PostgreSQL. PDF ЦМИ кладутся в `data/bank/pdf/`.

## Разработка без Docker

```bash
uv sync
uv run pytest
uv run ruff check .
uv run python -m backend.ingest.run --once   # один проход сбора (нужен Postgres)
uv run python -m backend.profile.archetypes  # какие компании CSV выбраны под архетипы демо
```

Интерфейс (React + Vite, `frontend/`):

```bash
cd frontend && npm ci
npm run dev        # http://localhost:5173, /api проксируется на http://localhost:8000 (BACKEND_URL)
npm run dev:mock   # без бэкенда: моки на фикстуре rate_14 (frontend/src/mocks)
npm run build
```

## Демо без Docker (фикстура «ставка 14%»)

```bash
LLM_MODE=fixture uv run uvicorn backend.api.main:app --port 8000   # без ключа и без ₽
cd frontend && npm run dev                                          # http://localhost:5173
```

Вход: компания `U_DEMO_BORROWER` (роль `cfo`) или `U_DEMO_DEPOSITOR` (роль `treasurer`). Лента собирается
конвейером при первом запросе: скоринг → планировщик → аналитики → критик → арбитр → карточка → проверка кодом.
Живые модели: `LLM_MODE=live` и ключ в переменной окружения (≈0,3–0,4 ₽ на карточку).

### Переменные окружения

| Переменная | Значения | Что делает |
|---|---|---|
| `LLM_MODE` | `live` / `fixture` | настоящие модели или записанные ответы (`backend/llm/fixtures/`), тесты — всегда `fixture` |
| `CARD_STORE` | `pipeline` (по умолчанию) / `fixture` | карточки из конвейера или готовые из фикстуры |
| `TOOL_SOURCE` | `fixture` (по умолчанию) / `db` | откуда агенты и скоринг читают данные: фикстура или таблицы Р1 (JSONB `data`) |
| `FEED_DEEP_PER_REQUEST`, `FEED_MIN_SCORE` | `3`, `0.05` | лента: глубоких разборов за обращение (остальное — быстрые карточки) и порог значимости сюжета для ленты |
| `CACHE_BACKEND`, `CACHE_TTL_S` | `memory` / `redis`, секунды (24 ч) | кеш разбора, карточек и трасс |
| `CRITIC_LLM_ENABLED` | `true` / `false` | LLM-критик DeepSeek по сомнительным утверждениям |

Отчёты: `uv run python -m backend.llm.report` (бюджет по шагам и моделям), `uv run python -m backend.cards.quality` (качество карточек).

## Как работаем в develop

```bash
git fetch origin && git checkout develop && git pull
git checkout -b feature/devN-<задача>      # от свежего develop
uv run pytest -q && uv run ruff check . --exclude .claude
```

Изменения вливаются в `develop` через PR маленькими кусками. Старые ветки `feature/data-schemas-ingest` и
`feature/dev2-scoring-ui` — предки `develop`: `git merge origin/develop` в них проходит без конфликтов.
Чужую зону не правим — запрос в `docs/HANDOFFS.md`.

## Структура

Дерево повторяет раздел «Стек и структура репозитория» финальной архитектуры. Р1 — данные и знания,
Р2 — скоринг и интерфейс, Р3 — агенты и LLM.

```
├── docker-compose.yml, Dockerfile, pyproject.toml, uv.lock
├── config/
│   ├── sources.yaml           # источники, уровни 0–3, ручные веса                       Р1
│   ├── channels_matrix.yaml   # каналы-факторы и матрица ОКВЭД → чувствительность         Р1 (каналы), Р2 (матрица)
│   ├── event_types.yaml       # типы событий и факторы, которые они двигают              Р1
│   └── bank_docs.yaml         # реестр обзоров ЦМИ                                        Р1
├── data/                      # CSV кейса, PDF ЦМИ (не в git), синтетические данные
├── backend/
│   ├── schemas/               # Pydantic-контракты всех объектов                         Р1
│   ├── db/                    # таблицы, init, загрузка CSV                               Р1
│   ├── ingest/                # RSS, Telegram, полный текст → raw_items                   Р1
│   ├── stories/               # отсев, SimHash, склейка, версии сюжета                   Р1
│   ├── extract/               # NER, LLM-экстрактор, проверка цитат                       Р1
│   ├── profile/               # профиль клиента; graph.py — граф платежей                Р2; graph.py — Р1
│   ├── bank/                  # реестр позиций ЦМИ, поиск                                 Р1
│   ├── knowledge/             # знания аналитиков рынка, консенсус, origin                Р1
│   ├── ranking/               # скоринг, быстрая карточка                                 Р2
│   ├── agents/                # планировщик, аналитики, критик, арбитр                    Р3; bank_market.py, statistician.py — Р1
│   ├── cards/                 # персональная карточка, вопрос-ответ                       Р3
│   ├── llm/                   # клиент, бюджет, запасные модели, журнал                   Р3
│   ├── security/              # доступ по company_id, фильтры инъекций                    Р3
│   ├── api/                   # FastAPI                                                   все
│   └── fixtures.py            # загрузка сквозных фикстур
├── fixtures/                  # сквозная фикстура «ставка 14%»                            Р1
├── schemas_json/              # JSON Schema контрактов для фронта и промптов              Р1
├── eval/labeling/             # разметка пар «сюжет × клиент»                             Р1
├── frontend/                  # интерфейс Альфа-Бизнеса: лента и карточка                 Р2
├── docs/                      # заметки по данным кейса
└── tests/
```

Любая правка схем делается через Р1 и сразу вместе с фикстурами: после правки
`uv run python scripts/export_json_schemas.py`, иначе тест `test_schemas_json_up_to_date` упадёт.
