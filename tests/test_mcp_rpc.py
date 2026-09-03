"""T095: разбор сообщений MCP без поднятого сервера.

`handle` — чистая функция «сообщение → ответ», поэтому кривые формы вызова
проверяются здесь, а не через сокет: через сокет то же самое стоило бы
поднятого сервера на каждую строчку таблицы.

Арендатор сюда приходит уже разобранным из токена и в сообщении не участвует
— в этом файле он просто передаётся параметром, как это делает транспорт.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.mcp.cli import main
from src.mcp.config import MCP_TOKENS_VAR, MIN_TOKEN_LENGTH, Settings
from src.mcp.rpc import (
    CODE_INVALID_PARAMS,
    CODE_INVALID_REQUEST,
    LATEST_PROTOCOL,
    SUPPORTED_PROTOCOLS,
    handle,
)
from src.mcp.server import _Handler, build_server

АРЕНДАТОР = "партнёр-а"
ТОКЕН = "c" * MIN_TOKEN_LENGTH


def _вызов(инструмент: str, аргументы: Any) -> Any:
    return handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": инструмент, "arguments": аргументы},
        },
        tenant=АРЕНДАТОР,
    )


# --- вызов инструмента: форма аргументов -------------------------------------


@pytest.mark.parametrize("имя", [None, "", 42, {"тут": "объект"}])
def test_вызов_без_внятного_имени_инструмента_это_отказ(имя: Any) -> None:
    ответ = handle(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": имя}},
        tenant=АРЕНДАТОР,
    )

    assert ответ is not None
    assert ответ["error"]["code"] == CODE_INVALID_PARAMS


@pytest.mark.parametrize("аргументы", ["строка", 42, ["список"]])
def test_аргументы_не_объектом_это_отказ(аргументы: Any) -> None:
    ответ = _вызов("network_summary", аргументы)

    assert ответ is not None
    assert ответ["error"]["code"] == CODE_INVALID_PARAMS


def test_без_обязательного_аргумента_это_отказ() -> None:
    """`unit_history` без точки вернул бы историю неизвестно чего."""
    ответ = _вызов("unit_history", {})

    assert ответ is not None
    assert ответ["error"]["code"] == CODE_INVALID_PARAMS
    assert "unit" in ответ["error"]["message"]


@pytest.mark.parametrize(
    ("аргументы", "поле"),
    [
        ({"limit": "сто"}, "limit"),
        ({"limit": True}, "limit"),
        ({"unit": 42}, "unit"),
        ({"date_from": 20260801}, "date_from"),
    ],
)
def test_аргумент_не_того_типа_это_отказ(аргументы: dict[str, Any], поле: str) -> None:
    """Строка вместо числа доехала бы до сравнения в базе и упала там трейсбеком.

    `limit=true` проверяется отдельно: в питоне `bool` — наследник `int`, и
    проверка «это целое?» пропустила бы его молча.
    """
    ответ = _вызов("list_inspections", аргументы)

    assert ответ is not None
    assert ответ["error"]["code"] == CODE_INVALID_PARAMS
    assert поле in ответ["error"]["message"]


def test_имя_аргумента_названо_а_значение_нет() -> None:
    """Отказ уходит и в лог, и клиенту: значением может оказаться чужой код.

    Поэтому в тексте отказа перечисляются только имена аргументов.
    """
    ответ = _вызов("list_inspections", {"tenant": "партнёр-б"})

    assert ответ is not None
    сообщение = ответ["error"]["message"]
    assert "tenant" in сообщение
    assert "партнёр-б" not in сообщение


# --- конверт протокола -------------------------------------------------------


@pytest.mark.parametrize("версия", SUPPORTED_PROTOCOLS)
def test_знакомая_версия_протокола_подтверждается_ею_же(версия: str) -> None:
    ответ = handle(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": версия}},
        tenant=АРЕНДАТОР,
    )

    assert ответ is not None
    assert ответ["result"]["protocolVersion"] == версия


@pytest.mark.parametrize("версия", ["1999-01-01", None, 42])
def test_незнакомая_версия_протокола_получает_нашу_последнюю(версия: Any) -> None:
    """Промолчать в рукопожатии нельзя: клиент считал бы согласованным своё."""
    ответ = handle(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": версия}},
        tenant=АРЕНДАТОР,
    )

    assert ответ is not None
    assert ответ["result"]["protocolVersion"] == LATEST_PROTOCOL


def test_параметры_не_объектом_не_роняют_разбор() -> None:
    """Кривые `params` у метода без аргументов — не повод падать трейсбеком."""
    ответ = handle(
        {"jsonrpc": "2.0", "id": 1, "method": "ping", "params": "мимо"}, tenant=АРЕНДАТОР
    )

    assert ответ == {"jsonrpc": "2.0", "id": 1, "result": {}}


@pytest.mark.parametrize(
    "сообщение",
    [
        {"method": "notifications/initialized"},  # уведомление без версии протокола
        {"jsonrpc": "2.0"},  # уведомление без метода
        {"jsonrpc": "2.0", "method": 42},  # уведомление с методом не строкой
    ],
)
def test_кривое_уведомление_остаётся_без_ответа(сообщение: dict[str, Any]) -> None:
    """На сообщение без `id` по JSON-RPC не отвечают даже отказом."""
    assert handle(сообщение, tenant=АРЕНДАТОР) is None


def test_идентификатор_запроса_возвращается_как_пришёл() -> None:
    """Клиент сопоставляет ответ с запросом по `id`; подменённый — потерянный ответ."""
    ответ = handle({"jsonrpc": "2.0", "id": "абв-1", "method": "ping"}, tenant=АРЕНДАТОР)

    assert ответ is not None
    assert ответ["id"] == "абв-1"


def test_отказ_протокола_тоже_несёт_идентификатор() -> None:
    ответ = handle({"jsonrpc": "2.0", "id": 7}, tenant=АРЕНДАТОР)

    assert ответ is not None
    assert ответ["id"] == 7
    assert ответ["error"]["code"] == CODE_INVALID_REQUEST


# --- обработчик без настроек и запуск ----------------------------------------


def test_обработчик_без_настроек_отказывает_громко() -> None:
    """Без настроек обработчик не знает ни токенов, ни арендаторов.

    Проверка стоит явной, а не `assert`: с `-O` ассерты исчезают — то есть
    ровно в бою обработчик пошёл бы дальше без карты токенов.
    """
    обработчик = _Handler.__new__(_Handler)
    обработчик.server = object()  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="без настроек"):
        _ = обработчик._settings


def test_запуск_без_карты_токенов_это_отказ_а_не_трейсбек(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Открытый сервер хуже не поднявшегося, а трейсбек показал бы пути машины."""
    monkeypatch.delenv(MCP_TOKENS_VAR, raising=False)

    код = main()

    assert код == 1
    assert MCP_TOKENS_VAR in capsys.readouterr().err


