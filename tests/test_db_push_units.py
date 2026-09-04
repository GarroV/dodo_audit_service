"""T092: слив связывает проверку с точкой по идентификатору, а не по строке.

Это то самое место, ради которого заводился справочник. Проверять «синоним
резолвится» отдельно от слива мало: резолвер мог бы работать, а `push_inspection`
по-прежнему заводить новую точку по введённой строке — и расхождение вылезло бы
только через полгода, когда у одной пиццерии обнаружились бы две истории.

Поэтому здесь проверяется не резолвер, а результат слива: `inspections.unit_id`
у проверки, начатой строкой «БГ2», обязан совпадать с идентификатором точки
«Белград 2» из справочника.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import requires_db

# `psycopg` — зависимость блока `db`, а не всего проекта: без этой строки сбор
# файла падает целиком там, где её ещё не поставили (тот же приём и та же
# причина, что в `tests/conftest.py`).
psycopg = pytest.importorskip("psycopg")

from src.db.directory import upsert_unit  # noqa: E402 — после importorskip намеренно
from src.db.push import push_inspection  # noqa: E402
from src.domain import add_finding, start_inspection  # noqa: E402

pytestmark = requires_db

ТОЧКА = "Белград 2"
СИНОНИМ = "БГ2"


def _проверка(chat_id: int, unit: str) -> str:
    start_inspection(chat_id, unit=unit, kind="Плановая", report_lang="ru")
    add_finding(chat_id, code="CLN05", level="D1", zone="hot_kitchen", text="нагар на печи")
    return push_inspection(chat_id)


def _строки(dsn: str, sql: str, params: tuple[object, ...] = ()) -> list[tuple[object, ...]]:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def test_проверка_по_синониму_ложится_в_ту_же_точку_что_и_по_названию(
    domain_env: Path, db_env: str
) -> None:
    """«БГ2» на бегу и «Белград 2» в спокойной обстановке — одна пиццерия."""
    unit_id = upsert_unit(ТОЧКА, code="BG2", aliases=(СИНОНИМ,))

    по_названию = _проверка(21, ТОЧКА)
    по_синониму = _проверка(22, СИНОНИМ)

    точки = _строки(
        db_env,
        "select unit_id from inspections where id = any(%s) ",
        ([по_названию, по_синониму],),
    )
    assert {str(строка[0]) for строка in точки} == {unit_id}, (
        "проверка по синониму уехала в другую точку — у одной пиццерии две истории"
    )
    (сколько_точек,) = _строки(db_env, "select count(*) from units", ())
    assert сколько_точек == (1,), "слив завёл вторую точку вместо связи со справочником"


def test_название_вне_справочника_по_прежнему_заводит_точку(domain_env: Path, db_env: str) -> None:
    """Незаполненный справочник не повод отказать в сливе: проверка уже сделана.

    Это поведение до T092, и оно обязано сохраниться: справочник появляется
    после MVP (D051), а сливать проверки надо и до него.
    """
    inspection_id = _проверка(23, "Нови-Сад 1")

    (строка,) = _строки(
        db_env,
        "select u.name, u.code from inspections i join units u on u.id = i.unit_id where i.id = %s",
        (inspection_id,),
    )
    assert строка == ("Нови-Сад 1", None), "точка не заведена или получила чужой код"


def test_синоним_не_перетягивает_на_себя_точку_с_таким_же_названием(
    domain_env: Path, db_env: str
) -> None:
    """Каноничное название сильнее чужого синонима — иначе точки меняются местами."""
    настоящая = upsert_unit("Белград 1", code="BG1")
    upsert_unit("Нови-Сад 3", code="NS3", aliases=("Белград 1",))

    inspection_id = _проверка(24, "Белград 1")

    (строка,) = _строки(db_env, "select unit_id from inspections where id = %s", (inspection_id,))
    assert str(строка[0]) == настоящая, (
        "проверка уехала в точку, которая всего лишь назвала чужое название своим синонимом"
    )
