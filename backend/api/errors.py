"""Единый формат ошибок: {"error": {"code", "message", "trace_id"}} без внутренностей."""

import logging
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.security.audit import get_audit
from backend.security.guard import audit_foreign, trace_id_of
from backend.security.session import optional_session

log = logging.getLogger(__name__)

CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    422: "validation_error",
    429: "too_many_requests",
    503: "service_unavailable",
}


class ErrorDetail(BaseModel):
    code: str = Field(description="Машинный код: unauthorized, forbidden, not_found, validation_error, …")
    message: str = Field(description="Короткое сообщение для пользователя, без внутренностей")
    trace_id: str = Field(description="Id запроса для журнала (также в заголовке X-Trace-Id)")


class ErrorResponse(BaseModel):
    """Единый формат ошибки API."""

    error: ErrorDetail


def error_doc(description: str) -> dict[str, Any]:
    """Описание ответа-ошибки для `responses=` эндпоинта (OpenAPI)."""
    return {"description": description, "model": ErrorResponse}


def error_response(request: Request, status: int, code: str, message: str) -> JSONResponse:
    trace_id = trace_id_of(request) or uuid.uuid4().hex
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "trace_id": trace_id}},
        headers={"X-Trace-Id": trace_id},
    )


async def trace_middleware(request: Request, call_next):
    request.state.trace_id = uuid.uuid4().hex
    try:
        response = await call_next(request)
    except Exception:
        log.exception("необработанная ошибка trace_id=%s", request.state.trace_id)
        return error_response(request, 500, "internal_error", "Внутренняя ошибка сервера")
    response.headers["X-Trace-Id"] = request.state.trace_id
    return response


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = CODES.get(exc.status_code, "error")
    message = exc.detail if isinstance(exc.detail, str) else "Ошибка запроса"
    return error_response(request, exc.status_code, code, message)


async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = exc.errors()
    # company_id в теле запроса: чужой — это попытка доступа (403 + журнал), свой — просто лишнее поле
    session = optional_session(request)
    if session:
        audit = request.app.dependency_overrides.get(get_audit, get_audit)()
        for err in errors:
            is_company = err.get("type") == "extra_forbidden" and err["loc"][-1] == "company_id"
            if is_company and err.get("input") != session.company_id:
                audit_foreign(audit, request, session, str(err.get("input"))[:64])
                return error_response(request, 403, "forbidden", "Доступ к данным другой компании запрещён")
    fields = sorted({".".join(str(p) for p in e["loc"][1:]) or str(e["loc"][0]) for e in errors})
    return error_response(request, 422, "validation_error", "Некорректный запрос: " + ", ".join(fields))


async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
    log.error("необработанная ошибка trace_id=%s", trace_id_of(request), exc_info=exc)
    return error_response(request, 500, "internal_error", "Внутренняя ошибка сервера")


def install_error_handling(app: FastAPI) -> None:
    app.middleware("http")(trace_middleware)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_handler)
