// Метрики отбора из reports/metrics.md (backend/eval/metrics.py, Р2). Отчёт встраивается при сборке:
// нет файла — нет и раздела «Отбор», страница не падает. Обновить цифры — пересобрать фронт после импорта разметки.
const files = import.meta.glob("../../reports/metrics.md", { query: "?raw", import: "default", eager: true }) as Record<
  string,
  string
>;

export interface SelectionMetrics {
  pairs: number | null;
  clients: number | null;
  ndcg: [number, number] | null; // система, бейзлайн
  precision: [number, number] | null;
  coverage: { name: string; clients: number; ndcg: [number, number]; precision: [number, number] }[];
  misses: { name: string; value: string }[];
  interpretation: string[];
}

const num = (s: string) => Number(s.trim().replace(",", "."));

export function parseMetrics(md: string): SelectionMetrics {
  const out: SelectionMetrics = { pairs: null, clients: null, ndcg: null, precision: null, coverage: [], misses: [], interpretation: [] };
  const head = md.match(/Пар:\s*(\d+),\s*клиентов:\s*(\d+)/);
  if (head) [out.pairs, out.clients] = [Number(head[1]), Number(head[2])];
  let section = "";
  for (const line of md.split("\n")) {
    if (line.startsWith("## ")) section = line.slice(3).trim();
    if (section === "Интерпретация" && line.startsWith("- ")) out.interpretation.push(line.slice(2).trim());
    if (!line.startsWith("|") || line.startsWith("|---")) continue;
    const cells = line.split("|").slice(1, -1).map((c) => c.trim());
    if (/^nDCG@/.test(cells[0]) && cells.length === 3) out.ndcg = [num(cells[1]), num(cells[2])];
    else if (/^precision@/.test(cells[0]) && cells.length === 3) out.precision = [num(cells[1]), num(cells[2])];
    else if (cells.length === 6 && /покрытые|непокрытые/.test(cells[0]))
      out.coverage.push({ name: cells[0], clients: Number(cells[1]), ndcg: [num(cells[2]), num(cells[3])], precision: [num(cells[4]), num(cells[5])] });
    else if (cells.length === 2 && /отрасли/.test(cells[0])) out.misses.push({ name: cells[0], value: cells[1] });
  }
  return out;
}

export const selectionMetrics: SelectionMetrics | null = (() => {
  const md = Object.values(files)[0];
  return md ? parseMetrics(md) : null;
})();
