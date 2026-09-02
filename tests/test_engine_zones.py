"""T103: доли зон мимо 100 % — отказ, а не молчаливая подмена методики.

`load_zones()` при сумме `share_pct`, отличной от 100, переписывал ВСЕ доли на
равные (100/N) и не говорил ни слова. Сегодня это незаметно: десять зон по 10 %
дают ровно 100. Как только кухня станет тяжелее фасада, а сумма из-за
округления окажется 99,99 — отчёт уйдёт партнёру посчитанным по другой
методике, и увидеть это будет негде.

Поэтому здесь проверяется не «движок не упал», а два наблюдаемых факта: отказ
называет причину (сумму, ожидание и файл) и неравные доли доходят до расчёта
такими, какими их записал человек.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import Run, requires_data

pytestmark = requires_data


def переписать_доли(data_dir: Path, доли: dict[str, float]) -> float:
    """Проставить зонам новые доли и вернуть их сумму.

    Правится копия методики (фикстура `data_copy`), боевой `data/zones.csv`
    не трогается никогда.
    """
    path = data_dir / "zones.csv"
    with path.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
        fields = list(rows[0].keys())
    for r in rows:
        if r["code"] in доли:
            r["share_pct"] = f"{доли[r['code']]:g}"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    return sum(float(r["share_pct"]) for r in rows)


@pytest.fixture
def методика(data_copy: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Копия методики, подставленная движку через `CHECKLIST_DIR`."""
    monkeypatch.setenv("CHECKLIST_DIR", str(data_copy))
    return data_copy


def test_сумма_долей_мимо_100_роняет_расчёт(started: Callable[..., Run], методика: Path) -> None:
    started("add", "--qid", "CLN05", "--level", "D1", "--zone", "hot_kitchen")
    # Ровно тот случай из задачи: доли разошлись на сотую от округления.
    сумма = переписать_доли(методика, {"facade": 9.99})
    assert сумма == pytest.approx(99.99)

    r = started("score")

    assert r.code != 0, "методика с суммой долей 99,99 % посчитана как ни в чём не бывало"
    assert "99.99" in r.text, f"в отказе нет фактической суммы долей: {r.text!r}"
    assert "100" in r.text, f"в отказе нет ожидаемой суммы: {r.text!r}"


def test_отказ_называет_файл_с_долями(started: Callable[..., Run], методика: Path) -> None:
    """Каталогов методики два (боевой и форк) — без пути человек правит не тот файл."""
    переписать_доли(методика, {"facade": 20.0})

    r = started("score")

    assert str(методика / "zones.csv") in r.text, f"в отказе нет пути к файлу: {r.text!r}"


def test_неравные_доли_не_подменяются_равными(started: Callable[..., Run], методика: Path) -> None:
    """Кухня тяжелее фасада — методика, а не ошибка: сумма сходится, доли живут как есть."""
    сумма = переписать_доли(методика, {"facade": 5.0, "hot_kitchen": 15.0})
    assert сумма == pytest.approx(100.0)

    r = started("zones")

    assert r.code == 0, r.text
    строки = {s.split("|")[0].strip(): s for s in r.out.splitlines() if "|" in s}
    assert "5%" in строки["facade"], f"доля фасада подменена: {строки['facade']!r}"
    assert "15%" in строки["hot_kitchen"], f"доля кухни подменена: {строки['hot_kitchen']!r}"


def test_неравные_доли_доходят_до_оценки(started: Callable[..., Run], методика: Path) -> None:
    """Разбивка по зонам в `score` обязана считаться по долям из файла, а не по 100/N."""
    переписать_доли(методика, {"facade": 5.0, "hot_kitchen": 15.0})
    started("add", "--qid", "CLN05", "--level", "D1", "--zone", "hot_kitchen")

    r = started("score", "--json")

    assert r.code == 0, r.text
    зоны = json.loads(r.out)["zones"]
    assert зоны["hot_kitchen"]["share"] == pytest.approx(15.0), (
        f"доля кухни в расчёте подменена: {зоны['hot_kitchen']}"
    )
    assert зоны["facade"]["share"] == pytest.approx(5.0), (
        f"доля фасада в расчёте подменена: {зоны['facade']}"
    )


def test_округление_в_пределах_допуска_не_роняет(
    started: Callable[..., Run], методика: Path
) -> None:
    """Доли вроде 100/3 не дают ровно 100 в десятичной записи — это не подмена методики."""
    сумма = переписать_доли(методика, {"facade": 10.001})
    assert сумма == pytest.approx(100.001)

    r = started("score")

    assert r.code == 0, f"расхождение 0,001 п.п. — округление, а не другая методика: {r.text}"


def test_отчёт_партнёру_не_собирается_на_подменённых_долях(
    started: Callable[..., Run], report: Callable[..., Run], методика: Path
) -> None:
    """Письмо и PDF идут наружу — они обязаны спотыкаться о ту же проверку, что и `score`."""
    started("add", "--qid", "CLN05", "--level", "D1", "--zone", "hot_kitchen")
    переписать_доли(методика, {"facade": 9.99})

    r = report("letter")

    assert r.code != 0, "письмо партнёру собрано по методике с подменёнными долями"
    assert "zones.csv" in r.text, f"в отказе нет файла с долями: {r.text!r}"
