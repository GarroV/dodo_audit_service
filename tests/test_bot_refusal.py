"""T127: отказ движка — сырьё, а не сообщение (задача #102).

Так это выглядело до задачи, дословно из прогона сверки:

    Не записал: CLN05 в зоне hot_kitchen уже зафиксировано — запись #1.
    Доснимите фото (audit.py photo 1 --add ...) или поправьте её
    (audit.py edit --n 1 ...)

Человеку с телефоном на точке предлагают запустить командную строку, зона
названа кодом, а текст всегда русский — даже если весь остальной интерфейс у
него английский. Случай при этом частый: тот же пункт в той же зоне аудитор
снимает дважды за обход.

Проверяется здесь три вещи и все три — про то, что бот говорит своими словами:
командной строки и кода зоны в чате нет, пункт и зона названы по-человечески,
а «поправить» и «доснять» делаются кнопками той записи, которая пару заняла.
Сам текст движка при этом не теряется — он уходит в журнал.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from bot_harness import AUDITOR_ID, CHAT_ID, feed, make_bot, photo_message
from bot_harness import callback_query as callback

from src import domain
from src.bot.app import build_dispatcher
from src.bot.config import BotSettings
from src.bot.texts import t
from src.bot.view import zone_title

pytestmark = [pytest.mark.asyncio]

SETTINGS = BotSettings(token="unused-in-tests", allowed_ids=frozenset({AUDITOR_ID}), mode="polling")


def кнопки_под(session: Any, начало: str) -> list[str]:
    """Кнопки того самого сообщения, а не последнего с клавиатурой.

    Последним оно не бывает: после отказа материал уходит дальше и рисует свои
    кнопки поверх (T121). Смотреть на хвост переписки значило бы проверять не
    отказ, а то, что случилось после него.
    """
    for call in session.calls:
        if type(call).__name__ != "SendMessage":
            continue
        if not str(getattr(call, "text", "")).startswith(начало):
            continue
        markup = getattr(call, "reply_markup", None)
        rows = getattr(markup, "inline_keyboard", None) or []
        return [b.callback_data or "" for row in rows for b in row]
    return []


def начата(lang: str = "ru") -> None:
    domain.start_inspection(
        CHAT_ID, "Белград 2", "Плановая", lang, ui_lang=lang, date="2026-08-21", auditor="Гарро"
    )


def занять_пару() -> None:
    """Пара «пункт + зона» занята — то, что случается дважды за обход."""
    domain.add_finding(CHAT_ID, "CLN05", "D1", "hot_kitchen", "нагар на подине печи")


async def повторить_ту_же_фиксацию(lang: str = "ru") -> object:
    """Тот же пункт в той же зоне ещё раз — быстрым путём, как на точке."""
    начата(lang)
    занять_пару()
    bot, session = make_bot()
    await feed(
        build_dispatcher(SETTINGS),
        bot,
        photo_message("frame-2", caption="печь в нагаре, тепловой участок"),
    )
    return session


async def test_отказ_не_зовёт_аудитора_в_командную_строку(domain_env: Path) -> None:
    """Главное в задаче: человеку с телефоном не предлагают запустить `audit.py`."""
    session = await повторить_ту_же_фиксацию()

    сказанное = "\n".join(session.texts)
    assert "audit.py" not in сказанное, "аудитору предложили командную строку"
    assert "--add" not in сказанное and "--n" not in сказанное


async def test_отказ_называет_пункт_и_зону_по_человечески(domain_env: Path) -> None:
    """Ни кода зоны, ни голого кода пункта — вопрос чек-листа и название зоны."""
    session = await повторить_ту_же_фиксацию()

    отказ = next(text for text in session.texts if text.startswith("Не записал"))
    assert отказ == t(
        "record.duplicate",
        "ru",
        n=1,
        item=domain.get_item("CLN05").question("ru"),
        zone=zone_title("hot_kitchen", "ru"),
    )
    assert "hot_kitchen" not in отказ, "зона названа кодом, а не по-человечески"


async def test_под_отказом_стоят_кнопки_той_записи_что_заняла_пару(domain_env: Path) -> None:
    """«Доснимите фото» и «поправьте её» из совета движка делаются кнопками."""
    session = await повторить_ту_же_фиксацию()

    assert кнопки_под(session, "Не записал") == [
        "edit:1:zone",
        "edit:1:level",
        "edit:1:text",
        "edit:1:drop",
    ], "кнопки ведут не к той записи, которая заняла пару"


async def test_отказ_говорит_на_языке_интерфейса(domain_env: Path) -> None:
    """Английский интерфейс — английский отказ. Язык параметр, а не константа."""
    session = await повторить_ту_же_фиксацию("en")

    отказ = next(text for text in session.texts if text.startswith("Not recorded"))
    assert отказ == t(
        "record.duplicate",
        "en",
        n=1,
        item=domain.get_item("CLN05").question("en"),
        zone=zone_title("hot_kitchen", "en"),
    )


async def test_текст_движка_уходит_в_журнал_а_не_в_чат(
    domain_env: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Совет движка не потерян: его читает тот, кто чинит, а не тот, кто на точке."""
    начата()
    занять_пару()
    bot, session = make_bot()

    with caplog.at_level("WARNING"):
        await feed(
            build_dispatcher(SETTINGS),
            bot,
            photo_message("frame-2", caption="печь в нагаре, тепловой участок"),
        )

    assert "audit.py" not in "\n".join(session.texts)
    assert "audit.py" in caplog.text, "разбор отказа не записан в журнал"


async def test_отказ_не_заканчивается_тупиком(domain_env: Path) -> None:
    """Материал после отказа уходит дальше — это поведение T121 не тронуто."""
    session = await повторить_ту_же_фиксацию()

    assert t("record.thinking", "ru") in session.texts, "после отказа материал никуда не пошёл"


# --- то же самое при правке записи ------------------------------------------


async def test_правка_в_занятую_зону_отвечает_по_человечески(domain_env: Path) -> None:
    """Смена зоны — самый частый способ упереться в занятую пару при правке."""
    начата()
    занять_пару()
    domain.add_finding(CHAT_ID, "CLN05", "D1", "dining", "нагар и здесь")
    bot, session = make_bot()

    await feed(build_dispatcher(SETTINGS), bot, callback("ez:2:hot_kitchen"))

    assert session.last_text == t(
        "edit.duplicate",
        "ru",
        n=1,
        item=domain.get_item("CLN05").question("ru"),
        zone=zone_title("hot_kitchen", "ru"),
    )
    assert "audit.py" not in session.last_text
    assert кнопки_под(session, "Не поправил") == [
        "edit:1:zone",
        "edit:1:level",
        "edit:1:text",
        "edit:1:drop",
    ]


async def test_прочий_отказ_правки_тоже_не_показывает_кишки(domain_env: Path) -> None:
    """Класс, не разрешённый пункту: запись цела, а сообщение — человеческое."""
    начата()
    занять_пару()
    bot, session = make_bot()

    await feed(build_dispatcher(SETTINGS), bot, callback("el:1:D3"))

    assert session.last_text == t(
        "edit.failed",
        "ru",
        n=1,
        item=domain.get_item("CLN05").question("ru"),
        zone=zone_title("hot_kitchen", "ru"),
    )
    проверка = domain.get_state(CHAT_ID)
    assert проверка is not None
    запись = проверка.finding(1)
    assert запись is not None and запись.level == "D1", "отказ движка испортил запись"
