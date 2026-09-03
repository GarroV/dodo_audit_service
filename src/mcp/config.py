"""Окружение MCP-сервера: чей токен, чьи проверки, на каком адресе слушать.

**Арендатор берётся из токена, а не из аргумента вызова.** Это главное
свойство блока, и оно живёт здесь, а не в инструментах. Аргумент `tenant`
назвать может кто угодно — агент, которому такой инструмент дали в руки,
подставит туда код соседа, и слой чтения честно отдаст чужую историю: он
обязан фильтровать по арендатору, а не угадывать, откуда тот взялся (T110,
`docs/forge/blocks/db.md`). Поэтому связка «токен → арендатор» задаётся
человеком в `.env`, разбирается один раз на старте и снаружи не принимается
ни в каком виде.

Секретов в коде нет: здесь только ИМЕНА переменных, значения — в `.env`
(конституция, раздел «Правила безопасности»).
"""

from __future__ import annotations

import ipaddress
import os
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from .errors import AuthError, McpConfigError

# Подавление S105: это ИМЯ переменной окружения, а не значение секрета.
MCP_TOKENS_VAR = "MCP_TOKENS"
MCP_HOST_VAR = "MCP_HOST"
MCP_PORT_VAR = "MCP_PORT"

#: Где хранилище версий методики. Пусто — инструменты методики выключены
#: целиком: до задачи T098 весь MCP был только чтением проверок, и запись в
#: методику управляющей компании включается явно, а не оказывается включённой.
MCP_CHECKLIST_STORE_VAR = "MCP_CHECKLIST_STORE"

#: Кому открыта методика — чтение версий и правка. Пусто — никому.
#: Отдельно от токенов доступа намеренно: токен партнёра открывает ЕГО
#: проверки, а методика одна на всех, и правит её управляющая компания.
MCP_CHECKLIST_TENANTS_VAR = "MCP_CHECKLIST_TENANTS"

#: Каталог боевой методики — тот же, из которого читает движок. Своей
#: переменной блок не заводит: второй путь к одной методике разошёлся бы с
#: первым, и агент правил бы не то, по чему считается оценка.
DATA_DIR_VAR = "AUDIT_DATA_DIR"

#: Короче этого токен не принимается. Токен, который человек набирает с
#: памяти, дверью не является: сервер отдаёт историю проверок партнёра, и
#: перебор по локальной петле ничем не ограничен.
MIN_TOKEN_LENGTH = 24

#: Слушаем только петлю. Площадка общая — рядом живут чужие проекты, и порт,
#: открытый в сеть, отдал бы им проверки партнёров (конституция).
DEFAULT_HOST = "127.0.0.1"

#: Порт по умолчанию из диапазона рабочей копии блока (8260–8269).
DEFAULT_PORT = 8265

#: Ниже 1024 порты системные, выше 65535 их не бывает.
MIN_PORT = 1024
MAX_PORT = 65535

#: Записи связки разделяются запятой и/или переводом строки: в `.env` длинную
#: строку удобнее разложить построчно, а короткую — записать в одну.
_SEPARATORS = re.compile(r"[,\n]")

#: Схема авторизации, которую понимает сервер. Регистр в заголовке
#: HTTP-клиенты пишут по-разному, поэтому сверка идёт без учёта регистра.
_BEARER = "bearer"


def _bytes(value: str) -> bytes:
    """Строка → байты для сравнения токенов.

    `surrogateescape` здесь не украшение: заголовки HTTP разбираются как
    latin-1, и строка оттуда может нести что угодно. Кодировка одна и та же с
    обеих сторон сравнения, поэтому годный (то есть ASCII) токен совпадает
    сам с собой, а любой другой набор байтов просто не совпадает ни с чем.
    """
    return value.encode("utf-8", "surrogateescape")


