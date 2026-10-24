"""Детерминированная часть экстрактора: тип события, статус, числа, направление факторов.

Всё, что здесь найдено, привязано к дословному фрагменту текста. LLM-слой дополняет и уточняет,
а при исчерпании бюджета эта разметка остаётся единственной.
"""

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from backend.schemas import ChannelImpact, NumberFact

EVENT_TYPES = Path(__file__).resolve().parents[2] / "config" / "event_types.yaml"

_SENT = re.compile(r"(?<=[.!?…])\s+(?=[А-ЯЁA-Z«\"])")
INTENT = re.compile(
    r"\b(планиру\w*|намерен\w*|собира\w*|рассматрива\w*|предлага\w*|может|могут|готов\w+ к|допуска\w+)\b",
    re.IGNORECASE,
)
RUMOR = re.compile(
    r"по (данным|информации|словам) (источник|собеседник)|источники? (в|из|знаком|близк)|якобы|по слухам",
    re.IGNORECASE,
)
FORECAST = re.compile(r"\b(прогноз\w*|ожида\w+|ждут|ожидани\w+)\b", re.IGNORECASE)
# типы-меры, которые отрицание отменяет: «не ввели санкции» — не санкции
NEGATABLE = {"sanctions", "secondary_sanctions", "sanctions_relief"}
# типы-меры: от модели принимаются, только если мера есть в тексте (живой прогон 24.10: «ВСУ атаковали Орёл»
# модель назвала supply_disruption, анонс заседания ЦБ — rate_decision)
MEASURE_TYPES = NEGATABLE | {
    "supply_disruption", "tariff_duty", "export_duty", "import_duty", "export_ban_quota", "rate_decision",
}
# отрицание и условие: «не ввели», «нужно, чтобы сняли», «если продлят»
_NEG = re.compile(r"\b(не|чтобы|если|когда|пока)(\s+\w+){0,2}\s*$", re.IGNORECASE)
_EXEMPT = re.compile(r"\bисключени\w*\b|\bиз-под санкци\w*", re.IGNORECASE)
# ставка иностранного регулятора — не решение Банка России: «Центробанк Турции снизил ставку», «ФРС сохранила»
MONETARY = {"rate_decision", "monetary_signal"}
# анонс заседания: «заседание впереди», «чего ждать от ЦБ» — без глагола решения это ещё не rate_decision
_ANNOUNCE = re.compile(
    r"\b(впереди|предстоящ\w*|накануне|в преддверии|перед заседанием|ожида\w*|ждут|ждём|ждем|прогноз\w*"
    r"|консенсус\w*|сделает|примет|эфир\w*)\b|\bдо (решени|заседани)\w*|\bк заседани\w*",
    re.IGNORECASE,
)
_DECIDED = re.compile(
    r"\b(сохранил\w*|оставил\w*|осталась|снизил\w*|понизил\w*|повысил\w*|принял\w* решение)\b", re.IGNORECASE
)
_FOREIGN_CB = re.compile(
    r"\b((?i:центробанк\w*|центральн\w+ банк\w*|нацбанк\w*|национальн\w+ банк\w*)|ЦБ)\s+"
    r"(?!России\b|РФ\b|Российской\b)[А-ЯЁ][а-яё]+(и|а|ы|ии)\b"
    r"|\b(ФРС|ЕЦБ|Банк Англии|Банк Японии|Народный банк Китая|Резервный банк|Федеральн\w+ резервн\w+)",
)

