"""Запуск MCP-сервера: `python -m src.mcp` (или `make mcp`).

`load_dotenv()` подставляет переменные из `.env` до чтения настроек — тем же
приёмом и по той же причине, что точка входа бота (`src/bot/__main__.py`):
конфигурация смотрит только `os.environ`, а класть туда файл иначе некому.

Сервер поднимается локально и наружу не публикуется: база проверок сегодня
локальная (D061), а доступ закрыт личным токеном из `MCP_TOKENS`.
"""

from __future__ import annotations  # pragma: no cover

from pathlib import Path  # pragma: no cover

from dotenv import load_dotenv  # pragma: no cover

# pragma: no cover ниже — тонкая обёртка: подстановка .env и вызов `main`,
# обе части проверены по отдельности (`tests/test_mcp_cli.py`). Тот же приём
# и та же причина, что у раннера миграций (`src/db/migrate.py`).
load_dotenv(Path(__file__).resolve().parents[2] / ".env")  # pragma: no cover

from .cli import main  # noqa: E402 -- окружение читается до настроек  # pragma: no cover

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
