"""Мастер начала проверки через настоящий диспетчер (T050, T051, T052, T063).

События идут тем же путём, что в бою: `Update` → `Dispatcher.feed_update` →
мидлварь доступа → фильтры → хендлер. Проверяется не текст сообщений, а
поведение: что бот спросил, что положил в состояние проверки и чего не сделал.
"""

from __future__ import annotations

from datetime import date

import pytest
from bot_harness import (
    AUDITOR_ID,
    CHAT_ID,
    STRANGER_ID,
    callback_query,
    feed,
    make_bot,
    text_message,
)

from src.bot.app import build_dispatcher
from src.bot.config import BotSettings
from src.bot.keyboards import (
    KIND_TITLES,
    NEW_INSPECTION_CALLBACK,
    RESUME_CONTINUE_CALLBACK,
    RESUME_NEW_CALLBACK,
)
from src.domain import get_state, start_inspection

pytestmark = pytest.mark.asyncio


def settings(names: dict[int, str] | None = None) -> BotSettings:
    return BotSettings(
        token="unused-in-tests",
        allowed_ids=frozenset({AUDITOR_ID}),
        mode="polling",
        auditor_names=names or {},
    )


async def walk_through_wizard(unit: str = "Белград 2") -> tuple[object, object]:
    """Пройти мастер целиком: /start → «Новая проверка» → название → вид → язык."""
    bot, session = make_bot()
    dp = build_dispatcher(settings())
    await feed(dp, bot, text_message("/start"))
    await feed(dp, bot, callback_query(NEW_INSPECTION_CALLBACK))
    await feed(dp, bot, text_message(unit))
    await feed(dp, bot, callback_query("start:kind:planned"))
    await feed(dp, bot, callback_query("start:lang:ru"))
    return bot, session


async def test_stranger_gets_no_answer_at_all(domain_env: object) -> None:
    """Посторонний не должен получить даже отказа: это подтверждение, что бот есть."""
    bot, session = make_bot()
    dp = build_dispatcher(settings())

    await feed(dp, bot, text_message("/start", user_id=STRANGER_ID))

    assert session.calls == []


async def test_start_offers_new_inspection_button(domain_env: object) -> None:
    bot, session = make_bot()
    dp = build_dispatcher(settings())

    await feed(dp, bot, text_message("/start"))

    assert NEW_INSPECTION_CALLBACK in session.keyboard_data()


async def test_wizard_asks_unit_then_kind_then_language(domain_env: object) -> None:
    bot, session = make_bot()
    dp = build_dispatcher(settings())

    await feed(dp, bot, text_message("/start"))
    await feed(dp, bot, callback_query(NEW_INSPECTION_CALLBACK))
    assert "пиццерии" in session.last_text.lower()

    await feed(dp, bot, text_message("Белград 2"))
    assert set(session.keyboard_data()) == {f"start:kind:{code}" for code in KIND_TITLES}

    await feed(dp, bot, callback_query("start:kind:planned"))
    assert session.keyboard_data() == ["start:lang:ru", "start:lang:en"]


async def test_wizard_creates_inspection_with_typed_unit(domain_env: object) -> None:
    """D051: пиццерия вводится текстом, справочника в MVP нет."""
    await walk_through_wizard(unit="Белград 2")

    inspection = get_state(CHAT_ID)
    assert inspection is not None
    assert inspection.unit == "Белград 2"
    # Вид проверки записан КОДОМ (T152): слово живёт только в шапке для
    # движка и на кнопке мастера, а сама проверка связывается кодом.
    assert inspection.kind == "planned"
    assert inspection.report_lang == "ru"


