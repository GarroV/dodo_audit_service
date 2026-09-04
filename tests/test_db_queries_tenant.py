"""T110: чтение проверок не имеет права выходить за арендатора и за предел.

Схема стала мультиарендной миграцией `0002`, а точка чтения — нет:
`list_inspections` отдавала проверки ВСЕХ арендаторов сразу и без предела.
Пока арендатор в продукте один, течь нечему; но читает через этот слой
MCP-сервер (T095), который мы сами даём в руки агенту партнёра, — и там
«забыли передать фильтр» означает, что партнёр A видит историю партнёра B.

Поэтому проверяется не «фильтр работает», а три отдельных свойства:
арендатор нельзя не передать (иначе однажды не передадут), выдача имеет
конечный предел, и запрос идёт по индексу, а не полным проходом по таблице.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import requires_db

psycopg = pytest.importorskip("psycopg")

from src.db.errors import DbError  # noqa: E402 — после importorskip намеренно
from src.db.push import push_inspection  # noqa: E402
from src.db.queries import (  # noqa: E402
    _LIST_ALL_SQL,
    DEFAULT_LIMIT,
    MAX_LIMIT,
    list_inspections,
)
from src.domain import add_finding, start_inspection  # noqa: E402

pytestmark = requires_db

АРЕНДАТОР_А = "партнёр-а"
АРЕНДАТОР_Б = "партнёр-б"


def _проверка(chat_id: int, *, арендатор: str, точка: str = "Белград-1") -> str:
    """Завершённая проверка нужного арендатора через официальный контракт домена."""
    start_inspection(chat_id, unit=точка, kind="planned", report_lang="ru", tenant=арендатор)
    add_finding(chat_id, code="CLN05", level="D1", zone="hot_kitchen", text="нагар на печи")
    return push_inspection(chat_id)


def test_выборка_не_отдаёт_проверки_чужого_арендатора(domain_env: Path, db_env: str) -> None:
    """Главная проверка задачи: снятый фильтр по арендатору обязан её валить."""
    id_а = _проверка(101, арендатор=АРЕНДАТОР_А, точка="Белград-1")
    id_б = _проверка(102, арендатор=АРЕНДАТОР_Б, точка="Ниш-1")

    свои = list_inspections(tenant=АРЕНДАТОР_А)

    коды = {строка.tenant_code for строка in свои}
    assert коды == {АРЕНДАТОР_А}, (
        f"выборка арендатора {АРЕНДАТОР_А} отдала чужие проверки: {коды}. "
        f"Через MCP это история партнёра, которую он видеть не должен"
    )
    assert [строка.id for строка in свои] == [id_а]
    assert id_б not in {строка.id for строка in свои}


def test_выборка_по_точке_не_отдаёт_чужого_арендатора(domain_env: Path, db_env: str) -> None:
    """Одинаковое название точки у двух арендаторов — самый вероятный случай.

    «Белград-1» есть и у управляющей компании, и у партнёра: фильтр только по
    названию склеил бы две разные пиццерии в одну историю.
    """
    id_а = _проверка(103, арендатор=АРЕНДАТОР_А, точка="Белград-1")
    _проверка(104, арендатор=АРЕНДАТОР_Б, точка="Белград-1")

    свои = list_inspections(tenant=АРЕНДАТОР_А, unit="Белград-1")

    assert [строка.id for строка in свои] == [id_а]
    assert {строка.tenant_code for строка in свои} == {АРЕНДАТОР_А}


def test_арендатор_обязателен_а_не_необязательный_фильтр() -> None:
    """Необязательный фильтр однажды забудут передать — и дефект вернётся молча.

    Проверяется именно отказ вызова без арендатора: значение по умолчанию
    («default») выглядело бы работающим ровно до появления второго арендатора.
    """
    with pytest.raises(TypeError):
        list_inspections()  # type: ignore[call-arg]


@pytest.mark.parametrize("пустой", ["", "   "])
def test_пустой_арендатор_это_отказ_а_не_пустой_список(
    domain_env: Path, db_env: str, пустой: str
) -> None:
    """Пустая строка совпала бы ни с чем и вернула бы пустой список — то есть
    ошибка вызывающего выглядела бы как «проверок нет»."""
    with pytest.raises(DbError, match="рендатор"):
        list_inspections(tenant=пустой)


def test_у_выдачи_есть_предел_и_он_настраивается(domain_env: Path, db_env: str) -> None:
    _проверка(105, арендатор=АРЕНДАТОР_А)
    _проверка(106, арендатор=АРЕНДАТОР_А, точка="Ниш-1")
    _проверка(107, арендатор=АРЕНДАТОР_А, точка="Ниш-2")

    assert len(list_inspections(tenant=АРЕНДАТОР_А, limit=2)) == 2
    assert len(list_inspections(tenant=АРЕНДАТОР_А, unit="Белград-1", limit=1)) == 1
    assert DEFAULT_LIMIT > 0, "предел по умолчанию обязан быть конечным числом"


@pytest.mark.parametrize("предел", [0, -1, MAX_LIMIT + 1])
def test_бессмысленный_предел_это_отказ(domain_env: Path, db_env: str, предел: int) -> None:
    """Ноль вернул бы пустоту, а миллион — тот же полный проход под видом предела."""
    with pytest.raises(DbError, match="редел"):
        list_inspections(tenant=АРЕНДАТОР_А, limit=предел)


def test_недоступная_база_это_отказ_а_не_пустой_список(monkeypatch: pytest.MonkeyPatch) -> None:
    """Испорченный вход — заведомо мёртвый адрес базы, а не выдуманное поведение.

    Пустой список вместо отказа означал бы «у этого арендатора проверок нет» —
    а на деле их просто не смогли прочитать.
    """
    monkeypatch.setenv("DATABASE_URL", "postgresql://nouser@127.0.0.1:1/nodb?connect_timeout=2")

    with pytest.raises(DbError, match="список проверок"):
        list_inspections(tenant=АРЕНДАТОР_А)


# --- план запроса ------------------------------------------------------------

_НАПОЛНИТЬ_SQL = """
insert into tenants (code) values (%(tenant)s) on conflict (code) do nothing
"""

_ТОЧКА_SQL = """
insert into units (tenant_code, name, name_normalized)
values (%(tenant)s, 'Нагрузочная', 'нагрузочная')
returning id
"""

_МНОГО_ПРОВЕРОК_SQL = """
insert into inspections (
    tenant_code, unit_id, chat_id, kind, inspection_date, report_lang,
    ui_lang, speech_lang, checklist_version, pct, grade, source_fingerprint,
    pushed_at
)
select
    %(tenant)s, %(unit_id)s, 1, 'planned', current_date, 'ru',
    'ru', 'ru', 'v1', 97.5, 'A', %(tenant)s || '-' || g,
    now() - (g || ' minutes')::interval
