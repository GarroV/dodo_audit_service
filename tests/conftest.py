"""Общая оснастка тестов движка.

Движок запускается подпроцессом — так же, как его будет звать `domain` (см.
`docs/forge/plan.md`, раздел «Архитектура»). Импортировать его в тесты нельзя:
контракт import-linter запрещает импорт `engine` из кода продукта, и тесты не
должны учить обходить это правило.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
AUDIT = ROOT / "engine" / "audit.py"
REPORT = ROOT / "engine" / "report.py"
DATA = ROOT / "data"
EXAMPLES = ROOT / "examples"

# Причина пропуска одна на все тесты: методика и боевые проверки лежат вне git
# (решение D002), поэтому на чужой машине их может не быть.
NO_DATA = not (DATA / "checklist.csv").exists()
NO_EXAMPLES = not (EXAMPLES / "belgrade-1" / "inspection.json").exists()

requires_data = pytest.mark.skipif(
    NO_DATA, reason="нет data/checklist.csv — методика вне git (D002)"
)
requires_examples = pytest.mark.skipif(
    NO_EXAMPLES, reason="нет examples/belgrade-1 — боевые проверки вне git (D002)"
)


@dataclass(frozen=True)
class Run:
    """Результат запуска команды движка."""

    code: int
    out: str
    err: str

    @property
    def text(self) -> str:
        return self.out + self.err


def run_engine(
    script: Path,
    *args: str,
    cwd: Path,
    state: Path | None = None,
    env_extra: dict[str, str] | None = None,
) -> Run:
    """Запустить скрипт движка в отдельном процессе.

    Путь к состоянию передаётся через `INSPECTION_FILE` — тот же механизм,
    которым бот будет разводить проверки по чатам. `env_extra` нужен тестам,
    которые имитируют машину без рабочего рендерера PDF.
    """
    env = dict(os.environ)
    if state is not None:
        env["INSPECTION_FILE"] = str(state)
    env.update(env_extra or {})
    p = subprocess.run(  # noqa: S603 — аргументы собираем сами, ввода извне тут нет
        [sys.executable, str(script), *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
    )
    return Run(p.returncode, p.stdout, p.stderr)


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    """Пустая рабочая папка проверки — аналог папки на чат."""
    d = tmp_path / "chat-1"
    d.mkdir()
    return d


@pytest.fixture
def audit(workdir: Path) -> Callable[..., Run]:
    """Вызов `audit.py` в рабочей папке с состоянием `inspection.json` в ней."""

    def call(*args: str) -> Run:
        return run_engine(AUDIT, *args, cwd=workdir, state=workdir / "inspection.json")

    return call


@pytest.fixture
def report(workdir: Path) -> Callable[..., Run]:
    """Вызов `report.py` в той же рабочей папке."""

    def call(*args: str, env_extra: dict[str, str] | None = None) -> Run:
        return run_engine(
            REPORT, *args, cwd=workdir, state=workdir / "inspection.json", env_extra=env_extra
        )

    return call


@pytest.fixture
def started(audit: Callable[..., Run]) -> Callable[..., Run]:
    """Начатая проверка: минимальная шапка, дальше можно добавлять записи."""
    r = audit("init", "--unit", "Тестовая", "--auditor", "Тест", "--date", "2026-08-21")
    assert r.code == 0, r.text
    return audit


@pytest.fixture
def no_renderer(tmp_path: Path) -> dict[str, str]:
    """Окружение, в котором ни один рендерер PDF не работает.

    Проверяем именно поведение при провале сборки: заглушка `wkhtmltopdf`
    падает, chromium в урезанном PATH не находится, а импорт `weasyprint`
    подменён модулем, который бросает исключение.
    """
    stub_bin = tmp_path / "stub-bin"
    stub_bin.mkdir()
    wk = stub_bin / "wkhtmltopdf"
    wk.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    wk.chmod(0o755)
    stub_lib = tmp_path / "stub-lib"
    stub_lib.mkdir()
    (stub_lib / "weasyprint.py").write_text(
        "raise ImportError('рендерер недоступен — заглушка теста')\n", encoding="utf-8"
    )
    return {"PATH": f"{stub_bin}:/usr/bin:/bin", "PYTHONPATH": str(stub_lib)}


@pytest.fixture
def domain_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Окружение блока `domain`: боевая методика, состояние — во временной папке.

    Рабочий каталог тоже уводится во временный: блок отказывается работать при
    форке чек-листа рядом (`checklist_data/`), и проверять это на настоящем
    репозитории нельзя. Возвращается каталог состояния (`STATE_DIR`).
    """
    state = tmp_path / "state"
    monkeypatch.setenv("AUDIT_DATA_DIR", str(DATA))
    monkeypatch.setenv("STATE_DIR", str(state))
    monkeypatch.chdir(tmp_path)
    return state


