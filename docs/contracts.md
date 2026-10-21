# Контракты данных

Все модули обмениваются Pydantic-моделями из `newsai/schemas/`. Меняет только Р1, вместе с фикстурами.

> В коде имена отличаются: `Analysis` → `AnalystReport`, `Card` → `InsightCard` (+ `Claim`), `Counterparties` → `CounterpartyGraph`, `ClientProfile` → `CompanyProfile`, журнал → `RunTrace`/`LlmCall`. Фикстура — `fixtures/rate_14/bundle.json` (`newsai.fixtures.load_bundle()`).

| Объект | Ключ | Главные поля | Пишет |
|---|---|---|---|
| `RawItem` | source + native_id | url, published_at, fetched_at, text, simhash, tier, edits[] | Р1 |
| `Story` | story_id + version | items[], first_seen, status (rumor/confirmed), corroboration, event_type | Р1 |
| `EventFacts` | story_id + version | fields[] {name, value, quote, item_id}, numbers[], entities[] | Р1 |
| `BankPosition` | position_id | thesis, forecast {metric, value, horizon}, channel, report, page, quote, valid_until | Р1 |
| `MarketKnowledge` | claim_id | type (mechanism/expectation/forecast/case), event_type, sector, channel, direction, horizon, quote, channel_id, published_at, origin | Р1 |
| `Counterparties` | company_id | top[] {okved, share, role: supplier/buyer}, hhi | Р1 |
| `ClientProfile` | company_id | okved, region, products, ved {flag, country}, sensitivities {channel: {sign, strength, why}}, revenue_rank, completeness | Р2 |
| `Analysis` | story_id × sector × version | drivers {channel: {direction, strength, horizon, refs}}, claims[], agreement, trace_id | Р3 |
| `Card` | story_id × company_id × role | blocks[] {kind, text, refs}, score, contributions[], confidence {level, reasons}, status | Р3 (текст), Р2 (скор) |

Направление фактора: `up | down | flat`; сила: `low | medium | high`; горизонт: `days | quarter | year`.

## Пример карточки (fixtures/rate_14/bundle.json (insight_card))
```json
{
  "card_id": "st_4812:c_00731:cfo",
  "score": 0.82,
  "confidence": {"level": "high", "reasons": ["первичный источник ЦБ", "банк того же знака", "прямой канал через кредит"]},
  "status": "published",
  "contributions": [
    {"feature": "ставка ↑ × кредит (−)", "value": 0.41, "ref": "profile:c_00731:loa"},
    {"feature": "сюрприз к консенсусу рынка", "value": 0.22, "ref": "mk_cons_2026-09-11"}
  ],
  "blocks": [
    {"kind": "fact", "text": "ЦБ сохранил ключевую ставку 14%", "refs": ["item:cbr_press_2026-09-11"]},
    {"kind": "bank_view", "text": "ЦМИ: риторика стала жестче", "refs": ["bp_017"]},
    {"kind": "market_view", "text": "Рынок ждал снижения", "refs": ["mk_cons_2026-09-11"]},
    {"kind": "model_inference", "text": "Обслуживание вашего кредита в ближайший квартал не подешевеет", "refs": ["profile:c_00731:loa", "analysis:st_4812:G46"]},
    {"kind": "bank_offer", "text": "Фиксация ставки по новым траншам", "trigger": "loa+rate_up"}
  ],
  "trace_id": "tr_9f2c"
}
```
