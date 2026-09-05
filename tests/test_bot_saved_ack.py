"""Отбивка о сохранении — одна на ВСЕ пути добавления записи (T230, решение D090).

До задачи «сохранено» говорил только один путь из трёх, и говорил своими
словами. Сверка со списком нарушений открывала показ фразой про запись без
подтверждения; кнопка кандидата и ручной перечень не говорили о сохранении
вовсе — аудитор читал строку `#1 CLN05 · D1 · Тепловой участок` и сам решал,
уехало это в отчёт или ещё нет. Решение D090 требует пары «принял / сохранено»
на каждом способе завести запись и одной формулировки на все: разные слова об
одном событии читаются как разные события.

Что защищает этот файл — четыре вещи.

**Отбивка есть на каждом пути добавления.** Их три: сверка по словам, кнопка
кандидата модели, ручной перечень пунктов.

**Формулировка у них одна.** Сверяется не буквальный текст, а совпадение между
путями и с единственным ключом каталога: тест на дословную строку краснел бы от
любой вычитки и ничего бы не стерёг.

**Правка записи отбивку о сохранении НЕ повторяет.** Записи там не прибавилось,
и «сохранено» отправило бы аудитора искать в переписке вторую.

**Язык — параметр.** На английской проверке отбивка английская.
"""

from __future__ import annotations

from typing import Any

import pytest
from bot_harness import (
    AUDITOR_ID,
    CHAT_ID,
    bot_message,
    candidate,
    feed,
    make_bot,
    manual,
    photo_message,
    stub_classify,
    stub_manual,
    suggestion,
    text_message,
)
from bot_harness import callback_query as callback

from src.bot import sidecar
from src.bot.app import build_dispatcher
from src.bot.config import BotSettings
from src.bot.texts import t
from src.bot.view import stored_headline
from src.domain import Finding, get_state, start_inspection
from src.recognize.errors import ModelUnavailable

pytestmark = pytest.mark.asyncio

SETTINGS = BotSettings(token="unused-in-tests", allowed_ids=frozenset({AUDITOR_ID}), mode="polling")

#: Однозначная фраза синтетической карты: строка «Печь» плюс колонка «грязная»
#: → CLN05. Ведёт запись быстрым путём, без модели.
CLEAR = "печь грязная"

#: Строка карты произнесена, но по словам не видно, грязь это или поломка:
#: материал уходит модели. Тем же приёмом разводит пути `test_bot_record_manual`.
AMBIGUOUS = "печь, посмотри что тут"


def started(lang: str = "ru") -> None:
    start_inspection(CHAT_ID, "Белград 2", "planned", lang, ui_lang=lang)
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")


def findings() -> list[Finding]:
    state = get_state(CHAT_ID)
    return [] if state is None else list(state.findings)


async def by_words(dp: Any, bot: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Путь первый: сверка со списком нарушений записала сама (T117, D064)."""
    stub_classify(monkeypatch, suggestion())
    await feed(dp, bot, photo_message("frame-words", caption=CLEAR))


async def by_button(dp: Any, bot: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Путь второй: кандидат модели, запись по нажатию (T055)."""
    stub_classify(monkeypatch, suggestion(candidate("CLN06", "D1", "hot_kitchen", "мебель")))
    await feed(dp, bot, photo_message("frame-button", caption=AMBIGUOUS))
    await feed(dp, bot, callback("rec:pick:0"))


async def by_manual(dp: Any, bot: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Путь третий: модели нет, пункт выбран в ручном перечне (T034)."""
    stub_classify(monkeypatch, ModelUnavailable("нет сети"))
    stub_manual(monkeypatch, (manual("PRD06", ("D1",), "Пункт без сети"),))
    await feed(dp, bot, photo_message("frame-manual", caption=AMBIGUOUS))
    await feed(dp, bot, callback("rec:mi:0"))


ПУТИ = (by_words, by_button, by_manual)


@pytest.mark.parametrize("путь", ПУТИ, ids=["слова", "кнопка", "перечень"])
async def test_каждый_путь_добавления_говорит_о_сохранении(
    domain_env: object, monkeypatch: pytest.MonkeyPatch, путь: Any
) -> None:
    """Пара «принял / сохранено» нужна каждому способу завести запись (D090)."""
    started()
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await путь(dp, bot, monkeypatch)

    отбивка = stored_headline("ru")
    # Пустая отбивка нашлась бы в любом тексте, и проверка ниже зеленела бы на
    # боте, который о сохранении молчит. Сторож стоит здесь, а не только в
    # каталоге текстов: сюда смотрит тот, кто будет менять эту функцию.
    assert отбивка.strip(), "отбивка о сохранении пуста — искать её в тексте бессмысленно"
    assert len(findings()) == 1, "путь не довёл до записи — тест проверяет не то"
    assert отбивка in session.last_text, (
        f"показ записи не говорит о сохранении: {session.last_text!r}"
    )


async def test_формулировка_отбивки_одна_на_все_пути(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Сердце задачи: три пути — одни и те же слова о сохранении.

    Сверяется совпадение между путями, а не буквальный текст: буквальный
    краснел бы от вычитки формулировки, а разъезжаются пути именно молча.
    """
    сказанное: list[str] = []
    for путь in ПУТИ:
        started()
        bot, session = make_bot()
        dp = build_dispatcher(SETTINGS)
        await путь(dp, bot, monkeypatch)
        сказанное.append(session.last_text)
        sidecar.reset(CHAT_ID)

    отбивка = stored_headline("ru")
    assert all(отбивка in текст for текст in сказанное), (
        f"пути говорят о сохранении по-разному: {сказанное!r}"
    )


async def test_правка_ответом_не_называется_сохранением(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Правка — другое событие: записи не прибавилось, и слова у неё свои (T204).

    Скажи бот здесь «сохранено», аудитор пошёл бы искать в переписке вторую
    запись — ровно тот исход, ради которого правка ответом и заведена.
    """
    started()
    stub_classify(monkeypatch, suggestion())
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    await feed(dp, bot, photo_message("frame-1", caption=CLEAR))
    сказано_о_записи = session.last_sent_id

    await feed(
        dp,
        bot,
        text_message(
            "посудный участок, раковина и смеситель грязные", reply_to=bot_message(сказано_о_записи)
        ),
    )

    assert len(findings()) == 1, "правка завела вторую запись"
    assert stored_headline("ru") not in session.last_text, (
        "правка выдана за сохранение новой записи"
    )
    assert t("record.corrected", "ru", n=1, line="", guess="", title="", note="", cue="")[:10] in (
        session.last_text
    ), "правка не названа правкой"


async def test_на_английской_проверке_отбивка_английская(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Язык — параметр, а не константа."""
    started(lang="en")
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await by_words(dp, bot, monkeypatch)

    assert stored_headline("en") in session.last_text
    assert stored_headline("ru") not in session.last_text
