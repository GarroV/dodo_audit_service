"""Чтение уже слитых проверок. Только `select` — решений и расчётов здесь нет.

Дополняет `push_inspection` (T093) собственным способом проверить слив, не
трогая psql руками; полноценное чтение поверх базы для агента — задача T095
(блок `mcp`), эта функция не подменяет её.
"""

from __future__ import annotations

from typing import Any

import psycopg

from .config import check_environment
from .errors import DbError
from .models import InspectionRow
from .units import normalize_unit_name

# Оба запроса печатают один и тот же список колонок целиком (не собирают его
# из общего куска строкой): динамическая сборка текста SQL — ровно то, что
# ловит S608, и здесь ей взяться неоткуда не по обещанию, а по устройству кода.
_LIST_ALL_SQL = """
select
    i.id, i.tenant_code, u.name, i.chat_id, i.kind, i.inspection_date,
    i.report_lang, i.checklist_version, i.pct, i.grade,
    (select count(*) from findings f where f.inspection_id = i.id),
    i.pushed_at
from inspections i
join units u on u.id = i.unit_id
order by i.pushed_at desc
"""

_LIST_BY_UNIT_SQL = """
select
    i.id, i.tenant_code, u.name, i.chat_id, i.kind, i.inspection_date,
    i.report_lang, i.checklist_version, i.pct, i.grade,
    (select count(*) from findings f where f.inspection_id = i.id),
    i.pushed_at
from inspections i
join units u on u.id = i.unit_id
where u.name_normalized = %s
order by i.pushed_at desc
"""


def _row_to_inspection(row: Any) -> InspectionRow:
    """Строка курсора → `InspectionRow`.

    Тип строки психкопг не даёт статически по позиции колонки — `row: Any`
    здесь ровно на этой границе, а не расползается по модулю: дальше в коде
    типы снова конкретные.
    """
    return InspectionRow(
        id=str(row[0]),
        tenant_code=str(row[1]),
        unit_name=str(row[2]),
        chat_id=int(row[3]),
        kind=str(row[4]),
        inspection_date=row[5],
        report_lang=str(row[6]),
        checklist_version=str(row[7]),
        pct=float(row[8]),
        grade=str(row[9]),
        findings_count=int(row[10]),
        pushed_at=row[11].isoformat(),
    )


def list_inspections(unit: str | None = None) -> list[InspectionRow]:
    """Слитые проверки, новые сначала. `unit` фильтрует по точному имени точки."""
    settings = check_environment()
    try:
        with psycopg.connect(settings.dsn) as conn, conn.cursor() as cur:
            if unit is None:
                cur.execute(_LIST_ALL_SQL)
            else:
                cur.execute(_LIST_BY_UNIT_SQL, (normalize_unit_name(unit),))
            rows = cur.fetchall()
    except psycopg.Error as exc:
        raise DbError(f"Не удалось прочитать список проверок ({type(exc).__name__})") from exc
    return [_row_to_inspection(row) for row in rows]
