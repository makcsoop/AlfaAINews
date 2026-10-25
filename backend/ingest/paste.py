"""«Вставить ссылку или текст» (блок В): материал пользователя → сюжет → факты. HTTP-обёртку делает Р3.

    story_id = ingest_paste(conn, url="https://…", text=None, title=None, company_id=session.company_id)

1. Ссылка — скачиваем (только http/https и только публичные адреса: никаких запросов во внутреннюю сеть
   стенда), текст — trafilatura; вставленный текст берём как есть.
2. Материал источника `user_paste`, уровень 3 (непроверенный), в `raw_items` с меткой владельца
   (`owner:<company_id>`, см. `backend.ingest.private`). Отсев словарём не применяется: пользователь прислал
   материал сам. Вставка личная: другим компаниям она не видна.
3. Сюжет: ищем общий сюжет окна 72 ч по тем же правилам, что склейка (средняя похожесть ≥ порога и общая
   сущность) — нашли, отдаём его, не меняя; иначе — новый личный сюжет из одной вставки. Подтверждением
   материал пользователя не считается, в склейку (`rebuild`) вставки не идут.
4. Факты — экстрактором; текст уходит в модель только блоком данных (`data_block`), поэтому команды
   внутри вставленного текста («игнорируй инструкции…») модель не выполняет.
"""

import hashlib
import ipaddress
import socket
import time
from collections.abc import Callable
from datetime import UTC, datetime
from urllib.parse import urljoin, urlparse

import httpx
import numpy as np
import trafilatura
from sklearn.feature_extraction.text import TfidfVectorizer
from sqlalchemy import Connection, select, update
from sqlalchemy.dialects.postgresql import insert

from backend.config import get_settings
from backend.db import models as m
from backend.extract.entities import extract_entities
from backend.extract.extractor import extract
from backend.extract.llm_port import JsonLLM
from backend.extract.run import save_facts
from backend.ingest.normalize import clean_text, clean_title, content_hash, item_id, simhash
from backend.ingest.private import PASTE_SOURCE_ID, has_paste, owner_tag
from backend.ingest.store import insert_items
from backend.schemas import RawItem, Story
from backend.stories import cluster

SOURCE_ID = PASTE_SOURCE_ID
TIER = 3
MAX_TEXT = 50_000
MAX_BYTES = 3_000_000
FETCH_DEADLINE_S = 20.0  # на всю загрузку с редиректами, а не на каждую операцию
TITLE_MAX = 200


class PasteError(ValueError):
    """Материал не принят: пустой текст, плохая ссылка, страница не скачалась."""


def resolve_public(url: str) -> tuple[str, str]:
    """Ссылка и её публичный IP: только http(s) и только публичные адреса (не во внутреннюю сеть стенда).

    Подключаемся именно к проверенному IP (fetch_page): иначе второй DNS-запрос при подключении
    мог бы вернуть внутренний адрес (DNS rebinding).
    """
    u = urlparse(url.strip())
    if u.scheme not in {"http", "https"} or not u.hostname:
        raise PasteError("нужна ссылка http(s)")
    try:
        infos = socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80))
    except OSError:
        raise PasteError("адрес не найден") from None
    except ValueError:
        raise PasteError("нужна ссылка http(s)") from None
    ips = [ipaddress.ip_address(info[4][0]) for info in infos]
    if not ips or any(not ip.is_global for ip in ips):
        raise PasteError("ссылка ведёт во внутреннюю сеть")
    return u.geturl(), str(ips[0])


def check_url(url: str) -> str:
    """Только http(s) и только публичные адреса (защита от запросов во внутреннюю сеть стенда)."""
    return resolve_public(url)[0]


def _get_pinned(client: httpx.Client, url: str, ip: str, deadline: float) -> tuple[int, str | None, bytes]:
    """GET к проверенному IP с исходными Host и SNI; тело — потоком, не больше MAX_BYTES и до дедлайна."""
    target = httpx.URL(url)
    headers = {"Host": target.netloc.decode("ascii")}
    ext = {"sni_hostname": target.host} if target.scheme == "https" else {}
    with client.stream("GET", target.copy_with(host=ip), headers=headers, extensions=ext) as r:
        if r.is_redirect:
            return r.status_code, r.headers.get("location"), b""
        if r.status_code != 200:
            return r.status_code, None, b""
        body = bytearray()
        for chunk in r.iter_bytes():
            body += chunk
            if len(body) >= MAX_BYTES or time.monotonic() > deadline:
                break
        return 200, None, bytes(body[:MAX_BYTES])


def fetch_page(url: str, transport: httpx.BaseTransport | None = None) -> tuple[str | None, str | None]:
    """(заголовок, текст) страницы. Редиректы проверяются заново: публичная ссылка не уведёт внутрь."""
    s = get_settings()
    deadline = time.monotonic() + FETCH_DEADLINE_S
    with httpx.Client(
        timeout=10, headers={"User-Agent": s.ingest_user_agent}, transport=transport
    ) as client:
        for _ in range(5):
            if time.monotonic() > deadline:
                raise PasteError("страница грузится слишком долго")
            url, ip = resolve_public(url)
            status, location, html = _get_pinned(client, url, ip, deadline)
            if location:
                url = urljoin(url, location)
                continue
            break
        else:
            raise PasteError("слишком много перенаправлений")
    if status != 200:
        raise PasteError(f"страница не открылась: HTTP {status}")
    text = trafilatura.extract(html, url=url, favor_precision=True, include_comments=False)
    meta = trafilatura.extract_metadata(html)
    return (meta.title if meta else None), text


