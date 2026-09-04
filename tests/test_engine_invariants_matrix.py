"""Табличные тесты на инварианты CLI `engine/audit.py`, ещё не покрытые точечно.

Матрица: отказы `add` по чек-листу/зоне/классу, нетипичная зона как
предупреждение (а не отказ), информационные записи `D0` вне арифметики
оценки, сама арифметика на маленьких наборах, фото, поведение команд без
начатой проверки и чтение справочников без состояния.

Путь к движку берётся через `ENGINE`, а не напрямую через фикстуры
`conftest.audit`/`conftest.started`: так этот же файл можно прогнать против
намеренно испорченной копии `audit.py`, подставив `PROBE_ENGINE=путь`, не
трогая `engine/` и не редактируя `conftest.py` (см. рецепт порчи в задаче
блока `engine-fix`). Без `PROBE_ENGINE` поведение не отличается от обычных
тестов — используется тот же боевой `engine/audit.py`.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import AUDIT, Run, run_engine

ENGINE = Path(os.environ.get("PROBE_ENGINE", str(AUDIT)))


@pytest.fixture
def audit(workdir: Path) -> Callable[..., Run]:
    """Как `conftest.audit`, но зовёт `ENGINE` — боевой движок либо испорченную копию."""

    def call(*args: str) -> Run:
        return run_engine(ENGINE, *args, cwd=workdir, state=workdir / "inspection.json")

    return call


@pytest.fixture
def started(audit: Callable[..., Run]) -> Callable[..., Run]:
    r = audit("init", "--unit", "Тестовая", "--auditor", "Тест", "--date", "2026-08-21")
    assert r.code == 0, r.text
    return audit


def state_of(workdir: Path) -> dict:
    return json.loads((workdir / "inspection.json").read_text(encoding="utf-8"))


def numbers(workdir: Path) -> list[int]:
    return [f["n"] for f in state_of(workdir)["findings"]]


def finding(workdir: Path, n: int) -> dict:
    hit = [f for f in state_of(workdir)["findings"] if f["n"] == n]
    assert hit, f"записи #{n} нет в состоянии"
    return hit[0]


def score_json(run: Callable[..., Run]) -> dict:
    r = run("score", "--json")
    assert r.code == 0, r.text
    return json.loads(r.out)


# --- 1. Валидация `add`: код пункта, класс, зона --------------------------

INVALID_ADD_CASES = [
    pytest.param(
        ("--qid", "НЕТТАКОГО", "--level", "D1", "--zone", "hot_kitchen"),
        id="неизвестный-код-пункта",
    ),
    pytest.param(
        ("--qid", "CLN05", "--level", "D2", "--zone", "hot_kitchen"),
        id="класс-D2-недопустим-для-CLN05",
    ),
    pytest.param(
        ("--qid", "PRD02", "--level", "D1", "--zone", "cold_kitchen"),
        id="класс-D1-недопустим-для-PRD02",
    ),
    pytest.param(
        ("--qid", "CLN05", "--level", "D1", "--zone", "марс"),
        id="несуществующая-зона",
    ),
]


@pytest.mark.parametrize("args", INVALID_ADD_CASES)
def test_add_отклоняет_недопустимые_комбинации(
    started: Callable[..., Run], workdir: Path, args: tuple[str, ...]
) -> None:
    r = started("add", *args)
    assert r.code != 0, f"должен быть отказ на {args}, а вышел код 0: {r.text!r}"
    assert numbers(workdir) == [], f"состояние изменилось при отказе на {args}"


MISSING_ARG_CASES = [
    pytest.param(("--level", "D1", "--zone", "hot_kitchen"), id="без-qid"),
    pytest.param(("--qid", "CLN05", "--zone", "hot_kitchen"), id="без-level"),
    pytest.param(("--qid", "CLN05", "--level", "D1"), id="без-zone"),
]


@pytest.mark.parametrize("args", MISSING_ARG_CASES)
def test_add_без_обязательного_аргумента_код_argparse(
    started: Callable[..., Run], workdir: Path, args: tuple[str, ...]
) -> None:
    r = started("add", *args)
    assert r.code == 2, f"argparse обязан вернуть код 2 на {args}: {r.text!r}"
    assert numbers(workdir) == [], "состояние изменилось при ошибке разбора аргументов"


# --- 2. Нетипичная зона — предупреждение, а не отказ -----------------------


def test_нетипичная_зона_проходит_с_предупреждением(
    started: Callable[..., Run], workdir: Path
) -> None:
    """CLN05 объявлен только для hot_kitchen — зал остаётся нетипичной зоной."""
    r = started("add", "--qid", "CLN05", "--level", "D1", "--zone", "dining")
    assert r.code == 0, r.text
    assert "нетипич" in r.text.lower(), f"нет предупреждения о нетипичной зоне: {r.text!r}"
    assert finding(workdir, 1).get("zone_unusual") is True, "флаг нетипичной зоны не выставлен"


# --- 3. Информационные записи D0 вне арифметики оценки ---------------------


def test_d0_не_считается_в_вычетах(started: Callable[..., Run]) -> None:
    r = started("add", "--qid", "INF10", "--level", "D0", "--zone", "fridge")
    assert r.code == 0, r.text
    res = score_json(started)
    assert any(f["qid"] == "INF10" for f in res["findings"]), "запись D0 пропала из score --json"
    assert res["counts"].get("D1", 0) == 0
    assert res["counts"].get("D2", 0) == 0
    assert res["counts"].get("D3", 0) == 0
    assert res["pct"] == 100.0, f"D0 не должна снижать процент: {res['pct']}"
    assert res["deductions"] == 0, f"D0 не должна давать вычет: {res['deductions']}"


def test_смесь_одного_d1_и_одной_d0_даёт_99_5_процента(started: Callable[..., Run]) -> None:
    started("add", "--qid", "CLN05", "--level", "D1", "--zone", "hot_kitchen")
    started("add", "--qid", "INF10", "--level", "D0", "--zone", "fridge")
    res = score_json(started)
    assert res["pct"] == 99.5, f"D0 в довесок к D1 не должна менять вычет: {res['pct']}"
    assert res["counts"]["D1"] == 1


# --- 4. Арифметика оценки на маленьких наборах -----------------------------
#
# Ожидаемые числа — константы, посчитанные по docs/02-domain.md заранее, а не
# формулой движка: тест не должен дублировать код, который проверяет.

SCORE_CASES = [
    pytest.param([], 100.0, "A", {"D1": 0, "D2": 0, "D3": 0}, id="пусто"),
    pytest.param(
        [("CLN05", "D1", "hot_kitchen")], 99.5, "A", {"D1": 1, "D2": 0, "D3": 0}, id="один-D1"
    ),
    pytest.param(
        [("PRD02", "D2", "cold_kitchen")], 98.0, "B", {"D1": 0, "D2": 1, "D3": 0}, id="один-D2"
    ),
    pytest.param(
        [("PRD02", "D2", "cold_kitchen"), ("PRD02", "D2", "fridge")],
        96.0,
        "C",
        {"D1": 0, "D2": 2, "D3": 0},
        id="два-D2",
    ),
    pytest.param(
        [("PRD09", "D3", "fridge")],
        90.0,
        "D",
        {"D1": 0, "D2": 0, "D3": 1},
        id="один-D3-в-fridge",
    ),
]


@pytest.mark.parametrize("steps, expected_pct, expected_grade, expected_counts", SCORE_CASES)
def test_арифметика_оценки_на_маленьких_наборах(
    started: Callable[..., Run],
    steps: list[tuple[str, str, str]],
    expected_pct: float,
    expected_grade: str,
    expected_counts: dict[str, int],
) -> None:
    for qid, level, zone in steps:
        r = started("add", "--qid", qid, "--level", level, "--zone", zone)
        assert r.code == 0, r.text
    res = score_json(started)
    assert res["pct"] == expected_pct, f"{steps} -> pct={res['pct']}, ожидали {expected_pct}"
    grade_msg = f"{steps} -> grade={res['grade']}, ожидали {expected_grade}"
    assert res["grade"] == expected_grade, grade_msg
    for lvl, cnt in expected_counts.items():
        assert res["counts"].get(lvl, 0) == cnt, f"{steps} -> counts={res['counts']}"


# --- 5. Фотографии ----------------------------------------------------------


def test_photo_add_через_запятую_даёт_две_штуки(started: Callable[..., Run], workdir: Path) -> None:
    started("add", "--qid", "CLN05", "--level", "D1", "--zone", "hot_kitchen")
    r = started("photo", "1", "--add", "a.jpg,b.jpg")
    assert r.code == 0, r.text
    assert finding(workdir, 1)["photos"] == ["a.jpg", "b.jpg"]


def test_photo_повторный_путь_не_дублируется(started: Callable[..., Run], workdir: Path) -> None:
    started("add", "--qid", "CLN05", "--level", "D1", "--zone", "hot_kitchen")
    started("photo", "1", "--add", "a.jpg,b.jpg")
    r = started("photo", "1", "--add", "a.jpg")
    assert r.code == 0, r.text
    assert finding(workdir, 1)["photos"] == ["a.jpg", "b.jpg"], "путь продублирован"


def test_photo_clear_очищает_список(started: Callable[..., Run], workdir: Path) -> None:
    started("add", "--qid", "CLN05", "--level", "D1", "--zone", "hot_kitchen")
    started("photo", "1", "--add", "a.jpg,b.jpg")
    r = started("photo", "1", "--clear")
    assert r.code == 0, r.text
    assert finding(workdir, 1)["photos"] == [], "--clear не очистил фото"


def test_photo_несуществующего_номера_падает(started: Callable[..., Run]) -> None:
    r = started("photo", "99", "--add", "a.jpg")
    assert r.code != 0, "доснять фото к несуществующей записи не должно получаться"


# --- 6. Команды состояния без начатой проверки -----------------------------

NO_STATE_CASES = [
    pytest.param(("add", "--qid", "CLN05", "--level", "D1", "--zone", "hot_kitchen"), id="add"),
    pytest.param(("edit", "--n", "1", "--level", "D1"), id="edit"),
    pytest.param(("photo", "1", "--add", "a.jpg"), id="photo"),
    pytest.param(("drop", "1"), id="drop"),
    pytest.param(("list",), id="list"),
    pytest.param(("score",), id="score"),
    pytest.param(("meta", "--unit", "Белград-1"), id="meta"),
]


@pytest.mark.parametrize("args", NO_STATE_CASES)
def test_команда_без_начатой_проверки_падает(
    audit: Callable[..., Run], args: tuple[str, ...]
) -> None:
    r = audit(*args)
    assert r.code != 0, f"{args} обязана падать без inspection.json"
    assert "не начата" in r.text, f"нет внятного сообщения о непочатой проверке: {r.text!r}"


# --- 7. Чтение справочников без состояния -----------------------------------


def test_zones_печатает_десять_зон(audit: Callable[..., Run]) -> None:
    r = audit("zones")
    assert r.code == 0, r.text
    lines = [line for line in r.out.strip().splitlines() if line]
    assert len(lines) == 10, f"в zones.csv 10 зон, а напечатано {len(lines)}: {r.out!r}"


def test_index_по_зоне_даёт_непустой_вывод(audit: Callable[..., Run]) -> None:
    r = audit("index", "--zone", "hot_kitchen")
    assert r.code == 0, r.text
    assert r.out.strip() != "", "index --zone hot_kitchen не должен быть пустым"


def test_detail_известного_кода_печатает_код(audit: Callable[..., Run]) -> None:
    r = audit("detail", "CLN05")
    assert r.code == 0, r.text
    assert "CLN05" in r.out


def test_detail_неизвестного_кода_говорит_что_пункта_нет(audit: Callable[..., Run]) -> None:
    r = audit("detail", "НЕТТАКОГО")
    assert r.code == 0, r.text
    assert "нет такого вопроса" in r.text, f"не сказано, что пункта нет: {r.text!r}"


# --- 8. `score --json` — валидный JSON с нужными ключами --------------------


def test_score_json_содержит_обязательные_ключи(started: Callable[..., Run]) -> None:
    started("add", "--qid", "CLN05", "--level", "D1", "--zone", "hot_kitchen")
    res = score_json(started)
    for key in ("meta", "pct", "grade", "counts", "zones", "findings"):
        assert key in res, f"в score --json нет ключа {key}: {sorted(res)}"
