// Знакомство с сервисом: показан ли тур и какие шаги пользователь уже сделал сам.
// Хранится в браузере; без хранилища — только до перезагрузки.
import { useSyncExternalStore } from "react";

export type StepId = "open" | "rate" | "ask" | "paste";

export interface OnboardingState {
  done: StepId[];
  hidden: boolean;
  toured: boolean;
}

const KEY = "onboarding:v1";
const listeners = new Set<() => void>();

function load(): OnboardingState {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) ?? "null");
    if (v && Array.isArray(v.done)) return { done: v.done, hidden: !!v.hidden, toured: !!v.toured };
  } catch {
    /* нет хранилища — начинаем с нуля */
  }
  return { done: [], hidden: false, toured: false };
}

let state = load();

function set(next: OnboardingState) {
  state = next;
  try {
    localStorage.setItem(KEY, JSON.stringify(state));
  } catch {
    /* без хранилища прогресс держится до перезагрузки */
  }
  listeners.forEach((l) => l());
}

export function completeStep(id: StepId) {
  if (!state.done.includes(id)) set({ ...state, done: [...state.done, id] });
}

export function setChecklistHidden(hidden: boolean) {
  set({ ...state, hidden });
}

export function setToured() {
  if (!state.toured) set({ ...state, toured: true });
}

export function useOnboarding(): OnboardingState {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => state,
  );
}
