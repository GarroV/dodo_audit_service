"""Язык интерфейса ДО начала проверки — параметр развёртывания (T131).

После старта язык интерфейса берётся из самой проверки (`Inspection.ui_lang`),
и это работало. Но до старта проверки нет, брать язык неоткуда — и он был
зашит константой `ru`. На демо-стенде это единственное оставшееся русское
место: сама проверка, чек-лист, отчёт и письмо уже английские, а первое, что
видит человек, открывший демо, — приветствие и мастер начала проверки.

Проверяется поэтому не только строка приветствия, а весь путь до старта
проверки: приветствие, кнопка входа, вопросы мастера и надписи на его кнопках.
Русская кнопка под английским текстом — та же поломка, только незаметнее.

Три языка проекта разные, и здесь их легко перепутать: вид проверки
(«Плановая») стоит на кнопке мастера — это интерфейс, — но уезжает в шапку
отчёта партнёру, а это язык отчёта. Поэтому у него отдельный случай.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from bot_harness import AUDITOR_ID, CHAT_ID, callback_query, feed, make_bot, text_message

from src.bot.app import build_dispatcher
from src.bot.config import UI_LANG_VAR, BotSettings, load_bot_settings
from src.bot.errors import BotConfigError, BotTextError
from src.bot.keyboards import NEW_INSPECTION_CALLBACK
from src.bot.texts import default_ui_lang, t
from src.domain import check_environment, get_state
from src.domain.engine import state_file
from src.domain.kinds import kind_title

ROOT = Path(__file__).resolve().parent.parent
ENV_EXAMPLE = ROOT / ".env.example"
COMPOSE_PROJECT = "dodo_audit_service-tests"

requires_docker = pytest.mark.skipif(
    shutil.which("docker") is None,
    reason="нет docker — разбор конфигурации стенда идёт его же средствами",
)


def settings() -> BotSettings:
    return BotSettings(
        token="unused-in-tests",
        allowed_ids=frozenset({AUDITOR_ID}),
        mode="polling",
        auditor_names={},
    )


async def walk_wizard_to_language(dp: Any, bot: Any) -> None:
    """Мастер до последнего вопроса: вход → название → вид проверки."""
    await feed(dp, bot, text_message("/start"))
    await feed(dp, bot, callback_query(NEW_INSPECTION_CALLBACK))
    await feed(dp, bot, text_message("Demo Pizzeria #1"))
    await feed(dp, bot, callback_query("start:kind:planned"))


# --- значение переменной ------------------------------------------------------


def test_variable_not_set_keeps_the_russian_default() -> None:
    """Боевой стенд не должен сменить язык молча: без переменной всё как было."""
    assert default_ui_lang({}) == "ru"
    assert default_ui_lang({UI_LANG_VAR: "   "}) == "ru"


def test_variable_sets_the_language_before_the_inspection_starts() -> None:
    assert default_ui_lang({UI_LANG_VAR: "en"}) == "en"


def test_unknown_language_is_refused_not_silently_russian() -> None:
    """Опечатка в переменной обязана быть слышной: иначе демо тихо станет русским."""
    with pytest.raises(BotTextError, match="sr"):
        default_ui_lang({UI_LANG_VAR: "sr"})


def test_unknown_language_stops_the_bot_at_startup() -> None:
    """Отказ приходит на старте, а не на первом сообщении аудитора."""
    env = {
        "TELEGRAM_BOT_TOKEN": "111111:AAA",
        "ALLOWED_TELEGRAM_IDS": "4242",
        UI_LANG_VAR: "sr",
    }
    with pytest.raises(BotConfigError, match="sr"):
        load_bot_settings(env)


def test_known_language_starts_the_bot() -> None:
    env = {
        "TELEGRAM_BOT_TOKEN": "111111:AAA",
        "ALLOWED_TELEGRAM_IDS": "4242",
        UI_LANG_VAR: "en",
    }
    assert load_bot_settings(env).ui_lang == "en"


# --- разговор до начала проверки ---------------------------------------------


@pytest.mark.asyncio
async def test_greeting_stays_russian_without_the_variable(domain_env: object) -> None:
    bot, session = make_bot()
    dp = build_dispatcher(settings())

    await feed(dp, bot, text_message("/start"))

    assert session.last_text == t("start.greeting", "ru")
    assert session.keyboard_texts() == [t("btn.new_inspection", "ru")]


@pytest.mark.asyncio
async def test_greeting_and_its_button_speak_the_deployment_language(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Первое, что видит человек на демо: и текст, и кнопка под ним."""
    monkeypatch.setenv(UI_LANG_VAR, "en")
    bot, session = make_bot()
    dp = build_dispatcher(settings())

    await feed(dp, bot, text_message("/start"))

    assert session.last_text == t("start.greeting", "en")
    assert session.keyboard_texts() == [t("btn.new_inspection", "en")]


