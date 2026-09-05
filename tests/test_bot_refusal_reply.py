"""T227 (#182): отказ, назвавший запись, — это её показ, и ответом на него её правят.

Отказ занятой пары говорит с аудитором про конкретную запись: печатает её номер
(«поправьте запись #1») и ставит под собой её же кнопки правки. Аудитор делает
ровно то, что ему предложили, — отвечает на это сообщение словами. До задачи
такой ответ в карту «сообщение → запись» не попадал, уходил мимо роутера правки
дальше по цепочке и заводил НОВУЮ запись, приклеив к ней старейший ждущий кадр:
в смоуке так появилась запись с посторонней фотографией.

Механизм правки ответом уже был (T204), не хватало одной связи — этот файл про
неё и про её границу.

**Отказ с записью правит именно её.** Ответ адресует названную запись, и записей
не прибавляется.

**Чужой кадр остаётся ждать.** Он не имеет к отказу отношения, и ответ на отказ
не имеет права его израсходовать: кадр без записи виден в конце проверки, а
кадр в ЧУЖОЙ записи уезжает партнёру.

**Отказ при правке кнопкой — то же самое.** Дверь другая, запись названа так же.

**Отказ, который записи не назвал, в карту не попадает.** Править ответом там
нечего, и связывание комментария с кадром обязано работать как прежде.
"""

from __future__ import annotations

from typing import Any

import pytest
from bot_harness import (
    AUDITOR_ID,
    CHAT_ID,
    bot_message,
    feed,
    make_bot,
    photo_message,
    stub_classify,
    suggestion,
    text_message,
)
from bot_harness import callback_query as callback

from src import domain
from src.bot import sidecar
from src.bot.app import build_dispatcher
from src.bot.config import BotSettings
from src.domain import Finding, get_state

pytestmark = pytest.mark.asyncio

SETTINGS = BotSettings(token="unused-in-tests", allowed_ids=frozenset({AUDITOR_ID}), mode="polling")

#: Кадр, который прислали и ещё не объяснили словами: он ждёт в очереди и к
#: отказу отношения не имеет. Именно он уезжал в чужую запись.
СТОРОННИЙ_КАДР = "кадр-без-слов"

#: Тот же пункт в той же зоне ещё раз — как на точке, где одно и то же снимают
#: дважды за обход. Ведёт к отказу занятой пары.
СНОВА_ТО_ЖЕ = "печь в нагаре, тепловой участок"

#: Ответ аудитора формы из решения D081: названы и место, и объект. Ведёт к
#: другому пункту И в другую зону — то есть правку видно.
ОТВЕТ = "посудный участок, раковина и смеситель грязные"

#: Слова про печь без названия зоны: зону подставит память (D048).
ПЕЧЬ = "печь грязная"

ОТПРАВЛЯЮЩИЕ = {"SendMessage", "SendPhoto", "SendDocument", "EditMessageText"}


def начата() -> None:
    domain.start_inspection(CHAT_ID, "Белград 2", "planned", "ru")


def занять_пару() -> None:
    domain.add_finding(CHAT_ID, "CLN05", "D1", "hot_kitchen", "нагар на подине печи")


def записи() -> list[Finding]:
    state = get_state(CHAT_ID)
    return [] if state is None else list(state.findings)


def кадры() -> list[str]:
    return [photo for finding in записи() for photo in finding.photos]


def номер_сообщения(session: Any, начало: str) -> int:
    """Номер сообщения бота, которое начинается с этих слов.

    По номеру, а не по последнему отправленному: после отказа бот успевает
    сказать что-нибудь ещё, и ответ на хвост переписки проверял бы не отказ.
    """
    номера = iter(session.sent_ids)
    for call in session.calls:
        name = type(call).__name__
        if name not in ОТПРАВЛЯЮЩИЕ:
            continue
        message_id = next(номера)
        if name == "SendMessage" and str(getattr(call, "text", "")).startswith(начало):
            return message_id
    raise AssertionError(f"бот не отправил сообщения, начинающегося с «{начало}»")


async def сцена(dp: Any, bot: Any) -> None:
    """Запись #1 занята, сторонний кадр ждёт в очереди, дальше — повтор той же пары."""
    начата()
    занять_пару()
    await feed(dp, bot, photo_message(СТОРОННИЙ_КАДР))


