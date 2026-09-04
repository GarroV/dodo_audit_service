"""Что переживает перезапуск бота, а что нет.

Пункт готовности блока — «прерванная проверка продолжается после перезапуска».
Перезапуск здесь моделируется честно: строится новый диспетчер с новым
хранилищем автомата и новой очередью материалов, то есть всё, что бот держал в
памяти, теряется. Уцелеть должно ровно то, что лежит в файле проверки.

Отдельно зафиксировано и то, что перезапуск НЕ переживает: очередь ожидания
комментария живёт только в памяти, поэтому комментарий, присланный после
перезапуска, привязывать уже не к чему — и бот говорит об этом прямо.

Кадр при этом не теряется: задача T068 решена не сохранением очереди, а
заметками бота (`src/bot/sidecar.py`) — присланные кадры лежат в файле рядом с
проверкой и показываются при завершении, даже если бот перезапускался.
"""

from __future__ import annotations

import pytest
from bot_harness import (
    AUDITOR_ID,
    CHAT_ID,
    candidate,
    feed,
    make_bot,
    photo_message,
    stub_classify,
    suggestion,
    text_message,
)

from src.bot.app import build_dispatcher
from src.bot.config import BotSettings
from src.bot.texts import t
from src.domain import add_finding, get_state, start_inspection

pytestmark = pytest.mark.asyncio

SETTINGS = BotSettings(token="unused-in-tests", allowed_ids=frozenset({AUDITOR_ID}), mode="polling")


def restart() -> object:
    """Новый диспетчер — всё, что бот держал в памяти, начинается с чистого листа."""
    return build_dispatcher(SETTINGS, album_window=5.0)


async def test_inspection_survives_restart_and_is_offered_to_continue(
    domain_env: object,
) -> None:
    start_inspection(CHAT_ID, "Белград 2", "Плановая", "ru", auditor="Владимир Гарро")
    add_finding(CHAT_ID, "PRD01", "D1", "hot_kitchen", "Пол в горячем цеху загрязнён")

    bot, session = make_bot()
    await feed(restart(), bot, text_message("/start"))

    assert "Белград 2" in session.last_text
    assert "Владимир Гарро" in session.last_text
    assert "1" in session.last_text


async def test_material_intake_works_after_restart(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Фиксация продолжается без дополнительного шага «восстановить»."""
    start_inspection(CHAT_ID, "Белград 2", "Плановая", "ru")
    stub_classify(monkeypatch, suggestion(candidate("PRD01", "D1", "hot_kitchen", "скол на борту")))

    bot, session = make_bot()
    first = restart()
    await feed(first, bot, photo_message("photo-1"))

    second = restart()
    session.clear()
    await feed(second, bot, photo_message("photo-2", caption="скол на бортике"))

    assert "скол на борту" in session.last_text
    assert any(data.startswith("rec:pick:") for data in session.keyboard_data())


async def test_uncommented_frame_does_not_survive_restart(domain_env: object) -> None:
    """Кадр без комментария живёт в памяти: после перезапуска связывать нечего.

    Записью он не стал, поэтому проверка от этого ничего не теряет, но
    комментарий, присланный уже после перезапуска, привязать не к чему — и бот
    говорит об этом прямо, а не молчит.
    """
    start_inspection(CHAT_ID, "Белград 2", "Плановая", "ru")

    bot, session = make_bot()
    await feed(restart(), bot, photo_message("photo-1"))

    session.clear()
    await feed(restart(), bot, text_message("грязный пол"))

    # Раньше сверялось с фразой «не вижу кадра». С T160 (D078) отказ несёт
    # правило методики, а не жалобу продукта на себя, и сверка идёт по ключу
    # каталога: буквальная строка краснела бы от любой вычитки формулировки.
    assert t("material.no_photo", "ru") in session.last_text


async def test_wizard_step_does_not_survive_restart_and_start_is_offered_again(
    domain_env: object,
) -> None:
    """Шаг мастера — в памяти, и это осознанно: проверка ещё не начата, терять нечего."""
    bot, session = make_bot()
    first = restart()
    await feed(first, bot, text_message("/start"))
    await feed(first, bot, text_message("/start"))

    session.clear()
    await feed(restart(), bot, text_message("Белград 2"))

    assert get_state(CHAT_ID) is None
    # Раньше сверялось с фразой «не вижу кадра». С T160 (D078) отказ несёт
    # правило методики, а не жалобу продукта на себя, и сверка идёт по ключу
    # каталога: буквальная строка краснела бы от любой вычитки формулировки.
    assert t("material.no_photo", "ru") in session.last_text or "проверка не начата" in (
        session.last_text.lower()
    )
