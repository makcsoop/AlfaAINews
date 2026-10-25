# Деплой на сервер

Один сервер, Docker Compose: база (PostgreSQL + pgvector), Redis, API, воркер сбора новостей, фронт (nginx)
и Caddy — вход с TLS. Наружу открыты только порты 80 и 443; API и база слушают только `127.0.0.1`.

```
интернет → Caddy :443 (TLS, Let's Encrypt) → frontend (nginx) ─┬─ интерфейс
                                                              └─ /api/ → backend :8000 → db, redis
                                         ingest (сбор, склейка, факты) → db
```

Связанные документы: [сопровождение](scaling_and_ops.md), [безопасность](security.md), [мониторинг](monitoring.md).

## Требования к серверу

| | Минимум |
|---|---|
| ОС | Ubuntu 22.04 / 24.04 или другой Linux с Docker |
| Docker | Engine 24+ и Compose ≥ 2.24 (`docker compose version`) |
| Ресурсы | 2 vCPU, 4 ГБ RAM, 20 ГБ диска |
| Сеть | открыты 22 (SSH), 80 и 443; исходящий доступ к источникам новостей и шлюзу моделей |
| Домен (для HTTPS) | A-запись домена на IP сервера — до первого запуска |

Установка Docker на чистый Ubuntu:

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER   # перелогиниться
```

## Файл .env

Создаётся на сервере один раз в папке проекта (`/opt/alfa-ai-news/.env`), в git и в скрипты не попадает:

| Переменная | Значение | Обязательно |
|---|---|---|
| `SESSION_SECRET` | `openssl rand -hex 32` | да — без него compose не стартует |
| `POSTGRES_PASSWORD` | длинный случайный пароль | да (по умолчанию `newsai`) |
| `LLM_API_KEY` | ключ шлюза моделей | да для живых моделей |
| `LLM_API_BASE` | адрес шлюза, как в локальном `.env` | если отличается от умолчания |
| `LLM_MODE` | `live` (по умолчанию) или `fixture` — без ключа и без ₽ | нет |
| `LLM_DAILY_BUDGET_RUB` | дневной лимит, например `500` | нет (300) |
| `SITE_ADDRESS` | домен (`news.example.ru`) или `:80` без домена | да |
| `SESSION_COOKIE_SECURE` | `true` при домене и HTTPS, `false` на `:80` | да |
| `EXTRACT_LLM` | `true` — тип подтверждённых сюжетов ставит модель (≈ 1 ₽ за проход) | нет (`false`) |
| `TG_API_ID`, `TG_API_HASH`, `TG_SESSION_STRING` | вход в Telegram; без них — публичные страницы `t.me/s` | нет |

Права на файл: `chmod 600 .env`.

## Первый запуск

С машины, где есть репозиторий и данные (`data/case/`, `data/snapshot/`, `data/bank/pdf/`):

```bash
ssh user@host 'sudo mkdir -p /opt/alfa-ai-news && sudo chown $USER /opt/alfa-ai-news'
# создать на сервере /opt/alfa-ai-news/.env (раздел выше)
BOOTSTRAP=1 ./scripts/deploy.sh user@host
```

Скрипт выкладывает код ветки `main` (только закоммиченное), копирует данные rsync, собирает образы, поднимает
сервисы и с `BOOTSTRAP=1` строит демо-базу: CSV кейса, граф платежей, профили, снимок знаний ЦМИ, один проход
сбора, склейка, факты — без вызовов моделей, ≈ 5–6 минут.

Прогрев глубоких карточек перед показом (≈ 0,4 ₽ за карточку):

```bash
ssh user@host 'cd /opt/alfa-ai-news && docker compose -f docker-compose.yml -f docker-compose.prod.yml exec backend python scripts/warm_demo_cache.py --stories 20 --max-rub 20'
```

Проверка: `https://<домен>` (или `http://<IP>` при `SITE_ADDRESS=:80`) открывает ленту; выбор компании — из
витрины `config/demo_companies.yaml`.

## Обновление

```bash
./scripts/deploy.sh user@host                    # код main, данные, пересборка, перезапуск
REF=<коммит или тег> ./scripts/deploy.sh user@host   # откат на нужную версию
```

База, Redis и сертификаты лежат в томах Docker и переживают обновление. Кеш карточек — в Redis, поэтому после
перезапуска API готовые разборы не пропадают.

## Эксплуатация

Все команды — на сервере в `/opt/alfa-ai-news`; для краткости `dc` = `docker compose -f docker-compose.yml -f docker-compose.prod.yml`.

| Задача | Команда |
|---|---|
| Состояние сервисов | `dc ps` |
| Логи API / сбора | `dc logs -f --tail 200 backend` / `dc logs -f --tail 200 ingest` |
| Перезапуск API | `dc restart backend` |
| Расход моделей | `dc exec backend sh -c 'python -m backend.llm.report && cat reports/budget.md'` |
| Резервная копия базы | `dc exec -T db pg_dump -U newsai newsai \| gzip > backup-$(date +%F).sql.gz` |
| Восстановление | `gunzip -c backup.sql.gz \| dc exec -T db psql -U newsai newsai` |
| Выключить фоновые разборы | `FEED_BACKGROUND=false` в `.env`, `dc up -d backend` |
| Режим без ключа | `LLM_MODE=fixture` в `.env`, `dc up -d backend ingest` |

Журналы вызовов моделей и попыток доступа — `data/llm_journal/`, `data/security_audit/` на сервере
(папка `data` смонтирована в контейнеры). Логи контейнеров ограничены: 5 файлов по 20 МБ на сервис.

## Безопасность сервера

- Наружу только 80/443 (Caddy) и SSH; база и API — на `127.0.0.1`. Файрвол: `sudo ufw allow OpenSSH && sudo ufw allow 80,443/tcp && sudo ufw enable`.
- `SESSION_COOKIE_SECURE=true` и домен — cookie сессии только по HTTPS.
- Демо-вход без пароля (витрина компаний) — для показа; для эксплуатации нужна аутентификация банка
  ([security.md](security.md), «Что не закрыто»).