@pytest.mark.asyncio
async def test_whole_start_wizard_speaks_the_deployment_language(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Мастер целиком: вопросы и надписи на кнопках, а не только первая строка."""
    monkeypatch.setenv(UI_LANG_VAR, "en")
    bot, session = make_bot()
    dp = build_dispatcher(settings())

    await feed(dp, bot, text_message("/start"))
    await feed(dp, bot, callback_query(NEW_INSPECTION_CALLBACK))
    assert session.last_text == t("start.ask_unit", "en")

    await feed(dp, bot, text_message("Demo Pizzeria #1"))
    assert session.last_text == t("start.ask_kind", "en")
    assert session.keyboard_texts() == ["Planned", "Repeat", "Unscheduled"]

    await feed(dp, bot, callback_query("start:kind:planned"))
    assert session.last_text == t("start.ask_lang", "en")


@pytest.mark.asyncio
async def test_resume_buttons_speak_the_language_of_the_inspection(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Кнопки «Продолжить»/«Начать новую» слушаются языка так же, как текст.

    Проверка тут уже заведена, и язык берётся из неё, а не из переменной.
    Случай всё равно про демо: пройдя мастер, человек вернётся к `/start` — и
    русская кнопка под английским вопросом видна ровно так же, как русский текст.
    """
    monkeypatch.setenv(UI_LANG_VAR, "en")
    bot, session = make_bot()
    dp = build_dispatcher(settings())
    await walk_wizard_to_language(dp, bot)
    await feed(dp, bot, callback_query("start:lang:en"))

    await feed(dp, bot, text_message("/start"))

    assert session.keyboard_texts() == [
        t("btn.resume_continue", "en"),
        t("btn.resume_new", "en"),
    ]


# --- после начала проверки язык берётся из неё самой --------------------------


@pytest.mark.asyncio
async def test_started_inspection_outranks_the_deployment_language(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Английский стенд с русской проверкой говорит по-русски — иначе поле мертво."""
    monkeypatch.setenv(UI_LANG_VAR, "en")
    bot, session = make_bot()
    dp = build_dispatcher(settings())
    await walk_wizard_to_language(dp, bot)

    await feed(dp, bot, callback_query("start:lang:ru"))

    inspection = get_state(CHAT_ID)
    assert inspection is not None
    assert inspection.ui_lang == "ru"
    session.clear()
    await feed(dp, bot, text_message("/start"))
    assert session.keyboard_texts() == [
        t("btn.resume_continue", "ru"),
        t("btn.resume_new", "ru"),
    ]


def header_type() -> str:
    """Вид проверки так, как его НАПЕЧАТАЕТ движок — по коду из шапки (T177).

    В шапке с T177 лежит код, а не слово: слово, записанное при заведении
    проверки, невозможно перевести при печати на другом языке. Поэтому тест
    спрашивает то же, что спросит движок при печати, — слово по коду и языку
    отчёта, а не готовую строку из файла.
    """
    raw: dict[str, Any] = json.loads(state_file(CHAT_ID, check_environment()).read_text("utf-8"))
    meta = raw["meta"]
    assert not meta.get("type"), "в шапке снова слово — ровно то, что ломало пересборку"
    return kind_title(str(meta["kind"]), str(meta["lang"]))


@pytest.mark.asyncio
async def test_kind_of_inspection_follows_the_report_language_not_the_interface(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Вид проверки стоит на кнопке интерфейса, но уезжает в шапку отчёта партнёру.

    Языка два разных, и путать их нельзя: на английском стенде с русским
    отчётом в шапке обязана стоять «Плановая», а не «Planned».

    Сама проверка при этом не помнит ни того, ни другого слова: она помнит код
    (T152). Слово живёт только в шапке для движка — там, где его печатают.
    """
    monkeypatch.setenv(UI_LANG_VAR, "en")
    bot, _ = make_bot()
    dp = build_dispatcher(settings())
    await walk_wizard_to_language(dp, bot)

    await feed(dp, bot, callback_query("start:lang:ru"))

    inspection = get_state(CHAT_ID)
    assert inspection is not None
    assert inspection.kind == "planned"
    assert header_type() == "Плановая"


@pytest.mark.asyncio
async def test_english_report_gets_an_english_kind_in_its_header(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(UI_LANG_VAR, "ru")
    bot, _ = make_bot()
    dp = build_dispatcher(settings())
    await walk_wizard_to_language(dp, bot)

    await feed(dp, bot, callback_query("start:lang:en"))

    inspection = get_state(CHAT_ID)
    assert inspection is not None
    assert inspection.kind == "planned"
    assert header_type() == "Planned"


# --- переменная описана там, где описаны остальные ---------------------------


def test_variable_is_documented_in_env_example() -> None:
    """Незаписанная переменная — это переменная, о которой никто не узнает."""
    assert f"\n{UI_LANG_VAR}=" in ENV_EXAMPLE.read_text(encoding="utf-8")


@requires_docker
def test_demo_stand_starts_the_bot_in_english() -> None:
    """Демо обязано быть английским целиком, и держится это профилем, а не памятью.

    Конфигурация читается самим `docker compose config`: проверять надо то, что
    получится на площадке — с профилями, якорями и подстановкой из `.env`, — а
    не то, как выглядит файл. Значения переменных нигде не печатаются: рядом в
    `.env` лежит настоящий токен боевого бота.
    """
    result = subprocess.run(  # noqa: S603 — аргументы собираем сами, ввода извне нет
        [  # noqa: S607 — docker из PATH
            "docker",
            "compose",
            "-p",
            COMPOSE_PROJECT,
            "--profile",
            "demo",
            "config",
            "--format",
            "json",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, "docker compose config не собрался"
    services = json.loads(result.stdout)["services"]
    assert services["demo"]["environment"].get(UI_LANG_VAR) == "en"
