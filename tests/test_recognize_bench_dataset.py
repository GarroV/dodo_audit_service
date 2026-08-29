"""T035: тесты оснастки офлайн-набора распознавания (`tools/bench_dataset.py`).

Проверяют сборку случаев из боевых проверок в `examples/` — эталонные коды,
классы, зоны и вид кадра (нарушение/информация) — и отказ при неполном или
пустом входе (правило проекта: молчаливого успеха быть не должно).
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest
from conftest import EXAMPLES, requires_examples

from tools.bench_dataset import load_cases

pytestmark = requires_examples


def test_набор_собирается_из_двух_проверок() -> None:
    # Act
    cases = load_cases(EXAMPLES)

    # Assert
    assert len(cases) == 31
    assert sum(1 for c in cases if c.kind == "violation") == 15
    assert sum(1 for c in cases if c.kind == "info") == 16


def test_нарушение_несёт_код_класс_и_зону() -> None:
    # Arrange
    cases = {c.case_id: c for c in load_cases(EXAMPLES)}

    # Act
    case = cases["belgrade-1/p09.jpg"]

    # Assert
    assert case.code == "CLN05"
    assert case.level == "D1"
    assert case.zone == "hot_kitchen"
    assert case.kind == "violation"


def test_информационная_запись_несёт_код_и_класс_D0() -> None:
    # Arrange
    cases = {c.case_id: c for c in load_cases(EXAMPLES)}

    # Act
    case = cases["belgrade-1/p01.jpg"]

    # Assert
    assert case.code == "INF11"
    assert case.level == "D0"
    assert case.zone == "dining"
    assert case.kind == "info"


def test_вторая_информационная_запись_из_belgrade_1() -> None:
    # Arrange
    cases = {c.case_id: c for c in load_cases(EXAMPLES)}

    # Act
    case = cases["belgrade-1/p19.jpg"]

    # Assert
    assert case.code == "INF10"
    assert case.level == "D0"
    assert case.zone == "hot_kitchen"
    assert case.kind == "info"


def test_код_не_пуст_и_у_info_класс_всегда_D0() -> None:
    # Act
    cases = load_cases(EXAMPLES)

    # Assert
    assert all(c.code for c in cases), "код должен быть заполнен на всех случаях"
    info_cases = [c for c in cases if c.kind == "info"]
    assert info_cases, "в наборе должны быть информационные случаи"
    assert all(c.level == "D0" for c in info_cases), "у info-случаев класс всегда D0"


def test_вторая_проверка_тоже_разбирается() -> None:
    # Arrange
    cases = {c.case_id: c for c in load_cases(EXAMPLES)}

    # Act
    case = cases["belgrade-2/q42.jpg"]

    # Assert
    assert case.code == "CLN06"
    assert case.level == "D1"
    assert case.zone == "dough"


def test_кадры_существуют_id_уникальны_и_список_отсортирован() -> None:
    # Act
    cases = load_cases(EXAMPLES)

    # Assert
    ids = [c.case_id for c in cases]
    assert len(set(ids)) == len(ids), "case_id должны быть уникальны"
    assert ids == sorted(ids), "список должен быть отсортирован по case_id"
    for case in cases:
        assert case.photo.is_file(), f"кадр не найден: {case.photo}"
        assert case.photo.is_absolute(), f"путь к кадру должен быть абсолютным: {case.photo}"


def _copy_inspection(src: Path, dst: Path) -> None:
    """Скопировать одну папку проверки (json + фото) во временный каталог."""
    dst.mkdir(parents=True)
    shutil.copyfile(src / "inspection.json", dst / "inspection.json")
    shutil.copytree(src / "photos", dst / "photos")


def test_отказ_при_пропавшем_кадре(tmp_path: Path) -> None:
    # Arrange: копия belgrade-1 во временном каталоге, из которой пропал кадр
    examples_root = tmp_path / "examples"
    _copy_inspection(EXAMPLES / "belgrade-1", examples_root / "belgrade-1")
    missing = examples_root / "belgrade-1" / "photos" / "p09.jpg"
    missing.unlink()

    # Act / Assert
    with pytest.raises(FileNotFoundError, match=re.escape("p09.jpg")):
        load_cases(examples_root)


def test_отказ_на_пустом_каталоге(tmp_path: Path) -> None:
    # Act / Assert
    with pytest.raises(FileNotFoundError):
        load_cases(tmp_path)
