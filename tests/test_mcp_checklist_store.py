"""T098: хранилище версий методики — правка идёт в новую версию, а не поверх боевой.

Здесь проверяется само хранилище, без инструментов и без сервера: что правка
не трогает боевой набор, что версия названа по D050, что негодную методику
хранилище не принимает и что каждое действие видно в журнале.

Методика в этих тестах синтетическая и крошечная (два пункта, две зоны): её
достаточно, чтобы движок посчитал по ней оценку, а прогон не зависит от
`data/` — она лежит вне git (D002) и на чужой машине её может не быть.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import pytest
from mcp_checklist_harness import ЧЕКЛИСТ, build_methodology

from src.mcp.checklist import (
    JOURNAL_FILE,
    VERSIONS_DIR,
    Outcome,
    Store,
    apply_change,
    current_version,
    publish,
    read_item,
    read_items,
    read_journal,
    versions,
)
from src.mcp.errors import ChecklistError

АРЕНДАТОР = "укашка"
СЕГОДНЯ = date(2026, 9, 3)


def _слепок(каталог: Path) -> dict[str, str]:
    """Содержимое каталога по файлам — чтобы поймать правку боевой методики."""
    return {
        путь.name: hashlib.sha256(путь.read_bytes()).hexdigest()
        for путь in sorted(каталог.iterdir())
        if путь.is_file()
    }


@pytest.fixture
def методика(tmp_path: Path) -> Path:
    """Боевая методика: та, которую читает движок. Хранилище её только читает."""
    return build_methodology(tmp_path / "живая-методика")


@pytest.fixture
def хранилище(tmp_path: Path, методика: Path) -> Store:
    return Store(root=tmp_path / "хранилище", live=методика)


def _добавить(
    хранилище: Store,
    *,
    code: str = "TST01",
    criteria: str | None = "D1: проба",
    version_name: str | None = "imf",
    base: str | None = None,
) -> Outcome:
    return apply_change(
        хранилище,
        tenant=АРЕНДАТОР,
        tool="add_checklist_item",
        command="add",
        options={
            "id": code,
            "process": "Проба",
            "question-ru": "Проба пера",
            "levels": "D1",
            "zones": "fridge",
            "days": 5,
            "criteria": criteria,
        },
        base=base,
        version_name=version_name,
        today=СЕГОДНЯ,
    )


# --- боевая методика не трогается --------------------------------------------


def test_правка_не_меняет_ни_одного_файла_боевой_методики(хранилище: Store, методика: Path) -> None:
    """Главное свойство задачи: правка идёт в НОВУЮ версию. Боевой набор,
    по которому считаются уже назначенные проверки, обязан остаться байт в
    байт таким же."""
    было = _слепок(методика)

    итог = _добавить(хранилище)

    # Сообщение — СЛОВА ДВИЖКА, а не постоянная строка `status` (T187): она у
    # отклонённой правки всегда одна и та же, и упавший тест не говорил, что
    # именно случилось. Разбор такого падения стоил трёх часов.
    assert итог.accepted, итог.refusal
    assert _слепок(методика) == было


def test_новая_версия_содержит_правку_а_прежняя_нет(хранилище: Store) -> None:
    исходная = current_version(хранилище)
    итог = _добавить(хранилище)

    коды_новой = {пункт["id"] for пункт in read_items(хранилище, version=итог.version)}
    коды_прежней = {пункт["id"] for пункт in read_items(хранилище, version=исходная)}

    assert "TST01" in коды_новой
    assert "TST01" not in коды_прежней


def test_прежняя_версия_остаётся_читаемой_после_правки(хранилище: Store) -> None:
    """D050: старые версии не удаляются никогда — проверка, посчитанная по
    прежней методике, обязана остаться объяснимой."""
    исходная = current_version(хранилище)

    _добавить(хранилище)
    _добавить(хранилище, code="TST02", version_name=None)

    имена = {версия.version for версия in versions(хранилище)}
    assert исходная in имена
    assert len(имена) == 3


# --- идентификатор версии (D050) ---------------------------------------------


def test_идентификатор_версии_это_имя_дата_и_отпечаток(хранилище: Store) -> None:
    итог = _добавить(хранилище, version_name="imf")

    assert итог.version.startswith("imf-2026-09-03-")
    отпечаток = итог.version.rsplit("-", 1)[1]
    assert len(отпечаток) == 12
    assert отпечаток != итог.base_version.rsplit("-", 1)[1]


def test_без_имени_набора_правка_не_проходит(хранилище: Store) -> None:
    """Набор, который никто не издавал, — это `local-<отпечаток>` без даты.
    Выдумывать за управляющую компанию имя нельзя, а версия без даты
    противоречит D050, поэтому первая правка обязана имя назвать."""
    with pytest.raises(ChecklistError) as отказ:
        _добавить(хранилище, version_name=None)

    assert "version_name" in str(отказ.value)


def test_имя_набора_подхватывается_из_прежней_версии(хранилище: Store) -> None:
    """Назвать набор нужно один раз: дальше имя берётся из изданной версии, а
    меняется только дата и отпечаток."""
    _добавить(хранилище, version_name="imf")

    второй = _добавить(хранилище, code="TST02", version_name=None)

    assert второй.version.startswith("imf-2026-09-03-")


@pytest.mark.parametrize(
    ("имя", "про_что"),
    [
        ("ИМФ", "Имя набора"),
        ("imf набор", "Имя набора"),
        ("imf/../побег", "Имя набора"),
        ("", "Имя набора"),
        ("imf-2026-09-01", "кончается датой"),
    ],
)
def test_негодное_имя_набора_это_отказ(хранилище: Store, имя: str, про_что: str) -> None:
    """Имя набора попадает в идентификатор версии и в имя каталога хранилища.
    Знак пути в нём — побег из хранилища, дата в конце — вторая дата в
    идентификаторе рядом с той, которую ставит система.

    Сверяется и текст отказа: ниже по коду стоит второй сторож — проверка
    имени ВЕРСИИ, — и он поймал бы те же строки, но сказал бы про версию.
    Человек, назвавший набор кириллицей, пошёл бы искать несуществующую
    версию вместо того, чтобы переименовать набор.
    """
    with pytest.raises(ChecklistError) as отказ:
        _добавить(хранилище, version_name=имя)

    assert про_что in str(отказ.value)


def test_правка_ничего_не_изменившая_это_отказ_а_не_новая_версия(хранилище: Store) -> None:
    """Отпечаток считается по данным: правка, не тронувшая ни байта, дала бы
    «новую версию» с тем же отпечатком. Молча выдать её за изменение значило
    бы записать в журнал правку, которой не было."""
    первый = _добавить(хранилище, version_name="imf")

    with pytest.raises(ChecklistError) as отказ:
        apply_change(
            хранилище,
            tenant=АРЕНДАТОР,
            tool="edit_checklist_item",
            command="edit",
            positional="TST01",
            options={"days": 5},
            base=первый.version,
            today=СЕГОДНЯ,
        )

    assert "ничего не изменила" in str(отказ.value)


# --- валидация до записи ------------------------------------------------------


def test_пункт_без_критериев_версии_не_создаёт(хранилище: Store) -> None:
    """Движок отказывается считать методику, в которой у пункта нет критериев
    (иначе распознавание по фото начнёт угадывать класс). Проверка идёт ДО
    записи: в хранилище такая версия не появляется."""
    было = {версия.version for версия in versions(хранилище)}

    итог = _добавить(хранилище, criteria=None)

    assert not итог.accepted
    assert итог.version is None
    assert {версия.version for версия in versions(хранилище)} == было


def test_отказ_приходит_словами_движка(хранилище: Store) -> None:
    итог = _добавить(хранилище, criteria=None)

    assert "нет критериев" in итог.refusal


def test_несходящиеся_доли_зон_версии_не_создают(хранилище: Store) -> None:
    """T103: зона с явной долей оставляет сумму мимо 100%, и движок такую
    методику считать откажется. Хранилище обязано отказать раньше него."""
    итог = apply_change(
        хранилище,
        tenant=АРЕНДАТОР,
        tool="add_zone",
        command="zone-add",
        options={"code": "terrace", "name-ru": "Терраса", "share": 5},
        version_name="imf",
        today=СЕГОДНЯ,
    )

    assert not итог.accepted
    assert "100%" in итог.refusal


def test_решение_о_долях_остаётся_за_управляющей_компанией(хранилище: Store) -> None:
    """T112: зона добавляется только с явным словом о долях. Отказ движка
    доходит до вызывающего целиком, а не превращается в «не получилось»."""
    итог = apply_change(
        хранилище,
        tenant=АРЕНДАТОР,
        tool="add_zone",
        command="zone-add",
        options={"code": "terrace", "name-ru": "Терраса"},
        version_name="imf",
        today=СЕГОДНЯ,
    )

    assert not итог.accepted
    assert "--equal-shares" in итог.refusal


def test_зона_с_уравненными_долями_проходит(хранилище: Store) -> None:
    итог = apply_change(
        хранилище,
        tenant=АРЕНДАТОР,
        tool="add_zone",
        command="zone-add",
        options={"code": "terrace", "name-ru": "Терраса", "equal-shares": True},
        version_name="imf",
        today=СЕГОДНЯ,
    )

    assert итог.accepted, итог.refusal
    зоны = read_items(хранилище, version=итог.version, what="zones")
    assert {з["code"] for з in зоны} == {"fridge", "dough", "terrace"}


def test_путь_временного_каталога_в_ответ_не_уезжает(хранилище: Store) -> None:
    """Отказ движка называет файл, в котором проблема, — а это временная копия
    под правку. Показать её агенту значит показать путь, которого у человека
    нет и не будет."""
    итог = _добавить(хранилище, criteria=None)

    assert ".tmp" not in итог.refusal
    assert "/var/folders" not in итог.refusal


# --- журнал -------------------------------------------------------------------


def test_журнал_помнит_кто_что_и_когда(хранилище: Store) -> None:
    итог = _добавить(хранилище)

    записи = read_journal(хранилище)
    последняя = записи[-1]
    assert последняя["tenant"] == АРЕНДАТОР
    assert последняя["tool"] == "add_checklist_item"
    assert последняя["outcome"] == "accepted"
    assert последняя["version"] == итог.version
    assert последняя["at"]


def test_журнал_помнит_и_отклонённую_правку(хранилище: Store) -> None:
    """Отклонённая правка — тоже событие: без неё непонятно, почему методика
    не менялась, хотя её пытались менять."""
    _добавить(хранилище, criteria=None)

    последняя = read_journal(хранилище)[-1]
    assert последняя["outcome"] == "refused"
    assert последняя["version"] is None
    assert последняя["refusal"]


def test_журнал_дописывается_а_не_переписывается(хранилище: Store) -> None:
    _добавить(хранилище)
    _добавить(хранилище, code="TST02", version_name=None)

    строки = (хранилище.root / JOURNAL_FILE).read_text(encoding="utf-8").strip().splitlines()
    assert len(строки) >= 3
    for строка in строки:
        json.loads(строка)


def test_секретов_и_токенов_в_журнале_нет(хранилище: Store) -> None:
    """Журнал переживёт и запрос, и клиента: в нём код арендатора, а не то,
    чем он себя предъявил."""
    _добавить(хранилище)

    текст = (хранилище.root / JOURNAL_FILE).read_text(encoding="utf-8")
    assert "Authorization" not in текст
    assert "Bearer" not in текст


# --- нулевая версия -----------------------------------------------------------


def test_хранилище_заводит_нулевую_версию_из_боевой_методики(хранилище: Store) -> None:
    """Хранилище начинается не с пустоты: первая запись в нём — снимок той
    методики, по которой продукт считает сегодня. Иначе первая же правка
    осталась бы без предшественника, и сравнить её было бы не с чем."""
    исходная = current_version(хранилище)

    assert исходная is not None
    assert исходная.startswith("local-")
    каталог = хранилище.root / VERSIONS_DIR / исходная
    assert (каталог / "checklist.csv").read_text(encoding="utf-8") == ЧЕКЛИСТ


def test_нулевая_версия_записана_в_журнал(хранилище: Store) -> None:
    current_version(хранилище)

    первая = read_journal(хранилище)[0]
    assert первая["tool"] == "bootstrap"
    assert первая["outcome"] == "accepted"


def test_боевая_методика_без_файлов_это_отказ(tmp_path: Path) -> None:
    пусто = tmp_path / "пусто"
    пусто.mkdir()
    store = Store(root=tmp_path / "store", live=пусто)

    with pytest.raises(ChecklistError) as отказ:
        current_version(store)

    assert "checklist.csv" in str(отказ.value)


# --- чтение -------------------------------------------------------------------


def test_чтение_пункта_отдаёт_и_критерии(хранилище: Store) -> None:
    пункт = read_item(хранилище, code="CLN01")

    assert пункт["id"] == "CLN01"
    assert "слой грязи" in пункт["criteria"]


def test_чтение_неизвестного_пункта_это_отказ(хранилище: Store) -> None:
    """Пустая выдача читалась бы как «пункта нет в методике», а это может быть
    и опечатка в коде."""
    with pytest.raises(ChecklistError) as отказ:
        read_item(хранилище, code="ZZZ99")

    assert "ZZZ99" in str(отказ.value)


@pytest.mark.parametrize("код", ["НЕТУ01", "--hard", "", "CLN 01"])
def test_код_с_посторонними_знаками_до_движка_не_доходит(хранилище: Store, код: str) -> None:
    """Код уходит движку позиционным аргументом: значение с дефисом впереди
    разбор аргументов прочитал бы как флаг, а его отказ ничего не объясняет
    тому, кто ошибся в коде."""
    with pytest.raises(ChecklistError) as отказ:
        read_item(хранилище, code=код)

    assert "код" in str(отказ.value)


def test_пункт_без_раздела_критериев_отдаётся_с_пустыми_критериями(
    хранилище: Store, tmp_path: Path
) -> None:
    """Критериев может не быть — у служебных пунктов их и не бывает. Это не
    отказ: отказ здесь означал бы, что половина методики нечитаема."""
    исходная = current_version(хранилище)
    (хранилище.root / VERSIONS_DIR / исходная / "criteria.md").unlink()

    assert read_item(хранилище, code="CLN01")["criteria"] == ""


def test_чтение_неизвестной_версии_это_отказ(хранилище: Store) -> None:
    with pytest.raises(ChecklistError):
        read_items(хранилище, version="imf-2026-01-01-000000000000")


@pytest.mark.parametrize("версия", ["../побег", "imf/вложенно", ".", "", "ВЕРСИЯ"])
def test_версия_с_знаком_пути_это_отказ(хранилище: Store, версия: str) -> None:
    """Имя версии приходит от агента и становится куском пути внутри
    хранилища. Знак пути в нём читал бы что угодно на машине.

    Сверяется текст: ниже стоит второй сторож — «такого каталога нет», — и он
    поймал бы те же строки, но только пока за `..` не окажется настоящего
    каталога. Тогда отбор пути становится настоящим побегом, а тест об этом
    молчал бы.
    """
    with pytest.raises(ChecklistError) as отказ:
        read_items(хранилище, version=версия)

    assert "не похоже на версию" in str(отказ.value)


def test_побег_из_хранилища_по_пути_не_читает_чужую_методику(
    хранилище: Store, tmp_path: Path
) -> None:
    """Настоящий побег, а не его форма: за `..` лежит существующий каталог с
    настоящим `checklist.csv`. Проверка одного лишь «такого каталога нет»
    отдала бы его содержимое."""
    current_version(хранилище)
    чужая = build_methodology(tmp_path / "чужая-методика")
    шаги = "../" * (len(чужая.resolve().parts) + 2)

    with pytest.raises(ChecklistError):
        read_items(хранилище, version=f"{шаги}{чужая.resolve().relative_to('/')}")


# --- публикация ---------------------------------------------------------------


def test_публикация_переставляет_текущую_версию(tmp_path: Path, методика: Path) -> None:
    """Движок читает `AUDIT_DATA_DIR`. Чтобы правка стала боевой, этот каталог
    обязан быть самим указателем хранилища — тогда публикация это перестановка
    указателя, а не перезапись методики."""
    store = Store(root=tmp_path / "store", live=методика)
    итог = _добавить(store)
    указатель = store.root / "current"
    рабочее = Store(root=store.root, live=указатель)

    publish(рабочее, tenant=АРЕНДАТОР, version=итог.version)

    assert current_version(рабочее) == итог.version
    assert "TST01" in (указатель / "checklist.csv").read_text(encoding="utf-8")


def test_публикация_отказывает_если_движок_читает_не_хранилище(
    хранилище: Store, методика: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Публикация, которую движок не увидит, — молчаливый сбой: агент скажет
    «опубликовано», а проверки продолжат считаться по старой методике.

    Причина уходит агенту словами и именами переменных, путь — в лог сервера
    (T120): ответ инструмента уезжает в модель, а лог остаётся на машине.
    Разделение проверяется здесь же, чтобы отказ не потерял ни одной половины.
    """
    итог = _добавить(хранилище)
    capsys.readouterr()

    with pytest.raises(ChecklistError) as отказ:
        publish(хранилище, tenant=АРЕНДАТОР, version=итог.version)

    сказано = str(отказ.value)
    assert str(методика) not in сказано
    assert str(хранилище.root) not in сказано
    assert "AUDIT_DATA_DIR" in сказано
    assert str(методика) in capsys.readouterr().err


