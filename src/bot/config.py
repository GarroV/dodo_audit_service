"""Окружение бота: токен, кому отвечать, как запускаться.

Площадка — деталь реализации (решение D004): режим переключается переменной
`BOT_MODE`, значения по умолчанию для секретов не подставляются намеренно —
бот без токена или списка разрешённых ID не должен подниматься и отвечать
случайным отправителям.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from .errors import BotConfigError

TOKEN_VAR = "TELEGRAM_BOT_TOKEN"
ALLOWED_IDS_VAR = "ALLOWED_TELEGRAM_IDS"
MODE_VAR = "BOT_MODE"

#: `.env.example` объявляет только polling — единственный поддерживаемый режим
#: сейчас (разработка без публичного адреса). `webhook` зарезервирован решением
#: D004 на будущий переезд, но реализации у него пока нет: принимать значение,
#: для которого нет обработчика, хуже, чем отказать сразу на старте.
KNOWN_MODES = ("polling",)
DEFAULT_MODE = "polling"


@dataclass(frozen=True)
class BotSettings:
    """Разобранное окружение бота."""

    token: str
    allowed_ids: frozenset[int]
    mode: str


def _required(env: Mapping[str, str], name: str) -> str:
    raw = (env.get(name) or "").strip()
    if not raw:
        raise BotConfigError(
            f"Не задана переменная окружения {name}. Пример значения — в .env.example"
        )
    return raw


def _parse_allowed_ids(raw: str) -> frozenset[int]:
    ids: set[int] = set()
    for chunk in raw.split(","):
        piece = chunk.strip()
        if not piece:
            continue
        if not piece.lstrip("-").isdigit():
            raise BotConfigError(
                f"{ALLOWED_IDS_VAR} содержит нечисловое значение «{piece}». "
                f"Нужны Telegram ID через запятую, например 111111,222222"
            )
        ids.add(int(piece))
    if not ids:
        raise BotConfigError(
            f"{ALLOWED_IDS_VAR} пуст. Без списка разрешённых ID бот отвечал бы "
            f"любому отправителю — это запрещено принципами проекта"
        )
    return frozenset(ids)


def load_bot_settings(env: Mapping[str, str] | None = None) -> BotSettings:
    """Прочитать и проверить окружение бота. Отказ — `BotConfigError`."""
    src = os.environ if env is None else env
    token = _required(src, TOKEN_VAR)
    allowed_ids = _parse_allowed_ids(_required(src, ALLOWED_IDS_VAR))
    mode = (src.get(MODE_VAR) or DEFAULT_MODE).strip().lower()
    if mode not in KNOWN_MODES:
        raise BotConfigError(
            f"Режим «{mode}» ({MODE_VAR}) не поддержан. Доступно: {', '.join(KNOWN_MODES)}"
        )
    return BotSettings(token=token, allowed_ids=allowed_ids, mode=mode)
