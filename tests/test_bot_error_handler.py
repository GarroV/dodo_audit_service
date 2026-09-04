"""T126: испорченное состояние не делает бота немым (задача #101).

Прогон сверки: `inspection.json` испорчен — `/start`, `/finish`, приём кадра и
правка все четыре бросают отказ, аудитор не получает **ничего**. Тексты самих
отказов при этом написаны для человека, но доставить их некому: глобального
обработчика у диспетчера не было, а неперехваченное исключение aiogram глотает.

Выйти из тупика из чата тоже было нельзя: `/start` падал ровно так же.

Здесь проверяется три вещи, и все три — про молчание, а не про сам отказ:
бот отвечает на каждом входе; `/start` работает и на испорченном состоянии,
потому что он и есть выход; текст исключения в чат не уходит — там причина и
что делать, а разбор с путями к файлам остаётся в журнале.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from aiogram import Bot
from aiogram.methods import TelegramMethod
from aiogram.types import CallbackQuery, Message
from bot_harness import (
    AUDITOR_ID,
    CHAT_ID,
    RecordingSession,
    feed,
    make_bot,
    photo_message,
    text_message,
)
from bot_harness import callback_query as callback

from src.bot.app import build_dispatcher
from src.bot.config import BotSettings
from src.bot.texts import t
from src.domain import start_inspection
from src.domain.config import check_environment
from src.domain.engine import state_file

pytestmark = pytest.mark.asyncio

SETTINGS = BotSettings(token="unused-in-tests", allowed_ids=frozenset({AUDITOR_ID}), mode="polling")

#: Так выглядит состояние, побывавшее в обрыве записи: файл есть, JSON — нет.
МУСОР = "{это не json"


def испортить_состояние() -> Path:
    """Начать проверку и испортить её файл — как при обрыве записи на точке."""
    start_inspection(CHAT_ID, "Белград 2", "planned", "ru", date="2026-08-21", auditor="Гарро")
    файл = state_file(CHAT_ID, check_environment())
    файл.write_text(МУСОР, encoding="utf-8")
    return файл


async def test_старт_на_испорченном_состоянии_отвечает_и_даёт_выход(domain_env: Path) -> None:
    """`/start` — единственный выход из тупика, и он обязан работать всегда."""
    испортить_состояние()
    bot, session = make_bot()

    await feed(build_dispatcher(SETTINGS), bot, text_message("/start"))

    assert session.texts, "на `/start` бот не ответил ничего — выхода из тупика нет"
    assert session.last_text == t("start.state_broken", "ru")
    assert session.keyboard_data() == ["start:new"], "выход назван словами, но нажать нечего"


async def test_новая_проверка_после_повреждения_доходит_до_мастера(domain_env: Path) -> None:
    """Кнопка из тупика обязана вести дальше, а не в тот же тупик."""
    испортить_состояние()
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, text_message("/start"))
    await feed(dp, bot, callback("start:new"))

    assert session.last_text == t("start.ask_unit", "ru")


async def test_испорченное_состояние_не_переписывается_молча(domain_env: Path) -> None:
    """Повреждение названо вслух, а файл до решения человека не тронут (T052)."""
    файл = испортить_состояние()
    bot, session = make_bot()

    await feed(build_dispatcher(SETTINGS), bot, text_message("/start"))

    assert файл.read_text(encoding="utf-8") == МУСОР, "бот переписал повреждённое состояние сам"
    assert session.texts


async def test_на_кадре_с_испорченным_состоянием_бот_не_молчит(domain_env: Path) -> None:
    """Любой другой вход тоже отвечает — молчание было главным в задаче."""
    испортить_состояние()
    bot, session = make_bot()

    await feed(build_dispatcher(SETTINGS), bot, photo_message("frame-1", caption="печь грязная"))

    assert session.texts, "на кадр бот не ответил ничего"


async def test_подробности_отказа_остаются_в_журнале_а_не_в_чате(
    domain_env: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Путь к файлу и текст исключения — для того, кто чинит, а не для точки."""
    файл = испортить_состояние()
    bot, session = make_bot()

    with caplog.at_level("ERROR"):
        await feed(build_dispatcher(SETTINGS), bot, text_message("/finish"))

    сказанное = "\n".join(session.texts)
    assert сказанное, "на `/finish` бот не ответил ничего"
    assert str(файл) not in сказанное, "путь к файлу проверки ушёл аудитору в чат"
    assert "json" not in сказанное.lower(), "текст исключения показан аудитору как есть"
    assert str(файл) in caplog.text, "разбор не записан в журнал — чинить будет нечем"


