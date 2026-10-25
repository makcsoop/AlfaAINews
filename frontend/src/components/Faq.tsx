// «Вопросы и ответы»: поиск, разделы, раскрывающиеся ответы. Ответы — по устройству сервиса (docs/architecture.md, CLAUDE.md).
import { useMemo, useState } from "react";
import type { ReactNode } from "react";
import { go } from "../nav";
import { CLAIM_HINTS, ClaimTag } from "./bits";
import { IconChevron, IconCross, IconSearch } from "./icons";
import type { ClaimType } from "../types";

interface Item {
  id: string;
  topic: string;
  q: string;
  a: ReactNode;
  text: string; // для поиска
}

const TOPICS = ["О сервисе", "Как отбираются события", "Метки и проверка", "Данные и безопасность", "AI и отклик"];

function item(id: string, topic: string, q: string, a: string, extra?: ReactNode): Item {
  return { id, topic, q, a: extra ?? <p>{a}</p>, text: `${q} ${a}`.toLowerCase() };
}

const LABEL_ANSWER = (
  <>
    <p>Каждое утверждение в карточке помечено, откуда оно:</p>
    <ul className="faq-labels">
      {(Object.keys(CLAIM_HINTS) as ClaimType[]).map((t) => (
        <li key={t}>
          <ClaimTag type={t} />
          <span>{CLAIM_HINTS[t]}</span>
        </li>
      ))}
    </ul>
  </>
);

export const FAQ: Item[] = [
  item(
    "what",
    "О сервисе",
    "Что такое «Важно для бизнеса»?",
    "Персональная новостная аналитика внутри Альфа-Бизнеса. Сервис читает новости, официальные источники и аналитику ЦМИ Альфа-Банка, склеивает публикации в сюжеты и показывает только события, которые затрагивают вашу компанию, — с объяснением, что они значат и что с ними делать.",
  ),
  item(
    "sources",
    "О сервисе",
    "Откуда берутся новости?",
    "Из официальных источников (Банк России, Минфин, Минэкономразвития, Росстат, ФНС, ФТС), информационных агентств и деловых изданий (Интерфакс, ТАСС, РБК, ПРАЙМ, Коммерсантъ, Ведомости), отраслевых лент и публичных каналов ведомств. Позиция банка — из обзоров аналитиков ЦМИ Альфа-Банка.",
  ),
  item(
    "fast",
    "О сервисе",
    "Чем быстрая оценка отличается от глубокого разбора?",
    "Быстрая оценка появляется сразу: код сопоставляет событие с профилем компании и считает значимость, без языковых моделей. Для самых значимых событий агенты готовят глубокий разбор: план, два независимых аналитика, арбитр и критик, который возвращает текст на доработку. Разбор догружается в ту же карточку.",
  ),
  item(
    "review",
    "О сервисе",
    "Что значит «на проверке»?",
    "Независимые аналитики разошлись в оценке события. Факты и источники в такой карточке проверены, а вывод перед публикацией смотрит эксперт банка.",
  ),
  item(
    "why",
    "Как отбираются события",
    "Почему я вижу это событие?",
    "Событие меняет экономические факторы — ставку, курс, тарифы, налоги, спрос, — а они влияют на компанию через её профиль в банке: кредиты и депозиты, отрасль, внешнеэкономическую деятельность, контрагентов. В каждой карточке в блоке «Почему вы это видите» показан вклад каждого фактора.",
  ),
  item(
    "ai-decides",
    "Как отбираются события",
    "Решает ли нейросеть, что для меня важно?",
    "Нет. Значимость считает прозрачный скоринг по профилю компании. Языковые модели только разбирают событие и пишут текст, а всё, что они пишут, проверяет код.",
  ),
  item(
    "sign",
    "Как отбираются события",
    "Почему одно событие — риск для одной компании и возможность для другой?",
    "Знак влияния зависит от профиля. Сохранение ключевой ставки на 14% — риск для компании с кредитами: деньги остаются дорогими. И возможность для компании с депозитами: доходность сохраняется.",
  ),
  item(
    "roles",
    "Как отбираются события",
    "Зачем выбирать роль?",
    "Лента и рекомендации учитывают роль: одно и то же событие может быть важнее финансовому директору, чем главному бухгалтеру, и требовать других действий.",
  ),
  item("labels", "Метки и проверка", "Что означают метки в карточке?", Object.values(CLAIM_HINTS).join(" "), LABEL_ANSWER),
  item(
    "checks",
    "Метки и проверка",
    "Как проверяются цифры и даты?",
    "Каждое утверждение ссылается на источник — это цифры-сноски в тексте. Код сверяет числа и даты с цитатой, а вывод — с фактами, на которые он опирается. Если проверка не прошла, рядом с утверждением стоит пометка «не проверено».",
  ),
  item(
    "ads",
    "Метки и проверка",
    "Это реклама?",
    "Нет. Анализ отделён от продуктов банка: предложения всегда отмечены меткой «Предложение банка» и показываются отдельно от выводов.",
  ),
  item(
    "data",
    "Данные и безопасность",
    "Какие данные о компании используются?",
    "Профиль компании в банке: продукты, отрасль, регион, внешнеэкономическая деятельность и доли контрагентов. В языковые модели передаются только признаки и доли — не сырые платежи и не персональные данные.",
  ),
  item(
    "other",
    "Данные и безопасность",
    "Может ли AI рассказать о другой компании?",
    "Нет. Компания определяется по вашей сессии в интернет-банке, а не по тексту вопроса. На вопросы о чужих данных AI отвечает отказом.",
  ),
  item(
    "ask",
    "AI и отклик",
    "Как задать вопрос по событию?",
    "Внизу каждой карточки есть блок «Спросить о событии». AI отвечает по материалам разбора и данным вашей компании и показывает, на что опирался и какие данные смотрел.",
  ),
  item(
    "tune",
    "AI и отклик",
    "Как настроить ленту под себя?",
    "Отмечайте разборы «Полезно» или «Не про нас» — эти отметки учитываются в скоринге, и похожие события будут показываться чаще или реже. Неверное утверждение отметьте «Неверно?» — отметка попадёт в журнал отзывов.",
  ),
  item(
    "paste",
    "AI и отклик",
    "Можно ли разобрать свою новость?",
    "Да. На странице «Обзор» вставьте ссылку или текст новости. Быстрая оценка появится сразу, глубокий разбор догрузится.",
  ),
  item(
    "runs",
    "AI и отклик",
    "Что такое журнал разборов и почему там рубли?",
    "Это открытый журнал работы агентов: какие события разбирались, чем закончилась проверка и сколько стоил каждый разбор. На вызовы моделей действует дневной лимит бюджета.",
  ),
];

