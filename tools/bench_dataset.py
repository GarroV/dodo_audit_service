"""Оснастка задачи T035: офлайн-набор кадров для замера точности разбора.

Обходит боевые проверки в `examples/` (симлинк на основную копию, сами данные
вне git — решение D002) и для каждого фото каждой записи `findings` строит один
`BenchCase` с эталонным ответом разбора: код пункта, класс и зона записи. Класс
`D0` — информационная запись (замер температуры, фото продукта, настройка
печи): в чек-листе это полноценные предлагаемые пункты (INF09/INF10/INF11), а
не пустое место, поэтому код и класс у них тоже сохраняются — от нарушения их
отличает только `kind`. Набор — вход для последующего замера точности модели
на боевых данных (`tasks.md`, T035).

Ключ `info` из `inspection.json` не используется: по методике он не наполняется
и не должен влиять на разбор (см. `docs/02-domain.md`).

Модуль читает только то, что лежит внутри переданного `examples_dir` — никаких
обращений к `data/`, `src/` или сети.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Класс методики, которым помечается информационная запись (замер температуры,
# фото продукта, настройка печи) — не нарушение. См. docs/02-domain.md.
_INFO_LEVEL = "D0"


@dataclass(frozen=True)
class BenchCase:
    """Один кадр из боевой проверки с эталонным ответом разбора."""

    case_id: str  # "belgrade-1/p09.jpg" — папка проверки + имя файла
    photo: Path  # абсолютный путь к файлу кадра
    zone: str  # зона записи, например "hot_kitchen"
    code: str | None  # ожидаемый код пункта, например "CLN05" или "INF10"
    level: str | None  # ожидаемый класс, например "D1" или "D0" (info)
    kind: str  # "violation" или "info"


def _inspection_dirs(examples_dir: Path) -> list[Path]:
    """Папки проверок внутри `examples_dir`, отсортированные по имени."""
    return sorted(
        (p for p in examples_dir.iterdir() if p.is_dir() and (p / "inspection.json").is_file()),
        key=lambda p: p.name,
    )


def _cases_from_finding(inspection_dir: Path, finding: dict[str, Any]) -> list[BenchCase]:
    """Случаи, порождённые одной записью `findings` — по одному на фото."""
    kind = "info" if finding["level"] == _INFO_LEVEL else "violation"

    cases = []
    for rel_photo in finding["photos"]:
        photo = inspection_dir / rel_photo
        if not photo.is_file():
            raise FileNotFoundError(f"кадр не найден на диске: {photo}")
        case_id = f"{inspection_dir.name}/{photo.name}"
        cases.append(
            BenchCase(
                case_id=case_id,
                photo=photo.resolve(),
                zone=finding["zone"],
                code=finding["qid"],
                level=finding["level"],
                kind=kind,
            )
        )
    return cases


def load_cases(examples_dir: Path) -> list[BenchCase]:
    """Собрать набор случаев из всех проверок в `examples_dir`.

    Порядок обхода проверок детерминирован (сортировка по имени папки), итог
    отсортирован по `case_id`. Пропавший на диске кадр или отсутствие хотя бы
    одной проверки — это отказ (`FileNotFoundError`), а не молчаливый пропуск:
    в этом проекте проверка, которая не падает на неполном входе, — не
    проверка.
    """
    inspection_dirs = _inspection_dirs(examples_dir)
    if not inspection_dirs:
        raise FileNotFoundError(
            f"в {examples_dir} не нашлось ни одной проверки (нет папки с inspection.json)"
        )

    cases: list[BenchCase] = []
    for inspection_dir in inspection_dirs:
        inspection = json.loads((inspection_dir / "inspection.json").read_text(encoding="utf-8"))
        for finding in inspection["findings"]:
            cases.extend(_cases_from_finding(inspection_dir, finding))

    return sorted(cases, key=lambda c: c.case_id)
