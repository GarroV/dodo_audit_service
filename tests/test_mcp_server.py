"""T095: вход MCP-сервера — кого пускаем и чьи проверки отдаём.

Здесь проверяется дверь, а не инструменты (те — в `test_mcp_tools.py`).
Главное её свойство: **арендатор берётся из токена и ниоткуда больше**. Тот же
самый запрос, посланный двумя разными токенами, обязан вернуть две разные
истории; аргумент `tenant`, подсунутый в вызов, обязан получить отказ, а не
быть тихо проигнорированным — тихо проигнорированный он однажды перестанет
игнорироваться, и никто этого не заметит.

Запросы идут по настоящему HTTP через настоящий сокет, а не в обход
обработчика: проверка авторизации живёт именно в HTTP-слое, и вызов функции
напрямую её бы не задел.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from conftest import requires_data, requires_db

from src.mcp.catalogue import KIND_CHECKLIST, TOOLS
from src.mcp.config import MIN_TOKEN_LENGTH, Settings
from src.mcp.rpc import (
    CODE_INVALID_PARAMS,
    CODE_INVALID_REQUEST,
    CODE_METHOD_NOT_FOUND,
    CODE_PARSE_ERROR,
    SERVER_NAME,
)
from src.mcp.server import MAX_BODY_BYTES, build_server

ТОКЕН_А = "a" * MIN_TOKEN_LENGTH
ТОКЕН_Б = "b" * MIN_TOKEN_LENGTH
АРЕНДАТОР_А = "партнёр-а"
АРЕНДАТОР_Б = "партнёр-б"

ЗОНЫ = ("hot_kitchen", "cold_kitchen", "dough")


@pytest.fixture
def сервер() -> Iterator[str]:
    """Поднятый на случайном порту сервер с двумя арендаторами. Отдаёт адрес.

    Порт нулевой — его выбирает система: фиксированный номер столкнулся бы с
    соседней рабочей копией или с забытым процессом, и тест падал бы по
    причине, к проверяемому свойству отношения не имеющей.
    """
    settings = Settings(
        tokens={ТОКЕН_А: АРЕНДАТОР_А, ТОКЕН_Б: АРЕНДАТОР_Б},
        tenants=(АРЕНДАТОР_А, АРЕНДАТОР_Б),
        host="127.0.0.1",
        port=0,
    )
    httpd: ThreadingHTTPServer = build_server(settings)
    поток = threading.Thread(target=httpd.serve_forever, daemon=True)
    поток.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}/"
    finally:
        httpd.shutdown()
        httpd.server_close()
        поток.join(timeout=5)


def _запрос(
    адрес: str,
    тело: bytes | str,
    *,
    токен: str | None = ТОКЕН_А,
    метод: str = "POST",
) -> tuple[int, str]:
    """Сырой HTTP-запрос к серверу. Возвращает код ответа и тело строкой."""
    payload = тело.encode("utf-8") if isinstance(тело, str) else тело
    заголовки = {"Content-Type": "application/json"}
    if токен is not None:
        заголовки["Authorization"] = f"Bearer {токен}"
    # S310: схема здесь всегда http на свою же петлю — адрес собирает фикстура,
    # снаружи он не приходит.
    request = urllib.request.Request(адрес, data=payload, headers=заголовки, method=метод)  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=10) as ответ:  # noqa: S310
            return int(ответ.status), ответ.read().decode("utf-8")
    except urllib.error.HTTPError as отказ:
        return int(отказ.code), отказ.read().decode("utf-8")


def _rpc(адрес: str, метод: str, параметры: dict[str, Any] | None = None, **kw: Any) -> Any:
    """Вызов JSON-RPC. Возвращает разобранный ответ целиком."""
    сообщение: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": метод}
    if параметры is not None:
        сообщение["params"] = параметры
    код, тело = _запрос(адрес, json.dumps(сообщение), **kw)
    assert код == 200, f"ожидался 200, пришло {код}: {тело[:200]}"
    return json.loads(тело)


def _вызов(адрес: str, инструмент: str, аргументы: dict[str, Any], **kw: Any) -> Any:
    return _rpc(адрес, "tools/call", {"name": инструмент, "arguments": аргументы}, **kw)


def _данные(ответ: dict[str, Any]) -> Any:
    """Полезная нагрузка вызова инструмента: JSON внутри текстового блока."""
    return json.loads(ответ["result"]["content"][0]["text"])


def _проверка(chat_id: int, *, арендатор: str, точка: str, находок: int = 1) -> str:
    from src.db.push import push_inspection
    from src.domain import add_finding, start_inspection

    start_inspection(
        chat_id, unit=точка, kind="Плановая", report_lang="ru", tenant=арендатор, date="2026-08-15"
    )
    for номер in range(находок):
        add_finding(chat_id, code="CLN05", level="D1", zone=ЗОНЫ[номер], text="нагар на печи")
    return push_inspection(chat_id)


# --- дверь -------------------------------------------------------------------


#: Токен в заголовке HTTP обязан быть латиницей: заголовки кодируются
#: latin-1, и кириллический токен упал бы в клиенте, не дойдя до сервера.
@pytest.mark.parametrize("токен", [None, "no-such-token-but-long-enough-xx"])
def test_без_годного_токена_сервер_не_отдаёт_ничего(сервер: str, токен: str | None) -> None:
    """DoD блока: без токена сервер не отдаёт ничего — ни списка инструментов."""
    код, тело = _запрос(сервер, json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}))
    assert код == 200  # здоровый случай, чтобы отказ ниже был отличим от поломки

    код, тело = _запрос(
        сервер, json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}), токен=токен
    )

    assert код == 401
    assert "list_inspections" not in тело, "перечень инструментов — тоже выдача"


def test_отказ_не_называет_ни_токена_ни_арендаторов(сервер: str) -> None:
    """По тексту отказа не должно перечисляться, какие партнёры вообще есть."""
    чужой = ТОКЕН_Б[:-1] + "z"
    _, тело = _запрос(
        сервер, json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}), токен=чужой
    )

    assert ТОКЕН_А not in тело
    assert АРЕНДАТОР_А not in тело
    assert АРЕНДАТОР_Б not in тело


def test_читающий_сервер_не_отвечает_на_get(сервер: str) -> None:
    """Просматриваемой снаружи поверхности у сервера нет: только JSON-RPC."""
    код, _ = _запрос(сервер, b"", метод="GET")

    assert код == 405


def test_слишком_большое_тело_отклоняется_по_размеру(сервер: str) -> None:
    """Читающий сервер не обязан принимать мегабайты — иначе память кончится."""
    код, _ = _запрос(сервер, b"x" * (MAX_BODY_BYTES + 1))

    assert код == 413


def test_нечитаемая_длина_тела_это_отказ(сервер: str) -> None:
    """Заголовок с длиной не числом иначе уронил бы обработчик трейсбеком.

    Запрос идёт сырым сокетом: `urllib` такой заголовок собрать не даст.
    """
    import socket
    from urllib.parse import urlparse

    адрес = urlparse(сервер)
    assert адрес.hostname is not None and адрес.port is not None
    запрос = (
        f"POST / HTTP/1.1\r\nHost: {адрес.hostname}\r\n"
        f"Authorization: Bearer {ТОКЕН_А}\r\n"
        f"Content-Length: сколько-то\r\n\r\n"
    ).encode()
    with socket.create_connection((адрес.hostname, адрес.port), timeout=10) as сокет:
        сокет.sendall(запрос)
        ответ = сокет.recv(4096).decode("utf-8", "replace")

    assert "400" in ответ.splitlines()[0]


# --- арендатор берётся из токена ---------------------------------------------


@requires_data
@requires_db
def test_один_и_тот_же_запрос_разными_токенами_даёт_разные_истории(
    сервер: str, domain_env: Path, db_env: str
) -> None:
    """Главная проверка входа: чей токен — того и проверки.

    Запрос дословно одинаковый; различается только заголовок авторизации.
    """
    id_а = _проверка(301, арендатор=АРЕНДАТОР_А, точка="Белград-1")
    id_б = _проверка(302, арендатор=АРЕНДАТОР_Б, точка="Ниш-1")

    свои_а = _данные(_вызов(сервер, "list_inspections", {}, токен=ТОКЕН_А))
    свои_б = _данные(_вызов(сервер, "list_inspections", {}, токен=ТОКЕН_Б))

    assert [строка["id"] for строка in свои_а["inspections"]] == [id_а]
    assert [строка["id"] for строка in свои_б["inspections"]] == [id_б]
    assert свои_а["tenant"] == АРЕНДАТОР_А
    assert свои_б["tenant"] == АРЕНДАТОР_Б


@requires_data
@requires_db
def test_подсунутый_арендатор_это_отказ_а_не_тихо_проигнорированный_аргумент(
    сервер: str, domain_env: Path, db_env: str
) -> None:
    """Дефект, ради которого блок делался осторожно (T110).

    Аргумент `tenant`, назвавший соседа, обязан получить отказ. Тихо
    отброшенный, он выглядел бы работающей защитой ровно до того дня, когда
    кто-нибудь добавит его в список принимаемых аргументов «для гибкости».
    """
    _проверка(303, арендатор=АРЕНДАТОР_Б, точка="Ниш-1")

    ответ = _вызов(сервер, "list_inspections", {"tenant": АРЕНДАТОР_Б}, токен=ТОКЕН_А)

    assert "error" in ответ, f"аргумент tenant проехал молча: {ответ}"
    assert ответ["error"]["code"] == CODE_INVALID_PARAMS
    assert "tenant" in json.dumps(ответ, ensure_ascii=False)
    assert АРЕНДАТОР_Б not in json.dumps(ответ["error"].get("data", ""), ensure_ascii=False)


@requires_data
@requires_db
def test_незнакомый_аргумент_это_отказ_а_не_выдача_без_фильтра(
    сервер: str, domain_env: Path, db_env: str
) -> None:
    """Опечатка в имени фильтра иначе вернула бы всю сеть под видом одной точки."""
    _проверка(304, арендатор=АРЕНДАТОР_А, точка="Белград-1")
    _проверка(305, арендатор=АРЕНДАТОР_А, точка="Ниш-1")

    ответ = _вызов(сервер, "list_inspections", {"unit_name": "Белград-1"}, токен=ТОКЕН_А)

    assert "error" in ответ
    assert ответ["error"]["code"] == CODE_INVALID_PARAMS


# --- протокол ----------------------------------------------------------------


def test_рукопожатие_называет_сервер_и_версию_протокола(сервер: str) -> None:
    ответ = _rpc(сервер, "initialize", {"protocolVersion": "2024-11-05", "capabilities": {}})

    assert ответ["result"]["serverInfo"]["name"] == SERVER_NAME
    assert ответ["result"]["protocolVersion"] == "2024-11-05", (
        "версия клиента поддержана — отвечаем ею же"
    )
    assert "tools" in ответ["result"]["capabilities"]


def test_перечень_инструментов_отдаётся_целиком(сервер: str) -> None:
    """Перечень един для всех и от настроек не зависит.

    Инструменты методики видны и тому, кому она не открыта, а отказ приходит
    при вызове и объясняет себя (`test_mcp_checklist_access.py`). Перечень,
    зависящий от прав, сказал бы агенту «такого инструмента нет», и человек
    пошёл бы искать причину в коде вместо `.env`.
    """
    ответ = _rpc(сервер, "tools/list")

    имена = {инструмент["name"] for инструмент in ответ["result"]["tools"]}
    assert имена == {
        # проверки — только чтение (T095, T119)
        "list_inspections",
        "unit_history",
        "network_summary",
        "get_inspection",
        "findings_by_unit",
        # методика — чтение версий и правка (T098), закрыта отдельной настройкой
        "checklist_versions",
        "checklist_items",
        "checklist_item",
        "add_checklist_item",
        "edit_checklist_item",
        "remove_checklist_item",
        "restore_checklist_item",
        "add_zone",
        "remove_zone",
        "publish_checklist_version",
    }
    assert all("inputSchema" in инструмент for инструмент in ответ["result"]["tools"])


def test_уведомление_остаётся_без_ответа(сервер: str) -> None:
    """По JSON-RPC на сообщение без `id` отвечать нельзя — клиент этого не ждёт."""
    код, тело = _запрос(
        сервер, json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
    )

    assert код == 202
    assert тело.strip() == ""


def test_неразбираемое_тело_это_ошибка_разбора(сервер: str) -> None:
    код, тело = _запрос(сервер, "{это не json")

    assert код == 200
    assert json.loads(тело)["error"]["code"] == CODE_PARSE_ERROR


@pytest.mark.parametrize(
    "сообщение",
    [
        {"id": 1, "method": "ping"},  # нет версии протокола
        {"jsonrpc": "1.0", "id": 1, "method": "ping"},  # чужая версия
        {"jsonrpc": "2.0", "id": 1},  # нет метода
        {"jsonrpc": "2.0", "id": 1, "method": 42},  # метод не строка
        [1, 2, 3],  # вообще не объект
    ],
)
def test_кривое_сообщение_это_отказ_с_кодом_протокола(сервер: str, сообщение: object) -> None:
    код, тело = _запрос(сервер, json.dumps(сообщение))

    assert код == 200
    assert json.loads(тело)["error"]["code"] == CODE_INVALID_REQUEST


def test_неизвестный_метод_это_отказ(сервер: str) -> None:
    ответ = _rpc(сервер, "tools/execute")

    assert ответ["error"]["code"] == CODE_METHOD_NOT_FOUND


def test_неизвестный_инструмент_это_отказ(сервер: str) -> None:
    ответ = _вызов(сервер, "delete_everything", {})

    assert ответ["error"]["code"] == CODE_INVALID_PARAMS
    assert "delete_everything" in json.dumps(ответ, ensure_ascii=False)


def test_проверки_остаются_только_на_чтение(сервер: str) -> None:
    """Запись появилась (T098), но ровно в методику — и ни в одну проверку.

    Проверку заводит и завершает аудитор через бота, а в базу её кладёт слив
    (T093). Инструмент MCP, который писал бы проверки или находки, означал бы
    запись в отчёт партнёру мимо человека, — а «модель предлагает, фиксирует
    человек» держится не обещанием.

    Проверяется не по списку имён, а по виду инструмента: у всего, что не
    объявлено методикой, обработчик обязан лежать в модуле чтения проверок,
    где записи нет вовсе.
    """
    ответ = _rpc(сервер, "tools/list")
    объявленные = {инструмент["name"] for инструмент in ответ["result"]["tools"]}

    for spec in TOOLS:
        assert spec.name in объявленные
        if spec.kind == KIND_CHECKLIST:
            continue
        assert spec.handler.__module__ == "src.mcp.tools", spec.name
        for запрещённое in ("create", "update", "delete", "insert", "push", "set_", "edit"):
            assert запрещённое not in spec.name


# --- отказ инструмента виден спрашивающему -----------------------------------


def test_недоступная_база_видна_как_ошибка_а_не_как_пустая_выдача(
    сервер: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Пустой ответ означал бы «проверок нет», а на деле их не смогли прочитать."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://nouser@127.0.0.1:1/nodb?connect_timeout=2")

    ответ = _вызов(сервер, "network_summary", {})

    assert ответ["result"]["isError"] is True
    текст = ответ["result"]["content"][0]["text"]
    assert "no inspections" not in текст.lower(), "отказ чтения не должен читаться как «пусто»"


def test_кривой_аргумент_инструмента_это_отказ(сервер: str) -> None:
    ответ = _вызов(сервер, "unit_history", {"unit": ""})

    assert ответ["result"]["isError"] is True or "error" in ответ
