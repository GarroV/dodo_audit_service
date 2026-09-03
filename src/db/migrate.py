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
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg import sql

from .errors import ConfigError

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

#: Непривилегированная роль приложения (миграция `0004`). Продукт ходит в базу
#: под ней, накат — под привилегированной: суперпользователь обходит RLS
#: всегда, и приложение под ним означало бы политики, которые ничего не держат.
APP_ROLE = "dodo_audit_app"

#: Строка подключения для НАКАТА. Отдельная от `DATABASE_URL` потому, что
#: `DATABASE_URL` — это подключение приложения, и права у него намеренно
#: маленькие: заводить роли и менять схему оно не должно. Не задана — накат
#: идёт по `DATABASE_URL`, как раньше: на машине разработчика там обычно
#: собственный суперпользователь Postgres, и вторая переменная не нужна.
DATABASE_ADMIN_URL_VAR = "DATABASE_ADMIN_URL"

#: Пароль роли приложения. В миграции его нет и быть не может — секретам не
#: место в git (конституция); сюда он приходит из `.env`, а накат ставит его
#: роли. Не задан — пароль не трогается вовсе: локально ходят по peer/trust.
DATABASE_APP_PASSWORD_VAR = "DATABASE_APP_PASSWORD"  # noqa: S105 — имя переменной, не секрет

#: `.env` рядом с проектом, а не найденный поиском от текущего каталога:
#: `make migrate` зовут из корня, а руками — откуда придётся, и предсказуемость
#: здесь дороже удобства. Тот же приём и та же причина, что в
#: `src/bot/__main__.py`.
DOTENV_PATH = Path(__file__).resolve().parents[2] / ".env"

#: Имя файла миграции: четырёхзначный номер, подчёркивание, короткое имя.
#: Номер обязателен и фиксированной ширины, потому что порядок наката задаёт
#: сортировка по имени: без нуля впереди `10_` встанет раньше `9_`.
MIGRATION_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")

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
    """Миграции по имени файла — числовой префикс задаёт порядок наката.

    История схемы обязана быть линейной: один номер — один файл. Два файла с
    одним номером («два листа») появляются сами, когда две ветки независимо
    добавили `0002_*.sql`, и опасны именно тем, что на машине, где одна из
    них уже накатана, всё выглядит рабочим — падает накат на чистой базе.
    Поэтому расхождение ловится здесь, а не на площадке.
    """
    if not directory.is_dir():
        raise ConfigError(f"Каталог миграций не найден: {directory}")
    files = sorted(p for p in directory.glob("*.sql") if p.is_file())
    if not files:
        raise ConfigError(f"В каталоге миграций нет ни одного файла: {directory}")
    migrations = []
    numbers: dict[str, str] = {}
    for path in files:
        match = MIGRATION_NAME.match(path.name)
        if match is None:
            raise ConfigError(
                f"Файл {path.name} в каталоге миграций назван не по правилу "
                f"NNNN_имя.sql — место такого файла в истории схемы неизвестно, "
                f"а порядок наката задаётся именно номером"
            )
        number = match.group(1)
        earlier = numbers.get(number)
        if earlier is not None:
            raise ConfigError(
                f"История схемы разошлась на два листа: номер {number} занят "
                f"сразу двумя файлами — {earlier} и {path.name}. Порядок наката "
                f"между ними ничем не задан, а на уже накатанной базе это не "
                f"видно вовсе: чинить переименованием одного из них в следующий "
                f"свободный номер"
            )
        numbers[number] = path.name
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


def load_env_file(path: Path = DOTENV_PATH) -> None:
    """Подставить переменные из `.env` в окружение процесса.

    Без этого `make migrate` падал с «не задана DATABASE_URL» у всех, кроме
    того, кто держал переменную в своей оболочке: `config.load_settings`
    смотрит только `os.environ`, а класть туда файл было некому. Комментарий в
    Makefile при этом обещал обратное — «DATABASE_URL берётся из .env».

    Уже стоящие в окружении переменные не перетираются (`override=False` по
    умолчанию): в контейнере их ставит `docker compose`, и файла там нет
    вовсе — тогда вызов просто ничего не делает.
    """
    load_dotenv(path)


