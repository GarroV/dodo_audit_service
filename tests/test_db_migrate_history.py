"""T090: история схемы линейна и накатывается на пустую базу с нуля.

Почему это отдельная проверка, а не «и так видно по зелёному прогону»: уже
накатанная база **скрывает** поломку. Раннер применяет только то, чего ещё
нет, поэтому на рабочей машине разработчика всё выглядит рабочим, а на чистой
базе — на новой машине, на площадке, в тесте приёмки — накат падает.

Самый дорогой случай такой поломки: два файла с одним номером. Он появляется
сам собой, когда две ветки независимо добавили `0002_*.sql` и обе слились.
Порядок наката между ними неопределён (решает только алфавит хвоста имени),
а на машинах, где одна из веток уже была накатана, расхождение не видно
вовсе.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from conftest import requires_db
from db_harness import empty_database

psycopg = pytest.importorskip("psycopg")

from src.db.errors import ConfigError  # noqa: E402 — после importorskip намеренно
from src.db.migrate import MIGRATIONS_DIR, apply_migrations, discover_migrations  # noqa: E402


def _номер(filename: str) -> str:
    match = re.match(r"^(\d+)_", filename)
    assert match is not None, f"у миграции {filename} нет числового префикса"
    return match.group(1)


def test_история_схемы_линейна_и_идёт_по_порядку() -> None:
    """Настоящий каталог миграций: номера уникальны и возрастают."""
    имена = [m.filename for m in discover_migrations()]
    номера = [_номер(имя) for имя in имена]
    assert len(set(номера)) == len(номера), f"в истории схемы два листа: {имена}"
    assert номера == sorted(номера), f"порядок наката не совпадает с номерами: {имена}"


def test_две_миграции_с_одним_номером_не_проходят(tmp_path: Path) -> None:
    """Испорченный вход: ветка добавила свой `0002_*`, не зная о чужом."""
    (tmp_path / "0001_initial.sql").write_text("select 1;\n", encoding="utf-8")
    (tmp_path / "0002_alpha.sql").write_text("select 1;\n", encoding="utf-8")
    (tmp_path / "0002_beta.sql").write_text("select 1;\n", encoding="utf-8")

    with pytest.raises(ConfigError, match=r"0002"):
        discover_migrations(tmp_path)


def test_файл_без_номера_в_каталоге_миграций_не_проходит(tmp_path: Path) -> None:
    """Без номера место файла в истории неизвестно — это отказ, а не «в конец»."""
    (tmp_path / "0001_initial.sql").write_text("select 1;\n", encoding="utf-8")
    (tmp_path / "schema.sql").write_text("select 1;\n", encoding="utf-8")

    with pytest.raises(ConfigError, match=r"schema\.sql"):
        discover_migrations(tmp_path)


@requires_db
def test_накат_на_пустую_базу_применяет_всю_историю_по_порядку() -> None:
    """С нуля, а не «поверх того, что уже стояло»: применяются ВСЕ файлы."""
    ожидаемые = [m.filename for m in discover_migrations()]
    with empty_database() as dsn:
        применённые = apply_migrations(dsn)
        assert применённые == ожидаемые, (
            "накат с нуля применил не всю историю схемы: "
            f"ожидалось {ожидаемые}, применено {применённые}"
        )
        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute("select filename from schema_migrations order by filename")
            записано = [row[0] for row in cur.fetchall()]
        assert записано == ожидаемые
        assert apply_migrations(dsn) == [], "повторный накат на той же базе не идемпотентен"


def test_каталог_миграций_не_пустой() -> None:
    """Сторож на случай, если проверки выше начнут гонять по пустому списку."""
    assert MIGRATIONS_DIR.is_dir()
    assert list(MIGRATIONS_DIR.glob("*.sql")), "в каталоге миграций нет ни одного файла"
