"""Приём материала через настоящий диспетчер (T053): три способа связать комментарий.

Проверяется не текст ответов, а что именно бот связал: какие кадры попали в
материал и какой комментарий к ним прикреплён. Для этого на место обработчика
материала ставится накопитель — то самое место, где во второй очереди встанет
разбор (T055).
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
from src.bot.texts import t
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
    """Диспетчер с накопителем. Окно альбома длинное: закрывать должно событие, не таймер."""
    return build_dispatcher(SETTINGS, on_material=caught, album_window=album_window)


async def test_caption_links_immediately(domain_env: object) -> None:
    """Способ 1 — подпись к фотографии."""
    start_inspection(CHAT_ID, "Белград 2", "planned", "ru")
    caught = Caught()
    bot, _ = make_bot()
    dp = dispatcher_with(caught)

    await feed(dp, bot, photo_message("photo-1", caption="пол в горячем цеху грязный"))

    assert caught.last.photo_file_ids == ("photo-1",)
    assert caught.last.comment.text == "пол в горячем цеху грязный"


async def test_next_message_links_to_waiting_photo(domain_env: object) -> None:
    """Способ 2 — комментарий отдельным сообщением следом."""
    start_inspection(CHAT_ID, "Белград 2", "planned", "ru")
    caught = Caught()
    bot, session = make_bot()
    dp = dispatcher_with(caught)

    await feed(dp, bot, photo_message("photo-1"))
    assert caught.materials == []
    assert "коммент" in session.last_text.lower()

    await feed(dp, bot, text_message("скол на бортике теста"))

    assert caught.last.photo_file_ids == ("photo-1",)
    assert caught.last.comment.text == "скол на бортике теста"


async def test_reply_links_to_the_replied_photo_not_the_oldest(domain_env: object) -> None:
    """Способ 3 — ответ на конкретный кадр сильнее очереди ожидания."""
    start_inspection(CHAT_ID, "Белград 2", "planned", "ru")
    caught = Caught()
    bot, _ = make_bot()
    dp = dispatcher_with(caught)

    first = photo_message("photo-1")
    second = photo_message("photo-2")
    await feed(dp, bot, first)
    await feed(dp, bot, second)

    await feed(dp, bot, text_message("трещина в плитке", reply_to=second))

    assert caught.last.photo_file_ids == ("photo-2",)


async def test_voice_comment_is_linked_as_voice(domain_env: object) -> None:
    start_inspection(CHAT_ID, "Белград 2", "planned", "ru")
    caught = Caught()
    bot, _ = make_bot()
    dp = dispatcher_with(caught)

    await feed(dp, bot, photo_message("photo-1"))
    await feed(dp, bot, voice_message("voice-1"))

    assert caught.last.photo_file_ids == ("photo-1",)
    assert caught.last.comment.voice_file_id == "voice-1"
    assert caught.last.comment.text is None


async def test_biggest_photo_size_is_taken(domain_env: object) -> None:
    """Telegram присылает несколько размеров кадра — в отчёт должен идти крупный."""
    start_inspection(CHAT_ID, "Белград 2", "planned", "ru")
    caught = Caught()
    bot, _ = make_bot()
    dp = dispatcher_with(caught)

    await feed(dp, bot, photo_message("photo-1", caption="скол"))

    assert caught.last.photo_file_ids == ("photo-1",)


async def test_comment_without_any_photo_says_so(domain_env: object) -> None:
    start_inspection(CHAT_ID, "Белград 2", "planned", "ru")
    caught = Caught()
    bot, session = make_bot()
    dp = dispatcher_with(caught)

    await feed(dp, bot, text_message("тут просто мысль вслух"))

    assert caught.materials == []
    # Раньше здесь стояло «не вижу кадра» — жалоба продукта на себя. С T160
    # (D078) отказ несёт правило методики: фотофиксация обязательна всегда.
    assert t("material.photo_required", "ru") in session.last_text


async def test_reply_to_a_bot_message_falls_back_to_the_waiting_photo(
    domain_env: object,
) -> None:
    """Ответить на своё же сообщение бота — обычное дело; комментарий терять нельзя."""
    start_inspection(CHAT_ID, "Белград 2", "planned", "ru")
    caught = Caught()
    bot, _ = make_bot()
    dp = dispatcher_with(caught)

    await feed(dp, bot, photo_message("photo-1"))
    not_a_photo = text_message("Кадр принят.")
    await feed(dp, bot, text_message("грязный пол", reply_to=not_a_photo))

    assert caught.last.photo_file_ids == ("photo-1",)


async def test_photo_without_inspection_is_refused(domain_env: object) -> None:
    caught = Caught()
    bot, session = make_bot()
    dp = dispatcher_with(caught)

    await feed(dp, bot, photo_message("photo-1", caption="скол"))

    assert caught.materials == []
    assert "проверка не начата" in session.last_text.lower()


async def test_comment_without_inspection_is_refused(domain_env: object) -> None:
    caught = Caught()
    bot, session = make_bot()
    dp = dispatcher_with(caught)

    await feed(dp, bot, text_message("грязный пол"))

    assert caught.materials == []
    assert "проверка не начата" in session.last_text.lower()


async def test_command_is_not_taken_for_a_comment(domain_env: object) -> None:
    """`/start` при ждущем кадре обязан остаться командой, а не стать формулировкой."""
    start_inspection(CHAT_ID, "Белград 2", "planned", "ru")
    caught = Caught()
    bot, session = make_bot()
    dp = dispatcher_with(caught)

    await feed(dp, bot, photo_message("photo-1"))
    await feed(dp, bot, text_message("/start"))

    assert caught.materials == []
    assert "Белград 2" in session.last_text


async def test_second_comment_takes_the_second_photo(domain_env: object) -> None:
    """Очередь ожидания — FIFO: два кадра подряд получают комментарии по порядку."""
    start_inspection(CHAT_ID, "Белград 2", "planned", "ru")
    caught = Caught()
    bot, _ = make_bot()
    dp = dispatcher_with(caught)

    await feed(dp, bot, photo_message("photo-1"))
    await feed(dp, bot, photo_message("photo-2"))
    await feed(dp, bot, text_message("первый"))
    await feed(dp, bot, text_message("второй"))

    assert [m.photo_file_ids for m in caught.materials] == [("photo-1",), ("photo-2",)]
    assert [m.comment.text for m in caught.materials] == ["первый", "второй"]
