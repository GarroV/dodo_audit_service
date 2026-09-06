"""T255: проба здоровья MCP-сервера — что именно она считает здоровьем.

Проба живёт в `docker-compose.yml` и решает, зелёный контейнер или красный.
Ошибиться она может двумя способами, и оба хуже её отсутствия:

* **сказать «здоров» про больного** — контейнер зелёный, подключиться нельзя.
  Ровно это и случилось на площадке: всё выглядело поднятым, а сервер не был
  запущен вовсе;
* **сказать «здоров» про ЧУЖОГО** — площадка общая, и на том же порту петли
  может оказаться сосед. Проба, радующаяся любому отказу, однажды порадуется
  не нашему серверу.

Поэтому здесь поднимается настоящий сервер и настоящие подделки под него, а
проба запускается подпроцессом — той же строкой, что стоит в описании стенда.
Вызов функции напрямую проверял бы не то, что выполняет docker.
"""

from __future__ import annotations

import http.server
import subprocess
import sys
import threading
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from src.mcp.config import DEFAULT_HOST, DEFAULT_PORT, MCP_HOST_VAR, MCP_PORT_VAR, Settings
from src.mcp.server import build_server

ROOT = Path(__file__).resolve().parent.parent
ПРОБА = ROOT / "tools" / "mcp_healthcheck.py"

ТОКЕН = "z" * 32
АРЕНДАТОР = "партнёр"


def _поднять(httpd: ThreadingHTTPServer) -> Iterator[int]:
    поток = threading.Thread(target=httpd.serve_forever, daemon=True)
    поток.start()
    try:
        yield int(httpd.server_address[1])
    finally:
        httpd.shutdown()
        httpd.server_close()
        поток.join(timeout=5)


@pytest.fixture
def сервер() -> Iterator[int]:
    """Настоящий MCP-сервер на случайном порту. Отдаёт порт."""
    settings = Settings(tokens={ТОКЕН: АРЕНДАТОР}, tenants=(АРЕНДАТОР,), host="127.0.0.1", port=0)
    yield from _поднять(build_server(settings))


def _подделка(код: int, имя: str) -> type[http.server.BaseHTTPRequestHandler]:
    """Чужой сервер на том же порту: свой код ответа и своё имя в `Server`."""

    class Подделка(http.server.BaseHTTPRequestHandler):
        server_version = имя
        sys_version = ""

        def do_POST(self) -> None:
            self.send_response(код)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, _fmt: str, *_args: object) -> None:
            return

    return Подделка


@pytest.fixture
def чужой_сервер() -> Iterator[int]:
    """Сосед по машине: отвечает 401, но представляется своим именем.

    Имя латиницей не для удобства: заголовки HTTP кодируются latin-1, и
    кириллическое имя не отправилось бы вовсе — подделка развалилась бы раньше,
    чем проба успела её не признать.
    """
    yield from _поднять(ThreadingHTTPServer(("127.0.0.1", 0), _подделка(401, "nginx")))


@pytest.fixture
def дверь_настежь() -> Iterator[int]:
    """Сервер, пускающий БЕЗ токена. Самая опасная поломка из возможных."""
    yield from _поднять(ThreadingHTTPServer(("127.0.0.1", 0), _подделка(200, "dodo-audit-mcp")))


def прогон(port: int | None) -> subprocess.CompletedProcess[str]:
    """Запустить пробу так, как её запускает docker: по имени файла."""
    env = {"PATH": "/usr/bin:/bin", MCP_HOST_VAR: "127.0.0.1"}
    if port is not None:
        env[MCP_PORT_VAR] = str(port)
    return subprocess.run(  # noqa: S603 — аргументы собираем сами, ввода извне нет
        [sys.executable, str(ПРОБА)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=60,
    )


def test_живой_сервер_проба_считает_здоровым(сервер: int) -> None:
    r = прогон(сервер)
    assert r.returncode == 0, f"живой сервер объявлен больным: {r.stderr}"


def test_сервера_нет_проба_называет_причину() -> None:
    """Порт, на котором никто не слушает, — это и есть сегодняшняя поломка."""
    # Порт занимаем и тут же отпускаем: свободный номер, выбранный системой,
    # надёжнее выдуманного — выдуманный однажды окажется чужим.
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _подделка(401, "неважно"))
    порт = int(httpd.server_address[1])
    httpd.server_close()

    r = прогон(порт)
    assert r.returncode == 1
    assert "нет ответа" in r.stderr, r.stderr


def test_чужой_сервер_на_том_же_порту_здоровьем_не_считается(чужой_сервер: int) -> None:
    """Отказ 401 сам по себе доказательством не является: площадка общая."""
    r = прогон(чужой_сервер)
    assert r.returncode == 1
    assert "не наш сервер" in r.stderr, r.stderr


def test_сервер_пускающий_без_токена_объявляется_больным(дверь_настежь: int) -> None:
    """Ответ 200 без токена — повод гасить сервер, а не признак здоровья."""
    r = прогон(дверь_настежь)
    assert r.returncode == 1
    assert "БЕЗ токена" in r.stderr, r.stderr


def test_проба_стучится_туда_же_где_слушает_сервер() -> None:
    """Умолчания у пробы и у сервера одни: второй список разошёлся бы молча."""
    sys.path.insert(0, str(ROOT / "tools"))
    import mcp_healthcheck  # импорт по месту: скрипт, не пакет

    assert mcp_healthcheck.probe_url({}) == f"http://{DEFAULT_HOST}:{DEFAULT_PORT}/"
    assert mcp_healthcheck.probe_url({MCP_HOST_VAR: "127.0.0.2", MCP_PORT_VAR: "9"}) == (
        "http://127.0.0.2:9/"
    )


def test_имя_нашего_сервера_у_пробы_не_разошлось_с_живым_ответом(сервер: int) -> None:
    """Копия строки в пробе сверяется с тем, чем сервер представляется на деле."""
    import urllib.error
    import urllib.request

    sys.path.insert(0, str(ROOT / "tools"))
    import mcp_healthcheck

    запрос = urllib.request.Request(f"http://127.0.0.1:{сервер}/", data=b"", method="POST")
    try:
        urllib.request.urlopen(запрос, timeout=10)  # noqa: S310
    except urllib.error.HTTPError as отказ:
        представился = отказ.headers.get("Server")
    else:  # pragma: no cover — сервер без токена не пускает, сюда не попасть
        pytest.fail("сервер пустил без токена")

    # `.strip()` не подгонка под ответ: `BaseHTTPRequestHandler` склеивает имя
    # сервера с версией питона через пробел, а версия у нас пустая намеренно
    # (её незачем сообщать стучащемуся) — отсюда хвостовой пробел в заголовке.
    # Проба обрезает его по той же причине.
    assert (представился or "").strip() == mcp_healthcheck.SERVER_HEADER, (
        "проба сверяет заголовок Server с устаревшей копией имени сервера"
    )
