"""Финальная проверка карточки кодом: метки, ссылки, числа и даты, голос банка, шесть вопросов ТЗ.

Модель пишет текст, но верит ему только код: каждое число и каждая дата в утверждении должны
найтись в цитате или в данных элементов досье, на которые утверждение ссылается (для вывода AI —
ещё и в опорных утверждениях и признаках компании). «14,0» = «14» = «14%».
"""

import json
import re
from collections.abc import Iterable, Sequence
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, Field

from newsai.agents.dossier import Dossier, DossierItem
from newsai.cards.build import _BANK_VOICE, MARKET_SOURCE_PREFIX
from newsai.cards.features import is_sector_feature
from newsai.cards.offers import find_offer
from newsai.schemas import AnalystReport, Claim, CompanyProfile, InsightCard
from newsai.schemas.common import BANK_SOURCE_PREFIX


class CardFailure(BaseModel):
    code: str
    claim_id: str | None = None
    detail: str = ""

    def __str__(self) -> str:
        where = f" [{self.claim_id}]" if self.claim_id else ""
        return f"{self.code}{where}: {self.detail}"


class CardCheck(BaseModel):
    ok: bool
    failures: list[CardFailure] = Field(default_factory=list)

    def messages(self) -> list[str]:
        return [str(f) for f in self.failures]


# ---------- числа и даты ----------

_MONTHS = {
    "янв": 1, "фев": 2, "мар": 3, "апр": 4, "ма": 5, "июн": 6,
    "июл": 7, "авг": 8, "сен": 9, "окт": 10, "ноя": 11, "дек": 12,
}  # fmt: skip
_MONTH_RE = r"(янв|фев|мар|апр|мая|мае|май|июн|июл|авг|сен|окт|ноя|дек)[а-яё]*"
_DATE_RU = re.compile(rf"\b(\d{{1,2}})\s+{_MONTH_RE}(?:\s+(\d{{4}}))?", re.IGNORECASE)
_DATE_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DATE_DOT = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")
_NUMBER = re.compile(r"(?<![\w.,])(?:\d{1,3}(?:[   ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)")

DateKey = tuple[int, int, int | None]  # день, месяц, год (None — год не указан)


def _month(word: str) -> int:
    w = word.lower()
    return _MONTHS["ма"] if w.startswith(("мая", "мае", "май")) else _MONTHS[w[:3]]


def _norm_number(raw: str) -> str | None:
    s = re.sub(r"[\s  ]", "", raw).replace(",", ".")
    try:
        d = Decimal(s).normalize()
    except InvalidOperation:
        return None
    return format(d, "f") if d != d.to_integral() else str(int(d))


def extract_dates(text: str) -> tuple[set[DateKey], str]:
    """Даты из текста и сам текст с вырезанными датами (чтобы день и год не считались числами)."""
    found: set[DateKey] = set()

    def ru(m: re.Match[str]) -> str:
        found.add((int(m.group(1)), _month(m.group(2)), int(m.group(3)) if m.group(3) else None))
        return " "

    def iso(m: re.Match[str]) -> str:
        found.add((int(m.group(3)), int(m.group(2)), int(m.group(1))))
        return " "

    def dot(m: re.Match[str]) -> str:
        found.add((int(m.group(1)), int(m.group(2)), int(m.group(3))))
        return " "

    rest = _DATE_ISO.sub(iso, text)
    rest = _DATE_DOT.sub(dot, rest)
    rest = _DATE_RU.sub(ru, rest)
    return found, rest


def extract_numbers(text: str) -> set[str]:
    return {n for m in _NUMBER.finditer(text) if (n := _norm_number(m.group(0))) is not None}


def _walk(value: Any) -> Iterable[Any]:
    if isinstance(value, dict):
        for v in value.values():
            yield from _walk(v)
    elif isinstance(value, list | tuple | set):
        for v in value:
            yield from _walk(v)
    else:
        yield value


