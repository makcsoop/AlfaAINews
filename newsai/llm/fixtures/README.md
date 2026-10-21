# Фикстуры LLM

В режиме `LLM_MODE=fixture` клиент не ходит в сеть. Ответ модели берётся из файла:

    newsai/llm/fixtures/<step>/<fixture_key>.json      # fixture_key по умолчанию "default"

Содержимое файла — ровно тот JSON, который вернула бы модель (он проходит ту же
валидацию Pydantic-схемой, что и живой ответ). Пример `extract/default.json`:

    {"facts": [], "event_type": "rate_decision"}

Токены оцениваются как `len(text) // 4`, стоимость 0 ₽, запись в журнал делается.
Нет файла — `LLMUnavailable`. Фикстуры конкретных шагов добавляют задачи этих шагов.
