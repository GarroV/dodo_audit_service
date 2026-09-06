"""T256: смоук доступа снаружи — что он обязан считать доступом.

Инструмент существует ради одного: чтобы «проверено» больше не означало
«контейнер поднят». Сегодняшняя поломка выглядела здоровой со всех сторон —
бот выдавал правильную строку, контейнеры были зелёными, — и не работала ни с
одной машины. Значит, у самого смоука есть ровно два способа оказаться
бесполезным, и оба проверяются здесь:

* **согласиться проверять петлю.** Тогда он проверяет машину, на которой
  запущен, всегда зелен и не значит ничего;
* **принять за доступ «что-то ответило».** За туннелем может отвечать страница
  ошибки самого туннеля, посредник или чужой сервис — все они доступны по HTTPS
  и все выглядят работающими.

Проверяется настоящим запуском инструмента подпроцессом против настоящего
сервера и настоящих подделок под него: разбор аргументов, чтение окружения и
коды возврата — часть того, что чинится, а не обвязка вокруг.
"""

from __future__ import annotations

import http.server
import json
import subprocess
import sys
import threading
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from src.mcp.config import Settings
from src.mcp.server import build_server

ROOT = Path(__file__).resolve().parent.parent
СМОУК = ROOT / "tools" / "mcp_outside.py"

ТОКЕН = "y" * 32
АРЕНДАТОР = "партнёр"


def _поднять(httpd: ThreadingHTTPServer) -> Iterator[str]:
    поток = threading.Thread(target=httpd.serve_forever, daemon=True)
    поток.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}/"
    finally:
        httpd.shutdown()
        httpd.server_close()
        поток.join(timeout=5)


@pytest.fixture
def сервер() -> Iterator[str]:
    """Настоящий MCP-сервер на случайном порту. Отдаёт адрес."""
    settings = Settings(tokens={ТОКЕН: АРЕНДАТОР}, tenants=(АРЕНДАТОР,), host="127.0.0.1", port=0)
    yield from _поднять(build_server(settings))


def _подделка(код: int, имя: str, тело: bytes = b"") -> type[http.server.BaseHTTPRequestHandler]:
    """Не наш сервер на том же адресе: свой код, своё имя, своё тело."""

    class Подделка(http.server.BaseHTTPRequestHandler):
        server_version = имя
        sys_version = ""

        def do_POST(self) -> None:
            self.send_response(код)
            self.send_header("Content-Length", str(len(тело)))
            self.end_headers()
            if тело:
                self.wfile.write(тело)

        def log_message(self, _fmt: str, *_args: object) -> None:
            return

    return Подделка


@pytest.fixture
def страница_туннеля() -> Iterator[str]:
    """Туннель поднят, а за ним никого: отвечает он сам, и отвечает 502."""
    yield from _поднять(ThreadingHTTPServer(("127.0.0.1", 0), _подделка(502, "cloudflare")))


@pytest.fixture
def чужой_за_туннелем() -> Iterator[str]:
    """Дверь заперта, но за ней не наш сервер: 401 отдаёт кто-то другой.

    Площадка общая, а туннель настраивается в панели Cloudflare на адрес —
    промах в порту привёл бы ровно сюда: отказ есть, доступ выглядит запертым,
    а сервера проверок за ним нет.
    """
    yield from _поднять(ThreadingHTTPServer(("127.0.0.1", 0), _подделка(401, "cloudflare")))


def _подменённый() -> type[http.server.BaseHTTPRequestHandler]:
    """Дверь заперта, имя наше — а `initialize` подписан не нами."""
    ответ = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "result": {"serverInfo": {"name": "other-mcp"}}}
    ).encode("utf-8")
    настоящий = f"Bearer {ТОКЕН}"

    class Подменённый(http.server.BaseHTTPRequestHandler):
        server_version = "dodo-audit-mcp"
        sys_version = ""

        def do_POST(self) -> None:
            свой = self.headers.get("Authorization") == настоящий
            тело = ответ if свой else b""
            self.send_response(200 if свой else 401)
            self.send_header("Content-Length", str(len(тело)))
            self.end_headers()
            if тело:
                self.wfile.write(тело)

        def log_message(self, _fmt: str, *_args: object) -> None:
            return

    return Подменённый


