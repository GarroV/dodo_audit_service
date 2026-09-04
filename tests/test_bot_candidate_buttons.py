"""Кнопка под предложением называет действие, а не номер (T136, issue #107).

Ряд выглядел так: «1 // Выбрать пункт // Не записывать». Голая цифра рядом с
двумя глаголами читается как номер чего-то, а не как «нажми, чтобы записать», —
и при единственном кандидате она бессмысленна вдвойне: номер, у которого нет
второго.

Цифра в надписи вообще не нужна: место в списке едет в `callback_data`, а не в
тексте кнопки. Нужна она только для того, чтобы связать кнопку со строкой
показанного перечня, когда кандидатов несколько, — тогда номер остаётся, но уже
при глаголе.

Проверяется здесь именно свойство «в ряду нет кнопки из одной цифры», а не
дословные надписи: тест на буквальный текст краснел бы от вычитки формулировки
и не стерёг бы ничего.
"""

from __future__ import annotations

from typing import Any

import pytest
from bot_harness import (
    AUDITOR_ID,
    CHAT_ID,
    candidate,
    feed,
    make_bot,
    photo_message,
    stub_classify,
    suggestion,
)
from bot_harness import callback_query as callback

from src.bot.app import build_dispatcher
from src.bot.config import BotSettings
from src.bot.texts import t
from src.domain import get_state, start_inspection

pytestmark = pytest.mark.asyncio

SETTINGS = BotSettings(
    token="unused-in-tests",
    allowed_ids=frozenset({AUDITOR_ID}),
    mode="polling",
    auditor_names={},
)


async def show_candidates(dp: Any, bot: Any, monkeypatch: pytest.MonkeyPatch, *zones: str) -> None:
    """Довести разговор до показа предложений моделью.

    Кадр без подписи и кнопка «Разобрать»: слов нет, поэтому сверка со списком
    нарушений (T117) в дело не вступает и предложения точно приходят от модели.
    """
    stub_classify(
        monkeypatch,
        suggestion(
            *(candidate("CLN05", "D1", zone, f"формулировка {i}") for i, zone in enumerate(zones))
        ),
    )
    await feed(dp, bot, photo_message("frame-1", message_id=501))
    await feed(dp, bot, callback("rec:analyze:501"))


async def test_в_ряду_предложения_нет_кнопки_из_одной_цифры(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Сам дефект: цифра рядом с двумя глаголами читается как номер, а не действие."""
    start_inspection(CHAT_ID, "Белград 2", "planned", "ru")
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await show_candidates(dp, bot, monkeypatch, "hot_kitchen", "dining", "fridge")

    bare = [text for text in session.keyboard_texts() if text.strip().isdigit()]
    assert bare == [], f"кнопка подписана голой цифрой: {bare}"


async def test_единственный_кандидат_подписан_глаголом_без_номера(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Номер, у которого нет второго, не связывает ни с чем — он просто шум."""
    start_inspection(CHAT_ID, "Белград 2", "planned", "ru")
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await show_candidates(dp, bot, monkeypatch, "hot_kitchen")

    assert session.keyboard_texts()[0] == t("btn.pick_single", "ru")
    assert "1" not in session.keyboard_texts()[0]


async def test_нескольких_кандидатов_кнопка_называет_и_действие_и_номер(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Номер нужен, чтобы связать кнопку со строкой перечня, — но при глаголе."""
    start_inspection(CHAT_ID, "Белград 2", "planned", "ru")
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await show_candidates(dp, bot, monkeypatch, "hot_kitchen", "dining")

    texts = session.keyboard_texts()
    assert texts[0] == t("btn.pick_numbered", "ru", index=1)
    assert texts[1] == t("btn.pick_numbered", "ru", index=2)
    assert "1" in texts[0] and "2" in texts[1]


async def test_код_кнопки_не_изменился_и_запись_по_ней_появляется(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Надпись — для человека, `callback_data` — для связи: менялось только первое."""
    start_inspection(CHAT_ID, "Белград 2", "planned", "ru")
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await show_candidates(dp, bot, monkeypatch, "hot_kitchen", "dining")
    assert session.keyboard_data()[:2] == ["rec:pick:0", "rec:pick:1"]

    await feed(dp, bot, callback("rec:pick:1"))

    state = get_state(CHAT_ID)
    assert state is not None
    assert [(f.code, f.zone) for f in state.findings] == [("CLN05", "dining")]


async def test_надписи_кнопок_переводятся(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Язык — параметр и на кнопке: русский глагол в английском разговоре недопустим."""
    start_inspection(CHAT_ID, "Belgrade 2", "planned", "en", ui_lang="en")
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await show_candidates(dp, bot, monkeypatch, "hot_kitchen", "dining")

    texts = session.keyboard_texts()
    assert texts[0] == t("btn.pick_numbered", "en", index=1)
    assert texts[0] != t("btn.pick_numbered", "ru", index=1)
