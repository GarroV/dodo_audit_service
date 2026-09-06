"""Смоук доступа к MCP-серверу С ЧУЖОЙ МАШИНЫ (задача T256, #211).

Отвечает на вопрос, на который «контейнер поднят» не отвечает: **может ли
человек, сидящий не на площадке, подключиться по той строке, которую выдаёт
бот**. Это не придирка к формулировке. Сегодня бот выдавал правильный доступ,
контейнеры были зелёными, а подключиться было нельзя ни с одной машины: сервер
не был запущен вовсе, а адрес в строке был петлёй — то есть указывал на машину
того, кто строку выполняет. Ни один из зелёных признаков этого не показал.

**Запускать с ДРУГОЙ машины, не с площадки.** С самой площадки прогон проверит
туннель, но не путь снаружи, и об этом сказано в выводе прямо.

    DODO_MCP_URL=<адрес туннеля> DODO_MCP_TOKEN=<токен> python3 tools/mcp_outside.py

Адрес берётся из `DODO_MCP_URL`, а не задан — из `BOT_MCP_URL`: по умолчанию
проверяется ровно та строка, которую бот раздаёт людям, а не отдельно
придуманный адрес. Имена переменных те же, что у моста `tools/mcp_bridge.sh`.

Что проверяется и почему именно это:

1. **Адрес — не петля.** Петля означает, что проверка проверила бы машину, на
   которой запущена, то есть саму себя. Это не строгость, а условие
   осмысленности всего дальнейшего.
2. **TLS.** Наружу выходим туннелем с TLS (решение D100). По `http://` токен
   доступа к истории проверок партнёров уехал бы открытым текстом.
3. **Без токена — отказ 401, и отвечает НАШ сервер.** Доказывает, что за
   туннелем действительно сервер проверок с запертой дверью, а не страница
   ошибки туннеля и не чужой процесс: страница ошибки тоже отвечает по HTTPS
   и тоже выглядит «доступной».
4. **Мусорный токен — отказ.** Ловит посредника, отдающего 200 на что угодно.
5. **Настоящий токен — 200 и наш `serverInfo`.** Единственная проверка,
   которая доказывает, что подключение РАБОТАЕТ, а не просто отвечает.

Токен нигде не печатается: ни в выводе, ни в отказе. Вывод этого прогона
кладут в переписку, а токен из переписки пришлось бы менять.

Коды возврата: 0 — доступен, 1 — нет, 2 — проверять нечего (не назван адрес
или токен).
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request

#: Имя, которым представляется наш сервер (`src/mcp/server.py`,
#: `_Handler.server_version`) и которым он подписывается в `initialize`
#: (`src/mcp/rpc.py`, `SERVER_NAME`). Копия строки — тест сверяет её с живым
#: ответом, чтобы расхождение поймал прогон, а не площадка.
SERVER_NAME = "dodo-audit-mcp"

#: Адрес: своя переменная, а не задана — та, которую печатает бот.
URL_VARS = ("DODO_MCP_URL", "BOT_MCP_URL")

#: Токен. Тот же, что у моста: человек уже держит его в окружении.
TOKEN_VAR = "DODO_MCP_TOKEN"  # noqa: S105 — имя переменной, не значение

#: Туннель через полмира — не локальная петля, и десяти секунд ему мало не
#: бывает только при настоящей поломке.
TIMEOUT_SEC = 15.0

OK = "OK"
FAIL = "ПРОВАЛ"
WARN = "ВНИМАНИЕ"


class Result:
    """Строка вывода: что проверяли, чем кончилось, считать ли провалом."""

    def __init__(self, name: str, mark: str, detail: str = "") -> None:
        self.name = name
        self.mark = mark
        self.detail = detail

    @property
    def failed(self) -> bool:
        return self.mark == FAIL

    def line(self) -> str:
        хвост = f" ({self.detail})" if self.detail else ""
        return f"{self.name:<34} {self.mark}{хвост}"


def _loopback(host: str) -> bool:
    """Указывает ли имя или адрес на машину того, кто спрашивает."""
    if host.lower() in {"localhost", "localhost.localdomain", "ip6-localhost"}:
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def address_results(url: str, *, self_test: bool) -> list[Result]:
    """Свойства самого адреса: он вообще способен вести на чужую машину?

    Проверяется до единого запроса. Прогон по петле выглядел бы удачным всегда,
    и именно так «проверка» превращается в самообман: сервер на машине
    проверяющего отвечает, вывод зелёный, а человек в другом городе по этой же
    строке не соединяется.
    """
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or ""
    результаты: list[Result] = []

    if _loopback(host):
        результаты.append(
            Result(
                "адрес ведёт наружу",
                # В самопроверке это не «хорошо», а «допущено ради проверки
                # самого инструмента»: строка обязана читаться как оговорка, а
                # не как подтверждение, иначе зелёный вывод однажды предъявят
                # вместо настоящей проверки.
                WARN if self_test else FAIL,
                "петля: этот адрес указывает на машину того, кто выполняет строку",
            )
        )
    elif not host:
        результаты.append(Result("адрес ведёт наружу", FAIL, "в адресе нет имени машины"))
    else:
        частный = False
        try:
            частный = ipaddress.ip_address(host.strip("[]")).is_private
        except ValueError:
            частный = False
        результаты.append(
            Result(
                "адрес ведёт наружу",
                WARN if частный else OK,
                "адрес частной сети: за её пределами он не работает" if частный else "",
            )
        )

    if parts.scheme == "https":
        результаты.append(Result("TLS", OK))
    else:
        результаты.append(
            Result(
                "TLS",
                WARN if self_test else FAIL,
                f"схема {parts.scheme or 'не названа'}: токен уехал бы открытым текстом",
            )
        )
    return результаты


def _post(url: str, token: str | None, body: bytes) -> tuple[int, dict[str, str], bytes] | str:
    """Один запрос. Возвращает `(код, заголовки, тело)` или строку с причиной.

    Отказ сети — не исключение наружу: смоук обязан назвать причину строкой и
    дойти до вердикта, а не оборваться трейсбеком посреди списка.
    """
    заголовки = {"Content-Type": "application/json"}
    if token is not None:
        заголовки["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=body, headers=заголовки, method="POST")  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SEC) as ответ:  # noqa: S310
            return int(ответ.status), dict(ответ.headers), ответ.read()
    except urllib.error.HTTPError as отказ:
        return int(отказ.code), dict(отказ.headers), отказ.read()
    except OSError as беда:
        # Сюда же приходит непроверенный сертификат: для нас это отказ доступа,
        # а не отдельный род событий.
        return str(беда)
    except UnicodeError:
        # Заголовки HTTP кодируются latin-1, и токен с кириллицей или «умной»
        # кавычкой не отправится вовсе. Это обычная человеческая ошибка при
        # вставке, и отвечать на неё трейсбеком посреди списка проверок нельзя:
        # человек прочтёт его как поломку доступа. Значение токена в ответе не
        # печатается — вывод смоука уезжает в переписку.
        return "токен содержит символы, которых в заголовке HTTP быть не может"


def _server_said(headers: dict[str, str]) -> str:
    return (headers.get("Server") or headers.get("server") or "").strip()


def reachability_results(url: str, token: str) -> list[Result]:
    """Живые запросы: заперта ли дверь, наш ли за ней сервер, пускает ли он.

    Порядок не случаен. Сначала отказ без токена — он доказывает, что отвечает
    сервер проверок, а не страница ошибки туннеля: страница ошибки тоже
    доступна по HTTPS и тоже выглядит как «работает». Потом мусорный токен —
    он ловит посредника, отдающего 200 на что угодно. И только потом
    настоящий: до него две предыдущие строки уже сказали, с кем мы говорим.
    """
    результаты: list[Result] = []

    без_токена = _post(url, None, b"")
    if isinstance(без_токена, str):
        результаты.append(Result("сервер отвечает", FAIL, без_токена))
        return результаты
    код, заголовки, _ = без_токена
    представился = _server_said(заголовки)
    if код != 401:
        результаты.append(
            Result(
                "дверь заперта",
                FAIL,
                f"на запрос без токена ответ {код}"
                + (f", отвечает {представился!r}" if представился else ""),
            )
        )
        return результаты
    результаты.append(Result("дверь заперта", OK, "без токена — 401"))

    if представился != SERVER_NAME:
        результаты.append(
            Result("за туннелем наш сервер", FAIL, f"заголовок Server = {представился!r}")
        )
        return результаты
    результаты.append(Result("за туннелем наш сервер", OK, f"Server = {SERVER_NAME}"))

    # Выдуманный токен — латиницей, и это не мелочь: заголовки HTTP кодируются
    # latin-1, и кириллица в них не отправляется вовсе. Проверено прогоном:
    # смоук падал трейсбеком на собственном мусорном токене, не дойдя до
    # вердикта, — то есть ровно так, как проверке делать нельзя.
    мусор = _post(url, f"not-a-real-token-{secrets.token_urlsafe(24)}", b"")
    if isinstance(мусор, str):
        результаты.append(Result("чужой токен не пускают", FAIL, мусор))
        return результаты
    if мусор[0] != 401:
        результаты.append(
            Result("чужой токен не пускают", FAIL, f"на выдуманный токен ответ {мусор[0]}")
        )
        return результаты
    результаты.append(Result("чужой токен не пускают", OK))

    вопрос = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}).encode("utf-8")
    свой = _post(url, token, вопрос)
    if isinstance(свой, str):
        результаты.append(Result("подключение работает", FAIL, свой))
        return результаты
    код, _, тело = свой
    if код != 200:
        результаты.append(Result("подключение работает", FAIL, f"ответ {код}"))
        return результаты
    try:
        ответ = json.loads(тело or b"null")
    except (UnicodeDecodeError, json.JSONDecodeError):
        результаты.append(Result("подключение работает", FAIL, "ответ не разбирается как JSON"))
        return результаты
    имя = ((ответ or {}).get("result") or {}).get("serverInfo", {}).get("name")
    if имя != SERVER_NAME:
        результаты.append(
            Result("подключение работает", FAIL, f"initialize отдал serverInfo.name = {имя!r}")
        )
        return результаты
    результаты.append(Result("подключение работает", OK, "initialize отвечает нашим serverInfo"))
    return результаты


def resolve_url(env: dict[str, str] | None = None) -> tuple[str, str]:
    """Адрес и имя переменной, из которой он взят. Пусто — значит не назван."""
    src = os.environ if env is None else env
    for имя in URL_VARS:
        значение = (src.get(имя) or "").strip()
        if значение:
            return значение, имя
    return "", ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="проверка самого инструмента на локальном сервере; проверкой снаружи НЕ является",
    )
    args = parser.parse_args(argv)

    url, откуда = resolve_url()
    if not url:
        print(
            f"смоук: не назван адрес — задай {URL_VARS[0]} (или {URL_VARS[1]}, тот же адрес, "
            f"что бот печатает людям)",
            file=sys.stderr,
        )
        return 2
    token = (os.environ.get(TOKEN_VAR) or "").strip()
    if not token:
        print(
            f"смоук: не назван {TOKEN_VAR}. Без токена доказать, что подключение работает, "
            f"нечем — а «отвечает» подключением не является",
            file=sys.stderr,
        )
        return 2

    if args.self_test:
        print("САМОПРОВЕРКА ИНСТРУМЕНТА — проверкой доступа снаружи НЕ является\n")
    else:
        print("Запускать с ЧУЖОЙ машины: с самой площадки этот прогон проверит туннель,")
        print("но не путь снаружи.\n")
    print(f"адрес взят из {откуда}\n")

    результаты = address_results(url, self_test=args.self_test)
    if not any(r.failed for r in результаты):
        результаты += reachability_results(url, token)
    for строка in результаты:
        print(строка.line())

    провал = any(r.failed for r in результаты)
    print()
    if args.self_test:
        итог = "КРАСНАЯ" if провал else "ЗЕЛЁНАЯ (снаружи не проверено)"
        print(f"САМОПРОВЕРКА {итог}")
    else:
        print("ДОСТУПЕН СНАРУЖИ" if not провал else "СНАРУЖИ НЕДОСТУПЕН")
    return 1 if провал else 0


if __name__ == "__main__":
    raise SystemExit(main())
