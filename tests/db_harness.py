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

import pytest
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


#: Роль администратора истории (миграция `0010`). Вторая непривилегированная
#: роль базы: снятые проверки видны только ей. Проверять разграничение под
#: суперпользователем бессмысленно — он обходит политики всегда.
ADMIN_ROLE = "dodo_audit_admin"

#: Пароль роли администратора, если на этой машине к Postgres ходят по паролю.
#: Пусто — подключение идёт без него (peer/trust), как на машине разработчика.
ADMIN_PASSWORD_VAR = "DATABASE_RETRACTION_PASSWORD"

#: Переменная, из которой блок берёт подключение администратора истории.
RETRACTION_URL_VAR = "DATABASE_RETRACTION_URL"


def admin_role_dsn(dsn: str) -> str:
    """Та же база, но под ролью администратора истории.

    Пароль чужой роли отбрасывается намеренно, тем же приёмом и по той же
    причине, что в `conftest.app_role_dsn`: подставленный сюда, он дал бы отказ
    аутентификации вместо понятного «у роли администратора нет пароля».
    """
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    params = {k: v for k, v in conninfo_to_dict(dsn).items() if k != "password"}
    params["user"] = ADMIN_ROLE
    password = os.environ.get(ADMIN_PASSWORD_VAR)
    if password:
        params["password"] = password
    return make_conninfo(**params)


def set_retraction_env(db_env: str, monkeypatch: pytest.MonkeyPatch) -> str:
    """Дать тесту подключение администратора истории вдобавок к подключению приложения.

    Помощник, а не фикстура: фикстуру пришлось бы импортировать в каждый файл,
    а импортированное имя фикстуры совпадает с именем аргумента теста — и
    линтер справедливо читает это как переопределение. Поэтому файлы объявляют
    свою однострочную фикстуру поверх этого помощника.

    Обе связи выдаются одновременно, потому что так устроен продукт: половина
    проверок этого набора в том и состоит, что одна роль видит, а вторая нет.
    """
    dsn = admin_role_dsn(db_env)
    monkeypatch.setenv(RETRACTION_URL_VAR, dsn)
    return dsn
