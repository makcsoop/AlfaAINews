// «Вставить ссылку или текст»: POST /analyze → быстрая карточка сразу, глубокий разбор догружается в ленте.
import { useState } from "react";
import type { Api } from "../api";
import { HttpError, errorText } from "../api";
import type { InsightCard } from "../types";

// тот же материал второй раз не отправляем: открываем уже собранную карточку, лента не дублируется.
// Общий для поля на главной и в ленте; сбрасывается перезагрузкой страницы
const done = new Map<string, InsightCard>();

interface Props {
  api: Api;
  scope: string; // компания и роль сессии: карточка вставки своя у каждой
  onCard: (c: InsightCard) => void;
}

export function PasteBox({ api, scope, onCard }: Props) {
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    const v = value.trim();
    if (!v || busy) return;
    setBusy(true);
    setError(null);
    const isUrl = /^https?:\/\/\S+$/i.test(v);
    const key = `${scope}|${v.replace(/\s+/g, " ")}`;
    const known = done.get(key);
    if (known) {
      onCard(known);
      setValue("");
      setBusy(false);
      return;
    }
    try {
      const card = await api.analyze(isUrl ? { url: v } : { text: v });
      done.set(key, card);
      onCard(card);
      setValue("");
    } catch (e) {
      // 400 — что не так со ссылкой или текстом, 503 — текст сервера (база недоступна, не разобрали);
      // 404/405 — на этом стенде нет эндпоинта вставки
      setError(
        e instanceof HttpError && (e.status === 404 || e.status === 405)
          ? "Разбор вставленных новостей на этом стенде не подключён."
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
