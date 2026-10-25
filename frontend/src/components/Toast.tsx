// Короткие уведомления внизу экрана: «скрыли — вернуть», «спасибо за отклик».
import { createContext, useCallback, useContext, useRef, useState } from "react";
import type { ReactNode } from "react";

interface Toast {
  id: number;
  text: string;
  action?: { label: string; onClick: () => void };
}

type Show = (text: string, action?: Toast["action"], ms?: number) => void;

const Ctx = createContext<Show>(() => {});

export const useToast = () => useContext(Ctx);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const seq = useRef(0);
  const drop = useCallback((id: number) => setToasts((t) => t.filter((x) => x.id !== id)), []);
  const show = useCallback<Show>(
    (text, action, ms = 4500) => {
      const id = ++seq.current;
      setToasts((t) => [...t.slice(-2), { id, text, action }]);
      setTimeout(() => drop(id), ms);
    },
    [drop],
  );
  return (
    <Ctx.Provider value={show}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className="toast">
            <span>{t.text}</span>
            {t.action && (
              <button
                onClick={() => {
                  t.action!.onClick();
                  drop(t.id);
                }}
              >
                {t.action.label}
              </button>
            )}
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}
