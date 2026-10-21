"""Журнал безопасности: попытки доступа к чужой компании. В памяти и в JSONL по дням."""

import json
import logging
import os
import threading
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

log = logging.getLogger(__name__)

DEFAULT_AUDIT_DIR = Path("data/security_audit")


class AuditRecord(BaseModel):
    """Без лишних персональных данных: только идентификаторы и маршрут."""

    at: datetime
    user_id: str | None
    session_company: str | None
    requested_company: str | None
    endpoint: str
    trace_id: str | None


class AuditLog:
    def __init__(self, directory: Path | None = None) -> None:
        env = os.environ.get("SECURITY_AUDIT_DIR")
        self.directory = directory or (Path(env) if env else DEFAULT_AUDIT_DIR)
        self.records: list[AuditRecord] = []
        self._lock = threading.Lock()

    def record(
        self,
        *,
        user_id: str | None,
        session_company: str | None,
        requested_company: str | None,
        endpoint: str,
        trace_id: str | None,
    ) -> AuditRecord:
        rec = AuditRecord(
            at=datetime.now(UTC),
            user_id=user_id,
            session_company=session_company,
            requested_company=requested_company,
            endpoint=endpoint,
            trace_id=trace_id,
        )
        with self._lock:
            self.records.append(rec)
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                path = self.directory / f"{rec.at:%Y-%m-%d}.jsonl"
                with path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(rec.model_dump(mode="json"), ensure_ascii=False) + "\n")
            except OSError:
                log.warning("не удалось записать журнал безопасности на диск")
        log.warning(
            "security: доступ к чужим данным endpoint=%s session_company=%s requested=%s",
            endpoint,
            session_company,
            requested_company,
        )
        return rec


_audit: AuditLog | None = None


def get_audit() -> AuditLog:
    """FastAPI-зависимость: общий журнал процесса (в тестах подменяется)."""
    global _audit
    if _audit is None:
        _audit = AuditLog()
    return _audit
