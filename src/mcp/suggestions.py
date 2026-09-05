"""Предложения для управляющей компании: где модель промахнулась и что дописать в карту слов.

Задача T165, решение D077. Владелец дословно: «при несостыковках, или если
пользователь добавит что-то в духе "ГРЯЗЬ НА ПОЛКЕ В ГОРЯЧЕМ ЦЕХЕ, ЭТО
ЧИСТОТА" то мы долполняем наш список терминов».

**Боевой список слов автоматически не пополняется, и это не осторожность, а
условие самого решения.** По карте слов быстрый путь (T113) записывает находку
БЕЗ подтверждения аудитора (D064): строка, дописанная сюда автоматически,
уехала бы в отчёт партнёру без чьего-либо ведома. Поэтому здесь собирается
ПРЕДЛОЖЕНИЕ человеку — с готовым вызовом, который сделает он сам, — и ни один
файл методики этим модулем не открывается на запись вовсе.

**Считается сравнением того, что уже записано.** Что аудитор поправил, отдельно
нигде не хранится: это разница между тройкой предложения и тройкой самой
записи (`db.FindingRow.corrections`). Здесь она не пересчитывается своей
формулой — иначе появилась бы вторая, которая разошлась бы с первой молча.

**Числа тут только счётные.** Сколько раз пара «предложено → записано»
встретилась, какая уверенность у выборки самая низкая и самая высокая — это
счёт и выбор записанного, а не выведенная величина. Доля попаданий («модель
права в 73%») сюда не попадает намеренно: такого числа никто не записывал, а
в пересказе агента оно немедленно пошло бы как измеренное качество модели.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..db.models import FindingRow
from .errors import ToolError

#: Сколько строк промахов отдаём по умолчанию. Предложение читает человек, а
#: не машина: хвост из редких пар он всё равно не разберёт, а `truncated`
#: скажет, что хвост есть.
DEFAULT_MISSES = 50

#: Приписка, которой кончается любой ответ этого инструмента. Отдельной
#: строкой и в каждом исходе: агент пересказывает человеку статус, и «собрано
#: N предложений» без неё читается как «N слов добавлено».
NOT_APPLIED = (
    "Nothing was changed: proposals are not applied automatically (decision D077) — "
    "the word map is a management-company document, and a wrong word added here "
    "would reach a partner's report through the fast path without anyone confirming it."
)

#: Класс нарушения живёт не в карте слов, а в разделе порогов и в критериях, и
#: правки ни того, ни другого этот инструмент не предлагает: разборщик
#: быстрого пути раздел порогов пропускает вовсе, а критерии — это текст, по
#: которому человек решает, а не таблица соответствий.
LEVEL_NOTE = (
    "Classes live in the thresholds section of the word map and in criteria.md; "
    "this tool proposes no edit to either."
)

#: Чего в истории нет и почему предложение уже поэтому. Оговорки уходят в
#: ответ списком, а не остаются в этом файле: читающий обязан узнать границы
#: выборки из самой выборки.
CAVEATS = (
    "The auditor's raw words are not stored in the history yet (task T185: the column "
    "does not exist), so a proposal can name the cue row that led to the wrong code, "
    "but not the phrase the auditor actually used.",
    "Fast-path records carry no confidence at all — matching against the word map never "
    "measures one — and they are the records made without the auditor confirming the item. "
    "They are therefore always included, whatever min_confidence says.",
)


@dataclass(frozen=True)
class CueRow:
    """Одна строка карты слов: где лежит, чем названа и какие пункты предлагает.

    Строка называется своей фразой целиком: похожая не подставляется, потому
    что правка не той строки меняет то, что уезжает партнёру (T144).

    Коды хранятся ПО КОЛОНКАМ, а не одним списком, и это не педантизм: колонки
    значат разное («грязь» и «поломка» — два вопроса про один объект), правка
    принимает ровно столько ячеек, сколько колонок в разделе, и плоский список
    дал бы предложение, которое `edit_photo_cue` отклонит как строку не той
    ширины. Проверено разбором `photo_cues._check_codes`.
    """

    section: str
    phrase: str
    columns: tuple[tuple[str, ...], ...]

    @property
    def codes(self) -> tuple[str, ...]:
        """Все коды строки подряд — по ним строка и находится."""
        return tuple(код for колонка in self.columns for код in колонка)


def _check_threshold(value: float | None) -> float | None:
    """Порог уверенности как доля — или отказ.

    Уверенность модели это доля от нуля до единицы (`domain.Suggestion`).
    Порог, названный процентами, отобрал бы пустоту и выглядел бы работающим
    фильтром: «промахов не найдено» вместо «порог не в той шкале».
    """
    if value is None:
        return None
    if not 0.0 <= value <= 1.0:
        raise ToolError(
            f"Порог уверенности {value} вне доли от 0 до 1. Уверенность модели — доля, а не "
            f"проценты: порог 70 отобрал бы пустоту и читался бы как «промахов нет»"
        )
    return value


def _check_limit(value: int) -> int:
    if value < 1:
        raise ToolError(
            f"Предел выдачи {value} меньше единицы: ноль вернул бы пустоту вместо отказа, "
            f"и она читалась бы как «промахов не найдено»"
        )
    return value


def _passes(row: FindingRow, threshold: float | None) -> bool:
    """Строка попадает в выборку.

    Условие написано как «порога нет ИЛИ уверенности нет ИЛИ она не ниже
    порога», и средняя часть здесь главная: записи быстрого пути идут БЕЗ
    уверенности, а это самые ценные строки — промах без подтверждения
    аудитора. Отбор «уверенность выше порога» потерял бы ровно их.
    """
    if threshold is None:
        return True
    return row.suggested_confidence is None or row.suggested_confidence >= threshold


@dataclass
class _Bucket:
    """Копилка одной пары «предложено → записано»."""

    count: int = 0
    units: set[str] = field(default_factory=set)
    codes: set[str] = field(default_factory=set)
    known: list[float] = field(default_factory=list)
    unknown: int = 0

    def add(self, row: FindingRow) -> None:
        self.count += 1
        self.units.add(row.unit_name)
        self.codes.add(row.code)
        if row.suggested_confidence is None:
            self.unknown += 1
        else:
            self.known.append(row.suggested_confidence)

    def confidence(self) -> dict[str, object]:
        return {
            "min": min(self.known) if self.known else None,
            "max": max(self.known) if self.known else None,
            "unknown": self.unknown,
        }


def _cut(rows: list[dict[str, Any]], limit: int) -> tuple[list[dict[str, Any]], bool]:
    return rows[:limit], len(rows) > limit


def _order(
    items: Iterable[tuple[tuple[str, ...], _Bucket]],
) -> list[tuple[tuple[str, ...], _Bucket]]:
    """Частый промах впереди, при равном счёте — по кодам.

    Порядок задан до конца намеренно: выдача, зависящая от порядка строк в
    базе, менялась бы между двумя одинаковыми вопросами, и человек читал бы
    это как изменение в данных.
    """
    return sorted(items, key=lambda пара: (-пара[1].count, пара[0]))


def _code_misses(
    buckets: dict[tuple[str, ...], _Bucket], cues: Sequence[CueRow]
) -> list[dict[str, Any]]:
    строки: list[dict[str, Any]] = []
    for (предложено, записано), копилка in _order(buckets.items()):
        подходящие = [row for row in cues if предложено in row.codes]
        строки.append(
            {
                "suggested_code": предложено,
                "recorded_code": записано,
                "count": копилка.count,
                "units": sorted(копилка.units),
                "confidence": копилка.confidence(),
                "cue_rows": [
                    {"section": row.section, "phrase": row.phrase, "codes": list(row.codes)}
                    for row in подходящие
                ],
                "suggested_edits": [_edit(row, предложено, записано) for row in подходящие],
                "note": _code_note(предложено, записано, len(подходящие)),
            }
        )
    return строки


def _edit(row: CueRow, suggested: str, recorded: str) -> dict[str, Any]:
    """Готовый вызов правки — тот, который сделает человек, а не этот модуль.

    Код дописывается К существующим, а не заменяет их: карта слов кандидатов
    только добавляет и переставляет, но никогда не урезает (T142), и замена
    отняла бы у быстрого пути пункт, который он сегодня предлагает верно.

    Дописывается он в ТУ колонку, где стоит промахнувшийся код: в разделе с
    несколькими колонками они значат разное, и код, положенный не в ту,
    ответил бы «грязь» на вопрос о поломке. Ячеек в вызове ровно столько,
    сколько колонок у раздела, — иначе правка отклонит строку по ширине.
    """
    ячейки: list[str] = []
    for колонка in row.columns:
        коды = list(колонка)
        if suggested in коды and recorded not in коды:
            коды.append(recorded)
        ячейки.append(", ".join(коды))
    return {
        "tool": "edit_photo_cue",
        "arguments": {"phrase": row.phrase, "codes": ячейки},
    }


def _code_note(suggested: str, recorded: str, rows: int) -> str:
    if rows == 0:
        return (
            f"There is no cue row in the word map leading to {suggested}: the code came from "
            f"the model itself, not from the map, so there is no row here to correct. Adding "
            f"{recorded} to the map needs a phrase, and the auditor's raw words are not stored yet."
        )
    return (
        f"{rows} cue row(s) lead to {suggested}. The proposal adds {recorded} beside it rather "
        f"than replacing it: the map only adds and reorders candidates and never trims them."
    )


def _level_misses(buckets: dict[tuple[str, ...], _Bucket]) -> list[dict[str, Any]]:
    return [
        {
            "code": код,
            "suggested_level": предложено,
            "recorded_level": записано,
            "count": копилка.count,
            "confidence": копилка.confidence(),
            "note": LEVEL_NOTE,
        }
        for (код, предложено, записано), копилка in _order(buckets.items())
    ]


def _zone_misses(buckets: dict[tuple[str, ...], _Bucket]) -> list[dict[str, Any]]:
    return [
        {
            "suggested_zone": предложено,
            "recorded_zone": записано,
            "count": копилка.count,
            "codes": sorted(копилка.codes),
            "confidence": копилка.confidence(),
        }
        for (предложено, записано), копилка in _order(buckets.items())
    ]


def _status(*, findings: int, with_suggestion: int, corrected: int, misses: int) -> str:
    """Состояние выдачи словами. Пусто бывает трёх разных видов, и они разные.

    «Находок нет» — не то же самое, что «предложений рядом с ними нет», и оба
    не то же самое, что «модель не промахивается». Слитые в один пустой
    список, они читались бы агентом как измеренное качество модели, хотя
    мерить было нечего.
    """
    if findings == 0:
        return f"no findings recorded in this period, so there is nothing to compare. {NOT_APPLIED}"
    if with_suggestion == 0:
        return (
            f"{findings} findings in this period, and no model suggestion is stored beside "
            f"any of them: this is nothing to measure, not a model that never misses. "
            f"A suggestion is kept only for findings made after the model started "
            f"proposing one. {NOT_APPLIED}"
        )
    return (
        f"{corrected} of {with_suggestion} findings with a model suggestion were corrected "
        f"by the auditor; they group into {misses} recurring code miss patterns. {NOT_APPLIED}"
    )


def build(
    rows: Sequence[FindingRow],
    *,
    cues: Sequence[CueRow],
    version: str,
    inspections: int,
    units: int,
    min_confidence: float | None = None,
    limit: int = DEFAULT_MISSES,
    truncated: bool = False,
) -> dict[str, Any]:
    """Собрать предложения из записанных расхождений. Ничего не пишет и не правит.

    `truncated` приходит из чтения: страница проверок могла упереться в
    потолок, и молча выдать её за весь период нельзя — предложение по половине
    периода выглядит точно так же, как предложение по всему.
    """
    порог = _check_threshold(min_confidence)
    предел = _check_limit(limit)

    коды: dict[tuple[str, ...], _Bucket] = {}
    классы: dict[tuple[str, ...], _Bucket] = {}
    зоны: dict[tuple[str, ...], _Bucket] = {}
    с_предложением = без_уверенности = ниже_порога = поправлено = 0
    без_класса = без_зоны = 0

    for row in rows:
        if row.suggested_code is None:
            continue
        с_предложением += 1
        if row.suggested_confidence is None:
            без_уверенности += 1
        if not _passes(row, порог):
            ниже_порога += 1
            continue
        правки = row.corrections()
        if правки:
            поправлено += 1
        if "code" in правки:
            коды.setdefault((row.suggested_code, row.code), _Bucket()).add(row)
        elif row.suggested_level is None:
            без_класса += 1
        elif "level" in правки:
            классы.setdefault((row.code, row.suggested_level, row.level), _Bucket()).add(row)
        if row.suggested_zone is None:
            без_зоны += 1
        elif "zone" in правки:
            зоны.setdefault((row.suggested_zone, row.zone), _Bucket()).add(row)

    промахи_кодов, обрезаны_коды = _cut(_code_misses(коды, cues), предел)
    промахи_классов, обрезаны_классы = _cut(_level_misses(классы), предел)
    промахи_зон, обрезаны_зоны = _cut(_zone_misses(зоны), предел)

    return {
        "checklist_version": version,
        "min_confidence": порог,
        "considered": {
            "inspections": inspections,
            "units": units,
            "findings": len(rows),
            "with_suggestion": с_предложением,
            "without_confidence": без_уверенности,
            "below_threshold": ниже_порога,
            "corrected": поправлено,
            "no_level_proposed": без_класса,
            "no_zone_proposed": без_зоны,
        },
        "code_misses": промахи_кодов,
        "level_misses": промахи_классов,
        "zone_misses": промахи_зон,
        "applied": False,
        "truncated": truncated or обрезаны_коды or обрезаны_классы or обрезаны_зоны,
        "caveats": list(CAVEATS),
        "status": _status(
            findings=len(rows),
            with_suggestion=с_предложением,
            corrected=поправлено,
            misses=len(промахи_кодов),
        ),
    }
