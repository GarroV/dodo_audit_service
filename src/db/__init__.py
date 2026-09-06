"""Блок `db`. Контракт — `docs/forge/blocks/db.md`.

Postgres принимает уже завершённую проверку (D027, D053); идущая проверка
остаётся файлом (`domain`, решение D007). Никто, кроме этого блока, не ходит
в базу — импорт `psycopg` или прямых запросов из других блоков не по адресу.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .errors import (
    ConfigError,
    DbError,
    PushError,
    RetractionError,
    StorageError,
    VersionMismatchError,
)
from .models import FindingRow, InfoRow, InspectionDetail, InspectionRow

# `apply_migrations` (src.db.migrate) и `check_environment` (src.db.config)
# сюда намеренно не попадают: это операционные функции наката и диагностики
# окружения, а не часть контракта блока (`docs/forge/blocks/db.md`). Импорт
# `python -m src.db.migrate` их и так найдёт напрямую; тащить их в пакетный
# `__init__` означало бы каждому импорту `src.db` тянуть psycopg заранее.
#
# Слив и чтение (`push_inspection`, `list_inspections`, `get_inspection`,
# `findings_by_unit`), наоборот, — часть контракта, но импортируются лениво
# через `__getattr__` (PEP 562), а не сразу здесь: любой
# импорт `src.db.<что угодно>` сперва выполняет этот файл целиком, а жадный
# `from .push import push_inspection` тянул бы `psycopg` даже для теста
# `units.py`/`fingerprint.py`, которому база не нужна вовсе. Проверено: без
# отложенной загрузки сбор `tests/` падает целиком в окружении без psycopg —
# даже для файлов, которые его не используют.
if TYPE_CHECKING:
    from .directory import Unit as Unit
    from .directory import list_units as list_units
    from .directory import resolve_unit as resolve_unit
    from .directory import upsert_unit as upsert_unit
    from .photos import upload_photos as upload_photos
    from .push import push_inspection as push_inspection
    from .queries import findings_by_unit as findings_by_unit
    from .queries import get_inspection as get_inspection
    from .queries import list_inspections as list_inspections
    from .retract import Retraction as Retraction
    from .retract import retract_inspection as retract_inspection

__all__ = [
    "ConfigError",
    "DbError",
    "FindingRow",
    "InfoRow",
    "InspectionDetail",
    "InspectionRow",
    "PushError",
    "Retraction",
    "RetractionError",
    "StorageError",
    "Unit",
    "VersionMismatchError",
    "findings_by_unit",
    "get_inspection",
    "list_inspections",
    "list_units",
    "push_inspection",
    "resolve_unit",
    "retract_inspection",
    "upload_photos",
    "upsert_unit",
]

_LAZY = {
    "Unit": (".directory", "Unit"),
    "list_units": (".directory", "list_units"),
    "resolve_unit": (".directory", "resolve_unit"),
    "upsert_unit": (".directory", "upsert_unit"),
    "upload_photos": (".photos", "upload_photos"),
    "push_inspection": (".push", "push_inspection"),
    "list_inspections": (".queries", "list_inspections"),
    "get_inspection": (".queries", "get_inspection"),
    "findings_by_unit": (".queries", "findings_by_unit"),
    "retract_inspection": (".retract", "retract_inspection"),
    "Retraction": (".retract", "Retraction"),
}


def __getattr__(name: str) -> Any:
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = target
    import importlib

    module = importlib.import_module(module_name, __name__)
    return getattr(module, attr_name)
