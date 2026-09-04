"""T109: `manage.py` не стирает колонки методики, которых не знает движок.

`write_rows` перезаписывал `checklist.csv` фиксированным списком `FIELDS`, а
`zone-add`/`zone-remove` — таким же фиксированным списком колонок зон. Любая
колонка, заведённая управляющей компанией сверх этого списка, исчезала при
первой же правке методики через `manage.py`, без единого слова. Восстановить
её неоткуда: методика лежит вне git (D002).

Насколько это не теория: порядок обхода точки (T061) сделан отдельным файлом
`data/route.csv` именно потому, что колонкой в чек-листе он не выжил бы
(`docs/02-domain.md`, «Почему отдельным файлом»).

Правило, которое здесь закреплено: колонка управляющей компании не исчезает
молча. Где данные для неё есть — они переезжают в новый файл; где их взять
неоткуда (`import-xlsx` пересобирает чек-лист из чужой выгрузки целиком) —
команда отказывается и называет колонки поимённо.
"""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest
from conftest import ROOT, Run, run_engine

MANAGE = ROOT / "engine" / "manage.py"

# Колонки, которые знает движок. Дублируются здесь намеренно: тест обязан
# ловить и молчаливое расширение списка в самом движке.
FIELDS = [
    "id",
    "kind",
    "process_ru",
    "process_en",
    "question_ru",
    "question_en",
    "levels",
    "zones",
    "days",
]
ZONE_FIELDS = ["code", "name_ru", "name_en", "share_pct"]


def читать(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """Заголовок и строки файла методики — как они лежат на диске."""
    with path.open(encoding="utf-8-sig") as f:
        r = csv.DictReader(f)
        return list(r.fieldnames or []), list(r)


def дописать_колонку(path: Path, имя: str, значение: Callable[[dict[str, str]], str]) -> None:
    """Завести в файле методики колонку, о которой движок не знает."""
    fields, rows = читать(path)
    for r in rows:
        r[имя] = значение(r)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[*fields, имя])
        w.writeheader()
        w.writerows(rows)


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()  # noqa: S324 — сверка файла, не защита


@pytest.fixture
def manage(data_copy: Path, workdir: Path) -> Callable[..., Run]:
    """`manage.py` на копии методики. Боевой `data/` не трогается."""

    def call(*args: str) -> Run:
        return run_engine(MANAGE, *args, cwd=workdir, env_extra={"CHECKLIST_DIR": str(data_copy)})

    return call


@pytest.fixture
def чек_лист(data_copy: Path) -> Path:
    """Чек-лист с колонкой управляющей компании: порядок обхода по пункту."""
    p = data_copy / "checklist.csv"
    дописать_колонку(p, "route_order", lambda r: f"{len(r['id']) * 10}")
    return p


@pytest.fixture
def зоны(data_copy: Path) -> Path:
    """Зоны с колонкой управляющей компании: этаж, на котором зона находится."""
    p = data_copy / "zones.csv"
    дописать_колонку(p, "floor", lambda r: "1" if r["code"] != "staff" else "2")
    return p


# --- чек-лист ---------------------------------------------------------------


def test_чужая_колонка_переживает_правку_пункта(manage: Callable[..., Run], чек_лист: Path) -> None:
    """Главный случай задачи: правка одного поля не должна стоить колонки УК."""
    было = {r["id"]: r["route_order"] for r in читать(чек_лист)[1]}

    r = manage("edit", "CLN05", "--days", "7")
    assert r.code == 0, r.text

    fields, rows = читать(чек_лист)
    assert "route_order" in fields, f"колонка УК исчезла молча: {fields}"
    assert {r["id"]: r["route_order"] for r in rows} == было, "значения колонки УК потеряны"
    правленый = next(r for r in rows if r["id"] == "CLN05")
    assert правленый["days"] == "7", "правка не применилась"


def test_чужая_колонка_переживает_добавление_пункта(
    manage: Callable[..., Run], чек_лист: Path
) -> None:
    """Новой строке значения взять неоткуда — но у остальных оно обязано остаться."""
    было = {r["id"]: r["route_order"] for r in читать(чек_лист)[1]}

    r = manage(
        "add",
        "--id",
        "ZZZ01",
        "--process",
        "Проба",
        "--question-ru",
        "Пробный пункт",
        "--levels",
        "D1",
        "--zones",
        "*",
    )
    assert r.code == 0, r.text

    fields, rows = читать(чек_лист)
    assert "route_order" in fields, f"колонка УК исчезла молча: {fields}"
    новые = {r["id"]: r["route_order"] for r in rows}
    assert новые.pop("ZZZ01") == "", "новой строке приписано чужое значение"
    assert новые == было, "значения колонки УК потеряны"


