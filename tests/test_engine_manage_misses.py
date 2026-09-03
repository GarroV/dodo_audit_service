"""Промах по коду не имеет права выглядеть успехом.

`cmd_remove` и `cmd_edit` давно отказывают на несуществующем пункте или
неизвестной зоне — эта проверка в них есть. В `cmd_zone_remove`, `cmd_restore`
и в проверке `--zones` внутри `cmd_edit` её просто забыли: не решение, а
пропуск, и вот чем он дорог на этом проекте.

`manage.py zone-remove nosuchzone` печатал «зона nosuchzone убрана…» и
возвращал 0 — при этом переписывал `zones.csv` и `checklist.csv` под опечатку,
которой не существует. `manage.py restore NOSUCH01` печатал «включён
NOSUCH01» и тоже возвращал 0 — работу, которую он не сделал. `manage.py edit
CLN05 --zones "opechatka,hot_kitchen"` писал в чек-лист код `opechatka`,
которого нет ни в одной зоне: `cmd_add` эту же проверку делает, `cmd_edit` —
нет.

Опасность не в самом отказе — отказать легко. Опасность в том, что человек за
терминалом читает «убрана» / «включён» / «обновлён» и код возврата 0 и
двигается дальше, уверенный, что методика поправлена. Автоматизация (бот,
скрипт CI) вообще не смотрит на текст — только на код возврата. Оба канала
здесь врали: опечатка в коде зоны или id тихо портила рабочую копию чек-листа
партнёра вместо того, чтобы остановить работу с понятным сообщением.
"""

from __future__ import annotations

import csv
import hashlib
from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import ROOT, Run, requires_data, run_engine

pytestmark = requires_data

MANAGE = ROOT / "engine" / "manage.py"


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()  # noqa: S324 — сверка файла, не защита


@pytest.fixture
def manage(data_copy: Path, workdir: Path) -> Callable[..., Run]:
    """`manage.py` на копии методики. Боевой `data/` не трогается."""

    def call(*args: str) -> Run:
        return run_engine(MANAGE, *args, cwd=workdir, env_extra={"CHECKLIST_DIR": str(data_copy)})

    return call


@pytest.fixture
def зоны(data_copy: Path) -> Path:
    return data_copy / "zones.csv"


@pytest.fixture
def чек_лист(data_copy: Path) -> Path:
    return data_copy / "checklist.csv"


