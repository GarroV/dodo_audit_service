"""Посмотреть, что уже записано, не завершая проверку (T139, задача #110).

Находка юзабилити-прохода (T130): список записей показывала одна команда
«Завершить». Она безопасна — ничего не завершает сама, только показывает итог и
даёт кнопки, — но называется так, что на середине обхода аудитор её не нажмёт:
он думает, что закончит проверку. Функция была, и была спрятана за пугающим
именем.

Здесь проверяется ровно это: у показа записанного есть свой вход, он показывает
то же, что первая часть `/finish`, и **ничего не завершает** — ни отчёта, ни
кнопки сборки, ни следа сдачи. И отдельно — что вход этот виден: команда
объявлена в меню телеграма, иначе она остаётся такой же спрятанной, как раньше,
только с другим именем.

Процента в показе нет намеренно (T162, D072): показ зовётся посреди обхода, а
там оценка не показывается.
"""

from __future__ import annotations

import pytest
from bot_harness import (
    AUDITOR_ID,
    CHAT_ID,
    feed,
    make_bot,
    photo_message,
    text_message,
)

from src.bot.app import announce_commands, build_dispatcher
from src.bot.config import BotSettings
from src.bot.keyboards import FINISH_BUILD_CALLBACK
from src.bot.texts import t
from src.domain import add_finding, start_inspection

pytestmark = pytest.mark.asyncio

SETTINGS = BotSettings(token="unused-in-tests", allowed_ids=frozenset({AUDITOR_ID}), mode="polling")

#: Имя команды. Проверяется как строка, а не как константа модуля: аудитор
#: набирает и читает именно её, и переименование обязано ломать тест.
COMMAND = "/records"


def начать_с_записью() -> None:
    start_inspection(CHAT_ID, "Белград 2", "Плановая", "ru")
    add_finding(CHAT_ID, "PRD01", "D1", "fridge", "Ярлык без даты вскрытия")


async def test_показывает_записанное_не_завершая_проверку(domain_env: object) -> None:
    """Тот же список, что в первой части `/finish`, и ни одной кнопки сборки."""
    начать_с_записью()
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, text_message(COMMAND))

    показано = "\n".join(session.texts)
    assert "#1" in показано and "PRD01" in показано, "записанное не показано"
    assert FINISH_BUILD_CALLBACK not in session.keyboard_data(), (
        "показ записанного не должен предлагать собрать отчёт — он ничего не завершает"
    )
    assert session.documents == [], "показ записанного отчёт не собирает"


async def test_процента_в_показе_нет(domain_env: object) -> None:
    """Зовётся посреди обхода — значит, действует D072 (T162)."""
    начать_с_записью()
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, text_message(COMMAND))

    показано = "\n".join(session.texts)
    assert "#1" in показано, "показ пустой — проверять на процент нечего"
    assert "%" not in показано, "процент во время обхода показывать нельзя (D072)"


async def test_пустая_проверка_отвечает_словами_а_не_молчанием(domain_env: object) -> None:
    """Ни одной записи — это ответ, а не повод промолчать."""
    start_inspection(CHAT_ID, "Белград 2", "Плановая", "ru")
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, text_message(COMMAND))

    assert session.last_text == t("finish.empty", "ru")


async def test_кадры_без_записи_видны_и_здесь(domain_env: object) -> None:
    """Кадр, присланный и не разобранный, виден до завершения, а не только в конце (T068)."""
    начать_с_записью()
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, photo_message("frame-lost", message_id=555))
    session.clear()
    await feed(dp, bot, text_message(COMMAND))

    assert t("finish.unclaimed_line", "ru", message_id=555) in "\n".join(session.texts)


async def test_без_начатой_проверки_бот_объясняет_а_не_падает(domain_env: object) -> None:
    начать_с_записью.__doc__  # noqa: B018 — проверка идёт БЕЗ старта, вызов не нужен
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, text_message(COMMAND))

    assert session.last_text == t("material.no_inspection", "ru")


async def test_команда_объявлена_в_меню_телеграма(domain_env: object) -> None:
    """Спрятанная команда — та же спрятанная функция, только с другим именем.

    Меню телеграма — единственное место, где аудитор увидит команду, не зная о
    ней заранее. Поэтому объявление проверяется, а не остаётся обещанием.
    """
    bot, session = make_bot()

    await announce_commands(bot)

    объявлено = [c for c in session.calls if type(c).__name__ == "SetMyCommands"]
    assert объявлено, "команды в меню не объявлены вовсе"
    имена = [c.command for c in объявлено[0].commands]
    assert COMMAND.removeprefix("/") in имена, "показа записанного в меню нет"
    for обязательная in ("start", "undo", "finish"):
        assert обязательная in имена, f"команда {обязательная} пропала из меню"
