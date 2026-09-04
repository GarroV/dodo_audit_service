"""T114: новые точки чтения не имеют права выходить за арендатора и за предел.

Слой чтения умел одно — `list_inspections`. Задача добавляет чтение проверки
по идентификатору и чтение находок, а это два новых способа выйти за
арендатора. Идентификатор проверки угадать нельзя, но неугадываемость — это
надежда, а не защита: `get_inspection` фильтрует по арендатору так же, как
список, и чужая проверка по настоящему идентификатору не читается.

Проверяется здесь не «фильтр написан», а то, что его СНЯТИЕ видно: каждый
тест ниже устроен так, чтобы падать от снятого арендатора, а не от
какой-нибудь другой сломавшейся ссылки. Поэтому рядом с «чужого не отдало»
всегда стоит «своё отдало»: пустая выдача из-за сломанного запроса выглядела
бы точно так же, как работающая защита.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from conftest import requires_db

psycopg = pytest.importorskip("psycopg")

from src.db.errors import DbError  # noqa: E402 — после importorskip намеренно
from src.db.push import push_inspection  # noqa: E402
from src.db.queries import (  # noqa: E402
    _FINDINGS_BY_UNIT_SQL,
    MAX_LIMIT,
    findings_by_unit,
    get_inspection,
)
from src.domain import add_finding, start_inspection  # noqa: E402

pytestmark = requires_db

АРЕНДАТОР_А = "партнёр-а"
АРЕНДАТОР_Б = "партнёр-б"

#: Один пункт в одной зоне движок принимает один раз, поэтому несколько
#: находок разводятся по зонам, а не повторяют одну и ту же запись.
ЗОНЫ = ("hot_kitchen", "cold_kitchen", "dough", "dishwashing", "staff")


def _проверка(
    chat_id: int,
    *,
    арендатор: str,
    точка: str = "Белград-1",
    текст: str = "нагар на печи",
    находок: int = 1,
) -> str:
    """Завершённая проверка нужного арендатора через официальный контракт домена."""
    start_inspection(chat_id, unit=точка, kind="Плановая", report_lang="ru", tenant=арендатор)
    for номер in range(находок):
        add_finding(chat_id, code="CLN05", level="D1", zone=ЗОНЫ[номер], text=текст)
    return push_inspection(chat_id)


# --- проверка по идентификатору ----------------------------------------------


def test_проверка_чужого_арендатора_не_читается_по_идентификатору(
    domain_env: Path, db_env: str
) -> None:
    """Главная проверка задачи для `get_inspection`: снятый арендатор её валит.

    Идентификатор здесь настоящий — тот самый, что вернул слив чужой проверки.
    Именно так выглядит утечка через MCP: агент партнёра А называет id, который
    когда-то увидел, и получает документ партнёра Б.
    """
    чужая = _проверка(301, арендатор=АРЕНДАТОР_Б, точка="Ниш-1")
    своя = _проверка(302, арендатор=АРЕНДАТОР_А, точка="Белград-1")

    assert get_inspection(чужая, tenant=АРЕНДАТОР_А) is None, (
        "чтение по идентификатору отдало проверку чужого арендатора — "
        "через MCP это документ партнёра, который спрашивающий видеть не должен"
    )

    моя = get_inspection(своя, tenant=АРЕНДАТОР_А)
    assert моя is not None, "своя проверка не прочиталась — пустота выше была бы по другой причине"
    assert моя.inspection.id == своя
    assert моя.inspection.tenant_code == АРЕНДАТОР_А


def test_чужая_проверка_не_отдаёт_и_своих_находок(domain_env: Path, db_env: str) -> None:
    """Отказ обязан быть целым: ни шапки чужой проверки, ни её находок."""
    чужая = _проверка(303, арендатор=АРЕНДАТОР_Б, текст="чужая находка", находок=2)

    assert get_inspection(чужая, tenant=АРЕНДАТОР_А) is None


def test_несуществующий_идентификатор_это_пусто_а_не_поломка(domain_env: Path, db_env: str) -> None:
    """Ненайденная проверка — законный ответ `None`, а не исключение."""
    assert get_inspection(str(uuid.uuid4()), tenant=АРЕНДАТОР_А) is None


# --- находки точки -----------------------------------------------------------


def test_находки_точки_не_отдают_чужого_арендатора(domain_env: Path, db_env: str) -> None:
    """Одинаковое название точки у двух арендаторов — самый вероятный случай.

    «Белград-1» есть и у управляющей компании, и у партнёра: фильтр только по
    названию склеил бы находки двух разных пиццерий в один список нарушений.
    """
    _проверка(304, арендатор=АРЕНДАТОР_Б, точка="Белград-1", текст="чужая находка")
    своя = _проверка(305, арендатор=АРЕНДАТОР_А, точка="Белград-1", текст="своя находка")

    находки = findings_by_unit(tenant=АРЕНДАТОР_А, unit="Белград-1")

    assert [запись.inspection_id for запись in находки] == [своя]
    assert {запись.text for запись in находки} == {"своя находка"}, (
        "в находках точки оказались чужие формулировки — это уже готовый пересказ "
        "чужой проверки, даже без её идентификатора"
    )


# --- арендатор и предел ------------------------------------------------------


def test_арендатор_обязателен_у_обеих_новых_выборок() -> None:
    """Необязательный фильтр однажды забудут передать — и утечка вернётся молча.

    Проверяется именно отказ вызова без арендатора: значение по умолчанию
    («default») выглядело бы работающим ровно до появления второго арендатора.
    """
    with pytest.raises(TypeError):
        get_inspection(str(uuid.uuid4()))  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        findings_by_unit(unit="Белград-1")  # type: ignore[call-arg]


@pytest.mark.parametrize("пустой", ["", "   "])
def test_пустой_арендатор_это_отказ_а_не_пустая_выдача(
    domain_env: Path, db_env: str, пустой: str
) -> None:
    """Пустая строка не совпала бы ни с чем: ошибка вызывающего выглядела бы
    как «такой проверки нет» и «нарушений у точки нет»."""
    with pytest.raises(DbError, match="рендатор"):
        get_inspection(str(uuid.uuid4()), tenant=пустой)
    with pytest.raises(DbError, match="рендатор"):
        findings_by_unit(tenant=пустой, unit="Белград-1")


def test_у_находок_есть_предел_и_он_настраивается(domain_env: Path, db_env: str) -> None:
    _проверка(306, арендатор=АРЕНДАТОР_А, точка="Белград-1", находок=3)

    assert len(findings_by_unit(tenant=АРЕНДАТОР_А, unit="Белград-1")) == 3
    assert len(findings_by_unit(tenant=АРЕНДАТОР_А, unit="Белград-1", limit=2)) == 2


@pytest.mark.parametrize("предел", [0, -1, MAX_LIMIT + 1])
def test_бессмысленный_предел_находок_это_отказ(domain_env: Path, db_env: str, предел: int) -> None:
    """Ноль вернул бы пустоту, а миллион — полный проход под видом предела."""
    with pytest.raises(DbError, match="редел"):
        findings_by_unit(tenant=АРЕНДАТОР_А, unit="Белград-1", limit=предел)


@pytest.mark.parametrize("пустая", ["", "   "])
def test_пустое_название_точки_это_отказ(domain_env: Path, db_env: str, пустая: str) -> None:
    """Пустое название вернуло бы пустой список нарушений — то есть «у этой
    точки всё хорошо» вместо «вы не назвали точку»."""
    with pytest.raises(DbError, match="очк"):
        findings_by_unit(tenant=АРЕНДАТОР_А, unit=пустая)


# --- план запроса ------------------------------------------------------------

#: Сколько точек у арендатора в нагрузочных данных. Не одна намеренно: у сети
#: сотни пиццерий, а на единственной точке планировщик выбирает не тот план,
#: что на настоящих данных, — и проверка плана на такой заливке проверяет не то.
ТОЧЕК = 40

_ТОЧКИ_SQL = """
insert into units (tenant_code, name, name_normalized)
select %(tenant)s, 'Точка ' || g, 'точка ' || g
from generate_series(1, %(точек)s) g
"""

_ПРОВЕРКИ_SQL = """
with точки as (
    select id, row_number() over (order by name_normalized) - 1 as ном
    from units where tenant_code = %(tenant)s
)
insert into inspections (
    tenant_code, unit_id, chat_id, kind, inspection_date, report_lang,
    ui_lang, speech_lang, checklist_version, pct, grade, source_fingerprint
)
select
    %(tenant)s, т.id, 1, 'Плановая', date '2026-01-01' + g, 'ru',
    'ru', 'ru', 'v1', 97.5, 'A', %(tenant)s || '-' || g