def test_публикация_неизвестной_версии_это_отказ(tmp_path: Path, методика: Path) -> None:
    store = Store(root=tmp_path / "store", live=методика)
    current_version(store)
    рабочее = Store(root=store.root, live=store.root / "current")

    with pytest.raises(ChecklistError):
        publish(рабочее, tenant=АРЕНДАТОР, version="imf-2026-09-03-000000000000")


def test_публикация_записана_в_журнал(tmp_path: Path, методика: Path) -> None:
    store = Store(root=tmp_path / "store", live=методика)
    итог = _добавить(store)
    рабочее = Store(root=store.root, live=store.root / "current")

    publish(рабочее, tenant=АРЕНДАТОР, version=итог.version)

    последняя = read_journal(рабочее)[-1]
    assert последняя["tool"] == "publish_checklist_version"
    assert последняя["version"] == итог.version


def test_на_месте_указателя_не_ссылка_это_отказ(tmp_path: Path, методика: Path) -> None:
    """Хранилище держится на указателе-ссылке. Обычный файл на его месте
    означает, что каталог собран не этим механизмом, и дописывать в него
    версии нельзя."""
    store = Store(root=tmp_path / "store", live=методика)
    store.root.mkdir(parents=True)
    (store.root / "current").write_text("не ссылка\n", encoding="utf-8")

    with pytest.raises(ChecklistError) as отказ:
        current_version(store)

    assert "ссылка" in str(отказ.value)


