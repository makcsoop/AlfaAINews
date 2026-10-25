# AlfaAINews

Сервис новостной аналитики для руководителей крупного бизнеса (кейс Альфа-Банка). Он собирает новости и аналитику банка, склеивает их в сюжеты с проверенными фактами, сопоставляет с профилем компании и объясняет влияние события с разделением на факт, позицию банка и вывод AI.

> Рабочая ветка команды — `develop`. Что осталось каждому разработчику: `docs/next/dev1.md`, `dev2.md`, `dev3.md`.

## Документация

- [Архитектура](docs/architecture.md) и [контракты данных](docs/contracts.md)
- [Масштабирование и сопровождение](docs/scaling_and_ops.md): новые отрасли, клиенты, источники, модели; регламент и что делать при сбоях
- [Мониторинг качества и обратная связь](docs/monitoring.md): метрики контента, персонализации и AI, отметки пользователей, разметка
- [Безопасность](docs/security.md): модель угроз, механизмы с местом в коде, что не закрыто
- [Деплой на сервер](docs/deploy.md)
- [Сценарий демо](docs/demo.md)

## Быстрый старт

```bash
cp .env.example .env
echo "SESSION_SECRET=$(openssl rand -hex 32)" >> .env   # без него compose не стартует
docker compose up --build
```

- `init` создаёт таблицы и расширение `vector`, загружает CSV кейса из `data/case/` (если они там лежат) и фикстуру «ставка 14%».
- `ingest` опрашивает RSS из `config/sources.yaml` и пишет материалы в `raw_items`.
- `backend` (FastAPI) работает на http://localhost:8000, документация в `/docs`:
  - `/session`, `/feed`, `/feed/runs`, `/card/{id}`, `/trace/{id}`, `/ask`, `POST /feedback`, `POST /analyze` (вставить ссылку или текст) — карточки для клиента из сессии;
  - `/stories?business_only=true` — свежие сюжеты с типом события и статусом подтверждения;
  - `/stories/{id}` — сюжет, факты с цитатами и материалы; `/stories/{id}/bank_market`, `/stories/{id}/statistician`;
  - `/labeling` — разметка пар «сюжет × клиент» (только при `LABELING_ENABLED=true`, на демо — 404);
  - `/companies/{id}/counterparties` — граф платежей компании;
  - `/raw_items` (без полного текста), `/stats/ingest`, `/health` — служебные.
  - `/stories*`, `/raw_items`, `/stats/ingest` — только с сессией (`POST /session`); вставки (`/analyze`) личные: видны только своей компании.
- `ingest` раз в 5 минут склеивает материалы в сюжеты и извлекает факты: правила, а при `EXTRACT_LLM=true` подтверждённые сюжеты типирует Gemma через общий LLM-клиент (до 60 вызовов за проход).
- `frontend` работает на http://localhost:3000: макет главного экрана Альфа-Бизнеса, виджет «Важно для вашего бизнеса», лента и карточка. API доступен под `/api` (nginx проксирует на `backend`).

Postgres доступен с хоста на `localhost:55432` (порт задаёт `DB_HOST_PORT`), чтобы не конфликтовать с локально установленным PostgreSQL.

## Данные организаторов

В git не коммитятся (материалы банка и данные кейса), лежат на диске организаторов:

