#!/usr/bin/env bash
# Запуск всего решения одной командой: ./start.sh
#
# 0. Данные кейса и снимок знаний: скачиваются архивом с Яндекс Диска (в git их нет).
# 1. .env: секрет сессий генерируется сам, ключ модели спрашивается один раз (пишется только в локальный .env).
# 2. docker compose up: база, Redis, API, сборщик новостей, интерфейс.
# 3. Первый запуск: демо-база из данных кейса (data/case) и снимка знаний (data/snapshot) — профили 85 тыс.
#    компаний, граф платежей, реестр позиций ЦМИ, архив материалов; свежий сбор новостей; сюжеты типирует модель.
# 4. Прогрев: API сам считает ленты демо-компаний и ставит глубокие разборы живыми моделями; скрипт ждёт,
#    пока у первой демо-компании появятся готовые разборы.
#
# Повторный запуск базу не пересоздаёт. Остановить — docker compose down; с нуля — docker compose down -v.
# Без ключа модели (офлайн, без ₽): LLM_MODE=fixture ./start.sh
set -euo pipefail
cd "$(dirname "$0")"

say() { printf '\033[1m→ %s\033[0m\n' "$*"; }
fail() { printf '\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

DEFAULT_BASE="https://api.alfahack-gateway26.ru/v1"
WARM_STORIES="${WARM_STORIES:-5}"   # сколько глубоких разборов дождаться у первой демо-компании
WARM_MAX_RUB="${WARM_MAX_RUB:-10}"  # потолок расхода на прогрев, ₽
WEB_PORT="${WEB_PORT:-3000}"          # порт интерфейса (занят — WEB_PORT=3100 ./start.sh)
export WEB_PORT

# --- 0. Docker -----------------------------------------------------------------------------------------
command -v docker >/dev/null || fail "нужен Docker: https://docs.docker.com/get-docker/"
docker info >/dev/null 2>&1 || fail "Docker не запущен — запустите Docker Desktop и повторите"
docker compose version >/dev/null 2>&1 || fail "нужен Docker Compose v2 (команда «docker compose»)"
C="docker compose"

# --- 0.5. Данные кейса и снимок знаний -----------------------------------------------------------------
# В git их нет: архив demo-data.tar.gz (scripts/pack_demo_data.sh) лежит на Яндекс Диске, скрипт качает его сам.
# Другая ссылка — ./start.sh <ссылка> или DEMO_DATA_URL=<ссылка> ./start.sh (Яндекс Диск или прямая ссылка);
# архив недоступен — запуск продолжается на синтетике (200 компаний, без позиций ЦМИ).
DEFAULT_DATA_URL="https://disk.yandex.ru/d/Rhd5iiIumWFXBA"
DATA_URL="${1:-${DEMO_DATA_URL:-$DEFAULT_DATA_URL}}"

fetch_data() {
  local href tmp
  case "$DATA_URL" in
    https://disk.yandex.*|https://yadi.sk/*)
      href="$(curl -sf -m 30 --retry 5 --retry-all-errors --retry-delay 3 -G "https://cloud-api.yandex.net/v1/disk/public/resources/download" \
        --data-urlencode "public_key=$DATA_URL" | sed -n 's/.*"href":"\([^"]*\)".*/\1/p')"
      [ -n "$href" ] || { say "Яндекс Диск не отдал архив (ссылка закрыта или недоступна)"; return 1; }
      ;;
    https://*) href="$DATA_URL" ;;
    *) say "ссылка на архив должна начинаться с https://"; return 1 ;;
  esac
  tmp="$(mktemp -d)"
  say "скачиваем данные кейса (30 МБ)"
  if ! curl -sfL -m 900 --retry 5 --retry-all-errors --retry-delay 3 -o "$tmp/demo-data.tar.gz" "$href"; then
    rm -rf "$tmp"; say "не удалось скачать архив данных"; return 1
  fi
  # распаковываем только data/case и data/snapshot: архив — внешний файл, чужие пути не пускаем
  if tar -tzf "$tmp/demo-data.tar.gz" | grep -vqE '^data/(case|snapshot)(/[A-Za-z0-9_.-]*)?$'; then
    rm -rf "$tmp"
    fail "в архиве лишние файлы — ожидаются только data/case/ и data/snapshot/"
  fi
  tar -xzf "$tmp/demo-data.tar.gz" --no-same-owner
  rm -rf "$tmp"
  say "данные кейса на месте"
}

if [ ! -f data/case/df_Uid_data_hack.csv ] || [ ! -f data/snapshot/bank_positions.json.gz ]; then
  if ! fetch_data; then
    printf '\033[33m! Данные кейса не скачались — запуск на синтетике: 200 компаний, без позиций ЦМИ.\n'
    printf '  Для полной версии остановите (Ctrl+C), проверьте интернет и запустите ./start.sh снова.\033[0m\n'
    sleep 5
  fi
fi

# --- 1. .env -------------------------------------------------------------------------------------------
touch .env
has() { grep -qE "^$1=.+" .env; }
put() { printf '%s=%s\n' "$1" "$2" >> .env; }

