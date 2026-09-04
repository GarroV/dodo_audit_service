"""Отказы мастера начала проверки: ветки, где что-то пошло не так.

`tests/test_bot_start_router.py` проверяет счастливый путь мастера. Здесь —
то, что случается на точке: движок отказал, аудитор прислал не то, что
ждали, нажал кнопку с чужим или устаревшим кодом. Молчаливый отказ на старте
проверки — худшее, что может случиться, поэтому каждая ветка обязана либо
ответить понятным текстом, либо честно не сдвинуть мастер дальше — но никогда
не упасть и не создать проверку тайком.
"""

from __future__ import annotations

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from bot_harness import (
    AUDITOR_ID,
    CHAT_ID,
    callback_query,
    feed,
    make_bot,
    photo_message,
    text_message,
)

from src import domain
from src.bot.app import build_dispatcher
from src.bot.config import BotSettings
from src.bot.keyboards import (
    KIND_PREFIX,
    KIND_TITLES,
    LANG_PREFIX,
    NEW_INSPECTION_CALLBACK,
    RESUME_CONTINUE_CALLBACK,
)
from src.bot.states import StartFlow
from src.bot.texts import t
from src.domain.errors import DomainError

pytestmark = pytest.mark.asyncio


def settings() -> BotSettings:
    return BotSettings(token="unused-in-tests", allowed_ids=frozenset({AUDITOR_ID}), mode="polling")


async def walk_to_language_step(dp: object, bot: object, unit: str = "Белград 2") -> None:
    """Пройти мастер до кнопок языка отчёта, не нажимая последнюю."""
    await feed(dp, bot, text_message("/start"))
    await feed(dp, bot, callback_query(NEW_INSPECTION_CALLBACK))
    await feed(dp, bot, text_message(unit))
    await feed(dp, bot, callback_query("start:kind:planned"))


async def test_domain_failure_is_reported_and_creates_no_inspection(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Движок отказал на последнем шаге мастера — бот называет причину, проверка не создаётся."""

    def raise_domain_error(*args: object, **kwargs: object) -> None:
        raise DomainError("методика неполна")

    monkeypatch.setattr(domain, "start_inspection", raise_domain_error)

    bot, session = make_bot()
    dp = build_dispatcher(settings())
    await walk_to_language_step(dp, bot)
    await feed(dp, bot, callback_query("start:lang:ru"))

    assert session.last_text == t("start.failed", "ru", reason="методика неполна")
    assert domain.get_state(CHAT_ID) is None


async def test_after_domain_failure_plain_text_is_no_longer_taken_as_unit_name(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """После отказа движка шаги сброшены — текст бот встречает как материал, не как название."""

    def raise_domain_error(*args: object, **kwargs: object) -> None:
        raise DomainError("методика неполна")

    monkeypatch.setattr(domain, "start_inspection", raise_domain_error)

    bot, session = make_bot()
    dp = build_dispatcher(settings())
    await walk_to_language_step(dp, bot)
    await feed(dp, bot, callback_query("start:lang:ru"))

    await feed(dp, bot, text_message("Белград 3"))

    assert session.last_text == t("material.no_inspection", "ru")
    assert domain.get_state(CHAT_ID) is None


async def test_photo_instead_of_unit_name_keeps_the_wizard_on_the_same_step(
    domain_env: object,
) -> None:
    """Кадр вместо названия пиццерии — бот говорит, чего ждёт, а шаг мастера не сбрасывается."""
    bot, session = make_bot()
    dp = build_dispatcher(settings())
    await feed(dp, bot, text_message("/start"))
    await feed(dp, bot, callback_query(NEW_INSPECTION_CALLBACK))

    await feed(dp, bot, photo_message("wrong-step"))
    assert session.last_text == t("start.unit_expected", "ru")

    await feed(dp, bot, text_message("Белград 4"))
    assert set(session.keyboard_data()) == {f"start:kind:{code}" for code in KIND_TITLES}


async def test_resume_continue_without_a_saved_inspection_replies_gone_not_crash(
    domain_env: object,
) -> None:
    """«Продолжить» нажато, а файла состояния не было — бот отвечает, что продолжать нечего."""
    bot, session = make_bot()
    dp = build_dispatcher(settings())

    await feed(dp, bot, callback_query(RESUME_CONTINUE_CALLBACK))

    assert session.last_text == t("start.resume_gone", "ru")
    assert domain.get_state(CHAT_ID) is None


async def test_unknown_kind_code_does_not_advance_the_wizard(domain_env: object) -> None:
    """Кнопка вида проверки с неизвестным кодом не двигает мастер дальше — язык не спрашивается."""
    bot, session = make_bot()
    dp = build_dispatcher(settings())
    await feed(dp, bot, text_message("/start"))
    await feed(dp, bot, callback_query(NEW_INSPECTION_CALLBACK))
    await feed(dp, bot, text_message("Белград 5"))
    kind_codes = {f"start:kind:{code}" for code in KIND_TITLES}
    assert set(session.keyboard_data()) == kind_codes

    await feed(dp, bot, callback_query(KIND_PREFIX + "нет-такого"))

    assert set(session.keyboard_data()) == kind_codes
    assert domain.get_state(CHAT_ID) is None


async def test_unknown_lang_code_creates_no_inspection(domain_env: object) -> None:
    """Кнопка языка отчёта с неизвестным кодом не создаёт проверку — движок не вызывается."""
    bot, session = make_bot()
    dp = build_dispatcher(settings())
    await walk_to_language_step(dp, bot, unit="Белград 6")
    lang_codes = {"start:lang:ru", "start:lang:en"}
    assert set(session.keyboard_data()) == lang_codes

    await feed(dp, bot, callback_query(LANG_PREFIX + "нет-такого"))

    assert set(session.keyboard_data()) == lang_codes
    assert domain.get_state(CHAT_ID) is None


async def test_lang_pressed_with_no_wizard_data_falls_back_to_greeting(
    domain_env: object,
) -> None:
    """Язык нажат, а шаги мастера не пройдены (состояние очищено) — бот возвращает приветствие."""
    bot, session = make_bot()
    dp = build_dispatcher(settings())

    # Состояние выставлено напрямую, в обход мастера: имитирует нажатие без
    # пройденных шагов unit/kind — данные FSM пусты, а состояние осталось.
    key = StorageKey(bot_id=bot.id, chat_id=CHAT_ID, user_id=AUDITOR_ID)
    state = FSMContext(storage=dp.storage, key=key)
    await state.set_state(StartFlow.waiting_lang)

    await feed(dp, bot, callback_query("start:lang:ru"))

    assert session.last_text == t("start.greeting", "ru")
    assert NEW_INSPECTION_CALLBACK in session.keyboard_data()
    assert domain.get_state(CHAT_ID) is None
