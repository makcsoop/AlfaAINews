#!/usr/bin/env bash
# Архив данных для запуска у жюри: CSV кейса и снимок знаний (реестр ЦМИ, консенсус, архив материалов).
# В git их нет — архив выкладывается на Яндекс Диск (доступ по ссылке), ссылка передаётся в ./start.sh <ссылка>.
#
#   scripts/pack_demo_data.sh            # → demo-data.tar.gz в корне проекта (в .gitignore)
#
# Свежий снимок перед упаковкой — на машине с собранной базой: python -m backend.db.demo --export
set -euo pipefail
cd "$(dirname "$0")/.."

for f in data/case/df_Uid_data_hack.csv data/case/df_Uid_payment_hack.csv data/case/df_Fid_Uid_link_hack.csv \
  data/snapshot/bank_positions.json.gz data/snapshot/raw_items.json.gz; do
  [ -f "$f" ] || { echo "нет $f" >&2; exit 1; }
done
COPYFILE_DISABLE=1 tar -czf demo-data.tar.gz data/case/*.csv data/snapshot/*.json.gz
ls -lh demo-data.tar.gz
tar -tzf demo-data.tar.gz
