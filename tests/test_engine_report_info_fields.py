"""T159: информационные поля печатаются в отчёте партнёру, скрытых среди них нет.

Три поля (`INF01`, `INF05`, `INF06`) движок собирал в состояние, но из отчёта
вычёркивал списком `HIDDEN_INFO`. Партнёр их не видел, а аудитор об этом не
знал — поле заполнялось и молча пропадало. Решение владельца D069, дословно:
«Собирать и печатать в отчёте».

Проверяем именно печать значения, а не наличие раздела: раздел приложения
появляется и от одной записи D0, а поле при этом может остаться вычеркнутым.
"""

from __future__ import annotations

from collections.abc import Callable

from conftest import Run

#: Ровно те коды, что стояли в `HIDDEN_INFO`. Список здесь не «на всякий
#: случай»: тест обязан покраснеть, если вычёркивание вернут для любого из них.
ONCE_HIDDEN = ("INF01", "INF05", "INF06")


def test_прежде_скрытые_поля_печатаются_в_отчёте(
    started: Callable[..., Run], report: Callable[..., Run]
) -> None:
    for i, qid in enumerate(ONCE_HIDDEN, start=1):
        r = started("info", "--qid", qid, "--text", f"значение поля {i}")
        assert r.code == 0, r.text
    r = report("html")
    assert r.code == 0, r.text
    for i, qid in enumerate(ONCE_HIDDEN, start=1):
        assert f"значение поля {i}" in r.out, f"{qid} не напечатан партнёру"


def test_информационное_поле_печатается_под_формулировкой_пункта(
    started: Callable[..., Run], report: Callable[..., Run]
) -> None:
    """Партнёр читает вопрос, а не код: подпись берётся из чек-листа."""
    started("info", "--qid", "INF01", "--text", "смена из четырёх человек")
    r = report("html")
    assert r.code == 0, r.text
    assert "Справочная строка: состав смены" in r.out, "подпись поля взята не из чек-листа"
    assert "смена из четырёх человек" in r.out


def test_единственное_скрытое_поле_поднимает_приложение(
    started: Callable[..., Run], report: Callable[..., Run]
) -> None:
    """Раздела приложения не было вовсе, когда все заполненные поля вычёркивались."""
    started("info", "--qid", "INF06", "--text", "зоны роста")
    r = report("html")
    assert r.code == 0, r.text
    assert "Приложение. Информационные записи" in r.out, "приложение не собрано"
    assert "зоны роста" in r.out
