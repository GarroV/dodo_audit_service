"""Типы, которыми блок `db` разговаривает наружу.

`InspectionRow` — сводка одной проверки для списка (контракт
`list_inspections` в `docs/forge/blocks/db.md`). Полный разбор находок сюда не
входит: это не задача блока в этой волне, только читаемая витрина того, что
уже слито.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class InspectionRow:
    """Одна строка списка проверок этого блока."""

    id: str
    tenant_code: str
    unit_name: str
    chat_id: int
    kind: str
    inspection_date: date
    report_lang: str
    checklist_version: str
    pct: float
    grade: str
    findings_count: int
    pushed_at: str