class _Pool:
    """Числа и даты, разрешённые для утверждения."""

    def __init__(self) -> None:
        self.numbers: set[str] = set()
        self.dates: set[DateKey] = set()

    def add_text(self, text: str | None) -> None:
        if not text:
            return
        dates, rest = extract_dates(text)
        self.dates |= dates
        self.numbers |= extract_numbers(text)  # годы и дни дат тоже считаются числами источника
        self.numbers |= extract_numbers(rest)

    def add_datetime(self, value: date | datetime | None) -> None:
        if value is not None:
            self.dates.add((value.day, value.month, value.year))

    def add_value(self, value: Any) -> None:
        for v in _walk(value):
            if isinstance(v, bool) or v is None:
                continue
            if isinstance(v, datetime | date):
                self.add_datetime(v)
            elif isinstance(v, int | float):
                if (n := _norm_number(repr(v))) is not None:
                    self.numbers.add(n)
            elif isinstance(v, str):
                self.add_text(v)
            else:
                self.add_text(json.dumps(v, ensure_ascii=False, default=str))

    def add_item(self, item: DossierItem) -> None:
        self.add_text(item.title)
        self.add_text(item.text)
        self.add_value(item.data)
        self.add_datetime(item.published_at)

    def has_date(self, key: DateKey) -> bool:
        d, m, y = key
        return any(d == sd and m == sm and (y is None or y == sy) for sd, sm, sy in self.dates)


def _items_for(dossier: Dossier, source_id: str) -> list[DossierItem]:
    """Элементы досье, на которые указывает evidence: по id или по id документа в data.source_id."""
    return [i for i in dossier.items if i.id == source_id or i.data.get("source_id") == source_id]


def _claim_pool(
    claim: Claim, by_id: dict[str, Claim], dossier: Dossier, _seen: set[str] | None = None
) -> _Pool:
    seen = _seen if _seen is not None else set()
    seen.add(claim.claim_id)
    pool = _Pool()
    for ev in claim.evidence:
        pool.add_text(ev.quote)
        pool.add_datetime(ev.doc_date)
        for item in _items_for(dossier, ev.source_id):
            pool.add_item(item)
    if claim.type == "model_inference" or claim.type == "bank_offer":
        for key, refs in claim.based_on.items():
            if key == "client_features":
                for f in refs:
                    pool.add_text(f)
                continue
            for ref in refs:
                sup = by_id.get(ref)
                if sup is None or ref in seen:
                    continue
                pool.add_text(sup.text)
                sub = _claim_pool(sup, by_id, dossier, seen)
                pool.numbers |= sub.numbers
                pool.dates |= sub.dates
    return pool


# ---------- проверка ----------


def _has_markets_or_bank(dossier: Dossier) -> bool:
    for i in dossier.items:
        if i.kind == "bank_position" and i.id.startswith(BANK_SOURCE_PREFIX):
            return True
        if (
            i.kind == "market_knowledge"
            and i.id.startswith(MARKET_SOURCE_PREFIX)
            and not (i.title or i.text).upper().startswith("ЗАГЛУШКА")
        ):
            return True
    return False


