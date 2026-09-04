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

#: Методика, по которой идёт набор тестов (T141). Синтетическая и лежит в git:
#: боевая живёт вне репозитория (D002) и её правит управляющая компания — пока
#: тесты читали её, добавленное в данные слово красило нашу сборку, и применение
#: новой карты слов требовало правки тестов тем же движением.
#:
#: Настоящие здесь только идентификаторы (коды пунктов и зон, классы, ставки
#: вычетов): ими продукт связывает сущности, и в репозитории они были и раньше.
#: Формулировки, названия зон, критерии и подсказки выдуманы —
#: см. `tests/methodology/README.md`.
TEST_DATA = ROOT / "tests" / "methodology"

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
    data_dir: Path | None = None,
) -> Run:
    """Запустить скрипт движка в отдельном процессе.

    Путь к состоянию передаётся через `INSPECTION_FILE` — тот же механизм,
    которым бот разводит проверки по чатам. `env_extra` нужен тестам, которые
    имитируют машину без рабочего рендерера PDF.

    **Методика передаётся явно и по умолчанию синтетическая** (T141). Без
    `CHECKLIST_DIR` движок берёт данные из своей копии рядом со скриптом, то
    есть из боевого `data/`, — и тогда правка методики управляющей компанией
    красит прогон. `data_dir=DATA` пишется в тесте руками и означает «этому
    тесту нужна именно боевая методика»; такой тест обязан быть помечен
    `requires_data`. Молчаливого варианта здесь нет намеренно: боевой якорь
    97.5 %/97 %, посчитанный по синтетическому набору, сошёлся бы по совпадению
    ставок и долей — и перестал бы что-либо стеречь, не покраснев ни разу.
    """
    env = dict(os.environ)
    if state is not None:
        env["INSPECTION_FILE"] = str(state)
    if data_dir is not None:
        env["CHECKLIST_DIR"] = str(data_dir)
    else:
        # `setdefault`, а не присваивание: тест, подставивший движку свою
        # методику через `monkeypatch.setenv("CHECKLIST_DIR", ...)`, должен
        # получить именно её. Из оболочки запускающего переменная сюда попасть
        # не может — её снимает `_методика_не_протекает_из_оболочки`.
        env.setdefault("CHECKLIST_DIR", str(TEST_DATA))
    env.update(env_extra or {})
    p = subprocess.run(  # noqa: S603 — аргументы собираем сами, ввода извне тут нет
        [sys.executable, str(script), *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
    )
    return Run(p.returncode, p.stdout, p.stderr)


CHECKLIST_DIR_VAR = "CHECKLIST_DIR"


@pytest.fixture(autouse=True)
def _методика_не_протекает_из_оболочки(monkeypatch: pytest.MonkeyPatch) -> None:
    """`CHECKLIST_DIR` не достаётся тесту, который её не просил (T141).

    Переменная решает, по какой методике считает движок. Оставленная в оболочке
    (например, после ручного прогона на копии), она увела бы весь набор на чужие
    данные, и падало бы это не там, где причина. Тест, которому нужна своя
    методика, ставит переменную сам — автоматическая фикстура срабатывает раньше
    запрошенных явно, и его `setenv` ложится уже поверх снятой.
    """
    monkeypatch.delenv(CHECKLIST_DIR_VAR, raising=False)


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
    """Окружение блока `domain`: синтетическая методика, состояние — во временной папке.

    Методика — `tests/methodology` (T141): боевую правит управляющая компания, и
    пока набор читал её, добавленное в данные слово красило нашу сборку. Тесту,
    которому нужна именно боевая, — фикстура `live_data_env`.

    Рабочий каталог тоже уводится во временный: блок отказывается работать при
    форке чек-листа рядом (`checklist_data/`), и проверять это на настоящем
    репозитории нельзя. Возвращается каталог состояния (`STATE_DIR`).
    """
    state = tmp_path / "state"
    monkeypatch.setenv("AUDIT_DATA_DIR", str(TEST_DATA))
    monkeypatch.setenv("STATE_DIR", str(state))
    monkeypatch.chdir(tmp_path)
    return state


@pytest.fixture
def live_data_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Окружение блока на БОЕВОЙ методике — для тестов о ней самой.

    Таких тестов должно быть мало, и каждый обязан объяснять, почему ему нужна
    именно она: боевой якорь на `examples/` (97.5 %/97 %), проверка полноты
    каталога управляющей компании, живой замер быстрого пути. Всё остальное
    идёт через `domain_env` и синтетическую методику — иначе правка данных
    управляющей компанией снова начнёт красить нашу сборку (T141).

    Такой тест обязан быть помечен `requires_data`: боевая методика лежит вне
    git (D002), и на чужой машине её нет.
    """
    state = tmp_path / "state"
    monkeypatch.setenv("AUDIT_DATA_DIR", str(DATA))
    monkeypatch.setenv("STATE_DIR", str(state))
    monkeypatch.chdir(tmp_path)
    return state


@pytest.fixture
def data_copy(tmp_path: Path) -> Path:
    """Копия синтетической методики, которую тесту можно портить."""
    dst = tmp_path / "data-copy"
    shutil.copytree(TEST_DATA, dst)
    return dst


@pytest.fixture
def live_data_copy(tmp_path: Path) -> Path:
    """Копия БОЕВОЙ методики — для тестов о ней самой (см. `live_data_env`).

    Копия, а не сам каталог: тест может запустить по ней инструмент, который
    пишет рядом свои файлы, а боевая методика лежит вне git (D002) и
    восстановить её было бы неоткуда. Такой тест обязан быть помечен
    `requires_data`.
    """
    dst = tmp_path / "live-data-copy"
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

#: Строка подключения запускающего, снятая ОДИН РАЗ при сборе тестов. Дальше
#: она из окружения убирается (`_база_не_видна_сама_собой`), поэтому читать её
#: во время теста уже неоткуда — и это единственное место, где она берётся.
BASE_DSN = os.environ.get(DATABASE_URL_VAR, "")

requires_db = pytest.mark.skipif(
    not BASE_DSN,
    reason=f"нет {DATABASE_URL_VAR} — тесты блока db идут только с настоящим Postgres рядом",
)


@pytest.fixture(autouse=True)
def _база_не_видна_сама_собой(monkeypatch: pytest.MonkeyPatch) -> None:
    """`DATABASE_URL` не достаётся тесту, который её не просил (T123).

    С задачи T123 продукт ходит в базу сам — на завершении проверки. Значит,
    любой тест бота, доводящий разговор до отчёта, при выставленной в оболочке
    переменной начал бы писать в базу разработчика, и один и тот же набор вёл
    бы себя по-разному с базой и без неё. Ровно этот разлад сверка со спекой
    однажды уже поймала, поэтому переменная выдаётся только через фикстуры
    `db_env`/`pg_dsn`, то есть тем, кто просит базу явно.

    Автоматическая фикстура ставится раньше запрошенных явно, поэтому `db_env`
    выставляет свою строку уже поверх снятой.
    """
    monkeypatch.delenv(DATABASE_URL_VAR, raising=False)


#: Язык интерфейса до начала проверки (T131). В прогоне он обязан быть таким
#: же, как на боевом стенде, — иначе набор ведёт себя по-разному у того, кто
#: держит переменную в оболочке ради демо, и у того, кто её не задаёт.
UI_LANG_VAR = "BOT_UI_LANG"


@pytest.fixture(autouse=True)
def _язык_интерфейса_не_протекает_из_оболочки(monkeypatch: pytest.MonkeyPatch) -> None:
    """`BOT_UI_LANG` не достаётся тесту, который её не просил (T131).

    Переменная задаёт язык разговора ДО начала проверки, то есть приветствие и
    весь мастер. Оставленная в оболочке (например, после ручного прогона демо),
    она перекрасила бы половину набора тестов бота в английский, и падало бы
    это не там, где причина. Тест, которому нужен язык стенда, ставит его сам.
    """
    monkeypatch.delenv(UI_LANG_VAR, raising=False)


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

    maintenance_dsn = make_conninfo(BASE_DSN, dbname="postgres")
    dbname = f"dodo_audit_test_{uuid.uuid4().hex[:12]}"
    create = sql.SQL("create database {}").format(sql.Identifier(dbname))
    drop = sql.SQL("drop database if exists {} with (force)").format(sql.Identifier(dbname))

    with psycopg.connect(maintenance_dsn, autocommit=True) as conn:
        conn.execute(create)
    try:
        dsn = make_conninfo(BASE_DSN, dbname=dbname)
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
APP_PASSWORD_VAR = "DATABASE_APP_PASSWORD"


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
