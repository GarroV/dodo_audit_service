"""Нажатия, на которые бот обязан промолчать или сказать честно, а не упасть.

Два класса случаев, и оба настоящие, а не выдуманные ради процента.

**Нажатие без сообщения.** Telegram не кладёт в `callback_query` сообщение,
если оно старше 48 часов или пришло из инлайн-режима. Отвечать боту тогда
некуда: чата он не знает. Каждый обработчик обязан это пережить — а их во
второй очереди полтора десятка, и достаточно одного забытого, чтобы аудитор на
точке получил молчащего бота вместо ответа.

**Испорченный код кнопки.** `callback_data` приходит от клиента и может быть
чем угодно: старая версия клавиатуры, обрезанная строка, чужая кнопка. Номер
записи, номер кандидата и номер страницы — числа, и небуквенное значение обязано
кончиться молчанием, а не разбором `int("abc")`.
"""

from __future__ import annotations

import pytest
from bot_harness import AUDITOR_ID, CHAT_ID, callback_query, feed, make_bot, text_message

from src import domain
from src.bot.app import build_dispatcher
from src.bot.config import BotSettings

pytestmark = pytest.mark.asyncio

SETTINGS = BotSettings(token="unused-in-tests", allowed_ids=frozenset({AUDITOR_ID}), mode="polling")

#: Все коды кнопок разговора — по одному на каждый обработчик нажатия.
#: Список перечислен руками, а не собран маской: маска молча пропустила бы
#: обработчик, забытый в этой проверке, — ровно то, ради чего тест и написан.
EVERY_CALLBACK = [
    "rec:analyze:501",
    "rec:pick:0",
    # Быстрый путь (T117): подтверждение найденного без модели и выход к модели.
    "rec:fast",
    "rec:model",
    "rec:zp:hot_kitchen",
    "rec:zm:hot_kitchen",
    "rec:manual",
    "rec:mp:1",
    "rec:mi:0",
    "rec:ml:0:D1",
    "rec:skip",
    "edit:1:zone",
    "ez:1:hot_kitchen",
    "el:1:D1",
    "fin:edit",
    "fin:pick:1",
    "fin:build",
    "fin:nophoto",
    "fin:resume",
    # Расхождение версии методики (T167): выбор аудитора приходит нажатием, и
    # без сообщения оно тоже приходит.
    "fin:ver:sync",
    "fin:ver:keep",
    # Информационная часть в конце проверки (T158): нажатия приходят в своём
    # состоянии диалога, но и без него бот обязан остаться живым.
    "info:skip",
    "info:done",
    "info:yes",
    "info:no",
    "info:save",
]


@pytest.mark.parametrize("data", EVERY_CALLBACK)
async def test_callback_without_a_message_is_survived(domain_env: object, data: str) -> None:
    """Нажатие без сообщения не роняет бота и не заставляет его молчать дальше."""
    domain.start_inspection(CHAT_ID, "Белград 2", "planned", "ru")
    domain.add_finding(CHAT_ID, "CLN05", "D1", "hot_kitchen", "Нагар на подине печи")
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, callback_query(data, with_message=False))

    # Отвечать было некуда — ни одного сообщения бот не отправил.
    assert session.texts == []

    # И он жив: следующее событие обрабатывается как обычно.
    await feed(dp, bot, text_message("/finish"))
    assert session.texts, "после нажатия без сообщения бот перестал отвечать"


@pytest.mark.parametrize(
    "data",
    [
        "rec:analyze:abc",
        "rec:mp:страница",
        "edit:abc:zone",
        "ez:abc:hot_kitchen",
        "ez:1:",
        "el:abc:D1",
        "el:1:",
    ],
)
async def test_broken_callback_data_is_ignored_quietly(domain_env: object, data: str) -> None:
    """Нечисловой номер в коде кнопки — молчание, а не падение на `int()`."""
    domain.start_inspection(CHAT_ID, "Белград 2", "planned", "ru")
    domain.add_finding(CHAT_ID, "CLN05", "D1", "hot_kitchen", "Нагар на подине печи")
    bot, _ = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, callback_query(data))

    # Запись не тронута, оценка не пересчитана, аудитору ничего не наврали.
    state = domain.get_state(CHAT_ID)
    assert state is not None
    assert [f.code for f in state.findings] == ["CLN05"]
    assert state.findings[0].zone == "hot_kitchen"
    assert state.findings[0].level == "D1"


async def test_pick_record_that_is_gone_says_so(domain_env: object) -> None:
    """Номер записи из старого итога, которой уже нет, — честный ответ, не пустота."""
    domain.start_inspection(CHAT_ID, "Белград 2", "planned", "ru")
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, callback_query("fin:pick:7"))

    assert "7" in session.last_text


async def test_edit_from_summary_without_records_says_it_is_empty(domain_env: object) -> None:
    """«Поправить запись» в пустой проверке — не пустой список кнопок, а объяснение."""
    domain.start_inspection(CHAT_ID, "Белград 2", "planned", "ru")
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, callback_query("fin:edit"))

    assert session.keyboard_data() == []
    assert session.texts, "бот промолчал вместо объяснения"
