"""Соединение, брошенное клиентом, не должно держать сервер (T095).

Найдено разбором безопасности 03.09 живым запуском, две вещи сразу:

1. Заголовок без терминатора держал поток сервера **бесконечно** — и держал
   его ДО проверки токена, потому что заголовки разбираются раньше. То есть
   валидный токен для этого не нужен вовсе.
2. `Content-Length` на пять мегабайт при теле в два байта — то же самое, уже
   после авторизации.

Площадка общая, рядом живут чужие проекты: любой процесс на машине мог открыть
сколько угодно таких соединений и оставить без ответа всех арендаторов сразу.

Проверки здесь идут через **сырой сокет**, а не через `urllib`: клиентская
библиотека сама завершает заголовки, и через неё этот сценарий не выразить.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from http.server import ThreadingHTTPServer

import pytest

from src.mcp.config import Settings
from src.mcp.server import MAX_CONNECTIONS, SOCKET_TIMEOUT_SEC, build_server

ТОКЕН = "токен-для-предельных-проверок-24"
АРЕНДАТОР = "alpha"


@pytest.fixture
def адрес() -> Iterator[tuple[str, int]]:
    settings = Settings(tokens={ТОКЕН: АРЕНДАТОР}, tenants=(АРЕНДАТОР,), host="127.0.0.1", port=0)
    httpd: ThreadingHTTPServer = build_server(settings)
    поток = threading.Thread(target=httpd.serve_forever, daemon=True)
    поток.start()
    try:
        yield ("127.0.0.1", httpd.server_address[1])
    finally:
        httpd.shutdown()
        httpd.server_close()
        поток.join(timeout=5)


def test_таймаут_задан_и_конечен() -> None:
    """Предел обязан быть числом, а не «когда-нибудь»."""
    assert SOCKET_TIMEOUT_SEC > 0
    assert SOCKET_TIMEOUT_SEC < 120, (
        "таймаут длиннее двух минут держит поток почти так же долго, как его отсутствие"
    )


def test_предел_соединений_задан() -> None:
    assert MAX_CONNECTIONS > 0
    assert MAX_CONNECTIONS < 1000, "предел в тысячу соединений пределом не является"


def test_брошенный_заголовок_не_держит_поток_вечно(адрес: tuple[str, int]) -> None:
    """Клиент замолчал, не дописав заголовки, и токена не предъявлял.

    Ждём заметно дольше таймаута сервера, но конечное время: если предел не
    работает, тест упадёт по своему собственному, и это видно по сообщению.
    """
    хост, порт = адрес
    s = socket.create_connection((хост, порт), timeout=5)
    s.settimeout(SOCKET_TIMEOUT_SEC * 3)
    начало = time.perf_counter()
    try:
        s.sendall(b"POST / HTTP/1.1\r\nHost: 127.0.0.1\r\n")
        try:
            s.recv(4096)
        except TimeoutError:
            pytest.fail(
                f"сервер не отпустил брошенное соединение за {SOCKET_TIMEOUT_SEC * 3:g} с — "
                "поток занят клиентом, который не предъявил даже токена"
            )
    finally:
        s.close()
    прошло = time.perf_counter() - начало
    assert прошло < SOCKET_TIMEOUT_SEC * 3, f"отпущено за {прошло:.1f} с"


def test_обещанное_тело_которого_нет_не_держит_поток(адрес: tuple[str, int]) -> None:
    """`Content-Length` больше, чем прислано: чтение обязано прекратиться."""
    хост, порт = адрес
    s = socket.create_connection((хост, порт), timeout=5)
    s.settimeout(SOCKET_TIMEOUT_SEC * 3)
    try:
        s.sendall(
            b"POST / HTTP/1.1\r\nHost: 127.0.0.1\r\n"
            b"Content-Type: application/json\r\nContent-Length: 5000000\r\n\r\n{}"
        )
        try:
            s.recv(4096)
        except TimeoutError:
            pytest.fail("сервер ждёт обещанное тело бесконечно")
    finally:
        s.close()
