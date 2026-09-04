"""T023: оценка приходит из движка и только из него.

Проценты, буква, разбивка по зонам — исключительно `audit.py score`, ставки
вычетов живут в методике (конституция проекта, принцип 2). Поэтому проверка
здесь не «цифры похожи на правду», а «цифры совпадают с тем, что печатает
движок, запущенный напрямую на тех же данных».

**Это тот самый файл, которому боевая методика нужна по существу** (T141).
Боевой якорь 97.5 %/97 % на `examples/` ценен ровно тем, что посчитан по
настоящим данным управляющей компании: разошёлся — это регрессия, о которой
говорит `CLAUDE.md`. Такие тесты идут через `live_data_env` и помечены
`requires_data` / `requires_examples`.

Тест про ставки боевых данных не требует и идёт по синтетической методике
`tests/methodology`: он проверяет не цифру методики, а то, что своей арифметики
в блоке нет. На боевой методике он краснел бы от чужой правки ставок, ничего не
сообщая о продукте.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from conftest import AUDIT, DATA, EXAMPLES, requires_data, requires_examples, run_engine

from src.domain import add_finding, get_state, score, start_inspection
from src.domain.errors import InspectionNotStarted

ПРОВЕРКИ = ["belgrade-1", "belgrade-2"]

#: Пары «пункт + зона» синтетической методики, каждая с единственным классом D1.
#: Разные пункты и разные зоны намеренно: движок отвергает дубль пары, а вычеты
#: по зонам не упираются в долю (`cap_zone_loss_at_share: false`).
ПЯТЬ_ЗАПИСЕЙ_D1 = [
    ("CLN05", "hot_kitchen"),
    ("CLN02", "dishwashing"),
    ("CLN12", "dining"),
    ("CLN09", "staff"),
    ("CLN24", "dry_storage"),
]


def подложить(state_dir: Path, chat_id: int, источник: Path) -> None:
    """Положить боевую проверку в папку чата — как будто её вёл бот."""
    chat = state_dir / f"chat_{chat_id}"
    chat.mkdir(parents=True)
    shutil.copy2(источник, chat / "inspection.json")


def прямой_запуск(name: str, *args: str) -> str:
    d = EXAMPLES / name
    r = run_engine(AUDIT, "score", *args, cwd=d, state=d / "inspection.json", data_dir=DATA)
    assert r.code == 0, r.text
    return r.out


@requires_data
@requires_examples
@pytest.mark.parametrize("name", ПРОВЕРКИ)
def test_оценка_совпадает_с_прямым_запуском_движка(name: str, live_data_env: Path) -> None:
    эталон = json.loads(прямой_запуск(name, "--json"))
    подложить(live_data_env, 1, EXAMPLES / name / "inspection.json")

    итог = score(1)

    assert итог.pct == эталон["pct"], "процент разошёлся с движком"
    assert итог.grade == эталон["grade"], "буква оценки разошлась с движком"
    assert итог.counts == эталон["counts"], "разбивка по классам разошлась с движком"
    assert итог.deductions == эталон["deductions"], "сумма вычетов разошлась с движком"
    assert итог.label("ru") == эталон["grade_label_ru"]
    assert итог.label("en") == эталон["grade_label_en"]


@requires_data
@requires_examples
@pytest.mark.parametrize("name", ПРОВЕРКИ)
def test_разбивка_по_зонам_совпадает_с_прямым_запуском(name: str, live_data_env: Path) -> None:
    эталон = json.loads(прямой_запуск(name, "--json"))["zones"]
    подложить(live_data_env, 1, EXAMPLES / name / "inspection.json")

    по_зонам = score(1).by_zone

    assert set(по_зонам) == set(эталон), "набор зон в разбивке разошёлся"
    for код, зона in эталон.items():
        наша = по_зонам[код]
        уровни = {k: v for k, v in зона.items() if k.startswith("D")}
        assert наша.counts == уровни, f"{код}: счёт записей по классам"
        assert наша.loss == зона["loss"], f"{код}: потеряно из доли"
        assert наша.left == зона["score"], f"{код}: осталось от доли"
        assert наша.share == зона["share"], f"{код}: доля зоны"
        assert наша.zeroed == зона["zeroed"], f"{код}: обнуление зоны по D3"


@requires_data
@requires_examples
@pytest.mark.parametrize("name", ПРОВЕРКИ)
def test_оценка_совпадает_с_тем_что_движок_печатает_человеку(
    name: str, live_data_env: Path
) -> None:
    """Аудитор в чате и партнёр в отчёте должны видеть одни и те же цифры."""
    первая_строка = прямой_запуск(name).splitlines()[0]
    подложить(live_data_env, 1, EXAMPLES / name / "inspection.json")

    итог = score(1)

    assert первая_строка == f"Итог: {итог.pct:g}%  оценка {итог.grade} — {итог.label('ru')}"


@requires_data
@requires_examples
def test_боевой_якорь_не_сдвинулся(live_data_env: Path) -> None:
    """Тот же якорь, что в конституции: расхождение — регрессия, а не уточнение."""
    подложить(live_data_env, 1, EXAMPLES / "belgrade-1" / "inspection.json")
    подложить(live_data_env, 2, EXAMPLES / "belgrade-2" / "inspection.json")

    первая, вторая = score(1), score(2)

    assert (первая.pct, первая.grade, первая.counts["D1"]) == (97.5, "A", 5)
    assert (вторая.pct, вторая.grade, вторая.counts["D1"]) == (97.0, "A", 6)
    assert первая.counts["D2"] == первая.counts["D3"] == 0


@requires_data
@requires_examples
def test_информационные_записи_видны_но_на_процент_не_влияют(live_data_env: Path) -> None:
    """Замеры и настройки живут среди записей с классом D0 (`docs/02-domain.md`)."""
    подложить(live_data_env, 1, EXAMPLES / "belgrade-1" / "inspection.json")

    итог = score(1)

    assert итог.counts["D0"] == 2, "информационные записи пропали из разбивки"
    assert итог.pct == 97.5, "информационная запись вычла процент — вычета у D0 быть не должно"


def test_ставки_вычетов_берутся_из_методики_а_не_из_кода(
    data_copy: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Правка ставки обязана менять результат: своей арифметики в блоке нет.

    Методика синтетическая, и её же правит тест: проверяется не цифра методики,
    а связь «ставка в данных → процент из движка».
    """
    scoring = data_copy / "scoring.json"
    ставки = json.loads(scoring.read_text(encoding="utf-8"))
    было = float(ставки["penalty"]["D1"])
    ставки["penalty"]["D1"] = было * 2
    scoring.write_text(json.dumps(ставки, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setenv("AUDIT_DATA_DIR", str(data_copy))
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    monkeypatch.chdir(tmp_path)
    start_inspection(1, unit="Проба", kind="planned", report_lang="ru")
    for код, зона in ПЯТЬ_ЗАПИСЕЙ_D1:
        add_finding(1, код, "D1", зона, "формулировка теста")

    ожидание = 100.0 - len(ПЯТЬ_ЗАПИСЕЙ_D1) * было * 2

    assert score(1).pct == ожидание, (
        f"пять D1 по {было * 2:g} п.п. должны были дать {ожидание:g}% — значит, процент "
        f"считается не по ставкам методики"
    )


def test_оценка_без_начатой_проверки_это_отказ(domain_env: Path) -> None:
    with pytest.raises(InspectionNotStarted):
        score(777)
    assert get_state(777) is None
