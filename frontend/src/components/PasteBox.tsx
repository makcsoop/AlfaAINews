// «Вставить ссылку или текст»: POST /analyze → быстрая карточка сразу, глубокий разбор догружается в ленте.
import { useState } from "react";
import type { Api } from "../api";
import { HttpError, errorText } from "../api";
import { completeStep } from "../onboarding";
import type { InsightCard } from "../types";
import { IconAlert, IconLink } from "./icons";

export function PasteBox({ api, onCard }: { api: Api; onCard: (c: InsightCard) => void }) {
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    const v = value.trim();
    if (!v || busy) return;
    setBusy(true);
    setError(null);
    completeStep("paste");
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
    <section className="paste-block" id="paste" aria-labelledby="paste-title">
      <h2 id="paste-title">Проверить свою новость</h2>
      <p>Вставьте ссылку или текст — покажем, что событие значит для вашей компании и что с ним делать.</p>
      <form
        className="paste"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <IconLink />
        <input
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="https://… или текст новости"
          aria-label="Ссылка или текст новости"
          disabled={busy}
        />
        <button className="btn btn-primary" disabled={busy || !value.trim()}>
          {busy ? "Разбираем…" : "Разобрать"}
        </button>
      </form>
      {error && (
        <div className="error small" role="alert">
          <IconAlert size={18} />
          {error}
        </div>
      )}
    </section>
  );
}
