"""Что аудитор видит в ответ на связанный материал, и что бывает при отказе.

Остальные тесты приёма материала подменяют обработчик накопителем `Caught` —
им важно, что и с чем связалось. Здесь наоборот: работает настоящий обработчик
второй очереди (разбор из `routers/record.py`), и проверяется то, что аудитор
читает на точке: услышанное голосовое, отказ транскрипции, предложения.

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
    candidate,
    feed,
    make_bot,
    photo_message,
    stub_classify,
    stub_transcribe,
    suggestion,
    text_message,
    voice_message,
)

from src.bot.app import build_dispatcher
from src.bot.config import BotSettings
from src.bot.material import Material
from src.bot.texts import t, with_photo_rule
from src.domain import start_inspection
from src.recognize.errors import TranscriptionFailed

pytestmark = pytest.mark.asyncio

SETTINGS = BotSettings(token="unused-in-tests", allowed_ids=frozenset({AUDITOR_ID}), mode="polling")


async def test_voice_comment_is_transcribed_and_shown_back(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Услышанное показывается аудитору: ошибку распознавания он заметит только так."""
    start_inspection(CHAT_ID, "Белград 2", "planned", "ru")
    heard = stub_transcribe(monkeypatch, "пол в горячем цеху грязный")
    stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen")))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, photo_message("voice-confirm"))
    await feed(dp, bot, voice_message("voice-file"))

    assert len(heard) == 1, "голосовое не ушло в транскрипцию"
    assert t("record.heard", "ru", note="пол в горячем цеху грязный") in session.texts


async def test_failed_transcription_asks_for_text_and_keeps_inspection_alive(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Сбой транскрипции не роняет проверку и не уходит в модель разбором пустоты."""
    start_inspection(CHAT_ID, "Белград 2", "planned", "ru")
    stub_transcribe(monkeypatch, TranscriptionFailed("голосовое пустое"))
    asked = stub_classify(monkeypatch, suggestion())
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, photo_message("voice-fail"))
    await feed(dp, bot, voice_message("voice-file"))

    assert "голосовое пустое" in session.last_text
    assert asked == [], "после сбоя транскрипции разбор звать нельзя — записывать нечего"


async def test_long_comment_is_shortened_where_it_is_shown_back(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Длинное услышанное обрезается: аудитору нужна отметка, а не пересказ."""
    start_inspection(CHAT_ID, "Белград 2", "planned", "ru")
    long_note = "грязь под тестомесом " * 20
    stub_transcribe(monkeypatch, long_note)
    stub_classify(monkeypatch, suggestion())
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, photo_message("long-comment"))
    await feed(dp, bot, voice_message("voice-file"))

    shown = next(text for text in session.texts if text.startswith("Услышал"))
    assert "…" in shown
    assert len(shown) < len(long_note)


async def test_album_timer_failure_is_logged_and_does_not_kill_the_bot(
    domain_env: object, caplog: pytest.LogCaptureFixture
) -> None:
    """Отказ фоновой задачи альбома попадает в журнал, а бот продолжает работать."""
    start_inspection(CHAT_ID, "Белград 2", "planned", "ru")

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
    # Комментарий без кадра с T229 (D090) не отказ, а ожидание фотографии —
    # но для этого теста важен сам факт ответа, а не то, какой он.
    session.clear()
    await feed(dp, bot, text_message("комментарий без кадра"))
    assert session.last_text == with_photo_rule(t("material.waiting_photo", "ru"), "ru")
