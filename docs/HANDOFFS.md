# Передачи и запросы

Обновляет тот, кто отдаёт. Статус: ⬜ не начато · 🟨 в работе · ✅ отдано · ❌ сорвано (в «Комментарий» — новый срок).

| День, срок | Что | От | Кому | Статус | Комментарий |
|---|---|---|---|---|---|
| Д1 13:00 | Pydantic-схемы + фикстура rate_14 | Р1 | Р2, Р3 | ⬜ | |
| Д1 вечер | LLM-клиент с бюджетом и журналом | Р3 | Р1 | ✅ | `backend.llm.get_client().call(step=…, schema=…, trace_id=…, dev="r1")`; шаги и запасные — `config/models.yaml`; тексты новостей через `data_block()`; в тестах `LLM_MODE=fixture`, ответы в `backend/llm/fixtures/<step>/` |
| Д1 вечер | API на заглушках /feed /card /trace /ask | Р3 | Р2 | ✅ | вход `POST /session {company_id, role}` (демо: U_DEMO_BORROWER, U_DEMO_DEPOSITOR), дальше cookie; OpenAPI в `/docs`; ошибки `{error: {code, message, trace_id}}` |
| Д2 15:00 | Граф платежей: контрагенты, HHI | Р1 | Р2 | ⬜ | |
| Д2 15:00 | Профили и матрица каналов | Р2 | Р3 | ✅ | 30 факторов и 52 отрасли в `config/channels_matrix.yaml` (+ `sector_links`), 40 типов в `event_types.yaml` (+ `prior`); профиль v1 — `backend.profile.exposures.with_exposures(profile, graph)`; в БД: `python -m backend.profile.publish` пишет все профили в `company_profiles` (граф берёт из `counterparty_graphs`, если есть) |
| Д2 вечер | Сюжеты и факты 100+ | Р1 | Р2, Р3 | ⬜ | |
| Д2 вечер | Скоринг v0 и топ-K | Р2 | Р3 | ✅ | `backend.ranking.score.score_pair(story, profile_v1, now=…, facts=…, graph=…, sources=…, analysis=…)` → `RelevanceScore` (вместо `relevance_score` из фикстуры в `stub_steps`); топ-K — `backend.ranking.select.select_deep(scores, sector_of, spent_rub=…, limit_rub=…)`; быстрая карточка — `backend.ranking.fast_card.fast_card(...)` (`stage="fast"`); веса — `config/scoring.yaml` |
| Д3 11:00 | Инструмент разметки | Р1 | все | ⬜ | |
| Д3 15:00 | Реестр ЦМИ + поиск | Р1 | Р2, Р3 | ⬜ | |
| Д3 вечер | Консенсус по ЦБ | Р1 | Р2 | ⬜ | |
| Д3 вечер | «Банк и рынок», «Статистик», примеры разборов | Р1 | Р3 | ⬜ | |
| Д3 вечер | Analysis + Card | Р3 | Р2 | 🟨 | на фикстуре rate_14 готово: `run_for_client(story_id, company_id, role, sector=…)` → (InsightCard, RunTrace), `/feed`; `AnalystReport.channels[]` — `direction`/`strength` (направление фактора, схемы 0.2.0); ждём живые сюжеты и скоринг |
| Д4 13:00 | Приоры каналов | Р1 | Р2 | ⬜ | |
| Д4 15:00 | Эндпоинт «вставить ссылку» | Р1 | Р2 | ⬜ | |
| Д4 день | Данные трассы и журнал стоимости | Р3 | Р2 | ✅ | `GET /trace/{id}` (RunTrace: шаги, инструменты, вызовы моделей), `GET /trace/{id}/cost` (₽ по шагам, остаток бюджета); `POST /ask` — вопрос-ответ |
| Д5 11:00 | Стенд и демо-кеш | Р1 | все | ⬜ | |

