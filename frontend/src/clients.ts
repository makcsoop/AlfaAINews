// Клиенты для переключателя. Основной источник — витрина API (GET /demo/companies, config/demo_companies.yaml):
// реальные компании кейса и демо-архетипы. DEMO_CLIENTS — запасной список (моки, API недоступен).
import type { OwnCompanyForm, OwnSession, ShowcaseCompany, UserRole } from "./types";

export interface DemoClient {
  companyId: string;
  title: string;
  subtitle: string;
  okvedName?: string;
  kind?: "case" | "demo" | "own";
  defaultRole: UserRole;
  ownerKey?: string; // своя компания по анкете: ключ повторного входа
}

export const DEMO_CLIENTS: DemoClient[] = [
  {
    companyId: "U_DEMO_BORROWER",
    title: "Оптовик с кредитами и ВЭД",
    subtitle: "ОКВЭД 46.69 · Москва · кредиты, лизинг · ВЭД с Китаем",
    defaultRole: "cfo",
    kind: "demo",
  },
  {
    companyId: "U_DEMO_DEPOSITOR",
    title: "Экспортёр с депозитами",
    subtitle: "ОКВЭД 24.10 · Екатеринбург · депозиты, без кредитов · ВЭД с Турцией",
    defaultRole: "treasurer",
    kind: "demo",
  },
];

const PRODUCT_LABELS: [keyof ShowcaseCompany["products"], string][] = [
  ["loans", "кредиты"],
  ["leasing", "лизинг"],
  ["deposits", "депозиты"],
  ["guarantees", "гарантии"],
  ["factoring", "факторинг"],
];

export function fromShowcase(list: ShowcaseCompany[]): DemoClient[] {
  return list.map((c) => {
    const products = PRODUCT_LABELS.filter(([k]) => c.products[k]).map(([, v]) => v);
    const fea = c.products.fea ? (c.fea_countries.length ? `ВЭД: ${c.fea_countries.join(", ")}` : "ВЭД") : "";
    const parts = [c.okved_code && `ОКВЭД ${c.okved_code}`, c.region, products.join(", "), fea].filter(Boolean);
    return {
      companyId: c.company_id,
      title: c.kind === "demo" ? `${c.title} (демо)` : c.title,
      subtitle: parts.join(" · ") || c.description,
      okvedName: c.okved_name || undefined,
      kind: c.kind,
      defaultRole: c.default_role,
    };
  });
}

// Пункт переключателя «+ Своя компания…»: открывает анкету
export const OWN_NEW = "__own_new__";

// Свои компании по анкете живут в этом браузере: id и ключ владельца нужны для повторного входа.
// Хранилище может быть недоступно — тогда компания живёт до перезагрузки.
const OWN_KEY = "own:companies";

export function readOwn(): DemoClient[] {
  try {
    const list = JSON.parse(localStorage.getItem(OWN_KEY) ?? "[]") as DemoClient[];
    return Array.isArray(list) ? list.filter((c) => c.companyId?.startsWith("U_OWN_") && c.ownerKey) : [];
  } catch {
    return [];
  }
}

export function saveOwn(list: DemoClient[]) {
  try {
    localStorage.setItem(OWN_KEY, JSON.stringify(list));
  } catch {
    /* без хранилища — до перезагрузки */
  }
}

export function ownClient(s: OwnSession, form: OwnCompanyForm, sectorName?: string): DemoClient {
  const products = PRODUCT_LABELS.filter(([k]) => k !== "fea" && form.products[k as keyof OwnCompanyForm["products"]]).map(
    ([, v]) => v,
  );
  const fea = form.fea ? (form.fea_countries.length ? `ВЭД: ${form.fea_countries.join(", ")}` : "ВЭД") : "";
  const parts = [`ОКВЭД ${form.okved_code}`, form.region, products.join(", "), fea].filter(Boolean);
  return {
    companyId: s.company_id,
    title: `${s.title} (своя)`,
    subtitle: parts.join(" · "),
    okvedName: sectorName,
    kind: "own",
    defaultRole: s.role,
    ownerKey: s.owner_key,
  };
}
