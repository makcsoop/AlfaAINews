// «Полезно / не про нас»: в обучение скоринга идут только эти явные отметки (вес 0,3), клики — в метрики.
import { useState } from "react";
import { completeStep } from "../onboarding";
import type { FeedbackKind } from "../types";
import { IconCheck, IconEyeOff, IconThumb } from "./icons";

export function FeedbackBar({ onMark }: { onMark: (kind: FeedbackKind) => void }) {
  const [sent, setSent] = useState<FeedbackKind | null>(null);
  const send = (kind: FeedbackKind) => {
    setSent(kind);
    onMark(kind);
    completeStep("rate");
  };
  return (
    <section className="feedback" id="sec-feedback" aria-label="Оценка разбора">
      {sent ? (
        <div className="feedback-done" role="status">
          <span className="feedback-ok">
            <IconCheck size={18} />
          </span>
          <div>
            <strong>Спасибо, учтём</strong>
            <p>
              {sent === "useful"
                ? "Будем показывать больше похожих событий."
                : "Похожие события будем показывать реже."}
            </p>
          </div>
        </div>
      ) : (
        <>
          <div>
            <strong>Разбор был полезен?</strong>
            <p>Отметка настраивает вашу ленту.</p>
          </div>
          <div className="feedback-btns">
            <button className="btn" onClick={() => send("useful")}>
              <IconThumb size={18} />
              Полезно
            </button>
            <button className="btn" onClick={() => send("not_relevant")}>
              <IconEyeOff size={18} />
              Не про нас
            </button>
          </div>
        </>
      )}
    </section>
  );
}
