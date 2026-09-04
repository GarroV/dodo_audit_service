"""Кадр без записи показывается самим кадром, а не номером сообщения (T138, #109).

Механизм T068 правильный и уже спасал сценарий: аудитор прислал два кадра с
комментариями, ни один не подтвердил, и завершение об этом прямо сказало.
Бесполезной была адресация — «кадр из сообщения 108». **Номер сообщения в
телеграме человеку не показывается**, найти по нему кадр нельзя: аудитор знал,
что потерял два кадра, и не знал какие.

Поэтому кадр теперь возвращается в чат сам и ответом на то сообщение, которым
пришёл: и в переписке видно место, и перед глазами сама фотография. Ответ
делается «мягким» (`allow_sending_without_reply`) — исходное сообщение аудитор
мог удалить, и отказ телеграма отменил бы показ ровно того кадра, ради которого
всё и затевалось.

Пачка при этом ограничена: тридцать фотографий подряд похоронят и итог, и
кнопки завершения. Сверх предела бот честно называет, сколько осталось, и как
их увидеть, — а не молчит и не притворяется, что показал всё.
"""

from __future__ import annotations

from typing import Any

import pytest
from bot_harness import (
    AUDITOR_ID,
    CHAT_ID,
    feed,
    make_bot,
    photo_message,
    text_message,
)

from src.bot.app import build_dispatcher
from src.bot.config import BotSettings
from src.bot.routers.records import UNCLAIMED_SHOWN_LIMIT
from src.bot.texts import t
from src.domain import add_finding, attach_photo, start_inspection

pytestmark = pytest.mark.asyncio

SETTINGS = BotSettings(
    token="unused-in-tests",
    allowed_ids=frozenset({AUDITOR_ID}),
    mode="polling",
    auditor_names={},
)


def started() -> None:
    start_inspection(CHAT_ID, "Белград 2", "planned", "ru", date="2026-08-21", auditor="Гарро")


def отправленные_кадры(session: Any) -> list[tuple[str, int | None]]:
    """Кадр и сообщение, ответом на которое он ушёл, — по каждому `SendPhoto`."""
    return [
        (str(call.photo), call.reply_to_message_id)
        for call in session.calls
        if type(call).__name__ == "SendPhoto"
    ]


async def test_кадр_без_записи_возвращается_самим_кадром(domain_env: object) -> None:
    """Сам дефект: аудитор обязан УВИДЕТЬ, какой кадр потерян."""
    started()
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, photo_message("lonely-frame", message_id=321))
    session.clear()
    await feed(dp, bot, text_message("/finish"))

    assert ("lonely-frame", 321) in отправленные_кадры(session), (
        "кадр без записи не показан кадром и не привязан к своему сообщению"
    )


async def test_номер_сообщения_человеку_больше_не_называется(domain_env: object) -> None:
    """Номер в телеграме не виден: назвать его — то же, что промолчать."""
    started()
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, photo_message("lonely-frame", message_id=321))
    session.clear()
    await feed(dp, bot, text_message("/finish"))

    assert "321" not in "\n".join(session.texts)


async def test_кадры_без_записи_названы_числом_как_и_раньше(domain_env: object) -> None:
    """Механизм T068 не пропал: сколько кадров осталось, сказано словами."""
    started()
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, photo_message("frame-1", message_id=321))
    await feed(dp, bot, photo_message("frame-2", message_id=322))
    session.clear()
    await feed(dp, bot, text_message("/finish"))

    assert t("finish.unclaimed", "ru", count=2) in session.texts


async def test_кадр_попавший_в_запись_обратно_не_присылается(domain_env: object) -> None:
    """Разобранный кадр в этом списке — шум, а шум прячет настоящую потерю."""
    started()
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, photo_message("used-frame", message_id=322))
    add_finding(CHAT_ID, "CLN05", "D1", "hot_kitchen", "Нагар на подине печи")
    attach_photo(CHAT_ID, 1, "used-frame")
    session.clear()
    await feed(dp, bot, text_message("/finish"))

    assert отправленные_кадры(session) == []


async def test_кадры_возвращаются_в_порядке_прихода(domain_env: object) -> None:
    """Порядок — тот же, в каком аудитор их слал: иначе он их не узнает."""
    started()
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    for n in range(3):
        await feed(dp, bot, photo_message(f"frame-{n}", message_id=400 + n))
    session.clear()
    await feed(dp, bot, text_message("/finish"))

    assert отправленные_кадры(session) == [(f"frame-{n}", 400 + n) for n in range(3)]


async def test_пачка_ограничена_и_остаток_назван_а_не_замолчан(domain_env: object) -> None:
    """Сверх предела бот говорит, сколько кадров осталось и как их увидеть."""
    started()
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    сколько = UNCLAIMED_SHOWN_LIMIT + 3
    for n in range(сколько):
        await feed(dp, bot, photo_message(f"frame-{n}", message_id=600 + n))
    session.clear()
    await feed(dp, bot, text_message("/finish"))

    assert len(отправленные_кадры(session)) == UNCLAIMED_SHOWN_LIMIT
    assert t("finish.unclaimed_rest", "ru", rest=3) in session.texts


async def test_показ_переживает_перезапуск(domain_env: object) -> None:
    """Список кадров лежит файлом рядом с проверкой, а не в памяти диспетчера."""
    started()
    bot, session = make_bot()

    await feed(build_dispatcher(SETTINGS), bot, photo_message("lonely-frame", message_id=333))
    session.clear()
    await feed(build_dispatcher(SETTINGS), bot, text_message("/finish"))

    assert ("lonely-frame", 333) in отправленные_кадры(session)


async def test_ответ_мягкий_чтобы_удалённое_сообщение_не_отменяло_показ(
    domain_env: object,
) -> None:
    """Исходное сообщение аудитор мог удалить — кадр обязан дойти всё равно."""
    started()
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, photo_message("lonely-frame", message_id=321))
    session.clear()
    await feed(dp, bot, text_message("/finish"))

    отправка = next(c for c in session.calls if type(c).__name__ == "SendPhoto")
    assert отправка.allow_sending_without_reply is True