def test_боевой_методики_нет_вовсе_это_отказ(tmp_path: Path) -> None:
    store = Store(root=tmp_path / "store", live=tmp_path / "нет-такого-каталога")

    with pytest.raises(ChecklistError) as отказ:
        current_version(store)

    assert "не найден" in str(отказ.value)


def test_методика_которую_движок_не_посчитает_версии_не_создаёт(
    tmp_path: Path, методика: Path
) -> None:
    """`validate` не читает ставки вычетов вовсе, а `score` без них не
    посчитает. Проверка обязана быть настоящим расчётом, а не только разбором
    файлов: иначе агент положил бы набор, ломающий продукт."""
    (методика / "scoring.json").write_text("{ это не json", encoding="utf-8")
    store = Store(root=tmp_path / "store", live=методика)

    итог = _добавить(store)

    assert not итог.accepted
    assert {версия.version for версия in versions(store)} == {current_version(store)}


# --- порядок правок -----------------------------------------------------------


def test_вторая_правка_подряд_не_теряет_первую(хранилище: Store) -> None:
    """Правка не публикуется сама, поэтому действующая версия остаётся
    прежней. Отсчитывай вторая правка от неё — она молча потеряла бы первую, и
    опубликован оказался бы набор без неё."""
    первый = _добавить(хранилище, code="TST01")

    второй = _добавить(хранилище, code="TST02", version_name=None)

    assert второй.base_version == первый.version
    коды = {пункт["id"] for пункт in read_items(хранилище, version=второй.version)}
    assert {"TST01", "TST02"} <= коды


