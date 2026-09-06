"""Пачка сообщений разом после потери связи (T059): порядок не должен путаться.

Телефон аудитора теряет связь, все отложенные кадры и комментарии приходят
Telegram-ом разом, но порядок доставки внутри чата Telegram сохраняет. Бот
обрабатывает обновления последовательно (`src/bot/app.py`, `handle_as_tasks=
False`), поэтому пачка обязана разобраться так же, как если бы аудитор
отправлял её по одному, с паузами. Здесь кадры и комментарии отдаются
диспетчеру подряд, без `asyncio.sleep`, — окно альбома нарочно длинное
(`dispatcher_with`, по умолчанию 5 секунд), чтобы альбом закрывало событие,
а не таймер.
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
    voice_message,
)

from src.bot.app import build_dispatcher
from src.bot.config import BotSettings
from src.bot.material import Material
from src.bot.texts import t, with_photo_rule
from src.domain import start_inspection

pytestmark = pytest.mark.asyncio

SETTINGS = BotSettings(token="unused-in-tests", allowed_ids=frozenset({AUDITOR_ID}), mode="polling")


class Caught:
    """Накопитель материалов вместо разбора: он появится во второй очереди."""

    def __init__(self) -> None:
        self.materials: list[Material] = []

    async def __call__(self, message: object, material: Material, lang: str) -> None:
        self.materials.append(material)


def dispatcher_with(caught: Caught, album_window: float = 5.0) -> object:
    """Диспетчер с накопителем. Окно альбома длинное: закрывать должно событие, не таймер."""
    return build_dispatcher(SETTINGS, on_material=caught, album_window=album_window)


async def test_four_bare_frames_then_four_comments_pair_up_by_fifo_order(
    domain_env: object,
) -> None:
    """Четыре кадра без подписи, затем четыре комментария — i-й комментарий достаётся i-му кадру."""
    start_inspection(CHAT_ID, "Белград 2", "planned", "ru")
    caught = Caught()
    bot, _ = make_bot()
    dp = dispatcher_with(caught)

    for i in range(1, 5):
        await feed(dp, bot, photo_message(f"burst-{i}"))
    assert caught.materials == []

    for i in range(1, 5):
        await feed(dp, bot, text_message(f"комментарий {i}"))

    assert [m.photo_file_ids for m in caught.materials] == [(f"burst-{i}",) for i in range(1, 5)]
    assert [m.comment.text for m in caught.materials] == [f"комментарий {i}" for i in range(1, 5)]


async def test_captioned_frames_in_the_middle_do_not_steal_the_bare_frames_comment(
    domain_env: object,
) -> None:
    """Кадры с подписью связались сами собой, комментарий достался кадру без подписи."""
    start_inspection(CHAT_ID, "Белград 2", "planned", "ru")
    caught = Caught()
    bot, _ = make_bot()
    dp = dispatcher_with(caught)

    await feed(dp, bot, photo_message("mix-a", caption="подпись A"))
    await feed(dp, bot, photo_message("mix-b"))
    await feed(dp, bot, photo_message("mix-c", caption="подпись C"))
    await feed(dp, bot, text_message("для безымянного кадра"))

    assert [m.photo_file_ids for m in caught.materials] == [
        ("mix-a",),
        ("mix-c",),
        ("mix-b",),
    ]
    assert [m.comment.text for m in caught.materials] == [
        "подпись A",
        "подпись C",
        "для безымянного кадра",
    ]


async def test_reply_to_middle_frame_then_plain_comment_takes_oldest_remaining(
    domain_env: object,
) -> None:
    """Reply забирает свой кадр, обычный комментарий — старейший из оставшихся, не тот же снова."""
    start_inspection(CHAT_ID, "Белград 2", "planned", "ru")
    caught = Caught()
    bot, _ = make_bot()
    dp = dispatcher_with(caught)

    first = photo_message("reply-1")
    second = photo_message("reply-2")
    third = photo_message("reply-3")
    await feed(dp, bot, first)
    await feed(dp, bot, second)
    await feed(dp, bot, third)

    await feed(dp, bot, text_message("для второго кадра", reply_to=second))
    await feed(dp, bot, text_message("для старейшего из оставшихся"))

    assert [m.photo_file_ids for m in caught.materials] == [
        ("reply-2",),
        ("reply-1",),
    ]
    assert [m.comment.text for m in caught.materials] == [
        "для второго кадра",
        "для старейшего из оставшихся",
    ]


async def test_album_plus_two_singles_then_three_comments_keep_arrival_order(
    domain_env: object,
) -> None:
    """Альбом остаётся одним материалом с первым комментарием, одиночные — вторым и третьим."""
    start_inspection(CHAT_ID, "Белград 2", "planned", "ru")
    caught = Caught()
    bot, _ = make_bot()
    dp = dispatcher_with(caught)

    group_id = "burst-album"
    await feed(dp, bot, photo_message("alb-1", media_group_id=group_id))
    await feed(dp, bot, photo_message("alb-2", media_group_id=group_id))
    await feed(dp, bot, photo_message("single-x"))
    await feed(dp, bot, photo_message("single-y"))
    assert caught.materials == []

    await feed(dp, bot, text_message("комментарий альбому"))
    await feed(dp, bot, text_message("комментарий x"))
    await feed(dp, bot, text_message("комментарий y"))

    assert [m.photo_file_ids for m in caught.materials] == [
        ("alb-1", "alb-2"),
        ("single-x",),
        ("single-y",),
    ]
    assert [m.comment.text for m in caught.materials] == [
        "комментарий альбому",
        "комментарий x",
        "комментарий y",
    ]


async def test_extra_frame_without_a_matching_comment_is_not_lost(domain_env: object) -> None:
    """Кадров больше комментариев — лишний ждёт и достаётся комментарию, пришедшему позже."""
    start_inspection(CHAT_ID, "Белград 2", "planned", "ru")
    caught = Caught()
    bot, _ = make_bot()
    dp = dispatcher_with(caught)

    await feed(dp, bot, photo_message("extra-1"))
    await feed(dp, bot, photo_message("extra-2"))
    await feed(dp, bot, text_message("только один комментарий"))

    assert len(caught.materials) == 1
    assert caught.materials[0].photo_file_ids == ("extra-1",)

    await feed(dp, bot, text_message("комментарий пришёл позже"))

    assert len(caught.materials) == 2
    assert caught.materials[1].photo_file_ids == ("extra-2",)
    assert caught.materials[1].comment.text == "комментарий пришёл позже"


async def test_extra_comment_without_a_frame_is_held_and_waits(domain_env: object) -> None:
    """Комментариев больше кадров — лишний ничему не достаётся сразу, но не пропадает (T229, D090).

    До T229 здесь стоял отказ: лишний комментарий терялся. Решение владельца
    D090 меняет это — комментарий придерживается и ждёт свой кадр, а бот
    отвечает не отказом, а ожиданием. Материалов по-прежнему не становится
    больше, чем кадров: фотофиксация (D078) в силе, придержать — не значит
    собрать запись без кадра.
    """
    start_inspection(CHAT_ID, "Белград 2", "planned", "ru")
    caught = Caught()
    bot, session = make_bot()
    dp = dispatcher_with(caught)

    await feed(dp, bot, photo_message("only-frame"))
    await feed(dp, bot, text_message("первый комментарий"))
    await feed(dp, bot, text_message("лишний комментарий"))

    assert len(caught.materials) == 1
    assert caught.materials[0].photo_file_ids == ("only-frame",)
    assert caught.materials[0].comment.text == "первый комментарий"
    # Рядом с ожиданием стоит правило фотофиксации (T160, D078): без него оно
    # читается сбоем продукта, а не порядком работы.
    assert session.last_text == with_photo_rule(t("material.waiting_photo", "ru"), "ru")


async def test_burst_of_voice_comments_link_to_frames_in_order(domain_env: object) -> None:
    """Пачка голосовых комментариев после пачки кадров расходится по кадрам в том же порядке."""
    start_inspection(CHAT_ID, "Белград 2", "planned", "ru")
    caught = Caught()
    bot, _ = make_bot()
    dp = dispatcher_with(caught)

    await feed(dp, bot, photo_message("voice-1"))
    await feed(dp, bot, photo_message("voice-2"))
    await feed(dp, bot, voice_message("v1"))
    await feed(dp, bot, voice_message("v2"))

    assert [m.photo_file_ids for m in caught.materials] == [("voice-1",), ("voice-2",)]
    assert [m.comment.voice_file_id for m in caught.materials] == ["v1", "v2"]
    assert all(m.comment.text is None for m in caught.materials)
