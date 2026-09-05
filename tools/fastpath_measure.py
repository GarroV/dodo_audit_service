#!/usr/bin/env python3
"""T113, T125: доля срабатываний быстрого пути — так, как его зовёт живой бот.

`fast_path` (`src/recognize/fastpath.py`) отвечает на комментарий аудитора
готовым пунктом без вызова модели, но только когда слова не оставляют выбора.
Отказ — законный ответ, срабатывание — нет: аудитор подтверждает предложенный
пункт нажатием, поэтому неверное срабатывание опаснее, чем полное отсутствие
быстрого пути. Замер меряет именно это на 17 боевых записях двух проверок
(`examples/belgrade-1`, `examples/belgrade-2`) и гоняется после каждого
пополнения карты слов `data/photo-cues.md`.

**Чем этот замер был неверен до T125 (задача #100).** Он звал
`fast_path(note, record.zone)`, подставляя зону из ЭТАЛОННОЙ записи — то есть
из уже известного правильного ответа. У бота этого знания нет. Он берёт зону
так (`src/bot/routers/record.py::_analyze`):

    spoken = zone_from_words(note)              # src/bot/zones.py
    zone_hint = spoken or sidecar.read(chat).zone

— слова текущего комментария первичны, память о прошлой записи (D048) идёт
только тогда, когда о зоне в словах не сказано ничего. Официальные 18% были
сняты с подсказки, которой на точке не будет, и к живому боту отношения не
имели.

**Что меряется теперь.** Три способа добыть зону, а не один, и каждый честно
подписан:

* `hints_bot` — как зовёт бот: слова, иначе память проверки. Это и есть
  боевое число.
* `hints_spoken` — только слова, без памяти: пол замера, то есть срабатывания,
  за которыми стоит названная человеком зона, а не догадка.
* `hints_reference` — эталонная зона из записи. Верхняя граница, живому боту
  недостижимая; оставлена, чтобы видеть цену незнания зоны, а не выдавать её
  за результат.

**Память смоделирована зоной предыдущей записи той же проверки.** Бот пишет в
заметки зону только что созданной записи (`sidecar.remember_zone` в `_save`,
`src/bot/routers/record.py`), а в `examples/*/inspection.json` записи лежат в
порядке создания и несут ту зону, которая в итоге записана. Перед первой
записью проверки памяти нет — значит, зоны нет вовсе.

**Числа живут ровно до следующей правки карты слов** (D066: карту ведёт
управляющая компания). Поэтому замер печатает дату и отпечаток карты сам:
число без версии карты через неделю нечем проверить.

**Второй раздел (T195, задача #160) меряет отдельный вопрос:** не «какой код
показан», а «сколько отрицаний правило `_denied_words`
(`src/recognize/fastpath.py`) действительно замечает». Корпус строится ИЗ
САМОЙ КАРТЫ на ходу (`render_negation_section`, `affirmative_bases`,
`negation_outcomes`, `insurance_lost`) — тем же приёмом, что иглы в
`tests/test_methodology_leak.py`: замороженный список строк карты был бы
такой же утечкой методики, как и её цитата. Число тоже привязано к отпечатку
карты в шапке первого раздела и живёт ровно до той же правки (D066). На код
возврата этот раздел не влияет: коды 0/1/2 остаются про первый раздел.

Ни одного обращения к сети: `fast_path` детерминированный, замер бесплатный.

Запуск:  python tools/fastpath_measure.py [--root PATH]
Коды возврата: 0 — норма, 1 — есть неверное срабатывание, 2 — боевых данных нет.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Разбор зоны по словам живёт в блоке `bot` и правится там же. Замер его именно
# ЗОВЁТ, а не повторяет: своя копия правил разошлась бы с ботом молча, и замер
# снова мерил бы не то, что происходит на точке. Контракт слоёв (import-linter,
# `pyproject.toml`) этим не задет — он описывает пакет `src`, а `tools/` это
# инструменты ПОВЕРХ продукта, не его ярус.
from src.bot.zones import zone_from_words  # noqa: E402

#: Замер идёт по выгрузкам `examples/`, а не по живой проверке: издания, по
#: которому её вели, здесь нет, и справочники читаются действующие (T225).
NO_CHAT = None
from src.domain import get_item  # noqa: E402
from src.domain.errors import ConfigError  # noqa: E402
from src.recognize import language  # noqa: E402
from src.recognize.cues import Cue, cues_path, load_cues, stems  # noqa: E402
from src.recognize.fastpath import fast_path  # noqa: E402

#: Откуда взялась зона, с которой позвали быстрый путь.
FROM_WORDS = "из слов"
FROM_MEMORY = "из памяти"
FROM_NOWHERE = "неоткуда"
FROM_REFERENCE = "из эталона"


@dataclass(frozen=True)
class Record:
    """Одна боевая запись: что подтвердил аудитор против того, что он написал."""

    code: str
    zone: str
    note: str
    source: str


@dataclass(frozen=True)
class Hint:
    """Зона, с которой зовут быстрый путь, и происхождение этой зоны."""

    zone: str | None
    source: str


@dataclass(frozen=True)
class Outcome:
    """Результат одного вызова `fast_path` на боевой записи."""

    record: Record
    hint: Hint
    #: Код, показанный быстрым путём, или `None`, если он не сработал.
    fired: str | None
    #: Причина отказа. Пусто, когда `fired` не `None`.
    reason: str

    @property
    def correct(self) -> bool | None:
        """`None`, когда быстрый путь не сработал — вопрос «верно или нет» тут не встаёт."""
        if self.fired is None:
            return None
        return self.fired == self.record.code


@dataclass(frozen=True)
class Mode:
    """Один способ добыть зону: как называется и что из него вышло."""

    title: str
    outcomes: tuple[Outcome, ...]

    @property
    def fired(self) -> tuple[Outcome, ...]:
        return tuple(o for o in self.outcomes if o.fired is not None)

    @property
    def wrong(self) -> tuple[Outcome, ...]:
        return tuple(o for o in self.outcomes if o.correct is False)

    @property
    def share(self) -> float:
        return len(self.fired) / len(self.outcomes) * 100 if self.outcomes else 0.0


def load_records(root: Path) -> tuple[Record, ...]:
    """Боевые записи из `examples/*/inspection.json`, в порядке их создания.

    Порядок важен: память бота о прошлой зоне (D048) моделируется предыдущей
    записью той же проверки, и перемешать записи значило бы смоделировать
    другой обход точки.

    Файлы лежат вне git (решение D002), поэтому на чужой машине их может не
    быть — пустой кортеж тогда не ошибка чтения, а законный итог.
    """
    records: list[Record] = []
    for path in sorted(root.glob("examples/*/inspection.json")):
        source = path.parent.name
        data = json.loads(path.read_text(encoding="utf-8"))
        for finding in data.get("findings", []):
            records.append(
                Record(
                    code=finding["qid"],
                    zone=finding["zone"],
                    note=finding["evidence"],
                    source=source,
                )
            )
    return tuple(records)


def hints_reference(records: Sequence[Record]) -> tuple[Hint, ...]:
    """Зона из эталонной записи — знание, которого у бота нет. Верхняя граница."""
    return tuple(Hint(zone=r.zone, source=FROM_REFERENCE) for r in records)


def hints_spoken(records: Sequence[Record]) -> tuple[Hint, ...]:
    """Только слова комментария, без памяти: пол замера."""
    out: list[Hint] = []
    for record in records:
        spoken = zone_from_words(record.note, chat_id=NO_CHAT)
        out.append(Hint(zone=spoken, source=FROM_WORDS if spoken else FROM_NOWHERE))
    return tuple(out)


def hints_bot(records: Sequence[Record]) -> tuple[Hint, ...]:
    """Как зовёт бот: слова комментария, иначе память о прошлой записи проверки.

    Память сбрасывается на смене проверки: у каждой свой чат и свои заметки.
    Она догадка, а не факт (D048), и подставленная ею чужая зона — не изъян
    модели замера, а ровно то, что происходит на точке.
    """
    out: list[Hint] = []
    memory: str | None = None
    inspection: str | None = None
    for record in records:
        if record.source != inspection:
            inspection, memory = record.source, None
        spoken = zone_from_words(record.note, chat_id=NO_CHAT)
        if spoken:
            out.append(Hint(zone=spoken, source=FROM_WORDS))
        elif memory:
            out.append(Hint(zone=memory, source=FROM_MEMORY))
        else:
            out.append(Hint(zone=None, source=FROM_NOWHERE))
        # Бот запоминает зону СОЗДАННОЙ записи, а созданная запись — эталонная.
        memory = record.zone
    return tuple(out)


def measure(records: Sequence[Record], hints: Sequence[Hint] | None = None) -> tuple[Outcome, ...]:
    """Прогнать `fast_path` по каждой записи. По умолчанию — ровно так, как зовёт бот."""
    chosen = hints if hints is not None else hints_bot(records)
    outcomes: list[Outcome] = []
    for record, hint in zip(records, chosen, strict=True):
        result = fast_path(record.note, hint.zone)
        fired = result.item.code if result.item else None
        outcomes.append(Outcome(record=record, hint=hint, fired=fired, reason=result.reason))
    return tuple(outcomes)


def modes(records: Sequence[Record]) -> tuple[Mode, ...]:
    """Три способа добыть зону. Первый — боевой, остальные для сравнения с ним."""
    return (
        Mode("Как зовёт бот: слова, иначе память проверки", measure(records, hints_bot(records))),
        Mode("Только слова аудитора, без памяти", measure(records, hints_spoken(records))),
        Mode(
            "Эталонная зона из записи — ВЕРХНЯЯ ГРАНИЦА, живому боту недостижима",
            measure(records, hints_reference(records)),
        ),
    )


def fingerprint() -> str:
    """Отпечаток карты слов: число замера без версии карты нечем проверить (D066)."""
    path = cues_path()
    if not path.is_file():
        return "карта не найдена"
    return f"md5 {hashlib.md5(path.read_bytes(), usedforsecurity=False).hexdigest()}"


def _verdict(outcome: Outcome) -> str:
    if outcome.fired is None:
        return "нет срабатывания"
    return "верно" if outcome.correct else "НЕВЕРНО"


def _table(outcomes: Sequence[Outcome]) -> list[str]:
    lines = [
        "| Проверка | Эталон | Зона у бота | Откуда зона | Быстрый путь | Вердикт | Отказ |",
        "|---|---|---|---|---|---|---|",
    ]
    lines.extend(
        f"| {o.record.source} | {o.record.code} | {o.hint.zone or '—'} | {o.hint.source} | "
        f"{o.fired or '—'} | {_verdict(o)} | {o.reason or '—'} |"
        for o in outcomes
    )
    return lines


def _zone_sources(outcomes: Sequence[Outcome]) -> str:
    counted = Counter(o.hint.source for o in outcomes)
    parts = ", ".join(
        f"{name}: {counted.get(name, 0)}" for name in (FROM_WORDS, FROM_MEMORY, FROM_NOWHERE)
    )
    return f"Откуда бот брал зону — {parts} (всего записей: {len(outcomes)})."


def _totals(measured: Sequence[Mode]) -> list[str]:
    lines = [
        "| Способ добыть зону | Срабатываний | Доля | Верных | НЕВЕРНЫХ |",
        "|---|---|---|---|---|",
    ]
    lines.extend(
        f"| {m.title} | {len(m.fired)} | {m.share:.0f}% | "
        f"{len(m.fired) - len(m.wrong)} | {len(m.wrong)} |"
        for m in measured
    )
    return lines


def _reasons(outcomes: Sequence[Outcome]) -> list[str]:
    counted = Counter(o.reason for o in outcomes if o.fired is None)
    if not counted:
        return []
    lines = ["Почему быстрый путь не сработал у бота (по убыванию частоты):"]
    lines.extend(f"  {count} × {reason}" for reason, count in counted.most_common())
    return lines


def render(measured: Sequence[Mode]) -> str:
    """Отчёт замера: шапка с версией карты, разбор по записям, итог по трём способам."""
    live = measured[0]
    lines = [
        f"Замер быстрого пути на боевых записях — {date.today().isoformat()}",
        f"Карта слов data/photo-cues.md: {fingerprint()}. Числа привязаны к ЭТОЙ версии "
        "карты: карту ведёт управляющая компания (D066), после её правки замер "
        "снимается заново.",
        "",
        "Зона берётся так же, как в src/bot/routers/record.py::_analyze — "
        "zone_from_words(комментарий), иначе память о прошлой записи проверки.",
        "",
        *_table(live.outcomes),
        "",
        _zone_sources(live.outcomes),
        "",
        *_totals(measured),
        "",
        *_reasons(live.outcomes),
    ]
    return "\n".join(lines)


# --- Раздел 2: замер защиты от отрицания (T195, задача #160) ---------------
#
# У раздела выше — 17 боевых записей и один вопрос («какой код фактически
# показан»). Здесь вопрос другой: «сколько ОТРИЦАНИЙ правило T195 замечает»,
# и боевых записей для него не хватит — отрицание в них случается на
# единицах комментариев. Корпус строится ИЗ САМОЙ КАРТЫ на ходу, тем же
# приёмом, что и иглы в `tests/test_methodology_leak.py`: репозиторий
# публичный, а замороженный список строк карты был бы такой же утечкой
# методики управляющей компании, как и цитата в тесте.
#
# Числа живут ровно до следующей правки карты (D066), как и у раздела выше —
# отдельный отпечаток тут не печатается: он уже стоит в шапке первого
# раздела, и оба раздела гоняются по одной и той же карте одного вызова.

#: Вид отрицания — те же четыре способа, которыми T195 переворачивает смысл
#: слова (`_denied_words`, `src/recognize/fastpath.py`): частица перед словом,
#: частица «без» перед словом, «нет» после слова и частица, отделённая от
#: слова служебным словом, которое отрицание обязано перешагнуть.
NEGATION_BEFORE = "«не» перед словом"
NEGATION_WITHOUT = "«без» перед словом"
NEGATION_AFTER = "«нет» после слова"
NEGATION_THROUGH_FUNCTION_WORD = "через служебное слово"

#: Порядок видов — он же порядок строк во второй таблице отчёта.
_NEGATION_KINDS = (
    NEGATION_BEFORE,
    NEGATION_WITHOUT,
    NEGATION_AFTER,
    NEGATION_THROUGH_FUNCTION_WORD,
)

#: Слово фразы строки карты. Своя регулярка, а не импорт `_WORD` из
#: `src.recognize.cues`: там она приватная, и тянуть приватность модуля
#: продукта в инструмент поверх него — значило бы держать эти два места
#: синхронными по соглашению, а не по контракту. Символ `ё` в класс не входит
#: намеренно: фраза перед разбором приводится к тому же виду, что видит сам
#: продукт (`.lower().replace("ё", "е")`), и после замены он в тексте уже не
#: встречается.
_PHRASE_WORD = re.compile(r"[а-яa-z0-9]+")


@dataclass(frozen=True)
class AffirmativeBase:
    """Заметка и зона, на которых строка карты сработала утвердительно.

    Отрицать есть что только там, где уже есть утверждение: строка карты, для
    которой не нашлось ни одной пары (заметка, зона) со срабатыванием, в
    замер отрицания не попадает вовсе — шаг 1 задания.

    `code` запомнен отдельно от `note`/`zone` не для количества полей, а для
    перестраховки (ниже): она обязана убедиться, что на безобидной приписке
    срабатывает ТОТ ЖЕ пункт, а не любой, — иначе приписка могла бы незаметно
    подменить код, и это выглядело бы в отчёте как «срабатывание сохранено»,
    хотя аудитору подсунули другой пункт.
    """

    cue: Cue
    note: str
    zone: str
    code: str


def _column_probe_words() -> tuple[str, ...]:
    """По одному слову на каждую колонку встроенного словаря — добавка к фразе.

    Строка карты не всегда однозначна сама по себе: «Печь» распадается на
    «грязь» и «поломку», и без слова колонки быстрый путь честно откажет по
    `NO_COLUMN`. Слово встроенного словаря (не карты: `language.column_words()`,
    а не `cues.column_words()`) добирается детерминированно — первое по
    алфавиту, — чтобы прогон за прогоном пробовал одно и то же слово, а не
    зависел от порядка словаря в файле правил.
    """
    return tuple(sorted(words)[0] for words in language.column_words().values())


def _zones_for_cue(cue: Cue) -> tuple[str, ...]:
    """Зоны, в которых применим хоть один код строки — кандидаты для зова `fast_path`.

    Зона `*` («применимо везде», `ChecklistItem.zones`) пропускается: это не
    имя зоны, а отсутствие ограничения, и подставить её в `zone_hint` нельзя —
    у неё нет соответствующей записи в `zones.csv`, с которой сверяется бот.
    """
    zones: list[str] = []
    seen: set[str] = set()
    for code in cue.codes:
        for zone in get_item(code).zones:
            if zone == "*" or zone in seen:
                continue
            seen.add(zone)
            zones.append(zone)
    return tuple(zones)


def find_affirmative_base(cue: Cue) -> AffirmativeBase | None:
    """Первая пара (заметка, зона), на которой строка сработала утвердительно.

    Перебор идёт по зонам строки, а внутри зоны — по заметкам: сама фраза,
    затем фраза с одним словом каждой колонки (шаг 1 задания). Первое
    срабатывание и есть база; строка, не сработавшая ни на одной комбинации
    (в названной методике нет зоны, применимой к её кодам, либо колонка не
    выбирается embedded-словарём вовсе), в замер не попадает — проверять
    отрицание тут не на чем, а не дефект правила.
    """
    probes = (cue.phrase, *(f"{cue.phrase}, {word}" for word in _column_probe_words()))
    for zone in _zones_for_cue(cue):
        for note in probes:
            result = fast_path(note, zone)
            if result.item is not None:
                return AffirmativeBase(cue=cue, note=note, zone=zone, code=result.item.code)
    return None


def affirmative_bases(cues: Sequence[Cue]) -> tuple[AffirmativeBase, ...]:
    """Строки карты, для которых нашлась утвердительная база — корпус замера отрицания."""
    bases: list[AffirmativeBase] = []
    for cue in cues:
        base = find_affirmative_base(cue)
        if base is not None:
            bases.append(base)
    return tuple(bases)


@dataclass(frozen=True)
class NegationOutcome:
    """Один вызов `fast_path` на отрицании утвердительной базы.

    `missed` — правило T195 отрицания НЕ заметило: быстрый путь сработал так
    же, как на утвердительной заметке. Пропуском считается именно ЭТО, любое
    срабатывание, а не несовпадение кода с базой: `_covered`
    (`src/recognize/fastpath.py`) не пытается угадывать код по огрызку слов,
    она либо видит строку целиком с тем же отрицанием, либо не видит вовсе —
    третьего, «увидела не то», в её устройстве нет.
    """

    base: AffirmativeBase
    kind: str
    note: str
    missed: bool


def _negation_variants(base: AffirmativeBase) -> tuple[tuple[str, str], ...]:
    """Четыре отрицания на каждое значимое слово ФРАЗЫ строки (шаг 2 задания).

    Позиции слова берутся из фразы, приведённой к тому виду, в котором её
    видит сам разбор (`.lower().replace("ё", "е")`) — так же, как это делает
    `_denied_words` в `src/recognize/fastpath.py` через `words_and_gaps`. Срез
    применяется уже к НАЙДЕННОЙ ЗАМЕТКЕ, а не к фразе: заметка либо равна
    фразе, либо начинается с неё и продолжается словом колонки через запятую
    (шаг 1), поэтому индексы фразы остаются верными индексами её префикса и в
    заметке тоже.
    """
    phrase_lower = base.cue.phrase.lower().replace("ё", "е")
    note = base.note
    variants: list[tuple[str, str]] = []
    for match in _PHRASE_WORD.finditer(phrase_lower):
        word = match.group()
        if not stems(word):
            continue
        start, end = match.span()
        variants.append((NEGATION_BEFORE, f"{note[:start]}не {note[start:]}"))
        variants.append((NEGATION_WITHOUT, f"{note[:start]}без {note[start:]}"))
        variants.append((NEGATION_AFTER, f"{note[:end]} нет{note[end:]}"))
        variants.append((NEGATION_THROUGH_FUNCTION_WORD, f"{note[:start]}не в {note[start:]}"))
    return tuple(variants)


def negation_outcomes(bases: Sequence[AffirmativeBase]) -> tuple[NegationOutcome, ...]:
    """Прогнать все четыре отрицания по каждому значимому слову каждой базы."""
    outcomes: list[NegationOutcome] = []
    for base in bases:
        for kind, note in _negation_variants(base):
            missed = fast_path(note, base.zone).item is not None
            outcomes.append(NegationOutcome(base=base, kind=kind, note=note, missed=missed))
    return tuple(outcomes)


def _insurance_notes(note: str) -> tuple[str, str, str]:
    """Три безобидных отрицания вокруг заметки — половина смысла замера (шаг 3 задания).

    Отрицание здесь стоит в другой части фразы или после точки — ровно тот
    случай, для которого `_denied_words` останавливается на знаке препинания
    (T195): без этой остановки «жалоб нет, в зале урна переполнена» теряет
    срабатывание вовсе, потому что «нет» и «урна» стоят по соседству через
    запятую. Каждая из трёх обязана СОХРАНИТЬ срабатывание — иначе правило не
    осторожничает, а тихо обнуляет быстрый путь.
    """
    return (
        f"жалоб нет, {note}",
        f"{note}, остальное без замечаний",
        f"замечаний нет. {note}",
    )


def insurance_lost(bases: Sequence[AffirmativeBase]) -> tuple[AffirmativeBase, ...]:
    """Базы, потерявшие срабатывание хоть на одном из трёх безобидных отрицаний.

    Слишком строгое правило обнуляет быстрый путь молча, и цена такой
    перестраховки не видна нигде, кроме как в замере вроде этого: доля
    срабатываний просто падает, а выглядит это как «стало безопаснее». Именно
    поэтому перестраховка — не строка в скобках к основной таблице, а
    отдельная проверка: без неё правило могло бы стать неотличимо строгим
    (отрицающим всё подряд) и остаться зелёным по всем меркам раздела выше.
    """
    lost: list[AffirmativeBase] = []
    for base in bases:
        for note in _insurance_notes(base.note):
            result = fast_path(note, base.zone)
            if result.item is None or result.item.code != base.code:
                lost.append(base)
                break
    return tuple(lost)


def _negation_summary_table(
    bases: Sequence[AffirmativeBase], outcomes: Sequence[NegationOutcome]
) -> list[str]:
    total = len(outcomes)
    missed = sum(1 for o in outcomes if o.missed)
    share = (total - missed) / total * 100 if total else 0.0
    return [
        "| Строк с утвердительным срабатыванием | Отрицаний всего | Пропущено | Доля пойманных |",
        "|---|---|---|---|",
        f"| {len(bases)} | {total} | {missed} | {share:.0f}% |",
    ]


def _negation_kinds_table(outcomes: Sequence[NegationOutcome]) -> list[str]:
    lines = ["| Вид отрицания | Всего | Пропущено |", "|---|---|---|"]
    for kind in _NEGATION_KINDS:
        of_kind = [o for o in outcomes if o.kind == kind]
        lines.append(f"| {kind} | {len(of_kind)} | {sum(1 for o in of_kind if o.missed)} |")
    return lines


def render_negation_section(cues: Sequence[Cue]) -> str:
    """Замер защиты от отрицания (T195) — второй раздел отчёта.

    Пустая карта (`load_cues()` не нашёл файл или файл пуст, D068 — карта
    необязательна) — не поломка инструмента и не повод падать: раздел тогда
    печатает ровно одну объясняющую строку и ничего не считает, как и весь
    остальной продукт при отсутствующей карте (быстрый путь просто молчит).
    """
    if not cues:
        return "карты нет — отрицание проверять не на чем"

    bases = affirmative_bases(cues)
    outcomes = negation_outcomes(bases)
    lost = insurance_lost(bases)

    lines = [
        "Замер защиты от отрицания (T195)",
        "",
        *_negation_summary_table(bases, outcomes),
        "",
        *_negation_kinds_table(outcomes),
        "",
        f"Перестраховка: потеряно {len(lost)} из {len(bases)} строк на трёх "
        "безобидных отрицаниях («жалоб нет, …», «…, остальное без замечаний», "
        "«замечаний нет. …»).",
        "",
        "Пропуск — не всегда дефект правила T195. Строка карты, сама "
        "сформулированная через отсутствие («без ярлыка»), законно совпадает "
        "с повторным отрицанием — это двойное отрицание, а не брешь. Вторая "
        "причина — попадание в строку-двойник, которая сама сформулирована "
        "через отрицание и потому реагирует зеркально. Третьей причиной был "
        "повтор одной и той же основы внутри строки (второе вхождение "
        "оставалось сказанным, даже когда отрицания коснулось только первое) — "
        "это была настоящая дыра, и T197 её закрыл: основа, сказанная и "
        "отрицаемая разом, не считается ни сказанной, ни отрицаемой.",
    ]
    return "\n".join(lines)


def _cues_or_empty() -> tuple[Cue, ...]:
    """Строки карты — или пустой корпус, если окружение вовсе не настроено.

    `load_cues()` сам не глотает `ConfigError`: без `AUDIT_DATA_DIR` продукту
    неоткуда узнать методику, и молчаливый пустой чек-лист выглядел бы как
    честное «нарушений нет» (`src/domain/errors.py`). У ЭТОГО инструмента
    вход шире: свежая копия репозитория без единого файла методики — обычное
    дело (D002), а не поломка, и трассировка исключения вместо отчёта здесь
    обрывала бы замер там, где первый раздел просто печатает «боевых данных
    нет» и код 2. Отсутствие карты внутри настроенного окружения (D068) до
    сюда не доходит вовсе — там `load_cues()` и без этой обёртки отдаёт пустой
    кортеж, не поднимая исключения.
    """
    try:
        return load_cues()
    except ConfigError:
        return ()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="корень репозитория (по умолчанию — вычисленный от файла; параметр для тестов)",
    )
    args = parser.parse_args(argv)

    # Читается независимо от боевых записей ниже: карте кадров examples/ не
    # нужны, а раздел про отрицание — не про то, что подтвердил аудитор, а
    # про саму карту (D066). Без боевых записей первый раздел печатать нечего
    # (код возврата 2), а этот — есть что, если карта на месте.
    negation_section = render_negation_section(_cues_or_empty())

    records = load_records(args.root)
    if not records:
        print(
            "Боевых данных нет: examples/*/inspection.json не найдены (они вне git, "
            "решение D002). Замерять нечего — это не поломка инструмента."
        )
        print()
        print(negation_section)
        return 2

    measured = modes(records)
    print(render(measured))
    print()
    print(negation_section)

    # Ненулевой код — на неверное срабатывание в ЛЮБОМ из способов. У боевого
    # оно опаснее всего, но и на эталонной зоне оно значит, что карта слов
    # ведёт к чужому пункту, — а эталонную зону часто подставляет та же память.
    # Раздел про отрицание на код возврата не влияет вовсе: перестраховка и
    # пропуск здесь — числа для замера, а не сигнал «неверное срабатывание»
    # в смысле первого раздела, и мешать их в одну цифру значило бы одной
    # придиркой к отрицанию красить прогон, который проверяет совсем другое.
    if any(mode.wrong for mode in measured):
        return 1
    return 0


if __name__ == "__main__":
    os.environ.setdefault("AUDIT_DATA_DIR", str(ROOT / "data"))
    raise SystemExit(main())
