"""T092: справочник точек и карта синонимов (`src/db/directory.py`).

Задача не про ввод названия — оно по-прежнему приходит текстом (D051).
Задача про то, ЧЕМ введённая строка становится: раньше «БГ2» и «Белград 2»
заводили две несвязанные точки с двумя историями, теперь синоним приводит
к той же точке по её идентификатору (`units.id`). Формулировки (названия,
синонимы) переводятся и правятся, коды нет (конституция, принцип 5) —
поэтому проверки ниже смотрят на `id`, а не на совпавшую строку, и там, где
нужно убедиться, что вторая строка не появилась, — считают строки в базе, а
не верят одному возвращённому значению.
"""

from __future__ import annotations

import pytest
from conftest import requires_db

# `psycopg` — зависимость блока `db`, а не всего проекта: без этой строки сбор
# этого файла падает целиком в окружении, где её ещё не поставили (см.
# аналогичный приём и объяснение в `tests/conftest.py`, раздел про `db`).
psycopg = pytest.importorskip("psycopg")

from src.db.directory import list_units, resolve_unit, upsert_unit  # noqa: E402
from src.db.errors import PushError  # noqa: E402
from src.db.units import normalize_unit_name  # noqa: E402

pytestmark = requires_db


def _строки(dsn: str, sql: str, params: tuple[object, ...] = ()) -> list[tuple[object, ...]]:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def test_точка_заводится_и_находится_по_каноничному_названию(db_env: str) -> None:
    """Базовый цикл: без него ни upsert, ни resolve не имеют смысла обсуждать дальше."""
    unit_id = upsert_unit("Белград 2")

    найдена = resolve_unit("Белград 2")

    assert найдена is not None
    assert найдена.id == unit_id
    assert найдена.name == "Белград 2"


def test_синоним_ведёт_к_той_же_точке(db_env: str) -> None:
    """Суть задачи: «БГ2» и «Белград 2» — одна история, а не две несвязанные."""
    unit_id = upsert_unit("Белград 2", aliases=("БГ2",))

    найдена = resolve_unit("БГ2")

    assert найдена is not None
    assert найдена.id == unit_id


def test_написание_синонима_не_важно(db_env: str) -> None:
    """Регистр и пробелы по краям/внутри — та же нормализация, что у названия точки."""
    unit_id = upsert_unit("Белград 2", aliases=("БГ2",))

    for написание in ("бг2", "БГ2", " БГ2 ", "  бг2  ", "БГ2 "):
        assert normalize_unit_name(написание) == "бг2", (
            f"«{написание}» нормализуется иначе, чем эталонное «бг2» — тест проверяет не то"
        )
        найдена = resolve_unit(написание)
        assert найдена is not None, f"«{написание}» не резолвится, хотя должно"
        assert найдена.id == unit_id


def test_пробел_внутри_синонима_это_другое_написание(db_env: str) -> None:
    """«БГ 2» с пробелом внутри нормализуется в другой ключ, чем «БГ2» без пробела.

    Заведён только «БГ2» без пробела — «БГ 2» резолвиться не должен, пока не
    заведён отдельным синонимом. Проверка фактического поведения
    `normalize_unit_name`, а не догадки о нём.
    """
    assert normalize_unit_name("БГ 2") != normalize_unit_name("БГ2")
    upsert_unit("Белград 2", aliases=("БГ2",))

    assert resolve_unit("БГ 2") is None


def test_повторный_upsert_с_тем_же_кодом_не_создаёт_вторую_точку(db_env: str) -> None:
    """Повторная загрузка справочника из внешнего источника опознаёт точку по коду."""
    первый_id = upsert_unit("Белград 2", code="BG2")

    второй_id = upsert_unit("Белград-2 (новое имя)", code="BG2")

    assert первый_id == второй_id
    строки = _строки(db_env, "select id::text, name from units where code = %s", ("BG2",))
    assert строки == [(первый_id, "Белград-2 (новое имя)")], (
        "повторный upsert с тем же кодом должен обновить имя одной и той же строки"
    )