| Что | Откуда | Куда | Чем загружается |
|---|---|---|---|
| CSV кейса: компании (85,5 тыс.), платежи (1,38 млн), связи физлиц | [«Данные для участников»](https://drive.google.com/drive/folders/1Ep8wWoEHTg5Q8kwrLx2j_xFyxGUEVxFq) | `data/case/` | `init` (сам), принудительно — `python -m backend.db.init --load-case --force-case` |
| 9 обзоров ЦМИ (сканы PDF, 64 стр.) | [«Аналитические материалы»](https://drive.google.com/drive/folders/15xZTLF1lZ65p6o5xpNA_fRbxjoV-UlG1) | `data/bank/pdf/` | снимок (`data/snapshot/`) или заново: `python -m backend.bank.ocr` (Qwen3-VL, ≈8,5 ₽) → `python -m backend.bank.positions` (Kimi, ≈15 ₽) |
| Архив Telegram 01.04–23.10 (15 каналов, 40 тыс. постов, выгрузка Telethon) | пакет «источники», `tg_export.py` | любая папка | `python -m backend.ingest.tg_archive <папка с tg_*.jsonl>` |

Без CSV `init` загружает синтетику из `data/case_synthetic/` (200 компаний той же структуры) — демо-клиенты
`U_DEMO_*` работают и на ней, но граф контрагентов и отраслевые доли — только на настоящих CSV.

Архив Telegram нужен двум шагам: консенсусу рынка перед заседаниями ЦБ (неофициальные каналы за 10 дней до решения)
и «Статистику» (похожие случаи за 120 дней). Импорт идемпотентен, id постов совпадают с живым сбором `t.me/s`,
после импорта сбор продолжает с последнего поста архива. Реестр источников дополнен из пакета «источники»
(ведомства, отраслевые каналы и ленты; веса — `w_card` пакета): `config/sources.yaml`.

## Демо-база с нуля

На машине, где уже собраны знания (Р1), снять снимок — страницы ЦМИ, реестр позиций, консенсус, архив материалов
за 120 дней. Снимок содержит тексты ЦМИ и чужие материалы, поэтому в git не попадает (`data/snapshot/`):

```bash
uv run python -m backend.db.demo --export
```

Передать папку `data/snapshot/` (и CSV кейса в `data/case/`) на стенд не через git (файлообменник), положить в корень
проекта, затем там:

```bash
docker compose up -d --build
docker compose run --rm backend python -m backend.db.demo          # только правила, без ₽
docker compose run --rm -e LLM_MODE=live backend python -m backend.db.demo --llm   # + типирование моделью
```

Шаги: таблицы и фикстура → CSV кейса (или синтетика) → граф платежей → профили (Р2) → снимок знаний → один проход
сбора → склейка за 72 ч → факты → консенсус (из снимка; без снимка и с `--llm` — заново по архиву каналов).
Реестр ЦМИ из снимка — без повторного OCR и Kimi. Лента — http://localhost:3000 (вход `U_DEMO_BORROWER` / `cfo`
или `U_DEMO_DEPOSITOR` / `treasurer`).

Стенд по умолчанию (`docker-compose.yml`, переопределяется в `.env`): `backend` — `LLM_MODE=live`, `TOOL_SOURCE=db`,
`CARD_STORE=pipeline`, `CACHE_BACKEND=redis`, `CRITIC_LLM_ENABLED=true`, `LABELING_ENABLED=false` (порог ленты и число
глубоких разборов — `feed` в `config/scoring.yaml`); `ingest` — `EXTRACT_LLM=false` (для демо: `EXTRACT_LLM=true docker compose up -d ingest`,
до 60 вызовов Gemma за проход раз в 5 минут, ≈1 ₽). Ключ `LLM_API_KEY` — только в `.env`.

Перед показом — прогрев демо-кеша (глубокие карточки топ-сюжетов, ≈0,4 ₽ за карточку, кеш в Redis):

```bash
uv run python scripts/warm_demo_cache.py --stories 30 --max-rub 40
```

Проверка ленты без браузера — `uv run python scripts/check_feed_top.py --company U_DEMO_BORROWER --role cfo`
(топ-20 по скорингу и сюрприз по решению ЦБ).

### Если что-то не так

- **Нет ключа модели** — `LLM_MODE=fixture` в `.env`: лента и карточки из фикстур, без ₽.
- **Нет снимка** — `docker compose run --rm -e LLM_MODE=live backend python -m backend.db.demo --llm`: консенсус заново
  по архиву каналов; реестр ЦМИ — `python -m backend.bank.ocr` и `python -m backend.bank.positions` (≈25 ₽, PDF в `data/bank/pdf/`).
- **В карточке решения ЦБ пустая строка «Банк ждал»** — нет позиций ЦМИ по ставке: `SELECT count(*) FROM bank_positions`.
- **Решение ЦБ в ленте несколькими карточками** — `python -m backend.stories.cluster --hours 96` и
  `python -m backend.extract.run --hours 96 --force`: решения с одной датой и ставкой склеиваются в один сюжет.
- **`compose up` падает на `SESSION_SECRET`** — `echo "SESSION_SECRET=$(openssl rand -hex 32)" >> .env`.

Все вызовы моделей идут через `backend/llm/client.py` (бюджет и журнал команды), в том числе OCR сканов ЦМИ
(`backend/bank/vision.py`, Qwen3-VL, шаг `vision`): картинка уходит составным сообщением через `call_text`.

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
| `FEED_DEEP_PER_REQUEST`, `FEED_MIN_SCORE` | из `config/scoring.yaml` (`feed`: `1`, `0.05`) | переопределение настроек ленты: глубоких разборов за обращение (остальное — быстрые карточки) и порог значимости сюжета |
| `FEED_MAX_AGE_HOURS` | из `config/scoring.yaml` (`feed.max_age_hours`: `72`) | окно свежести живой ленты при `TOOL_SOURCE=db`: сюжеты старше в ленту не идут; `0` — без окна. На фикстуре окна нет |
| `SESSION_SECRET` | случайная строка (`openssl rand -hex 32`) | подпись сессий; на стенде задать обязательно, в git не хранить |
| `FEEDBACK_STORE` | `db` / `memory` (по умолчанию `db` при `TOOL_SOURCE=db`) | куда пишет `POST /feedback`: таблица `feedback` или память |
| `CACHE_BACKEND`, `CACHE_TTL_S` | `memory` / `redis`, секунды (24 ч) | кеш разбора, карточек и трасс |
| `CRITIC_LLM_ENABLED` | `true` / `false` | LLM-критик DeepSeek по сомнительным утверждениям |
| `EXTRACT_LLM` | `false` (по умолчанию) / `true` | воркер `ingest`: тип и факторы подтверждённых сюжетов ставит модель (шаг `extract`) |
| `LABELING_ENABLED` | `false` (по умолчанию) / `true` | открыть `/labeling` (профили компаний без сессии — только для разметки) |

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
├── docs/                      # архитектура, сопровождение, мониторинг, безопасность, деплой
└── tests/
```

Любая правка схем делается через Р1 и сразу вместе с фикстурами: после правки
`uv run python scripts/export_json_schemas.py`, иначе тест `test_schemas_json_up_to_date` упадёт.
