"""Что аудитор видит в ответ на связанный материал, и что бывает при отказе.

Остальные тесты приёма материала подменяют обработчик накопителем `Caught` —
им важно, что и с чем связалось. Здесь наоборот: работает настоящий
обработчик первой очереди (`confirm_link`), и проверяется ровно та строка,
которую аудитор читает на точке после каждого кадра.

Отдельно — отказ фоновой задачи, закрывающей альбом по окну ожидания.
Исключение внутри неё никуда не всплывает: аудитор не получит ответа, а в
журнале не окажется ни строки, если её не записать явно. Поэтому запись в
журнал проверяется тестом, а не остаётся обещанием в комментарии.
"""

from __future__ import annotations

import asyncio
import logging

import pytest
from bot_harness import (
    AUDITOR_ID,
    CHAT_ID,
    feed,
    make_bot,
    photo_message,
    text_message,
    voice_message,
)
from conftest import requires_data

from src.bot.app import build_dispatcher
from src.bot.config import BotSettings
from src.bot.material import Material
from src.bot.routers.material import COMMENT_PREVIEW_LIMIT
from src.bot.texts import t
from src.domain import start_inspection

pytestmark = [pytest.mark.asyncio, requires_data]

SETTINGS = BotSettings(token="unused-in-tests", allowed_ids=frozenset({AUDITOR_ID}), mode="polling")


async def test_voice_comment_confirmation_says_it_was_voice(domain_env: object) -> None:
    """Голосовой комментарий подтверждается своим текстом: пересказать голос бот не может."""
    start_inspection(CHAT_ID, "Белград 2", "Плановая", "ru")
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, photo_message("voice-confirm"))
    await feed(dp, bot, voice_message("voice-file"))

    assert session.last_text == t("material.linked_voice", "ru", count=1)


async def test_long_comment_is_shortened_in_the_confirmation(domain_env: object) -> None:
    """Длинный комментарий в подтверждении обрезается: аудитору нужна отметка, а не пересказ."""
    start_inspection(CHAT_ID, "Белград 2", "Плановая", "ru")
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    long_comment = "грязь под тестомесом " * 20
    await feed(dp, bot, photo_message("long-comment"))
    await feed(dp, bot, text_message(long_comment))

    assert session.last_text.endswith("…».")
    # Показанное — начало настоящего комментария, а не выдумка и не пустая строка.
    assert long_comment[:40] in session.last_text
    assert len(session.last_text) < len(long_comment)
    assert COMMENT_PREVIEW_LIMIT < len(long_comment)


async def test_album_timer_failure_is_logged_and_does_not_kill_the_bot(
    domain_env: object, caplog: pytest.LogCaptureFixture
) -> None:
    """Отказ фоновой задачи альбома попадает в журнал, а бот продолжает работать."""
    start_inspection(CHAT_ID, "Белград 2", "Плановая", "ru")

    async def refuse(message: object, material: Material, lang: str) -> None:
        raise RuntimeError("разбор недоступен")

    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS, on_material=refuse, album_window=0.01)

    with caplog.at_level(logging.ERROR, logger="src.bot.routers.material"):
        await feed(dp, bot, photo_message("boom-1", caption="подпись", media_group_id="boom"))
        await feed(dp, bot, photo_message("boom-2", media_group_id="boom"))
        await asyncio.sleep(0.1)

    assert any("не удалось закрыть альбом" in r.message for r in caplog.records)

    # Бот жив: следующее сообщение он всё ещё обрабатывает и отвечает на него.
    session.clear()
    await feed(dp, bot, text_message("комментарий без кадра"))
    assert session.last_text == t("material.no_photo", "ru")
