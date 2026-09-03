"""Точка запуска сервера, отделённая от `__main__` ради проверяемости.

В самом `__main__.py` остаётся только подстановка `.env` и вызов отсюда: тот
файл выполняется при импорте целиком, вместе с `load_dotenv`, и проверить его
тестом означало бы на каждом прогоне подсовывать всему набору настоящие
значения из `.env` рабочей копии.
"""

from __future__ import annotations

import sys

from .config import load_settings
from .errors import McpError
from .server import serve


def main() -> int:
    """Прочитать окружение и обслуживать запросы. Ошибка настроек — не трейсбек.

    Трейсбек на старте показал бы пути на машине и содержимое настроек; чтобы
    поднять сервер, человеку нужна одна строка о том, чего не хватает.
    """
    try:
        settings = load_settings()
    except McpError as отказ:
        print(f"[mcp] не поднялся: {отказ}", file=sys.stderr)
        return 1
    serve(settings)
    return 0
