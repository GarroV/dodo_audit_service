"""T020: чек-лист, зоны и допустимые классы как их отдаёт блок `domain`.

Пункты связываются кодом, не формулировкой (`docs/02-domain.md`), а язык —
параметр, а не константа: у пункта и у зоны есть обе формулировки сразу.

Методика здесь синтетическая — `tests/methodology` (T141, T146). Раньше файл
читал боевую и цитировал её дословно: формулировку вопроса и оба названия зоны.
Это красило сборку от чужой правки данных и клало данные, заведённые вне git
(D002), в публичный репозиторий. Совпадают с боевой только идентификаторы —
коды пунктов и зон, классы: ими продукт связывает сущности.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
from conftest import TEST_DATA

from src.domain import allowed_levels, list_items, list_zones
from src.domain.errors import ValidationError

ZONE_CODES = {
    "facade",
    "dining",
    "dry_storage",
    "freezer",
    "fridge",
    "hot_kitchen",
    "cold_kitchen",
    "dough",
    "dishwashing",
    "staff",
}


def test_чек_лист_читается_целиком(domain_env: Path) -> None:
    """Читаются все строки файла, а не первая горсть, и коды доезжают целыми."""
    items = list_items()
    строк = sum(
        1
        for line in (TEST_DATA / "checklist.csv").read_text(encoding="utf-8").splitlines()[1:]
        if line and not line.startswith(("#", '"#'))
    )
    assert len(items) == строк, "пункты потерялись при разборе файла"
    codes = {i.code for i in items}
    assert {"CLN05", "PRD01", "INF10"} <= codes


def test_у_пункта_есть_код_класс_зоны_и_срок(domain_env: Path) -> None:
    item = next(i for i in list_items() if i.code == "PRD01")
    assert item.levels == ["D1", "D2"]
    assert item.zones == ["fridge", "cold_kitchen", "hot_kitchen"]
    assert item.days == 10
    assert item.kind == "violation"


def test_формулировка_и_процесс_зависят_от_языка(domain_env: Path) -> None:
    """Обе формулировки лежат рядом, а выбирает язык вызывающий.

    Строки берутся из файла методики, а не вписаны в тест: вписанная строка
    проверяла бы память автора, а не то, что блок отдал прочитанное.
    """
    строка = next(
        r
        for r in csv.DictReader((TEST_DATA / "checklist.csv").open(encoding="utf-8-sig"))
        if r["id"] == "CLN05"
    )
    item = next(i for i in list_items() if i.code == "CLN05")

    assert item.question("ru") == строка["question_ru"]
    assert item.question("en") == строка["question_en"]
    assert item.process("ru") == строка["process_ru"]
    assert item.process("en") == строка["process_en"]
    assert item.question("ru") != item.question("en"), "языки в методике не различаются"


def test_фильтр_по_зоне_оставляет_только_применимые(domain_env: Path) -> None:
    codes = {i.code for i in list_items(zone="fridge")}
    assert "PRD01" in codes, "пункт применим к холодильной камере"
    assert "CLN05" not in codes, "печь в холодильной камере не проверяют"


def test_пункт_на_все_зоны_попадает_в_любой_фильтр(domain_env: Path) -> None:
    """`zones = *` в чек-листе означает «во всех зонах», а не «ни в одной»."""
    assert "INF10" in {i.code for i in list_items(zone="fridge")}
    assert "INF10" in {i.code for i in list_items(zone="facade")}


def test_фильтр_по_несуществующей_зоне_это_отказ(domain_env: Path) -> None:
    with pytest.raises(ValidationError) as e:
        list_items(zone="подсобка")
    assert "подсобка" in str(e.value)


def test_служебные_пункты_отделимы_видом(domain_env: Path) -> None:
    """`aggregate` и `info` аудитору не предлагают — вид пункта должен быть виден."""
    kinds = {i.kind for i in list_items()}
    assert kinds == {"violation", "aggregate", "info"}
    сколько = len(list_items(kind="violation"))
    assert 0 < сколько < len(list_items()), "фильтр по виду ничего не отсеял"


def test_допустимые_классы_берутся_из_чек_листа(domain_env: Path) -> None:
    assert allowed_levels("CLN05") == ["D1"]
    assert allowed_levels("PRD01") == ["D1", "D2"]
    assert allowed_levels("INF10") == ["D0"], "информационная запись — это класс D0"


def test_неизвестный_код_это_отказ_а_не_пустой_список(domain_env: Path) -> None:
    with pytest.raises(ValidationError) as e:
        allowed_levels("XXX99")
    assert "XXX99" in str(e.value)


def test_зоны_читаются_с_долями_и_обоими_названиями(domain_env: Path) -> None:
    zones = list_zones()
    assert {z.code for z in zones} == ZONE_CODES
    assert sum(z.share_pct for z in zones) == pytest.approx(100.0)
    строка = next(
        r
        for r in csv.DictReader((TEST_DATA / "zones.csv").open(encoding="utf-8-sig"))
        if r["code"] == "fridge"
    )
    fridge = next(z for z in zones if z.code == "fridge")
    assert fridge.title("ru") == строка["name_ru"]
    assert fridge.title("en") == строка["name_en"]


# Версия методики проверяется отдельно — `tests/test_domain_version.py`: после
# D050 идентификатор составной (имя, дата, отпечаток), и случаев там столько,
# что в файле про чтение чек-листа они перестали быть видны.
