// Подписи для интерфейса: метки утверждений, роли, каналы влияния, признаки компании.
import type { ClaimType, Confidence, Polarity, UserRole } from "./types";

export const CLAIM_LABELS: Record<ClaimType, string> = {
  fact: "Факт",
  bank_view: "Позиция банка",
  market_view: "Мнение рынка",
  model_inference: "Вывод AI",
  bank_offer: "Предложение банка",
};

export const ROLE_LABELS: Partial<Record<UserRole, string>> = {
  cfo: "Финансовый директор",
  treasurer: "Казначей",
  chief_accountant: "Главный бухгалтер",
};

export const DEMO_ROLES: UserRole[] = ["cfo", "treasurer", "chief_accountant"];

export const POLARITY_LABELS: Record<Polarity, string> = {
  risk: "Риск",
  opportunity: "Возможность",
  neutral: "К сведению",
  mixed: "Риск и возможность",
};

export const CONFIDENCE_LABELS: Record<Confidence, string> = {
  high: "Уверенность высокая",
  medium: "Уверенность средняя",
  low: "Уверенность низкая",
};

export const STORY_STATUS_LABELS: Record<string, string> = {
  confirmed: "подтверждено",
  single: "один источник",
  rumor: "слух",
  unconfirmed: "не подтверждено",
  contradictory: "источники расходятся",
};

// Факторы — config/channels_matrix.yaml (названия для ленты, коротко)
export const CHANNEL_LABELS: Record<string, string> = {
  key_rate: "ключевая ставка",
  fx_rub: "курс рубля",
  oil_gas: "нефть и газ",
  metals: "цены на металлы",
  agro: "агросырьё",
  gold: "золото",
  inflation: "инфляция",
  consumer_demand: "потребительский спрос",
  labor_costs: "рынок труда",
  construction: "стройка и ипотека",
  trade_restrictions: "пошлины и санкции",
  logistics_costs: "логистика и платежи ВЭД",
  tax_burden: "налоги",
  regulation: "регулирование",
  public_spending: "госзаказ и господдержка",
  coal: "уголь",
  petroleum_products: "топливо",
  fertilizers_chemicals: "удобрения и химия",
  timber: "лес и древесина",
  electricity_tariffs: "тарифы на энергию",
  rail_tariffs: "тарифы РЖД",
  capital_controls: "валютный контроль",
  credit_conditions: "условия кредитования",
  counterparty_defaults: "неплатежи контрагентов",
  equity_market: "фондовый рынок",
  real_estate: "недвижимость",
  it_import_substitution: "импортозамещение ПО",
  export_demand: "внешний спрос",
  subsidized_lending: "льготные кредиты",
  import_prices: "цены импорта",
};

// Прямые совпадения скоринга: backend/ranking/score.py (direct_contributions)
const DIRECT_LABELS: Record<string, string> = {
  company: "о вашей компании",
  group: "о вашей группе",
  counterparty: "о вашем контрагенте",
  sector: "о вашей отрасли",
  region: "о вашем регионе",
};

// Поля CSV кейса → что это значит для клиента
const FEATURE_LABELS: Record<string, string> = {
  "CL_PRODUCT_LOA_MARK=1": "кредиты в банке",
  "CL_PRODUCT_LOA_MARK=0": "нет кредитов",
  "CL_PRODUCT_LEA_MARK=1": "лизинг",
  "CL_PRODUCT_DEP_MARK=1": "депозиты в банке",
  "CL_PRODUCT_GAR_MARK=1": "банковские гарантии",
  "CL_PRODUCT_ZPP_MARK=1": "зарплатный проект",
  "CL_PRODUCT_RKO_MARK=1": "расчётный счёт",
  "FACTORING_MARK=1": "факторинг",
  "FTS_FLAG=1": "внешнеэкономическая деятельность",
};

