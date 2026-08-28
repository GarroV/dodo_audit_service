"""Блок `domain` — единственная точка доступа к предметной области.

Контракт — `docs/forge/blocks/domain.md`. Через него ходят остальные блоки:
чек-лист, зоны, состояние проверки, оценка. Никто, кроме этого блока, не
открывает `inspection.json` и не запускает движок — импорт `engine` из `bot`,
`recognize` и `report` роняет прогон (контракт `engine-not-imported` в
`lint-imports`).

Оценка не считается здесь ни в каком виде: `score()` разбирает вывод
`audit.py score`, ставки вычетов живут в `data/scoring.json`.
"""

from __future__ import annotations

from .checklist import allowed_levels, checklist_version, get_item, list_items, list_zones
from .config import check_environment
from .models import ChecklistItem, Finding, Inspection, Score, Zone, ZoneScore

__all__ = [
    "ChecklistItem",
    "Finding",
    "Inspection",
    "Score",
    "Zone",
    "ZoneScore",
    "allowed_levels",
    "check_environment",
    "checklist_version",
    "get_item",
    "list_items",
    "list_zones",
]