#: Каждый вход читает состояние тем же путём, каким его читал `/start` до
#: T126 — напрямую, без своего `except DomainError`, — и потому обязан
#: пройти через тот же последний рубеж (`on_unexpected_error`).
#: Кнопка правки собрана в формате `edit:{номер}:{что}` (`src/bot/keyboards.py`,
#: `EDIT_PREFIX`) — тем же, что и в `tests/test_bot_callback_edges.py`. Задание
#: называло случай `edit:zone:1`, но в этом порядке `on_edit` (`src/bot/routers/edit.py`)
#: видит нечисловой номер записи и выходит раньше, чем доходит до чтения
#: состояния, — испорченный файл тогда вообще не участвует в проверке.
@pytest.mark.parametrize(
    "собрать_событие",
    [
        pytest.param(lambda: text_message("/finish"), id="finish"),
        pytest.param(lambda: text_message("/undo"), id="undo"),
        pytest.param(lambda: callback("edit:1:zone"), id="кнопка_правки"),
        pytest.param(lambda: text_message("печь грязная"), id="комментарий"),
    ],
)
async def test_каждый_вход_на_испорченном_состоянии_отвечает_человеку_а_не_молчит(
    domain_env: Path, собрать_событие: Callable[[], Message | CallbackQuery]
) -> None:
    """`/finish`, `/undo`, кнопка правки и комментарий читают состояние напрямую.

    Все четыре обязаны не только ответить (как в задаче T126), но ответить
    именно текстом `error.unexpected` — сообщением для человека на точке, а не
    пересказом исключения из `domain.get_state`.
    """
    испортить_состояние()
    bot, session = make_bot()

    await feed(build_dispatcher(SETTINGS), bot, собрать_событие())

    assert session.last_text == t("error.unexpected", "ru")


async def test_нажатие_без_сообщения_на_испорченном_состоянии_не_падает_и_молчит(
    domain_env: Path,
) -> None:
    """Отвечать некуда — у нажатия нет сообщения, и это не повод падать.

    Испорченное состояние здесь ни при чём по построению: `on_edit`
    (`src/bot/routers/edit.py`) уходит через `chat_of(callback) is None` раньше,
    чем успевает прочитать `domain.get_state`. Прогоняется всё равно на
    испорченном файле — ровно так, как обычно и совпадает несчастье на точке:
    старое нажатие приходит в чат с тем же убитым состоянием.
    """
    испортить_состояние()
    bot, session = make_bot()

    # Само по себе то, что `feed` не бросает исключение наружу, и есть первая
    # часть проверки: упади обработчик — тест упал бы здесь же, без assert.
    await feed(build_dispatcher(SETTINGS), bot, callback("edit:1:zone", with_message=False))

    assert session.texts == [], "отвечать было некуда, а бот всё равно что-то отправил"


async def test_отказ_телеграма_на_сообщение_о_сбое_не_роняет_обработчик(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Последний рубеж обязан пережить и отказ самого телеграма на свой ответ.

    `on_unexpected_error` (`src/bot/app.py`) уже ловит эту неудачу в
    собственном `except Exception` — здесь проверяется, что защита реальна:
    `feed` не бросает исключение наружу, а причина остаётся в журнале.
    """
    испортить_состояние()
    bot, session = make_bot()
    session_ = bot.session
    assert isinstance(session_, RecordingSession)
    исходный_make_request = session_.make_request

    async def падающий_на_sendmessage(
        bot: Bot,
        method: TelegramMethod[Any],
        timeout: int | None = None,  # noqa: ASYNC109 — сигнатура из BaseSession
    ) -> Any:
        if type(method).__name__ == "SendMessage":
            raise RuntimeError("телеграм отказал")
        return await исходный_make_request(bot, method, timeout)

    monkeypatch.setattr(session_, "make_request", падающий_на_sendmessage)

    with caplog.at_level("ERROR"):
        await feed(build_dispatcher(SETTINGS), bot, text_message("/finish"))

    assert session.texts == [], "SendMessage должен был отказать, а не пройти"
    assert "не удалось сказать аудитору о сбое" in caplog.text
