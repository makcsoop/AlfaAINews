// Демо-клиенты для переключателя. Пока карточки есть только у компаний фикстуры rate_14;
// архетипы из config/archetypes.yaml (fixture_company) добавятся, когда для них появятся профили и карточки.
import type { UserRole } from "./types";

export interface DemoClient {
  companyId: string;
  title: string;
  subtitle: string;
  defaultRole: UserRole;
}

export const DEMO_CLIENTS: DemoClient[] = [
  {
    companyId: "U_DEMO_BORROWER",
    title: "Оптовик с кредитами и ВЭД",
    subtitle: "ОКВЭД 46.69 · Москва · кредиты, лизинг · ВЭД с Китаем",
    defaultRole: "cfo",
  },
  {
    companyId: "U_DEMO_DEPOSITOR",
    title: "Экспортёр с депозитами",
    subtitle: "ОКВЭД 24.10 · Екатеринбург · депозиты, без кредитов · ВЭД с Турцией",
    defaultRole: "treasurer",
  },
];