def test_правку_можно_отсчитать_от_названной_версии(хранилище: Store) -> None:
    """Ветка от прежней версии — законное действие: так откатывают неудачную
    правку, не удаляя её (D050 запрещает удалять версии вовсе)."""
    исходная = current_version(хранилище)
    _добавить(хранилище, code="TST01")

    ветка = apply_change(
        хранилище,
        tenant=АРЕНДАТОР,
        tool="add_checklist_item",
        command="add",
        options={
            "id": "TST02",
            "process": "Проба",
            "question-ru": "Другая ветка",
            "levels": "D1",
            "zones": "fridge",
            "days": 5,
            "criteria": "D1: проба",
        },
        base=исходная,
        version_name="imf",
        today=СЕГОДНЯ,
    )

    коды = {пункт["id"] for пункт in read_items(хранилище, version=ветка.version)}
    assert "TST02" in коды
    assert "TST01" not in коды


def test_одна_и_та_же_правка_дважды_это_одна_версия_а_не_две(хранилище: Store) -> None:
    """Отпечаток считается по данным, поэтому две одинаковые правки от одной
    основы дают одно и то же имя версии. Тихо перезаписать её означало бы
    потерять то, что уже лежит под этим именем.

    Это НАСТОЯЩИЙ повтор: та же правка от той же основы, и её результат уже
    лежит под этим именем. Отказ обязан говорить именно это — соседний тест
    про откат проверяет, что тем же словом не называется другой случай."""
    исходная = current_version(хранилище)
    первый = _добавить(хранилище, code="TST01")

    with pytest.raises(ChecklistError) as отказ:
        _добавить(хранилище, code="TST01", base=исходная)

    текст = str(отказ.value)
    assert первый.version in текст
    assert исходная in текст, "повтор не назван основой, от которой он повтор"
    assert "уже сделан" in текст


