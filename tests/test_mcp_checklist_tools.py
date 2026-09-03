"""T098: инструменты методики — что агент видит в ответ и чего не видит.

Хранилище версий проверяется отдельно (`test_mcp_checklist_store.py`); здесь —
поверхность, которую агент держит в руках: форма ответа, отказ вместо
«готово, но», и границы аргументов, которые до движка доходить не должны.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from mcp_checklist_harness import build_methodology

from src.mcp import checklist_tools as инструменты
from src.mcp.checklist import Store, current_version, read_journal
from src.mcp.errors import ChecklistError

АРЕНДАТОР = "укашка"


@pytest.fixture
def методика(tmp_path: Path) -> Path:
    return build_methodology(tmp_path / "живая-методика")


@pytest.fixture
def store(tmp_path: Path, методика: Path) -> Store:
    return Store(root=tmp_path / "хранилище", live=методика)


def _завести(store: Store, **прочее: object) -> dict:
    аргументы: dict[str, object] = {
        "process": "Проба",
        "question_ru": "Проба пера",
        "levels": "D1",
        "zones": "fridge",
        "days": 5,
        "criteria": "D1: проба",
        "version_name": "imf",
    }
    аргументы.update(прочее)
    return инструменты.add_checklist_item(tenant=АРЕНДАТОР, store=store, **аргументы)


# --- чтение -------------------------------------------------------------------


def test_версии_показывают_действующую_и_последнюю(store: Store) -> None:
    """Действующая и последняя — разные вещи: правка не публикуется сама, и
    агент, спутавший их, скажет человеку «методика изменена», когда движок
    ещё считает по старой."""
    исходная = current_version(store)
    новая = _завести(store, code="TST01")["version"]

    ответ = инструменты.checklist_versions(tenant=АРЕНДАТОР, store=store)

    assert ответ["current"] == исходная
    assert ответ["latest"] == новая
    assert ответ["count"] == 2
    assert {в["version"] for в in ответ["versions"]} == {исходная, новая}


def test_версия_помечена_действующей_ровно_одна(store: Store) -> None:
    _завести(store, code="TST01")

    ответ = инструменты.checklist_versions(tenant=АРЕНДАТОР, store=store)

    assert sum(1 for в in ответ["versions"] if в["current"]) == 1


def test_перечень_пунктов_идёт_вместе_с_зонами(store: Store) -> None:
    """Пункт применим в зонах, а зоны названы кодами: без справочника зон
    рядом агент не сможет ни прочитать колонку `zones`, ни заполнить её."""
    ответ = инструменты.checklist_items(tenant=АРЕНДАТОР, store=store)

    assert {п["id"] for п in ответ["items"]} == {"CLN01", "CLN02"}
    assert {з["code"] for з in ответ["zones"]} == {"fridge", "dough"}


def test_перечень_пунктов_отбирается_по_процессу(store: Store) -> None:
    ответ = инструменты.checklist_items(tenant=АРЕНДАТОР, store=store, process="чистот")

    assert ответ["count"] == 2
    assert ответ["filters"]["process"] == "чистот"


def test_отбор_ни_во_что_не_попавший_говорит_об_этом_словами(store: Store) -> None:
    """Пустой список читается как «в методике ничего такого нет». Ответ обязан
    сказать это словами, а не оставить пустоту на догадку."""
    ответ = инструменты.checklist_items(tenant=АРЕНДАТОР, store=store, process="персонал")

    assert ответ["count"] == 0
    assert "no checklist items" in ответ["status"]


def test_перечень_пунктов_критериев_не_несёт(store: Store) -> None:
    """Критерии бывают на страницу на пункт: в перечне из 136 строк они
    превратили бы ответ в выгрузку, которую агент не дочитает."""
    ответ = инструменты.checklist_items(tenant=АРЕНДАТОР, store=store)

    assert all("criteria" not in пункт for пункт in ответ["items"])


def test_один_пункт_отдаётся_с_критериями(store: Store) -> None:
    ответ = инструменты.checklist_item(tenant=АРЕНДАТОР, store=store, code="CLN01")

    assert ответ["item"]["id"] == "CLN01"
    assert "слой грязи" in ответ["item"]["criteria"]


def test_пункты_читаются_из_названной_версии(store: Store) -> None:
    исходная = current_version(store)
    новая = _завести(store, code="TST01")["version"]

    прежние = инструменты.checklist_items(tenant=АРЕНДАТОР, store=store, version=исходная)
    свежие = инструменты.checklist_items(tenant=АРЕНДАТОР, store=store, version=новая)

    assert "TST01" not in {п["id"] for п in прежние["items"]}
    assert "TST01" in {п["id"] for п in свежие["items"]}


# --- правка -------------------------------------------------------------------


def test_правка_отвечает_новой_версией_и_говорит_что_не_опубликована(store: Store) -> None:
    """Самое опасное недоразумение этого блока: агент пересказывает «пункт
    добавлен» так, будто проверки уже считаются по новой методике."""
    ответ = _завести(store, code="TST01")

    assert ответ["published"] is False
    assert ответ["version"] != ответ["base_version"]
    assert "not published yet" in ответ["status"]


def test_отклонённая_правка_это_отказ_а_не_ответ_с_пометкой(store: Store) -> None:
    """Поле `accepted: false` в обычном ответе агент однажды перескажет как
    «сделано». Отказ он так пересказать не может."""
    with pytest.raises(ChecklistError) as отказ:
        _завести(store, code="TST01", criteria=None)

    assert "нет критериев" in str(отказ.value)


def test_правка_пункта_меняет_только_названное(store: Store) -> None:
    ответ = инструменты.edit_checklist_item(
        tenant=АРЕНДАТОР, store=store, code="CLN01", days=3, version_name="imf"
    )
    пункт = инструменты.checklist_item(
        tenant=АРЕНДАТОР, store=store, code="CLN01", version=ответ["version"]
    )["item"]

    assert пункт["days"] == "3"
    assert пункт["question_ru"] == "Пол чистый"
    assert пункт["levels"] == "D1;D2"


def test_снятый_пункт_остаётся_в_методике_выключенным(store: Store) -> None:
    """Выключенный пункт видно в файле — так видно и саму правку, и вернуть
    его можно одним вызовом. Удалённый совсем восстанавливается только из
    прежней версии."""
    ответ = инструменты.remove_checklist_item(
        tenant=АРЕНДАТОР, store=store, code="CLN01", version_name="imf"
    )
    пункт = инструменты.checklist_item(
        tenant=АРЕНДАТОР, store=store, code="CLN01", version=ответ["version"]
    )["item"]

    assert пункт["kind"] == "off"


def test_снятый_пункт_возвращается(store: Store) -> None:
    инструменты.remove_checklist_item(
        tenant=АРЕНДАТОР, store=store, code="CLN01", version_name="imf"
    )

    ответ = инструменты.restore_checklist_item(tenant=АРЕНДАТОР, store=store, code="CLN01")
    пункт = инструменты.checklist_item(
        tenant=АРЕНДАТОР, store=store, code="CLN01", version=ответ["version"]
    )["item"]

    assert пункт["kind"] == "violation"


def test_удаление_совсем_убирает_строку(store: Store) -> None:
    ответ = инструменты.remove_checklist_item(
        tenant=АРЕНДАТОР, store=store, code="CLN01", hard=True, version_name="imf"
    )

    пункты = инструменты.checklist_items(tenant=АРЕНДАТОР, store=store, version=ответ["version"])[
        "items"
    ]
    assert {п["id"] for п in пункты} == {"CLN02"}


def test_зона_заводится_с_уравненными_долями(store: Store) -> None:
    ответ = инструменты.add_zone(
        tenant=АРЕНДАТОР,
        store=store,
        code="terrace",
        name_ru="Терраса",
        equal_shares=True,
        version_name="imf",
    )

    зоны = инструменты.checklist_items(tenant=АРЕНДАТОР, store=store, version=ответ["version"])[
        "zones"
    ]
    assert {з["code"] for з in зоны} == {"fridge", "dough", "terrace"}


def test_зона_убирается_и_из_списков_зон_у_пунктов(store: Store) -> None:
    """Убранная зона, оставшаяся в колонке `zones` у пункта, — это код без
    зоны: движок такую методику считать откажется."""
    ответ = инструменты.remove_zone(
        tenant=АРЕНДАТОР, store=store, code="fridge", equal_shares=True, version_name="imf"
    )

    выдача = инструменты.checklist_items(tenant=АРЕНДАТОР, store=store, version=ответ["version"])
    assert {з["code"] for з in выдача["zones"]} == {"dough"}
    assert all("fridge" not in п["zones"] for п in выдача["items"])


def test_решение_о_долях_зон_остаётся_за_человеком(store: Store) -> None:
    """T112: доли — вес зоны в оценке, и раздать их за управляющую компанию
    движок не вправе. Отказ доходит до агента словами движка."""
    with pytest.raises(ChecklistError) as отказ:
        инструменты.add_zone(
            tenant=АРЕНДАТОР, store=store, code="terrace", name_ru="Терраса", version_name="imf"
        )

    assert "--equal-shares" in str(отказ.value)


# --- границы аргументов -------------------------------------------------------


def test_информационный_пункт_заводится_своим_видом(store: Store) -> None:
    """Информационная запись (замер температуры, фото продукта) — не нарушение
    и на оценку не влияет, но в отчёте нужна. Вид пункта решает это, и завести
    такой пункт агент обязан уметь."""
    ответ = _завести(store, code="INF20", kind="info", criteria=None)

    пункт = инструменты.checklist_item(
        tenant=АРЕНДАТОР, store=store, code="INF20", version=ответ["version"]
    )["item"]
    assert пункт["kind"] == "info"


@pytest.mark.parametrize("вид", ["off", "нарушение", "Violation", ""])
def test_неизвестный_вид_пункта_это_отказ(store: Store, вид: str) -> None:
    """`off` — не вид пункта, а выключенное состояние. Отданный сюда, он дал
    бы второй способ выключить пункт, которого никто не ищет в журнале."""
    with pytest.raises(ChecklistError) as отказ:
        _завести(store, code="TST01", kind=вид)

    assert "вид пункта" in str(отказ.value).lower()


@pytest.mark.parametrize("код", ["--hard", "CLN 01", "ПУНКТ", "a" * 40])
def test_негодный_код_до_движка_не_доходит(store: Store, код: str) -> None:
    with pytest.raises(ChecklistError):
        _завести(store, code=код)


def test_негодный_код_зоны_до_движка_не_доходит(store: Store) -> None:
    with pytest.raises(ChecklistError):
        инструменты.add_zone(
            tenant=АРЕНДАТОР,
            store=store,
            code="--equal-shares",
            name_ru="Побег",
            equal_shares=True,
            version_name="imf",
        )


def test_формулировка_с_дефиса_флагом_не_становится(store: Store) -> None:
    """Формулировка пункта вполне может начинаться с дефиса, и разбор
    аргументов движка прочитал бы её как имя следующего флага. Значения
    уходят формой `--флаг=значение` именно поэтому."""
    ответ = _завести(
        store, code="TST01", question_ru="--levels подмена", question_en="-5 °C и ниже"
    )

    пункт = инструменты.checklist_item(
        tenant=АРЕНДАТОР, store=store, code="TST01", version=ответ["version"]
    )["item"]
    assert пункт["question_ru"] == "--levels подмена"
    assert пункт["question_en"] == "-5 °C и ниже"


def test_слишком_длинное_пояснение_это_отказ(store: Store) -> None:
    """Журнал читает человек: причина правки одной фразой, а не выгрузка."""
    with pytest.raises(ChecklistError):
        _завести(store, code="TST01", note="я" * (инструменты.MAX_NOTE + 1))


def test_пояснение_попадает_в_журнал(store: Store) -> None:
    _завести(store, code="TST01", note="просили на созвоне")

    assert read_journal(store)[-1]["note"] == "просили на созвоне"


def test_пустое_пояснение_журнал_не_засоряет(store: Store) -> None:
    _завести(store, code="TST01", note="   ")

    assert read_journal(store)[-1]["note"] is None


# --- публикация ---------------------------------------------------------------


def test_публикация_делает_версию_действующей(tmp_path: Path, методика: Path) -> None:
    store = Store(root=tmp_path / "хранилище", live=методика)
    новая = _завести(store, code="TST01")["version"]
    рабочее = Store(root=store.root, live=store.root / "current")

    ответ = инструменты.publish_checklist_version(tenant=АРЕНДАТОР, store=рабочее, version=новая)

    assert ответ["published"] == новая
    assert "not recalculated" in ответ["status"]
    assert current_version(рабочее) == новая


def test_публикация_которую_движок_не_увидит_это_отказ(store: Store) -> None:
    """Молчаливый сбой худшего вида: агент говорит «опубликовано», а проверки
    продолжают считаться по прежней методике."""
    новая = _завести(store, code="TST01")["version"]

    with pytest.raises(ChecklistError) as отказ:
        инструменты.publish_checklist_version(tenant=АРЕНДАТОР, store=store, version=новая)

    assert "AUDIT_DATA_DIR" in str(отказ.value)