# Число + единица. Порядок важен: сначала длинные единицы.
_UNITS = [
    (r"трлн\.?\s*(руб|рублей|₽)", "трлн руб"),
    (r"млрд\.?\s*(руб|рублей|₽)", "млрд руб"),
    (r"млн\.?\s*(руб|рублей|₽)", "млн руб"),
    (r"млрд\.?\s*(долл\w*|\$)", "млрд USD"),
    (r"млн\.?\s*(долл\w*|\$)", "млн USD"),
    (r"(руб|рублей|₽)", "руб"),
    (r"(долл\w*|\$)", "USD"),
    (r"(евро|€)", "EUR"),
    (r"(%|процент\w*|п\.п\.)", "%"),
    (r"(б\.п\.|базисн\w+ пункт\w*)", "б.п."),
    (r"(трлн)", "трлн"),
    (r"(млрд)", "млрд"),
    (r"(млн)", "млн"),
    (r"(тыс\.?\s*т|млн\s*т|т\b|тонн\w*)", "т"),
]
_NUM_UNIT = re.compile(
    r"(?<![\w.,])(?P<num>\d+(?:[  ]\d{3})*(?:[.,]\d+)?)\s*(?P<unit>" + "|".join(u for u, _ in _UNITS) + r")",
    re.IGNORECASE,
)
_UNIT_RX = [(re.compile(u, re.IGNORECASE), name) for u, name in _UNITS]
# валюта перед числом: «$111,05», «€1 050»
_PRE_UNIT = re.compile(r"(?P<cur>[$€])\s?(?P<num>\d+(?:[  ]\d{3})*(?:[.,]\d+)?)")
_PRE_NAMES = {"$": "USD", "€": "EUR"}
_UP = re.compile(r"(вырос|повысил|увелич|подорожал|ускорил|подня)", re.IGNORECASE)
_DOWN = re.compile(r"(снизил|сниж|упал|уменьш|подешевел|замедлил|сократил)", re.IGNORECASE)


@dataclass
class EventTypeRule:
    name: str
    channels: list[str]
    patterns: list[re.Pattern]


@lru_cache
def _config():
    cfg = yaml.safe_load(EVENT_TYPES.read_text(encoding="utf-8"))
    types = [
        EventTypeRule(
            name, et.get("channels", []), [re.compile(p, re.IGNORECASE) for p in et.get("patterns", [])]
        )
        for name, et in cfg["event_types"].items()
    ]
    hints = {
        ch: {k: re.compile(v, re.IGNORECASE) for k, v in h.items()}
        for ch, h in cfg.get("direction_hints", {}).items()
    }
    return types, hints


def sentences(text: str) -> list[str]:
    """Предложения; перенос строки — тоже граница (заголовок без точки не склеивается с текстом)."""
    return [s.strip() for line in text.splitlines() for s in _SENT.split(line) if s.strip()]


DICTIONARIES = EVENT_TYPES.parent / "dictionaries.yaml"
# макрособытия касаются всех отраслей — отрасль у них не ставится
MACRO_TYPES = {
    "rate_decision", "monetary_signal", "inflation_data", "fx_move", "fx_control", "macro_data",
    "budget_policy", "ofz_auction", "capital_control_change", "credit_regulation", "minimum_wage",
    "labor_policy", "foreign_demand_data", "other",
}


@lru_cache
def _sector_rx() -> dict[str, re.Pattern]:
    cfg = yaml.safe_load(DICTIONARIES.read_text(encoding="utf-8"))
    return {code: re.compile(rx, re.IGNORECASE) for code, rx in (cfg.get("sectors") or {}).items()}


def sectors(etype: str, text: str) -> list[tuple[str, str]]:
    """Отрасли отраслевого события по заголовку и лиду: [(ОКВЭД, фрагмент текста)]."""
    if etype in MACRO_TYPES:
        return []
    out = []
    for code, rx in _sector_rx().items():
        m = rx.search(text)
        if m:
            out.append((code, m.group(0)))
    return out


def _negated(part: str, start: int) -> bool:
    """«ЕС не ввёл санкции», «не стали продлевать»: перед совпадением отрицание в пределах двух слов."""
    return bool(_NEG.search(part[max(0, start - 30) : start]))


