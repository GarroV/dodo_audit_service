#!/usr/bin/env python3
"""T113: доля однозначных срабатываний быстрого пути на боевых данных.

`fast_path` (`src/recognize/fastpath.py`) отвечает на комментарий аудитора
готовым пунктом без вызова модели, но только когда слова не оставляют выбора.
Отказ — законный ответ, срабатывание — нет: аудитор подтверждает предложенный
пункт нажатием, поэтому неверное срабатывание опаснее, чем полное отсутствие
быстрого пути. Замер меряет именно это на 17 боевых записях двух проверок
(`examples/belgrade-1`, `examples/belgrade-2`) и гоняется после каждого
пополнения карты слов `data/photo-cues.md` — карта растёт, доля срабатываний
должна расти вместе с ней, а не число неверных.

Ни одного обращения к сети: `fast_path` детерминированный, замер бесплатный.

Запуск:  python tools/fastpath_measure.py [--root PATH]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.recognize.fastpath import fast_path  # noqa: E402


@dataclass(frozen=True)
class Record:
    """Одна боевая запись: что подтвердил аудитор против того, что он написал."""

    code: str
    zone: str
    note: str
    source: str


@dataclass(frozen=True)
class Outcome:
    """Результат одного вызова `fast_path` на боевой записи."""

    record: Record
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


def load_records(root: Path) -> tuple[Record, ...]:
    """Боевые записи из `examples/*/inspection.json`.

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


def measure(records: Sequence[Record]) -> tuple[Outcome, ...]:
    """Прогнать `fast_path` по каждой записи ровно так, как его зовёт бот."""
    outcomes: list[Outcome] = []
    for record in records:
        result = fast_path(record.note, record.zone)
        fired = result.item.code if result.item else None
        outcomes.append(Outcome(record=record, fired=fired, reason=result.reason))
    return tuple(outcomes)


def render(outcomes: Sequence[Outcome]) -> str:
    """Таблица по записям и итог: доля срабатываний, верных/неверных, причины отказа."""
    lines = [
        "| Проверка | Зона | Эталон | Быстрый путь | Вердикт | Причина отказа |",
        "|---|---|---|---|---|---|",
    ]
    for outcome in outcomes:
        fired = outcome.fired or "—"
        if outcome.fired is None:
            verdict = "нет срабатывания"
        elif outcome.correct:
            verdict = "верно"
        else:
            verdict = "НЕВЕРНО"
        lines.append(
            f"| {outcome.record.source} | {outcome.record.zone} | {outcome.record.code} | "
            f"{fired} | {verdict} | {outcome.reason or '—'} |"
        )

    total = len(outcomes)
    fired_outcomes = [o for o in outcomes if o.fired is not None]
    correct = [o for o in fired_outcomes if o.correct]
    wrong = [o for o in fired_outcomes if not o.correct]
    share = len(fired_outcomes) / total * 100 if total else 0.0

    lines.append("")
    lines.append(
        f"Записей: {total}, однозначных срабатываний: {len(fired_outcomes)} ({share:.0f}%), "
        f"из них верных: {len(correct)}, неверных: {len(wrong)}"
    )

    reasons = Counter(o.reason for o in outcomes if o.fired is None)
    if reasons:
        lines.append("Причины отказа (по убыванию частоты):")
        for reason, count in reasons.most_common():
            lines.append(f"  {count} × {reason}")

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

    outcomes = measure(records)
    print(render(outcomes))

    if any(o.correct is False for o in outcomes):
        return 1
    return 0


if __name__ == "__main__":
    os.environ.setdefault("AUDIT_DATA_DIR", str(ROOT / "data"))
    raise SystemExit(main())
