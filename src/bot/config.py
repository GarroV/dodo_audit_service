"""Окружение бота: токен, кому отвечать, как запускаться.

Площадка — деталь реализации (решение D004): режим переключается переменной
`BOT_MODE`, значения по умолчанию для секретов не подставляются намеренно —
бот без токена или списка разрешённых ID не должен подниматься и отвечать
случайным отправителям.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

from .errors import BotConfigError

# Подавление ниже: S105 видит «TOKEN» в имени и считает строку зашитым секретом.
# Здесь это имя переменной окружения, а не значение — сам токен в коде
# не появляется ни в каком виде.
TOKEN_VAR = "TELEGRAM_BOT_TOKEN"  # noqa: S105
ALLOWED_IDS_VAR = "ALLOWED_TELEGRAM_IDS"
MODE_VAR = "BOT_MODE"
#: Имя проверяющего для шапки отчёта по его Telegram ID (T063, решение D032).
#: Переменная необязательна: без неё имя берётся из профиля Telegram.
AUDITOR_NAMES_VAR = "AUDITOR_NAMES"

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
    #: Telegram ID → имя проверяющего, как оно должно стоять в отчёте партнёру.
    #: Пустая карта — законное состояние: имена возьмутся из профилей Telegram.
    auditor_names: Mapping[int, str] = field(default_factory=dict)


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


def _parse_auditor_names(raw: str, allowed_ids: frozenset[int]) -> dict[int, str]:
    """Разобрать карту «ID:имя» через запятую.

    Кривая запись — отказ на старте, а не пропуск: пропущенная строка означала
    бы, что в отчёт партнёру молча уедет имя из профиля Telegram, и заметить
    это можно только по готовому отчёту. По той же причине отвергается ID,
    которого нет в списке разрешённых: две разъезжающиеся копии списка — то,
    из-за чего имя перестаёт подставляться без единого сообщения об ошибке.
    """
    names: dict[int, str] = {}
    for chunk in raw.split(","):
        piece = chunk.strip()
        if not piece:
            continue
        if ":" not in piece:
            raise BotConfigError(
                f"{AUDITOR_NAMES_VAR}: запись «{piece}» без двоеточия. "
                f"Нужен вид 111111:Имя Фамилия через запятую"
            )
        raw_id, _, name = piece.partition(":")
        key, value = raw_id.strip(), name.strip()
        if not key.lstrip("-").isdigit():
            raise BotConfigError(
                f"{AUDITOR_NAMES_VAR}: «{key}» — не Telegram ID. Нужно число до двоеточия"
            )
        if not value:
            raise BotConfigError(f"{AUDITOR_NAMES_VAR}: у ID {key} пустое имя")
        if int(key) not in allowed_ids:
            raise BotConfigError(
                f"{AUDITOR_NAMES_VAR}: ID {key} не входит в {ALLOWED_IDS_VAR}. "
                f"Имя без доступа никогда не подставится"
            )
        names[int(key)] = value
    return names


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
    names = _parse_auditor_names(src.get(AUDITOR_NAMES_VAR) or "", allowed_ids)
    return BotSettings(token=token, allowed_ids=allowed_ids, mode=mode, auditor_names=names)
