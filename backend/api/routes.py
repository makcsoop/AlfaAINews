"""Эндпоинты /session /feed /card /trace /ask. company_id и роль — только из сессии."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from backend.agents.qa import answer_question
from backend.api.store import CardStore, DeepRun, get_store
from backend.llm import get_client
from backend.schemas.common import UserRole
from backend.schemas.insight import InsightCard, RunTrace
from backend.security.audit import AuditLog, get_audit
from backend.security.guard import audit_foreign, enforce_company, trace_id_of
from backend.security.ratelimit import ask_limiter
from backend.security.session import (
    COOKIE_NAME,
    Session,
    current_session,
    demo_companies,
    issue_token,
    ttl_seconds,
)

router = APIRouter()

SessionDep = Annotated[Session, Depends(current_session)]
StoreDep = Annotated[CardStore, Depends(get_store)]
AuditDep = Annotated[AuditLog, Depends(get_audit)]

ERRORS = {
    401: {"description": "Нет сессии или токен недействителен"},
    403: {"description": "Запрошены данные другой компании"},
}


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_id: str = Field(description="Компания из списка демо-компаний")
    role: UserRole = Field(description="Роль пользователя")
    user_id: str | None = Field(default=None, description="Необязательно; по умолчанию демо-пользователь")


class SessionInfo(BaseModel):
    company_id: str
    role: UserRole
    user_id: str


class LoginResponse(SessionInfo):
    token: str = Field(description="Подписанный токен; также ставится в cookie `session`")
    expires_in: int = Field(description="Срок жизни, секунды")


class AskRequest(BaseModel):
    """company_id в теле запрещён: компания берётся только из сессии."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=2000)
    card_id: str | None = Field(default=None, description="Карточка, по которой задан вопрос")


class AskResponse(BaseModel):
    answer: str
    refs: list[str] = Field(default_factory=list)
    tools_used: list[str] = Field(default_factory=list)
    trace_id: str
    status: Literal["ok", "refused", "degraded"] = "ok"


@router.post("/session", response_model=LoginResponse, tags=["session"], summary="Демо-вход")
def login(body: LoginRequest, request: Request, response: Response, audit: AuditDep) -> LoginResponse:
    """Выдаёт сессию только для демо-компаний (env DEMO_COMPANIES). Токен также кладётся в cookie."""
    if body.company_id not in demo_companies():
        audit.record(
            user_id=body.user_id,
            session_company=None,
            requested_company=body.company_id[:64],
            endpoint="POST /session",
            trace_id=trace_id_of(request),
        )
        raise HTTPException(status_code=403, detail="Эта компания недоступна для демо-входа")
    session = Session(
        company_id=body.company_id,
        role=body.role,
        user_id=body.user_id or f"demo_{body.company_id}_{body.role}",
    )
    token = issue_token(session)
    response.set_cookie(COOKIE_NAME, token, max_age=ttl_seconds(), httponly=True, samesite="lax", path="/")
    return LoginResponse(
        **session.model_dump(include={"company_id", "role", "user_id"}), token=token, expires_in=ttl_seconds()
    )


@router.get(
    "/session", response_model=SessionInfo, tags=["session"], summary="Текущая сессия", responses=ERRORS
)
def whoami(session: SessionDep) -> SessionInfo:
    return SessionInfo(company_id=session.company_id, role=session.role, user_id=session.user_id)


@router.get(
    "/feed",
    response_model=list[InsightCard],
    tags=["cards"],
    summary="Лента карточек компании",
    responses=ERRORS,
)
def feed(
    request: Request,
    session: SessionDep,
    store: StoreDep,
    audit: AuditDep,
    role: Annotated[
        UserRole | None, Query(description="Только роль сессии; другая даёт 403 (роль берётся из сессии)")
    ] = None,
    company_id: Annotated[
        str | None, Query(description="Только своя компания из сессии; чужая даёт 403")
    ] = None,
) -> list[InsightCard]:
    """Карточки компании из сессии, по убыванию значимости (relevance.score)."""
    cid = enforce_company(session, company_id, request, audit)
    if role is not None and role != session.role:
        audit_foreign(audit, request, session, None, requested_role=role)
        raise HTTPException(status_code=403, detail="Роль берётся только из сессии")
    cards = store.list_cards(cid, session.role)
    return sorted(cards, key=lambda c: c.relevance.score, reverse=True)


