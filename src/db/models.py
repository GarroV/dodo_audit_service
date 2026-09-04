"""Типы, которыми блок `db` разговаривает наружу.

`InspectionRow` — сводка одной проверки для списка (контракт
`list_inspections` в `docs/forge/blocks/db.md`). `FindingRow` — одна
записанная находка, `InspectionDetail` — проверка целиком вместе с находками
(контракт `get_inspection` и `findings_by_unit`, T114).

Ни один из этих типов ничего не считает: числа приходят из базы такими, какими
их положил движок при завершении проверки (конституция, принцип 2).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any


@dataclass(frozen=True)
class InspectionRow:
    """Одна строка списка проверок этого блока."""

    id: str
    tenant_code: str
    unit_name: str
    chat_id: int
    #: Вид проверки КОДОМ (`planned`/`repeat`/`unscheduled`), а не словом
    #: (T152, D025): формулировки переводятся и правятся, коды нет. Показывать
    #: вид человеком обязан тот, кто показывает, — через `domain.kind_title`
    #: и на языке того документа, который печатает.
    kind: str
    inspection_date: date
    report_lang: str
    checklist_version: str
    pct: float
    grade: str
    findings_count: int
    pushed_at: str
    #: Шапка проверки — то, чем письмо партнёру подписано и кому адресовано
    #: (T176). Слив пишет эти поля с самого T091, но чтение их не возвращало, и
    #: письмо, пересобранное по записанной проверке (`src/mcp/letters.py`),
    #: подписывалось прочерком вместо имени аудитора — молча, с нулевым кодом
    #: возврата у движка.
    #:
    #: Пустая строка означает «поле не заполнено», а не «поля нет»: колонки
    #: объявлены `not null default ''`, мастер бота даёт их пропустить, а `None`
    #: вместо пустой строки превратился бы у потребителя в слово «None» в
    #: подписи документа партнёру.
    auditor: str = ""
    city: str = ""
    partner: str = ""
    contact: str = ""


@dataclass(frozen=True)
class FindingRow:
    """Одна записанная находка вместе с проверкой, из которой она взята.

    Проверка названа прямо в строке (`inspection_id`, `unit_name`,
    `inspection_date`): находка без даты и точки не отвечает ни на один
    вопрос, ради которого её читают («что повторяется у этой пиццерии»), а
    добирать это отдельным запросом на каждую строку — тот самый N+1.
    """

    id: str
    inspection_id: str
    unit_name: str
    inspection_date: date
    n: int
    code: str
    level: str
    zone: str
    zone_unusual: bool
    #: Со слов аудитора или распознано по кадру (D044). Пусто — источник не
    #: записан: так выглядят проверки, заведённые до D044. Пустая строка и NULL
    #: в базе означают здесь одно и то же и склеены намеренно: «не записан» не
    #: бывает двух разных видов.
    source: str
    #: Язык, на котором находка записана (речь аудитора). Формулировки хранятся
    #: строками `(entity_id, lang, text)` (D025), и это поле говорит, какую
    #: строку вернули, — вместо того чтобы выдавать её за единственную.
    lang: str
    #: Формулировка и комментарий на языке `lang`. `None` — строки перевода нет
    #: вовсе; подменять её пустой строкой нельзя, иначе «перевода нет»
    #: становится неотличимо от «аудитор ничего не написал».
    text: str | None
    comment: str | None


@dataclass(frozen=True)
class InspectionDetail:
    """Проверка целиком: сводка, разбивка оценки и все её находки.

    `counts`, `by_zone` и `deductions` отдаются в том виде, в каком их положил
    движок: это его `Score`, а не пересчёт. Форму этих полей блок `db` не
    разбирает и не проверяет — он их хранит и возвращает.
    """

    inspection: InspectionRow
    deductions: float
    counts: dict[str, Any]
    by_zone: dict[str, Any]
    findings: tuple[FindingRow, ...]