from generate_series(1, %(сколько)s) g
join точки т on т.ном = g %% %(точек)s
"""

_НАХОДКИ_SQL = """
insert into findings (inspection_id, n, code, level, zone)
select id, 1, 'CLN05', 'D1', 'hot_kitchen' from inspections where tenant_code = %(tenant)s
"""


def _насыпать(dsn: str, *, арендатор: str, сколько: int) -> None:
    """Настоящий объём для планировщика: по находке на проверку.

    Наполнение идёт под привилегированной ролью, а не под ролью приложения:
    `analyze` требует владения таблицей, а без свежей статистики планировщик
    строит план по выдуманным оценкам, и проверять его бессмысленно.
    """
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "insert into tenants (code) values (%(tenant)s) on conflict (code) do nothing",
            {"tenant": арендатор},
        )
        cur.execute(_ТОЧКИ_SQL, {"tenant": арендатор, "точек": ТОЧЕК})
        cur.execute(_ПРОВЕРКИ_SQL, {"tenant": арендатор, "сколько": сколько, "точек": ТОЧЕК})
        cur.execute(_НАХОДКИ_SQL, {"tenant": арендатор})
        cur.execute("analyze inspections")
        cur.execute("analyze findings")
        cur.execute("analyze units")
        conn.commit()


def test_находки_точки_идут_по_индексу_а_не_полным_проходом(pg_dsn: str) -> None:
    """План строит настоящий планировщик на настоящем объёме, а не догадка.

    Разбирается тот же текст запроса, который выполняет код: план,
    закреплённый по переписанной от руки копии запроса, обещает не то, что
    происходит на самом деле.
    """
    _насыпать(pg_dsn, арендатор=АРЕНДАТОР_А, сколько=8000)
    _насыпать(pg_dsn, арендатор=АРЕНДАТОР_Б, сколько=2000)

    with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "explain " + _FINDINGS_BY_UNIT_SQL,
            {"tenant": АРЕНДАТОР_А, "unit": "точка 7", "limit": 100},
        )
        план = "\n".join(строка[0] for строка in cur.fetchall())

    assert "Seq Scan on findings" not in план, f"находки точки читаются полным проходом:\n{план}"
    assert "Seq Scan on inspections" not in план, (
        f"находки точки тянут за собой полный проход по проверкам:\n{план}"
    )
    assert "inspections_tenant_unit_date_idx" in план, (
        f"находки точки идут мимо индекса «арендатор + точка + дата» (миграция 0005): "
        f"без него отбор по точке идёт поверх всей истории арендатора\n{план}"
    )