function Answer({ it, open, onToggle }: { it: Item; open: boolean; onToggle: () => void }) {
  return (
    <li className={open ? "faq-item open" : "faq-item"}>
      <h3>
        <button aria-expanded={open} aria-controls={`faq-${it.id}`} id={`faq-q-${it.id}`} onClick={onToggle}>
          <span>{it.q}</span>
          <span className="faq-sign" aria-hidden="true" />
        </button>
      </h3>
      <div className="faq-a" id={`faq-${it.id}`} role="region" aria-labelledby={`faq-q-${it.id}`}>
        <div>{it.a}</div>
      </div>
    </li>
  );
}

export function FaqPage() {
  const [query, setQuery] = useState("");
  const [topic, setTopic] = useState<string | null>(null);
  const [open, setOpen] = useState<Set<string>>(() => new Set(["why"]));
  const q = query.trim().toLowerCase();
  const found = useMemo(
    () => FAQ.filter((it) => (!topic || it.topic === topic) && (!q || it.text.includes(q))),
    [q, topic],
  );
  const toggle = (id: string) =>
    setOpen((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });
  return (
    <div className="faq">
      <aside className="faq-nav">
        <label className="search">
          <IconSearch size={18} />
          <span className="sr-only">Поиск по вопросам</span>
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Поиск по вопросам" />
          {query && (
            <button onClick={() => setQuery("")} aria-label="Очистить поиск">
              <IconCross size={16} />
            </button>
          )}
        </label>
        <nav aria-label="Разделы вопросов">
          <button className={topic === null ? "on" : undefined} onClick={() => setTopic(null)}>
            Все вопросы <span>{FAQ.length}</span>
          </button>
          {TOPICS.map((t) => (
            <button key={t} className={topic === t ? "on" : undefined} onClick={() => setTopic(t)}>
              {t} <span>{FAQ.filter((x) => x.topic === t).length}</span>
            </button>
          ))}
        </nav>
      </aside>
      <div className="faq-main">
        {found.length === 0 ? (
          <div className="faq-empty">
            <strong>Ничего не нашли</strong>
            <p>Попробуйте другое слово или задайте вопрос AI в карточке события — он ответит по её материалам.</p>
          </div>
        ) : (
          (topic ? [topic] : TOPICS).map((t) => {
            const list = found.filter((it) => it.topic === t);
            if (list.length === 0) return null;
            return (
              <section key={t} className="faq-group">
                <h2>{t}</h2>
                <ul>
                  {list.map((it) => (
                    <Answer key={it.id} it={it} open={open.has(it.id) || !!q} onToggle={() => toggle(it.id)} />
                  ))}
                </ul>
              </section>
            );
          })
        )}
        <div className="faq-more">
          <div>
            <strong>Не нашли ответ?</strong>
            <p>Посмотрите, как устроен разбор, или задайте вопрос в любой карточке события.</p>
          </div>
          <button className="btn" onClick={() => go("how")}>
            Как это работает
            <IconChevron size={16} />
          </button>
        </div>
      </div>
    </div>
  );
}

// Несколько частых вопросов на «Обзоре» — со ссылкой на все
export function FaqTeaser() {
  const [open, setOpen] = useState<string | null>(null);
  const top = FAQ.filter((x) => ["why", "ai-decides", "data", "fast"].includes(x.id));
  return (
    <section className="block faq-teaser" aria-labelledby="faq-teaser-title">
      <div className="block-head">
        <h2 id="faq-teaser-title">Частые вопросы</h2>
        <button className="link" onClick={() => go("faq")}>
          Все {FAQ.length}
          <IconChevron size={16} />
        </button>
      </div>
      <ul>
        {top.map((it) => (
          <Answer key={it.id} it={it} open={open === it.id} onToggle={() => setOpen(open === it.id ? null : it.id)} />
        ))}
      </ul>
    </section>
  );
}
