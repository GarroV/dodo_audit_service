"""T113: тесты замера доли однозначных срабатываний быстрого пути.

Замер (`tools/fastpath_measure.py`) — критерий пользы карты слов
`data/photo-cues.md`: пополнение карты бессмысленно, если быстрый путь после
него не срабатывает чаще или начинает срабатывать неверно. Здесь проверяются
три вещи: сам подсчёт (`measure`) на синтетике, где заранее известны и верное
срабатывание, и отказ; коды возврата CLI (1 — есть неверное срабатывание,
2 — боевых данных нет); и то, что `load_records` действительно поднимает
боевые записи и живой замер на них остаётся зелёным — это регрессионный якорь.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import ROOT, requires_data

from src.recognize.fastpath import NO_COLUMN
from tools import fastpath_measure as fpm
from tools.fastpath_measure import Outcome, Record

pytestmark = requires_data


def test_измерение_на_синтетике_даёт_верное_срабатывание_и_отказ(domain_env: Path) -> None:
    """«Печь: под лентой нагар» произносит строку карты целиком — срабатывание.

    «Панель печи: температура 277 °C» карту не покрывает — законный отказ,
    а не угадывание.
    """
    records = (
        Record(code="CLN05", zone="hot_kitchen", note="Печь: под лентой нагар", source="synthetic"),
        Record(
            code="INF09",
            zone="hot_kitchen",
            note="Панель печи: температура 277 °C",
            source="synthetic",
        ),
    )

    fired, rejected = fpm.measure(records)

    assert fired.fired == "CLN05"
    assert fired.correct is True
    assert fired.reason == ""
    assert rejected.fired is None
    assert rejected.correct is None
    assert rejected.reason == NO_COLUMN


def test_неверное_срабатывание_даёт_код_возврата_1(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Неверное срабатывание опаснее отсутствия: аудитор подтверждает пункт нажатием."""
    record = Record(
        code="TEH05", zone="hot_kitchen", note="Печь: под лентой нагар", source="synthetic"
    )
    outcome = Outcome(record=record, fired="CLN05", reason="")
    assert outcome.correct is False

    monkeypatch.setattr(fpm, "load_records", lambda _root: (record,))
    monkeypatch.setattr(fpm, "measure", lambda _records: (outcome,))

    rc = fpm.main(["--root", str(tmp_path)])

    assert rc == 1


def test_без_боевых_данных_код_возврата_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Пустой корень — законный итог (D002: примеры вне git), а не поломка инструмента."""
    rc = fpm.main(["--root", str(tmp_path)])

    out = capsys.readouterr().out
    assert rc == 2
    assert "нечего" in out
    assert "не поломка" in out


def test_load_records_поднимает_боевые_записи() -> None:
    records = fpm.load_records(ROOT)

    assert len(records) == 17
    for record in records:
        assert record.code
        assert record.zone
        assert record.note


def test_живой_замер_на_боевых_данных_зелёный(domain_env: Path) -> None:
    """Ни одного неверного срабатывания на боевых данных — регрессионный якорь T113."""
    rc = fpm.main(["--root", str(ROOT)])

    assert rc == 0
