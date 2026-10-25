// Форматирование денег: копеечные вызовы моделей не должны выглядеть как «0 ₽».

/** Число рублей без знака: 0 → «0», 0 < v < 0,01 → «<0,01», иначе до двух знаков. */
export const rubNum = (v: number): string =>
  v > 0 && v < 0.01 ? "<0,01" : v.toLocaleString("ru-RU", { maximumFractionDigits: 2 });

/** Сумма со знаком ₽: rub(0.004) → «<0,01 ₽». */
export const rub = (v: number): string => `${rubNum(v)} ₽`;
