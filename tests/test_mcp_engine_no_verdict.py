"""T187: движок, не давший ответа, — это отказ окружения, а не отклонение методики.

Задача пришла так: «тесты хранилища методики падают под нагрузкой машины —
правка отклоняется вместо создания новой версии, 2 падения из 8 на занятой
машине против 0 из 12 на свободной». Разбор этого стоил владельцу трёх часов,
и вот почему он столько стоил.

Хранилище считало ЛЮБОЙ ненулевой код возврата движка отказом методики. Но
ненулевым код бывает и тогда, когда движок до разбора методики не дошёл вовсе:
процесс убит (нехватка памяти на занятой машине — обычное дело), не уложился в
срок, вышел молча. Все три случая приходили вызывающему как «движок вашу
методику не принял» и ложились в журнал строкой `refused` — то есть машинная
беда выглядела ровно как дефект методики, и отличить одно от другого было
нечем.

Здесь проверяется различение: вердикт движка отдаётся вердиктом, а его
отсутствие — отказом окружения, названным своим именем и записанным в журнал
отдельным исходом.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from mcp_checklist_harness import build_methodology

from src.mcp import checklist as store_api
from src.mcp.checklist import Store, apply_change, read_journal, versions
from src.mcp.errors import ChecklistError

АРЕНДАТОР = "укашка"
СЕГОДНЯ = date(2026, 9, 3)

#: Подделки движка. Каждая — «ответа не будет», но своим способом: убит,
#: завис, вышел молча. Настоящий движок так не делает никогда: каждый свой
#: отказ он объясняет словами, и слова эти уходят вызывающему.
УБИТЫЙ = "import os, signal\nos.kill(os.getpid(), signal.SIGKILL)\n"
ЗАВИСШИЙ = "import time\ntime.sleep(30)\n"
МОЛЧАЛИВЫЙ = "import sys\nsys.exit(3)\n"
#: Убитый на полуслове: успел напечатать и снят. Самый опасный из трёх — у
#: него ЕСТЬ вывод, и по одному лишь коду возврата он неотличим от разбора,
#: кончившегося отказом: обрывок вывода уехал бы вызывающему как «движок
#: сказал вот что».
УБИТЫЙ_НА_ПОЛУСЛОВЕ = (
    "import os, signal, sys\n"
    "print('вопросов: 2, зон: 2')\n"
    "sys.stdout.flush()\n"
    "os.kill(os.getpid(), signal.SIGKILL)\n"
)


@pytest.fixture
def хранилище(tmp_path: Path) -> Store:
    return Store(root=tmp_path / "хранилище", live=build_methodology(tmp_path / "живая"))


def _подделка(tmp_path: Path, тело: str) -> Path:
    путь = tmp_path / "подделка_движка.py"
    путь.write_text(тело, encoding="utf-8")
    return путь


def _добавить(хранилище: Store) -> object:
    return apply_change(
        хранилище,
        tenant=АРЕНДАТОР,
        tool="add_checklist_item",
        command="add",
        options={
            "id": "TST01",
            "process": "Проба",
            "question-ru": "Проба пера",
            "levels": "D1",
            "zones": "fridge",
            "days": 5,
            "criteria": "D1: проба",
        },
        version_name="imf",
        today=СЕГОДНЯ,
    )


@pytest.mark.parametrize(
    ("тело", "про_что"),
    [
        (УБИТЫЙ, "сигнал"),
        (УБИТЫЙ_НА_ПОЛУСЛОВЕ, "сигнал"),
        (МОЛЧАЛИВЫЙ, "ни слова"),
    ],
)
def test_движок_без_ответа_это_не_отклонение_методики(
    хранилище: Store,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    тело: str,
    про_что: str,
) -> None:
    """Главное утверждение файла: отсутствие вердикта не выдаётся за вердикт."""
    monkeypatch.setattr(store_api, "MANAGE_SCRIPT", _подделка(tmp_path, тело))

    with pytest.raises(ChecklistError) as отказ:
        _добавить(хранилище)

    текст = str(отказ.value).lower()
    assert про_что in текст, текст
    assert "не дал ответа" in текст, текст


def test_срок_вышел_а_методику_никто_не_рассматривал(
    хранилище: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Занятая машина растягивает подпроцесс, и однажды он не уложится в срок.
    Это ровно тот исход, который выглядел отклонением правки."""
    monkeypatch.setattr(store_api, "MANAGE_SCRIPT", _подделка(tmp_path, ЗАВИСШИЙ))
    monkeypatch.setattr(store_api, "ENGINE_TIMEOUT_SEC", 0.5)

    with pytest.raises(ChecklistError) as отказ:
        _добавить(хранилище)

    assert "не дал ответа" in str(отказ.value).lower()


def test_отказ_окружения_записан_в_журнал_отдельным_исходом(
    хранилище: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`refused` в журнале означает «движок методику не принял». Запись о
    машинной беде тем же словом читалась бы через год как история правок,
    которых движок не принимал, — и по ней чинили бы методику."""
    monkeypatch.setattr(store_api, "MANAGE_SCRIPT", _подделка(tmp_path, УБИТЫЙ))

    with pytest.raises(ChecklistError):
        _добавить(хранилище)

    последнее = read_journal(хранилище)[-1]
    assert последнее["outcome"] == "failed"
    assert последнее["version"] is None
    assert последнее["tool"] == "add_checklist_item"


def test_версии_от_несостоявшейся_правки_не_остаётся(
    хранилище: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    было = {версия.version for версия in versions(хранилище)}
    monkeypatch.setattr(store_api, "MANAGE_SCRIPT", _подделка(tmp_path, УБИТЫЙ))

    with pytest.raises(ChecklistError):
        _добавить(хранилище)

    assert {версия.version for версия in versions(хранилище)} == было


def test_в_отказе_окружения_нет_путей_с_диска(
    хранилище: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T120: ответ уходит в модель, то есть за пределы машины. Отказ по сроку
    особенно опасен — `subprocess.TimeoutExpired` печатает всю командную
    строку целиком, вместе с путём к интерпретатору и к скрипту движка."""
    monkeypatch.setattr(store_api, "MANAGE_SCRIPT", _подделка(tmp_path, ЗАВИСШИЙ))
    monkeypatch.setattr(store_api, "ENGINE_TIMEOUT_SEC", 0.5)

    with pytest.raises(ChecklistError) as отказ:
        _добавить(хранилище)

    текст = str(отказ.value)
    assert str(tmp_path) not in текст
    assert "/Users/" not in текст


def test_настоящий_отказ_движка_остаётся_отказом_методики(хранилище: Store) -> None:
    """Различение работает в обе стороны: методику, которую движок разобрал и
    не принял, по-прежнему отклоняет он, и словами говорит он же."""
    итог = apply_change(
        хранилище,
        tenant=АРЕНДАТОР,
        tool="add_checklist_item",
        command="add",
        options={
            "id": "TST02",
            "process": "Проба",
            "question-ru": "Проба пера",
            "levels": "D1",
            "zones": "нет-такой-зоны",
            "days": 5,
            "criteria": "D1: проба",
        },
        version_name="imf",
        today=СЕГОДНЯ,
    )

    assert итог.accepted is False
    assert итог.refusal
    assert read_journal(хранилище)[-1]["outcome"] == "refused"
