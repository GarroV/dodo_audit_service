"""Каталог инструментов MCP: чем агент партнёра может спросить у базы проверок.

Декларация, а не логика: имя, текст для агента, JSON Schema аргументов и
ссылка на обработчик из `src.mcp.tools`. Сам разбор аргументов, чтение и
отказы остаются там — этот файл только описывает, что наружу видно.

**У инструментов нет аргумента `tenant`.** Арендатора называет не собеседник,
а личный токен запроса (`src/mcp/config.py`): схема, объявившая `tenant`,
позволила бы агенту назвать код соседа и прочитать чужую историю проверок —
то самое свойство, которое `src/mcp/tools.py` и `src/db/queries.py` держат на
своей стороне (T110). Здесь оно проверяется тестами, а не комментарием.

Импорта `src.db` в этом файле нет и не будет: обработчики читает
`src.mcp.tools`, а он сам тянет `src.db.queries` (и с ним `psycopg`) лениво,
внутри функций, а не при импорте модуля — жадный импорт здесь один раз уже
ронял сбор `tests/` в окружении без этой зависимости.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from . import tools


@dataclass(frozen=True)
class ToolSpec:
    """Одна запись каталога: как агент видит инструмент и что вызывается внутри."""

    name: str
    description: str
    input_schema: dict[str, object]
    handler: Callable[..., dict[str, object]]


def _date_property(*, meaning: str) -> dict[str, object]:
    """Свойство-дата: единый формат и единый смысл фильтра для всех инструментов.

    Формат называется явно (ГГГГ-ММ-ДД), а не оставлен на догадку агента: у
    инструментов по обе стороны один и тот же разбор дат (`tools._parse_date`),
    и молча угаданный порядок дня и месяца дал бы фильтр за другой период.
    """
    return {
        "type": "string",
        "description": (
            f"{meaning} Format: YYYY-MM-DD. Filters by the date the inspection "
            "itself took place (the day the unit was visited), not by the date "
            "the record was pushed into the database — those two dates differ "
            "whenever a backlog of past inspections is pushed in one batch."
        ),
    }


#: Предел выдачи. Число потолка сюда не попадает: он живёт ровно в одном
#: месте, `src.db.queries.MAX_LIMIT`, и второй экземпляр здесь разошёлся бы с
#: ним при первой же правке одного файла без другого.
_LIMIT_PROPERTY: dict[str, object] = {
    "type": "integer",
    "minimum": 1,
    "description": (
        "Maximum number of rows to return. The server enforces its own upper "
        "cap on this value; a limit above that cap is rejected with an "
        "explicit error, not silently clamped down to it."
    ),
}

_UNIT_FILTER_PROPERTY: dict[str, object] = {
    "type": "string",
    "description": "Restrict results to this unit (pizzeria) name. Omit to include all units.",
}

_UNIT_REQUIRED_PROPERTY: dict[str, object] = {
    "type": "string",
    "description": "Name of the unit (pizzeria) whose inspection history to read.",
}

_INSPECTION_ID_PROPERTY: dict[str, object] = {
    "type": "string",
    "description": (
        "Identifier of the inspection to read, as returned in the 'id' field "
        "of list_inspections entries. Must be a UUID."
    ),
}

_FINDINGS_UNIT_PROPERTY: dict[str, object] = {
    "type": "string",
    "description": "Name of the unit (pizzeria) whose recorded findings to read.",
}

TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="list_inspections",
        description=(
            "List recorded inspections for the caller's tenant, most recent "
            "first. Each entry carries the score (percentage, letter grade) "
            "and finding count exactly as they were recorded when that "
            "inspection was completed — nothing is recalculated here. "
            "Optionally filter by unit name and by the date range the "
            "inspections took place in."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "unit": _UNIT_FILTER_PROPERTY,
                "date_from": _date_property(
                    meaning="Only include inspections on or after this date."
                ),
                "date_to": _date_property(
                    meaning="Only include inspections on or before this date."
                ),
                "limit": _LIMIT_PROPERTY,
            },
            "required": [],
            "additionalProperties": False,
        },
        handler=tools.list_inspections,
    ),
    ToolSpec(
        name="unit_history",
        description=(
            "Read the recorded score history of one unit (pizzeria), most "
            "recent inspection first — a series of percentages and letter "
            "grades exactly as recorded at the time of each inspection. No "
            "trend, average, or difference between entries is computed here; "
            "the caller compares the series itself."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "unit": _UNIT_REQUIRED_PROPERTY,
                "limit": _LIMIT_PROPERTY,
            },
            "required": ["unit"],
            "additionalProperties": False,
        },
        handler=tools.unit_history,
    ),
    ToolSpec(
        name="network_summary",
        description=(
            "Summarize the caller's tenant network over a date range: how "
            "many inspections and units, total findings, the distribution of "
            "recorded letter grades, and the best- and worst-scoring recorded "
            "inspections. No average score is computed — that number was "
            "never recorded by the audit engine, so it is not invented here."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "date_from": _date_property(
                    meaning="Only include inspections on or after this date."
                ),
                "date_to": _date_property(
                    meaning="Only include inspections on or before this date."
                ),
            },
            "required": [],
            "additionalProperties": False,
        },
        handler=tools.network_summary,
    ),
    ToolSpec(
        name="get_inspection",
        description=(
            "Read one recorded inspection of the caller's tenant in full: its "
            "header, the score breakdown (percentage, letter grade, "
            "deductions, per-check counts, per-zone breakdown), and every "
            "recorded finding — exactly as the audit engine stored them when "
            "the inspection was completed, with nothing recalculated here. "
            "Returns found: false, with no error, when the id does not match "
            "any inspection of this tenant (including one that belongs to "
            "another tenant)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "id": _INSPECTION_ID_PROPERTY,
            },
            "required": ["id"],
            "additionalProperties": False,
        },
        handler=tools.get_inspection,
    ),
    ToolSpec(
        name="findings_by_unit",
        description=(
            "List the recorded findings of one unit (pizzeria) across all its "
            "inspections, most recent inspection first, exactly as each "
            "finding was recorded. No repeat count, grouping by code, or "
            "share is computed here — that number was never recorded by the "
            "audit engine, so the caller summarizes the returned rows itself."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "unit": _FINDINGS_UNIT_PROPERTY,
                "limit": _LIMIT_PROPERTY,
            },
            "required": ["unit"],
            "additionalProperties": False,
        },
        handler=tools.findings_by_unit,
    ),
)

#: Индекс по имени — `find()` вызывается на каждый запрос `tools/call`,
#: а линейный проход по пяти записям пересчитывать незачем.
_BY_NAME: dict[str, ToolSpec] = {spec.name: spec for spec in TOOLS}


def find(name: str) -> ToolSpec | None:
    """Спецификация инструмента по имени или `None`, если имя незнакомо."""
    return _BY_NAME.get(name)


def as_list() -> list[dict[str, object]]:
    """Каталог в форме ответа MCP `tools/list`: ровно `name`/`description`/`inputSchema`."""
    return [
        {"name": spec.name, "description": spec.description, "inputSchema": spec.input_schema}
        for spec in TOOLS
    ]
