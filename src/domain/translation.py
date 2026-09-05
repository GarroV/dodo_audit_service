"""Поля методики, заполненные не на том языке, на котором их напечатают (T186).

`pick_text` в `models.py` честно отдаёт то, что стоит в колонке языка, и на
неизвестном языке отказывает — но против колонки, ЗАПОЛНЕННОЙ чужим языком, он
бессилен: с его точки зрения данные в порядке. А отчёт печатает эти поля как
есть (`engine/report.py`: `process_en`, `question_en`, `zone_name_en`), и
партнёр получает документ, где одна строка внезапно не на его языке.

**Правило одностороннее, и это не упрощение.** Ищется письменность, которой в
поле быть не может: кириллица в английской колонке. Обратного правила («русское
поле обязано быть кириллицей») здесь нет намеренно — латиница в русском поле
обычна («Wi-Fi», «Dodo IS»), и такое правило давало бы ложные находки, а
проверку с ложными находками выключают целиком.

Сравнение колонок между собой (`ru == en`) тоже не годится и по той же причине:
совпадение законно. Весь демо-набор англоязычен, и там обе колонки совпадают в
каждой строке — «дефектом» оказался бы он весь.

**Что с находкой делать — решает вызывающий, а не этот модуль.** Правит
методику управляющая компания (D002), продукт её не переписывает и не переводит
за неё: он обязан только не печатать чужой язык молча.
"""

from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass

from .checklist import list_items, list_zones

#: Язык → письменность, наличие которой означает «поле не на этом языке».
#: Третий язык добавляется сюда строкой, а не правкой логики.
FOREIGN_SCRIPT: dict[str, re.Pattern[str]] = {"en": re.compile(r"[Ѐ-ӿ]")}


@dataclass(frozen=True)
class Untranslated:
    """Одно поле методики, написанное не на языке, которым его напечатают.

    `code` — код пункта или зоны: сущности связываются кодами, не формулировками.
    `field` — что именно не переведено (`process`, `question`, `title`).
    `text` — что стоит в поле; нужен тому, кто понесёт правку в управляющую
    компанию, поэтому идёт в журнал стенда, а не в чат аудитору.
    """

    code: str
    field: str
    text: str


def is_foreign(text: str, lang: str) -> bool:
    """Написан ли текст не на языке `lang`. Языка без правила — всегда «нет»."""
    pattern = FOREIGN_SCRIPT.get(lang)
    return bool(text and pattern is not None and pattern.search(text))


def untranslated(
    lang: str,
    *,
    codes: Collection[str] | None = None,
    zones: Collection[str] | None = None,
) -> tuple[Untranslated, ...]:
    """Поля методики, которые на языке `lang` напечатаются чужим языком.

    `codes` и `zones` сужают проверку до записей конкретной проверки: аудитору
    называют то, что уйдёт в ЕГО отчёт, а не весь каталог управляющей компании.
    `None` — вся методика; так её смотрит тот, кто проверяет данные целиком.

    Формулировки берутся теми же методами, которыми их берёт продукт
    (`item.process`, `item.question`, `zone.title`), а не чтением полей по имени
    колонки: язык, которого в методике нет, обязан отказать здесь так же, как
    он отказывает всюду, а не тихо вернуть пустой ответ.
    """
    found: list[Untranslated] = []
    for item in list_items():
        if codes is not None and item.code not in codes:
            continue
        for field, text in (("process", item.process(lang)), ("question", item.question(lang))):
            if is_foreign(text, lang):
                found.append(Untranslated(code=item.code, field=field, text=text))
    for zone in list_zones():
        if zones is not None and zone.code not in zones:
            continue
        title = zone.title(lang)
        if is_foreign(title, lang):
            found.append(Untranslated(code=zone.code, field="title", text=title))
    return tuple(found)
