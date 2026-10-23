// «Полезно / не про нас»: в обучение скоринга идут только эти явные отметки (вес 0,3), клики — в метрики.
import { useState } from "react";
import type { FeedbackKind } from "../types";

export function FeedbackBar({ onMark }: { onMark: (kind: FeedbackKind) => void }) {
  const [sent, setSent] = useState<FeedbackKind | null>(null);
  const send = (kind: FeedbackKind) => {
    setSent(kind);
    onMark(kind);
  };
  return (
    <section className="feedback" aria-label="Оценка карточки">
      {sent ? (
        <span className="muted">
          {sent === "useful"
            ? "Спасибо — будем показывать больше похожих событий."
            : "Спасибо — похожие события будем показывать реже."}
        </span>
      ) : (
        <>
          <span className="muted small">Карточка была полезна?</span>
          <button className="btn" onClick={() => send("useful")}>
            Полезно
          </button>
          <button className="btn" onClick={() => send("not_relevant")}>
            Не про нас
          </button>
        </>
      )}
    </section>
  );
}