def читать_зоны(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def читать_чек_лист(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


# --- zone-remove несуществующей зоны — отказ, файлы не тронуты ---------------


def test_zone_remove_несуществующей_зоны_это_отказ(
    manage: Callable[..., Run], зоны: Path, чек_лист: Path, workdir: Path
) -> None:
    """Опечатка в коде зоны не должна переписывать zones.csv и checklist.csv."""
    было_зоны, было_чек_лист = md5(зоны), md5(чек_лист)

    r = manage("zone-remove", "nosuchzone", "--keep-shares")

    assert r.code != 0, f"опечатка в коде зоны вернула 0: {r.text!r}"
    assert md5(зоны) == было_зоны, "zones.csv переписан на несуществующей зоне"
    assert md5(чек_лист) == было_чек_лист, "checklist.csv переписан на несуществующей зоне"
    assert "nosuchzone" in r.text, f"в отказе не назван код зоны: {r.text!r}"
    assert not (workdir / "checklist_data").exists(), (
        "форк методики создан в рабочем каталоге, хотя команда отказала"
    )


def test_zone_remove_существующей_зоны_по_прежнему_работает(
    manage: Callable[..., Run], зоны: Path
) -> None:
    """Проверка на слишком строгий отказ: штатный путь остаётся штатным."""
    было = читать_зоны(зоны)

    r = manage("zone-remove", "staff", "--equal-shares")

    assert r.code == 0, r.text
    стало = читать_зоны(зоны)
    assert len(стало) == len(было) - 1, f"зон стало не на одну меньше: {стало}"
    assert "staff" not in {z["code"] for z in стало}, "убранная зона осталась в файле"


# --- restore несуществующего пункта — отказ, checklist.csv не тронут ---------


def test_restore_несуществующего_пункта_это_отказ(
    manage: Callable[..., Run], чек_лист: Path
) -> None:
    """Опечатка в id не должна переписывать checklist.csv и печатать «включён»."""
    было = md5(чек_лист)

    r = manage("restore", "NOSUCH01")

    assert r.code != 0, f"опечатка в id пункта вернула 0: {r.text!r}"
    assert md5(чек_лист) == было, "checklist.csv переписан на несуществующем пункте"
    assert "NOSUCH01" in r.text, f"в отказе не назван код пункта: {r.text!r}"


def test_restore_существующего_выключенного_пункта_включает_его(
    manage: Callable[..., Run], чек_лист: Path
) -> None:
    """Штатный путь: выключить, потом вернуть — пункт снова участвует в оценке."""
    assert manage("remove", "CLN05").code == 0
    выключенный = next(r for r in читать_чек_лист(чек_лист) if r["id"] == "CLN05")
    assert выключенный["kind"] == "off", выключенный

    r = manage("restore", "CLN05")

    assert r.code == 0, r.text
    включённый = next(r for r in читать_чек_лист(чек_лист) if r["id"] == "CLN05")
    assert включённый["kind"] == "violation", включённый


# --- edit --zones с неизвестным кодом — отказ, checklist.csv не тронут -------


def test_edit_zones_с_неизвестным_кодом_это_отказ(
    manage: Callable[..., Run], чек_лист: Path
) -> None:
    """`cmd_add` эту проверку делает, `cmd_edit` — нет: opechatka не должна попасть в файл."""
    было = md5(чек_лист)

    r = manage("edit", "CLN05", "--zones", "opechatka,hot_kitchen")

    assert r.code != 0, f"неизвестная зона в edit --zones вернула 0: {r.text!r}"
    assert md5(чек_лист) == было, "checklist.csv переписан с неизвестной зоной"
    assert "opechatka" in r.text, f"в отказе не названа плохая зона: {r.text!r}"


def test_edit_zones_звёздочка_законна(manage: Callable[..., Run], чек_лист: Path) -> None:
    """`"*"` — все зоны, а не код зоны, проверке не подлежит."""
    r = manage("edit", "CLN05", "--zones", "*")

    assert r.code == 0, r.text
    правленый = next(r for r in читать_чек_лист(чек_лист) if r["id"] == "CLN05")
    assert правленый["zones"] == "*", правленый


def test_edit_zones_с_известными_кодами_проходит_и_записывается(
    manage: Callable[..., Run], чек_лист: Path
) -> None:
    r = manage("edit", "CLN05", "--zones", "fridge,freezer")

    assert r.code == 0, r.text
    правленый = next(r for r in читать_чек_лист(чек_лист) if r["id"] == "CLN05")
    assert правленый["zones"] == "fridge,freezer", правленый


def test_edit_без_zones_работает_как_раньше(manage: Callable[..., Run], чек_лист: Path) -> None:
    """Флаг не передан — проверять коды зон нечего, старое поведение цело."""
    r = manage("edit", "CLN05", "--days", "7")

    assert r.code == 0, r.text
    правленый = next(r for r in читать_чек_лист(чек_лист) if r["id"] == "CLN05")
    assert правленый["days"] == "7", правленый


def test_add_с_неизвестной_зоной_по_прежнему_отказ(
    manage: Callable[..., Run], чек_лист: Path
) -> None:
    """Проверка зон стала общей для `add` и `edit` — за `add` теперь тоже есть свидетель.

    До T112 её код жил внутри `cmd_add` и тестом не был закрыт: вынос в общую
    функцию мог тихо изменить поведение `add`, и заметить это было бы негде.
    """
    было = md5(чек_лист)

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
        "opechatka",
    )

    assert r.code != 0, f"пункт заведён с несуществующей зоной: {r.text!r}"
    assert md5(чек_лист) == было, "checklist.csv переписан с неизвестной зоной"
    assert "opechatka" in r.text, f"в отказе не названа плохая зона: {r.text!r}"
