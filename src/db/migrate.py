"""Накат SQL-миграций. Схема правится только так — руками её не трогают.

Файлы лежат в `src/db/migrations/`, каждый применяется ровно один раз внутри
своей транзакции: упавшая половина миграции не остаётся в схеме. Применённые
имена и отпечаток их содержимого пишутся в служебную таблицу
`schema_migrations` — тем же принципом, что версия чек-листа (D050): правка
уже применённого файла не проходит тихо, отпечаток не совпадёт и накат
откажет явно, а не по факту непонятного расхождения через полгода.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import psycopg

from .errors import ConfigError

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

_TRACKING_TABLE_SQL = """
create table if not exists schema_migrations (
    filename text primary key,
    checksum text not null,
    applied_at timestamptz not null default now()
);
"""


@dataclass(frozen=True)
class Migration:
    filename: str
    sql: str
    checksum: str


def _checksum(sql: str) -> str:
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


def discover_migrations(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    """Миграции по имени файла — числовой префикс задаёт порядок наката."""
    if not directory.is_dir():
        raise ConfigError(f"Каталог миграций не найден: {directory}")
    files = sorted(p for p in directory.glob("*.sql") if p.is_file())
    if not files:
        raise ConfigError(f"В каталоге миграций нет ни одного файла: {directory}")
    migrations = []
    for path in files:
        sql = path.read_text(encoding="utf-8")
        migrations.append(Migration(filename=path.name, sql=sql, checksum=_checksum(sql)))
    return migrations


def apply_migrations(dsn: str, *, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Накатить все миграции, которых ещё нет в базе. Вернуть имена применённых.

    Идемпотентно: повторный вызов на уже накатанной базе ничего не делает и
    возвращает пустой список — сравнение по имени файла и отпечатку.
    Изменённый задним числом уже применённый файл — явный отказ, а не тихий
    пропуск: расхождение схемы между копиями иначе всплывает только в
    проде.
    """
    migrations = discover_migrations(directory)
    applied: list[str] = []
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(_TRACKING_TABLE_SQL)
            cur.execute("select filename, checksum from schema_migrations")
            known: dict[str, str] = dict(cur.fetchall())
        for migration in migrations:
            existing = known.get(migration.filename)
            if existing is not None:
                if existing != migration.checksum:
                    raise ConfigError(
                        f"Миграция {migration.filename} применена, но её содержимое с тех пор "
                        f"изменилось — отпечаток в базе не совпадает с файлом. Старые миграции "
                        f"не правятся: новая правка идёт новым файлом"
                    )
                continue
            with conn.cursor() as cur:
                cur.execute(migration.sql)
                cur.execute(
                    "insert into schema_migrations (filename, checksum) values (%s, %s)",
                    (migration.filename, migration.checksum),
                )
            conn.commit()
            applied.append(migration.filename)
    return applied


def main() -> None:  # pragma: no cover — тонкая обёртка CLI, проверяется через apply_migrations
    from .config import check_environment

    settings = check_environment()
    applied = apply_migrations(settings.dsn)
    if applied:
        print("Применены миграции: " + ", ".join(applied))
    else:
        print("Схема уже актуальна, накатывать нечего")


if __name__ == "__main__":  # pragma: no cover
    main()
