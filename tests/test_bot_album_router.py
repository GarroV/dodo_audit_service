"""Склейка альбома через настоящий диспетчер (T054): несколько кадров — один материал.

`tests/test_bot_albums.py` проверяет саму копилку (`AlbumBuffer`) юнит-тестами
без aiogram. `tests/test_bot_material_router.py` проверяет три способа связать
комментарий на одиночных кадрах. Здесь — то, что между ними: как альбом целиком
проходит через роутер и становится материалом, включая три способа закрытия
(другое событие, комментарий, окно ожидания) и три способа связать комментарий
уже на альбоме (подпись, отдельное сообщение, reply на любой из кадров).
"""

from __future__ import annotations

import asyncio

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
from src.domain import start_inspection

pytestmark = pytest.mark.asyncio

SETTINGS = BotSettings(token="unused-in-tests", allowed_ids=frozenset({AUDITOR_ID}), mode="polling")


class Caught:
    """Накопитель материалов вместо разбора: он появится во второй очереди."""

    def __init__(self) -> None:
        self.materials: list[Material] = []

    async def __call__(self, message: object, material: Material, lang: str) -> None:
        self.materials.append(material)

    @property
    def last(self) -> Material:
        assert self.materials, "бот не собрал ни одного материала"
        return self.materials[-1]


def dispatcher_with(caught: Caught, album_window: float = 5.0) -> object:
    """Диспетчер с накопителем. По умолчанию окно длинное: закрывать должно событие, не таймер."""
    return build_dispatcher(SETTINGS, on_material=caught, album_window=album_window)


async def test_album_of_three_without_caption_becomes_one_material(domain_env: object) -> None:
    """Три кадра без подписи — один материал с тремя кадрами, а не три материала."""
    start_inspection(CHAT_ID, "Белград 2", "planned", "ru")
    caught = Caught()
    bot, _ = make_bot()
    dp = dispatcher_with(caught)

    group_id = "album-plain"
    await feed(dp, bot, photo_message("a1", media_group_id=group_id))
    await feed(dp, bot, photo_message("a2", media_group_id=group_id))
    await feed(dp, bot, photo_message("a3", media_group_id=group_id))
    assert caught.materials == []

    await feed(dp, bot, text_message("трещина в кафеле"))

    assert len(caught.materials) == 1
    assert caught.last.photo_file_ids == ("a1", "a2", "a3")
    assert caught.last.comment.text == "трещина в кафеле"


async def test_caption_on_first_frame_of_album_links_without_extra_comment(
    domain_env: object,
) -> None:
    """Подпись на первом кадре — материал собран без отдельного сообщения-комментария."""
    start_inspection(CHAT_ID, "Белград 2", "planned", "ru")
    caught = Caught()
    bot, _ = make_bot()
    dp = dispatcher_with(caught, album_window=0.01)

    group_id = "album-caption-first"
    await feed(dp, bot, photo_message("cap-1", media_group_id=group_id, caption="пол грязный"))
    await feed(dp, bot, photo_message("cap-2", media_group_id=group_id))
    await feed(dp, bot, photo_message("cap-3", media_group_id=group_id))
    await asyncio.sleep(0.1)

    assert len(caught.materials) == 1
    assert caught.last.photo_file_ids == ("cap-1", "cap-2", "cap-3")
    assert caught.last.comment.text == "пол грязный"


async def test_caption_on_second_frame_still_becomes_the_comment(domain_env: object) -> None:
    """Telegram кладёт подпись на один кадр из всех — не обязательно на первый."""
    start_inspection(CHAT_ID, "Белград 2", "planned", "ru")
    caught = Caught()
    bot, _ = make_bot()
    dp = dispatcher_with(caught, album_window=0.01)

    group_id = "album-caption-second"
    await feed(dp, bot, photo_message("b1", media_group_id=group_id))
    await feed(dp, bot, photo_message("b2", media_group_id=group_id, caption="скол на бортике"))
    await feed(dp, bot, photo_message("b3", media_group_id=group_id))
    await asyncio.sleep(0.1)

    assert len(caught.materials) == 1
    assert caught.last.photo_file_ids == ("b1", "b2", "b3")
    assert caught.last.comment.text == "скол на бортике"


async def test_album_without_caption_plus_separate_comment_becomes_one_material(
    domain_env: object,
) -> None:
    """Способ 2 на альбоме: комментарий отдельным сообщением собирает все кадры."""
    start_inspection(CHAT_ID, "Белград 2", "planned", "ru")
    caught = Caught()
    bot, _ = make_bot()
    dp = dispatcher_with(caught)

    group_id = "album-plus-comment"
    await feed(dp, bot, photo_message("c1", media_group_id=group_id))
    await feed(dp, bot, photo_message("c2", media_group_id=group_id))

    await feed(dp, bot, text_message("грязная плитка у входа"))

    assert len(caught.materials) == 1
    assert caught.last.photo_file_ids == ("c1", "c2")
    assert caught.last.comment.text == "грязная плитка у входа"


async def test_album_without_caption_plus_voice_comment_is_linked_as_voice(
    domain_env: object,
) -> None:
    """Способ 2 на альбоме, но комментарий голосовой — тот же путь связывания."""
    start_inspection(CHAT_ID, "Белград 2", "planned", "ru")
    caught = Caught()
    bot, _ = make_bot()
    dp = dispatcher_with(caught)

    group_id = "album-voice"
    await feed(dp, bot, photo_message("d1", media_group_id=group_id))
    await feed(dp, bot, photo_message("d2", media_group_id=group_id))

    await feed(dp, bot, voice_message("voice-album"))

    assert len(caught.materials) == 1
    assert caught.last.photo_file_ids == ("d1", "d2")
    assert caught.last.comment.voice_file_id == "voice-album"
    assert caught.last.comment.text is None


