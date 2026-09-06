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
import os
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

#: Состав обычного `docker compose up -d` — без профилей. MCP-сервер здесь не
#: для полноты: до задачи T255 его в стенде не было вовсе, он поднимался руками
#: и жил до конца сессии запустившего (#210).
STAND_SERVICES = ("bot", "mcp")

#: Имя проекта — своё, как требует правило параллельных копий: без него вызов
#: разговаривал бы с контейнерами соседа. Здесь ничего не поднимается, только
#: читается конфигурация, но привычку ломать нельзя.
PROJECT = "dodo_audit_service-tests"

requires_docker = pytest.mark.skipif(
    shutil.which("docker") is None,
    reason="нет docker — проверка разбирает конфигурацию его же средствами",
)


def compose(*args: str) -> subprocess.CompletedProcess[str]:
    # COMPOSE_PROFILES гасится намеренно. Compose читает эту переменную из `.env`
    # рабочей копии, а на площадке в ней стоит `tunnel` (T256): унаследованная,
    # она включала бы профиль сама, и проверка «обычный `up -d` поднимает ровно
    # эти сервисы» отвечала бы на вопрос про чужую настройку, а не про файл.
    окружение = {**os.environ, "COMPOSE_PROFILES": ""}
    return subprocess.run(  # noqa: S603 — аргументы собираем сами, ввода извне нет
        ["docker", "compose", "-p", PROJECT, *args],  # noqa: S607 — docker из PATH
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
        env=окружение,
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
    assert sorted(r.stdout.split()) == list(STAND_SERVICES), (
        "обычный `docker compose up -d` поднимает не тот состав: демо и туннель "
        "тянуться за собой не должны, а бот и MCP-сервер обязаны быть в нём оба"
    )


@requires_docker
def test_demo_profile_brings_up_seed_and_bot() -> None:
    r = compose("--profile", "demo", "config", "--services")
    assert r.returncode == 0, r.stderr
    assert sorted(r.stdout.split()) == sorted([*STAND_SERVICES, *DEMO_SERVICES]), (
        "профиль демо меняет состав боевого стенда, а обязан только дополнять его"
    )


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


# --- MCP-сервер: сервис стенда, а не команда оператора (T255, #210) ----------
#
# Задача заведена по живой поломке: бот выдал человеку правильный доступ, все
# контейнеры были зелёными, а порт сервера не слушал никто — сервер поднимали
# руками, и жил он до конца сессии запустившего. Ниже сторожатся ровно те
# свойства, потеря которых возвращает эту поломку.


@pytest.fixture(scope="module")
def with_tunnel() -> dict[str, dict]:
    """Конфигурация с включённым профилем туннеля."""
    r = compose("--profile", "tunnel", "config", "--format", "json")
    assert r.returncode == 0, r.stderr
    services = json.loads(r.stdout)["services"]
    assert isinstance(services, dict)
    return services


@requires_docker
def test_mcp_server_is_part_of_the_stand(resolved: dict[str, dict]) -> None:
    """Обычный `up -d` поднимает сервер сам — иначе он снова забудется."""
    assert "mcp" in resolved, "MCP-сервера нет в стенде: его снова придётся поднимать руками"
    assert not resolved["mcp"].get("profiles"), (
        "MCP-сервер спрятан за профилем: обычный `docker compose up -d` его не поднимет, "
        "и подключение снаружи снова окажется настроенным в никуда"
    )


@requires_docker
def test_mcp_server_restarts_itself(resolved: dict[str, dict]) -> None:
    assert resolved["mcp"].get("restart") == "unless-stopped", (
        "сервер не перезапускается сам: перезагрузка машины оставит стенд без MCP"
    )


@requires_docker
def test_mcp_server_publishes_no_ports(with_tunnel: dict[str, dict]) -> None:
    """Главный запрет блока: в СЕТЬ порт не публикуется ничем.

    Сервер отдаёт проверки партнёров по токену, а площадка общая — рядом живут
    чужие проекты. Публикация на `0.0.0.0` отдала бы их всей сети.

    Петля хоста — исключение, и появилось оно не для удобства: площадка выходит
    наружу общим входом (D102), а он живёт на хосте и до петли КОНТЕЙНЕРА не
    дотягивается. Разница существенная: на петле хоста сервер виден тем же, кто
    и так на этой машине, а в сети — всякому соседу по ней.
    """
    for имя, сервис in with_tunnel.items():
        for порт in сервис.get("ports") or []:
            # `docker compose config` разворачивает короткую запись в словарь,
            # и адрес лежит в `host_ip`; строка остаётся строкой, когда конфиг
            # читают как есть. Разбирается оба вида — иначе тест зелен на одном
            # и слеп на другом.
            адрес = порт.get("host_ip", "") if isinstance(порт, dict) else str(порт)
            назван_петлёй = адрес == "127.0.0.1" or адрес.startswith("127.0.0.1:")
            assert назван_петлёй, (
                f"сервис {имя} публикует порт в сеть: {порт}. "
                f"Проверки партнёров становятся доступны соседям по машине"
            )


@requires_docker
def test_mcp_server_listens_on_the_container_loopback(resolved: dict[str, dict]) -> None:
    """Адрес прослушивания — петля, и назван он в самом описании стенда."""
    адрес = resolved["mcp"]["environment"].get("MCP_HOST")
    assert адрес == "127.0.0.1", (
        f"MCP_HOST у сервиса стенда = {адрес!r}. Сервер обязан слушать петлю контейнера: "
        f"внешний адрес он и сам не примет (src/mcp/config.py, _parse_host)"
    )


@requires_docker
def test_mcp_server_has_its_own_health_probe(resolved: dict[str, dict]) -> None:
    """Запечённая в образ проверка ищет процесс БОТА — серверу нужна своя.

    Без своего `healthcheck` контейнер сервера был бы нездоров всегда, а
    «нездоров всегда» читается ровно так же, как «нездоров сейчас», то есть не
    читается вовсе.
    """
    проба = resolved["mcp"].get("healthcheck") or {}
    команда = " ".join(проба.get("test") or [])
    assert "tools/mcp_healthcheck.py" in команда, (
        f"у сервера нет своей пробы здоровья: {команда!r}. Проверка из образа ищет "
        f"процесс бота (`python -m src.bot`) и для сервера красна всегда"
    )
    assert "src.bot" not in команда


@requires_docker
def test_mcp_server_never_writes_the_inspection_state(resolved: dict[str, dict]) -> None:
    """Полка изданий принадлежит идущим проверкам: сервер её только читает."""
    состояние = [v for v in resolved["mcp"]["volumes"] if v["target"] == "/app/state"]
    assert состояние, "серверу не смонтирована полка изданий — пересборка письма откажет"
    assert состояние[0].get("read_only") is True, (
        "том состояния смонтирован серверу на запись: он читает полку снимков методики "
        "и писать в состояние идущих проверок не имеет права ничем"
    )


@requires_docker
def test_mcp_server_and_bot_share_one_image(resolved: dict[str, dict]) -> None:
    """Один образ на бот и на сервер: второй Dockerfile разошёлся бы с первым."""
    бот, сервер = resolved["bot"]["build"], resolved["mcp"]["build"]
    assert (бот["dockerfile"], бот["context"]) == (сервер["dockerfile"], сервер["context"])


@requires_docker
def test_mcp_server_waits_for_the_database_without_demanding_it(resolved: dict[str, dict]) -> None:
    """Зависимость от базы есть, но стенд без профиля `db` она не ломает.

    База — источник всего, что сервер читает. При этом жить она может и снаружи
    compose (D061), поэтому зависимость обязана быть необязательной: жёсткая
    сделала бы обычный `docker compose up -d` невозможным.
    """
    зависимости = resolved["mcp"].get("depends_on") or {}
    assert "db" in зависимости, "сервер не ждёт базу: он ответит пустотой на живой вопрос"
    assert зависимости["db"].get("condition") == "service_healthy"
    assert зависимости["db"].get("required") is False, (
        "зависимость от базы жёсткая: стенд без профиля `db` перестанет подниматься целиком"
    )


# --- туннель наружу (T256, #211, решение D100) -------------------------------


@requires_docker
def test_tunnel_is_not_pulled_in_by_the_plain_stand(resolved: dict[str, dict]) -> None:
    """Туннелю нужен свой секрет, и на ноутбуке он не нужен вовсе."""
    assert "tunnel" not in resolved, (
        "туннель поднимается обычным `up -d`: на стенде без секрета он будет "
        "перезапускаться вечно и мешать читать состояние остальных сервисов"
    )


@requires_docker
def test_tunnel_reaches_the_server_only_through_its_loopback(with_tunnel: dict[str, dict]) -> None:
    """Туннель живёт в сетевом пространстве сервера — иначе пришлось бы открывать порт.

    Это и есть механизм решения D100: другого пути к серверу не появляется, а
    открыть его пришлось бы, слушай сервер внешний адрес.
    """
    assert with_tunnel["tunnel"].get("network_mode") == "service:mcp", (
        "туннель ходит к серверу не через петлю его контейнера. Любой другой путь "
        "требует, чтобы сервер слушал внешний адрес, — а он этого не делает"
    )


@requires_docker
def test_tunnel_secret_never_reaches_the_process_arguments(with_tunnel: dict[str, dict]) -> None:
    """Секрет — переменной, не аргументом: `ps` на общей машине видят все."""
    туннель = with_tunnel["tunnel"]
    команда = " ".join(туннель.get("command") or [])
    assert "--token" not in команда, (
        "токен туннеля уходит аргументом команды: список процессов на общей площадке "
        "читается любым соседом"
    )
    assert "TUNNEL_TOKEN" in (туннель.get("environment") or {})


@requires_docker
def test_tunnel_version_is_pinned(with_tunnel: dict[str, dict]) -> None:
    """Обновление туннеля — осознанное изменение, а не то, что подтянулось само."""
    образ = with_tunnel["tunnel"]["image"]
    assert ":" in образ and not образ.endswith(":latest"), (
        f"версия туннеля не прибита: {образ}. Подменившийся между двумя `up` бинарник — "
        f"это смена того, через что ходит доступ к проверкам партнёров"
    )


@requires_docker
def test_every_tunnel_variable_is_documented() -> None:
    """Та же проверка, что у демо-переменных, и по той же причине."""
    в_стенде = set(
        re.findall(r"\$\{(CLOUDFLARE_[A-Z0-9_]+)", COMPOSE_FILE.read_text(encoding="utf-8"))
    )
    assert в_стенде, "в docker-compose.yml не осталось переменных туннеля"
    описано = set(re.findall(r"^([A-Z][A-Z0-9_]+)=", ENV_EXAMPLE.read_text(encoding="utf-8"), re.M))
    assert в_стенде <= описано, (
        f"переменные туннеля не описаны в .env.example: {sorted(в_стенде - описано)}"
    )
    assert "COMPOSE_PROFILES" in описано, (
        "COMPOSE_PROFILES не описан: без него человек на площадке не узнает, чем "
        "включается туннель, и подключение снаружи молча не заработает"
    )
