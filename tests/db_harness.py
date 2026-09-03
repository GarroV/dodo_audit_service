"""Оснастка тестов блока `db`, которой не место в общем `conftest.py`.

Общий `tests/conftest.py` — файл всех блоков сразу: его правят и `domain`, и
`bot`, и правка из параллельной работы стоит конфликта на ровном месте. Всё,
что нужно только проверкам базы, живёт здесь и подключается обычным импортом
(`tests/` плоский, `sys.path` до него доводит `pythonpath = ["."]` и сам
pytest).

`psycopg` импортируется внутри тела функции, а не на уровне модуля: этот файл
собирается вместе со всем `tests/`, и голый импорт уронил бы сбор целиком в
окружении, где зависимость блока ещё не поставлена (тот же приём и та же
причина, что в `tests/conftest.py`).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

DATABASE_URL_VAR = "DATABASE_URL"


@contextmanager
def empty_database() -> Iterator[str]:
    """DSN свежесозданной базы, на которой НЕ накатана ни одна миграция.

    Отличие от фикстуры `pg_dsn` принципиальное: та отдаёт базу с уже
    применённой схемой, и на ней накат «с нуля» проверить нечем — раннер
    честно ответит «нечего накатывать». Пустая база нужна ровно там, где
    проверяется, что вся история применяется по порядку и до конца.

    Имя случайное, база удаляется в `finally` — соседние базы на том же
    сервере (проектная, чужих блоков) не трогаются ни при каком исходе.
    """
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo

    base = os.environ[DATABASE_URL_VAR]
    maintenance_dsn = make_conninfo(base, dbname="postgres")
    dbname = f"dodo_audit_test_{uuid.uuid4().hex[:12]}"

    with psycopg.connect(maintenance_dsn, autocommit=True) as conn:
        conn.execute(sql.SQL("create database {}").format(sql.Identifier(dbname)))
    try:
        yield make_conninfo(base, dbname=dbname)
    finally:
        with psycopg.connect(maintenance_dsn, autocommit=True) as conn:
            conn.execute(
                sql.SQL("drop database if exists {} with (force)").format(sql.Identifier(dbname))
            )