from generate_series(1, %(сколько)s) g
"""


def _насыпать(dsn: str, *, арендатор: str, сколько: int) -> None:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(_НАПОЛНИТЬ_SQL, {"tenant": арендатор})
        cur.execute(_ТОЧКА_SQL, {"tenant": арендатор})
        row = cur.fetchone()
        assert row is not None
        cur.execute(
            _МНОГО_ПРОВЕРОК_SQL, {"tenant": арендатор, "unit_id": row[0], "сколько": сколько}
        )
        cur.execute("analyze inspections")
        conn.commit()


def test_выборка_идёт_по_индексу_а_не_полным_проходом(pg_dsn: str) -> None:
    """План строит настоящий планировщик на настоящем объёме, а не догадка.

    Без составного индекса `(tenant_code, inspection_date desc)` этот запрос
    читает таблицу целиком и сортирует всё прочитанное ради первой сотни строк
    — то есть предел выдачи экономит трафик и не экономит базу. Разбирается
    тот же текст запроса, который выполняет код: план, закреплённый по
    переписанной от руки копии, обещает не то, что происходит на самом деле.

    Наполнение идёт под привилегированной ролью, а не под ролью приложения:
    `analyze` требует владения таблицей, а без свежей статистики планировщик
    строит план по выдуманным оценкам, и проверять его бессмысленно.
    """
    _насыпать(pg_dsn, арендатор=АРЕНДАТОР_А, сколько=8000)
    _насыпать(pg_dsn, арендатор=АРЕНДАТОР_Б, сколько=2000)

    with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "explain " + _LIST_ALL_SQL,
            {
                "tenant": АРЕНДАТОР_А,
                "limit": DEFAULT_LIMIT,
                "date_from": None,
                "date_to": None,
            },
        )
        план = "\n".join(строка[0] for строка in cur.fetchall())

    assert "Seq Scan on inspections" not in план, (
        f"выборка проверок арендатора читает таблицу целиком:\n{план}"
    )
    assert "inspections_tenant_date_idx" in план, (
        f"выборка не пользуется составным индексом по арендатору и дате обхода:\n{план}"
    )