def verify_card(
    card: InsightCard,
    dossier: Dossier,
    *,
    profile_features: Sequence[str],
    profile: CompanyProfile | None = None,
    analyses: Sequence[AnalystReport] = (),
) -> CardCheck:
    """Проверка карточки; `profile` и `analyses` нужны только для проверки триггера предложения банка."""
    fails: list[CardFailure] = []

    def fail(code: str, detail: str, claim_id: str | None = None) -> None:
        fails.append(CardFailure(code=code, claim_id=claim_id, detail=detail))

    by_id = {c.claim_id: c for c in card.claims}
    feats = {f.strip().casefold() for f in profile_features}

    for c in card.claims:
        # (a) метка, обоснование и существование ссылок
        if not c.evidence and not any(c.based_on.values()):
            fail("no_support", "утверждение без источника и без based_on", c.claim_id)
        for ev in c.evidence:
            if not _items_for(dossier, ev.source_id):
                fail("unknown_source", f"в досье нет {ev.source_id!r}", c.claim_id)
        for key, refs in c.based_on.items():
            for ref in refs:
                if key == "client_features":
                    if feats and ref.strip().casefold() not in feats:
                        fail("unknown_feature", f"признака компании {ref!r} нет в профиле", c.claim_id)
                elif ref not in by_id:
                    fail("unknown_claim", f"based_on[{key}]: нет утверждения {ref!r}", c.claim_id)

        # (b) метка и источник: мнение банка и рынка — только с собственными доказательствами
        if c.type == "bank_view":
            if not c.evidence:
                fail("bank_view_source", "позиция банка без evidence на материал ЦМИ", c.claim_id)
            for ev in c.evidence:
                item = dossier.get(ev.source_id)
                if not ev.source_id.startswith(BANK_SOURCE_PREFIX) or (item and item.kind != "bank_position"):
                    fail("bank_view_source", f"позиция банка ссылается на {ev.source_id!r}", c.claim_id)
        elif c.type == "market_view":
            if not c.evidence:
                fail("market_view_source", "мнение рынка без evidence на знание рынка", c.claim_id)
            for ev in c.evidence:
                item = dossier.get(ev.source_id)
                if (
                    not ev.source_id.startswith(MARKET_SOURCE_PREFIX)
                    or item is None
                    or item.kind != "market_knowledge"
                ):
                    fail("market_view_source", f"мнение рынка ссылается на {ev.source_id!r}", c.claim_id)
        elif c.type == "fact":
            for ev in c.evidence:
                if ev.source_id.startswith((BANK_SOURCE_PREFIX, MARKET_SOURCE_PREFIX)):
                    fail("fact_opinion_source", f"факт ссылается на мнение {ev.source_id!r}", c.claim_id)

        # (c) числа и даты (и в тексте предложения банка тоже)
        pool = _claim_pool(c, by_id, dossier)
        dates, rest = extract_dates(c.text)
        for key in sorted(dates, key=str):
            if not pool.has_date(key):
                d, m, y = key
                fail("date_not_in_source", f"дата {d:02d}.{m:02d}{f'.{y}' if y else ''}", c.claim_id)
        for n in sorted(extract_numbers(rest)):
            if n not in pool.numbers:
                fail("number_not_in_source", f"число {n} не найдено в цитатах и данных", c.claim_id)

        # (d) голос банка вне bank_view
        if c.type != "bank_view" and _BANK_VOICE.search(c.text):
            fail("bank_voice", f"позиция банка вне раздела «Банк и рынок»: {c.text[:80]!r}", c.claim_id)

        # (g) реклама
        if c.type == "bank_offer" and not c.is_advertising:
            fail("offer_not_marked", "предложение банка без пометки рекламы", c.claim_id)

    for n, a in enumerate(card.actions):
        if _BANK_VOICE.search(a.text):
            fail("bank_voice", f"actions[{n}]: {a.text[:80]!r}")
        if a.type == "bank_product" and not a.is_advertising:
            fail("offer_not_marked", f"actions[{n}]: продукт банка без пометки рекламы")
    for n, text in enumerate(card.what_would_change):
        if _BANK_VOICE.search(text):
            fail("bank_voice", f"what_would_change[{n}]: {text[:80]!r}")

    # (e) шесть вопросов
    for name in ("what_happened", "why_it_matters", "consequences", "actions", "what_would_change"):
        if not getattr(card, name):
            fail("empty_section", f"раздел {name} пуст")
    if not card.bank_and_market and _has_markets_or_bank(dossier):
        fail("empty_section", "раздел bank_and_market пуст, хотя в досье есть позиции ЦМИ или рынка")

    # (f) почему важно — не меньше двух признаков компании кроме отрасли
    own = {
        f.strip().casefold()
        for cid in card.why_it_matters
        if cid in by_id
        for f in by_id[cid].based_on.get("client_features", [])
        if not is_sector_feature(f)
    }
    available = [f for f in profile_features if not is_sector_feature(f)]
    if len(own) < min(2, len(available)):
        fail("why_features", f"why_it_matters опирается на {len(own)} признаков компании, нужно ≥ 2")

    # (g) предложение банка — только по триггеру
    offers = [c for c in card.claims if c.type == "bank_offer"]
    if len(offers) > 1:
        fail("offer_many", "в карточке больше одного предложения банка")
    if offers:
        trigger = (
            None
            if profile is None  # без признаков компании триггер не сверить: предложение не подтверждено
            else find_offer(
                profile=profile, relevance=card.relevance, analyses=analyses, story_status=card.event.status
            )
        )
        if trigger is None or trigger.text != offers[0].text:
            fail("offer_no_trigger", "предложение банка без сработавшего триггера", offers[0].claim_id)

    return CardCheck(ok=not fails, failures=fails)