def test_чужая_колонка_переживает_выключение_и_возврат_пункта(
    manage: Callable[..., Run], чек_лист: Path
) -> None:
    """`remove` и `restore` перезаписывают файл тем же путём — значит и они."""
    было = {r["id"]: r["route_order"] for r in читать(чек_лист)[1]}

    assert manage("remove", "CLN05").code == 0
    assert "route_order" in читать(чек_лист)[0], "колонка УК исчезла на remove"

    assert manage("restore", "CLN05").code == 0
    fields, rows = читать(чек_лист)
    assert "route_order" in fields, "колонка УК исчезла на restore"
    assert {r["id"]: r["route_order"] for r in rows} == было, "значения колонки УК потеряны"


def test_известные_колонки_идут_первыми(manage: Callable[..., Run], чек_лист: Path) -> None:
    """Формат файла не должен перетасовываться: чужие колонки идут в хвосте."""
    assert manage("edit", "CLN05", "--days", "7").code == 0

    fields, _ = читать(чек_лист)
    assert fields == [*FIELDS, "route_order"], f"порядок колонок изменился: {fields}"


# --- зоны -------------------------------------------------------------------


def test_чужая_колонка_зон_переживает_добавление_зоны(
    manage: Callable[..., Run], зоны: Path
) -> None:
    было = {r["code"]: r["floor"] for r in читать(зоны)[1]}

    # `--equal-shares` — не деталь: с T112 правка списка зон не решает за человека,
    # что делать с долями. Здесь проверяются колонки, поэтому берётся любой из двух
    # явных путей.
    r = manage(
        "zone-add",
        "--code",
        "terrace",
        "--name-ru",
        "Терраса",
        "--name-en",
        "Terrace",
        "--equal-shares",
    )
    assert r.code == 0, r.text

    fields, rows = читать(зоны)
    assert fields == [*ZONE_FIELDS, "floor"], f"колонка УК потеряна или переставлена: {fields}"
    новые = {r["code"]: r["floor"] for r in rows}
    assert новые.pop("terrace") == "", "новой зоне приписан чужой этаж"
    assert новые == было, "значения колонки УК потеряны"


def test_чужая_колонка_зон_переживает_удаление_зоны(manage: Callable[..., Run], зоны: Path) -> None:
    было = {r["code"]: r["floor"] for r in читать(зоны)[1] if r["code"] != "staff"}

    r = manage("zone-remove", "staff", "--equal-shares")
    assert r.code == 0, r.text

    fields, rows = читать(зоны)
    assert "floor" in fields, f"колонка УК исчезла молча: {fields}"
    assert {r["code"]: r["floor"] for r in rows} == было, "значения колонки УК потеряны"


def test_удаление_зоны_не_стирает_колонку_чек_листа(
    manage: Callable[..., Run], зоны: Path, чек_лист: Path
) -> None:
    """`zone-remove` переписывает и чек-лист — вычищая из пунктов удалённую зону."""
    было = {r["id"]: r["route_order"] for r in читать(чек_лист)[1]}

    assert manage("zone-remove", "staff", "--equal-shares").code == 0

    fields, rows = читать(чек_лист)
    assert "route_order" in fields, f"колонка УК исчезла молча: {fields}"
    assert {r["id"]: r["route_order"] for r in rows} == было, "значения колонки УК потеряны"


# --- импорт из xlsx ---------------------------------------------------------
#
# Единственная команда, которая пересобирает чек-лист целиком из чужой
# выгрузки: значений для колонки УК там нет и взяться им неоткуда. Поэтому
# здесь не сохранение, а отказ с именами колонок и явный флаг для того, кто
# согласен их потерять.


