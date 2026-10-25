"""Локальный запуск всего проекта одной командой: API, сборщик новостей и фронт.

    uv run python scripts/dev.py                    # живые модели, сбор с типизацией сюжетов моделью
    EXTRACT_LLM=false uv run python scripts/dev.py  # сбор только правилами, без ₽
    LLM_MODE=fixture uv run python scripts/dev.py   # без ключа и без ₽

На сервере то же делает docker compose (сервисы backend, ingest, frontend). Нужна база (PostgreSQL +
pgvector): `docker compose up -d db`, по умолчанию localhost:55432. Ключ модели и прочие секреты читают сами
приложения из .env — скрипт его не открывает. Ctrl+C останавливает все три процесса; упал один —
останавливаются все.
"""

import contextlib
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DEFAULTS = {
    "DATABASE_URL": "postgresql+psycopg://newsai:newsai@localhost:55432/newsai",
    "LLM_MODE": "live",
    "EXTRACT_LLM": "true",
    "TOOL_SOURCE": "db",
    "CARD_STORE": "pipeline",
    "CACHE_BACKEND": "memory",
    "CRITIC_LLM_ENABLED": "true",
    "SESSION_SECRET": "local-dev",
}

PROCESSES = {
    "api": [sys.executable, "-m", "uvicorn", "backend.api.main:app", "--port", "8000"],
    "ingest": [sys.executable, "-m", "backend.ingest.run"],
    "web": ["npm", "--prefix", "frontend", "run", "dev"],
}


def _db_ok(url: str) -> bool:
    from sqlalchemy import create_engine, text

    try:
        with create_engine(url).connect() as conn:
            conn.execute(text("select 1"))
        return True
    except Exception:
        return False


def _pipe(tag: str, proc: subprocess.Popen) -> None:
    # у каждой строки лога — метка процесса
    assert proc.stdout is not None
    for line in proc.stdout:
        sys.stdout.write(f"[{tag}] {line}")
        sys.stdout.flush()


def main() -> int:
    env = {**DEFAULTS, **os.environ}
    if not _db_ok(env["DATABASE_URL"]):
        url = env["DATABASE_URL"]
        print(f"✗ база недоступна ({url}) — запустите: docker compose up -d db", file=sys.stderr)
        return 1

    procs: dict[str, subprocess.Popen] = {}

    def stop(*_: object) -> None:
        # своя группа процессов у каждого (start_new_session): гасим и дочерние — uvicorn, vite
        for p in procs.values():
            if p.poll() is None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(p.pid, signal.SIGTERM)
        deadline = time.monotonic() + 10
        for p in procs.values():
            try:
                p.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGKILL)

    def on_term(*_: object) -> None:
        stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, on_term)
    try:
        for tag, cmd in PROCESSES.items():
            procs[tag] = subprocess.Popen(
                cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                bufsize=1, start_new_session=True,
            )
            threading.Thread(target=_pipe, args=(tag, procs[tag]), daemon=True).start()
        print(
            "→ API http://localhost:8000 · интерфейс http://localhost:5173 · "
            f"сбор новостей: EXTRACT_LLM={env['EXTRACT_LLM']}, LLM_MODE={env['LLM_MODE']}",
            flush=True,
        )
        while True:
            for tag, p in procs.items():
                if p.poll() is not None:
                    msg = f"✗ {tag} завершился (код {p.returncode}) — останавливаю остальные"
                    print(msg, file=sys.stderr)
                    return 1
            time.sleep(2)
    except KeyboardInterrupt:
        return 0
    finally:
        stop()


if __name__ == "__main__":
    sys.exit(main())
