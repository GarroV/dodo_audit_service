"""T090: стенд базы поднимается и накатывается одной командой, кем угодно.

Проверяется не «есть ли в файле такие строчки», а три свойства, потеря
которых стоит вечера следующему человеку:

* **площадка живёт в `DATABASE_URL` и больше нигде.** Зашитая в код строка
  подключения переживает переезд молча: код продолжает работать — просто не с
  той базой. Переезд на Supabase по D061 обязан стоить одну переменную;
* **накат берёт эту переменную из `.env`**, а не требует помнить про `export`.
  Команда, которая падает у всех, кроме автора, — это не «одна команда»;
* **Postgres рядом с ботом поднимается тем же `docker compose`**, своим
  профилем, со своим томом и не выставляя себя в сеть.

Значения переменных здесь не печатаются никогда: рядом в `.env` лежит боевой
токен бота.
"""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

# `psycopg` — зависимость блока `db`, а не всего проекта: без этой строки сбор
# файла падает целиком там, где её ещё не поставили (тот же приём и та же
# причина, что в `tests/conftest.py`).
pytest.importorskip("psycopg")

from src.db.config import check_environment, load_settings
from src.db.errors import ConfigError
from src.db.migrate import DOTENV_PATH, load_env_file

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
COMPOSE_FILE = ROOT / "docker-compose.yml"
ENV_EXAMPLE = ROOT / ".env.example"
MAKEFILE = ROOT / "Makefile"

#: Своё имя проекта: без него вызов разговаривал бы с контейнерами соседней
#: рабочей копии. Здесь ничего не поднимается, только читается конфигурация.
PROJECT = "dodo_audit_service-tests"

#: Состав обычного `docker compose up -d` — без профилей. Бот и MCP-сервер: с
#: задачи T255 сервер тоже сервис стенда, а не команда оператора (#210).
STAND_SERVICES = ("bot", "mcp")

#: Схема строки подключения Postgres в любом её написании.
DSN_LITERAL = re.compile(r"(?i)\bpostgres(?:ql)?://")

requires_docker = pytest.mark.skipif(
    shutil.which("docker") is None,
    reason="нет docker — проверка разбирает конфигурацию его же средствами",
)


def compose(*args: str) -> subprocess.CompletedProcess[str]:
    # COMPOSE_PROFILES гасится намеренно: compose читает эту переменную из `.env`
    # рабочей копии, а на площадке в ней стоит `tunnel` (T256). Унаследованная,
    # она включала бы профиль сама, и проверка состава стенда отвечала бы на
    # вопрос про чужую настройку, а не про файл.
    окружение = {**os.environ, "COMPOSE_PROFILES": ""}
    return subprocess.run(  # noqa: S603 — аргументы собираем сами, ввода извне нет
        ["docker", "compose", "-p", PROJECT, *args],  # noqa: S607 — docker из PATH
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
        env=окружение,
    )


# --- площадка живёт только в переменной окружения ----------------------------