@dataclass(frozen=True)
class Settings:
    """Разобранное окружение сервера.

    Карта токенов помечена `repr=False` не для красоты: `repr` настроек
    попадает в трейсбек любой соседней ошибки, а трейсбек — в лог. Один такой
    лог, показанный на созвоне, и токен придётся менять всем.
    """

    tokens: Mapping[str, str] = field(repr=False)
    tenants: tuple[str, ...]
    host: str
    port: int
    #: Хранилище версий методики. `None` — инструменты методики выключены.
    checklist_store: Path | None = None
    #: Арендаторы, которым методика открыта. Пусто — никому.
    checklist_tenants: tuple[str, ...] = ()
    #: Боевая методика, из которой читает движок.
    data_dir: Path | None = None

    def may_manage_checklist(self, tenant: str) -> bool:
        """Открыта ли методика этому арендатору.

        Проверяется по коду арендатора, а тот приходит из токена и ниоткуда
        больше, — то есть право на правку методики держится на той же двери,
        что и граница арендаторов, и отдельного способа его получить нет.
        """
        return self.checklist_store is not None and tenant in self.checklist_tenants

    def tenant_for(self, token: str) -> str | None:
        """Арендатор этого токена или `None`, если токен незнакомый.

        Сверка идёт `secrets.compare_digest`, а не `==`: обычное сравнение
        строк выходит на первом несовпавшем символе, и по времени ответа
        токен подбирается посимвольно. Перебор идёт по всем записям без
        досрочного выхода — по той же причине.

        Сверяются БАЙТЫ, а не строки (T115). Строковый `compare_digest` знаков
        вне ASCII не поддерживает и бросает `TypeError`, а содержимое
        предъявленного токена задаёт клиент: любой набор байтов в заголовке
        ронял бы сверку. Байты сравниваются при любой кодировке, и незнакомый
        токен получает отказ вместо падения. Свои токены до сюда доходят уже
        проверенными (`_parse_tokens`) — заслон стоит с обеих сторон нарочно:
        падение здесь означало бы пустой ответ вместо кода ошибки.
        """
        предъявленный = _bytes(token)
        found: str | None = None
        for known, tenant in self.tokens.items():
            if secrets.compare_digest(_bytes(known), предъявленный):
                found = tenant
        return found


def _parse_tokens(raw: str) -> dict[str, str]:
    """`арендатор=токен` через запятую или перевод строки → карта «токен → арендатор».

    Разбор идёт по ПЕРВОМУ `=`: код арендатора — простой идентификатор, а
    токен произвольный и вполне может содержать `=` (хвост base64). Разбор по
    последнему знаку обрезал бы такой токен, и человек искал бы ошибку в
    доступе, а не в разборе настроек.
    """
    tokens: dict[str, str] = {}
    for chunk in _SEPARATORS.split(raw):
        entry = chunk.strip()
        if not entry:
            continue
        tenant, sign, token = entry.partition("=")
        tenant, token = tenant.strip(), token.strip()
        if not sign or not tenant or not token:
            raise McpConfigError(
                f"Запись {MCP_TOKENS_VAR} читается как «арендатор=токен», а пришло нечто "
                f"другое. Пропустить её молча нельзя: человек считает такой доступ выданным"
            )
        if len(token) < MIN_TOKEN_LENGTH:
            raise McpConfigError(
                f"Токен арендатора «{tenant}» короче {MIN_TOKEN_LENGTH} знаков — такая длина "
                f"перебирается за минуты, а за дверью история проверок партнёра"
            )
        if not token.isascii():
            raise McpConfigError(
                f"Токен арендатора «{tenant}» набран знаками вне ASCII (кириллицей и т. п.). "
                f"Такой токен не доедет до сервера: заголовки HTTP кодируются latin-1, часть "
                f"клиентов откажется его отправлять, часть пришлёт другие байты — доступ будет "
                f"считаться выданным и не работать. Годный токен даёт "
                f'python3 -c "import secrets; print(secrets.token_urlsafe(32))"'
            )
        if token in tokens and tokens[token] != tenant:
            raise McpConfigError(
                f"Один и тот же токен назван дважды, у арендаторов «{tokens[token]}» и "
                f"«{tenant}». Чью историю он открывает — решал бы порядок строк в файле"
            )
        tokens[token] = tenant
    if not tokens:
        raise McpConfigError(
            f"Не задана переменная окружения {MCP_TOKENS_VAR} — связка «арендатор=токен». "
            f"Без неё сервер поднялся бы открытым: пример — в .env.example"
        )
    return tokens


def _parse_host(raw: str) -> str:
    """Адрес прослушивания. Только петля — и это проверка, а не договорённость."""
    host = raw.strip() or DEFAULT_HOST
    if host == "localhost":
        return host
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = False
    if not loopback:
        raise McpConfigError(
            f"Адрес {MCP_HOST_VAR}={host} не петля. Сервер отдаёт проверки партнёров по "
            f"личному токену, а площадка общая: наружу он не публикуется (T095)"
        )
    return host


def _parse_port(raw: str) -> int:
    """Порт прослушивания в осмысленных границах — или явный отказ."""
    value = raw.strip()
    if not value:
        return DEFAULT_PORT
    try:
        port = int(value)
    except ValueError:
        raise McpConfigError(f"Порт {MCP_PORT_VAR}={value} не число") from None
    if not MIN_PORT <= port <= MAX_PORT:
        raise McpConfigError(
            f"Порт {MCP_PORT_VAR}={port} вне допустимого: ожидается от {MIN_PORT} до {MAX_PORT}"
        )
    return port


