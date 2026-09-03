"""Транспорт MCP-сервера: HTTP на локальной петле, личный токен в заголовке.

Форма повторяет работающий `swarm-mcp` (D055): клиент говорит по stdio,
крохотный мост перекладывает строку в POST, сервер отвечает JSON-RPC. Разница
одна и она осознанная — сервер поднимается **локально**, а не Edge Function:
база проверок сегодня локальная (D061), наружу блок не публикуется, и
переезд в Edge Function будет сменой транспорта при тех же инструментах.

Слушаем только петлю (проверяет `config._parse_host`): площадка общая, рядом
живут чужие проекты, и открытый в сеть порт отдал бы им проверки партнёров.

Авторизация здесь, до разбора сообщения: **арендатор получается из токена**,
и ниже по коду его подменить нечем.
"""

from __future__ import annotations

import json
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .config import Settings, resolve_tenant
from .errors import AuthError
from .rpc import CODE_PARSE_ERROR, handle

#: Больше мегабайта читающему серверу присылать нечего: вопросы к базе
#: короткие. Предел стоит до чтения тела, иначе он не предел, а пожелание.
MAX_BODY_BYTES = 1 << 20

#: Ответ на уведомление JSON-RPC: принято, тела нет.
_ACCEPTED = HTTPStatus.ACCEPTED


class _Server(ThreadingHTTPServer):
    """`ThreadingHTTPServer`, знающий свои настройки.

    Настройки висят на сервере, а не на классе обработчика: обработчик
    создаётся заново на каждый запрос, а класс — общий на процесс, и настройки
    на нём означали бы, что два сервера в одном процессе (а это ровно тесты)
    делят одну карту токенов.
    """

    daemon_threads = True

    def __init__(self, address: tuple[str, int], settings: Settings) -> None:
        self.settings = settings
        super().__init__(address, _Handler)


class _Handler(BaseHTTPRequestHandler):
    """Один POST — одно сообщение JSON-RPC."""

    server_version = "dodo-audit-mcp"
    #: Своя версия питона в заголовке ответа не печатается: подсказывать
    #: версию интерпретатора тому, кто стучится, незачем.
    sys_version = ""

    @property
    def _settings(self) -> Settings:
        """Настройки сервера, которому принадлежит этот обработчик.

        Проверка типа явная, а не `assert`: без настроек обработчик не знает
        ни токенов, ни арендаторов, и продолжать ему нельзя — а `assert`
        исчезает при запуске с `-O`, то есть ровно в бою.
        """
        settings = getattr(self.server, "settings", None)
        if not isinstance(settings, Settings):
            raise RuntimeError("Обработчик MCP поднят без настроек сервера")
        return settings

    # Имя `format` задано сигнатурой базового класса и переименованию не подлежит.
    def log_message(self, format: str, *args: Any) -> None:
        """Короткая строка в stderr вместо стандартного лога.

        Ни заголовков, ни тела: в заголовке едет токен, в теле — вопросы про
        проверки партнёров (конституция, «в логах — идентификаторы, а не
        содержимое»).
        """
        print(f"[mcp] {self.command} {format % args}", file=sys.stderr)

    def _send(self, status: HTTPStatus, payload: dict[str, Any] | None = None) -> None:
        body = b"" if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        if body:
            self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if status == HTTPStatus.UNAUTHORIZED:
            self.send_header("WWW-Authenticate", 'Bearer realm="dodo-audit-mcp"')
        self.end_headers()
        if body:
            self.wfile.write(body)

    # Имена do_GET/do_POST задаёт BaseHTTPRequestHandler — он ищет их по имени.
    def do_GET(self) -> None:
        """Просматриваемой поверхности у сервера нет: только JSON-RPC по POST."""
        self._send(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "Сервер отвечает только на POST"})

    def do_POST(self) -> None:
        try:
            tenant = resolve_tenant(self._settings, self.headers.get("Authorization"))
        except AuthError as отказ:
            # Ни имени арендатора, ни токена: по отказу нельзя перечислить,
            # какие партнёры вообще заведены на этом сервере.
            self._send(HTTPStatus.UNAUTHORIZED, {"error": str(отказ)})
            return

        raw = self.headers.get("Content-Length") or "0"
        try:
            length = int(raw)
        except ValueError:
            self._send(HTTPStatus.BAD_REQUEST, {"error": "Не разобрана длина тела запроса"})
            return
        if length > MAX_BODY_BYTES:
            # Ограниченный кусок всё же вычитывается и выбрасывается, а
            # соединение закрывается. Отказ без единого чтения выглядит для
            # клиента не отказом, а оборванным соединением: он ещё пишет тело
            # и получает broken pipe вместо внятного 413. Читаем не больше
            # того же предела — заявленный гигабайт дочитывать никто не будет.
            self.rfile.read(min(length, MAX_BODY_BYTES + 1))
            self.close_connection = True
            self._send(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {"error": f"Тело больше {MAX_BODY_BYTES} байт: вопрос к базе таким не бывает"},
            )
            return

        body = self.rfile.read(length) if length > 0 else b""
        try:
            message = json.loads(body or b"null")
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send(
                HTTPStatus.OK,
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": CODE_PARSE_ERROR, "message": "Тело запроса не разбирается"},
                },
            )
            return

        answer = handle(message, tenant=tenant)
        if answer is None:
            self._send(_ACCEPTED)
            return
        self._send(HTTPStatus.OK, answer)


def build_server(settings: Settings) -> ThreadingHTTPServer:
    """Собрать сервер на заданном адресе, но не запускать.

    Отдельно от `serve`, чтобы тест мог поднять сервер на порту, который
    выберет система (`port=0`): фиксированный номер в тестах сталкивается с
    соседней рабочей копией и с забытым процессом.
    """
    return _Server((settings.host, settings.port), settings)


def serve(settings: Settings) -> None:
    """Поднять сервер и обслуживать запросы до Ctrl-C.

    Печатается только адрес: токен в лог не попадает никогда.
    """
    httpd = build_server(settings)
    # Порт берётся у поднятого сокета, а не из настроек: при `port=0` его
    # выбирает система, и напечатанный из настроек ноль был бы ложью.
    port = int(httpd.server_address[1])
    print(
        f"[mcp] слушаю http://{settings.host}:{port}/ — "
        f"арендаторов заведено: {len(settings.tenants)}"
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("[mcp] остановлен")
    finally:
        httpd.server_close()