async def test_ответ_на_отказ_правит_названную_запись_а_не_заводит_новую(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """То, что видели в смоуке: вместо правки появлялась запись с чужим кадром."""
    stub_classify(monkeypatch, suggestion())
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    await сцена(dp, bot)

    await feed(dp, bot, photo_message("кадр-повтор", caption=СНОВА_ТО_ЖЕ))
    отказ = номер_сообщения(session, "Не записал")
    await feed(dp, bot, text_message(ОТВЕТ, reply_to=bot_message(отказ)))

    assert [(f.n, f.code, f.zone) for f in записи()] == [(1, "CLN02", "dishwashing")], (
        "ответ на отказ не поправил названную им запись"
    )
    assert СТОРОННИЙ_КАДР not in кадры(), "в запись уехал кадр, к которому отказ отношения не имел"


async def test_сторонний_кадр_после_ответа_на_отказ_всё_ещё_ждёт(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Кадр не израсходован, а именно ждёт: следующий комментарий берёт его.

    Без этого «кадр не уехал» доказывалось бы и потерянным кадром, а потеря
    здесь не лучше: кадр без записи аудитор видит в конце проверки только если
    он всё ещё в очереди.
    """
    stub_classify(monkeypatch, suggestion())
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    await сцена(dp, bot)

    await feed(dp, bot, photo_message("кадр-повтор", caption=СНОВА_ТО_ЖЕ))
    отказ = номер_сообщения(session, "Не записал")
    await feed(dp, bot, text_message(ОТВЕТ, reply_to=bot_message(отказ)))

    # Пара CLN05 + hot_kitchen освободилась правкой — слова про печь ложатся
    # записью, и ждущий кадр достаётся ей.
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")
    await feed(dp, bot, text_message(ПЕЧЬ))

    assert [(f.n, f.code, f.zone) for f in записи()] == [
        (1, "CLN02", "dishwashing"),
        (2, "CLN05", "hot_kitchen"),
    ], "кадр достался не тем словам"
    assert записи()[1].photos == [СТОРОННИЙ_КАДР], (
        "сторонний кадр не дождался своих слов: его забрал ответ на отказ"
    )


async def test_отказ_при_правке_кнопкой_тоже_правится_ответом(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Дверь другая — правило то же: отказ назвал запись, значит говорит о ней."""
    stub_classify(monkeypatch, suggestion())
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    await сцена(dp, bot)
    domain.add_finding(CHAT_ID, "CLN05", "D1", "dining", "нагар и здесь")

    await feed(dp, bot, callback("ez:2:hot_kitchen"))
    отказ = номер_сообщения(session, "Не поправил")
    await feed(dp, bot, text_message(ОТВЕТ, reply_to=bot_message(отказ)))

    assert [(f.n, f.code, f.zone) for f in записи()] == [
        (1, "CLN02", "dishwashing"),
        (2, "CLN05", "dining"),
    ], "ответ на отказ правки не поправил названную запись"
    assert СТОРОННИЙ_КАДР not in кадры()


async def test_отказ_без_записи_в_карту_не_попадает(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Граница правила: править ответом нечего, и прежнее связывание цело.

    Класс, не разрешённый пункту, — отказ без занятой пары. Ответ на него
    обязан работать как раньше: он комментарий к ждущему кадру, и запись из них
    получается одна.
    """
    stub_classify(monkeypatch, suggestion())
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    await сцена(dp, bot)

    await feed(dp, bot, callback("el:1:D3"))
    отказ = номер_сообщения(session, "Не поправил")
    sidecar.remember_zone(CHAT_ID, "dishwashing")
    await feed(dp, bot, text_message(ОТВЕТ, reply_to=bot_message(отказ)))

    assert sidecar.record_of(CHAT_ID, отказ) is None, "отказ без записи попал в карту сообщений"
    assert СТОРОННИЙ_КАДР in кадры(), "ответ не на запись перестал связываться с ждущим кадром"


async def test_сообщение_отказа_запомнено_как_показ_названной_записи(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Та же связь напрямую в карте: адресность правки держится ею, а не текстом."""
    stub_classify(monkeypatch, suggestion())
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    await сцена(dp, bot)

    await feed(dp, bot, photo_message("кадр-повтор", caption=СНОВА_ТО_ЖЕ))

    assert sidecar.record_of(CHAT_ID, номер_сообщения(session, "Не записал")) == 1