@pytest.fixture
def data_copy(tmp_path: Path) -> Path:
    """Копия каталога методики, которую тесту можно портить."""
    dst = tmp_path / "data-copy"
    shutil.copytree(DATA, dst)
    return dst


# --- Оснастка блока `db` (T091, T093) ----------------------------------------
#
# `psycopg` — зависимость этого блока (добавлена в pyproject.toml), а не
# всего проекта, поэтому ниже он импортируется только внутри тела фикстуры, а
# не на уровне модуля: голый `import psycopg` здесь означал бы, что сбор
# ВСЕГО `tests/` падает целиком, пока зависимость не поставлена в текущее
# окружение прогона (проверено: без этого приёма `pytest tests` не собирает
# ни одного теста, включая те, что к `db` отношения не имеют).

DATABASE_URL_VAR = "DATABASE_URL"

requires_db = pytest.mark.skipif(
    not os.environ.get(DATABASE_URL_VAR),
    reason=f"нет {DATABASE_URL_VAR} — тесты блока db идут только с настоящим Postgres рядом",
)


@pytest.fixture
def pg_dsn() -> Iterator[str]:
    """DSN одноразовой базы данных со свежо накатанной схемой блока `db`.

    Имя — случайное (`dodo_audit_test_<hex>`), поэтому параллельный тестовый
    прогон не топчет чужую базу того же имени. Обслуживающее подключение идёт
    к базе `postgres` — из своей же базы `DROP DATABASE` не выполнить. Ни
    настоящие данные проекта, ни база любого другого блока не трогаются:
    каждый тест поднимает и удаляет полностью свою.
    """
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo

    from src.db.migrate import apply_migrations

    base = os.environ[DATABASE_URL_VAR]
    maintenance_dsn = make_conninfo(base, dbname="postgres")
    dbname = f"dodo_audit_test_{uuid.uuid4().hex[:12]}"
    create = sql.SQL("create database {}").format(sql.Identifier(dbname))
    drop = sql.SQL("drop database if exists {} with (force)").format(sql.Identifier(dbname))

    with psycopg.connect(maintenance_dsn, autocommit=True) as conn:
        conn.execute(create)
    try:
        dsn = make_conninfo(base, dbname=dbname)
        apply_migrations(dsn)
        yield dsn
    finally:
        with psycopg.connect(maintenance_dsn, autocommit=True) as conn:
            conn.execute(drop)


#: Роль приложения (миграция `0004`). Тесты блока идут под ней, а не под
#: суперпользователем, и это не педантизм: суперпользователь обходит RLS
#: всегда, поэтому под ним любая проверка политик зелена по неверной причине.
APP_ROLE = "dodo_audit_app"

#: Пароль роли приложения, если на этой машине к Postgres ходят по паролю.
#: Пусто — подключение идёт без него (peer/trust), как на машине разработчика.
APP_PASSWORD_VAR = "DATABASE_APP_PASSWORD"  # noqa: S105 — имя переменной, не секрет


def app_role_dsn(admin_dsn: str) -> str:
    """Та же база, но под ролью приложения.

    Пароль привилегированной роли отбрасывается намеренно: он от другой роли, и
    подставленный сюда дал бы отказ аутентификации вместо понятного «у роли
    приложения нет пароля».
    """
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    params = {k: v for k, v in conninfo_to_dict(admin_dsn).items() if k != "password"}
    params["user"] = APP_ROLE
    password = os.environ.get(APP_PASSWORD_VAR)
    if password:
        params["password"] = password
    return make_conninfo(**params)


@pytest.fixture
def db_env(pg_dsn: str, monkeypatch: pytest.MonkeyPatch) -> str:
    """`DATABASE_URL` тестовой базы — так, как его читает `db.config.check_environment`.

    Подключение идёт под ролью приложения, а не под той, что создавала базу:
    именно так продукт ходит в базу на площадке (T111). Весь остальной набор
    тестов блока заодно становится проверкой того, что выданных этой роли прав
    хватает на настоящую работу — слив, справочник, выгрузку кадров.
    """
    dsn = app_role_dsn(pg_dsn)
    monkeypatch.setenv(DATABASE_URL_VAR, dsn)
    return dsn
