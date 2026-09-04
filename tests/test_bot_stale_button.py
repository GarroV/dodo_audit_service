"""Кнопка, которую больше некому обработать, обязана ответить (T134, issue #105).

Код кнопки из прошлой версии продукта не попадал ни в один обработчик — и это
означало не «бот промолчал», а хуже: нажатие остаётся неотвеченным, и Telegram
крутит человеку часики до собственного таймаута. Проверено фактом:
`callback_query("start:kind:0")` (старый формат кода вида проверки) давал ноль
вызовов API — ни сообщения, ни `answerCallbackQuery`.

Случай не выдуманный. Сообщения с кнопками висят в чате месяцами: аудитор
пролистал переписку вверх, нажал на предложение позапрошлой проверки — и
получил зависший интерфейс вместо ответа. Тот же класс — кнопка снятого
обработчика (`rec:fast` жил до T121) и кнопка шага, который уже пройден.

Ответ поэтому нужен любой ценой, и каналов у него два. Обычный — сообщение в
чат. Но у нажатия старше 48 часов Telegram не кладёт сообщения вовсе, и
отвечать боту некуда: тогда единственный канал — текст самого
`answerCallbackQuery`, всплывающим окном. Отсюда предел в 200 байт, который
проверяется здесь же: Telegram режет текст окна молча.

Главный риск правки — что последний рубеж съест настоящие нажатия. Он и
проверяется отдельным тестом: мастер начала проверки должен пройти целиком, ни
разу не выдав ответ рубежа.
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
from src.bot.keyboards import NEW_INSPECTION_CALLBACK
from src.bot.texts import ALERT_TEXT_LIMIT, UI_LANGS, t
from src.domain import get_state, start_inspection

pytestmark = pytest.mark.asyncio

SETTINGS = BotSettings(
    token="unused-in-tests",
    allowed_ids=frozenset({AUDITOR_ID}),
    mode="polling",
    auditor_names={},
)

#: Ровно тот код из дефекта: вид проверки в старом формате — номером, а не
#: кодом. Сегодня таких кнопок в продукте нет, а в переписке аудитора есть.
STALE = "start:kind:0"


def answers(session: Any) -> list[Any]:
    """Ответы на нажатие: их отсутствие и есть крутящиеся часики у человека."""
    return [c for c in session.calls if type(c).__name__ == "AnswerCallbackQuery"]


async def test_кнопка_из_прошлой_версии_получает_и_ответ_и_объяснение(
    domain_env: object,
) -> None:
    """Часики сняты, и человек прочитал, что делать, — вместо тишины до таймаута."""
    start_inspection(CHAT_ID, "Белград 2", "Плановая", "ru")
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, callback_query(STALE))

    assert answers(session), "нажатие осталось неотвеченным — у человека крутятся часики"
    assert session.last_text == t("error.button_gone", "ru")


async def test_нажатие_старше_48_часов_отвечает_всплывающим_окном(
    domain_env: object,
) -> None:
    """Сообщения у такого нажатия нет — отвечать в чат некуда, но молчать нельзя."""
    start_inspection(CHAT_ID, "Белград 2", "Плановая", "ru")
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, callback_query(STALE, with_message=False))

    assert session.texts == [], "отвечать было некуда, а бот отправил сообщение"
    replies = answers(session)
    assert len(replies) == 1
    assert replies[0].text == t("error.button_gone", "ru")
    assert replies[0].show_alert is True, "без окна текст ответа человек не увидит"


async def test_живые_кнопки_рубежом_не_перехвачены(domain_env: object) -> None:
    """Главный риск правки: последний рубеж не имеет права съедать настоящие нажатия."""
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, text_message("/start"))
    await feed(dp, bot, callback_query(NEW_INSPECTION_CALLBACK))
    await feed(dp, bot, text_message("Белград 2"))
    await feed(dp, bot, callback_query("start:kind:planned"))
    await feed(dp, bot, callback_query("start:lang:ru"))

    assert get_state(CHAT_ID) is not None, "мастер не дошёл до старта проверки"
    gone = t("error.button_gone", "ru")
    assert all(gone not in text for text in session.texts), "рубеж перехватил живую кнопку"


async def test_на_английском_стенде_рубеж_отвечает_по_английски(domain_env: object) -> None:
    """Язык — параметр и у последнего рубежа: проверка английская, ответ тоже."""
    start_inspection(CHAT_ID, "Belgrade 2", "Planned", "en", ui_lang="en")
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, callback_query(STALE))

    assert session.last_text == t("error.button_gone", "en")


@pytest.mark.parametrize("lang", UI_LANGS)
async def test_текст_рубежа_влезает_во_всплывающее_окно(lang: str) -> None:
    """Предел Telegram на текст окна — 200 знаков, и режет он молча.

    Проверяются оба языка, а не «самый длинный»: правку текста делают на одном
    языке, а переводят на второй отдельным движением, и вылезает за предел
    обычно как раз второй.
    """
    assert len(t("error.button_gone", lang)) <= ALERT_TEXT_LIMIT