@pytest.fixture
def подменённый_сервер() -> Iterator[str]:
    yield from _поднять(ThreadingHTTPServer(("127.0.0.1", 0), _подменённый()))


@pytest.fixture
def посредник() -> Iterator[str]:
    """Отдаёт 200 на что угодно, в том числе на выдуманный токен."""
    ответ = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}).encode("utf-8")
    yield from _поднять(ThreadingHTTPServer(("127.0.0.1", 0), _подделка(200, "proxy", ответ)))


def строка(вывод: str, начало: str) -> str:
    """Строка вывода про конкретную проверку.

    Искать слово по всему выводу нельзя, и это выяснилось порчей: снятая
    проверка петли оставляла набор зелёным — вердикт всё равно был красным, но
    уже из-за TLS, а слово «петля» оставалось в строке, помеченной OK. Тест,
    который так проверяет, ловит не то, что думает.
    """
    подходящие = [s for s in вывод.splitlines() if s.startswith(начало)]
    assert подходящие, f"в выводе нет строки про «{начало}»:\n{вывод}"
    return подходящие[0]


def прогон(
    адрес: str | None,
    токен: str | None = ТОКЕН,
    *,
    self_test: bool = True,
    переменная: str = "DODO_MCP_URL",
) -> subprocess.CompletedProcess[str]:
    env = {"PATH": "/usr/bin:/bin"}
    if адрес is not None:
        env[переменная] = адрес
    if токен is not None:
        env["DODO_MCP_TOKEN"] = токен
    аргументы = [sys.executable, str(СМОУК)]
    if self_test:
        аргументы.append("--self-test")
    return subprocess.run(  # noqa: S603 — аргументы собираем сами, ввода извне нет
        аргументы,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=120,
    )


# --- петля проверкой не считается -------------------------------------------


def test_петля_без_самопроверки_это_провал(сервер: str) -> None:
    """Главное свойство инструмента: он отказывается проверять сам себя.

    Ровно этот адрес стоял на площадке в строке, которую бот раздавал людям.
    Смоук, согласный его проверить, был бы зелёным — и бесполезным.
    """
    r = прогон(сервер, self_test=False)
    assert r.returncode == 1, r.stdout
    вывод = строка(r.stdout, "адрес ведёт наружу")
    assert "ПРОВАЛ" in вывод, вывод
    assert "петля" in вывод, вывод
    assert "СНАРУЖИ НЕДОСТУПЕН" in r.stdout


def test_петля_по_http_не_проходит_проверку_tls(сервер: str) -> None:
    r = прогон(сервер, self_test=False)
    вывод = строка(r.stdout, "TLS")
    assert "ПРОВАЛ" in вывод, вывод
    assert "открытым текстом" in вывод, вывод


def test_самопроверка_называет_себя_не_проверкой_снаружи(сервер: str) -> None:
    """Зелёная самопроверка не имеет права читаться как «доступен снаружи»."""
    r = прогон(сервер)
    assert r.returncode == 0, r.stdout
    assert "САМОПРОВЕРКА" in r.stdout
    assert "ДОСТУПЕН СНАРУЖИ" not in r.stdout


# --- «что-то ответило» доступом не является ---------------------------------


def test_живой_сервер_с_верным_токеном_это_доступ(сервер: str) -> None:
    r = прогон(сервер)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "подключение работает" in r.stdout


def test_страница_ошибки_туннеля_доступом_не_считается(страница_туннеля: str) -> None:
    """Туннель поднят, за ним никого — снаружи это выглядит рабочим адресом."""
    r = прогон(страница_туннеля)
    assert r.returncode == 1
    вывод = строка(r.stdout, "дверь заперта")
    assert "ПРОВАЛ" in вывод, вывод
    assert "502" in вывод, вывод


def test_посредник_отдающий_двести_на_всё_доступом_не_считается(посредник: str) -> None:
    """Открытая дверь ловится раньше, чем дело доходит до настоящего токена."""
    r = прогон(посредник)
    assert r.returncode == 1
    вывод = строка(r.stdout, "дверь заперта")
    assert "ПРОВАЛ" in вывод, вывод


