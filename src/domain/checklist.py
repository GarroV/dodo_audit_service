"""Чтение методики: пункты чек-листа, зоны, допустимые классы, версия набора.

Файлы читаются из `AUDIT_DATA_DIR` при каждом обращении — методику подкладывают
томом снаружи, и кеш в памяти означал бы, что после её замены бот продолжает
работать по старой до перезапуска. Разбор строк повторяет разбор движка (обойти
это нельзя: движок вызывается подпроцессом и своих структур наружу не отдаёт),
но никаких решений о вычетах и классах здесь нет — они остаются в движке.
"""

from __future__ import annotations

import csv
import hashlib
import re
from pathlib import Path

from .config import REQUIRED_DATA_FILES, Settings, check_environment
from .errors import ValidationError
from .models import ChecklistItem, Zone

#: Явная версия методики, если управляющая компания её проставила: «имя набора
#: плюс дата» из `docs/02-domain.md`. Файл необязательный.
VERSION_FILE = "checklist_version.txt"


def _text(row: dict[str, str | None], key: str) -> str:
    return (row.get(key) or "").strip()


def _rows(path: Path) -> list[dict[str, str | None]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _item(row: dict[str, str | None]) -> ChecklistItem:
    levels = [x.strip().upper() for x in re.split(r"[;,]", _text(row, "levels")) if x.strip()]
    zones = [z.strip() for z in _text(row, "zones").split(",") if z.strip()]
    try:
        days = int(float(_text(row, "days") or 0))
    except ValueError:
        days = 0
    return ChecklistItem(
        code=_text(row, "id"),
        kind=_text(row, "kind") or "violation",
        process_ru=_text(row, "process_ru"),
        process_en=_text(row, "process_en"),
        question_ru=_text(row, "question_ru"),
        question_en=_text(row, "question_en"),
        levels=levels,
        zones=zones,
        days=days,
    )


def _all_items(settings: Settings) -> list[ChecklistItem]:
    items: list[ChecklistItem] = []
    seen: set[str] = set()
    for row in _rows(settings.data_dir / "checklist.csv"):
        code = _text(row, "id")
        # Дубль кода движок пропускает с предупреждением в stderr; здесь то же
        # правило, иначе список пунктов и мнение движка разойдутся.
        if not code or code.startswith("#") or code in seen:
            continue
        seen.add(code)
        items.append(_item(row))
    return items


def list_items(zone: str | None = None, kind: str | None = None) -> list[ChecklistItem]:
    """Пункты чек-листа. `zone` — только применимые к зоне, `kind` — только этого вида.

    Служебные пункты (`aggregate`, `info`) не отфильтрованы намеренно: отсеивать
    их — работа разбора (`docs/03-recording-rules.md`, правило 8), а блоку
    методики не положено решать, что аудитору показывать.
    """
    settings = check_environment()
    items = _all_items(settings)
    if zone is not None:
        known = {z.code for z in _zones(settings)}
        if zone not in known:
            raise ValidationError(f"Нет зоны «{zone}». Доступны: {', '.join(sorted(known))}")
        items = [i for i in items if i.applies_to(zone)]
    if kind is not None:
        items = [i for i in items if i.kind == kind]
    return items


def _zones(settings: Settings) -> list[Zone]:
    zones = [
        Zone(
            code=_text(row, "code"),
            title_ru=_text(row, "name_ru"),
            title_en=_text(row, "name_en"),
            share_pct=float(_text(row, "share_pct") or 0),
        )
        for row in _rows(settings.data_dir / "zones.csv")
        if _text(row, "code")
    ]
    return zones


def list_zones() -> list[Zone]:
    """Справочник зон с долями. Доли считает движок, здесь они справочно."""
    return _zones(check_environment())


def get_item(code: str) -> ChecklistItem:
    """Пункт по коду. Неизвестный код — отказ: подобрать похожий блок не вправе."""
    settings = check_environment()
    wanted = code.strip().upper()
    for item in _all_items(settings):
        if item.code == wanted:
            return item
    raise ValidationError(f"Нет пункта «{code}» в чек-листе {settings.data_dir / 'checklist.csv'}")


def allowed_levels(code: str) -> list[str]:
    """Классы, допустимые для пункта. Список берётся из методики, не из кода."""
    return list(get_item(code).levels)


def checklist_version() -> str:
    """Версия методики, которая записывается в проверку.

    Проверка, посчитанная по одной версии, должна сходиться и через год, поэтому
    версия обязана меняться вместе с данными. Явное имя набора из
    `checklist_version.txt` важнее вычисленного; без файла берётся отпечаток
    самих данных — чек-листа, зон и ставок.
    """
    settings = check_environment()
    explicit = settings.data_dir / VERSION_FILE
    if explicit.is_file():
        for line in explicit.read_text(encoding="utf-8").splitlines():
            if line.strip():
                return line.strip()
    digest = hashlib.sha256()
    for name in REQUIRED_DATA_FILES:
        digest.update((settings.data_dir / name).read_bytes())
    return f"local-{digest.hexdigest()[:12]}"
