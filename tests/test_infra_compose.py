"""Стенд: демо поднимается тем же файлом и образом, что боевой (T102, D059).

Демо отставало от `main`, потому что жило отдельной ручной процедурой. Чтобы
оно не начало отставать снова, сторожатся ровно те свойства, из-за потери
которых отставание и возвращается:

* демо и продукт собираются из **одного** описания стенда — второй файл или
  второй образ разойдётся с первым, и заметно это будет на показе;
* демо **не включается само** — обычный `docker compose up -d` боевого стенда
  не тянет за собой демо-сервисы и не требует демо-переменных;
* демо **изолировано**: свой том, свой чек-лист, свой токен бота. Токен здесь
  не техническая мелочь: унаследуй демо боевой, Telegram молча отдал бы ему
  обновления настоящего бота;
* каждая демо-переменная **описана в `.env.example`** — без этого следующая
  сессия стенд не поднимет.

Конфигурация читается не парсером YAML, а самим `docker compose config`:
проверять надо то, что получится на площадке, вместе с профилями, якорями и
подстановкой из `.env`, а не то, как файл выглядит.

Значения переменных нигде не печатаются: в `.env` рядом лежит настоящий токен
боевого бота, и он не должен попасть ни в вывод прогона, ни в отчёт CI.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
COMPOSE_FILE = ROOT / "docker-compose.yml"
ENV_EXAMPLE = ROOT / ".env.example"
REFRESH = ROOT / "tools" / "demo_refresh.sh"

DEMO_SERVICES = ("demo", "demo-seed")

#: Имя проекта — своё, как требует правило параллельных копий: без него вызов
#: разговаривал бы с контейнерами соседа. Здесь ничего не поднимается, только
#: читается конфигурация, но привычку ломать нельзя.
PROJECT = "dodo_audit_service-tests"

requires_docker = pytest.mark.skipif(
    shutil.which("docker") is None,
    reason="нет docker — проверка разбирает конфигурацию его же средствами",
)


def compose(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 — аргументы собираем сами, ввода извне нет
        ["docker", "compose", "-p", PROJECT, *args],  # noqa: S607 — docker из PATH
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture(scope="module")
def resolved() -> dict[str, dict]:
    """Разобранный `docker compose config` с включённым профилем demo."""
    r = compose("--profile", "demo", "config", "--format", "json")
    assert r.returncode == 0, r.stderr
    services = json.loads(r.stdout)["services"]
    assert isinstance(services, dict)
    return services


def mounts(service: dict) -> dict[str, str]:
    """Точка монтирования → источник (имя тома или путь на хосте)."""
    return {v["target"]: str(v.get("source", "")) for v in service.get("volumes", [])}


# --- демо не включается само -------------------------------------------------


@requires_docker
def test_production_stand_does_not_pull_in_demo() -> None:
    r = compose("config", "--services")
    assert r.returncode == 0, r.stderr
    assert sorted(r.stdout.split()) == ["bot"], (
        "обычный `docker compose up -d` тянет за собой демо-сервисы: "
        "боевой стенд начнёт требовать демо-переменные и поднимать лишнее"
    )


@requires_docker
def test_demo_profile_brings_up_seed_and_bot() -> None:
    r = compose("--profile", "demo", "config", "--services")
    assert r.returncode == 0, r.stderr
    assert sorted(r.stdout.split()) == ["bot", "demo", "demo-seed"]


# --- один файл, один образ ---------------------------------------------------


@requires_docker
def test_demo_is_built_from_the_same_image_as_production(resolved: dict[str, dict]) -> None:
    """Общий Dockerfile и общий контекст сборки — это и есть «не отстаёт»."""
    builds = {
        name: (
            resolved[name]["build"]["dockerfile"],
            resolved[name]["build"]["context"],
        )
        for name in ("bot", *DEMO_SERVICES)
    }
    assert len(set(builds.values())) == 1, (
        f"демо собирается не тем же образом, что продукт, и разойдётся с ним: {builds}"
    )


# --- изоляция ----------------------------------------------------------------


@requires_docker
def test_demo_keeps_state_in_its_own_volume(resolved: dict[str, dict]) -> None:
    production_state = mounts(resolved["bot"])["/app/state"]
    for name in DEMO_SERVICES:
        demo_state = mounts(resolved[name])["/app/state"]
        assert demo_state != production_state, (
            f"{name} держит состояние в томе боевого стенда ({demo_state}): "
            f"пересев демо затрёт идущие настоящие проверки"
        )


@requires_docker
def test_demo_runs_on_the_synthetic_checklist(resolved: dict[str, dict]) -> None:
    for name in DEMO_SERVICES:
        env = resolved[name]["environment"]
        assert env["AUDIT_DATA_DIR"] == "/app/demo/data", (
            f"{name} считает по боевой методике, а не по синтетической"
        )
        assert "/app/demo/data" in mounts(resolved[name]), (
            f"{name} не монтирует demo/data — чек-лист демо взять неоткуда"
        )


@requires_docker
def test_demo_does_not_inherit_production_bot_token(resolved: dict[str, dict]) -> None:
    """Значения не печатаются: в .env рядом настоящий токен боевого бота."""
    production = resolved["bot"]["environment"].get("TELEGRAM_BOT_TOKEN") or ""
    for name in DEMO_SERVICES:
        demo = resolved[name]["environment"].get("TELEGRAM_BOT_TOKEN") or ""
        assert not (production and demo == production), (
            f"{name} унаследовал токен боевого бота из env_file: Telegram отдаёт "
            f"обновления одному получателю, и демо перехватит настоящего бота"
        )


@requires_docker
def test_demo_seed_and_demo_bot_share_state_and_data(resolved: dict[str, dict]) -> None:
    """Иначе бот покажет не то, что насеяли, — и молча."""
    seed, bot = (mounts(resolved[name]) for name in ("demo-seed", "demo"))
    assert seed["/app/state"] == bot["/app/state"]
    assert seed["/app/demo/data"] == bot["/app/demo/data"]
    seed_env = resolved["demo-seed"]["environment"]
    assert seed_env["DEMO_STATE_DIR"] == seed_env["STATE_DIR"], (
        "сид пишет не туда, откуда читает демо-бот"
    )


# --- доступы описаны ---------------------------------------------------------


def test_every_demo_variable_is_documented() -> None:
    """Проверка текстом файла, без docker: описание не зависит от площадки."""
    used = set(re.findall(r"\$\{(DEMO_[A-Z0-9_]+)", COMPOSE_FILE.read_text(encoding="utf-8")))
    assert used, "в docker-compose.yml не осталось ни одной демо-переменной"
    documented = set(
        re.findall(r"^(DEMO_[A-Z0-9_]+)=", ENV_EXAMPLE.read_text(encoding="utf-8"), re.M)
    )
    assert used <= documented, (
        f"демо-переменные не описаны в .env.example: {sorted(used - documented)}. "
        f"Без описания следующая сессия демо-стенд не поднимет"
    )


def test_refresh_script_is_runnable() -> None:
    assert REFRESH.is_file(), "нет tools/demo_refresh.sh — демо нечем догонять main"
    assert REFRESH.stat().st_mode & 0o111, "tools/demo_refresh.sh не исполняемый"
