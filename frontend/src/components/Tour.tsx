// Знакомство при первом входе в сервис: четыре экрана о том, как он работает. Повторить — из «Как это работает».
import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { AlfaMark, IconCheck, IconCross, IconSpark, IconThumb } from "./icons";
import { ClaimTag, Significance } from "./bits";

interface Props {
  company: string;
  features: string[];
  onClose: () => void;
}

const SOURCES = ["Банк России", "Минфин России", "Интерфакс", "ТАСС", "РБК", "Росстат", "ЦМИ Альфа-Банка"];

export function Tour({ company, features, onClose }: Props) {
  const [step, setStep] = useState(0);
  const dialog = useRef<HTMLDivElement>(null);
  const shown = features.length > 0 ? features.slice(0, 3) : ["кредиты в банке", "отрасль", "ВЭД"];

  const slides: { title: string; text: ReactNode; art: ReactNode }[] = [
    {
      title: "Новости, которые касаются именно вас",
      text: (
        <>
          Мы читаем официальные источники, агентства и аналитику ЦМИ Альфа-Банка, склеиваем публикации в сюжеты и
          оставляем только события, которые затрагивают «{company}».
        </>
      ),
      art: (
        <div className="art art-sources">
          <ul>
            {SOURCES.map((s, i) => (
              <li key={s} style={{ animationDelay: `${i * 70}ms` }}>
                {s}
              </li>
            ))}
          </ul>
          <span className="art-arrow" aria-hidden="true" />
          <div className="art-story">
            <span className="art-bar" />
            <b>Важно для вашего бизнеса</b>
            <span>только то, что касается вашей компании</span>
          </div>
        </div>
      ),
    },
    {
      title: "Значимость считает код, а не нейросеть",
      text: (
        <>
          Скоринг сопоставляет событие с профилем компании в банке и показывает вклад каждого фактора. Языковые модели
          только разбирают событие и пишут текст — его тоже проверяет код.
        </>
      ),
      art: (
        <div className="art art-score">
          <ul>
            {shown.map((f, i) => (
              <li key={f} style={{ animationDelay: `${i * 120}ms` }}>
                <IconCheck size={16} />
                {f}
              </li>
            ))}
          </ul>
          <div className="art-score-num">
            <span>значимость</span>
            <Significance score={0.82} size="l" />
          </div>
        </div>
      ),
    },
    {
      title: "Факт отдельно от мнения",
      text: (
        <>
          У каждого утверждения — метка и сноска на источник с цитатой. Позиция банка, ожидания рынка и выводы AI
          никогда не смешиваются с фактами, а предложения банка всегда помечены.
        </>
      ),
      art: (
        <div className="art art-labels">
          <p>
            <ClaimTag type="fact" /> ЦБ сохранил ставку 14%<sup>1</sup>
          </p>
          <p>
            <ClaimTag type="bank_view" /> ЦМИ ждёт 14% до конца года<sup>2</sup>
          </p>
          <p>
            <ClaimTag type="model_inference" /> Рефинансирование откладывается
          </p>
          <p>
            <ClaimTag type="bank_offer" /> Фиксация ставки по кредиту
          </p>
        </div>
      ),
    },
    {
      title: "Спрашивайте и настраивайте",
      text: (
        <>
          Задайте вопрос по событию — AI ответит по материалам разбора и данным вашей компании. Отмечайте «Полезно» или
          «Не про нас», и лента подстроится под ваши задачи.
        </>
      ),
      art: (
        <div className="art art-chat">
          <div className="art-q">Как это скажется на нашем долге?</div>
          <div className="art-a">
            <IconSpark size={16} />
            Платежи по плавающим кредитам не снизятся до конца года…
          </div>
          <div className="art-fb">
            <span>
              <IconThumb size={16} /> Полезно
            </span>
            <span>Не про нас</span>
          </div>
        </div>
      ),
    },
  ];

  const last = step === slides.length - 1;
  useEffect(() => {
    dialog.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === "ArrowRight") setStep((s) => Math.min(s + 1, slides.length - 1));
      if (e.key === "ArrowLeft") setStep((s) => Math.max(s - 1, 0));
    };
    window.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [onClose, slides.length]);

  const s = slides[step];
  return (
    <div className="modal-back" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="tour" role="dialog" aria-modal="true" aria-labelledby="tour-title" tabIndex={-1} ref={dialog}>
        <div className="tour-art" key={`art-${step}`}>
          <span className="tour-mark" aria-hidden="true">
            <AlfaMark />
          </span>
          {s.art}
        </div>
        <div className="tour-body">
          <button className="tour-x" onClick={onClose} aria-label="Закрыть знакомство">
            <IconCross size={20} />
          </button>
          <span className="tour-count">
            Знакомство · {step + 1} из {slides.length}
          </span>
          <div className="tour-text" key={`text-${step}`}>
            <h2 id="tour-title">{s.title}</h2>
            <p>{s.text}</p>
          </div>
          <div className="tour-nav">
            <div className="tour-dots" role="tablist" aria-label="Шаги знакомства">
              {slides.map((x, i) => (
                <button
                  key={x.title}
                  role="tab"
                  aria-selected={i === step}
                  aria-label={`Шаг ${i + 1}`}
                  className={i === step ? "on" : undefined}
                  onClick={() => setStep(i)}
                />
              ))}
            </div>
            {step > 0 && (
              <button className="btn btn-ghost" onClick={() => setStep(step - 1)}>
                Назад
              </button>
            )}
            <button className="btn btn-primary" onClick={() => (last ? onClose() : setStep(step + 1))}>
              {last ? "Начать" : "Далее"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