if ! has SESSION_SECRET; then
  put SESSION_SECRET "$(openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  say "сгенерирован SESSION_SECRET"
fi

# режим моделей: LLM_MODE из окружения (только на этот запуск, compose берёт его сам) или из .env
fixture() { [ "${LLM_MODE:-}" = fixture ] || { [ -z "${LLM_MODE:-}" ] && grep -qE '^LLM_MODE=fixture' .env; }; }

if fixture; then
  say "LLM_MODE=fixture: модели не вызываются, карточки из фикстур"
else
  if ! has LLM_API_BASE; then
    put LLM_API_BASE "${LLM_API_BASE:-$DEFAULT_BASE}"
  fi
  if ! has LLM_API_KEY; then
    key="${LLM_API_KEY:-}"
    if [ -z "$key" ]; then
      [ -t 0 ] || fail "нет LLM_API_KEY: запустите в терминале или передайте LLM_API_KEY=… ./start.sh"
      printf 'Ключ модели (LLM_API_KEY, ввод скрыт): '
      read -rs key
      echo
    fi
    [ -n "$key" ] || fail "ключ пустой — живые модели без него не работают (офлайн: LLM_MODE=fixture ./start.sh)"
    put LLM_API_KEY "$key"
    unset key
    say "ключ записан в .env (в git он не попадает)"
  fi
  # ключ проверяется запросом списка моделей; заголовок — через stdin, чтобы ключа не было в списке процессов
  base="$(grep -E '^LLM_API_BASE=' .env | tail -1 | cut -d= -f2-)"
  code="$(grep -E '^LLM_API_KEY=' .env | tail -1 | cut -d= -f2- \
    | sed 's/^/header = "Authorization: Bearer /; s/$/"/' \
    | curl -s -m 20 -o /dev/null -w '%{http_code}' -K - "$base/models" || true)"
  case "$code" in
    200) say "ключ модели принят ($base)" ;;
    401|403) fail "ключ модели не принят ($code): исправьте LLM_API_KEY в .env и повторите" ;;
    *) say "не удалось проверить ключ (ответ $code) — продолжаем, ошибки будут в логах backend" ;;
  esac
fi

# --- 2. Сервисы ----------------------------------------------------------------------------------------
say "собираем и запускаем сервисы (первый раз — несколько минут)"
# Docker Hub из некоторых сетей обрывает скачивание образов — до трёх попыток
for try in 1 2 3; do
  $C up -d --build && break
  [ "$try" = 3 ] && fail "сборка не удалась: проверьте доступ к Docker Hub (docker pull nginx:1.27-alpine)"
  say "сборка прервалась, повтор $((try + 1)) из 3"
  sleep 5
done

# --- 3. Демо-база при первом запуске -------------------------------------------------------------------
profiles() { $C exec -T db psql -U newsai -d newsai -tAc "select count(*) from company_profiles" 2>/dev/null | tr -d '[:space:]'; }
n="$(profiles || echo 0)"
if [ "${n:-0}" -lt 1000 ]; then
  [ -f data/case/df_Uid_data_hack.csv ] || say "нет data/case: профили из синтетики (200 компаний)"
  [ -f data/snapshot/bank_positions.json.gz ] || say "нет data/snapshot: без реестра ЦМИ и архива"
  say "первый запуск: заливаем демо-базу (≈5–7 минут)"
  $C stop ingest >/dev/null
  if fixture; then
    $C run --rm backend python -m backend.db.demo
  else
    $C run --rm backend python -m backend.db.demo --llm
  fi
  $C start ingest >/dev/null
  # API строил ленты по пустой базе — перезапуск, прогрев начнётся сразу по новым данным
  $C restart backend >/dev/null
else
  say "демо-база уже есть ($n профилей) — пропускаем заливку"
fi

# --- 4. Прогрев ----------------------------------------------------------------------------------------
say "ждём API"
for _ in $(seq 1 60); do
  curl -sf -m 3 http://localhost:$WEB_PORT/api/health >/dev/null 2>&1 && break
  sleep 2
done
curl -sf -m 3 http://localhost:$WEB_PORT/api/health >/dev/null || fail "API не отвечает: docker compose logs backend"

if ! fixture; then
  say "прогрев: глубокие разборы живыми моделями (до $WARM_STORIES карточек, не больше $WARM_MAX_RUB ₽)"
  $C exec -T backend python scripts/warm_demo_cache.py --base http://localhost:8000 \
    --client U_DEMO_BORROWER:cfo --stories "$WARM_STORIES" --max-rub "$WARM_MAX_RUB" --patience 240 || \
    say "прогрев прервался — разборы продолжат догружаться в фоне"
fi

cat <<EOF

  Готово: http://localhost:$WEB_PORT
  · компания и роль — в переключателе сверху; «+ Своя компания…» — лента под свой бизнес по анкете
  · новости собираются каждые 5 минут, глубокие разборы догружаются в фоне
  · журнал разборов и стоимость — вкладка «Журнал разборов»; API и документация — http://localhost:8000/docs
  · остановить: docker compose down
EOF
