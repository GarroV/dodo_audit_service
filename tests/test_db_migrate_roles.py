"""Роль приложения и предупреждение о её всесилии (T111).

Защита завершённых проверок держится построчными политиками, а суперпользователь
обходит их **всегда**. Значит приложение, пущенное под привилегированной ролью,
выглядит защищённым, ничем таковым не являясь, — и узнать об этом можно только
если раннер скажет вслух.

Эти пути остались непокрытыми, когда работа блока оборвалась по лимиту сессии:
код был написан и работал, а тестов на него не появилось. Дописано диспетчером.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import requires_db
from db_harness import empty_database

psycopg = pytest.importorskip("psycopg")

from src.db.errors import ConfigError  # noqa: E402 — после importorskip намеренно
from src.db.migrate import (  # noqa: E402
    APP_ROLE,
    DATABASE_ADMIN_URL_VAR,
    DATABASE_APP_PASSWORD_VAR,
    admin_dsn,
    apply_migrations,
    discover_migrations,
    role_bypasses_rls,
    set_app_password,
)


def test_каталог_миграций_которого_нет_отказывает(tmp_path: Path) -> None:
    """Пустой путь — не повод накатить ноль миграций и отчитаться об успехе."""
    with pytest.raises(ConfigError) as exc:
        discover_migrations(tmp_path / "нет-такого")
    assert "не найден" in str(exc.value)


def test_пустой_каталог_миграций_отказывает(tmp_path: Path) -> None:
    """Ноль файлов и «нечего накатывать» — разные вещи, вторая тут ложь."""
    with pytest.raises(ConfigError) as exc:
        discover_migrations(tmp_path)
    assert "ни одного файла" in str(exc.value)


def test_admin_dsn_читается_из_окружения() -> None:
    assert admin_dsn({DATABASE_ADMIN_URL_VAR: " postgresql://x/y "}) == "postgresql://x/y"


def test_admin_dsn_пустой_это_отсутствие_а_не_пустая_строка() -> None:
    """Пустая переменная не должна выглядеть как заданный адрес."""
    assert admin_dsn({DATABASE_ADMIN_URL_VAR: "   "}) is None
    assert admin_dsn({}) is None


@requires_db
def test_пустой_пароль_роли_отказывает() -> None:
    """Пустой пароль оставил бы роль приложения без входа — молча."""
    with empty_database() as dsn:
        with pytest.raises(ConfigError) as exc:
            set_app_password(dsn, "   ")
        assert DATABASE_APP_PASSWORD_VAR in str(exc.value)
        assert APP_ROLE in str(exc.value)


@requires_db
def test_суперпользователь_опознан_как_обходящий_политики() -> None:
    """Роль прогона — суперпользователь, и раннер обязан это видеть.

    Проверка, которая не может упасть, была бы бесполезна: если бы
    `role_bypasses_rls` всегда возвращала False, защита выглядела бы включённой
    везде. Поэтому здесь два утверждения — на привилегированной роли True, на
    роли приложения False.
    """
    with empty_database() as dsn:
        assert role_bypasses_rls(dsn) is True, (
            "роль прогона привилегированная, а раннер этого не заметил — значит и в бою не заметит"
        )


@requires_db
def test_роль_приложения_политики_не_обходит() -> None:
    """Встречное утверждение: после наката роль приложения уже не всесильна."""
    with empty_database() as dsn:
        apply_migrations(dsn)
        # Пароль роли НЕ ставится: утверждение теста от него не зависит, а роль
        # приложения — объект кластера, а не этой временной базы. Пока пароль
        # здесь менялся, прогон уносил с собой стенды соседних копий: следом
        # падало около девяноста чужих тестов, а виновник оставался зелёным
        # (задача #128). Сам `set_app_password` проверяется отдельно, отказом
        # на пустой пароль — там до SQL дело не доходит.
        with psycopg.connect(dsn) as conn:
            row = conn.execute(
                "select rolsuper or rolbypassrls from pg_roles where rolname = %s",
                (APP_ROLE,),
            ).fetchone()
        assert row is not None, f"роль {APP_ROLE} не заведена накатом миграций"
        assert row[0] is False, (
            f"роль {APP_ROLE} обходит политики — запрет правки завершённых "
            "проверок на ней не сработает, а выглядеть будет включённым"
        )