export function featureLabel(f: string): string {
  if (FEATURE_LABELS[f]) return FEATURE_LABELS[f];
  const country = f.match(/^FTS_COUNTRY_NAME=(.+)$/);
  if (country) return `ВЭД: ${country[1]}`;
  const okved = f.match(/^MAINOKVED_CODE=(.+)$/);
  if (okved) return `ОКВЭД ${okved[1]}`;
  return f;
}

export function channelLabel(c: string): string {
  // вклад через контрагентов: «counterparty:41» — покупатели или поставщики из сектора ОКВЭД 41
  const [kind, arg] = c.split(":");
  if (kind === "counterparty" && arg) return `контрагенты из ОКВЭД ${arg}`;
  if (kind === "linked_sector" && arg) return `связанная отрасль, ОКВЭД ${arg}`;
  if (kind === "direct" && arg) return DIRECT_LABELS[arg] ?? arg;
  if (c === "surprise") return "неожиданный исход";
  return CHANNEL_LABELS[c] ?? c;
}

// Ссылка на материал: позиция ЦМИ «cmi_doc#p3#c2» → «ЦМИ Альфа-Банка, с. 3»
export function sourceLabel(sourceId: string): string {
  const cmi = sourceId.match(/^cmi_[^#]+(?:#p(\d+))?/);
  if (cmi) return cmi[1] ? `ЦМИ Альфа-Банка, с. ${cmi[1]}` : "ЦМИ Альфа-Банка";
  if (sourceId.startsWith("mk_")) return "консенсус аналитиков";
  if (sourceId === "user_paste") return "ваш материал";
  return "первоисточник";
}

// Названия лент из config/sources.yaml (пока в StorySource не приходит title)
const SOURCE_NAMES: Record<string, string> = {
  user_paste: "ваш материал",
  prime: "ПРАЙМ",
  interfax: "Интерфакс",
  rbc: "РБК",
  tass: "ТАСС",
  kommersant: "Коммерсантъ",
  vedomosti: "Ведомости",
  cbr_press: "Банк России",
  cbr_events: "Банк России",
  cbr_fx: "Банк России, курсы",
  cbr_dailyinfo: "Банк России, ставки",
  moex_iss: "Московская биржа",
  agroinvestor: "Агроинвестор",
  nalog_rss: "ФНС России",
  customs_rss: "ФТС России",
  metalinfo: "Металлоснабжение и сбыт",
  comnews: "ComNews",
  frank_media: "Frank Media",
  smartlab: "Смартлаб",
  tg_centralbank_russia: "Банк России",
  tg_minfin: "Минфин России",
  tg_government_rus: "Правительство России",
  tg_minec_russia: "Минэкономразвития",
  tg_minenergo_official: "Минэнерго",
  tg_mintrans_russia: "Минтранс",
  tg_mcx_ru: "Минсельхоз",
  tg_mintrudrf: "Минтруд",
  tg_fasrussia: "ФАС России",
  tg_minpromtorg_ru: "Минпромторг",
  tg_duma_gov_ru: "Госдума",
  tg_rosstat_official: "Росстат",
  tg_spimexofficial: "СПбМТСБ",
  tg_kommersant: "Коммерсантъ",
  tg_vedomosti: "Ведомости",
  tg_agroinvestor: "Агроинвестор",
  tg_neftegazchannel: "Neftegaz.RU",
  tg_russianmacro: "MMI",
  tg_interfaxonline: "Интерфакс",
  tg_tass_agency: "ТАСС",
  tg_prime1: "ПРАЙМ",
  tg_rbc_news: "РБК",
};

export function sourceName(sourceId: string, title?: string | null): string {
  if (SOURCE_NAMES[sourceId]) return SOURCE_NAMES[sourceId];
  if (sourceId.startsWith("tg_")) return `@${sourceId.slice(3)}`;
  return title || sourceId;
}

export function formatNum(v: number): string {
  return v.toLocaleString("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
