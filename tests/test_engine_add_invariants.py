"""T013: уникальность пары «пункт + зона» и сквозные номера записей.

Оба инварианта из `docs/02-domain.md`: нарушение уникально по паре «пункт +
зона», а номера аудитор называет вслух — переиспользовать их нельзя.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from conftest import Run, requires_data

pytestmark = requires_data


def state_of(workdir: Path) -> dict:
    return json.loads((workdir / "inspection.json").read_text(encoding="utf-8"))


def numbers(workdir: Path) -> list[int]:
    return [f["n"] for f in state_of(workdir)["findings"]]


def test_повторный_add_той_же_пары_отклоняется(
    started: Callable[..., Run], workdir: Path
) -> None:
    started("add", "--qid", "CLN06", "--level", "D1", "--zone", "hot_kitchen")
    r = started("add", "--qid", "CLN06", "--level", "D1", "--zone", "hot_kitchen")
    assert r.code != 0, "второй такой же add обязан быть отклонён"
    assert numbers(workdir) == [1], "вторая запись всё-таки создана — оценка просядет дважды"


def test_отказ_называет_номер_существующей_записи(started: Callable[..., Run]) -> None:
    """Класс берём другой допустимый: отказ должен быть именно про дубль пары."""
    started("add", "--qid", "PRD09", "--level", "D1", "--zone", "fridge")
    r = started("add", "--qid", "PRD09", "--level", "D2", "--zone", "fridge")
    assert r.code != 0
    assert "#1" in r.text, f"аудитору не сказали, какая запись уже есть: {r.text!r}"


def test_тот_же_пункт_в_другой_зоне_это_отдельное_нарушение(
    started: Callable[..., Run], workdir: Path
) -> None:
    started("add", "--qid", "CLN06", "--level", "D1", "--zone", "hot_kitchen")
    r = started("add", "--qid", "CLN06", "--level", "D1", "--zone", "cold_kitchen")
    assert r.code == 0, r.text
    assert numbers(workdir) == [1, 2]


def test_номера_не_переиспользуются_после_удаления_последней(
    started: Callable[..., Run], workdir: Path
) -> None:
    started("add", "--qid", "CLN06", "--level", "D1", "--zone", "hot_kitchen")
    started("add", "--qid", "CLN06", "--level", "D1", "--zone", "dining")
    started("drop", "2")
    r = started("add", "--qid", "CLN06", "--level", "D1", "--zone", "dough")
    assert r.code == 0, r.text
    assert numbers(workdir) == [1, 3], "номер удалённой записи выдан повторно"


def test_номера_не_переиспользуются_после_удаления_всех(
    started: Callable[..., Run], workdir: Path
) -> None:
    started("add", "--qid", "CLN05", "--level", "D1", "--zone", "hot_kitchen")
    started("drop", "1")
    started("add", "--qid", "CLN05", "--level", "D1", "--zone", "hot_kitchen")
    assert numbers(workdir) == [2], "после опустошения счётчик начался заново"


def test_счётчик_поднимается_от_старого_состояния_без_него(
    started: Callable[..., Run], workdir: Path
) -> None:
    """Боевые `inspection.json` собраны до появления счётчика — они не должны ломаться."""
    st = state_of(workdir)
    st["findings"] = [
        {"n": 1, "qid": "CLN05", "level": "D1", "zone": "hot_kitchen", "photos": [],
         "comment": "", "evidence": "старая запись"},
        {"n": 4, "qid": "CLN06", "level": "D1", "zone": "dining", "photos": [],
         "comment": "", "evidence": "старая запись"},
    ]
    st.pop("seq", None)
    (workdir / "inspection.json").write_text(
        json.dumps(st, ensure_ascii=False), encoding="utf-8"
    )
    r = started("add", "--qid", "CLN06", "--level", "D1", "--zone", "dough")
    assert r.code == 0, r.text
    assert numbers(workdir) == [1, 4, 5], "счётчик не подхватил максимум из старого состояния"


def test_init_обнуляет_счётчик_новой_проверки(
    started: Callable[..., Run], audit: Callable[..., Run], workdir: Path
) -> None:
    started("add", "--qid", "CLN05", "--level", "D1", "--zone", "hot_kitchen")
    audit("init", "--unit", "Другая", "--date", "2026-08-22")
    audit("add", "--qid", "CLN05", "--level", "D1", "--zone", "hot_kitchen")
    assert numbers(workdir) == [1], "новая проверка началась не с первого номера"
