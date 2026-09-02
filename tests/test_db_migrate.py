"""T091: миграции создают схему и накатывают её без ручной правки.

`pg_dsn` уже прогоняет `apply_migrations` один раз (см. `pg_dsn` в
`tests/conftest.py`) — здесь проверяется то, что происходит вокруг первого
наката: список таблиц, идемпотентность повторного вызова и отказ на
испорченном входе (правило конституции: «проверка, которая не падает на
испорченном входе, — не проверка»).
"""

from __future__ import annotations

import pytest
from conftest import requires_db

# `psycopg` — зависимость блока `db`, а не всего проекта: без этой строки сбор
# этого файла падает целиком в окружении, где её ещё не поставили (см.
# аналогичный приём и объяснение в `tests/conftest.py`, раздел про `db`).
psycopg = pytest.importorskip("psycopg")

from src.db.errors import ConfigError  # noqa: E402 — после importorskip намеренно
from src.db.migrate import apply_migrations  # noqa: E402

pytestmark = requires_db

_ОЖИДАЕМЫЕ_ТАБЛИЦЫ = {
    "tenants",
    "units",
    "inspections",
    "findings",
    "photos",
    "translations",
    "schema_migrations",
}


def _таблицы(dsn: str) -> set[str]:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "select table_name from information_schema.tables where table_schema = 'public'"
        )
        return {row[0] for row in cur.fetchall()}


def test_накат_на_пустой_базе_создаёт_все_таблицы(pg_dsn: str) -> None:
    assert _ОЖИДАЕМЫЕ_ТАБЛИЦЫ <= _таблицы(pg_dsn)


def test_повторный_накат_идемпотентен(pg_dsn: str) -> None:
    assert apply_migrations(pg_dsn) == []


def test_изменённая_задним_числом_миграция_не_проходит_тихо(pg_dsn: str) -> None:
    """Испорченный вход: отпечаток в базе разошёлся с файлом на диске.

    Порча подтверждается прямо здесь (`UPDATE` и следом `SELECT`), а не
    предполагается: иначе негативный тест мог бы оказаться зелёным просто
    потому, что подмена не применилась.
    """
    with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "update schema_migrations set checksum = 'bogus' where filename = %s",
            ("0001_initial_schema.sql",),
        )
        conn.commit()
        cur.execute(
            "select checksum from schema_migrations where filename = %s",
            ("0001_initial_schema.sql",),
        )
        assert cur.fetchone() == ("bogus",), "порча не применилась — тест ничего не проверяет"

    with pytest.raises(ConfigError, match=r"0001_initial_schema\.sql"):
        apply_migrations(pg_dsn)