def test_откат_правки_повтором_не_называется(хранилище: Store) -> None:
    """T218 (#166): совпадает не запрос, а получившееся состояние.

    Пункт выключили и тут же включили обратно — методика вернулась к прежнему
    отпечатку, и имя версии совпало с той, что уже лежит в хранилище. Отказ
    «ровно эта правка уже сделана» тут неверен дважды: правка НЕ сделана
    (пункт остался выключенным), и совпавшая версия получилась не ею, а её
    основой. Агент управляющей компании прочитал бы это как «работа сделана».
    """
    первый = _добавить(хранилище, code="TST01")
    выключено = apply_change(
        хранилище,
        tenant=АРЕНДАТОР,
        tool="remove_checklist_item",
        command="remove",
        positional="TST01",
        options={"hard": None},
        today=СЕГОДНЯ,
    )
    assert выключено.version != первый.version

    with pytest.raises(ChecklistError) as отказ:
        apply_change(
            хранилище,
            tenant=АРЕНДАТОР,
            tool="restore_checklist_item",
            command="restore",
            positional="TST01",
            options={},
            today=СЕГОДНЯ,
        )

    текст = str(отказ.value)
    assert "уже сделан" not in текст, f"откат назван повтором правки: {текст!r}"
    assert первый.version in текст, "не названа версия, состоянием которой это стало"
    assert "publish_checklist_version" in текст, "не сказано, чем откатываются"