def _зашитые_строки_подключения(path: Path) -> list[str]:
    """Литералы с DSN в коде. Строки-документации не считаются: там пример уместен."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    docstrings = {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
    }
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
        and DSN_LITERAL.search(node.value)
    ]


def test_строка_подключения_нигде_не_зашита_в_коде() -> None:
    найдено = {
        str(path.relative_to(ROOT)): зашитые
        for path in sorted(SRC.rglob("*.py"))
        if (зашитые := _зашитые_строки_подключения(path))
    }
    assert not найдено, (
        f"в коде продукта зашита строка подключения: {найдено}. Переезд базы "
        f"обязан стоить одну переменную окружения (D061), а зашитый адрес "
        f"переживёт переезд молча — код продолжит работать не с той базой"
    )


def test_без_переменной_окружения_подставляется_не_база_по_умолчанию_а_отказ() -> None:
    """Тихая работа против случайной базы хуже явного отказа на старте."""
    with pytest.raises(ConfigError, match="DATABASE_URL"):
        load_settings({})


def test_накат_берёт_строку_подключения_из_env_файла(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """«Одной командой» — это без `export` в голове у запускающего.

    Проверяется то самое место: файл `.env` реально попадает в окружение
    процесса и его видит `check_environment`. Проверка «раннер не упал» этого
    бы не показала — она была бы зелёной и при вовсе не прочитанном файле,
    если переменная уже стояла в оболочке.
    """
    monkeypatch.delenv("DATABASE_URL", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DATABASE_URL=postgresql://nobody@host.invalid.example:1/dotenv_probe\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        check_environment()

    load_env_file(env_file)
    assert check_environment().dsn.endswith("dotenv_probe"), (
        "накат не увидел DATABASE_URL из .env — команда будет падать у всех, "
        "кроме того, кто держит переменную в своей оболочке"
    )


def test_env_файл_ищется_рядом_с_проектом_а_не_в_текущем_каталоге() -> None:
    """`make migrate` зовут из корня, руками — откуда придётся."""
    assert DOTENV_PATH == ROOT / ".env"


# --- стенд Postgres в docker compose -----------------------------------------


@pytest.fixture(scope="module")
def resolved_db() -> dict[str, dict]:
    r = compose("--profile", "db", "config", "--format", "json")
    assert r.returncode == 0, r.stderr
    services = json.loads(r.stdout)["services"]
    assert isinstance(services, dict)
    return services


@pytest.fixture(scope="module")
def resolved_storage() -> dict[str, dict]:
    r = compose("--profile", "storage", "config", "--format", "json")
    assert r.returncode == 0, r.stderr
    services = json.loads(r.stdout)["services"]
    assert isinstance(services, dict)
    return services


@requires_docker
def test_профиль_db_поднимает_постгрес_рядом_с_ботом() -> None:
    r = compose("--profile", "db", "config", "--services")
    assert r.returncode == 0, r.stderr
    assert sorted(r.stdout.split()) == sorted([*STAND_SERVICES, "db"]), (
        "профиль `db` обязан поднимать базу ВМЕСТЕ со стендом: база сама по себе "
        "никому не нужна, а бот без неё не сольёт завершённую проверку"
    )


@requires_docker
def test_постгрес_не_выставляется_в_сеть(resolved_db: dict[str, dict]) -> None:
    """Площадка общая: рядом живут чужие проекты, и порт на 0.0.0.0 отдаёт базу им."""
    for port in resolved_db["db"].get("ports", []):
        assert port.get("host_ip") in {"127.0.0.1", "::1"}, (
            f"порт базы опубликован на {port.get('host_ip') or 'все интерфейсы'} — "
            f"на общей площадке это открытая наружу база проверок"
        )


@requires_docker
def test_у_постгреса_свой_том_отдельно_от_состояния_проверок(
    resolved_db: dict[str, dict],
) -> None:
    состояние = {
        str(v.get("source", ""))
        for v in resolved_db["bot"]["volumes"]
        if v["target"] == "/app/state"
    }
    данные = {
        str(v.get("source", "")) for v in resolved_db["db"]["volumes"] if v.get("type") == "volume"
    }
    assert данные, "база держит данные внутри контейнера: пересоздание сотрёт все проверки"
    assert not (данные & состояние), (
        f"база и состояние идущих проверок делят один том ({данные & состояние})"
    )


@requires_docker
def test_у_постгреса_есть_проверка_готовности(resolved_db: dict[str, dict]) -> None:
    """Без неё «одна команда» гонит накат в ещё не поднявшуюся базу."""
    healthcheck = resolved_db["db"].get("healthcheck") or {}
    assert healthcheck.get("test"), "у сервиса db нет healthcheck"


# --- стенд хранилища кадров --------------------------------------------------


@requires_docker
def test_профиль_storage_поднимает_хранилище_рядом_с_ботом() -> None:
    r = compose("--profile", "storage", "config", "--services")
    assert r.returncode == 0, r.stderr
    assert sorted(r.stdout.split()) == sorted([*STAND_SERVICES, "storage"])


@requires_docker
def test_хранилище_не_выставляется_в_сеть(resolved_storage: dict[str, dict]) -> None:
    for port in resolved_storage["storage"].get("ports", []):
        assert port.get("host_ip") in {"127.0.0.1", "::1"}, (
            f"порт хранилища опубликован на {port.get('host_ip') or 'все интерфейсы'} — "
            f"на общей площадке это открытые наружу кадры проверок"
        )


@requires_docker
def test_у_хранилища_есть_проверка_готовности(resolved_storage: dict[str, dict]) -> None:
    """JVM поднимается несколько секунд: открытый порт ещё не значит рабочий S3 API."""
    healthcheck = resolved_storage["storage"].get("healthcheck") or {}
    assert healthcheck.get("test"), "у сервиса storage нет healthcheck"


@requires_docker
def test_ни_один_стенд_не_включается_сам(resolved_db: dict[str, dict]) -> None:
    """Обычный `docker compose up -d` не обязан поднимать ни базу, ни хранилище."""
    r = compose("config", "--services")
    assert r.returncode == 0, r.stderr
    assert sorted(r.stdout.split()) == list(STAND_SERVICES), (
        "стенды базы, хранилища или туннеля активны без своего профиля: боевой "
        "запуск начнёт требовать их переменные и поднимать лишнее"
    )


# --- доступы описаны ---------------------------------------------------------


@pytest.mark.parametrize("префикс", ["POSTGRES_", "S3_"])
def test_каждая_переменная_стендов_описана_в_env_example(префикс: str) -> None:
    """Проверка текстом файла, без docker: описание не зависит от площадки."""
    used = set(re.findall(rf"\$\{{({префикс}[A-Z0-9_]+)", COMPOSE_FILE.read_text(encoding="utf-8")))
    assert used, f"в docker-compose.yml не осталось ни одной переменной {префикс}*"
    documented = set(
        re.findall(rf"^({префикс}[A-Z0-9_]+)=", ENV_EXAMPLE.read_text(encoding="utf-8"), re.M)
    )
    assert used <= documented, (
        f"переменные стенда не описаны в .env.example: {sorted(used - documented)}. "
        f"Без описания следующая сессия стенд не поднимет"
    )


@pytest.mark.parametrize("цель_сноса", ["db-down", "storage-down"])
def test_снос_стенда_есть_отдельной_целью_и_без_флага_томов(цель_сноса: str) -> None:
    """`--profile <любой> down -v` уносит боевой том состояния — сносить поимённо."""
    makefile = MAKEFILE.read_text(encoding="utf-8")
    assert re.search(rf"^{цель_сноса}:", makefile, re.M), (
        f"нет цели `make {цель_сноса}`: единственной напрашивающейся командой сноса "
        f"останется `docker compose --profile ... down -v`, а она удаляет ВСЕ тома "
        f"проекта, включая состояние идущих проверок"
    )
    цель = makefile.split(f"\n{цель_сноса}:", 1)[1].split("\n\n", 1)[0]
    assert " -v" not in цель and "--volumes" not in цель, (
        f"в цели {цель_сноса} есть удаление всех томов проекта: {цель!r}"
    )