def make_item(*, url: str | None, text: str, title: str | None, now: datetime, owner: str) -> RawItem:
    """Материал вставки компании `owner`: у каждой компании свой материал (id зависит от владельца)."""
    text = clean_text(text[:MAX_TEXT]) or ""
    if len(text) < 20:
        raise PasteError("слишком короткий текст")
    title = clean_title(title) or clean_title(text.split("\n", 1)[0]) or text.split("\n", 1)[0]
    if len(title) > TITLE_MAX:
        title = title[: TITLE_MAX - 1].rsplit(" ", 1)[0] + "…"
    native = hashlib.sha1(f"{owner}\n{url or text}".encode()).hexdigest()[:16]
    return RawItem(
        id=item_id(SOURCE_ID, native),
        source_id=SOURCE_ID,
        native_id=native,
        tier=TIER,
        url=url,
        title=title,
        text=text,
        published_at=now,
        fetched_at=now,
        content_hash=content_hash(title, text),
        simhash=simhash(f"{title}\n{text}"),
        source_tags=[owner_tag(owner)],
    )


def _attach(conn: Connection, item: RawItem, now: datetime) -> Story:
    """Общий сюжет окна 72 ч, к которому склейка присоединила бы материал, или новый личный сюжет.

    Вставка личная: в общий сюжет она не добавляется (его видят все компании), сюжет только находится,
    чтобы показать клиенту карточку уже известного события. Личные сюжеты вставок в поиске не участвуют.
    """
    since = now - cluster.WINDOW
    stories = [
        st
        for st in (
            Story.model_validate(d)
            for d in conn.execute(select(m.stories.c.data).where(m.stories.c.last_seen >= since)).scalars()
        )
        if not has_paste(st)
    ]
    keys = extract_entities(cluster._text(item)).keys()
    names = {k.split(":", 1)[1] for k in keys}
    best, best_sim = None, 0.0
    if stories:
        ids = [i for s in stories for i in s.item_ids]
        rows = {
            r.id: r
            for r in conn.execute(
                select(m.raw_items.c.id, m.raw_items.c.title, m.raw_items.c.lead, m.raw_items.c.text).where(
                    m.raw_items.c.id.in_(ids)
                )
            )
        }
        texts = {i: f"{r.title}. {r.lead or (r.text or '')[:400]}" for i, r in rows.items()}
        order = list(texts)
        vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True)
        x = vec.fit_transform([cluster._text(item), *(texts[i] for i in order)])
        sim = dict(zip(order, (x[1:] @ x[0].T).toarray().ravel(), strict=True))
        for s in stories:
            vals = [sim[i] for i in s.item_ids if i in sim]
            if not vals:
                continue
            avg = float(np.mean(vals))
            shared = bool(names & set(s.entities))
            unknown = not names or not s.entities
            ok = (avg >= cluster.SIM_THRESHOLD and shared) or (unknown and avg >= cluster.SIM_NO_ENTITY)
            if ok and avg > best_sim:
                best, best_sim = s, avg
    if best is not None:
        return best  # общий сюжет не меняем: ни материалов, ни источников, ни подтверждения
    story = cluster.to_story(cluster._Draft(items=[item], idx=[0], keys=set(keys)), {})
    conn.execute(
        insert(m.stories).values(
            id=story.id, title=story.title, event_type=story.event_type, status=story.status,
            corroboration=story.corroboration, first_seen=story.first_seen, last_seen=story.last_seen,
            version=story.version, data=story.model_dump(mode="json"),
        ).on_conflict_do_nothing()
    )
    conn.execute(update(m.raw_items).where(m.raw_items.c.id == item.id).values(story_id=story.id))
    return story


def ingest_paste(
    conn: Connection,
    *,
    url: str | None,
    text: str | None,
    title: str | None,
    company_id: str,
    llm: JsonLLM | None = None,
    fetch: Callable[[str], tuple[str | None, str | None]] = fetch_page,
    now: datetime | None = None,
) -> str:
    """Материал пользователя → story_id. Повторная вставка того же материала даёт тот же сюжет.

    `company_id` — из сессии: вставка видна только этой компании (личный сюжет) или находит общий сюжет
    того же события, не меняя его.
    """
    now = now or datetime.now(UTC)
    if url:
        url = check_url(url)
        if not text:
            page_title, text = fetch(url)
            title = title or page_title
    if not text:
        raise PasteError("нет ни текста, ни читаемой страницы")
    item = make_item(url=url, text=text, title=title, now=now, owner=company_id)
    known = conn.execute(select(m.raw_items.c.story_id).where(m.raw_items.c.id == item.id)).first()
    if known and known.story_id:
        return known.story_id
    insert_items(conn, [item])
    story = _attach(conn, item, now)
    if item.id not in story.item_ids:  # нашли общий сюжет: факты извлекаем, только если их ещё нет
        has_facts = conn.execute(
            select(m.event_facts.c.story_id).where(m.event_facts.c.story_id == story.id)
        ).first()
        if has_facts:
            return story.id
    rows = conn.execute(select(m.raw_items.c.data).where(m.raw_items.c.id.in_(story.item_ids))).scalars()
    items = [RawItem.model_validate(r) for r in rows]
    save_facts(conn, extract(story, items, llm))
    return story.id

