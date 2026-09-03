"""Чтение уже слитых проверок. Только `select` — решений и расчётов здесь нет.

Дополняет `push_inspection` (T093) собственным способом проверить слив, не
трогая psql руками; полноценное чтение поверх базы для агента — задача T095
(блок `mcp`), эта функция не подменяет её.

**Арендатор — обязательный параметр, а не фильтр** (T110). Схема стала
мультиарендной миграцией `0002`, а эта точка чтения оставалась общей на всех:
выборка отдавала проверки всех арендаторов сразу и без предела. Пока арендатор
в продукте один, течь нечему — но читает отсюда MCP-сервер, который мы сами
даём в руки агенту партнёра, и необязательный фильтр там однажды не передадут.
Поэтому у арендатора нет значения по умолчанию: вызов без него не проходит
вовсе, вместо того чтобы молча отдать чужую историю.
"""

from __future__ import annotations

from typing import Any

import psycopg

from .config import check_environment
from .errors import DbError
from .models import InspectionRow
from .units import normalize_unit_name

#: Сколько проверок отдаётся, если предел не назвали. Сотня — это и есть
#: «история точки глазами человека»: больше за один запрос не читают ни в
#: отчёте, ни через агента, а безграничная выдача на сети из сотен пиццерий
#: означает вычитать всю таблицу по случайному вопросу.
DEFAULT_LIMIT = 100

#: Потолок, выше которого предел перестаёт быть пределом. Без него
#: `limit=10_000_000` — тот же полный проход, только записанный так, будто
#: ограничение есть.
MAX_LIMIT = 1000

# Оба запроса печатают один и тот же список колонок целиком (не собирают его
# из общего куска строкой): динамическая сборка текста SQL — ровно то, что
# ловит S608, и здесь ей взяться неоткуда не по обещанию, а по устройству кода.
#
# Точка присоединяется составной ссылкой `(tenant_code, id)` — той самой, что
# завела миграция `0002`. Так соединение физически не может подтянуть точку
# другого арендатора, даже если фильтр ниже когда-нибудь потеряется.
_LIST_ALL_SQL = """
select
    i.id, i.tenant_code, u.name, i.chat_id, i.kind, i.inspection_date,
    i.report_lang, i.checklist_version, i.pct, i.grade,
    (select count(*) from findings f where f.inspection_id = i.id),
    i.pushed_at
from inspections i
join units u on u.tenant_code = i.tenant_code and u.id = i.unit_id
where i.tenant_code = %(tenant)s
order by i.pushed_at desc
limit %(limit)s
"""

# Фильтр по точке идёт по обеим колонкам составного уникального индекса
# `units (tenant_code, name_normalized)`. Ведущая колонка — арендатор: без неё
# индекс не работал бы вовсе, а одноимённые точки двух арендаторов («Белград-1»
# есть и у управляющей компании, и у партнёра) склеились бы в одну историю.
_LIST_BY_UNIT_SQL = """
select
    i.id, i.tenant_code, u.name, i.chat_id, i.kind, i.inspection_date,
    i.report_lang, i.checklist_version, i.pct, i.grade,
    (select count(*) from findings f where f.inspection_id = i.id),
    i.pushed_at
from inspections i
join units u on u.tenant_code = i.tenant_code and u.id = i.unit_id
where i.tenant_code = %(tenant)s and u.name_normalized = %(unit)s
order by i.pushed_at desc
limit %(limit)s
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


def _require_tenant(tenant: str) -> str:
    """Непустой код арендатора — или явный отказ.

    Пустая строка не совпала бы ни с одним `tenant_code` и вернула бы пустой
    список: ошибка вызывающего выглядела бы как «проверок нет». Это худший из
    исходов — он не чинится, потому что его никто не замечает.
    """
    code = (tenant or "").strip()
    if not code:
        raise DbError(
            "Не задан арендатор, чьи проверки читаем. Выборка без него отдала бы "
            "либо чужие проверки, либо пустоту вместо ошибки — оба исхода тихие"
        )
    return code


def _require_limit(limit: int) -> int:
    """Предел выдачи в осмысленных границах — или явный отказ."""
    if limit < 1 or limit > MAX_LIMIT:
        raise DbError(
            f"Предел выдачи {limit} вне допустимого: ожидается от 1 до {MAX_LIMIT}. "
            f"Ноль вернул бы пустоту вместо отказа, а число сверху — тот же полный "
            f"проход по таблице под видом ограничения"
        )
    return limit


def list_inspections(
    *, tenant: str, unit: str | None = None, limit: int = DEFAULT_LIMIT
) -> list[InspectionRow]:
    """Слитые проверки одного арендатора, новые сначала.

    `tenant` обязателен и значения по умолчанию не имеет намеренно (T110):
    подстановка «default» выглядела бы работающей ровно до появления второго
    арендатора, а потом отдала бы агенту партнёра A историю партнёра B.

    `unit` фильтрует по точному названию точки в пределах того же арендатора
    (по тому же правилу нормализации, что и слив). Карту синонимов (T092) эта
    выборка не спрашивает: «БГ2» здесь не найдёт проверок «Белград 2» — при
    появлении потребителя это отдельная работа, а не молчаливое расширение.

    `limit` ограничивает выдачу всегда: без предела один вопрос агента вычитывал
    бы всю историю сети.
    """
    tenant_code = _require_tenant(tenant)
    rows_limit = _require_limit(limit)
    settings = check_environment()
    params: dict[str, object] = {"tenant": tenant_code, "limit": rows_limit}
    try:
        with psycopg.connect(settings.dsn) as conn, conn.cursor() as cur:
            if unit is None:
                cur.execute(_LIST_ALL_SQL, params)
            else:
                cur.execute(_LIST_BY_UNIT_SQL, {**params, "unit": normalize_unit_name(unit)})
            rows = cur.fetchall()
    except psycopg.Error as exc:
        raise DbError(f"Не удалось прочитать список проверок ({type(exc).__name__})") from exc
    return [_row_to_inspection(row) for row in rows]
