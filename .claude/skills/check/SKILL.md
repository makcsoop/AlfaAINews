---
name: check
description: Полная проверка перед коммитом: тесты, линт, типы и ревью инвариантов через агентов, в основной контекст только итог.
disable-model-invocation: true
context: fork
agent: general-purpose
---
1. Поручи агенту test-runner запустить `uv run pytest -q`, `uv run ruff check .`, `uv run mypy newsai`.
2. Поручи агенту reviewer проверить `git diff HEAD`.
Оба запусти параллельно. Верни сводку: OK или список проблем (файл:строка, причина), не больше 20 строк.
