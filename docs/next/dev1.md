# Р1 — что осталось доделать (на 22.10, ветка `develop` @ после 67716c1)

Состояние проверено по коду в `develop` и по всем веткам на GitHub. В репозитории от Р1 есть только коммит
`bf5a696` «Структура репозитория по финальной архитектуре» (21.10). Если у тебя есть незапушенная работа —
сначала прочитай раздел «Как перейти на develop» в конце файла.

## Уже есть в develop

- Схемы 0.2.0 в `backend/schemas/`, фикстура `fixtures/rate_14/bundle.json`, экспорт JSON-схем.
- `docker-compose.yml`: db (pgvector), redis, init, backend, ingest, frontend (сборка из `frontend/Dockerfile`).
- Таблицы `backend/db/models.py`, `backend/db/init.py`, загрузка CSV кейса `backend/db/case_loader.py`.
- `config/sources.yaml` (RSS, API, 41 Telegram-канал), `config/bank_docs.yaml` (9 материалов ЦМИ).
- Сбор RSS: `backend/ingest/` (http, normalize, registry, rss, store, run) → таблица `raw_items`.

## Чего нет (папки пустые: `backend/stories`, `extract`, `knowledge`, `bank`)

Это главный блокер всей команды: без сюжетов и фактов лента состоит из одной демо-карточки,
а вечерний прогон 20 сюжетов, отчёт качества и метрики невозможны.

### 1. Склейка новостей в сюжеты — `backend/stories/` (приоритет 1)
- SimHash ≤ 3 для дублей; затем `deepvk/USER-bge-m3` + общая сущность + тип события в окне 72 ч;
  запасной вариант без эмбеддингов — TF-IDF, косинус ≥ 0,30.
- Статус `rumor` / `confirmed`, `corroboration` (число независимых источников), `source_ids`, `first_seen`, `last_seen`.
- Запись в таблицу `stories` (JSONB `data` = `Story.model_dump(mode="json")`).
- Готово, когда: из `raw_items` за сутки получается ≥ 30 сюжетов, тест на сохранённой ленте.

### 2. Экстрактор фактов — `backend/extract/` (приоритет 1)
- Natasha + словари → LLM-шаг `extract` (Gemma 4, запасная Qwen3.6; уже описан в `config/models.yaml`).
- Вызов только через `backend.llm.get_client().call(step="extract", schema=…, trace_id=…, dev="r1")`,
  текст новости — только через `data_block()` (инвариант 7).
- `EventFacts`: `event_type` из `config/event_types.yaml`, `summary` + `summary_evidence` (цитаты с `source_id` = id raw_item),
  `numbers` с дословной `quote` (код проверяет число по цитате), `channels` = `ChannelImpact(direction, strength)`,
  **`sectors_okved` у отраслевых событий** (просьба Р2: санкции против нефтяников → `["06"]`), `is_rumor`, `is_intent`.
- Запись в `event_facts` (JSONB `data`).
- Готово, когда: 100+ сюжетов с фактами в БД; сравнение Gemma 4 / Qwen3.6 на 10 сюжетах (₽ и точность).

### 3. Реестр ЦМИ — `backend/bank/` (приоритет 1)
- 9 PDF из `config/bank_docs.yaml` (лежат в `data/bank/pdf/`, сканы → OCR) → `BankPosition`.
- id строго с префиксом `cmi_` (иначе проверка карточки отклонит `bank_view`), `quote` дословно со страницы,
  `doc_date`, `channel`, `direction`, `forecast` (значение и дата), `valid_until`, `superseded_by`.
- LLM-шаг `bank_registry` (Kimi-K2.6) — **только с `reasoning_effort: none`** (с рассуждением 2,43 ₽ за запрос).
- Поиск BM25 + pgvector, RRF. Запись в `bank_positions`.

