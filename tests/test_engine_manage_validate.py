"""T106: `manage.py validate` снова может упасть — и падает по делу.

Проверка суммы долей зон внутри `validate` была недостижима с рождения: она
считала сумму уже нормализованных долей (`load_zones()` переписывал их на
равные) и всегда получала ровно 100. После T103 стало хуже: `load_zones()`
завершает процесс раньше, чем `validate` напечатает хоть строку, — инструмент,
которым человек чинит методику, умирает на первой же проблеме и не показывает
остальные.

Проверка, которая не может упасть, — не проверка: она создаёт ложное чувство
покрытия. Поэтому здесь закреплено ровно то, чего раньше не было: `validate`
читает `zones.csv` сам, собирает ВСЕ проблемы разом и меряет сумму тем же
допуском, что и движок. Разные допуски — отдельная ловушка: человек чинит
методику по зелёному `validate`, а `score` её не считает.
"""

from __future__ import annotations

import csv
from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import ROOT, Run, requires_data, run_engine

pytestmark = requires_data

MANAGE = ROOT / "engine" / "manage.py"


def переписать_зоны(data_dir: Path, доли: dict[str, str]) -> None:
    """Проставить зонам новые доли КАК СТРОКИ — сюда кладут и не-числа."""
    path = data_dir / "zones.csv"
    with path.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
        fields = list(rows[0].keys())
    for r in rows:
        if r["code"] in доли:
            r["share_pct"] = доли[r["code"]]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def сломать_пункт(data_dir: Path, qid: str, поле: str, значение: str) -> None:
    """Испортить одну клетку чек-листа — чтобы проверить, что problems копятся."""
    path = data_dir / "checklist.csv"
    with path.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
        fields = list(rows[0].keys())
    for r in rows:
        if r["id"] == qid:
            r[поле] = значение
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


@pytest.fixture
def validate(data_copy: Path, workdir: Path) -> Callable[[], Run]:
    """`manage.py validate` на копии методики. Боевой `data/` не трогается."""

    def call() -> Run:
        return run_engine(
            MANAGE, "validate", cwd=workdir, env_extra={"CHECKLIST_DIR": str(data_copy)}
        )

    return call


def test_здоровая_методика_проходит(validate: Callable[[], Run]) -> None:
    """Ловит проверку, ставшую слишком строгой: здоровая методика обязана проходить."""
    r = validate()

    assert r.code == 0, f"здоровая методика забракована: {r.text}"
    assert "Всё в порядке" in r.out, r.text


def test_боевая_методика_проходит_проверку(live_data_copy: Path, workdir: Path) -> None:
    """Отдельно и намеренно на БОЕВОЙ методике (T141): это про неё, а не про продукт.

    Смысл теста — «наша проверка не забраковала данные управляющей компании».
    Проверить это на синтетическом наборе нельзя: он собран нами и проходит по
    построению. Значит, красный тест здесь — законный сигнал: либо проверка
    стала строже, чем методика, либо в методику приехало то, чего движок не
    принимает. Остальные тесты файла идут по синтетической копии.
    """
    r = run_engine(MANAGE, "validate", cwd=workdir, env_extra={"CHECKLIST_DIR": str(live_data_copy)})

    assert r.code == 0, f"боевая методика забракована: {r.text}"
    assert "Всё в порядке" in r.out, r.text


def test_информационные_пункты_не_считаются_поломкой(
    validate: Callable[[], Run], data_copy: Path
) -> None:
    """`D0` — приём для информационных записей (docs/02-domain.md), не опечатка.

    Этого уровня не знала только эта проверка, и три боевых пункта (INF09-INF11)
    делали `validate` красным всегда. Команда, которая кричит на исправных
    данных, так же бесполезна, как та, что упасть не может: её перестают
    запускать вместе со всем, что она ловит.
    """
    r = validate()

    assert "D0" not in r.text, f"информационный уровень принят за поломку: {r.text!r}"


def test_настоящий_мусор_в_уровнях_ловится(validate: Callable[[], Run], data_copy: Path) -> None:
    """Обратная сторона предыдущего: D0 разрешён поимённо, а не «любой уровень»."""
    сломать_пункт(data_copy, "CLN05", "levels", "D9")

    r = validate()

    assert r.code != 0, "неизвестный уровень D9 прошёл как нормальный"
    assert "D9" in r.text, f"в проблеме не назван встреченный уровень: {r.text!r}"


def test_сумма_долей_мимо_100_это_проблема(validate: Callable[[], Run], data_copy: Path) -> None:
    """То, чего проверка не умела с рождения: увидеть несходящуюся сумму."""
    переписать_зоны(data_copy, {"facade": "9.99"})

    r = validate()

    assert r.code != 0, "сумма долей 99,99 % признана нормальной"
    assert "99.99" in r.text, f"в списке проблем нет фактической суммы: {r.text!r}"
    assert "вопросов:" in r.out, (
        f"ненулевой код пришёл из загрузчика движка, а не от самой проверки — "
        f"`validate` снова умер до печати: {r.text!r}"
    )


def test_допуск_тот_же_что_у_движка(
    validate: Callable[[], Run], data_copy: Path, workdir: Path
) -> None:
    """Разойдись допуски — `validate` зеленел бы на методике, которую движок не считает."""
    переписать_зоны(data_copy, {"facade": "9.99"})

    r = validate()
    оценка = run_engine(
        ROOT / "engine" / "audit.py",
        "zones",
        cwd=workdir,
        env_extra={"CHECKLIST_DIR": str(data_copy)},
    )

    assert (r.code != 0) == (оценка.code != 0), (
        f"validate и движок разошлись на одних данных: "
        f"validate={r.code}, движок={оценка.code}\n{r.text}\n{оценка.text}"
    )


def test_нечисловая_доля_это_проблема_а_не_смерть(
    validate: Callable[[], Run], data_copy: Path
) -> None:
    """Главное здесь — что `validate` дожил до печати, а не упал внутри загрузчика."""
    переписать_зоны(data_copy, {"facade": "десять"})

    r = validate()

    assert r.code != 0, "нечисловая доля зоны признана нормальной"
    assert "вопросов:" in r.out, (
        f"validate умер до печати сводки — список проблем человек не увидел: {r.text!r}"
    )
    assert "facade" in r.text, f"в проблеме не названа зона: {r.text!r}"


def test_проблемы_зон_и_чек_листа_показываются_разом(
    validate: Callable[[], Run], data_copy: Path
) -> None:
    """`validate` — диагностика: он собирает все проблемы, а не падает на первой."""
    переписать_зоны(data_copy, {"facade": "20"})
    сломать_пункт(data_copy, "CLN05", "days", "десять")

    r = validate()

    assert r.code != 0, r.text
    assert "CLN05" in r.text, f"проблема чек-листа потерялась: {r.text!r}"
    assert "доли зон" in r.text, f"проблема зон потерялась: {r.text!r}"