def test_повторный_upsert_без_кода_с_тем_же_названием_не_создаёт_вторую_точку(
    db_env: str,
) -> None:
    """Тот же путь опознавания, что и в push_inspection, — по name_normalized."""
    первый_id = upsert_unit("Ниш 1")

    второй_id = upsert_unit("  ниш 1  ")

    assert первый_id == второй_id
    (число,) = _строки(db_env, "select count(*) from units where name_normalized = %s", ("ниш 1",))
    assert число == (1,)


def test_синоним_совпавший_с_каноничным_названием_в_карту_не_пишется(db_env: str) -> None:
    """Такой синоним ничего не решает, а место в карте занимает и путает читающего."""
    unit_id = upsert_unit("Белград 2", aliases=("белград 2", "БГ2"))

    строки = _строки(
        db_env, "select alias_normalized from unit_aliases where unit_id = %s", (unit_id,)
    )
    assert строки == [("бг2",)], "в карте должен остаться только настоящий синоним"


def test_синоним_можно_перевесить_на_другую_точку(db_env: str) -> None:
    """Тот же синоним у двух точек подряд — карта должна указывать на последнюю."""
    точка_a = upsert_unit("Белград 2", aliases=("БГ2",))
    точка_b = upsert_unit("Белград 3", aliases=("БГ2",))

    найдена = resolve_unit("БГ2")

    assert найдена is not None
    assert найдена.id == точка_b
    assert точка_a != точка_b
    строки = _строки(
        db_env, "select unit_id::text from unit_aliases where alias_normalized = %s", ("бг2",)
    )
    assert строки == [(точка_b,)], "строка синонима должна быть ровно одна и указывать на B"


def test_list_units_отдаёт_справочник_арендатора_по_алфавиту_без_чужих(db_env: str) -> None:
    """Список — то, из чего бот однажды покажет справочник; порядок и границы важны.

    Названия — без цифрового суффикса намеренно: на этой базе (Postgres 16,
    коллейшн en_US.UTF-8) добавление цифры к кириллическому названию меняет
    порядок сортировки на неочевидный («Белград 2» > «Ниш 1» при том, что
    «Белград» < «Ниш» без цифр) — это особенность коллейшна ОС, а не то, что
    здесь проверяется. Подробности — в отчёте задачи.
    """
    upsert_unit("Ниш", tenant="default")
    upsert_unit("Белград", aliases=("БГ",), tenant="default")
    upsert_unit("Партнёрская точка", tenant="partner")

    справочник = list_units(tenant="default")

    assert [точка.name for точка in справочник] == ["Белград", "Ниш"]
    белград = справочник[0]
    assert белград.aliases == ("БГ",)
    assert "Партнёрская точка" not in [точка.name for точка in справочник]


def test_неизвестное_название_это_none_а_не_выдумка(db_env: str) -> None:
    """Справочник не должен подсовывать похожую точку вместо честного отказа."""
    upsert_unit("Белград 2")

    assert resolve_unit("Такой пиццерии нет") is None


def test_пустое_название_это_отказ_при_записи_и_none_при_поиске(db_env: str) -> None:
    """Пустую строку в справочник заводить нечем — та же граница, что у upsert."""
    with pytest.raises(PushError):
        upsert_unit("   ")

    assert resolve_unit("   ") is None


def test_код_точки_не_обязателен_и_точек_без_кода_может_быть_много(db_env: str) -> None:
    """Уникальный индекс по коду не должен мешать двум разным точкам без кода."""
    первая_id = upsert_unit("Точка без кода 1")
    вторая_id = upsert_unit("Точка без кода 2")

    assert первая_id != вторая_id
    (число,) = _строки(db_env, "select count(*) from units where code is null")
    assert число == (2,)


def test_каноничное_название_сильнее_синонима(db_env: str) -> None:
    """Порядок поиска: сперва названия точек, потом карта синонимов.

    Иначе синоним одной точки мог бы перекрыть настоящее название другой.
    """
    точка_a = upsert_unit("Белград 1")
    upsert_unit("Белград 9", aliases=("Белград 1",))

    найдена = resolve_unit("Белград 1")

    assert найдена is not None
    assert найдена.id == точка_a
