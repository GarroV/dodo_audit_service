"""T113, T125: тесты замера доли срабатываний быстрого пути.

Замер (`tools/fastpath_measure.py`) — критерий пользы карты слов
`data/photo-cues.md`: пополнение карты бессмысленно, если быстрый путь после
него не срабатывает чаще или начинает срабатывать неверно.

Главное, что здесь проверяется, — что замер меряет ТО, ЧТО ПРОИСХОДИТ НА
ТОЧКЕ. До T125 (задача #100) он звал `fast_path` с зоной из эталонной записи,
то есть из уже известного правильного ответа, и его 18% к живому боту
отношения не имели. Бот берёт зону из слов комментария (`src/bot/zones.py`),
а память о прошлой записи (D048) — только догадка на случай, когда о зоне не
сказано ничего.

Остальное: подсчёт (`measure`) на синтетике с заранее известным исходом; коды
возврата CLI (1 — есть неверное срабатывание, 2 — боевых данных нет); шапка
отчёта с версией карты (D066); и то, что живой замер на боевых записях
остаётся зелёным, — это регрессионный якорь.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import ROOT, requires_data

from src.recognize.fastpath import NO_COLUMN, NO_ZONE, WRONG_ZONE
from tools import fastpath_measure as fpm
from tools.fastpath_measure import FROM_MEMORY, FROM_NOWHERE, FROM_WORDS, Mode, Record

pytestmark = requires_data

#: «Печь» — строка карты, произнесённая целиком, «нагар» выбирает колонку
#: «Грязь». Зоны в этих словах нет: их у бота придётся брать из памяти.
OVEN = "Печь: под лентой нагар"
#: Те же слова, но зону аудитор назвал сам.
OVEN_WITH_ZONE = "Горячий цех: печь, под лентой нагар"
#: Зона названа («в зале»), строка карты произнесена, колонка выбрана «крошками».
FURNITURE = "Мебель в зале: крошки на столах"


def test_замер_не_подставляет_эталонную_зону_которой_у_бота_нет(domain_env: Path) -> None:
    """T125: «Печь: под лентой нагар» зоны не называет — у бота зоны нет, и пункта тоже.

    Дефект #100: замер звал `fast_path` с зоной ИЗ ЭТАЛОННОЙ ЗАПИСИ, то есть из
    уже известного правильного ответа. Живой бот берёт зону только из слов
    аудитора (`src/bot/zones.py`) и памяти о прошлой записи; на первой записи
    проверки памяти ещё нет — значит, зоны нет вовсе и быстрый путь обязан
    отказать, а не показывать пункт.
    """
    records = (Record(code="CLN05", zone="hot_kitchen", note=OVEN, source="synthetic"),)

    (outcome,) = fpm.measure(records)

    assert outcome.fired is None
    assert outcome.reason == NO_ZONE
    assert outcome.hint.zone is None
    assert outcome.hint.source == FROM_NOWHERE


def test_память_проверки_может_подставить_чужую_зону(domain_env: Path) -> None:
    """Память — догадка (D048), и догадка бывает неверной; замер обязан это показывать.

    Первая запись сделана в зале, вторая — про печь, но зону аудитор не назвал.
    Бот подставит зал, и `CLN05` к залу не применим: законный отказ. Замер с
    эталонной зоной этого не видел вовсе — он подставлял горячий цех и
    рапортовал срабатывание, которого на точке не будет.
    """
    records = (
        Record(code="CLN13", zone="dining", note=FURNITURE, source="synthetic"),
        Record(code="CLN05", zone="hot_kitchen", note=OVEN, source="synthetic"),
    )

    first, second = fpm.measure(records)

    assert first.fired == "CLN13"
    assert first.hint.source == FROM_WORDS
    assert second.fired is None
    assert second.reason == WRONG_ZONE
    assert second.hint.zone == "dining"
    assert second.hint.source == FROM_MEMORY


def test_слова_комментария_сильнее_памяти(domain_env: Path) -> None:
    """Порядок бота: `spoken or memory`, а не наоборот (T124)."""
    records = (
        Record(code="CLN13", zone="dining", note=FURNITURE, source="synthetic"),
        Record(code="CLN05", zone="hot_kitchen", note=OVEN_WITH_ZONE, source="synthetic"),
    )

    _, second = fpm.measure(records)

    assert second.hint.zone == "hot_kitchen"
    assert second.hint.source == FROM_WORDS
    assert second.fired == "CLN05"
    assert second.correct is True


def test_память_не_переходит_из_одной_проверки_в_другую(domain_env: Path) -> None:
    """У каждой проверки свой чат и свои заметки: зона предыдущей сюда не течёт."""
    records = (
        Record(code="CLN13", zone="dining", note=FURNITURE, source="belgrade-1"),
        Record(code="CLN05", zone="hot_kitchen", note=OVEN, source="belgrade-2"),
    )

    _, second = fpm.measure(records)

    assert second.hint.source == FROM_NOWHERE
    assert second.reason == NO_ZONE


def test_эталонная_зона_считается_отдельно_как_верхняя_граница(domain_env: Path) -> None:
    """Прежнее число не выброшено, но подписано честно и стоит рядом с боевым.

    Те же две записи: с эталонной зоной «печь» срабатывает, «панель печи»
    отказывается по колонке — а как зовёт бот, не срабатывает ни одна.
    """
    records = (
        Record(code="CLN05", zone="hot_kitchen", note=OVEN, source="synthetic"),
        Record(
            code="INF09",
            zone="hot_kitchen",
            note="Панель печи: температура 277 °C",
            source="synthetic",
        ),
    )

    fired, rejected = fpm.measure(records, fpm.hints_reference(records))

    assert fired.fired == "CLN05"
    assert fired.correct is True
    assert fired.reason == ""
    assert rejected.fired is None
    assert rejected.correct is None
    assert rejected.reason == NO_COLUMN
    assert fpm.measure(records) == fpm.measure(records, fpm.hints_bot(records))
    assert [o.fired for o in fpm.measure(records)] == [None, None]


def test_доля_и_счёт_неверных_считаются_по_способу(domain_env: Path) -> None:
    """`Mode` отвечает за арифметику отчёта: срабатывания, доля, неверные."""
    records = (
        Record(code="CLN13", zone="dining", note=FURNITURE, source="synthetic"),
        Record(code="TEH05", zone="hot_kitchen", note=OVEN_WITH_ZONE, source="synthetic"),
        Record(code="CLN05", zone="hot_kitchen", note=OVEN, source="belgrade-2"),
    )

    mode = Mode("проба", fpm.measure(records))

    assert len(mode.fired) == 2
    assert mode.share == pytest.approx(200 / 3)
    # `TEH05` — поломка печи, а слова говорят про нагар: пункт не тот.
    assert [o.record.code for o in mode.wrong] == ["TEH05"]


def test_неверное_срабатывание_даёт_код_возврата_1(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Неверное срабатывание опаснее отсутствия: аудитор подтверждает пункт нажатием."""
    record = Record(code="TEH05", zone="hot_kitchen", note=OVEN_WITH_ZONE, source="synthetic")
    monkeypatch.setattr(fpm, "load_records", lambda _root: (record,))

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


def test_отчёт_несёт_дату_и_отпечаток_карты(domain_env: Path) -> None:
    """Число замера без версии карты через неделю нечем проверить (D066)."""
    records = (Record(code="CLN13", zone="dining", note=FURNITURE, source="synthetic"),)

    text = fpm.render(fpm.modes(records))

    assert fpm.fingerprint() in text
    assert "md5 " in text
    assert "ВЕРХНЯЯ ГРАНИЦА" in text
    assert "Как зовёт бот" in text


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
