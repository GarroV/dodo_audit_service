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

Ни одного обращения к сети: `fast_path` детерминированный, замер бесплатный.

Запуск:  python tools/fastpath_measure.py [--root PATH]
Коды возврата: 0 — норма, 1 — есть неверное срабатывание, 2 — боевых данных нет.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
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
from src.recognize.cues import cues_path  # noqa: E402
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
        spoken = zone_from_words(record.note)
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
        spoken = zone_from_words(record.note)
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="корень репозитория (по умолчанию — вычисленный от файла; параметр для тестов)",
    )
    args = parser.parse_args(argv)

    records = load_records(args.root)
    if not records:
        print(
            "Боевых данных нет: examples/*/inspection.json не найдены (они вне git, "
            "решение D002). Замерять нечего — это не поломка инструмента."
        )
        return 2

    measured = modes(records)
    print(render(measured))

    # Ненулевой код — на неверное срабатывание в ЛЮБОМ из способов. У боевого
    # оно опаснее всего, но и на эталонной зоне оно значит, что карта слов
    # ведёт к чужому пункту, — а эталонную зону часто подставляет та же память.
    if any(mode.wrong for mode in measured):
        return 1
    return 0


if __name__ == "__main__":
    os.environ.setdefault("AUDIT_DATA_DIR", str(ROOT / "data"))
    raise SystemExit(main())
