"""Карта слов: правка через агента, версиями (T144, issue #115).

`photo-cues.md` — документ управляющей компании, лежащий рядом с методикой. По
нему быстрый путь (T113) решает, что записать в проверку **без подтверждения
аудитора**, то есть правка карты меняет то, что уезжает партнёру. До этой
задачи править её было нечем: у чек-листа есть и инструмент, и версии, а у
карты не было ни того, ни другого, и откат делался копией файла вне git.

Три вещи, из которых здесь всё следует.

**Хранилище то же самое.** Карта входит в отпечаток версии методики
(`DATA_FILES`, с 04.09.2026), поэтому её правка уже даёт новую версию — не
хватало только инструмента. Ход правки общий с чек-листом (`checklist.apply_edit`):
снимок версии → правка копии → проверка → новая версия рядом. Публикация
остаётся отдельным действием (D049), а значит откат — это перестановка
указателя, а не поиск копии файла.

**Правит карту этот блок, а не движок, и это не исключение из правила.**
Правило звучало «правила живут в движке, повторять их здесь нельзя» — и оно
про `checklist.csv` и `zones.csv`, чьи правила держит `engine/manage.py`.
Карту движок не читает вовсе: её читает `src.recognize.cues`, и команды для
неё у движка нет. Повторять здесь нечего.

**Проверяет правку тот, кто карту читает.** После правки кандидат разбирается
`src.recognize.cues.load_cues` — тем же кодом, который работает в продукте, — и
результат сверяется с задуманным. Свой разборщик формата означал бы, что блок
согласен сам с собой, а продукт видит другое.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

from ..recognize.cues import CUES_FILE, THRESHOLDS_HEADINGS, load_cues
from .checklist import Outcome, Store, _ensure, _version_dir, apply_edit
from .errors import ChecklistError

#: Код пункта в ячейке — та же форма, по которой их находит разборщик продукта.
_CODE = re.compile(r"\b[A-Z]{3}\d{2}\b")

#: Знаки, из которых состоит строка-разделитель таблицы Markdown.
_RULE_CHARS = set("-: ")

#: Разделитель ячеек. Он же — то, чего не может быть внутри фразы: строка с
#: лишней чертой разъезжается на колонки, и разборщик читает её не так, как
#: задумывал человек.
_PIPE = "|"


@dataclass(frozen=True)
class _Row:
    """Строка файла карты, разобранная настолько, насколько нужно для правки."""

    index: int
    section: str
    cells: tuple[str, ...]
    codes: tuple[str, ...]


def _cells(line: str) -> tuple[str, ...]:
    return tuple(c.strip() for c in line.strip().strip(_PIPE).split(_PIPE))


def _is_rule(cells: tuple[str, ...]) -> bool:
    return all(set(c) <= _RULE_CHARS for c in cells)


def _scan(text: str) -> tuple[list[_Row], dict[str, tuple[str, ...]]]:
    """Строки-подсказки файла и заголовок таблицы каждого раздела.

    Правила отбора — те же, что у разборщика продукта: раздел порогов классов
    пропускается целиком (там коды стоят в первой ячейке и подсказками не
    являются), заголовком таблицы считается первая строка без кодов.
    """
    rows: list[_Row] = []
    headers: dict[str, tuple[str, ...]] = {}
    section = ""
    in_thresholds = False
    for index, line in enumerate(text.splitlines()):
        if line.startswith("## "):
            section = line[3:].strip()
            in_thresholds = line.strip().startswith(THRESHOLDS_HEADINGS)
            continue
        if in_thresholds or not line.lstrip().startswith(_PIPE):
            continue
        cells = _cells(line)
        if len(cells) < 2 or _is_rule(cells):
            continue
        codes = tuple(dict.fromkeys(_CODE.findall(" ".join(cells[1:]))))
        if not codes:
            headers.setdefault(section, cells)
            continue
        rows.append(_Row(index=index, section=section, cells=cells, codes=codes))
    return rows, headers


def _known_codes(data_dir: Path) -> set[str]:
    """Коды пунктов методики этой версии — по её же `checklist.csv`."""
    with (data_dir / "checklist.csv").open(encoding="utf-8-sig", newline="") as f:
        return {(row.get("id") or "").strip().upper() for row in csv.DictReader(f)} - {""}


def cell_codes(cell: str) -> tuple[str, ...]:
    """Коды пунктов ОДНОЙ ячейки таблицы, в порядке появления.

    Публично, потому что этим же разбором сборка предложений (T165) собирает
    вызов правки: ячеек в вызове ровно столько, сколько колонок в разделе, и
    свой разбор ячейки означал бы предложение, которое `edit_photo_cue`
    отклонит как строку не той ширины.
    """
    return tuple(dict.fromkeys(_CODE.findall(cell.upper())))


def _check_phrase(phrase: str) -> str:
    value = (phrase or "").strip()
    if not value:
        raise ChecklistError(
            "Не названа фраза подсказки. Пустая фраза срабатывала бы на любой комментарий "
            "или ни на одном — и то и другое молча"
        )
    if _PIPE in value or "\n" in value:
        raise ChecklistError(
            f"Во фразе «{phrase}» есть знак «{_PIPE}» или перевод строки. Такая строка "
            f"разъезжается на лишние колонки, и разборщик читает её не так, как задумано"
        )
    if _CODE.search(value):
        raise ChecklistError(
            f"Во фразе «{phrase}» стоит код пункта. Код в первой ячейке означает раздел "
            f"порогов классов, а не подсказку: такую строку разборщик продукта пропустит"
        )
    return value


def _check_codes(codes: list[str], *, known: set[str], columns: int) -> tuple[str, ...]:
    """Коды по колонкам — или отказ, называющий, что именно не так."""
    if not codes:
        raise ChecklistError(
            "Не названо ни одного кода пункта. Подсказка без кодов — это заголовок таблицы, "
            "а не строка карты: разборщик продукта прочитает её именно так"
        )
    if len(codes) != columns:
        raise ChecklistError(
            f"В таблице этого раздела {columns} колонок с кодами, а названо {len(codes)}. "
            f"Строка другой ширины разъезжается, и коды попадают не в те колонки — а колонки "
            f"здесь значат разное: «грязь» и «поломка» это два разных вопроса про один объект"
        )
    ячейки: list[str] = []
    for cell in codes:
        найденные = _CODE.findall(cell.upper())
        if not найденные:
            raise ChecklistError(
                f"В колонке «{cell}» нет ни одного кода пункта. Коды выглядят как CLN05: "
                f"сущности связываются кодами, а не формулировками"
            )
        чужие = sorted(set(найденные) - known)
        if чужие:
            raise ChecklistError(
                f"Кодов {', '.join(чужие)} в методике этой версии нет. Подсказка вывела бы "
                f"модели пункт, которого в чек-листе не существует, а быстрый путь записал бы "
                f"его без подтверждения аудитора"
            )
        ячейки.append(", ".join(dict.fromkeys(найденные)))
    return tuple(ячейки)


def _find(rows: list[_Row], phrase: str) -> _Row:
    """Строка карты с ровно этой фразой — или отказ.

    Похожей фразы здесь не ищется намеренно: подстановка «ближайшей» означала бы
    правку не той строки, а карта решает, что записывается без подтверждения
    аудитора.
    """
    нужная = phrase.strip().casefold()
    for row in rows:
        if row.cells[0].casefold() == нужная:
            return row
    raise ChecklistError(
        f"Строки «{phrase}» в карте слов нет. Строки карты называются своей фразой целиком "
        f"и дословно; перечень отдаёт photo_cues"
    )


def _cues_file(data_dir: Path) -> Path:
    return data_dir / CUES_FILE


def _text(data_dir: Path) -> str:
    path = _cues_file(data_dir)
    if not path.is_file():
        raise ChecklistError(
            f"В этой версии методики карты слов ({CUES_FILE}) нет. Файл необязательный, но "
            f"править в нём нечего, пока его не завёл человек"
        )
    return path.read_text(encoding="utf-8")


def _line(cells: tuple[str, ...]) -> str:
    return f"{_PIPE} " + f" {_PIPE} ".join(cells) + f" {_PIPE}"


def _verify(data_dir: Path, *, expected: dict[str, tuple[str, ...] | None]) -> None:
    """Сверить наблюдаемый результат разборщиком ПРОДУКТА, а не своим.

    `expected`: фраза → её коды, или `None`, если строки быть не должно. Без
    этой сверки правка возвращала бы успех, не сделав работу: формат карты
    свободный, и строка, записанная чуть не так, тихо перестаёт быть строкой.
    """
    видно = {cue.phrase: cue.codes for cue in load_cues(_cues_file(data_dir))}
    for phrase, codes in expected.items():
        if codes is None:
            if phrase in видно:
                raise ChecklistError(
                    f"Строка «{phrase}» осталась в карте после снятия: правка записана, но "
                    f"продукт видит прежнее"
                )
            continue
        if видно.get(phrase) != codes:
            raise ChecklistError(
                f"После правки разборщик продукта видит у строки «{phrase}» коды "
                f"{видно.get(phrase)}, а не {codes}. Правка записана не так, как задумано"
            )


# --- чтение -------------------------------------------------------------------


def rows_of(store: Store, *, version: str | None = None) -> tuple[str, tuple[_Row, ...]]:
    """Строки карты слов версии — и имя самой версии, чьи это строки.

    Отдельно от `read` намеренно: `read` собирает ОТВЕТ агенту (вложенные
    словари, ключи протокола), а сборке предложений (T165) нужны сами строки с
    типами. Разобрать ответ обратно значило бы читать свой же вывод — и
    молча разъехаться с ним при первой правке формы ответа.
    """
    каталог = _version_dir(store, _ensure(store) if version is None else version)
    строки, _ = _scan(_text(каталог))
    return каталог.name, tuple(строки)


def read(store: Store, *, version: str | None = None) -> dict[str, object]:
    """Карта слов версии: разделы и строки, как их видит продукт."""
    каталог = _version_dir(store, _ensure(store) if version is None else version)
    rows, headers = _scan(_text(каталог))
    разделы: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        разделы.setdefault(row.section, []).append(
            {"phrase": row.cells[0], "codes": list(row.codes), "cells": list(row.cells[1:])}
        )
    return {
        "version": каталог.name,
        "file": CUES_FILE,
        "count": len(rows),
        "status": (
            f"{len(rows)} cue rows in {len(разделы)} sections; the map only adds and reorders "
            f"candidates and never trims them, and the thresholds section is not part of it"
        ),
        "sections": [
            {
                "section": name,
                "columns": list(headers.get(name, ())[1:]),
                "cues": строки,
            }
            for name, строки in разделы.items()
        ],
    }


# --- правка -------------------------------------------------------------------


def add(
    store: Store,
    *,
    tenant: str,
    section: str,
    phrase: str,
    codes: list[str],
    version_name: str | None = None,
    note: str | None = None,
) -> Outcome:
    """Завести новую строку карты в названном разделе."""
    фраза = _check_phrase(phrase)

    def _mutate(кандидат: Path, _holder: Path) -> tuple[str | None, str]:
        текст = _text(кандидат)
        rows, headers = _scan(текст)
        if section.strip() not in headers:
            raise ChecklistError(
                f"Раздела «{section}» в карте слов нет. Опечатка завела бы раздел-двойник, и "
                f"половина карты разъехалась бы по двум местам. Разделы: "
                f"{', '.join(sorted(headers)) or 'нет'}"
            )
        if any(row.cells[0].casefold() == фраза.casefold() for row in rows):
            raise ChecklistError(
                f"Строка «{фраза}» в карте уже есть. Две строки с одной фразой — это правка "
                f"мимо цели: работать будет первая, а править человек станет вторую"
            )
        заголовок = headers[section.strip()]
        ячейки = _check_codes(codes, known=_known_codes(кандидат), columns=len(заголовок) - 1)
        свои = [row for row in rows if row.section == section.strip()]
        куда = (свои[-1].index if свои else _scan(текст)[0][0].index) + 1
        строки = текст.splitlines()
        строки.insert(куда, _line((фраза, *ячейки)))
        _cues_file(кандидат).write_text("\n".join(строки) + "\n", encoding="utf-8")
        коды = tuple(dict.fromkeys(_CODE.findall(" ".join(ячейки))))
        _verify(кандидат, expected={фраза: коды})
        return None, f"cue «{фраза}» added to section «{section.strip()}»"

    return apply_edit(
        store,
        tenant=tenant,
        tool="add_photo_cue",
        mutate=_mutate,
        version_name=version_name,
        note=note,
    )


def edit(
    store: Store,
    *,
    tenant: str,
    phrase: str,
    codes: list[str] | None = None,
    new_phrase: str | None = None,
    version_name: str | None = None,
    note: str | None = None,
) -> Outcome:
    """Поправить существующую строку: её коды, её фразу или и то и другое."""
    if codes is None and new_phrase is None:
        raise ChecklistError(
            "Не сказано, что менять: ни коды, ни фраза не названы. Правка без изменений "
            "записала бы в журнал правку, которой не было"
        )
    новая = _check_phrase(new_phrase) if new_phrase is not None else None

    def _mutate(кандидат: Path, _holder: Path) -> tuple[str | None, str]:
        текст = _text(кандидат)
        rows, headers = _scan(текст)
        строка = _find(rows, phrase)
        заголовок = headers.get(строка.section, строка.cells)
        ячейки = (
            _check_codes(codes, known=_known_codes(кандидат), columns=len(заголовок) - 1)
            if codes is not None
            else строка.cells[1:]
        )
        итоговая = новая if новая is not None else строка.cells[0]
        if новая is not None and новая.casefold() != строка.cells[0].casefold():
            занято = any(row.cells[0].casefold() == новая.casefold() for row in rows)
            if занято:
                raise ChecklistError(f"Строка «{новая}» в карте уже есть")
        строки = текст.splitlines()
        строки[строка.index] = _line((итоговая, *ячейки))
        _cues_file(кандидат).write_text("\n".join(строки) + "\n", encoding="utf-8")
        коды = tuple(dict.fromkeys(_CODE.findall(" ".join(ячейки))))
        ожидаемо: dict[str, tuple[str, ...] | None] = {итоговая: коды}
        if итоговая != строка.cells[0]:
            ожидаемо[строка.cells[0]] = None
        _verify(кандидат, expected=ожидаемо)
        return None, f"cue «{строка.cells[0]}» rewritten as «{итоговая}»"

    return apply_edit(
        store,
        tenant=tenant,
        tool="edit_photo_cue",
        mutate=_mutate,
        version_name=version_name,
        note=note,
    )


def remove(
    store: Store,
    *,
    tenant: str,
    phrase: str,
    version_name: str | None = None,
    note: str | None = None,
) -> Outcome:
    """Снять строку карты. Прежние версии остаются, поэтому откат — публикация."""

    def _mutate(кандидат: Path, _holder: Path) -> tuple[str | None, str]:
        текст = _text(кандидат)
        rows, _ = _scan(текст)
        строка = _find(rows, phrase)
        строки = текст.splitlines()
        del строки[строка.index]
        _cues_file(кандидат).write_text("\n".join(строки) + "\n", encoding="utf-8")
        _verify(кандидат, expected={строка.cells[0]: None})
        return None, f"cue «{строка.cells[0]}» removed"

    return apply_edit(
        store,
        tenant=tenant,
        tool="remove_photo_cue",
        mutate=_mutate,
        version_name=version_name,
        note=note,
    )
