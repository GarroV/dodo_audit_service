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
from .findings import add_finding, attach_photo, drop_finding, edit_finding
from .models import ChecklistItem, Finding, Inspection, Score, Zone, ZoneScore
from .scoring import score
from .state import get_state, start_inspection

__all__ = [
    "ChecklistItem",
    "Finding",
    "Inspection",
    "Score",
    "Zone",
    "ZoneScore",
    "add_finding",
    "allowed_levels",
    "attach_photo",
    "check_environment",
    "checklist_version",
    "drop_finding",
    "edit_finding",
    "get_item",
    "get_state",
    "list_items",
    "list_zones",
    "score",
    "start_inspection",
]
