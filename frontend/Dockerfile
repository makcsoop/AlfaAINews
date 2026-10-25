# Сборка React-интерфейса и раздача через nginx. Контекст сборки — корень репозитория
# (моки для dev:mock импортируют fixtures/, в прод-сборку они не попадают; отчёт метрик — из reports/).
FROM node:22-alpine AS build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend ./
COPY fixtures /app/fixtures
# reports/metrics.md встраивается в страницу метрик при сборке (frontend/src/metricsReport.ts)
COPY reports /app/reports
RUN npm run build

FROM nginx:1.27-alpine
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/frontend/dist /usr/share/nginx/html
