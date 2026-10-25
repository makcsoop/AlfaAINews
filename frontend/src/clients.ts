// Клиенты для переключателя. Основной источник — витрина API (GET /demo/companies, config/demo_companies.yaml):
// реальные компании кейса и демо-архетипы. DEMO_CLIENTS — запасной список (моки, API недоступен).
import type { ShowcaseCompany, UserRole } from "./types";

export interface DemoClient {
  companyId: string;
  title: string;
  subtitle: string;
  okvedName?: string;
  kind?: "case" | "demo";
  defaultRole: UserRole;
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
