"""T106: битая методика — отказ вместо тихой нормализации.

`load_zones()` и `load_checklist()` молча чинили три вида битых данных вместо
того, чтобы отказать:

- нечисловая или пустая доля зоны (`share_pct`) превращалась в 0.0 — зона
  теряла вес, а дальше это упиралось в уже существующую проверку суммы
  (задача T103), но та называла неверную сумму, а не пустую клетку;
- нечисловой срок устранения (`days`) превращался в 0, а `report.py` печатает
  при нулевом сроке «немедленно» — партнёру уходило предписание устранить
  рядовое нарушение сегодня же;
- дубль `id` в чек-листе пропускался с предупреждением в `sys.stderr`, которое
  никто не читает, и в отчёт молча попадал не тот вопрос (пункты связываются
  кодами, а не формулировками).

Здесь проверяется не «движок не упал», а то, что каждый из трёх случаев
даёт отказ с причиной, которую можно исправить не гадая, и что здоровая
боевая методика по-прежнему проходит как ни в чём не бывало.
"""

from __future__ import annotations

import csv
from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import Run


def испортить_долю_зоны(data_dir: Path, code: str, значение: str) -> None:
    """Записать в конкретную зону сырое (возможно, битое) значение share_pct.

    Значение пишется как есть, без приведения к float, — так добираются и
    пустая клетка, и нечисловой текст. Правится копия методики (`data_copy`),
    боевой `data/zones.csv` не трогается никогда.
    """
    path = data_dir / "zones.csv"
    with path.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
        fields = list(rows[0].keys())
    for r in rows:
        if r["code"] == code:
            r["share_pct"] = значение
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def испортить_срок(data_dir: Path, qid: str, значение: str) -> int:
    """Записать в days конкретного пункта сырое значение, вернуть номер строки.

    Строка 1 — заголовок, значит первая строка данных — 2 (как в
    `load_checklist`, который нумерует `enumerate(rows, start=2)`).
    """
    path = data_dir / "checklist.csv"
    with path.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
        fields = list(rows[0].keys())
    line = None
    for i, r in enumerate(rows, start=2):
        if r["id"] == qid:
            r["days"] = значение
            line = i
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    assert line is not None, f"пункт {qid} не найден в checklist.csv"
    return line


def задвоить_id(data_dir: Path, qid: str) -> tuple[int, int]:
    """Дописать в конец файла копию строки с указанным id.

    Возвращает номера строк первого и второго вхождения (заголовок — строка 1,
    первая строка данных — 2).
    """
    path = data_dir / "checklist.csv"
    with path.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
        fields = list(rows[0].keys())
    first = next(i for i, r in enumerate(rows, start=2) if r["id"] == qid)
    оригинал = next(r for r in rows if r["id"] == qid)
    rows.append(dict(оригинал))
    second = len(rows) + 1  # заголовок (1) + все строки данных, включая дописанную
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    return first, second


@pytest.fixture
def методика(data_copy: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Копия методики, подставленная движку через `CHECKLIST_DIR`."""
    monkeypatch.setenv("CHECKLIST_DIR", str(data_copy))
    return data_copy


def test_нечисловая_доля_зоны_роняет_расчёт(started: Callable[..., Run], методика: Path) -> None:
    испортить_долю_зоны(методика, "facade", "десять")

    r = started("score")

    assert r.code != 0, "методика с нечисловой долей зоны посчитана как ни в чём не бывало"
    assert "facade" in r.text, f"в отказе нет кода зоны: {r.text!r}"
    assert "десять" in r.text, f"в отказе нет встреченного значения: {r.text!r}"


def test_пустая_доля_зоны_роняет_расчёт(started: Callable[..., Run], методика: Path) -> None:
    испортить_долю_зоны(методика, "facade", "")

    r = started("score")

    assert r.code != 0, "методика с пустой долей зоны посчитана как ни в чём не бывало"
    assert "не заполнена" in r.text, f"в отказе не сказано, что клетка пуста: {r.text!r}"
    assert "facade" in r.text, f"в отказе нет кода зоны: {r.text!r}"


def test_отказы_по_долям_называют_файл(started: Callable[..., Run], методика: Path) -> None:
    """Каталогов методики два (боевой и форк) — без пути человек правит не тот файл."""
    путь = str(методика / "zones.csv")

    испортить_долю_зоны(методика, "facade", "десять")
    r_число = started("score")
    assert путь in r_число.text, f"в отказе про нечисловую долю нет пути: {r_число.text!r}"

    испортить_долю_зоны(методика, "facade", "")
    r_пусто = started("score")
    assert путь in r_пусто.text, f"в отказе про пустую долю нет пути: {r_пусто.text!r}"


def test_нечисловой_срок_роняет_расчёт(started: Callable[..., Run], методика: Path) -> None:
    строка = испортить_срок(методика, "CLN05", "три")

    r = started("score")

    assert r.code != 0, "методика с нечисловым сроком устранения посчитана как ни в чём не бывало"
    assert "CLN05" in r.text, f"в отказе нет кода пункта: {r.text!r}"
    assert "три" in r.text, f"в отказе нет встреченного значения: {r.text!r}"
    assert str(строка) in r.text, f"в отказе нет номера строки {строка}: {r.text!r}"


def test_пустой_срок_не_роняет_расчёт(started: Callable[..., Run], методика: Path) -> None:
    """Пустая клетка days — осознанное решение терпеть: `manage.py add` без --days
    пишет в CSV именно пустоту, и запрет сломал бы штатное заведение пункта."""
    испортить_срок(методика, "CLN05", "")

    r = started("score")

    assert r.code == 0, f"пустой срок устранения не обязан ронять расчёт: {r.text}"


def test_дубль_id_роняет_расчёт(started: Callable[..., Run], методика: Path) -> None:
    первая, вторая = задвоить_id(методика, "PRD01")

    r = started("score")

    assert r.code != 0, "методика с дублем id посчитана как ни в чём не бывало"
    assert "PRD01" in r.text, f"в отказе нет кода пункта: {r.text!r}"
    assert str(первая) in r.text, f"в отказе нет номера первой строки {первая}: {r.text!r}"
    assert str(вторая) in r.text, f"в отказе нет номера второй строки {вторая}: {r.text!r}"


def test_дубль_id_роняет_и_сборку_письма(
    started: Callable[..., Run], report: Callable[..., Run], методика: Path
) -> None:
    """Письмо партнёру идёт наружу — оно обязано спотыкаться о ту же проверку, что и `score`."""
    задвоить_id(методика, "PRD01")

    r = report("letter")

    assert r.code != 0, "письмо партнёру собрано по методике с дублем id"
    assert "PRD01" in r.text, f"в отказе нет кода пункта: {r.text!r}"
    assert "checklist.csv" in r.text, f"в отказе нет файла с чек-листом: {r.text!r}"


def test_здоровая_методика_проходит(started: Callable[..., Run], методика: Path) -> None:
    """Боевая методика без порчи обязана считаться как обычно — проверка не должна
    быть настолько строгой, что заворачивает исправные данные."""
    r = started("score")

    assert r.code == 0, f"здоровая боевая методика не должна ронять расчёт: {r.text}"
