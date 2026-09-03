"""Разделение арендаторов держится схемой, а не дисциплиной запросов (T092).

Мультиарендность заложена формой с первой версии (D017, конституция, принцип 6),
и пока в продукте один арендатор `default`, любая утечка между ними невидима:
кода, который ходил бы за чужой арендатор, просто нет. Именно поэтому её надо
ловить схемой — соглашение в коде проверить нечем, а когда арендаторов станет
двое, цена ошибки будет уже не тестовой.

Опасное место конкретное: и карта синонимов, и сама проверка ссылались на точку
по одному только `units.id`, без арендатора. Значит, синоним арендатора A мог
указывать на точку арендатора B — и `resolve_unit(name, tenant="A")` честно
отдал бы чужую пиццерию, ничем себя не выдав.
"""

from __future__ import annotations

import pytest
from conftest import requires_db

# `psycopg` — зависимость блока `db`, а не всего проекта: без этой строки сбор
# файла падает целиком там, где её ещё не поставили.
psycopg = pytest.importorskip("psycopg")

from src.db.directory import resolve_unit, upsert_unit  # noqa: E402 — после importorskip

pytestmark = requires_db

СВОЙ = "default"
ЧУЖОЙ = "partner"


def test_синоним_не_может_указывать_на_точку_чужого_арендатора(db_env: str) -> None:
    """Проверяется отказ базы, а не поведение кода: код такой строки и не пишет.

    Сверяется ИМЯ нарушенного ограничения, а не просто факт отказа: любая
    другая ошибка (не заведён арендатор, не хватает колонки) дала бы такое же
    исключение, и проверка была бы зелёной, ничего не проверяя.
    """
    upsert_unit("Белград 9", tenant=СВОЙ)  # чтобы свой арендатор существовал
    чужая_точка = upsert_unit("Будапешт 1", tenant=ЧУЖОЙ)

    with psycopg.connect(db_env) as conn, conn.cursor() as cur:
        with pytest.raises(psycopg.Error) as отказ:
            cur.execute(
                "insert into unit_aliases (tenant_code, alias_normalized, unit_id, alias) "
                "values (%s, %s, %s, %s)",
                (СВОЙ, "бп1", чужая_точка, "БП1"),
            )
    assert отказ.value.diag.constraint_name == "unit_aliases_tenant_code_unit_id_fkey", (
        f"отказ пришёл не от составной ссылки на точку, а от {отказ.value.diag.constraint_name}"
    )

    # Чужая точка так и не стала видна своему арендатору.
    assert resolve_unit("БП1", tenant=СВОЙ) is None


def test_проверка_не_может_ссылаться_на_точку_чужого_арендатора(db_env: str) -> None:
    """Имя ограничения сверяется по той же причине, что и выше.

    Проверено порчей: без этой сверки тест был зелёным даже со снятым
    ограничением — отказывала другая ссылка, на незаведённого арендатора.
    """
    upsert_unit("Белград 8", tenant=СВОЙ)  # чтобы свой арендатор существовал
    чужая_точка = upsert_unit("Будапешт 2", tenant=ЧУЖОЙ)

    with psycopg.connect(db_env) as conn, conn.cursor() as cur:
        with pytest.raises(psycopg.Error) as отказ:
            cur.execute(
                "insert into inspections (tenant_code, unit_id, chat_id, kind, inspection_date, "
                "report_lang, ui_lang, speech_lang, checklist_version, pct, grade, "
                "source_fingerprint) values (%s, %s, 1, 'Плановая', '2026-09-03', 'ru', 'ru', "
                "'ru', 'v1', 100, 'A', 'отпечаток-теста')",
                (СВОЙ, чужая_точка),
            )
    assert отказ.value.diag.constraint_name == "inspections_unit_same_tenant", (
        f"отказ пришёл не от составной ссылки на точку, а от {отказ.value.diag.constraint_name}"
    )


def test_точка_и_её_синоним_у_своего_арендатора_заводятся_как_обычно(db_env: str) -> None:
    """Сторож на случай, если запрет выше окажется слишком широким.

    Без этой проверки составная ссылка могла бы запрещать вообще всё, и оба
    теста выше остались бы зелёными по неверной причине.
    """
    своя = upsert_unit("Белград 5", aliases=("БГ5",), tenant=СВОЙ)
    найдена = resolve_unit("БГ5", tenant=СВОЙ)

    assert найдена is not None and найдена.id == своя
