"""T010: `audit.py edit` — правка уже зафиксированной записи.

Без неё бот работать не может: аудитор замечает ошибку через три кадра, а
поправить нечем. На обеих боевых проверках это делалось руками в JSON.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from conftest import Run


def state_of(workdir: Path) -> dict:
    return json.loads((workdir / "inspection.json").read_text(encoding="utf-8"))


def finding(workdir: Path, n: int) -> dict:
    hit = [f for f in state_of(workdir)["findings"] if f["n"] == n]
    assert hit, f"записи #{n} нет в состоянии"
    return hit[0]


def test_edit_меняет_класс(started: Callable[..., Run], workdir: Path) -> None:
    started("add", "--qid", "PRD09", "--level", "D1", "--zone", "fridge")
    r = started("edit", "--n", "1", "--level", "D2")
    assert r.code == 0, r.text
    assert finding(workdir, 1)["level"] == "D2"


def test_edit_меняет_зону(started: Callable[..., Run], workdir: Path) -> None:
    started("add", "--qid", "CLN06", "--level", "D1", "--zone", "dining")
    r = started("edit", "--n", "1", "--zone", "cold_kitchen")
    assert r.code == 0, r.text
    assert finding(workdir, 1)["zone"] == "cold_kitchen"


def test_edit_меняет_код_пункта(started: Callable[..., Run], workdir: Path) -> None:
    started("add", "--qid", "CLN06", "--level", "D1", "--zone", "hot_kitchen")
    r = started("edit", "--n", "1", "--qid", "CLN05")
    assert r.code == 0, r.text
    assert finding(workdir, 1)["qid"] == "CLN05"


def test_edit_меняет_формулировку(started: Callable[..., Run], workdir: Path) -> None:
    started("add", "--qid", "CLN05", "--level", "D1", "--zone", "hot_kitchen", "--evidence", "было")
    r = started("edit", "--n", "1", "--evidence", "стало")
    assert r.code == 0, r.text
    assert finding(workdir, 1)["evidence"] == "стало"


def test_edit_понимает_имена_из_контракта_блока(started: Callable[..., Run], workdir: Path) -> None:
    """`--code` и `--text` — синонимы `--qid` и `--evidence` из контракта блока."""
    started("add", "--qid", "CLN06", "--level", "D1", "--zone", "hot_kitchen")
    r = started("edit", "--n", "1", "--code", "CLN05", "--text", "нагар на подине")
    assert r.code == 0, r.text
    f = finding(workdir, 1)
    assert (f["qid"], f["evidence"]) == ("CLN05", "нагар на подине")


def test_edit_несуществующего_номера_внятно_падает(started: Callable[..., Run]) -> None:
    started("add", "--qid", "CLN05", "--level", "D1", "--zone", "hot_kitchen")
    r = started("edit", "--n", "7", "--level", "D1")
    assert r.code != 0, "правка несуществующей записи обязана падать"
    assert "7" in r.text, f"в сообщении нет номера записи: {r.text!r}"


def test_edit_не_ставит_недопустимый_для_пункта_класс(
    started: Callable[..., Run], workdir: Path
) -> None:
    started("add", "--qid", "CLN05", "--level", "D1", "--zone", "hot_kitchen")
    r = started("edit", "--n", "1", "--level", "D3")
    assert r.code != 0, "у CLN05 есть только D1 — D3 обязан быть отклонён"
    assert finding(workdir, 1)["level"] == "D1", "состояние не должно меняться при отказе"


def test_edit_не_ставит_несуществующую_зону(started: Callable[..., Run], workdir: Path) -> None:
    started("add", "--qid", "CLN05", "--level", "D1", "--zone", "hot_kitchen")
    r = started("edit", "--n", "1", "--zone", "выдуманная")
    assert r.code != 0, "зона вне справочника обязана быть отклонена"
    assert finding(workdir, 1)["zone"] == "hot_kitchen"


def test_edit_без_единого_поля_падает(started: Callable[..., Run]) -> None:
    started("add", "--qid", "CLN05", "--level", "D1", "--zone", "hot_kitchen")
    r = started("edit", "--n", "1")
    assert r.code != 0, "правка, которая ничего не меняет, — это ошибка вызова"


def test_edit_не_создаёт_дубль_пары_код_зона(started: Callable[..., Run], workdir: Path) -> None:
    """Уникальность «пункт + зона» держится и на правке, а не только на добавлении."""
    started("add", "--qid", "CLN06", "--level", "D1", "--zone", "hot_kitchen")
    started("add", "--qid", "CLN06", "--level", "D1", "--zone", "dining")
    r = started("edit", "--n", "2", "--zone", "hot_kitchen")
    assert r.code != 0, "после правки получилась бы вторая CLN06 в hot_kitchen"
    assert finding(workdir, 2)["zone"] == "dining"


def test_edit_смены_кода_пересчитывает_нетипичность_зоны(
    started: Callable[..., Run], workdir: Path
) -> None:
    """CLN05 применим только к горячему цеху: перевод на него из зала — нетипичная зона."""
    started("add", "--qid", "CLN06", "--level", "D1", "--zone", "dining")
    r = started("edit", "--n", "1", "--qid", "CLN05")
    assert r.code == 0, r.text
    assert finding(workdir, 1).get("zone_unusual") is True, "флаг нетипичной зоны не выставлен"