def test_запуск_поднимает_сервер_на_настроенном_адресе(monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверяется, что `main` доводит разобранные настройки до сервера.

    Сам `serve` подменяется: настоящий встал бы в вечный цикл обслуживания.
    """
    monkeypatch.setenv(MCP_TOKENS_VAR, f"{АРЕНДАТОР}={ТОКЕН}")
    monkeypatch.setenv("MCP_PORT", "8266")
    поднято: list[Settings] = []
    monkeypatch.setattr("src.mcp.cli.serve", поднято.append)

    код = main()

    assert код == 0
    assert поднято[0].port == 8266
    assert поднято[0].tenant_for(ТОКЕН) == АРЕНДАТОР


def test_остановка_с_клавиатуры_закрывает_сервер(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Ctrl-C — штатный способ остановки, а не аварийный выход с трейсбеком.

    Сокет при этом обязан закрыться: иначе следующий запуск упрётся в занятый
    порт и человек будет искать чужой процесс, которого нет.
    """
    from src.mcp import server as модуль

    закрыт: list[bool] = []

    class ФальшивыйСервер:
        server_address = ("127.0.0.1", 8267)

        def serve_forever(self) -> None:
            raise KeyboardInterrupt

        def server_close(self) -> None:
            закрыт.append(True)

    monkeypatch.setattr(модуль, "build_server", lambda _: ФальшивыйСервер())
    settings = Settings(tokens={ТОКЕН: АРЕНДАТОР}, tenants=(АРЕНДАТОР,), host="127.0.0.1", port=0)

    модуль.serve(settings)

    assert закрыт == [True], "сокет остался открытым после остановки"
    assert "остановлен" in capsys.readouterr().out


def test_сервер_обслуживает_пока_его_не_остановят(monkeypatch: pytest.MonkeyPatch) -> None:
    """`serve` проверяется настоящим циклом обслуживания, а не подменой.

    Порт нулевой, поэтому его выбирает система: занять чужой этот тест не
    может ни при каком стечении обстоятельств.
    """
    import threading

    from src.mcp import server as модуль

    settings = Settings(tokens={ТОКЕН: АРЕНДАТОР}, tenants=(АРЕНДАТОР,), host="127.0.0.1", port=0)
    поднятые = []

    def запомнить(настройки: Settings) -> Any:
        httpd = build_server(настройки)
        поднятые.append(httpd)
        return httpd

    monkeypatch.setattr(модуль, "build_server", запомнить)
    поток = threading.Thread(target=модуль.serve, args=(settings,), daemon=True)
    поток.start()
    for _ in range(100):
        if поднятые:
            break
        поток.join(timeout=0.05)
    assert поднятые, "сервер не поднялся за отведённое время"

    поднятые[0].shutdown()
    поток.join(timeout=5)

    assert not поток.is_alive(), "цикл обслуживания не остановился"


# --- отказ на неучтённом сбое называет то, что делал, а не то, что делал раньше


def _запрос(инструмент: str, аргументы: Any) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": инструмент, "arguments": аргументы},
    }


def _текст_отказа(ответ: Any) -> str:
    return str(ответ["result"]["content"][0]["text"])


def test_сбой_чтения_говорит_про_чтение(monkeypatch: pytest.MonkeyPatch) -> None:
    def падает(**_: Any) -> Any:
        raise OSError("диск отвалился")

    monkeypatch.setattr("src.mcp.tools.list_inspections", падает)
    ответ = handle(_запрос("list_inspections", {}), tenant=АРЕНДАТОР)

    текст = _текст_отказа(ответ)
    assert "проверки" in текст, текст
    assert ответ["result"]["isError"] is True


def test_сбой_записи_методики_не_говорит_про_чтение_проверок(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Наследие read-only эпохи: текст врал про чтение проверок на записи.

    Гонка двух одинаковых правок отвечала «не удалось прочитать проверки» —
    и агент пересказал бы это человеку как отказ чтения, пока его правка молча
    терялась. Найдено разбором безопасности 03.09 живым запуском двух
    параллельных вызовов.
    """

    class ПадающееХранилище:
        def __getattr__(self, _: str) -> Any:
            def падает(**_kw: Any) -> Any:
                raise OSError("гонка записи")

            return падает

    ответ = handle(
        _запрос("checklist_versions", {}),
        tenant=АРЕНДАТОР,
        checklist=ПадающееХранилище(),
    )

    текст = _текст_отказа(ответ)
    assert "проверки" not in текст, f"отказ записи методики говорит про чтение проверок: {текст}"
    assert "методик" in текст, текст
    assert ответ["result"]["isError"] is True