@router.get(
    "/feed/runs",
    response_model=list[DeepRun],
    tags=["cards"],
    summary="Журнал глубоких разборов ленты компании",
    responses=ERRORS,
)
def feed_runs(session: SessionDep, store: StoreDep) -> list[DeepRun]:
    """Глубокие разборы ленты компании из сессии: опубликованные, отброшенные по значимости, неудачные; ₽."""
    runs = getattr(store, "deep_runs", None)
    return runs(session.company_id) if runs else []


@router.get(
    "/card/{card_id}",
    response_model=InsightCard,
    tags=["cards"],
    summary="Карточка",
    responses={**ERRORS, 404: {"description": "Карточки нет или она не принадлежит компании"}},
)
def card(
    card_id: str, request: Request, session: SessionDep, store: StoreDep, audit: AuditDep
) -> InsightCard:
    found = store.get_card(card_id, session.company_id)
    if found is None:
        owner = store.card_owner(card_id)
        if owner is not None and owner != session.company_id:
            audit_foreign(audit, request, session, owner)
        raise HTTPException(status_code=404, detail="Карточка не найдена")
    return found


@router.get(
    "/trace/{trace_id}",
    response_model=RunTrace,
    tags=["cards"],
    summary="Журнал разбора",
    responses={**ERRORS, 404: {"description": "Трассировки нет или она не принадлежит компании"}},
)
def trace(trace_id: str, request: Request, session: SessionDep, store: StoreDep, audit: AuditDep) -> RunTrace:
    found = store.get_trace(trace_id)
    if found is None:
        raise HTTPException(status_code=404, detail="Трассировка не найдена")
    if found.company_id != session.company_id:
        if found.company_id is not None:
            audit_foreign(audit, request, session, found.company_id)
        raise HTTPException(status_code=404, detail="Трассировка не найдена")
    return found


class StepCost(BaseModel):
    calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_rub: float = 0.0


class TraceCost(BaseModel):
    """Деньги по прогону: ₽ по шагам из журнала вызовов и состояние дневного бюджета (для экрана метрик)."""

    trace_id: str
    cost_rub: float
    cost_by_step: dict[str, StepCost]
    budget: dict


def _cost_by_step(found: RunTrace) -> dict[str, StepCost]:
    out: dict[str, StepCost] = {}
    for c in found.llm_calls:
        b = out.setdefault(c.purpose, StepCost())
        b.calls += 1
        b.tokens_in += c.tokens_in
        b.tokens_out += c.tokens_out
        b.cost_rub = round(b.cost_rub + c.cost_rub, 6)
    return out


@router.get(
    "/trace/{trace_id}/cost",
    response_model=TraceCost,
    tags=["cards"],
    summary="Стоимость разбора по шагам",
    responses={**ERRORS, 404: {"description": "Трассировки нет или она не принадлежит компании"}},
)
def trace_cost(
    trace_id: str, request: Request, session: SessionDep, store: StoreDep, audit: AuditDep
) -> TraceCost:
    found = trace(trace_id, request, session, store, audit)
    steps = _cost_by_step(found)
    return TraceCost(
        trace_id=found.run_id,
        cost_rub=round(sum(s.cost_rub for s in steps.values()), 6),
        cost_by_step=steps,
        budget=get_client().budget.summary(),
    )


@router.post(
    "/ask",
    response_model=AskResponse,
    tags=["ask"],
    summary="Вопрос по карточке и своей компании",
    responses={
        **ERRORS,
        404: {"description": "Карточка не найдена"},
        429: {"description": "Слишком много вопросов за минуту"},
    },
)
def ask(
    body: AskRequest, request: Request, session: SessionDep, store: StoreDep, audit: AuditDep
) -> AskResponse:
    """Ответ по досье карточки и инструментам своей компании. Компания берётся только из сессии."""
    if not ask_limiter.allow(ask_limiter.key_of(session)):
        raise HTTPException(status_code=429, detail="Слишком много вопросов, попробуйте через минуту")
    if body.card_id and store.get_card(body.card_id, session.company_id) is None:
        owner = store.card_owner(body.card_id)
        if owner is not None and owner != session.company_id:
            audit_foreign(audit, request, session, owner)
        raise HTTPException(status_code=404, detail="Карточка не найдена")
    result = answer_question(
        body.question,
        session=session,
        card_id=body.card_id,
        store=store,
        client=get_client(),
        trace_id=trace_id_of(request) or "",
        audit=audit,
    )
    return AskResponse(**result.model_dump(include={"answer", "refs", "tools_used", "status", "trace_id"}))