def test_откат_проходит_под_другим_именем_набора(хранилище: Store) -> None:
    """Обратная половина: отказ не выдумывает препятствия. Имя набора входит
    в идентификатор версии, поэтому под другим именем то же состояние
    записывается отдельной версией — и отказ на это указывает."""
    первый = _добавить(хранилище, code="TST01")
    apply_change(
        хранилище,
        tenant=АРЕНДАТОР,
        tool="remove_checklist_item",
        command="remove",
        positional="TST01",
        options={"hard": None},
        today=СЕГОДНЯ,
    )

    итог = apply_change(
        хранилище,
        tenant=АРЕНДАТОР,
        tool="restore_checklist_item",
        command="restore",
        positional="TST01",
        options={},
        version_name="imf2",
        today=СЕГОДНЯ,
    )

    assert итог.accepted is True
    assert итог.version != первый.version
    пункты = {пункт["id"]: пункт["kind"] for пункт in read_items(хранилище, version=итог.version)}
    assert пункты["TST01"] == "violation"


def test_журнал_на_версию_которой_нет_основой_не_становится(хранилище: Store) -> None:
    """Журнал — запись о событиях, а не опись каталога. Названная в нём версия
    может исчезнуть (человек убрал каталог руками), и правка обязана отступить
    к действующей, а не упасть."""
    _добавить(хранилище, code="TST01")
    последняя = read_journal(хранилище)[-1]["version"]
    import shutil as _shutil

    _shutil.rmtree(хранилище.root / VERSIONS_DIR / str(последняя))

    итог = _добавить(хранилище, code="TST02", version_name="imf")

    assert итог.base_version == current_version(хранилище)


