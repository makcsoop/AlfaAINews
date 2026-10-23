# Р1 — что осталось доделать (на 23.10, после слияния `feature/labeling-bank-knowledge` в `develop`)

## Уже есть в develop
- Схемы 0.2.0, Docker Compose, таблицы, загрузка CSV, RSS (ПРАЙМ, Интерфакс, РБК, ТАСС, Ъ, Ведомости, ЦБ).
- Telegram: публичные страницы t.me/s без входа, Telethon — при наличии сессии; бэкфилл архива каналов.
- Отсев словарём, сущности, склейка в сюжеты (`backend/stories`), статус и подтверждения.
- Экстрактор фактов: правила + LLM-слой за портом, проверка цитат и чисел кодом (`backend/extract`); сравнение Gemma/Qwen.
- Граф платежей (`backend/profile/graph.py`): топ-10 контрагентов, отрасли, доли, HHI.
- Реестр ЦМИ: OCR сканов, 104 позиции из 8 обзоров, сверка цитат, устаревшие позиции, поиск BM25 (10/10).
- Консенсус рынка по заседаниям ЦБ (24.07, 11.09, 23.10) с проверкой направления кодом.
- `agents/bank_market.py`, `agents/statistician.py`, эндпоинты `/stories/{id}/bank_market`, `/stories/{id}/statistician`.
- Инструмент разметки `/labeling`: 60 пар, несколько разметчиков, выгрузка CSV.

## Что осталось

### 1. Срочно (до демо)
- **Решение ЦБ 23.10 в `config/cbr_meetings.yaml`** — живой сюрприз «рынок ждал / вышло» на демо.
- **`Dockerfile`: `COPY prompts ./prompts`** — без этого в контейнере глубокий разбор падает.
- **LLM только через общий клиент.** `OpenAIJsonLLM` ходит к провайдеру напрямую и читает `.env` (инварианты 1 и 9).
  Замена готова: `backend.llm.port.ClientJsonLLM(max_calls=…)` — тот же `complete_json` и `usage`,
  в `extract/run.py`, `bank/positions.py`, `knowledge/consensus.py`, `scripts/compare_extract_models.py` поменять импорт.
- **`/labeling` закрыть на демо** (флаг или только dev): страница открыта без сессии и показывает профили компаний.

### 2. Данные для живого конвейера
- Заполнить таблицы для `TOOL_SOURCE=db`: `stories`, `event_facts`, `bank_positions`, `market_knowledge`,
  `counterparty_graphs`, `raw_items` — JSONB `data` = полный объект схемы (Р3 читает `SELECT data FROM …`).
- `EventFacts.sectors_okved` у отраслевых событий (просьба Р2).
- `MarketKnowledge` консенсуса: `kind=expectation`, `target_event_date`, `expected_value`, `origin`, `published_at` до заседания.
- Скрипт «поднять демо-БД с нуля»: init → ingest за N дней → stories → extract → bank → consensus.

### 3. По плану, если успеваешь
- Механизмы «событие → фактор → отрасль» из архива аналитиков (ждут выгрузку 14 370 постов).
- Расширить консенсус до 20+ каналов (нужен список Honey Money с @username).
- Приоры для топ-10 типов, версии сюжета, веса источников; 5 новостей с внедрёнными командами → карточка не меняется.
- Эндпоинт «вставить ссылку»: на твоей стороне trafilatura → raw_item → сюжет → факты (API-обёртку делает Р3).
- День 5: деплой, запуск с нуля по README, THIRD_PARTY_NOTICES, демо-кеш.

### 4. Разметка
Твои 20 пар — вручную на `/labeling`; остальные 40 — команде. Каппа: `python -m backend.eval.agreement labels.csv` (Р2).

## Ждём от организаторов / заказчика
USER-bge-m3 разрешён?; единицы `usage.cost` (у Р3 в клиенте уже учтены через `LLM_COST_UNIT`); можно ли учиться на постах Telegram;
недостающие материалы ЦМИ («Commodities performance», прогноз к 11.09 и к 23.10); список аналитических каналов.

## Как продолжить
```bash
git fetch origin && git checkout develop && git pull
git checkout -b feature/dev1-<задача>
uv sync && uv run pytest -q
```
Сливай в `develop` через PR. Открытые вопросы — `docs/HANDOFFS.md`, раздел «Запросы».
