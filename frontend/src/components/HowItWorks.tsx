// «Как это работает»: путь события от публикации до карточки, устройство карточки, метки и данные.
import { useState } from "react";
import { go } from "../nav";
import { Legend } from "./bits";
import { IconCheck, IconChevron, IconCross, IconPlay } from "./icons";

const PIPELINE = [
  {
    name: "Сбор",
    short: "Источники",
    text: "Каждые несколько минут сервис забирает публикации официальных источников, агентств, деловых изданий, отраслевых лент и обзоры ЦМИ Альфа-Банка.",
    card: "Источники в шапке карточки — официальные и агентства первыми.",
  },
  {
    name: "Сюжеты и факты",
    short: "Сюжеты",
    text: "Публикации об одном событии склеиваются в сюжет. Факты извлекаются вместе с цитатами; статус «подтверждено» зависит от числа независимых источников.",
    card: "Раздел «Что произошло» и сноски с цитатами.",
  },
  {
    name: "Значимость",
    short: "Скоринг",
    text: "Скоринг сопоставляет каналы влияния события — ставку, курс, тарифы, налоги — с профилем компании: продуктами в банке, отраслью, ВЭД и контрагентами. Это делает код, а не модель.",
    card: "Значимость из 100 и «Почему вы это видите» с вкладом каждого фактора.",
  },
  {
    name: "Быстрая оценка",
    short: "Сразу",
    text: "Сразу после скоринга в ленте появляется карточка: что произошло и почему это важно вам — без языковых моделей.",
    card: "Пометка «быстрая оценка» пропадёт, когда догрузится разбор.",
  },
  {
    name: "Глубокий разбор",
    short: "Агенты",
    text: "Для самых значимых событий агенты составляют план, два независимых аналитика оценивают направление факторов, арбитр сводит их выводы. Знак для компании ставит скоринг.",
    card: "Последствия, что сделать, оценка банка и рынка.",
  },
  {
    name: "Проверка",
    short: "Критик",
    text: "Критик и код сверяют числа, даты, ссылки и метки с источниками и возвращают текст на доработку, пока проверка не пройдёт.",
    card: "Отметка «проверено» у каждого утверждения и журнал «Как это получено».",
  },
  {
    name: "Публикация",
    short: "Лента",
    text: "Карточка попадает в ленту. Если аналитики разошлись в оценке, вывод сначала смотрит эксперт банка.",
    card: "Пометка «на проверке», пока эксперт не подтвердит вывод.",
  },
];

const ANATOMY = [
  { q: "Что произошло", a: "Факты с цитатами и ссылками на первоисточник." },
  { q: "Почему важно вам", a: "Какие признаки компании задевает событие и насколько." },
  { q: "Последствия", a: "Риски и возможности с горизонтом и условиями." },
  { q: "Что сделать", a: "Конкретные шаги: что проверить, что обсудить." },
  { q: "Как оценивают банк и рынок", a: "Позиция ЦМИ, ожидания рынка, «ждали — вышло»." },
  { q: "На чём основан вывод", a: "Уверенность, её причины и что изменит вывод." },
];

export function HowItWorks({ onTour }: { onTour: () => void }) {
  const [step, setStep] = useState(2);
  const s = PIPELINE[step];
  return (
    <div className="how">
      <section className="how-hero">
        <div>
          <h2>От публикации до карточки — за семь шагов</h2>
          <p>
            Мы не пересказываем новости. Мы проверяем факты, считаем влияние на вашу компанию и объясняем, что делать, — и
            показываем, на чём основан каждый вывод.
          </p>
        </div>
        <button className="btn btn-dark" onClick={onTour}>
          <IconPlay size={18} />
          Пройти знакомство
        </button>
      </section>

      <section className="pipeline" aria-label="Путь события">
        <ol className="pipe-steps" role="tablist">
          {PIPELINE.map((p, i) => (
            <li key={p.name}>
              <button
                role="tab"
                aria-selected={i === step}
                className={i === step ? "on" : i < step ? "passed" : undefined}
                onClick={() => setStep(i)}
              >
                <span className="pipe-n">{i + 1}</span>
                <span className="pipe-name">{p.short}</span>
              </button>
            </li>
          ))}
        </ol>
        <div className="pipe-detail" role="tabpanel" key={step}>
          <div>
            <span className="pipe-kicker">Шаг {step + 1}</span>
            <h3>{s.name}</h3>
            <p>{s.text}</p>
          </div>
          <div className="pipe-card">
            <span>Что вы увидите в карточке</span>
            <p>{s.card}</p>
          </div>
          <div className="pipe-nav">
            <button className="btn btn-ghost" disabled={step === 0} onClick={() => setStep(step - 1)}>
              Назад
            </button>
            <button className="btn" disabled={step === PIPELINE.length - 1} onClick={() => setStep(step + 1)}>
              Следующий шаг
              <IconChevron size={16} />
            </button>
          </div>
        </div>
      </section>

      <div className="how-grid">
        <section className="block">
          <div className="block-head">
            <h2>Шесть вопросов карточки</h2>
          </div>
          <ol className="anatomy">
            {ANATOMY.map((x, i) => (
              <li key={x.q}>
                <span>{i + 1}</span>
                <div>
                  <strong>{x.q}</strong>
                  <p>{x.a}</p>
                </div>
              </li>
            ))}
          </ol>
        </section>
        <section className="block">
          <div className="block-head">
            <h2>Метки утверждений</h2>
          </div>
          <Legend />
        </section>
      </div>

      <section className="data-block" aria-labelledby="data-title">
        <h2 id="data-title">Ваши данные</h2>
        <div className="data-cols">
          <div>
            <h3>Используем для оценки</h3>
            <ul>
              {["продукты компании в банке", "отрасль и регион", "внешнеэкономическую деятельность", "доли контрагентов"].map((x) => (
                <li key={x}>
                  <IconCheck size={18} />
                  {x}
                </li>
              ))}
            </ul>
          </div>
          <div>
            <h3>Никогда не передаём моделям</h3>
            <ul>
              {["сырые платежи", "персональные данные", "данные других компаний"].map((x) => (
                <li key={x} className="no">
                  <IconCross size={18} />
                  {x}
                </li>
              ))}
            </ul>
          </div>
        </div>
        <button className="link" onClick={() => go("faq")}>
          Вопросы о данных
          <IconChevron size={16} />
        </button>
      </section>
    </div>
  );
}
