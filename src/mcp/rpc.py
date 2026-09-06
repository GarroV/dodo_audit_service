"""Разбор сообщений MCP: JSON-RPC 2.0 поверх одного POST-запроса.

Сетей и сокетов здесь нет — только чистое «сообщение → ответ», чтобы протокол
проверялся тестами без поднятого сервера. Транспорт живёт в `server.py`, и
там же авторизация: код арендатора приходит СЮДА уже разобранным из токена и
в аргументах вызова не участвует.

Форма взята с работающего `swarm-mcp` (D055): JSON-RPC по HTTP плюс мост
stdio↔HTTP на стороне клиента, без SDK и без своей библиотеки протокола.
Когда база переедет в Supabase (D061), этот же разбор переезжает в Edge
Function целиком — транспорт меняется, инструменты нет.

**Незнакомый аргумент — отказ, а не тихо отброшенное поле.** Это не
педантизм: аргумент `tenant`, назвавший чужого партнёра, обязан получить
внятный отказ, иначе однажды его добавят «для гибкости» в разбор, и никто не
заметит. Опечатка в имени фильтра (`unit_name` вместо `unit`) по той же
причине отказ, а не выдача по всей сети под видом одной точки.
"""

from __future__ import annotations

import json
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from .catalogue import KIND_CHECKLIST, KIND_RETRACTION, ToolSpec, as_list, find
from .checklist import Store
from .errors import McpError, ToolError

# Коды ошибок JSON-RPC 2.0. Свои коды не заводятся: клиент разбирает эти.
CODE_PARSE_ERROR = -32700
CODE_INVALID_REQUEST = -32600
CODE_METHOD_NOT_FOUND = -32601
CODE_INVALID_PARAMS = -32602
# Кода -32603 («внутренняя ошибка») здесь нет намеренно: отказ инструмента
# приходит спрашивающему как помеченный отказ в результате вызова, а не как
# ошибка протокола, — иначе агент видит «сервер сломался» вместо «прочитать
# проверки не удалось». Объявленная, но не используемая константа была бы
# мёртвым кодом.

SERVER_NAME = "dodo-audit-mcp"

#: Версии протокола MCP, с которыми сервер умеет разговаривать. Если клиент
#: назвал знакомую — отвечаем ею же, иначе своей последней: молча промолчать в
#: рукопожатии означало бы клиента, который считает согласованным своё.
SUPPORTED_PROTOCOLS = ("2024-11-05", "2025-03-26", "2025-06-18")
LATEST_PROTOCOL = SUPPORTED_PROTOCOLS[-1]

#: Соответствие типов JSON Schema питоновским. `bool` проверяется отдельно:
#: в питоне он наследник `int`, и `limit=true` иначе доехал бы до базы.
_TYPES: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "object": dict,
    "array": list,
}


def _version() -> str:
    """Версия продукта из метаданных пакета, а не переписанная сюда числом."""
    try:
        return version("dodo-audit-service")
    except PackageNotFoundError:  # pragma: no cover — пакет не установлен
        return "0"


