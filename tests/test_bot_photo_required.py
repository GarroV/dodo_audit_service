"""Фотофиксация обязательна всегда, и человек обязан это понять (T160, D078).

Владелец пересмотрел вчерашний собственный ответ. Сутки назад — «такие пункты
можно и пустить без фотофиксации» (D071), сегодня — дословно: «Нет, фото
фиксация всегда нужна . Либо давай сделаем так что бот уведомляет что есть с
проблема . В обще, надо чтобы человек понял что фото должно быть».

Запрета в коде не прибавилось: записи без кадра бот не делал и раньше — по
одному комментарию материал не собирается вовсе. Прибавиться должно понимание.
До этой задачи аудитор на текст без кадра читал «Не вижу кадра, к которому это
относится», то есть жалобу продукта на себя: бот чего-то не видит. Человек на
точке из такой фразы выносит «бот сломался», а не «так устроена проверка», —
и второй раз попробует то же самое.

Отсюда два требования, и каждое проверяется здесь отдельно.

**Правило звучит ЗАРАНЕЕ.** Первое сообщение проверки — единственное место, где
его можно сказать до первой ошибки, а не после. То же при продолжении
прерванной проверки: аудитор возвращается к ней через день и другую сессию, и
первого сообщения он мог не читать.

**Правило звучит ОДНОЙ формулировкой.** Источник у неё один
(`material.photo_required`), и оба места собираются `with_photo_rule`. Две
копии одной мысли разъезжаются: в отказе поправили, в приветствии забыли — и
человек читает разные правила в одном продукте.

Проверяется здесь именно ЭТО, а не буквальный текст: тест на дословную строку
краснел бы от любой вычитки формулировки и ничего бы не стерёг.
"""

from __future__ import annotations

from typing import Any

import pytest
from bot_harness import (
    AUDITOR_ID,
    CHAT_ID,
    callback_query,
    feed,
    make_bot,
    text_message,
)

from src.bot.app import build_dispatcher
from src.bot.config import BotSettings
from src.bot.keyboards import (
    NEW_INSPECTION_CALLBACK,
    RESUME_CONTINUE_CALLBACK,
)
from src.bot.texts import t, with_photo_rule
from src.domain import get_state, start_inspection

pytestmark = pytest.mark.asyncio

SETTINGS = BotSettings(
    token="unused-in-tests",
    allowed_ids=frozenset({AUDITOR_ID}),
    mode="polling",
    auditor_names={},
)


async def walk_wizard(dp: Any, bot: Any, *, report_lang: str = "ru") -> None:
    """Мастер целиком, настоящими событиями: вход → название → вид → язык."""
    await feed(dp, bot, text_message("/start"))
    await feed(dp, bot, callback_query(NEW_INSPECTION_CALLBACK))
    await feed(dp, bot, text_message("Белград 2"))
    await feed(dp, bot, callback_query("start:kind:planned"))
    await feed(dp, bot, callback_query(f"start:lang:{report_lang}"))


async def test_первое_сообщение_проверки_называет_правило_фотофиксации(
    domain_env: object,
) -> None:
    """Правило сказано до первой ошибки, а не в ответ на неё."""
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await walk_wizard(dp, bot)

    assert get_state(CHAT_ID) is not None, "мастер не начал проверку — тест проверяет не то"
    assert t("material.photo_required", "ru") in session.last_text


async def test_комментарий_без_кадра_читается_ожиданием_а_не_жалобой_продукта(
    domain_env: object,
) -> None:
    """Ожидание несёт то же правило, что и приветствие, и записи до кадра всё ещё нет.

    С T229 (D090) комментарий без кадра больше не отказ — бот придерживает
    слова и говорит, что ждёт фотографию. Правило фотофиксации (D078) при этом
    в силе: записи без кадра как не было, так и нет.
    """
    start_inspection(CHAT_ID, "Белград 2", "planned", "ru")
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, text_message("в зале грязный пол"))

    assert session.last_text == with_photo_rule(t("material.waiting_photo", "ru"), "ru")
    assert t("material.photo_required", "ru") in session.last_text
    state = get_state(CHAT_ID)
    assert state is not None
    assert list(state.findings) == [], "запись без кадра появиться не должна (D078)"


async def test_продолжение_прерванной_проверки_повторяет_правило(
    domain_env: object,
) -> None:
    """Возврат через день — то же начало работы, и правило звучит снова."""
    start_inspection(CHAT_ID, "Белград 2", "planned", "ru")
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, text_message("/start"))
    await feed(dp, bot, callback_query(RESUME_CONTINUE_CALLBACK))

    assert t("material.photo_required", "ru") in session.last_text


async def test_на_английской_проверке_правило_звучит_по_английски(
    domain_env: object,
) -> None:
    """Язык — параметр и здесь: русское правило в английском разговоре недопустимо."""
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await walk_wizard(dp, bot, report_lang="en")
    started = session.last_text
    await feed(dp, bot, text_message("dirty floor in the dining room"))

    assert t("material.photo_required", "en") in started
    assert t("material.photo_required", "en") in session.last_text
    assert t("material.photo_required", "ru") not in session.last_text