async def test_auditor_comes_from_telegram_id_and_is_never_asked(domain_env: object) -> None:
    """T063: имя проверяющего подставляется, руками не вводится."""
    bot, session = make_bot()
    dp = build_dispatcher(settings({AUDITOR_ID: "Владимир Гарро"}))

    await feed(dp, bot, text_message("/start"))
    await feed(dp, bot, callback_query(NEW_INSPECTION_CALLBACK))
    await feed(dp, bot, text_message("Белград 2"))
    await feed(dp, bot, callback_query("start:kind:planned"))
    await feed(dp, bot, callback_query("start:lang:ru"))

    inspection = get_state(CHAT_ID)
    assert inspection is not None
    assert inspection.auditor == "Владимир Гарро"
    assert not any("проверяющ" in text.lower() and "?" in text for text in session.texts[:-1])


async def test_date_is_today_and_not_asked(domain_env: object) -> None:
    await walk_through_wizard()

    inspection = get_state(CHAT_ID)
    assert inspection is not None
    assert inspection.date == date.today().isoformat()


async def test_empty_unit_is_asked_again_not_accepted(domain_env: object) -> None:
    bot, session = make_bot()
    dp = build_dispatcher(settings())

    await feed(dp, bot, text_message("/start"))
    await feed(dp, bot, callback_query(NEW_INSPECTION_CALLBACK))
    await feed(dp, bot, text_message("   "))

    assert "пуст" in session.last_text.lower()
    await feed(dp, bot, text_message("Белград 2"))
    assert set(session.keyboard_data()) == {f"start:kind:{code}" for code in KIND_TITLES}


async def test_unfinished_inspection_is_shown_not_overwritten(domain_env: object) -> None:
    """T052: чужую незавершённую проверку не затирать молча."""
    start_inspection(CHAT_ID, "Белград 1", "planned", "ru", auditor="Пётр Петров")

    bot, session = make_bot()
    dp = build_dispatcher(settings())
    await feed(dp, bot, text_message("/start"))

    assert "Белград 1" in session.last_text
    assert "Пётр Петров" in session.last_text
    assert date.today().isoformat() in session.last_text
    assert set(session.keyboard_data()) == {RESUME_CONTINUE_CALLBACK, RESUME_NEW_CALLBACK}

    still_there = get_state(CHAT_ID)
    assert still_there is not None and still_there.unit == "Белград 1"


async def test_new_inspection_button_also_warns_about_unfinished_one(domain_env: object) -> None:
    start_inspection(CHAT_ID, "Белград 1", "planned", "ru")

    bot, session = make_bot()
    dp = build_dispatcher(settings())
    await feed(dp, bot, callback_query(NEW_INSPECTION_CALLBACK))

    assert set(session.keyboard_data()) == {RESUME_CONTINUE_CALLBACK, RESUME_NEW_CALLBACK}
    assert get_state(CHAT_ID) is not None


async def test_continue_keeps_the_inspection(domain_env: object) -> None:
    start_inspection(CHAT_ID, "Белград 1", "planned", "ru")

    bot, session = make_bot()
    dp = build_dispatcher(settings())
    await feed(dp, bot, text_message("/start"))
    await feed(dp, bot, callback_query(RESUME_CONTINUE_CALLBACK))

    assert "Белград 1" in session.last_text
    kept = get_state(CHAT_ID)
    assert kept is not None and kept.unit == "Белград 1"


async def test_start_new_asks_unit_and_replaces_only_after_full_wizard(
    domain_env: object,
) -> None:
    start_inspection(CHAT_ID, "Белград 1", "planned", "ru")

    bot, session = make_bot()
    dp = build_dispatcher(settings())
    await feed(dp, bot, text_message("/start"))
    await feed(dp, bot, callback_query(RESUME_NEW_CALLBACK))

    assert "пиццерии" in session.last_text.lower()
    midway = get_state(CHAT_ID)
    assert midway is not None and midway.unit == "Белград 1"

    await feed(dp, bot, text_message("Белград 3"))
    await feed(dp, bot, callback_query("start:kind:repeat"))
    await feed(dp, bot, callback_query("start:lang:en"))

    replaced = get_state(CHAT_ID)
    assert replaced is not None
    assert replaced.unit == "Белград 3"
    assert replaced.report_lang == "en"
