// «Своя компания»: анкета вместо данных кейса → POST /session/own → лента строится как у демо-компаний.
// Только то, что влияет на ленту: отрасль, регион, продукты банка, ВЭД, отрасли покупателей и поставщиков.
import { useEffect, useState } from "react";
import type { Api } from "../api";
import { errorText } from "../api";
import type { DemoClient } from "../clients";
import { ownClient } from "../clients";
import { DEMO_ROLES, ROLE_LABELS } from "../labels";
import type { OkvedSector, OwnCompanyForm, UserRole } from "../types";
import { IconAlert } from "./icons";

const PRODUCTS: [keyof OwnCompanyForm["products"], string][] = [
  ["loans", "Кредиты"],
  ["leasing", "Лизинг"],
  ["deposits", "Депозиты"],
  ["guarantees", "Гарантии"],
  ["factoring", "Факторинг"],
  ["payroll", "Зарплатный проект"],
];

type Share = { code: string; pct: string };
const EMPTY_SHARES: Share[] = [
  { code: "", pct: "" },
  { code: "", pct: "" },
  { code: "", pct: "" },
];

function toShares(rows: Share[]): Record<string, number> {
  const out: Record<string, number> = {};
  for (const r of rows) {
    const pct = Math.round(Number(r.pct));
    if (r.code && pct > 0) out[r.code] = (out[r.code] ?? 0) + pct;
  }
  return out;
}