def test_чужой_сервер_за_туннелем_доступом_не_считается(чужой_за_туннелем: str) -> None:
    """Отказ 401 доказывает запертую дверь, но не то, ЧЬЯ она."""
    r = прогон(чужой_за_туннелем)
    assert r.returncode == 1, r.stdout
    вывод = строка(r.stdout, "за туннелем наш сервер")
    assert "ПРОВАЛ" in вывод, вывод


def test_сервер_подписавшийся_чужим_именем_доступом_не_считается(
    подменённый_сервер: str,
) -> None:
    """Последняя дверь: ответ 200 засчитывается только с нашим `serverInfo`."""
    r = прогон(подменённый_сервер)
    assert r.returncode == 1, r.stdout
    вывод = строка(r.stdout, "подключение работает")
    assert "ПРОВАЛ" in вывод, вывод
    assert "other-mcp" in вывод, вывод


def test_неверный_токен_доступом_не_считается(сервер: str) -> None:
    """Сервер тот, дверь заперта — а этому человеку не открывают."""
    r = прогон(сервер, токен="c" * 32)
    assert r.returncode == 1
    вывод = строка(r.stdout, "подключение работает")
    assert "ПРОВАЛ" in вывод, вывод


# --- проверять нечем — это не «зелено» --------------------------------------


def test_без_адреса_смоук_не_зеленеет() -> None:
    r = прогон(None)
    assert r.returncode == 2, r.stdout
    assert "не назван адрес" in r.stderr


def test_без_токена_смоук_не_зеленеет(сервер: str) -> None:
    """Без токена доказать, что подключение РАБОТАЕТ, нечем."""
    r = прогон(сервер, токен=None)
    assert r.returncode == 2, r.stdout
    assert "DODO_MCP_TOKEN" in r.stderr


def test_адрес_берётся_из_той_же_переменной_которую_печатает_бот(сервер: str) -> None:
    """По умолчанию проверяется ровно та строка, которую бот раздаёт людям."""
    r = прогон(сервер, переменная="BOT_MCP_URL")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "адрес взят из BOT_MCP_URL" in r.stdout


# --- токен не печатается ----------------------------------------------------


@pytest.mark.parametrize("токен", [ТОКЕН, "d" * 32])
def test_токен_не_попадает_в_вывод(сервер: str, токен: str) -> None:
    """Вывод смоука кладут в переписку, а токен из переписки пришлось бы менять."""
    r = прогон(сервер, токен=токен)
    assert токен not in r.stdout
    assert токен not in r.stderr


def test_токен_с_кириллицей_это_отказ_а_не_трейсбек(сервер: str) -> None:
    """Человеческая ошибка при вставке не имеет права выглядеть поломкой доступа.

    Заголовки HTTP кодируются latin-1, и токен с кириллицей или «умной»
    кавычкой не отправится вовсе. Поймано прогоном: смоук падал трейсбеком на
    собственном выдуманном токене, не дойдя до вердикта.
    """
    r = прогон(сервер, токен="токен-с-кириллицей-подставленный-по-ошибке")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "Traceback" not in r.stderr
    assert "заголовке HTTP" in r.stdout


# --- копия имени сервера не разошлась ---------------------------------------


def test_имя_нашего_сервера_у_смоука_не_разошлось_с_живым_ответом(сервер: str) -> None:
    import urllib.request

    sys.path.insert(0, str(ROOT / "tools"))
    import mcp_outside

    запрос = urllib.request.Request(  # noqa: S310 — адрес собрала фикстура, своя же петля
        сервер,
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}).encode("utf-8"),
        headers={"Authorization": f"Bearer {ТОКЕН}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(запрос, timeout=10) as ответ:  # noqa: S310
        имя = json.loads(ответ.read())["result"]["serverInfo"]["name"]

    assert имя == mcp_outside.SERVER_NAME, (
        "смоук сверяет serverInfo с устаревшей копией имени сервера"
    )