async def test_two_albums_in_a_row_get_comments_in_order_without_mixing(
    domain_env: object,
) -> None:
    """Два альбома подряд, потом два комментария: каждый уходит своему альбому по FIFO."""
    start_inspection(CHAT_ID, "Белград 2", "planned", "ru")
    caught = Caught()
    bot, _ = make_bot()
    dp = dispatcher_with(caught)

    await feed(dp, bot, photo_message("first-1", media_group_id="album-first"))
    await feed(dp, bot, photo_message("first-2", media_group_id="album-first"))
    # Кадр другого альбома закрывает первый (close_other), не дожидаясь комментария.
    await feed(dp, bot, photo_message("second-1", media_group_id="album-second"))
    await feed(dp, bot, photo_message("second-2", media_group_id="album-second"))

    await feed(dp, bot, text_message("для первого альбома"))
    await feed(dp, bot, text_message("для второго альбома"))

    assert [m.photo_file_ids for m in caught.materials] == [
        ("first-1", "first-2"),
        ("second-1", "second-2"),
    ]
    assert [m.comment.text for m in caught.materials] == [
        "для первого альбома",
        "для второго альбома",
    ]


async def test_album_closes_by_timer_when_nothing_else_arrives(domain_env: object) -> None:
    """Ничего не пришло следом — альбом закрывается сам по истечении окна."""
    start_inspection(CHAT_ID, "Белград 2", "planned", "ru")
    caught = Caught()
    bot, session = make_bot()
    dp = dispatcher_with(caught, album_window=0.01)

    group_id = "album-timer"
    await feed(dp, bot, photo_message("t1", media_group_id=group_id))
    await feed(dp, bot, photo_message("t2", media_group_id=group_id))
    await feed(dp, bot, photo_message("t3", media_group_id=group_id))
    await asyncio.sleep(0.1)

    assert caught.materials == []
    assert "3" in session.last_text


async def test_reply_to_second_frame_links_the_whole_album(domain_env: object) -> None:
    """Ответ на один кадр альбома связывает комментарий со всем альбомом, не с одним кадром."""
    start_inspection(CHAT_ID, "Белград 2", "planned", "ru")
    caught = Caught()
    bot, _ = make_bot()
    dp = dispatcher_with(caught)

    group_id = "album-reply"
    first = photo_message("r1", media_group_id=group_id)
    second = photo_message("r2", media_group_id=group_id)
    third = photo_message("r3", media_group_id=group_id)
    await feed(dp, bot, first)
    await feed(dp, bot, second)
    await feed(dp, bot, third)

    # Альбом ещё открыт (окно длинное) — ответ на кадр сам закрывает его первым
    # действием комментарного хендлера, и только потом ищет цель reply.
    await feed(dp, bot, text_message("трещина по всей стене", reply_to=second))

    assert len(caught.materials) == 1
    assert caught.last.photo_file_ids == ("r1", "r2", "r3")
    assert caught.last.comment.text == "трещина по всей стене"


async def test_single_photo_between_two_albums_does_not_merge(domain_env: object) -> None:
    """Одиночный кадр между двумя альбомами не склеивается ни с одним из них."""
    start_inspection(CHAT_ID, "Белград 2", "planned", "ru")
    caught = Caught()
    bot, _ = make_bot()
    dp = dispatcher_with(caught)

    await feed(dp, bot, photo_message("m1-1", media_group_id="album-1", caption="скол на бортике"))
    await feed(dp, bot, photo_message("m1-2", media_group_id="album-1"))

    # Одиночный кадр закрывает album-1 (close_other) и сам закрывается сразу же.
    await feed(dp, bot, photo_message("single-1", caption="одиночный кадр"))

    await feed(dp, bot, photo_message("m2-1", media_group_id="album-2", caption="трещина в плитке"))
    await feed(dp, bot, photo_message("m2-2", media_group_id="album-2"))
    await feed(dp, bot, text_message("закрывающее событие"))

    assert len(caught.materials) == 3
    assert caught.materials[0].photo_file_ids == ("m1-1", "m1-2")
    assert caught.materials[0].comment.text == "скол на бортике"
    assert caught.materials[1].photo_file_ids == ("single-1",)
    assert caught.materials[1].comment.text == "одиночный кадр"
    assert caught.materials[2].photo_file_ids == ("m2-1", "m2-2")
    assert caught.materials[2].comment.text == "трещина в плитке"


async def test_album_without_inspection_replies_once_not_per_frame(domain_env: object) -> None:
    """Проверка не начата — на весь альбом один ответ, а не по одному на каждый кадр."""
    caught = Caught()
    bot, session = make_bot()
    dp = dispatcher_with(caught, album_window=0.01)

    group_id = "album-no-inspection"
    await feed(dp, bot, photo_message("n1", media_group_id=group_id))
    await feed(dp, bot, photo_message("n2", media_group_id=group_id))
    await feed(dp, bot, photo_message("n3", media_group_id=group_id))
    await asyncio.sleep(0.1)

    assert caught.materials == []
    replies = [text for text in session.texts if "проверка не начата" in text.lower()]
    assert len(replies) == 1
