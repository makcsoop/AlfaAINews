"""Однократный вход в Telegram для сборщика: `uv run python scripts/telegram_login.py`.

Запускает владелец аккаунта: скрипт спросит номер телефона и код из Telegram (и пароль 2FA, если включён).
Готовая сессия записывается в .env как TG_SESSION_STRING и на экран не выводится.
Сессия даёт полный доступ к аккаунту: .env не коммитить, не пересылать. Лучше старый аккаунт, не свежий
(FAQ Telethon: новые и VoIP-номера чаще банят).
"""

import re
import sys
from pathlib import Path

from telethon.sessions import StringSession
from telethon.sync import TelegramClient

ENV = Path(__file__).resolve().parents[1] / ".env"


def _env() -> dict[str, str]:
    return dict(re.findall(r"^([A-Z0-9_]+)=(.*)$", ENV.read_text(encoding="utf-8"), re.MULTILINE))


def main() -> None:
    env = _env()
    if not env.get("TG_API_ID") or not env.get("TG_API_HASH"):
        sys.exit("В .env нет TG_API_ID / TG_API_HASH")
    with TelegramClient(StringSession(), int(env["TG_API_ID"]), env["TG_API_HASH"]) as client:
        me = client.get_me()
        session = client.session.save()
    text = ENV.read_text(encoding="utf-8")
    if re.search(r"^TG_SESSION_STRING=.*$", text, re.MULTILINE):
        text = re.sub(r"^TG_SESSION_STRING=.*$", f"TG_SESSION_STRING={session}", text, flags=re.MULTILINE)
    else:
        text += f"\nTG_SESSION_STRING={session}\n"
    ENV.write_text(text, encoding="utf-8")
    print(f"Готово: вошли как {me.first_name or ''} (@{me.username or '—'}), сессия записана в .env")


if __name__ == "__main__":
    main()