def admin_dsn(env: Mapping[str, str] | None = None) -> str | None:
    """Строка подключения для наката, если она задана отдельно от приложения.

    `None` — отдельной нет, накатывать нужно по `DATABASE_URL`. Возврат
    вместо тихой подстановки: решение «чем накатывать» принимает вызывающий,
    а не эта функция где-то в глубине.
    """
    src = os.environ if env is None else env
    dsn = (src.get(DATABASE_ADMIN_URL_VAR) or "").strip()
    return dsn or None


def set_app_password(dsn: str, password: str) -> None:
    """Поставить роли приложения пароль из окружения.

    Пароль подставляется `sql.Literal`, а не форматированием строки: пароль
    приходит из `.env`, то есть извне кода, и склеенный руками `alter role`
    был бы ровно тем местом, где кавычка в пароле превращается в чужой SQL.

    Пустой пароль — отказ, а не «оставим как есть»: роль без пароля на стенде
    с парольной аутентификацией просто не войдёт, и разбираться в этом будут
    по невнятной ошибке подключения, а не здесь.
    """
    if not password.strip():
        raise ConfigError(
            f"{DATABASE_APP_PASSWORD_VAR} задана пустой. Либо убрать переменную "
            f"совсем (тогда пароль роли {APP_ROLE} не трогается), либо задать "
            f"настоящий пароль — пустой оставит роль без входа"
        )
    statement = sql.SQL("alter role {} password {}").format(
        sql.Identifier(APP_ROLE), sql.Literal(password)
    )
    with psycopg.connect(dsn) as conn:
        conn.execute(statement)
        conn.commit()


def role_bypasses_rls(dsn: str) -> bool:
    """Обходит ли роль этого подключения построчные политики.

    Нужна ровно для одного: сказать вслух, что защита завершённых проверок не
    действует. Суперпользователь обходит RLS всегда, и приложение, пущенное под
    ним, выглядит защищённым, ничем таковым не являясь.
    """
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "select rolsuper or rolbypassrls from pg_roles where rolname = current_user"
        )
        row = cur.fetchone()
    return bool(row and row[0])


def _warn_if_app_role_bypasses_rls(app_url: str) -> None:
    """Проверить подключение приложения и предупредить, если оно всесильно."""
    try:
        if role_bypasses_rls(app_url):
            print(
                f"ВНИМАНИЕ: роль подключения приложения обходит построчные политики "
                f"(суперпользователь или bypassrls). Запрет правки завершённых "
                f"проверок из миграции 0004 на ней НЕ ДЕЙСТВУЕТ. Приложение должно "
                f"ходить под ролью {APP_ROLE} — см. .env.example, DATABASE_URL."
            )
    except psycopg.Error as exc:
        # Молча это глотать нельзя: непроверенное — не значит безопасное.
        print(
            f"Не удалось проверить, обходит ли роль приложения политики "
            f"({type(exc).__name__}): {exc}"
        )


def main() -> None:  # pragma: no cover — тонкая обёртка CLI, части проверены по отдельности
    from .config import check_environment

    load_env_file()
    app_url = check_environment().dsn
    dsn = admin_dsn() or app_url
    applied = apply_migrations(dsn)
    if applied:
        print("Применены миграции: " + ", ".join(applied))
    else:
        print("Схема уже актуальна, накатывать нечего")

    password = (os.environ.get(DATABASE_APP_PASSWORD_VAR) or "").strip()
    if password:
        set_app_password(dsn, password)
        print(f"Пароль роли приложения {APP_ROLE} обновлён из {DATABASE_APP_PASSWORD_VAR}")

    _warn_if_app_role_bypasses_rls(app_url)


if __name__ == "__main__":  # pragma: no cover
    main()
