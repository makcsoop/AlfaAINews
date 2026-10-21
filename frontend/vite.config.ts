import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";

// Бэкенд доступен под /api: в разработке через прокси Vite, в контейнере — через nginx.
// Один origin — cookie сессии работает без CORS.
const backend = process.env.BACKEND_URL ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  resolve: {
    // фикстуры лежат в корне репозитория и нужны только мокам (npm run dev:mock)
    alias: { "@fixtures": fileURLToPath(new URL("../fixtures", import.meta.url)) },
  },
  server: {
    port: 5173,
    fs: { allow: [".."] },
    proxy: {
      "/api": { target: backend, changeOrigin: true, rewrite: (p) => p.replace(/^\/api/, "") },
    },
  },
});
