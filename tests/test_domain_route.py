"""T061: порядок обхода — зоны и пункты выстраиваются как идёт аудитор.

Порядок живёт в данных (`route.csv` в каталоге методики), а не в коде: методику
правит человек управляющей компании, а не разработчик. Отдельным файлом, а не
колонкой в `checklist.csv`, потому что `engine/manage.py` перезаписывает
чек-лист и зоны фиксированным списком колонок — любая правка методики через него
стёрла бы маршрут молча.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import requires_data

from src.domain import checklist_version, list_items, list_zones
from src.domain.errors import ConfigError
from src.domain.route import ROUTE_FILE

pytestmark = requires_data


@pytest.fixture
def методика(data_copy: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Копия боевой методики как `AUDIT_DATA_DIR` — маршрут в ней можно менять."""
    monkeypatch.setenv("AUDIT_DATA_DIR", str(data_copy))
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    monkeypatch.chdir(tmp_path)
    return data_copy


def _маршрут(методика: Path, *строки: str) -> None:
    body = "\n".join(("entity,code,order", *строки))
    (методика / ROUTE_FILE).write_text(f"{body}\n", encoding="utf-8")


def test_зоны_идут_по_маршруту_а_не_по_порядку_в_файле(методика: Path) -> None:
    """Аудитор идёт фасад → зал → кухня; в zones.csv порядок свой, и он не главный."""
    _маршрут(
        методика,
        "zone,staff,10",
        "zone,facade,20",
        "zone,dining,30",
        "zone,hot_kitchen,40",
    )

    коды = [z.code for z in list_zones()[:4]]

    assert коды == ["staff", "facade", "dining", "hot_kitchen"]


def test_зоны_без_маршрута_идут_следом_в_прежнем_порядке(методика: Path) -> None:
    """Неупомянутая зона не исчезает и не всплывает наверх — она просто последняя."""
    _маршрут(методика, "zone,staff,10")

    коды = [z.code for z in list_zones()]

    assert коды[0] == "staff"
    assert коды[1:] == [
        "facade",
        "dining",
        "dry_storage",
        "freezer",
        "fridge",
        "hot_kitchen",
        "cold_kitchen",
        "dough",
        "dishwashing",
    ], "остальные зоны обязаны сохранить порядок методики"


def test_пункты_внутри_зоны_идут_по_маршруту(методика: Path) -> None:
    """Внутри зоны порядок тоже маршрутный: аудитор не возвращается к началу списка."""
    было = [i.code for i in list_items(zone="dining")]
    последний, первый = было[-1], было[0]
    assert последний != первый

    _маршрут(методика, f"item,{последний},10", f"item,{первый},20")

    стало = [i.code for i in list_items(zone="dining")]

    assert стало[:2] == [последний, первый]
    assert set(стало) == set(было), "маршрут не имеет права терять или добавлять пункты"


def test_без_файла_маршрута_порядок_остаётся_как_в_методике(методика: Path) -> None:
    """Маршрут необязателен: методика без него работает ровно как раньше."""
    # Копия боевой методики маршрут уже несёт — здесь проверяется жизнь без него.
    (методика / ROUTE_FILE).unlink(missing_ok=True)

    assert next(z.code for z in list_zones()) == "facade"
    assert next(i.code for i in list_items()) == "PRD01"


def test_маршрут_с_неизвестной_зоной_отвергается(методика: Path) -> None:
    """Опечатка в коде зоны иначе просто ничего не делает — и никто не заметит."""
    _маршрут(методика, "zone,terrace,10")

    with pytest.raises(ConfigError) as отказ:
        list_zones()

    assert "terrace" in str(отказ.value)
    assert ROUTE_FILE in str(отказ.value)


def test_маршрут_с_неизвестным_пунктом_отвергается(методика: Path) -> None:
    _маршрут(методика, "item,ZZZ99,10")

    with pytest.raises(ConfigError) as отказ:
        list_items()

    assert "ZZZ99" in str(отказ.value)


def test_дубль_кода_в_маршруте_отвергается(методика: Path) -> None:
    """Две позиции у одной зоны — маршрут неоднозначен, молча выбирать нельзя."""
    _маршрут(методика, "zone,facade,10", "zone,facade,20")

    with pytest.raises(ConfigError) as отказ:
        list_zones()

    assert "facade" in str(отказ.value)


def test_нечисловая_позиция_в_маршруте_отвергается(методика: Path) -> None:
    """Нечисловое движок бы тихо превратил в ноль — и зона уехала бы в начало."""
    _маршрут(методика, "zone,facade,первая")

    with pytest.raises(ConfigError) as отказ:
        list_zones()

    assert "первая" in str(отказ.value)


def test_чужой_вид_записи_в_маршруте_отвергается(методика: Path) -> None:
    """В маршруте только зоны и пункты: процессы связываются формулировкой, а не кодом."""
    _маршрут(методика, "process,Чистота,10")

    with pytest.raises(ConfigError) as отказ:
        list_zones()

    assert "process" in str(отказ.value)


def test_строки_с_решёткой_в_маршруте_пропускаются(методика: Path) -> None:
    """Маршрут читает человек, и пометки в нём — норма, а не мусор."""
    _маршрут(методика, "# сначала улица, потом зал", "zone,staff,10")

    assert next(z.code for z in list_zones()) == "staff"


def test_правка_маршрута_меняет_версию_методики(методика: Path) -> None:
    """Маршрут — часть методики (D050): его правка обязана поднимать версию."""
    (методика / "checklist_version.txt").write_text("imf 2026-09-01\n", encoding="utf-8")
    _маршрут(методика, "zone,facade,10")
    было = checklist_version()

    _маршрут(методика, "zone,facade,10", "zone,dining,20")

    assert checklist_version() != было, "правка маршрута прошла под прежней версией"


def test_запись_маршрута_без_кода_отвергается(методика: Path) -> None:
    """Строка с позицией, но без кода — маршрут, который никуда не ведёт."""
    _маршрут(методика, "zone,,10")

    with pytest.raises(ConfigError) as отказ:
        list_zones()

    assert "пустой код" in str(отказ.value)
