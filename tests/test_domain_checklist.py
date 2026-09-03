"""T020: чек-лист, зоны и допустимые классы как их отдаёт блок `domain`.

Пункты связываются кодом, не формулировкой (`docs/02-domain.md`), а язык —
параметр, а не константа: у пункта и у зоны есть обе формулировки сразу.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import requires_data

from src.domain import allowed_levels, list_items, list_zones
from src.domain.errors import ValidationError

pytestmark = requires_data

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
    items = list_items()
    assert len(items) == 136, "методика — 136 строк, см. docs/02-domain.md"
    codes = {i.code for i in items}
    assert {"CLN05", "PRD01", "INF10"} <= codes


def test_у_пункта_есть_код_класс_зоны_и_срок(domain_env: Path) -> None:
    item = next(i for i in list_items() if i.code == "PRD01")
    assert item.levels == ["D1", "D2"]
    assert item.zones == ["fridge", "cold_kitchen", "hot_kitchen"]
    assert item.days == 10
    assert item.kind == "violation"


def test_формулировка_и_процесс_зависят_от_языка(domain_env: Path) -> None:
    item = next(i for i in list_items() if i.code == "CLN05")
    assert item.question("ru") == "Печь без загрязнений (D1)"
    assert item.question("en") == "Oven is clean (D1)"
    assert item.process("ru") == "Чистота"
    assert item.process("en") == "Cleanliness"


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
    assert len(list_items(kind="violation")) == 123


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
    fridge = next(z for z in zones if z.code == "fridge")
    assert fridge.title("ru") == "Холодильная камера"
    assert fridge.title("en") == "Refrigerator"


# Версия методики проверяется отдельно — `tests/test_domain_version.py`: после
# D050 идентификатор составной (имя, дата, отпечаток), и случаев там столько,
# что в файле про чтение чек-листа они перестали быть видны.