def _result(request_id: Any, payload: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": payload}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    """Отказ протокола.

    В тексте называются только ИМЕНА аргументов, никогда их значения: значение
    может оказаться и чужим кодом арендатора, и куском данных проверки, а этот
    текст уходит и в лог, и клиенту.
    """
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _tool_text(text: str, *, failed: bool = False) -> dict[str, Any]:
    """Ответ инструмента в форме MCP: блок текста плюс отметка об отказе.

    Отказ инструмента приходит именно так, а не кодом протокола: спрашивающий
    обязан увидеть, ЧТО не получилось, — иначе «база не ответила» и «проверок
    нет» станут для него одним и тем же ответом.
    """
    payload: dict[str, Any] = {"content": [{"type": "text", "text": text}]}
    if failed:
        payload["isError"] = True
    return payload


def _check_arguments(spec: ToolSpec, arguments: dict[str, Any]) -> str | None:
    """Аргументы против объявленной схемы. Возвращает текст отказа или `None`."""
    schema = spec.input_schema
    declared = schema.get("properties")
    properties: dict[str, Any] = declared if isinstance(declared, dict) else {}
    required = schema.get("required")
    known = set(properties)
    unknown = sorted(set(arguments) - known)
    if unknown:
        return (
            f"Инструмент {spec.name} не принимает аргументы: {', '.join(unknown)}. "
            f"Известные: {', '.join(sorted(known)) or 'нет'}"
        )
    missing = sorted(
        name for name in (required if isinstance(required, list) else []) if name not in arguments
    )
    if missing:
        return f"Инструменту {spec.name} не хватает обязательных аргументов: {', '.join(missing)}"
    for name, value in arguments.items():
        property_schema = properties.get(name)
        expected = property_schema.get("type") if isinstance(property_schema, dict) else None
        python_type = _TYPES.get(expected) if isinstance(expected, str) else None
        # Свойство без объявленного типа не проверяется — не отвергается:
        # схема без `type` описывает аргумент, тип которого не назван, и
        # отказывать по ней означало бы решать за автора схемы.
        if python_type is not None:
            # `bool` в питоне наследник `int`, поэтому проверяется отдельно:
            # иначе `limit=true` прошёл бы как целое и доехал до базы.
            if isinstance(value, bool) and expected != "boolean":
                return f"Аргумент {name} инструмента {spec.name} ожидается типа {expected}"
            if not isinstance(value, python_type):
                return f"Аргумент {name} инструмента {spec.name} ожидается типа {expected}"
    return None


#: Отказ на инструмент методики без открытого доступа. Один текст на оба
#: случая — «не настроено» и «этому арендатору не открыто» — намеренно: разница
#: между ними ничем не помогает спрашивающему и рассказывает ему о настройках
#: сервера больше, чем нужно. Переменные названы, потому что читает этот отказ
#: тот же человек, который держит сервер у себя на петле, и без имён переменных
#: он пойдёт искать причину в коде.
CHECKLIST_CLOSED = (
    "Работа с методикой через агента для этого доступа не открыта. Методика одна на всю "
    "сеть, и правит её управляющая компания: доступ включается переменными "
    "MCP_CHECKLIST_STORE и MCP_CHECKLIST_TENANTS. Проверки при этом читаются как обычно"
)


#: Отказ на снятие проверки без открытого права. Один текст на оба случая —
#: «на сервере снятие не настроено» и «этому токену оно не открыто» — по той же
#: причине, что у методики: разница между ними спрашивающему ничем не поможет,
#: а о настройках сервера расскажет. Переменная названа, потому что читает
#: отказ тот же человек, который держит сервер у себя на петле.
#:
#: Про личность права сказано вслух: иначе владелец второго токена того же
#: арендатора решит, что доступ сломан, и пойдёт искать причину в коде.
RETRACTION_CLOSED = (
    "Снятие проверок для этого доступа не открыто. Право на снятие даётся не стороне, а "
    "конкретному токену: у одного арендатора токенов несколько, по человеку на токен, а "
    "снятие необратимо для партнёра. Открывается оно переменной MCP_RETRACTION_TOKENS. "
    "Проверки при этом читаются как обычно"
)


def _call_tool(
    params: dict[str, Any], *, tenant: str, checklist: Store | None, may_retract: bool
) -> dict[str, Any] | str:
    """Вызов инструмента. Строка в ответе — отказ протокола, словарь — результат.

    `checklist` — хранилище версий методики, и оно же признак права на неё:
    транспорт передаёт его, только если методика открыта ЭТОМУ арендатору.
    Заслон стоит здесь, у входа, рядом с границей арендаторов, а не в
    обработчиках: обработчик, забывший спросить о правах, был бы дырой,
    которую видно только чтением всех обработчиков подряд.
    """
    name = params.get("name")
    if not isinstance(name, str) or not name:
        return "Не назван инструмент: ожидается params.name"
    spec = find(name)
    if spec is None:
        return f"Неизвестный инструмент: {name}"
    arguments = params.get("arguments", {})
    if not isinstance(arguments, dict):
        return f"Аргументы инструмента {name} ожидаются объектом"
    if spec.kind == KIND_CHECKLIST and checklist is None:
        return _tool_text(CHECKLIST_CLOSED, failed=True)
    if spec.kind == KIND_RETRACTION and not may_retract:
        return _tool_text(RETRACTION_CLOSED, failed=True)
    refusal = _check_arguments(spec, arguments)
    if refusal is not None:
        return refusal
    прочее: dict[str, Any] = {"store": checklist} if spec.kind == KIND_CHECKLIST else {}
    try:
        payload = spec.handler(tenant=tenant, **прочее, **arguments)
    except ToolError as отказ:
        return _tool_text(str(отказ), failed=True)
    except McpError as отказ:
        # Сюда приходит и отказ методики (`ChecklistError`): правка, которую
        # движок не принял, — это отказ, а не результат с пометкой. Пометку
        # агент однажды перескажет человеку как «сделано».
        return _tool_text(str(отказ), failed=True)
    except Exception as отказ:
        # Широкий перехват намеренно: наружу уходит ТИП отказа, а не его текст
        # и не трейсбек. В тексте отказа драйвера базы может оказаться строка
        # подключения, а в трейсбеке — пути на машине; и то и другое ушло бы
        # агенту партнёра. Тихо проглотить отказ при этом нельзя — он приходит
        # спрашивающему помеченным как отказ.
        # Текст разводится по виду инструмента. Раньше он говорил про чтение
        # проверок всегда — наследие времени, когда блок был только чтением
        # (T095). После появления правки методики это стало враньём: гонка
        # двух одинаковых правок отвечала «не удалось прочитать проверки» на
        # ЗАПИСИ, и агент пересказал бы это человеку как отказ чтения, пока
        # его правка молча терялась. Проверено разбором безопасности 03.09
        # живым запуском двух параллельных вызовов.
        # Снятие разведено с чтением по той же причине, по какой когда-то
        # развели чтение и правку методики: «не удалось выполнить проверки»
        # на СНЯТИИ агент перескажет человеку как отказ чтения, и тот пойдёт
        # чинить историю, пока отозванный документ остаётся в ней висеть.
        что, чего_нет = {
            KIND_CHECKLIST: (
                "правку методики",
                "Это отказ записи, а не отклонение правки движком",
            ),
            KIND_RETRACTION: (
                "снятие проверки",
                "Это отказ базы или хранилища, а не отказ в снятии; сняли или нет — "
                "видно повторным вызовом, он же доделает начатое",
            ),
        }.get(spec.kind, ("проверки", "Это отказ чтения, а не отсутствие проверок"))
        return _tool_text(
            f"Не удалось выполнить {что} ({type(отказ).__name__}). {чего_нет}",
            failed=True,
        )
    return _tool_text(json.dumps(payload, ensure_ascii=False, indent=2))


def handle(
    message: object, *, tenant: str, checklist: Store | None = None, may_retract: bool = False
) -> dict[str, Any] | None:
    """Разобранное сообщение JSON-RPC → ответ. `None` — уведомление, ответа нет.

    `tenant` приходит от транспорта, разобравшего личный токен, и в сообщении
    не участвует ни в каком виде. `checklist` — оттуда же: транспорт передаёт
    хранилище версий методики, только если она открыта этому арендатору, а
    `None` по умолчанию означает, что до задачи T098 сервер отвечал только на
    вопросы о проверках, и таким он и остаётся, пока методику не открыли явно.
    """
    if not isinstance(message, dict):
        return _error(None, CODE_INVALID_REQUEST, "Ожидается объект JSON-RPC 2.0")
    request_id = message.get("id")
    is_notification = "id" not in message
    if message.get("jsonrpc") != "2.0":
        if is_notification:
            return None
        return _error(request_id, CODE_INVALID_REQUEST, "Ожидается jsonrpc: 2.0")
    method = message.get("method")
    if not isinstance(method, str):
        if is_notification:
            return None
        return _error(request_id, CODE_INVALID_REQUEST, "Не назван метод")
    if is_notification:
        # Уведомления по JSON-RPC ответа не получают — ни успешного, ни
        # ошибочного. Ответ на `notifications/initialized` выбивает клиента.
        return None
    params = message.get("params")
    params = params if isinstance(params, dict) else {}

    if method == "initialize":
        asked = params.get("protocolVersion")
        agreed = asked if asked in SUPPORTED_PROTOCOLS else LATEST_PROTOCOL
        return _result(
            request_id,
            {
                "protocolVersion": agreed,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": _version()},
            },
        )
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(request_id, {"tools": as_list()})
    if method == "tools/call":
        outcome = _call_tool(params, tenant=tenant, checklist=checklist, may_retract=may_retract)
        if isinstance(outcome, str):
            return _error(request_id, CODE_INVALID_PARAMS, outcome)
        return _result(request_id, outcome)
    return _error(request_id, CODE_METHOD_NOT_FOUND, f"Метод не поддерживается: {method}")
