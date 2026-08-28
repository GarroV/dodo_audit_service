"""T014: валидация шапки проверки и её правка после старта.

`init` принимал пустую пиццерию и дату в любом виде. Дата в формате
`21.08.2026` ломает расчёт сроков и оставляет в письме пустой прочерк —
это уже уходило партнёру.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from conftest import Run, requires_data

pytestmark = requires_data


def meta_of(workdir: Path) -> dict:
    return json.loads((workdir / "inspection.json").read_text(encoding="utf-8"))["meta"]


def test_init_без_пиццерии_падает(audit: Callable[..., Run], workdir: Path) -> None:
    r = audit("init", "--date", "2026-08-21")
    assert r.code != 0, "проверка без названия точки не должна начинаться"
    assert not (workdir / "inspection.json").exists(), "состояние создано при отказе"


def test_init_с_пустой_пиццерией_падает(audit: Callable[..., Run]) -> None:
    r = audit("init", "--unit", "   ")
    assert r.code != 0, "пробелы — это не название пиццерии"


def test_init_с_датой_не_в_iso_падает(audit: Callable[..., Run], workdir: Path) -> None:
    r = audit("init", "--unit", "Белград-1", "--date", "21.08.2026")
    assert r.code != 0, "дата не в ISO ломает сроки и письмо"
    assert "21.08.2026" in r.text, f"в сообщении нет отвергнутой даты: {r.text!r}"
    assert not (workdir / "inspection.json").exists()


def test_init_с_iso_датой_проходит(audit: Callable[..., Run], workdir: Path) -> None:
    r = audit("init", "--unit", "Белград-1", "--date", "2026-08-21")
    assert r.code == 0, r.text
    assert meta_of(workdir)["date"] == "2026-08-21"


def test_init_с_неизвестным_языком_падает(audit: Callable[..., Run]) -> None:
    """Иначе `report.py` молча откатится на русский и отдаст партнёру не тот язык."""
    r = audit("init", "--unit", "Белград-1", "--lang", "sr")
    assert r.code != 0, "язык отчёта вне справочника обязан быть отклонён"


def test_init_сохраняет_партнёра(audit: Callable[..., Run], workdir: Path) -> None:
    r = audit("init", "--unit", "Белград-1", "--partner", "ООО «Пример»")
    assert r.code == 0, r.text
    assert meta_of(workdir)["partner"] == "ООО «Пример»"


def test_meta_правит_только_переданные_поля(started: Callable[..., Run], workdir: Path) -> None:
    before = meta_of(workdir)
    r = started("meta", "--partner", "ООО «Пример»")
    assert r.code == 0, r.text
    after = meta_of(workdir)
    assert after["partner"] == "ООО «Пример»"
    assert after["unit"] == before["unit"], "правка партнёра затёрла название точки"
    assert after["date"] == before["date"], "правка партнёра затёрла дату"


def test_meta_не_трогает_зафиксированные_записи(
    started: Callable[..., Run], workdir: Path
) -> None:
    started("add", "--qid", "CLN05", "--level", "D1", "--zone", "hot_kitchen")
    r = started("meta", "--auditor", "Другой аудитор")
    assert r.code == 0, r.text
    st = json.loads((workdir / "inspection.json").read_text(encoding="utf-8"))
    assert [f["n"] for f in st["findings"]] == [1], "правка шапки потеряла записи"


def test_meta_с_кривой_датой_падает(started: Callable[..., Run], workdir: Path) -> None:
    before = meta_of(workdir)
    r = started("meta", "--date", "вчера")
    assert r.code != 0, "дата не в ISO обязана быть отклонена и в правке шапки"
    assert meta_of(workdir)["date"] == before["date"], "состояние изменилось при отказе"


def test_meta_с_пустой_пиццерией_падает(started: Callable[..., Run], workdir: Path) -> None:
    before = meta_of(workdir)
    r = started("meta", "--unit", "  ")
    assert r.code != 0
    assert meta_of(workdir)["unit"] == before["unit"]


def test_meta_без_полей_падает(started: Callable[..., Run]) -> None:
    r = started("meta")
    assert r.code != 0, "правка, которая ничего не меняет, — это ошибка вызова"


def test_meta_без_начатой_проверки_падает(audit: Callable[..., Run]) -> None:
    r = audit("meta", "--unit", "Белград-1")
    assert r.code != 0, "править шапку несуществующей проверки нечем"