function SharesInput({ label, hint, rows, sectors, onChange }: {
  label: string;
  hint: string;
  rows: Share[];
  sectors: OkvedSector[];
  onChange: (rows: Share[]) => void;
}) {
  const set = (i: number, patch: Partial<Share>) => onChange(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  return (
    <fieldset className="own-shares">
      <legend>{label}</legend>
      <p className="own-hint">{hint}</p>
      {rows.map((r, i) => (
        <div className="own-share" key={i}>
          <select aria-label={`${label}: отрасль ${i + 1}`} value={r.code} onChange={(e) => set(i, { code: e.target.value })}>
            <option value="">— отрасль —</option>
            {sectors.map((s) => (
              <option key={s.code} value={s.code}>
                {s.name}
              </option>
            ))}
          </select>
          <input
            aria-label={`${label}: доля ${i + 1}, %`}
            inputMode="numeric"
            placeholder="%"
            value={r.pct}
            onChange={(e) => set(i, { pct: e.target.value.replace(/[^0-9]/g, "").slice(0, 3) })}
          />
        </div>
      ))}
    </fieldset>
  );
}

export function OwnCompany({ api, onCreated, onCancel }: {
  api: Api;
  onCreated: (client: DemoClient, role: UserRole) => void;
  onCancel: () => void;
}) {
  const [sectors, setSectors] = useState<OkvedSector[]>([]);
  const [title, setTitle] = useState("");
  const [sector, setSector] = useState("");
  const [exact, setExact] = useState("");
  const [region, setRegion] = useState("");
  const [products, setProducts] = useState<OwnCompanyForm["products"]>({
    loans: false, leasing: false, deposits: false, guarantees: false, factoring: false, payroll: false,
  });
  const [fea, setFea] = useState(false);
  const [countries, setCountries] = useState("");
  const [buyers, setBuyers] = useState<Share[]>(EMPTY_SHARES);
  const [suppliers, setSuppliers] = useState<Share[]>(EMPTY_SHARES);
  const [role, setRole] = useState<UserRole>("cfo");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.ownSectors().then(setSectors, (e) => setError(errorText(e)));
  }, [api]);

  // точный код уточняет выбранную отрасль: 46 → 46.69; другой класс — ошибка, а не тихая подмена
  const okved = exact.trim() || sector;
  const exactBad = !!exact.trim() && (!/^\d{2}(\.\d{1,2}){0,2}$/.test(exact.trim()) || !exact.trim().startsWith(sector.slice(0, 2)));
  const sum = (rows: Share[]) => Object.values(toShares(rows)).reduce((a, b) => a + b, 0);
  const sharesBad = sum(buyers) > 100 || sum(suppliers) > 100;
  const ready = title.trim().length >= 2 && !!sector && !exactBad && !sharesBad && !busy;

  const submit = async () => {
    if (!ready) return;
    setBusy(true);
    setError(null);
    const form: OwnCompanyForm = {
      title: title.trim(),
      okved_code: okved,
      region: region.trim() || null,
      products,
      fea: fea || !!countries.trim(),
      fea_countries: countries.split(",").map((c) => c.trim()).filter(Boolean).slice(0, 5),
      buyers: toShares(buyers),
      suppliers: toShares(suppliers),
    };
    try {
      const s = await api.createOwn(form, role);
      // подотрасль из справочника (46.71 — «Оптовая торговля топливом») точнее отрасли
      const name = (sectors.find((x) => okved.startsWith(x.code) && x.code.length > 2) ?? sectors.find((x) => x.code === sector))?.name;
      onCreated(ownClient(s, form, name), role);
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  };

  return (
    <section className="block own" aria-labelledby="own-title">
      <h1 id="own-title">Своя компания</h1>
      <p className="own-lead">
        Заполните анкету — соберём ленту под ваш бизнес так же, как для компаний из демо. Названия, ИНН и сумм не
        спрашиваем: ленте нужны только отрасль, продукты, ВЭД и с кем вы работаете. Анкета хранится на сервере месяц,
        войти в неё можно только из этого браузера.
      </p>
      <form
        className="own-form"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <label className="own-field">
          <span>Как назвать компанию</span>
          <input value={title} maxLength={80} onChange={(e) => setTitle(e.target.value)} placeholder="Например: Наш опт" />
        </label>
        <div className="own-row">
          <label className="own-field">
            <span>Отрасль</span>
            <select value={sector} onChange={(e) => setSector(e.target.value)}>
              <option value="">— выберите —</option>
              {sectors.map((s) => (
                <option key={s.code} value={s.code}>
                  {s.code} · {s.name}
                </option>
              ))}
            </select>
          </label>
          <label className="own-field own-narrow">
            <span>Точный ОКВЭД (необязательно)</span>
            <input value={exact} onChange={(e) => setExact(e.target.value)} placeholder={sector ? `${sector.slice(0, 2)}.…` : "46.69"} aria-invalid={exactBad} />
          </label>
        </div>
        {exactBad && <p className="own-hint own-bad">Код вида 46.69, и он должен начинаться с выбранной отрасли.</p>}
        <label className="own-field">
          <span>Регион</span>
          <input value={region} maxLength={60} onChange={(e) => setRegion(e.target.value)} placeholder="Например: Нижегородская область" />
        </label>
        <fieldset className="own-checks">
          <legend>Продукты банка, которыми пользуетесь</legend>
          {PRODUCTS.map(([k, label]) => (
            <label key={k}>
              <input type="checkbox" checked={products[k]} onChange={(e) => setProducts({ ...products, [k]: e.target.checked })} />
              {label}
            </label>
          ))}
        </fieldset>
        <fieldset className="own-checks">
          <legend>Внешняя торговля</legend>
          <label>
            <input type="checkbox" checked={fea} onChange={(e) => setFea(e.target.checked)} />
            Есть импорт или экспорт
          </label>
          {fea && (
            <label className="own-field">
              <span>Страны через запятую</span>
              <input value={countries} onChange={(e) => setCountries(e.target.value)} placeholder="Китай, Казахстан" />
            </label>
          )}
        </fieldset>
        <SharesInput
          label="Кто ваши покупатели"
          hint="Отрасли покупателей и примерная доля в выручке. Можно пропустить."
          rows={buyers}
          sectors={sectors}
          onChange={setBuyers}
        />
        <SharesInput
          label="Кто ваши поставщики"
          hint="Отрасли поставщиков и примерная доля в закупках. Можно пропустить."
          rows={suppliers}
          sectors={sectors}
          onChange={setSuppliers}
        />
        {sharesBad && <p className="own-hint own-bad">Сумма долей по одной стороне — не больше 100%.</p>}
        <fieldset className="own-checks">
          <legend>Смотреть как</legend>
          {DEMO_ROLES.map((r) => (
            <label key={r}>
              <input type="radio" name="own-role" checked={role === r} onChange={() => setRole(r)} />
              {ROLE_LABELS[r]}
            </label>
          ))}
        </fieldset>
        {error && (
          <div className="error small" role="alert">
            <IconAlert size={16} />
            {error}
          </div>
        )}
        <div className="own-actions">
          <button className="btn btn-primary" disabled={!ready}>
            {busy ? "Собираем ленту…" : "Собрать ленту"}
          </button>
          <button type="button" className="btn" onClick={onCancel} disabled={busy}>
            Отмена
          </button>
        </div>
      </form>
    </section>
  );
}