def _parse_checklist(
    src: Mapping[str, str], tenants: tuple[str, ...]
) -> tuple[Path | None, tuple[str, ...], Path | None]:
    """Настройки методики: хранилище версий, кому открыто, где боевой набор.

    Обе половины настройки обязаны быть названы вместе. Названная наполовину
    настройка — самый неприятный исход: человек считает правку методики
    включённой, а инструменты молча отказывают всем — и разбираться он идёт в
    код, а не в `.env`.

    Арендатор, которому открыта методика, обязан существовать среди токенов:
    опечатка в коде иначе означала бы доступ, выданный никому, — и выглядела бы
    ровно как работающая настройка.
    """
    store_raw = (src.get(MCP_CHECKLIST_STORE_VAR) or "").strip()
    сказано = _SEPARATORS.split(src.get(MCP_CHECKLIST_TENANTS_VAR) or "")
    named = tuple(sorted({x.strip() for x in сказано if x.strip()}))
    if not store_raw and not named:
        return None, (), None
    if not store_raw:
        raise McpConfigError(
            f"Сказано, кому открыта методика ({MCP_CHECKLIST_TENANTS_VAR}), но не сказано, где "
            f"держать её версии ({MCP_CHECKLIST_STORE_VAR}). Правка методики без хранилища "
            f"версий невозможна: она обязана давать новую версию, а не переписывать боевую"
        )
    if not named:
        raise McpConfigError(
            f"Задано хранилище версий методики ({MCP_CHECKLIST_STORE_VAR}), но не сказано, кому "
            f"методика открыта ({MCP_CHECKLIST_TENANTS_VAR}). Инструменты методики отказывали бы "
            f"всем, а выглядело бы это как включённая правка"
        )
    чужие = [code for code in named if code not in tenants]
    if чужие:
        raise McpConfigError(
            f"В {MCP_CHECKLIST_TENANTS_VAR} названы арендаторы, которых нет среди токенов: "
            f"{', '.join(чужие)}. Опечатка в коде означала бы доступ, выданный никому, — и "
            f"выглядела бы как работающая настройка"
        )
    data_raw = (src.get(DATA_DIR_VAR) or "").strip()
    if not data_raw:
        raise McpConfigError(
            f"Правка методики включена, но не задан {DATA_DIR_VAR} — каталог методики, из "
            f"которого читает движок. С него начинается хранилище версий, и по нему же "
            f"проверяется, увидит ли движок публикацию"
        )
    return (
        Path(os.path.abspath(os.path.expanduser(store_raw))),
        named,
        Path(os.path.abspath(os.path.expanduser(data_raw))),
    )


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Прочитать окружение сервера. Связи с базой здесь не проверяется."""
    src = os.environ if env is None else env
    tokens = _parse_tokens(src.get(MCP_TOKENS_VAR) or "")
    tenants = tuple(sorted(set(tokens.values())))
    store, checklist_tenants, data_dir = _parse_checklist(src, tenants)
    return Settings(
        tokens=tokens,
        tenants=tenants,
        host=_parse_host(src.get(MCP_HOST_VAR) or ""),
        port=_parse_port(src.get(MCP_PORT_VAR) or ""),
        checklist_store=store,
        checklist_tenants=checklist_tenants,
        data_dir=data_dir,
    )


def resolve_tenant(settings: Settings, header: str | None) -> str:
    """Заголовок `Authorization` → код арендатора. Незнакомый токен — отказ.

    **Схема `Bearer` обязательна** (T116). Голый токен без схемы сервер
    принимал осознанно, брешью это не было — но инструменты, маскирующие
    секреты в логах, ищут именно образец `Bearer <токен>`, а голое значение
    заголовка записывают как есть. Сервер и сам объявляет схему в
    `WWW-Authenticate` при отказе: принимать при этом что-то ещё означало бы
    два вида годного доступа, из которых второй нигде не описан. Отказ при
    этом называет ожидаемый вид заголовка — иначе забытая схема неотличима от
    неверного токена, и человек чинит не то.

    Отказ не называет ни предъявленный токен, ни живых арендаторов: он уходит
    и в лог, и клиенту, а перечислять по нему партнёров не должен никто.
    """
    raw = (header or "").strip()
    scheme, _, rest = raw.partition(" ")
    if raw and scheme.lower() != _BEARER:
        raise AuthError(
            "Токен предъявлен без схемы Bearer: ожидается заголовок "
            "«Authorization: Bearer <токен>». Голый токен не принимается — "
            "инструменты маскируют в логах именно этот образец"
        )
    token = rest.strip()
    if not token:
        raise AuthError(
            "Запрос без токена по схеме Bearer: ожидается заголовок "
            "«Authorization: Bearer <токен>». Доступ к проверкам закрыт личным токеном"
        )
    tenant = settings.tenant_for(token)
    if tenant is None:
        raise AuthError("Токен не опознан. Доступ к проверкам закрыт личным токеном")
    return tenant
