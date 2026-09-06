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

from .errors import BotConfigError, BotTextError
from .texts import DEFAULT_UI_LANG, UI_LANG_VAR, default_ui_lang

# Подавление ниже: S105 видит «TOKEN» в имени и считает строку зашитым секретом.
# Здесь это имя переменной окружения, а не значение — сам токен в коде
# не появляется ни в каком виде.
TOKEN_VAR = "TELEGRAM_BOT_TOKEN"  # noqa: S105
ALLOWED_IDS_VAR = "ALLOWED_TELEGRAM_IDS"
MODE_VAR = "BOT_MODE"
#: Имя проверяющего для шапки отчёта по его Telegram ID (T063, решение D032).
#: Переменная необязательна: без неё имя берётся из профиля Telegram.
AUDITOR_NAMES_VAR = "AUDITOR_NAMES"

#: ОСНОВАТЕЛЬ круга доступа к MCP (T253, решение D099). Единственный человек,
#: попадающий в круг настройкой стенда, а не чужой рукой; всех остальных
#: приводят те, кто уже внутри.
#:
#: Переменная необязательна: стенд, на котором подключением к MCP никто не
#: пользуется, обязан подниматься без неё. Тогда круг пуст, и настройка не
#: доступна никому — о чём бот говорит вслух первой строкой журнала, а не
#: оставляет выяснять по молчащему пункту меню.
MCP_OWNER_ID_VAR = "BOT_MCP_OWNER_ID"

#: Чью историю проверок открывают токены, выпущенные ЭТИМ ботом. Тот же
#: вопрос, на который у токенов из `.env` отвечает запись «арендатор=токен»
#: (`src/mcp/config.py`), — и отвечает на него по-прежнему развёртывание, а не
#: человек в чате: арендатор, названный тем, кто просит доступ, это не граница
#: арендаторов, а её отсутствие.
MCP_TENANT_VAR = "BOT_MCP_TENANT"

#: Арендатор по умолчанию — ровно тот, под которым этот же бот сливает
#: проверки (`src.domain.state.DEFAULT_TENANT`, `src.db.push.DEFAULT_TENANT`).
#: Это не догадка, а то же самое значение: функций мультиарендности в MVP нет
#: (решение D005), и токен, открывающий что-то другое, не открывал бы ничего.
#: Стенд, сменивший арендатора проверок, обязан сменить и этот.
DEFAULT_MCP_TENANT = "default"
#: Язык интерфейса до начала проверки (T131). Имя и разбор живут в `texts.py`,
#: рядом с самим каталогом языков, — здесь только проверка на старте.

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
    #: Язык интерфейса стенда — тот, которым бот здоровается до начала проверки
    #: (T131). Начатая проверка перебивает его своим полем `ui_lang`.
    ui_lang: str = DEFAULT_UI_LANG
    #: Telegram ID → имя проверяющего, как оно должно стоять в отчёте партнёру.
    #: Пустая карта — законное состояние: имена возьмутся из профилей Telegram.
    auditor_names: Mapping[int, str] = field(default_factory=dict)
    #: Основатель круга доступа к MCP (T253). `None` — круг не назначен, и
    #: настройка подключения не доступна никому: пустой круг закрыт, а не
    #: открыт. Умолчание «пускать всех» здесь было бы худшим из возможных —
    #: забытая переменная раздавала бы историю проверок партнёров.
    mcp_owner_id: int | None = None
    #: Чью историю открывают выпущенные этим ботом токены.
    mcp_tenant: str = DEFAULT_MCP_TENANT


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


def _parse_mcp_owner_id(raw: str, allowed_ids: frozenset[int]) -> int | None:
    """Основатель круга доступа к MCP — или `None`, если круг не назначен (T253).

    Пусто — законно и означает «круг не назначен»: настройка подключения тогда
    не доступна никому. Именно так, а не наоборот: круг, открытый по умолчанию,
    раздавал бы историю проверок партнёров всякому стенду, где переменную
    забыли, — а забытую переменную замечают позже всего.

    **Основатель обязан входить в список разрешённых ID.** По той же причине,
    по которой это проверяется у имён проверяющих (`_parse_auditor_names`), и
    здесь она весомее: до хендлера человека не пускает мидлварь доступа, значит
    основатель круга, которого нет в списке, до настройки не доберётся никогда.
    Круг остался бы пустым навсегда, а выглядела бы такая настройка работающей.
    """
    piece = raw.strip()
    if not piece:
        return None
    if not piece.lstrip("-").isdigit():
        raise BotConfigError(
            f"{MCP_OWNER_ID_VAR}: «{piece}» — не Telegram ID. Нужно одно число"
        )
    owner = int(piece)
    if owner not in allowed_ids:
        raise BotConfigError(
            f"{MCP_OWNER_ID_VAR}: ID {owner} не входит в {ALLOWED_IDS_VAR}. До настройки "
            f"он не доберётся — мидлварь доступа не пустит его дальше, и круг остался бы "
            f"пустым навсегда"
        )
    return owner


def _parse_mcp_tenant(raw: str) -> str:
    """Арендатор, чью историю открывают выпущенные ботом токены.

    Пусто — тот же арендатор, под которым этот бот сливает проверки. Это не
    подстановка догадки: другого арендатора у проверок MVP не бывает (D005), и
    токен, открывающий что-то ещё, открывал бы пустоту.
    """
    return raw.strip() or DEFAULT_MCP_TENANT


def _parse_ui_lang(env: Mapping[str, str]) -> str:
    """Язык интерфейса стенда — или отказ на старте (T131).

    Разбор один на всех (`texts.default_ui_lang`), здесь он только переводится
    в отказ конфигурации: неизвестный язык обязан останавливать бота на старте,
    а не всплывать первой строкой в чате. Цена молчания — демо, тихо съехавшее
    на русский из-за опечатки в переменной, и узнают об этом на показе.
    """
    try:
        return default_ui_lang(env)
    except BotTextError as exc:
        raise BotConfigError(f"{UI_LANG_VAR}: {exc}") from exc


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
    return BotSettings(
        token=token,
        allowed_ids=allowed_ids,
        mode=mode,
        ui_lang=_parse_ui_lang(src),
        auditor_names=names,
        mcp_owner_id=_parse_mcp_owner_id(src.get(MCP_OWNER_ID_VAR) or "", allowed_ids),
        mcp_tenant=_parse_mcp_tenant(src.get(MCP_TENANT_VAR) or ""),
    )
