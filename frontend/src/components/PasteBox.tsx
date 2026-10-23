// «Вставить ссылку или текст»: POST /analyze → быстрая карточка сразу, глубокий разбор догружается в ленте.
import { useState } from "react";
import type { Api } from "../api";
import { HttpError, errorText } from "../api";
import type { InsightCard } from "../types";

export function PasteBox({ api, onCard }: { api: Api; onCard: (c: InsightCard) => void }) {
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    const v = value.trim();
    if (!v || busy) return;
    setBusy(true);
    setError(null);
    const isUrl = /^https?:\/\/\S+$/i.test(v);
    try {
      onCard(await api.analyze(isUrl ? { url: v } : { text: v }));
      setValue("");
    } catch (e) {
      // 404/405 — эндпоинт разбора ссылок ещё не подключён на бэкенде
      setError(
        e instanceof HttpError && (e.status === 404 || e.status === 405)
          ? "Разбор вставленных новостей пока не подключён на сервере."
          : errorText(e),
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <form
      className="paste"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <input
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="Вставьте ссылку на новость или её текст — разберём для вашей компании"
        aria-label="Ссылка или текст новости"
        disabled={busy}
      />
      <button className="btn btn-primary" disabled={busy || !value.trim()}>
        {busy ? "Разбираем…" : "Разобрать"}
      </button>
      {error && <div className="error small">{error}</div>}
    </form>
  );
}