def шаблон_imf(path: Path) -> Path:
    """Минимальная выгрузка Template_CL — та форма, которую разбирает import-xlsx."""
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Template_CL"
    ws.append(["Process", "Question", "Days for fixing", "Hint", "Custom answer"])
    # Формулировка выдуманная (T146): боевая методика лежит вне git (D002), а
    # проверяется здесь не она, а сохранение чужой колонки при импорте.
    ws.append(["Порядок", "Учебный пункт про поверхность", 10, "D1: следы", "Нет D1"])
    ws.append(["Порядок", "Учебный пункт про поверхность", 10, "D1: следы", "Да"])
    wb.save(path)
    return path


def test_импорт_отказывается_стирать_чужую_колонку(
    manage: Callable[..., Run], чек_лист: Path, tmp_path: Path
) -> None:
    """Отказ обязан назвать колонку и оставить файл нетронутым."""
    до = md5(чек_лист)

    r = manage("import-xlsx", str(шаблон_imf(tmp_path / "imf.xlsx")))

    assert r.code != 0, f"импорт стёр колонку УК и промолчал: {r.text}"
    assert "route_order" in r.text, f"в отказе не названа колонка: {r.text}"
    assert md5(чек_лист) == до, "файл переписан несмотря на отказ"


def test_импорт_с_явным_флагом_проходит(
    manage: Callable[..., Run], чек_лист: Path, tmp_path: Path
) -> None:
    """Отказ обязан иметь выход: кто согласен потерять колонку — говорит это вслух."""
    r = manage("import-xlsx", str(шаблон_imf(tmp_path / "imf.xlsx")), "--drop-extra-columns")

    assert r.code == 0, r.text
    fields, rows = читать(чек_лист)
    assert fields == FIELDS, f"после явного сброса остались лишние колонки: {fields}"
    # Код выводится импортом из имени процесса: «Порядок» → PPP01.
    assert [r["id"] for r in rows] == ["PPP01"], f"импорт собрал не то: {rows}"


def test_импорт_без_чужих_колонок_работает_как_раньше(
    manage: Callable[..., Run], data_copy: Path, tmp_path: Path
) -> None:
    """Проверка на слишком строгий отказ: обычная методика импортируется без флагов."""
    r = manage("import-xlsx", str(шаблон_imf(tmp_path / "imf.xlsx")))

    assert r.code == 0, r.text
    assert читать(data_copy / "checklist.csv")[0] == FIELDS


# --- сборщик колонок --------------------------------------------------------
#
# Два источника колонок: шапка файла на диске и ключи самих строк. Через CLI
# видно только первый: в рабочей копии `manage.py` читает и пишет один и тот же
# файл. Второй срабатывает, когда файла-цели ещё нет (правка зон в форке, куда
# `zones.csv` не скопирован) — подстроить это через CLI, не трогая боевую
# методику, нечем, поэтому функция проверяется напрямую.


def загрузить_manage() -> ModuleType:
    """Загрузить `manage.py` в этот процесс — ради одной чистой функции.

    `sys.path` возвращается как был: при импорте `manage.py` кладёт `engine/` в
    начало пути (ему нужен соседний `audit`), а прогон у всех тестов общий —
    оставленный путь начал бы перекрывать чужие модули через файл-другой.
    """
    spec = importlib.util.spec_from_file_location("manage_под_тестом", MANAGE)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    было = list(sys.path)
    try:
        spec.loader.exec_module(m)
    finally:
        sys.path[:] = было
    return m


def test_колонки_подбираются_и_из_строк_когда_файла_нет(tmp_path: Path) -> None:
    """Файла-цели нет — единственный источник чужих колонок это сами строки."""
    m = загрузить_manage()

    assert m.foreign_columns(
        tmp_path / "нет-такого.csv", ["code"], [{"code": "facade", "floor": "1"}]
    ) == ["floor"]


def test_колонки_подбираются_из_шапки_когда_строк_не_осталось(tmp_path: Path) -> None:
    """Файл с одной шапкой: колонка есть, значений нет — терять её всё равно нельзя."""
    m = загрузить_manage()
    p = tmp_path / "zones.csv"
    p.write_text("code,floor\n", encoding="utf-8")

    assert m.foreign_columns(p, ["code"], []) == ["floor"]


def test_хвост_ломаной_строки_колонкой_не_считается(tmp_path: Path) -> None:
    """Значений в строке больше, чем колонок в шапке: имени у хвоста нет."""
    m = загрузить_manage()

    assert m.foreign_columns(tmp_path / "нет.csv", ["code"], [{"code": "a", None: ["хвост"]}]) == []