def has_measure(etype: str, text: str) -> bool:
    """Есть ли в тексте мера этого типа (санкции: ввели / продлили / сняли…) — проверка типа от модели."""
    types, _ = _config()
    et = next((t for t in types if t.name == etype), None)
    if et is None or not et.patterns:
        return True
    for part in sentences(text):
        for rx in et.patterns:
            m = rx.search(part)
            if not m or (etype == "sanctions" and _EXEMPT.search(part)):
                continue
            if etype in MONETARY and _FOREIGN_CB.search(part):
                continue
            if (
                etype == "rate_decision"
                and not _DECIDED.search(part)
                and (_ANNOUNCE.search(part) or m.group(0).lower().startswith("решени"))
            ):
                continue
            if not (etype in NEGATABLE and _negated(part, m.start())):
                return True
    return False


def event_type(title: str, lead: str | None) -> tuple[str, str | None]:
    """Тип события и фрагмент, на котором сработал паттерн. Заголовок важнее лида."""
    types, _ = _config()
    foreign_cb = bool(_FOREIGN_CB.search(title))  # чужой регулятор в заголовке — и лид про его политику
    for part in (title, lead or ""):
        for et in types:
            for rx in et.patterns:
                m = rx.search(part)
                if m and et.name == "sanctions" and _EXEMPT.search(part):
                    continue  # исключение из пакета, вывод из-под санкций — не новая мера
                if m and et.name in MONETARY and (foreign_cb or _FOREIGN_CB.search(part)):
                    continue  # ставка чужого регулятора
                if m and et.name == "rate_decision" and not _DECIDED.search(part):
                    if _ANNOUNCE.search(part):
                        return "monetary_signal", part  # анонс заседания и ожидания — ещё не решение
                    if m.group(0).lower().startswith("решени"):
                        continue  # «фактор для принятия решения по ключевой ставке» — не само решение
                if m and not (et.name in NEGATABLE and _negated(part, m.start())):
                    return et.name, part
    return "other", None


def status(text: str) -> tuple[str, bool, bool]:
    """(status, is_intent, is_rumor) по первым предложениям: что произошло, а не что когда-то было."""
    head = " ".join(sentences(text)[:2])
    rumor = bool(RUMOR.search(head))
    intent = bool(INTENT.search(head))
    if rumor:
        return "rumor", intent, True
    if intent:
        return "planned", True, False
    if FORECAST.search(head):
        return "forecast", False, False
    return "fact", False, False


def numbers(text: str, limit: int = 12) -> list[NumberFact]:
    out: list[NumberFact] = []
    seen = set()
    for sent in sentences(text):
        found = [(m, None) for m in _NUM_UNIT.finditer(sent)] + [
            (m, _PRE_NAMES[m.group("cur")]) for m in _PRE_UNIT.finditer(sent)
        ]
        for m, pre_unit in sorted(found, key=lambda x: x[0].start()):
            raw = m.group("num").replace(" ", "").replace(" ", "").replace(",", ".")
            try:
                value = float(raw)
            except ValueError:
                continue
            unit = pre_unit or next(name for rx, name in _UNIT_RX if rx.fullmatch(m.group("unit").strip()))
            key = (value, unit)
            if key in seen:
                continue
            seen.add(key)
            window = sent[max(0, m.start() - 60) : m.end() + 20]
            direction = "up" if _UP.search(window) else "down" if _DOWN.search(window) else None
            out.append(
                NumberFact(value=value, unit=unit, direction=direction, quote=sent[:400], verified=True)
            )
            if len(out) >= limit:
                return out
    return out


def channel_directions(text: str, channels: list[str]) -> list[ChannelImpact]:
    """Направление факторов по подсказкам: ключ фактора и слово направления в одном предложении.
    Сила по правилам — 0,3 (низкая): уточняет LLM-разбор."""
    _, hints = _config()
    out = []
    for ch in channels:
        h = hints.get(ch)
        if not h:
            continue
        for sent in sentences(text)[:6]:
            if not h["key"].search(sent):
                continue
            direction = next((d for d in ("up", "down", "flat") if d in h and h[d].search(sent)), None)
            if direction:
                out.append(
                    ChannelImpact(
                        channel=ch, direction=direction, strength=0.3, rationale="правила: " + sent[:200]
                    )
                )
                break
    return out