## Запросы на правки чужой зоны
| Кто просит | Чья зона | Что нужно | Зачем | Статус |
|---|---|---|---|---|
| Р3 | Р1 | Поле заголовка карточки (`headline: str`) в `InsightCard` + фикстура | модель пишет заголовок, сейчас он лежит рядом в `CardResult`, не в схеме | ⬜ |
| Р3 | Р1 | `analyst_report` в фикстуре rate_14: `impact` ставки −0,6 — это знак «плохо для отрасли»; по инварианту 5 нужно направление фактора (ставка ↑ = +) | аналитики Р3 пишут направление фактора, фикстура противоречит; то же со `stance` позиций ЦМИ по `rate_cost` (сейчас знак переворачивается в `backend/cards/signals.py`, `COST_CHANNELS`); Р2 читает знак из разбора — нужно одно соглашение | ✅ закрыто схемами 0.2.0: `ChannelImpact.direction/strength`, `BankPosition.direction` — направление фактора; `COST_CHANNELS` в Р3 удалён |
| Р3 | Р1, Р2 | Реализация `ToolDataSource` из БД (`backend/agents/tools/source.py`: profile, counterparties, raw_items, bank_positions, market_knowledge, stories, event_facts, sources) | сейчас инструменты агентов работают на `FixtureDataSource("rate_14")` | ✅ сделал Р3: `backend/agents/tools/db_source.py`, `TOOL_SOURCE=db` (читает колонку `data` таблиц Р1) |
| Р3 | Р1 | Добавить `mypy` в dev-зависимости `pyproject.toml` | команда `uv run mypy backend` из CLAUDE.md не запускается | ⬜ |
| Р3 | Р1 | `Action` и `what_would_change` в `InsightCard` без `refs`/`kind` (строки) | инвариант 4: у каждого утверждения карточки метка и ссылки; сейчас ссылки проверяются кодом, но в карточку не сохраняются | ⬜ |
| Р3 | Р1 | Добавить `LLM_API_BASE_FALLBACK=https://api.alfahack-gateway26.ru/v1` в `.env.example` (пустая строка — отключить запасной адрес) | клиент `backend/llm/client.py` при сетевой ошибке/5xx на основном адресе один раз повторяет запрос на запасной (адрес для российских хостингов); по умолчанию уже зашит в `backend/llm/settings.py` | ⬜ |
| Р3 | Р1 | Kimi-K2.6 с рассуждением стоит 2,43 ₽ за крошечный запрос (без него 0,02 ₽). Звать модели только через клиент: в `config/models.yaml` у шагов `reasoning_effort: none` по умолчанию, `bank_registry` уже так | реестр ЦМИ может съесть дневной лимит | ⬜ |
| Р3 | Р1 | Сохранить `Analysis` из конвейера Р3 в БД/фикстуру: сейчас кеш разбора (`backend/agents/cache.py`) хранит свой формат (досье + отчёты), а не `Analysis` с `drivers/claims/agreement`; ключ `story_id:sector:version` совпадает с `InsightCard.analysis_ref` | нужен ли вам `Analysis` как отдельный объект и где он лежит — иначе Р3 соберёт его из кеша по просьбе | ⬜ |
| Р3 | Р1 | `Claim.trigger` для `bank_offer`: Р3 пишет id триггера (`fix_rate`, `long_deposit`, `fx_hedge`), в фикстуре — формат `loa+rate_up`; согласовать формат | валидатор схемы требует триггер, формат не зафиксирован | ⬜ |
| Р3 | Р1 | `Dockerfile`: добавить `COPY prompts ./prompts` рядом с `COPY config ./config` — планировщик, аналитики, карточка и LLM-критик читают промпты из `prompts/` | без этого в контейнере глубокий разбор падает и API молча отдаёт фикстурную карточку «на проверке» | ⬜ |
| Р2 | Р3 | `tests/test_analysts.py`: проверка `len(channels) == 15` заменена на 30 (таксономия расширена до 30 факторов по плану) — уже в ветке Р2, посмотреть | иначе тест падает после расширения матрицы | ✅ сделано Р2, нужен взгляд Р3 |
| Р2 | Р3 | `backend/cards/features.py` `CHANNEL_LABELS` знает 15 факторов: брать названия из `backend.profile.taxonomy.channel_name()` | новые 15 факторов попадут в карточку как id (`import_prices`) | ✅ сделал Р3: `channel_label()` берёт названия из скоринга (`factor_label` → `channel_name`), `CHANNEL_LABELS` удалён |
| Р2 | Р3 | В вкладах скоринга появились факторы `direct:*`, `counterparty:XX`, `linked_sector:XX`, `surprise` — не каналы матрицы; в шаблоне карточки подпись — `backend.ranking.select.factor_label()` | «почему вы это видите» для прямых совпадений | ✅ сделал Р3: подписи `direct:*`, `counterparty:XX`, `linked_sector:XX`, `surprise` в шаблоне и промпте карточки — `factor_label()` |
| Р2 | Р1 | `EventFacts.sectors_okved` заполнять у отраслевых событий (санкции против нефтяников → `["06"]`) | скоринг ослабляет такие события для чужих отраслей (`off_sector`) и даёт прямое совпадение своим и связанным | ⬜ |
| Р2 | Р1 | Граф контрагентов в `counterparty_graphs` (срок Д2 15:00) | профиль v1 добавляет каналы отраслей контрагентов и HHI; пока профили без него | ⬜ |
| Р2 | Р3 | Уверенность: `backend.ranking.confidence.confidence(story=…, relevance=…, profile=…, sources=…, analysis=…, claims=…, bank_positions=…, market=…)` — 5 признаков плана (источники, согласие аналитиков, доля проверенных утверждений, банк и рынок, прямая связь); предлагаю вызывать её в `assemble_card` вместо `compute_confidence`, чтобы быстрая и глубокая карточка считались одинаково | по плану уверенность — зона Р2; сейчас две разные формулы | ✅ сделал Р3: `assemble_card` зовёт `ranking.confidence.confidence`, `compute_confidence` — запасная при исключении; у вкладчика high, у заёмщика medium (в профиле v1 exposure key_rate помечена как предположение) |
| Р2 | Р3 | Сюрприз кодом: `backend.ranking.surprise.find_surprise(story, facts, bank_positions=…, market=…)` → `Surprise` по числу из фактов, прогнозу ЦМИ к дате события и консенсусу (медиана ожиданий до события); можно брать вместо `DraftSurprise` от модели | числа не из LLM; тот же объект идёт во вклад «сюрприз» скоринга | ✅ сделал Р3: `find_surprise` первым, `DraftSurprise` модели — только если код вернул None (с проверкой по цитате); тот же сюрприз идёт в `score_pair` |
| Р2 | Р3 | Пересчёт по разбору: `score_pair(..., analysis=Analysis, role=…)` — воздействие из `Analysis.drivers`, знак и веса ролей ставит скоринг | шаг 11 архитектуры, вызывать после арбитра | ✅ сделал Р3: после арбитра `Analysis` собирается из отчётов (`backend/cards/analysis.py`), `score_pair(..., analysis=, role=)` пересчитывает relevance, он попадает в карточку |
| Р2 | Р1 | Экспорт разметки в CSV `pair_id,labeler,relevance` (0–3) | `backend.eval.agreement` считает каппу на 10 общих парах | ⬜ |
| Р2 | Р1 | `MarketKnowledge` ожиданий перед заседаниями: `kind=expectation`, `event_type`, `target_event_date`, `expected_value`; агрегат — `source_id="consensus"` | «рынок ждал» в сюрпризе | ⬜ |
