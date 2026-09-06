"""Каталог инструментов MCP: чем агент партнёра может спросить у базы проверок и чем поправить.

Декларация, а не логика: имя, текст для агента, JSON Schema аргументов и
ссылка на обработчик. Читающие проверки инструменты (`kind=KIND_INSPECTIONS`)
ссылаются на `src.mcp.tools`, инструменты методики (`kind=KIND_CHECKLIST`) —
на `src.mcp.checklist_tools`. Сам разбор аргументов, чтение, правка и отказы
остаются там — этот файл только описывает, что наружу видно.

**У инструментов нет аргумента `tenant`.** Арендатора называет не собеседник,
а личный токен запроса (`src/mcp/config.py`): схема, объявившая `tenant`,
позволила бы агенту назвать код соседа и прочитать чужую историю проверок —
то самое свойство, которое `src/mcp/tools.py` и `src/db/queries.py` держат на
своей стороне (T110). Здесь оно проверяется тестами, а не комментарием.

**У инструментов методики нет и аргумента `store`.** Хранилище версий
подставляет точка входа сервера, а не собеседник: агент называет коды
пунктов и зон, а не путь к хранилищу на диске.

Импорта `src.db` в этом файле нет и не будет: обработчики читает
`src.mcp.tools`, а он сам тянет `src.db.queries` (и с ним `psycopg`) лениво,
внутри функций, а не при импорте модуля — жадный импорт здесь один раз уже
ронял сбор `tests/` в окружении без этой зависимости.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from . import checklist_tools, retraction, tools

#: Инструменты проверок: обработчику нужен только код арендатора.
KIND_INSPECTIONS = "inspections"

#: Инструменты методики: обработчику нужно ещё и хранилище версий, а сам
#: доступ к ним открывается отдельной настройкой (`MCP_CHECKLIST_TENANTS`).
#: Вид объявлен здесь, а не угадывается по имени: угадывание по имени однажды
#: открыло бы правку методики новому инструменту, которого никто не проверял.
KIND_CHECKLIST = "checklist"

#: Снятие проверки из истории (T211, D086/D089). Третий вид, а не «инструмент
#: проверок, который пишет»: правило блока — у всего, что не объявлено
#: методикой, обработчик обязан лежать в модуле чтения, где записи нет вовсе
#: (`tests/test_mcp_server.py`). Отдельный вид не ослабляет это правило, а
#: делает исключение из него ЕДИНСТВЕННЫМ, названным и пересчитываемым: тем же
#: тестом проверяется, что инструмент такого вида ровно один.
#:
#: Право на него личное и спрашивается на входе (`rpc._call_tool`), как и у
#: методики: обработчик, забывший спросить о правах, был бы дырой, которую
#: видно только чтением всех обработчиков подряд.
KIND_RETRACTION = "retraction"


@dataclass(frozen=True)
class ToolSpec:
    """Одна запись каталога: как агент видит инструмент и что вызывается внутри."""

    name: str
    description: str
    input_schema: dict[str, object]
    handler: Callable[..., dict[str, object]]
    kind: str = KIND_INSPECTIONS
    #: Инструмент читает историю проверок из базы.
    #:
    #: Объявляется отдельно от `kind`, а не выводится из него, и это не
    #: дублирование. Перебор в `tests/test_mcp_no_history.py` («пусто и
    #: «читать неоткуда» — не одно и то же») шёл по `kind ==
    #: KIND_INSPECTIONS`. Инструмент методики, который тоже ходит в базу
    #: (`photo_cue_suggestions`, T165), по виду в этот перебор не попадает и
    #: проехал бы мимо заслона молча — а стенд с намеренно не поднятой базой
    #: у продукта есть, и на нём такой инструмент ответил бы «промахов не
    #: найдено» вместо «читать неоткуда».
    history: bool = False


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


def _code_property(*, meaning: str) -> dict[str, object]:
    """Свойство-код: единый тип для кодов пунктов чек-листа и зон, разный смысл текста.

    Коды связывают сущности, формулировки — никогда: у каждого места
    использования свой смысл (какой пункт читать, какой пункт править, какую
    зону завести), и общий текст стёр бы это различие.
    """
    return {
        "type": "string",
        "description": meaning,
    }


#: Версия методики для чтения — одна и та же в checklist_items и
#: checklist_item: по умолчанию читается та версия, по которой движок сегодня
#: считает проверки, а не последняя записанная (для неё есть tip в
#: checklist_versions).
_CHECKLIST_VERSION_PROPERTY: dict[str, object] = {
    "type": "string",
    "description": (
        "Checklist version to read from, as returned by checklist_versions "
        "('version' field). Omit to read the version the audit engine "
        "currently scores inspections by (checklist_versions calls that one "
        "'current'; it may differ from 'latest', the most recently stored "
        "one)."
    ),
}

#: Имя набора методики — общее для всех шести правящих инструментов: назвать
#: один раз, дальше оно наследуется от предыдущей версии.
_VERSION_NAME_PROPERTY: dict[str, object] = {
    "type": "string",
    "description": (
        "Name of the checklist set this change belongs to (lowercase Latin "
        "letters, digits, hyphen and underscore only, no trailing date — the "
        "system stamps the publication date itself, e.g. 'imf'). Name it "
        "once; every later change inherits the name from the version it "
        "builds on, so it only needs to be repeated to start a new named "
        "set."
    ),
}

#: Пояснение к правке — общее для всех шести правящих инструментов: одна
#: фраза для человека, читающего журнал, а не место для данных.
_NOTE_PROPERTY: dict[str, object] = {
    "type": "string",
    "description": (
        "Reason for this change, in one short phrase. Recorded in the "
        "checklist audit journal for a human to read later; it is not part "
        "of the checklist itself."
    ),
}

#: Список зон — общий для add_checklist_item и edit_checklist_item: один и
#: тот же формат кодов, один и тот же смысл `*`.
_ZONES_PROPERTY: dict[str, object] = {
    "type": "string",
    "description": (
        "Comma-separated zone codes this item applies to (e.g. "
        "'fridge,freezer'), or '*' for all zones."
    ),
}

#: Срок устранения — общий для add_checklist_item и edit_checklist_item.
_DAYS_PROPERTY: dict[str, object] = {
    "type": "integer",
    "description": "Deadline to fix a violation of this item, in days.",
}

#: Классы критичности — общие для add_checklist_item и edit_checklist_item;
#: обязательность в схеме решает `required`, а не эта строка.
_LEVELS_PROPERTY: dict[str, object] = {
    "type": "string",
    "description": (
        "Semicolon-separated severity classes this item may be scored at, "
        "e.g. 'D1;D2'. These are the managing company's own criticality "
        "classes (from its own audit criteria), not a general severity "
        "scale."
    ),
}

_CUE_PHRASE_PROPERTY: dict[str, object] = {
    "type": "string",
    "description": (
        "The cue phrase — what an auditor says or a photo shows, in the "
        "wording the map carries. It is the row's identity: rows of this map "
        "are named by their phrase, in full and word for word, because the "
        "map has no codes of its own on that side."
    ),
}

_CUE_CODES_PROPERTY: dict[str, object] = {
    "type": "array",
    "items": {"type": "string"},
    "description": (
        "Checklist codes the cue raises, one entry per code column of the "
        "section's table (a two-column table takes exactly one entry). Put "
        "several codes in one entry separated by commas, in the order they "
        "should be offered. Codes only: entities are linked by code, never by "
        "wording."
    ),
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
        history=True,
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
        history=True,
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
        history=True,
    ),
    ToolSpec(
        name="get_inspection",
        description=(
            "Read one recorded inspection of the caller's tenant in full: its "
            "header, the score breakdown (percentage, letter grade, "
            "deductions, per-check counts, per-zone breakdown), every "
            "recorded finding, and the information part — the answers the "
            "auditor gave at the end of the walk, among them the action plan "
            "deadline the partner was given. All of it exactly as the audit "
            "engine stored it when the inspection was completed, with nothing "
            "recalculated here. Each information field carries its checklist "
            "code and the wording of that item in the methodology version the "
            "inspection was scored by; 'title' is null when that version is "
            "not on this machine — wording from another version would be a "
            "different question under the same date. Read 'status' before "
            "concluding anything from an empty information part: it says "
            "whether these answers were recorded at all. Returns "
            "found: false, with no error, when the id does not match "
            "any inspection of this tenant (including one that belongs to "
            "another tenant)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "id": _INSPECTION_ID_PROPERTY,
                "lang": {
                    "type": "string",
                    "description": (
                        "Language of the information field titles, as a "
                        "two-letter code (for example 'ru' or 'en'). Omit to "
                        "use the language the report was issued in. Titles "
                        "only: the auditor's own answers and the findings are "
                        "returned word for word and are never translated. A "
                        "language the methodology does not carry is rejected "
                        "with an explicit error rather than quietly answered "
                        "in another language."
                    ),
                },
            },
            "required": ["id"],
            "additionalProperties": False,
        },
        handler=tools.get_inspection,
        history=True,
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
        history=True,
    ),
    ToolSpec(
        name="inspection_letter",
        description=(
            "Rebuild the partner letter for one recorded inspection of the "
            "caller's tenant and return its text, ready for a human to paste "
            "into mail and send. The letter is written by the audit engine, "
            "not here, and it is rebuilt on the exact methodology version the "
            "inspection was scored by — never on today's methodology, which "
            "would silently restate the partner's grade. The score the engine "
            "computes is checked against the one recorded in the inspection: "
            "a mismatch is refused, not returned. 'lang' picks the language "
            "of the letter; omit it for the language the report was issued "
            "in. The auditor's own wording of each finding is never "
            "translated, so a letter asked for in another language comes out "
            "mixed: header and zone names translated, findings as recorded. "
            "Read 'ready_to_send' and 'not_restored' before passing the text "
            "on: fields the read layer cannot return leave gaps that the "
            "engine fills with a blank line, and findings recorded in another "
            "language are listed there too, with both languages named in "
            "'status'. Returns found: false, with no error, when the id does "
            "not match any inspection of this tenant."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "id": _INSPECTION_ID_PROPERTY,
                "lang": {
                    "type": "string",
                    "description": (
                        "Language of the letter, as a two-letter code (for "
                        "example 'ru' or 'en'). Omit to use the language the "
                        "report was issued in. A language the methodology "
                        "does not carry is rejected with an explicit error "
                        "rather than quietly answered in another language."
                    ),
                },
            },
            "required": ["id"],
            "additionalProperties": False,
        },
        handler=tools.inspection_letter,
        history=True,
    ),
    # --- методика: чтение --------------------------------------------------
    ToolSpec(
        name="checklist_versions",
        description=(
            "List every stored checklist version, newest first. Versions are "
            "never deleted: a report scored a year ago must stay explainable, "
            "so a version stays listed forever once it has been used. "
            "'current' is the version the audit engine scores inspections by "
            "today; 'latest' is the most recently stored version, which a "
            "checklist-editing tool builds on by default unless told "
            "otherwise — the two differ whenever a stored change has not "
            "been published yet."
        ),
        input_schema={
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        handler=checklist_tools.checklist_versions,
        kind=KIND_CHECKLIST,
    ),
    ToolSpec(
        name="checklist_items",
        description=(
            "List the checklist items of one methodology version, in file "
            "order, together with the version's zones. Items are returned "
            "without their D1/D2/D3 criteria text, which can run to a full "
            "page — read one item's criteria with checklist_item. Rows carry "
            "the managing company's own columns exactly as the audit engine "
            "reads them; nothing here is derived or renamed. Optionally "
            "filter by version and by a case-insensitive substring match on "
            "the process name."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "version": _CHECKLIST_VERSION_PROPERTY,
                "process": {
                    "type": "string",
                    "description": (
                        "Case-insensitive substring filter on the process "
                        "name (matches either the Russian or the English "
                        "text). Omit to include every process."
                    ),
                },
            },
            "required": [],
            "additionalProperties": False,
        },
        handler=checklist_tools.checklist_items,
        kind=KIND_CHECKLIST,
    ),
    ToolSpec(
        name="checklist_item",
        description=(
            "Read one checklist item together with its D1/D2/D3 criteria — "
            "the only source of what makes a finding D1 versus D2 versus D3 "
            "for this item; the class is never guessed from a photo, it is "
            "read off these written criteria. Fails with an explicit error "
            "if the code does not exist in the given version."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "code": _code_property(
                    meaning=(
                        "Code of the checklist item to read (e.g. 'CLN05'), "
                        "as it appears in checklist_items."
                    )
                ),
                "version": _CHECKLIST_VERSION_PROPERTY,
            },
            "required": ["code"],
            "additionalProperties": False,
        },
        handler=checklist_tools.checklist_item,
        kind=KIND_CHECKLIST,
    ),
    # --- методика: правка пунктов -------------------------------------------
    ToolSpec(
        name="add_checklist_item",
        description=(
            "Add a checklist item. The change is stored as a NEW checklist "
            "version next to the current one — the live methodology the "
            "engine scores by is never modified in place. The new version is "
            "not published automatically: the engine keeps scoring by the "
            "current version until publish_checklist_version is called on "
            "it. The version is only stored if the audit engine itself "
            "accepts the resulting checklist (it re-validates it and "
            "re-scores a probe inspection with it); if the engine refuses, "
            "the call returns a refusal and no version is created. Passing "
            "criteria matters in practice: without it the engine refuses the "
            "checklist outright, because judging a violation's class from a "
            "photo without written criteria is exactly the guesswork this "
            "checklist exists to rule out."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "process": {
                    "type": "string",
                    "description": ("Process (technological step) this item checks, in Russian."),
                },
                "question_ru": {
                    "type": "string",
                    "description": ("Checklist question text shown to the auditor, in Russian."),
                },
                "levels": _LEVELS_PROPERTY,
                "code": _code_property(
                    meaning=(
                        "Item code (Latin letters, digits and underscore, "
                        "e.g. 'CLN05'). Omit to have the engine assign one "
                        "automatically from the process name."
                    )
                ),
                "process_en": {
                    "type": "string",
                    "description": (
                        "Process name in English. Omit to leave the English text blank."
                    ),
                },
                "question_en": {
                    "type": "string",
                    "description": (
                        "Checklist question text in English. Omit to leave the English text blank."
                    ),
                },
                "zones": _ZONES_PROPERTY,
                "days": _DAYS_PROPERTY,
                "criteria": {
                    "type": "string",
                    "description": (
                        "D1/D2/D3 criteria text for this item — what makes a "
                        "finding D1 versus D2 versus D3. Without it the "
                        "engine refuses the checklist outright: a violation "
                        "class judged from a photo with no written criteria "
                        "would be guessed, not derived from the managing "
                        "company's own rules."
                    ),
                },
                "kind": {
                    "type": "string",
                    "enum": list(checklist_tools.ITEM_KINDS),
                    "description": (
                        "Kind of item: 'violation' (a checkable violation "
                        "found during an inspection), 'info' (informational, "
                        "not scored), or 'aggregate' (rolls up other items). "
                        "There is no 'off' here on purpose — disabling an "
                        "item is remove_checklist_item, not a kind, so a "
                        "disabled item shows up in the journal as one clear "
                        "action instead of two different ways to reach the "
                        "same state."
                    ),
                },
                "version_name": _VERSION_NAME_PROPERTY,
                "note": _NOTE_PROPERTY,
            },
            "required": ["process", "question_ru", "levels"],
            "additionalProperties": False,
        },
        handler=checklist_tools.add_checklist_item,
        kind=KIND_CHECKLIST,
    ),
    ToolSpec(
        name="edit_checklist_item",
        description=(
            "Edit fields of an existing checklist item. Only the fields "
            "named in the call change; every other field is carried over "
            "unchanged from the version this edit builds on. As with every "
            "checklist-editing tool, the change is stored as a NEW checklist "
            "version — the live methodology is never modified in place, the "
            "new version is not published automatically (publish_checklist_"
            "version does that), and it is only stored once the audit "
            "engine accepts the resulting checklist; otherwise the call "
            "returns a refusal and no version is created. There is no "
            "'kind' or enable/disable argument here on purpose: switching an "
            "item off or on is remove_checklist_item / restore_checklist_"
            "item, so those stay visible in the journal by their own name "
            "instead of as a field edit."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "code": _code_property(meaning="Code of the checklist item to edit."),
                "process": {
                    "type": "string",
                    "description": (
                        "New process (technological step) name in Russian. "
                        "Omit to keep the current value."
                    ),
                },
                "process_en": {
                    "type": "string",
                    "description": ("New process name in English. Omit to keep the current value."),
                },
                "question_ru": {
                    "type": "string",
                    "description": (
                        "New checklist question text in Russian. Omit to keep the current value."
                    ),
                },
                "question_en": {
                    "type": "string",
                    "description": (
                        "New checklist question text in English. Omit to keep the current value."
                    ),
                },
                "levels": _LEVELS_PROPERTY,
                "zones": _ZONES_PROPERTY,
                "days": _DAYS_PROPERTY,
                "criteria": {
                    "type": "string",
                    "description": (
                        "New D1/D2/D3 criteria text for this item. Omit to keep the current value."
                    ),
                },
                "version_name": _VERSION_NAME_PROPERTY,
                "note": _NOTE_PROPERTY,
            },
            "required": ["code"],
            "additionalProperties": False,
        },
        handler=checklist_tools.edit_checklist_item,
        kind=KIND_CHECKLIST,
    ),
    ToolSpec(
        name="remove_checklist_item",
        description=(
            "Disable a checklist item. By default the item stays in the "
            "file, marked disabled: it is no longer offered during "
            "inspections, but it stays visible and can be brought back with "
            "restore_checklist_item. hard removes the row entirely instead — "
            "no trace of the edit remains in the file, and bringing the item "
            "back means restoring it from an earlier version. As with every "
            "checklist-editing tool, the change is stored as a NEW checklist "
            "version, is not published automatically (publish_checklist_"
            "version does that), and is only stored once the audit engine "
            "accepts the resulting checklist."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "code": _code_property(meaning="Code of the checklist item to disable."),
                "hard": {
                    "type": "boolean",
                    "description": (
                        "Delete the item's row outright instead of disabling "
                        "it. Default: false — the item stays in the file, "
                        "disabled, and restore_checklist_item can bring it "
                        "back; hard removal leaves no trace, and undoing it "
                        "means restoring an earlier version instead."
                    ),
                },
                "version_name": _VERSION_NAME_PROPERTY,
                "note": _NOTE_PROPERTY,
            },
            "required": ["code"],
            "additionalProperties": False,
        },
        handler=checklist_tools.remove_checklist_item,
        kind=KIND_CHECKLIST,
    ),
    ToolSpec(
        name="restore_checklist_item",
        description=(
            "Re-enable a checklist item previously disabled by "
            "remove_checklist_item. Does not apply to an item removed with "
            "hard=true — that row no longer exists and can only be "
            "recovered by reading it from an earlier version. As with every "
            "checklist-editing tool, the change is stored as a NEW checklist "
            "version, is not published automatically (publish_checklist_"
            "version does that), and is only stored once the audit engine "
            "accepts the resulting checklist."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "code": _code_property(meaning="Code of the checklist item to re-enable."),
                "version_name": _VERSION_NAME_PROPERTY,
                "note": _NOTE_PROPERTY,
            },
            "required": ["code"],
            "additionalProperties": False,
        },
        handler=checklist_tools.restore_checklist_item,
        kind=KIND_CHECKLIST,
    ),
    # --- методика: правка зон -----------------------------------------------
    ToolSpec(
        name="add_zone",
        description=(
            "Add a physical zone (e.g. a fridge or a freezer). Zone shares "
            "weight the score, sum to 100%, and are the managing company's "
            "own decision — the engine will not silently redistribute them, "
            "so this call must say explicitly what to do with them: "
            "equal_shares rebalances every zone's share equally; share sets "
            "only the new zone's weight and leaves the others untouched, "
            "which means the shares no longer sum to 100% afterwards, and a "
            "checklist whose zone shares do not add up is one the engine "
            "refuses — no version is stored in that case. Uneven shares are "
            "set by editing zones.csv by hand instead. As with every "
            "checklist-editing tool, the change is stored as a NEW checklist "
            "version and is not published automatically (publish_checklist_"
            "version does that)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "code": _code_property(
                    meaning=(
                        "Code of the new zone (Latin letters, digits and "
                        "underscore, e.g. 'fridge')."
                    )
                ),
                "name_ru": {
                    "type": "string",
                    "description": "Zone name in Russian.",
                },
                "name_en": {
                    "type": "string",
                    "description": ("Zone name in English. Omit to leave the English name blank."),
                },
                "share": {
                    "type": "number",
                    "description": (
                        "Weight to assign to the new zone, as a percentage. "
                        "Leaves every other zone's share untouched, so the "
                        "shares will not sum to 100% afterwards — use "
                        "equal_shares instead unless the remaining shares "
                        "are about to be fixed by hand; a checklist whose "
                        "zone shares do not sum to 100% is refused by the "
                        "engine."
                    ),
                },
                "equal_shares": {
                    "type": "boolean",
                    "description": (
                        "Rebalance every zone, including the new one, to an "
                        "equal share of 100%. Use this instead of share to "
                        "keep the shares summing to 100% automatically."
                    ),
                },
                "version_name": _VERSION_NAME_PROPERTY,
                "note": _NOTE_PROPERTY,
            },
            "required": ["code", "name_ru"],
            "additionalProperties": False,
        },
        handler=checklist_tools.add_zone,
        kind=KIND_CHECKLIST,
    ),
    ToolSpec(
        name="remove_zone",
        description=(
            "Remove a physical zone. The removed zone's share must go "
            "somewhere, and the engine will not decide that on the managing "
            "company's behalf: equal_shares rebalances the remaining zones' "
            "shares equally; keep_shares leaves the remaining shares as they "
            "are, which means they no longer sum to 100% — and a checklist "
            "whose zone shares do not add up is refused, so no version is "
            "stored in that case. The zone is also dropped from every "
            "checklist item's zone list. As with every checklist-editing "
            "tool, the change is stored as a NEW checklist version and is "
            "not published automatically (publish_checklist_version does "
            "that)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "code": _code_property(meaning="Code of the zone to remove."),
                "keep_shares": {
                    "type": "boolean",
                    "description": (
                        "Leave the remaining zones' shares unchanged after "
                        "removal. The shares will then no longer sum to "
                        "100%, and the engine refuses such a checklist — use "
                        "this only together with a follow-up hand edit of "
                        "zones.csv, or use equal_shares instead."
                    ),
                },
                "equal_shares": {
                    "type": "boolean",
                    "description": (
                        "Rebalance the remaining zones to an equal share of 100% after removal."
                    ),
                },
                "version_name": _VERSION_NAME_PROPERTY,
                "note": _NOTE_PROPERTY,
            },
            "required": ["code"],
            "additionalProperties": False,
        },
        handler=checklist_tools.remove_zone,
        kind=KIND_CHECKLIST,
    ),
    # --- методика: публикация ------------------------------------------------
    # --- карта слов: чтение и правка (T144) --------------------------------
    ToolSpec(
        name="photo_cues",
        description=(
            "Read the word map of one methodology version: the sections and "
            "the cue rows inside them, each a phrase an auditor might say or "
            "a photo might show, mapped to the checklist codes it raises. "
            "This map is what lets a finding be recorded WITHOUT the auditor "
            "confirming it, so what it says ends up in the partner's report. "
            "It only adds candidates and reorders them; it never trims the "
            "list. The thresholds section is not part of it and is not "
            "returned. Omit 'version' for the version in force."
        ),
        input_schema={
            "type": "object",
            "properties": {"version": _CHECKLIST_VERSION_PROPERTY},
            "required": [],
            "additionalProperties": False,
        },
        handler=checklist_tools.photo_cues,
        kind=KIND_CHECKLIST,
    ),
    ToolSpec(
        name="add_photo_cue",
        description=(
            "Add one cue row to the word map, storing a new methodology "
            "version beside the one in force — the version in force does not "
            "change and publishing stays a separate call. Every code is "
            "checked against the checklist of that same version: a cue "
            "pointing at an item that does not exist would offer the model a "
            "code the methodology does not carry. 'codes' is one entry per "
            "code column of that section's table, so a two-column table takes "
            "exactly one entry; a row of the wrong width is rejected, because "
            "the columns mean different things (dirt and breakage are two "
            "questions about one object)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "section": {
                    "type": "string",
                    "description": (
                        "Heading of the map section to add the row to, "
                        "exactly as photo_cues returns it. An unknown section "
                        "is rejected rather than created, so a typo cannot "
                        "split the map across a twin section."
                    ),
                },
                "phrase": _CUE_PHRASE_PROPERTY,
                "codes": _CUE_CODES_PROPERTY,
                "version_name": _VERSION_NAME_PROPERTY,
                "note": _NOTE_PROPERTY,
            },
            "required": ["section", "phrase", "codes"],
            "additionalProperties": False,
        },
        handler=checklist_tools.add_photo_cue,
        kind=KIND_CHECKLIST,
    ),
    ToolSpec(
        name="edit_photo_cue",
        description=(
            "Change one cue row of the word map — its codes, its phrase, or "
            "both — storing a new methodology version beside the one in "
            "force. The row is named by its phrase in full and word for word: "
            "no nearest match is substituted, because editing the wrong row "
            "changes what gets recorded without the auditor confirming it. "
            "Name at least one of 'codes' and 'new_phrase'."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "phrase": _CUE_PHRASE_PROPERTY,
                "codes": _CUE_CODES_PROPERTY,
                "new_phrase": {
                    "type": "string",
                    "description": "New wording for the row. Omit to keep the phrase as it is.",
                },
                "version_name": _VERSION_NAME_PROPERTY,
                "note": _NOTE_PROPERTY,
            },
            "required": ["phrase"],
            "additionalProperties": False,
        },
        handler=checklist_tools.edit_photo_cue,
        kind=KIND_CHECKLIST,
    ),
    ToolSpec(
        name="remove_photo_cue",
        description=(
            "Remove one cue row from the word map, storing a new methodology "
            "version beside the one in force. Older versions are never "
            "deleted, so bringing the row back is a matter of publishing the "
            "earlier version rather than of finding a copy of the file."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "phrase": _CUE_PHRASE_PROPERTY,
                "version_name": _VERSION_NAME_PROPERTY,
                "note": _NOTE_PROPERTY,
            },
            "required": ["phrase"],
            "additionalProperties": False,
        },
        handler=checklist_tools.remove_photo_cue,
        kind=KIND_CHECKLIST,
    ),
    ToolSpec(
        name="photo_cue_suggestions",
        description=(
            "Report where the model has been systematically wrong, and propose "
            "what to add to the word map because of it. Every finding stores "
            "what the model proposed before the auditor fixed the record; the "
            "difference between the two is a miss, and identical misses are "
            "grouped and counted here. For each one the answer names the cue "
            "rows that lead to the wrong item code and gives a ready "
            "edit_photo_cue call. "
            "Nothing is applied: the word map is a management-company document, "
            "and a wrong word added automatically would reach a partner's "
            "report through the fast path without anyone confirming it "
            "(decision D077). Applying a proposal is a separate call, and even "
            "that only stores a new methodology version beside the one in "
            "force — publishing it is a third call. "
            "Every miss also quotes what the auditor actually said, whole and "
            "verbatim, with how often each phrase came up: the cue row says "
            "what to correct, the phrase says what was heard, and neither "
            "replaces the other. Words are stored beside a finding only since "
            "task T183, and a record made with no words at all — a photo read "
            "on its own, an item picked by button — has none; those records "
            "are counted as without_words and never shown as an empty phrase."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "date_from": _date_property(
                    meaning="Only look at findings from inspections on or after this date."
                ),
                "date_to": _date_property(
                    meaning="Only look at findings from inspections on or before this date."
                ),
                "min_confidence": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                    "description": (
                        "Only count a miss when the model's confidence was at "
                        "least this high. A fraction between 0 and 1, not a "
                        "percentage. Records made by matching the word map "
                        "carry no confidence at all — that path never measures "
                        "one — and they are never filtered out by this "
                        "threshold: they are the records made without the "
                        "auditor confirming the item, and so the most valuable "
                        "misses of all."
                    ),
                },
                # Not _LIMIT_PROPERTY: that one caps rows of a flat
                # inspection list and rejects a value above the server's own
                # ceiling (`src.db.queries.MAX_LIMIT`) outright. This limit
                # caps miss patterns *per group* of a grouped answer, and a
                # cut-off here is signalled through the 'truncated' field,
                # not a rejection — reusing the same property would have
                # attached the wrong behaviour's wording to this one.
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "description": (
                        "Maximum number of miss patterns to return in each "
                        "group. A cut-off answer says so in its 'truncated' "
                        "field rather than passing for a complete one."
                    ),
                },
                "version": _code_property(
                    meaning=(
                        "Checklist version whose word map the proposals refer "
                        "to. Defaults to the version in force. A proposal must "
                        "point at a row that exists in that version: a row "
                        "from another version is edited by another phrase."
                    )
                ),
            },
            "required": [],
            "additionalProperties": False,
        },
        handler=checklist_tools.photo_cue_suggestions,
        kind=KIND_CHECKLIST,
        history=True,
    ),
    ToolSpec(
        name="publish_checklist_version",
        description=(
            "Make a stored checklist version the one the audit engine "
            "scores inspections by. Inspections already scored stay on "
            "their own version and are not recalculated — a report already "
            "sent to a partner does not change retroactively. Rolling back "
            "is publishing an earlier version again; versions themselves "
            "are never deleted."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "version": _code_property(
                    meaning=(
                        "Checklist version to publish, as returned by "
                        "checklist_versions (e.g. "
                        "'imf-2026-09-03-3f5a91b2c7d0')."
                    )
                ),
            },
            "required": ["version"],
            "additionalProperties": False,
        },
        handler=checklist_tools.publish_checklist_version,
        kind=KIND_CHECKLIST,
    ),
    ToolSpec(
        name="retract_inspection",
        description=(
            "Retract a finalized inspection from the history. Use this when a "
            "report turned out to be wrong: the wrong one is retracted and a "
            "corrected inspection is recorded separately by an auditor — an "
            "inspection is never edited in place, and this tool cannot change "
            "anything inside one.\n\n"
            "THIS IS NOT REVERSIBLE FOR THE PARTNER. The report and the letter "
            "for this inspection are already in the partner's hands, and the "
            "photo evidence is deleted from storage for good. The row itself "
            "stays in the history, marked as retracted and carrying the "
            "reason, so it remains visible that the inspection happened — but "
            "from every ordinary read it disappears.\n\n"
            "A reason is mandatory and is recorded permanently. It is written "
            "once: retracting an already retracted inspection does not replace "
            "the reason it was retracted for.\n\n"
            "To make sure the right document is being withdrawn, the call must "
            "also name the unit and the inspection date as they are recorded — "
            "read them off the inspection first (get_inspection) and confirm "
            "with the person asking for this. If they do not match, nothing is "
            "retracted and the answer says what that inspection actually is."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "id": _INSPECTION_ID_PROPERTY,
                "reason": {
                    "type": "string",
                    "description": (
                        "Why this inspection is being withdrawn, in the words "
                        "of the person who decided it — recorded permanently "
                        "next to the inspection. Without a reason a withdrawn "
                        "document cannot be told apart from a quietly erased "
                        "one, so this is refused when empty."
                    ),
                },
                "confirm_unit": {
                    "type": "string",
                    "description": (
                        "Name of the unit (pizzeria) this inspection was made "
                        "at, as recorded. Confirmation, not a filter: it must "
                        "match, or nothing is retracted."
                    ),
                },
                "confirm_date": {
                    "type": "string",
                    "description": (
                        "Date the unit was visited, as recorded. Format: "
                        "YYYY-MM-DD. Confirmation, not a filter: it must "
                        "match, or nothing is retracted. This is the "
                        "inspection date, not the date the record was pushed "
                        "into the database."
                    ),
                },
            },
            "required": ["id", "reason", "confirm_unit", "confirm_date"],
            "additionalProperties": False,
        },
        handler=retraction.retract_inspection,
        kind=KIND_RETRACTION,
        history=True,
    ),
)

#: Индекс по имени — `find()` вызывается на каждый запрос `tools/call`,
#: а линейный проход по всем записям каталога пересчитывать незачем.
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
