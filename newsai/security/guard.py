"""Защита company_id: данные только компании из сессии, чужой id в запросе → 403 + журнал."""

from fastapi import HTTPException, Request

from newsai.security.audit import AuditLog
from newsai.security.session import Session


def trace_id_of(request: Request) -> str | None:
    return getattr(request.state, "trace_id", None)


def audit_foreign(
    audit: AuditLog,
    request: Request,
    session: Session,
    requested_company: str | None,
    requested_role: str | None = None,
) -> None:
    audit.record(
        user_id=session.user_id,
        session_company=session.company_id,
        requested_company=requested_company,
        endpoint=f"{request.method} {request.url.path}",
        trace_id=trace_id_of(request),
        requested_role=requested_role,
    )


def enforce_company(
    session: Session, requested_company_id: str | None, request: Request, audit: AuditLog
) -> str:
    """Возвращает company_id сессии. Явно переданный другой id → 403 и запись в журнал."""
    if requested_company_id is not None and requested_company_id != session.company_id:
        audit_foreign(audit, request, session, requested_company_id)
        raise HTTPException(status_code=403, detail="Доступ к данным другой компании запрещён")
    return session.company_id
