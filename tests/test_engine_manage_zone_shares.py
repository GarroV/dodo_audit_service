"""T112: правка списка зон не переписывает доли остальных зон молча.

`zone-add` и `zone-remove` заканчивались двумя строками — «доля каждой зоны
теперь 100/N» — и переписывали `share_pct` ВСЕХ зон, что бы там ни стояло.
Пока десять зон весят по 10 %, подмену не видно. Неравные доли разрешены с
T103 («кухня тяжелее фасада»), и первая же правка списка зон превращала
заданный руками вес 20 % в 9.0909 % — при действии, которое к долям отношения
не имеет. Отчёт партнёру уходил посчитанным по другой методике, и узнать об
этом было негде: методика лежит вне git (D002).

Хуже того, уравнивание маскировало проверку T103. `load_zones()` отказывается
считать методику, доли которой не сходятся к 100, — а `manage.py` заботливо
приводил сумму к 100 и тем самым гасил единственный сигнал.

Правило, которое здесь закреплено: доли — решение управляющей компании, и
движок его не принимает за неё. Что делать с долями при правке списка зон,
человек говорит явно (`--share`, `--keep-shares`, `--equal-shares`); пока не
сказал — команда не выполняется и файл не трогается.
"""

from __future__ import annotations

import csv
import hashlib
from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import AUDIT, ROOT, Run, requires_data, run_engine

pytestmark = requires_data

MANAGE = ROOT / "engine" / "manage.py"

# Неравные доли: ровно та методика, ради которой T103 разрешил неравенство —
# горячий цех тяжелее фасада. Сумма 100.
НЕРАВНЫЕ = {
    "facade": "5.0",
    "dining": "5.0",
    "dry_storage": "5.0",
    "freezer": "10.0",
    "fridge": "10.0",
    "hot_kitchen": "20.0",
    "cold_kitchen": "15.0",
    "dough": "10.0",
    "dishwashing": "10.0",
    "staff": "10.0",
}


def читать_доли(path: Path) -> dict[str, str]:
    """Доли зон как они лежат в файле — строками, без приведения к числу."""
    with path.open(encoding="utf-8-sig") as f:
        return {r["code"]: r["share_pct"] for r in csv.DictReader(f)}


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()  # noqa: S324 — сверка файла, не защита


@pytest.fixture
def методика(data_copy: Path) -> Path:
    """Копия методики с неравными долями зон. Боевой `data/` не трогается."""
    path = data_copy / "zones.csv"
    with path.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
        fields = list(rows[0].keys())
    for r in rows:
        r["share_pct"] = НЕРАВНЫЕ[r["code"]]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    assert sum(float(v) for v in читать_доли(path).values()) == pytest.approx(100.0)
    return data_copy


@pytest.fixture
def manage(методика: Path, workdir: Path) -> Callable[..., Run]:
    """`manage.py` на копии методики с неравными долями."""

    def call(*args: str) -> Run:
        return run_engine(MANAGE, *args, cwd=workdir, env_extra={"CHECKLIST_DIR": str(методика)})

    return call


@pytest.fixture
def зоны(методика: Path) -> Path:
    return методика / "zones.csv"


# --- главное: заданные руками доли переживают правку списка зон --------------


def test_добавление_зоны_не_трогает_заданные_доли(manage: Callable[..., Run], зоны: Path) -> None:
    """Вес кухни 20 % задан управляющей компанией — новая зона его не отменяет."""
    было = читать_доли(зоны)

    r = manage(
        "zone-add",
        "--code",
        "terrace",
        "--name-ru",
        "Терраса",
        "--name-en",
        "Terrace",
        "--share",
        "5",
    )
    assert r.code == 0, r.text

    стало = читать_доли(зоны)
    assert стало.pop("terrace") == "5.0", "доля новой зоны записана не та"
    assert стало == было, f"доли остальных зон переписаны: {стало}"


def test_удаление_зоны_не_трогает_доли_остальных(manage: Callable[..., Run], зоны: Path) -> None:
    было = {k: v for k, v in читать_доли(зоны).items() if k != "staff"}

    r = manage("zone-remove", "staff", "--keep-shares")
    assert r.code == 0, r.text

    assert читать_доли(зоны) == было, "доли оставшихся зон переписаны"


def test_после_правки_списка_названа_итоговая_сумма(manage: Callable[..., Run], зоны: Path) -> None:
    """Сумма перестала сходиться — человек обязан узнать об этом сразу, а не на расчёте."""
    r = manage("zone-remove", "staff", "--keep-shares")

    assert "90" in r.text, f"итоговая сумма долей не названа: {r.text!r}"
    assert "100" in r.text, f"ожидаемая сумма не названа: {r.text!r}"
    assert str(зоны) in r.text, f"файл с долями не назван: {r.text!r}"


