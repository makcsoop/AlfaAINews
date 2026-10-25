// Знак Альфа-Банка и иконки интерфейса — инлайн-SVG, без зависимостей.
import type { ReactNode } from "react";

// Литера «А» и фундамент: пропорции сняты с конструкции логотипа в брендбуке (стр. 1, 14)
export function AlfaMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 824 1317" className={className} aria-hidden="true" fill="currentColor">
      <path
        fillRule="evenodd"
        d="M20.3 883 289 80C305.7 30 357 0 412 0s106.3 30 123 80l268.7 803H622l-54-176H256l-54 176Zm391.7-683L295.7 564h232.6Z"
      />
      <rect y="1145" width="824" height="172" />
    </svg>
  );
}

// Иконка приложения: белый знак на красном — основная версия логотипа
export function AlfaAppIcon({ size = 40 }: { size?: number }) {
  return (
    <span className="app-icon" style={{ width: size, height: size }}>
      <AlfaMark />
    </span>
  );
}

function Svg({ children, size = 20 }: { children: ReactNode; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className="icon"
    >
      {children}
    </svg>
  );
}

type P = { size?: number };

export const IconHome = (p: P) => (
  <Svg {...p}>
    <path d="M3.5 10.2 12 3.5l8.5 6.7V19a1.5 1.5 0 0 1-1.5 1.5h-4.2v-6h-5.6v6H5A1.5 1.5 0 0 1 3.5 19Z" />
  </Svg>
);
// Стрелка вверх-вправо — символ паттерна брендбука: событие, которое двигает бизнес
export const IconInsights = (p: P) => (
  <Svg {...p}>
    <path d="M4 19 19 4M9 4h10v10" />
  </Svg>
);
export const IconWallet = (p: P) => (
  <Svg {...p}>
    <rect x="3" y="6" width="18" height="13" rx="2.5" />
    <path d="M3 10h18M16.5 14.5h.01" />
  </Svg>
);
export const IconTransfer = (p: P) => (
  <Svg {...p}>
    <path d="M4 8h15M15.5 4.5 19 8l-3.5 3.5M20 16H5M8.5 12.5 5 16l3.5 3.5" />
  </Svg>
);
export const IconCredit = (p: P) => (
  <Svg {...p}>
    <path d="M9 20.5v-17h5.5a4.5 4.5 0 0 1 0 9H6.5M6.5 16.5h9" />
  </Svg>
);
export const IconGlobe = (p: P) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M3.5 12h17M12 3.5c2.3 2.4 3.4 5.2 3.4 8.5s-1.1 6.1-3.4 8.5c-2.3-2.4-3.4-5.2-3.4-8.5s1.1-6.1 3.4-8.5Z" />
  </Svg>
);
export const IconPeople = (p: P) => (
  <Svg {...p}>
    <circle cx="9" cy="8.5" r="3.2" />
    <path d="M3.5 19.5c.6-3 2.8-4.8 5.5-4.8s4.9 1.8 5.5 4.8M15.5 5.6a3.2 3.2 0 0 1 0 5.8M17.5 14.9c1.6.6 2.7 2.2 3 4.6" />
  </Svg>
);
export const IconDoc = (p: P) => (
  <Svg {...p}>
    <path d="M14 3.5H7A1.5 1.5 0 0 0 5.5 5v14A1.5 1.5 0 0 0 7 20.5h10a1.5 1.5 0 0 0 1.5-1.5V8Z" />
    <path d="M14 3.5V8h4.5M9 12.5h6M9 16h4" />
  </Svg>
);
export const IconJournal = (p: P) => (
  <Svg {...p}>
    <path d="M9 6.5h11M9 12h11M9 17.5h11M4.5 6.5h.01M4.5 12h.01M4.5 17.5h.01" />
  </Svg>
);
export const IconChevron = (p: P) => (
  <Svg {...p}>
    <path d="m9.5 5.5 6.5 6.5-6.5 6.5" />
  </Svg>
);
export const IconChevronDown = (p: P) => (
  <Svg {...p}>
    <path d="m6 9.5 6 6 6-6" />
  </Svg>
);
export const IconBack = (p: P) => (
  <Svg {...p}>
    <path d="M19 12H5.5M11 5.5 4.5 12l6.5 6.5" />
  </Svg>
);
export const IconExternal = (p: P) => (
  <Svg {...p}>
    <path d="M8 16 16.5 7.5M9.5 7.5h7v7" />
  </Svg>
);
export const IconCheck = (p: P) => (
  <Svg {...p}>
    <path d="m5 12.5 4.2 4.2L19 7" />
  </Svg>
);
export const IconCross = (p: P) => (
  <Svg {...p}>
    <path d="M6.5 6.5l11 11M17.5 6.5l-11 11" />
  </Svg>
);
export const IconAlert = (p: P) => (
  <Svg {...p}>
    <path d="M12 4 21 19.5H3Z" />
    <path d="M12 10v4.2M12 17h.01" />
  </Svg>
);
export const IconInfo = (p: P) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M12 11v5.5M12 7.8h.01" />
  </Svg>
);
export const IconClock = (p: P) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M12 7.5V12l3 2" />
  </Svg>
);
export const IconSpark = (p: P) => (
  <Svg {...p}>
    <path d="M10.5 3.5 12 8.4l4.9 1.6L12 11.6l-1.5 4.9L9 11.6 4.1 10 9 8.4ZM17.5 14l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8Z" />
  </Svg>
);
export const IconLink = (p: P) => (
  <Svg {...p}>
    <path d="M10 14a4 4 0 0 0 5.7 0l3-3A4 4 0 0 0 13 5.3l-1.2 1.2M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1.2-1.2" />
  </Svg>
);
export const IconSend = (p: P) => (
  <Svg {...p}>
    <path d="M12 19.5V5M5.5 11.5 12 5l6.5 6.5" />
  </Svg>
);
export const IconThumb = (p: P) => (
  <Svg {...p}>
    <path d="M7.5 10.5v9h-3v-9ZM7.5 10.5 11 3.8a2 2 0 0 1 2.9 2.2l-.8 3.5h5a2 2 0 0 1 2 2.4l-1.3 6a2 2 0 0 1-2 1.6H7.5" />
  </Svg>
);
export const IconEyeOff = (p: P) => (
  <Svg {...p}>
    <path d="M4 4l16 16M10.6 6.1A9.6 9.6 0 0 1 12 6c5 0 8.5 4.6 9 6-.3.8-1.2 2.3-2.6 3.6M14.1 14.2A3 3 0 0 1 9.8 9.9M6.5 7.6C4.6 8.9 3.4 10.9 3 12c.5 1.4 4 6 9 6a9 9 0 0 0 3.6-.7" />
  </Svg>
);
export const IconShield = (p: P) => (
  <Svg {...p}>
    <path d="M12 3.5 19 6v5.5c0 4.3-3 7.7-7 9-4-1.3-7-4.7-7-9V6Z" />
    <path d="m8.8 12 2.2 2.2 4.2-4.4" />
  </Svg>
);
export const IconLayers = (p: P) => (
  <Svg {...p}>
    <path d="M12 3.5 21 8l-9 4.5L3 8Z" />
    <path d="m3 12.5 9 4.5 9-4.5M3 16.5 12 21l9-4.5" />
  </Svg>
);
export const IconSun = (p: P) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="4" />
    <path d="M12 2.8v2M12 19.2v2M2.8 12h2M19.2 12h2M5.5 5.5l1.4 1.4M17.1 17.1l1.4 1.4M5.5 18.5l1.4-1.4M17.1 6.9l1.4-1.4" />
  </Svg>
);
export const IconMoon = (p: P) => (
  <Svg {...p}>
    <path d="M19.5 14.2A7.8 7.8 0 0 1 9.8 4.5a7.8 7.8 0 1 0 9.7 9.7Z" />
  </Svg>
);
export const IconSearch = (p: P) => (
  <Svg {...p}>
    <circle cx="11" cy="11" r="6.5" />
    <path d="m16 16 4.5 4.5" />
  </Svg>
);
export const IconHelp = (p: P) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M9.6 9.5a2.5 2.5 0 0 1 4.8.9c0 1.7-2.4 2.1-2.4 3.6M12 16.8h.01" />
  </Svg>
);
export const IconPlay = (p: P) => (
  <Svg {...p}>
    <path d="M8 5.5v13l10.5-6.5Z" />
  </Svg>
);
export const IconBuilding = (p: P) => (
  <Svg {...p}>
    <path d="M4.5 20.5v-14l7-3v17M11.5 8.5l8 2.5v9.5M3 20.5h18M7.5 9h1M7.5 12.5h1M7.5 16h1M14.5 13.5h1M14.5 17h1" />
  </Svg>
);
