FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.4 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Сначала только зависимости — слой кешируется, пока не меняется uv.lock
COPY pyproject.toml uv.lock .python-version README.md ./
RUN uv sync --frozen --no-install-project --no-dev

COPY backend ./backend
COPY config ./config
# промпты планировщика, аналитиков, карточки и критика (Р3): без них глубокий разбор падает
COPY prompts ./prompts
COPY fixtures ./fixtures
COPY scripts ./scripts
COPY eval ./eval
RUN uv sync --frozen --no-dev

CMD ["uvicorn", "backend.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
