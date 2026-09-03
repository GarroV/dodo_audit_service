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

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from conftest import BASE_DSN


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

    # Строка запускающего берётся у `conftest`, а не из окружения: во время
    # теста переменной там уже нет — её снимает автоматическая фикстура
    # `_база_не_видна_сама_собой`, чтобы база не доставалась тому, кто её
    # не просил (T123).
    maintenance_dsn = make_conninfo(BASE_DSN, dbname="postgres")
    dbname = f"dodo_audit_test_{uuid.uuid4().hex[:12]}"

    with psycopg.connect(maintenance_dsn, autocommit=True) as conn:
        conn.execute(sql.SQL("create database {}").format(sql.Identifier(dbname)))
    try:
        yield make_conninfo(BASE_DSN, dbname=dbname)
    finally:
        with psycopg.connect(maintenance_dsn, autocommit=True) as conn:
            conn.execute(
                sql.SQL("drop database if exists {} with (force)").format(sql.Identifier(dbname))
            )
