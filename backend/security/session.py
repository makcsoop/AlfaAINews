"""Сессия: company_id и роль берутся только отсюда. Токен подписан HMAC-SHA256."""

import base64
import hashlib
import hmac
import json
import logging
import os
import time

from fastapi import HTTPException, Request
from pydantic import BaseModel

from backend.schemas.common import UserRole

log = logging.getLogger(__name__)

COOKIE_NAME = "session"
DEV_SECRET = "dev-insecure-session-secret"
DEFAULT_TTL_SECONDS = 8 * 3600
_warned = False


class Session(BaseModel):
    company_id: str
    role: UserRole
    user_id: str
    expires_at: int = 0


class InvalidToken(Exception):
    pass


def _secret() -> bytes:
    global _warned
    secret = os.environ.get("SESSION_SECRET")
    if not secret:
        if not _warned:
            log.warning("SESSION_SECRET не задан: используется секрет для разработки, не для продакшена")
            _warned = True
        secret = DEV_SECRET
    return secret.encode()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(payload: str) -> str:
    return _b64(hmac.new(_secret(), payload.encode(), hashlib.sha256).digest())


def ttl_seconds() -> int:
    try:
        return int(os.environ.get("SESSION_TTL_SECONDS", DEFAULT_TTL_SECONDS))
    except ValueError:
        return DEFAULT_TTL_SECONDS


def issue_token(session: Session, ttl: int | None = None, now: float | None = None) -> str:
    exp = int((now if now is not None else time.time()) + (ttl if ttl is not None else ttl_seconds()))
    body = {"cid": session.company_id, "role": session.role, "uid": session.user_id, "exp": exp}
    payload = _b64(json.dumps(body, separators=(",", ":")).encode())
    return f"{payload}.{_sign(payload)}"


def verify_token(token: str, now: float | None = None) -> Session:
    try:
        payload, sig = token.split(".", 1)
        if not hmac.compare_digest(sig, _sign(payload)):
            raise InvalidToken("bad signature")
        body = json.loads(_unb64(payload))
        if int(body["exp"]) < (now if now is not None else time.time()):
            raise InvalidToken("expired")
        return Session(
            company_id=body["cid"], role=body["role"], user_id=body["uid"], expires_at=int(body["exp"])
        )
    except InvalidToken:
        raise
    except Exception as e:  # битый токен любого вида
        raise InvalidToken("malformed") from e


def token_from_request(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    return request.cookies.get(COOKIE_NAME) or None


def optional_session(request: Request) -> Session | None:
    token = token_from_request(request)
    if not token:
        return None
    try:
        return verify_token(token)
    except InvalidToken:
        return None


def current_session(request: Request) -> Session:
    """Зависимость FastAPI: сессия из cookie или Bearer, иначе 401."""
    token = token_from_request(request)
    if not token:
        raise HTTPException(status_code=401, detail="Требуется вход")
    try:
        session = verify_token(token)
    except InvalidToken:
        raise HTTPException(status_code=401, detail="Сессия недействительна") from None
    request.state.session = session
    return session


def demo_companies() -> list[str]:
    """Компании, в которые разрешён демо-вход: env DEMO_COMPANIES или компании из фикстуры rate_14."""
    env = os.environ.get("DEMO_COMPANIES")
    if env:
        return [c.strip() for c in env.split(",") if c.strip()]
    from backend.fixtures import load_bundle

    seen: dict[str, None] = {}
    for card in load_bundle("rate_14")["insight_card"]:
        seen[card.company_id] = None  # type: ignore[attr-defined]
    return list(seen)
