import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
// Замена фирменных Styrene A / B без лицензии: Geologica (заголовки) и Onest (текст), раздаются вместе с интерфейсом
import "@fontsource-variable/geologica";
import "@fontsource-variable/onest";
import { App } from "./App";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