def test_журнала_нет_совсем_это_пусто_а_не_отказ(tmp_path: Path, методика: Path) -> None:
    store = Store(root=tmp_path / "store", live=методика)

    assert read_journal(store) == []


def test_без_журнала_основой_становится_действующая_версия(хранилище: Store) -> None:
    """Журнал могут стереть, а хранилище от этого работать не перестаёт: без
    записей основой служит то, на что смотрит указатель."""
    действующая = current_version(хранилище)
    (хранилище.root / JOURNAL_FILE).unlink()

    итог = _добавить(хранилище, code="TST01")

    assert итог.base_version == действующая


def test_посторонний_файл_в_хранилище_версией_не_считается(хранилище: Store) -> None:
    current_version(хранилище)
    (хранилище.root / VERSIONS_DIR / "заметка.txt").write_text("привет", encoding="utf-8")

    assert all(версия.version != "заметка.txt" for версия in versions(хранилище))


def test_старая_версия_публикуется_обратно(tmp_path: Path, методика: Path) -> None:
    """Откат — это публикация прежней версии, и он обязан работать: иначе
    правка методики через агента необратима."""
    store = Store(root=tmp_path / "store", live=методика)
    исходная = current_version(store)
    итог = _добавить(store)
    рабочее = Store(root=store.root, live=store.root / "current")
    publish(рабочее, tenant=АРЕНДАТОР, version=итог.version)

    publish(рабочее, tenant=АРЕНДАТОР, version=исходная)

    assert current_version(рабочее) == исходная
    assert "TST01" not in (store.root / "current" / "checklist.csv").read_text(encoding="utf-8")
