#!/usr/bin/env bash
# Выкладка на сервер по SSH (подробно — docs/deploy.md).
#
#   ./scripts/deploy.sh user@host                 # код ветки main + данные, сборка и перезапуск
#   BOOTSTRAP=1 ./scripts/deploy.sh user@host     # первый раз: ещё и демо-база с нуля (≈ 5–6 мин, без ₽)
#   REF=develop DIR=/srv/news ./scripts/deploy.sh user@host
#
# Код берётся из git (git archive — только закоммиченное, без .env и данных). Данные кейса, снимок знаний и
# PDF ЦМИ в git не хранятся и копируются rsync. Файл .env на сервере создаётся вручную один раз (ключи
# в репозиторий и в этот скрипт не попадают).
set -euo pipefail

HOST="${1:?укажите сервер: ./scripts/deploy.sh user@host}"
REF="${REF:-main}"
DIR="${DIR:-/opt/alfa-ai-news}"
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"

cd "$(git rev-parse --show-toplevel)"

echo "→ код $REF ($(git rev-parse --short "$REF")) в $HOST:$DIR"
ssh "$HOST" "mkdir -p '$DIR'"
git archive --format=tar "$REF" | ssh "$HOST" "tar -x -C '$DIR'"

echo "→ данные (не в git): data/case, data/snapshot, data/bank/pdf"
for d in data/case data/snapshot data/bank/pdf; do
  if [ -d "$d" ]; then
    ssh "$HOST" "mkdir -p '$DIR/$d'"
    rsync -az --delete "$d/" "$HOST:$DIR/$d/"
  else
    echo "  нет $d — пропускаю (без CSV кейса init возьмёт синтетику, без снимка — консенсус заново)"
  fi
done

if ! ssh "$HOST" "test -f '$DIR/.env'"; then
  echo "✗ на сервере нет $DIR/.env — создайте его по docs/deploy.md (раздел «Файл .env») и запустите снова" >&2
  exit 1
fi

echo "→ сборка и запуск"
ssh "$HOST" "cd '$DIR' && $COMPOSE up -d --build"

if [ "${BOOTSTRAP:-0}" = "1" ]; then
  # профили по всем платежам кейса берут > 600 МБ (лимит backend): на время заливки лимит выше, а сборщик
  # остановлен — он тоже склеивает сюжеты и спорит за память. Шаги, сделанные раньше, заливка пропускает
  echo "→ демо-база с нуля (правила, без вызовов моделей)"
  ssh "$HOST" "cd '$DIR' && $COMPOSE stop ingest && BACKEND_MEM_LIMIT=1500m $COMPOSE run --rm backend python -m backend.db.demo; rc=\$?; $COMPOSE start ingest; exit \$rc"
fi

echo "→ проверка"
ssh "$HOST" "cd '$DIR' && $COMPOSE ps && curl -fsS http://127.0.0.1:8000/health && echo"
echo "готово. Прогрев демо-кеша (≈ 0,4 ₽ за карточку):"
echo "  ssh $HOST \"cd $DIR && $COMPOSE exec backend python scripts/warm_demo_cache.py --stories 20 --max-rub 20\""
