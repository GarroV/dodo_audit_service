"""Блок `db`. Контракт — `docs/forge/blocks/db.md`.

Postgres принимает уже завершённую проверку (D027, D053); идущая проверка
остаётся файлом (`domain`, решение D007). Никто, кроме этого блока, не ходит
в базу — импорт `psycopg` или прямых запросов из других блоков не по адресу.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .errors import ConfigError, DbError, PushError
from .models import InspectionRow

# `apply_migrations` (src.db.migrate) и `check_environment` (src.db.config)
# сюда намеренно не попадают: это операционные функции наката и диагностики
# окружения, а не часть контракта блока (`docs/forge/blocks/db.md`). Импорт
# `python -m src.db.migrate` их и так найдёт напрямую; тащить их в пакетный
# `__init__` означало бы каждому импорту `src.db` тянуть psycopg заранее.
#
# `push_inspection` и `list_inspections`, наоборот, — часть контракта, но
# импортируются лениво через `__getattr__` (PEP 562), а не сразу здесь: любой
# импорт `src.db.<что угодно>` сперва выполняет этот файл целиком, а жадный
# `from .push import push_inspection` тянул бы `psycopg` даже для теста
# `units.py`/`fingerprint.py`, которому база не нужна вовсе. Проверено: без
# отложенной загрузки сбор `tests/` падает целиком в окружении без psycopg —
# даже для файлов, которые его не используют.
if TYPE_CHECKING:
    from .push import push_inspection as push_inspection
    from .queries import list_inspections as list_inspections

__all__ = [
    "ConfigError",
    "DbError",
    "InspectionRow",
    "PushError",
    "list_inspections",
    "push_inspection",
]

_LAZY = {
    "push_inspection": (".push", "push_inspection"),
    "list_inspections": (".queries", "list_inspections"),
}


def __getattr__(name: str) -> Any:
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = target
    import importlib

    module = importlib.import_module(module_name, __name__)
    return getattr(module, attr_name)