def test_движок_отказывается_считать_методику_после_такой_правки(
    manage: Callable[..., Run], методика: Path, workdir: Path
) -> None:
    """Связка с T103: несошедшиеся доли доходят до движка, а не гасятся уравниванием."""
    assert manage("zone-remove", "staff", "--keep-shares").code == 0

    r = run_engine(AUDIT, "zones", cwd=workdir, env_extra={"CHECKLIST_DIR": str(методика)})

    assert r.code != 0, f"движок посчитал методику с суммой долей 90 %: {r.text!r}"
    assert "90" in r.text, f"в отказе движка нет фактической суммы: {r.text!r}"


# --- уравнивание осталось, но только по явной просьбе ------------------------


def test_равные_доли_делаются_по_явной_просьбе_при_добавлении(
    manage: Callable[..., Run], зоны: Path
) -> None:
    r = manage(
        "zone-add",
        "--code",
        "terrace",
        "--name-ru",
        "Терраса",
        "--equal-shares",
    )
    assert r.code == 0, r.text

    доли = читать_доли(зоны)
    assert len(доли) == 11, доли
    assert len(set(доли.values())) == 1, f"доли не уравнены: {доли}"
    assert sum(float(v) for v in доли.values()) == pytest.approx(100.0, abs=0.005)


def test_равные_доли_делаются_по_явной_просьбе_при_удалении(
    manage: Callable[..., Run], зоны: Path
) -> None:
    r = manage("zone-remove", "staff", "--equal-shares")
    assert r.code == 0, r.text

    доли = читать_доли(зоны)
    assert len(доли) == 9, доли
    assert set(доли.values()) == {"11.1111"}, f"доли не уравнены: {доли}"


# --- пока человек не сказал, что делать с долями, файл не трогается ----------


def test_добавление_зоны_без_решения_о_долях_это_отказ(
    manage: Callable[..., Run], зоны: Path
) -> None:
    было = md5(зоны)

    r = manage("zone-add", "--code", "terrace", "--name-ru", "Терраса")

    assert r.code != 0, f"зона добавлена без решения о долях: {r.text!r}"
    assert md5(зоны) == было, "файл зон изменён, хотя команда отказала"
    assert "--share" in r.text, f"в отказе не назван путь «задать долю»: {r.text!r}"
    assert "--equal-shares" in r.text, f"в отказе не назван путь «уравнять»: {r.text!r}"


def test_удаление_зоны_без_решения_о_долях_это_отказ(
    manage: Callable[..., Run], зоны: Path, методика: Path
) -> None:
    было, было_чек_лист = md5(зоны), md5(методика / "checklist.csv")

    r = manage("zone-remove", "staff")

    assert r.code != 0, f"зона убрана без решения о долях: {r.text!r}"
    assert md5(зоны) == было, "файл зон изменён, хотя команда отказала"
    assert md5(методика / "checklist.csv") == было_чек_лист, "чек-лист правлен при отказе"
    assert "--keep-shares" in r.text, f"в отказе не назван путь «не трогать доли»: {r.text!r}"
    assert "--equal-shares" in r.text, f"в отказе не назван путь «уравнять»: {r.text!r}"


def test_противоречивые_флаги_это_отказ(manage: Callable[..., Run], зоны: Path) -> None:
    """T104-прецедент: два взаимоисключающих флага вместе — отказ, а не молчаливый выбор."""
    было = md5(зоны)

    r = manage("zone-remove", "staff", "--keep-shares", "--equal-shares")

    assert r.code != 0, f"противоречие проглочено: {r.text!r}"
    assert md5(зоны) == было, "файл зон изменён, хотя команда отказала"


def test_доля_новой_зоны_и_уравнивание_вместе_это_отказ(
    manage: Callable[..., Run], зоны: Path
) -> None:
    было = md5(зоны)

    r = manage(
        "zone-add",
        "--code",
        "terrace",
        "--name-ru",
        "Терраса",
        "--share",
        "5",
        "--equal-shares",
    )

    assert r.code != 0, f"противоречие проглочено: {r.text!r}"
    assert md5(зоны) == было, "файл зон изменён, хотя команда отказала"


def test_отрицательная_доля_это_отказ(manage: Callable[..., Run], зоны: Path) -> None:
    было = md5(зоны)

    r = manage("zone-add", "--code", "terrace", "--name-ru", "Терраса", "--share", "-5")

    assert r.code != 0, f"зона заведена с отрицательной долей: {r.text!r}"
    assert md5(зоны) == было, "файл зон изменён, хотя команда отказала"