### 4. Знания аналитиков и консенсус — `backend/knowledge/` (приоритет 2)
- `MarketKnowledge` с id `mk_…`, полем `origin`, `published_at` строго раньше даты сюжета (инвариант 10).
- Консенсус по 3 последним заседаниям ЦБ (включая 11.09 — 14%): `kind=expectation`, `event_type`,
  `target_event_date`, `expected_value`; агрегат — `source_id="consensus"` (нужно Р2 для «рынок ждал»).

### 5. Граф платежей — `backend/profile/graph.py` (приоритет 2, срок был Д2 15:00)
- Топ-10 контрагентов по сумме, ОКВЭД, доли, HHI, поставщики/покупатели, группа и физлица (физлица — только счётчиком).
- `CounterpartyGraph` → таблица `counterparty_graphs`. После этого Р2 запускает `python -m backend.profile.publish`.

### 6. Агенты `agents/bank_market.py` и `agents/statistician.py` (приоритет 3)
- «Банк и рынок»: тезисы ЦМИ + консенсус с цитатами и датами, пометка «до события».
- «Статистик»: чувствительность отрасли из матрицы + 3 похожих сюжета из архива.
- Если не успеваешь — Р3 обходится инструментами `get_bank_positions` / `get_market_knowledge` / `find_similar_cases`.

### 7. Остальное по плану
- Telegram (Telethon, выгрузка 41 канала, FloodWait; запасной t.me/s) — можно урезать до RSS.
- Инструмент разметки «сюжет × клиент → важно/нет, канал, знак» + экспорт CSV `pair_id,labeler,relevance` (0–3) — нужен Р2 для каппы.
- Эндпоинт «вставить ссылку или текст»: на твоей стороне — trafilatura → raw_item → сюжет → факты; API-обёртку сделает Р3.
- Приоры для топ-10 типов событий, версии сюжета, веса источников.
- 5 новостей с внедрёнными командами → прогон → карточка не изменилась.

### 8. Инфраструктура (сделать в первую очередь, 5 минут)
- **`Dockerfile`: добавить `COPY prompts ./prompts`** рядом с `COPY config ./config`. Без этого в контейнере
  глубокий разбор падает и API молча отдаёт фикстурную карточку «на проверке».
- `.env.example`: добавить `LLM_API_BASE_FALLBACK`, `CARD_STORE`, `TOOL_SOURCE=db`, `CACHE_BACKEND=redis` (без значений ключей).
- `mypy` в dev-зависимостях (`uv add --dev mypy`).
- День 5: деплой, запуск с нуля по README, THIRD_PARTY_NOTICES, демо-кеш 30 сюжетов × 5 клиентов.

## Открытые вопросы к тебе в `docs/HANDOFFS.md`
Поле заголовка в `InsightCard`; ссылки у `actions` и `what_would_change`; хранить ли `Analysis` отдельным объектом;
формат `Claim.trigger` (`fix_rate` у Р3 против `loa+rate_up` в фикстуре); `sectors_okved`; граф контрагентов;
экспорт разметки; консенсус `MarketKnowledge`; Dockerfile.

## Как подключается к остальным
Р3 читает всё через `TOOL_SOURCE=db` (`backend/agents/tools/db_source.py`): `SELECT data FROM` таблиц
`company_profiles`, `counterparty_graphs`, `raw_items` (где `dropped_reason IS NULL`), `bank_positions`,
`market_knowledge`, `stories`, `event_facts`. Пиши в эти таблицы полный объект схемы в JSONB `data` —
и конвейер с картой подхватят данные без правок.

## Как перейти на develop
```bash
git fetch origin
git checkout feature/data-schemas-ingest
git merge origin/develop          # твоя ветка — предок develop, будет fast-forward
git checkout -b feature/dev1-stories
```
Незапушенные изменения: сначала закоммить их в своей ветке, потом `git merge origin/develop`
(структура `backend/` та же, конфликтов в твоих папках быть не должно). Проверка: `uv run pytest -q` — всё зелёное.
Сливай в `develop` через PR, маленькими кусками (stories → extract → bank).
