"""Ручной выбор пункта без модели: последняя линия обороны (T034 со стороны бота).

Модель недоступна или ничего не предложила — проверка не встаёт. Аудитор берёт
пункт из перечня зоны кнопками, перечень пролистывается страницами, а зона,
если её не с чем сопоставить, сначала спрашивается отдельно.

Три вещи, которые защищает этот файл, отдельно от `test_bot_record_router.py`:

**Листание не сдвигает нумерацию.** Кнопка несёт номер места пункта в целом
перечне, а не на странице (T034), — иначе выбор с третьей страницы фиксировал
бы пункт с первой. Проверяется явным сравнением зафиксированного кода с тем,
что стоял под нажатым номером.

**Отказ конфигурации — это не «модель ничего не предложила».** `RecognizeConfigError`
(нет карты кадров, ключа или ffmpeg) обязан дойти до аудитора текстом, а не
провалиться в тихий пустой перечень: без карты кадров и сам перечень собрать
нечем, и попытка его открыть обязана отказать тем же способом.

**Устаревшая кнопка не фиксирует ничего.** Перезапуск бота стирает память о
показанном перечне; нажатие на кнопку из прошлой жизни отвечает «устарело», а
не тем более случайно попавшим индексом чужого списка.

Разбор и ручной перечень подменены (`stub_classify`, `stub_manual`): до модели
и до реального справочника тесты не ходят.

Комментарий аудитора здесь неоднозначен намеренно, и вернуть на его место
однозначный нельзя. С появлением быстрого пути (T117, D063) слова вроде «печь
грязная» до модели не доходят вовсе: пункт показывает сверка со списком
нарушений, а подменённый разбор не зовётся, и половина файла начинает
проверять не то, что написано в её названии. Поэтому в подписи стоит «печь,
посмотри что тут»: строка карты «Печь» произнесена, но по словам не видно,
грязь это или поломка, — материал уходит модели, как этому файлу и нужно. Сам
быстрый путь проверяет `tests/test_bot_fastpath.py`.
"""

from __future__ import annotations

import pytest
from bot_harness import (
    AUDITOR_ID,
    CHAT_ID,
    candidate,
    feed,
    make_bot,
    manual,
    photo_message,
    stub_classify,
    stub_manual,
    suggestion,
)
from bot_harness import callback_query as callback

from src.bot import sidecar
from src.bot.app import build_dispatcher
from src.bot.config import BotSettings
from src.bot.keyboards import MANUAL_PAGE_SIZE
from src.bot.texts import t
from src.domain import get_state, start_inspection
from src.recognize.errors import ModelUnavailable, RecognizeConfigError
from src.recognize.manual import ManualCandidate

pytestmark = pytest.mark.asyncio

SETTINGS = BotSettings(token="unused-in-tests", allowed_ids=frozenset({AUDITOR_ID}), mode="polling")

#: Настоящие коды методики для зоны "hot_kitchen" (проверено прогоном
#: `manual_candidates("hot_kitchen")`) — двадцать пунктов, три полные
#: страницы по MANUAL_PAGE_SIZE=8. Индекс 9 (второй пункт второй страницы)
#: нарочно однозначный по классу (только "D1"): фиксация не должна
#: переспрашивать класс, это отдельная забота другого теста.
_HOT_KITCHEN_ITEMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("PRD01", ("D1", "D2")),
    ("PRD02", ("D2",)),
    ("PRD03", ("D1", "D2")),
    ("PRD04", ("D3",)),
    ("PRD05", ("D1", "D2", "D3")),
    ("PRD06", ("D1",)),
    ("PRD07", ("D1",)),
    ("PRD08", ("D3",)),
    ("PRD09", ("D1", "D2", "D3")),
    ("PRD11", ("D1",)),
    ("PRD12", ("D1",)),
    ("PRD13", ("D1",)),
    ("PRD14", ("D1",)),
    ("PRD15", ("D1",)),
    ("TEH01", ("D1", "D2")),
    ("TEH02", ("D1",)),
    ("TEH03", ("D1",)),
    ("TEH04", ("D1",)),
    ("TEH05", ("D1",)),
    ("TEH06", ("D1",)),
)


def _hot_kitchen_manual_items() -> tuple[ManualCandidate, ...]:
    """Двадцать пунктов зоны "hot_kitchen" для `stub_manual` — три страницы."""
    return tuple(manual(code, levels, f"Пункт {code}") for code, levels in _HOT_KITCHEN_ITEMS)


def started() -> None:
    start_inspection(CHAT_ID, "Белград 2", "planned", "ru")


def findings() -> list[object]:
    state = get_state(CHAT_ID)
    return [] if state is None else list(state.findings)


async def test_manual_button_opens_the_list_without_recording_model_candidates(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """«Выбрать пункт» открывает перечень, а не фиксирует предложение модели."""
    started()
    stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen", "Печь в нагаре")))
    manual_calls = stub_manual(monkeypatch, (manual("PRD06", ("D1",), "Пункт без сети"),))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")

    await feed(dp, bot, photo_message("frame-1", caption="печь, посмотри что тут"))
    await feed(dp, bot, callback("rec:manual"))

    assert session.last_text == t("record.manual_page", "ru", page=1, pages=1)
    assert "rec:mi:0" in session.keyboard_data()
    assert manual_calls[-1] == ("hot_kitchen",)
    assert findings() == [], "кандидат модели не был выбран — записи быть не должно"


async def test_long_list_is_paginated_with_next_button_on_first_page(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Двадцать пунктов — ровно MANUAL_PAGE_SIZE кнопок, «дальше» есть, «назад» нет."""
    started()
    stub_classify(monkeypatch, ModelUnavailable("нет сети"))
    stub_manual(monkeypatch, _hot_kitchen_manual_items())
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")

    await feed(dp, bot, photo_message("frame-1", caption="печь, посмотри что тут"))

    data = session.keyboard_data()
    item_buttons = [d for d in data if d.startswith("rec:mi:")]
    assert item_buttons == [f"rec:mi:{i}" for i in range(MANUAL_PAGE_SIZE)]
    page_buttons = [d for d in data if d.startswith("rec:mp:")]
    assert page_buttons == ["rec:mp:1"], "на первой странице должна быть только кнопка «дальше»"
    assert session.last_text == t("record.manual_page", "ru", page=1, pages=3)


async def test_next_page_shows_different_items_and_adds_back_button(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Переход `rec:mp:1` — другие коды в кнопках, и появляется «назад»."""
    started()
    stub_classify(monkeypatch, ModelUnavailable("нет сети"))
    stub_manual(monkeypatch, _hot_kitchen_manual_items())
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")

    await feed(dp, bot, photo_message("frame-1", caption="печь, посмотри что тут"))
    first_page = {d for d in session.keyboard_data() if d.startswith("rec:mi:")}

    await feed(dp, bot, callback("rec:mp:1"))

    data = session.keyboard_data()
    second_page = {d for d in data if d.startswith("rec:mi:")}
    assert second_page == {f"rec:mi:{i}" for i in range(MANUAL_PAGE_SIZE, 2 * MANUAL_PAGE_SIZE)}
    assert second_page.isdisjoint(first_page), "вторая страница обязана показывать другие пункты"
    assert "rec:mp:0" in data, "со второй страницы должна появиться кнопка «назад»"
    assert session.last_text == t("record.manual_page", "ru", page=2, pages=3)


async def test_last_page_has_no_next_button(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Третья, неполная страница — кнопки «дальше» нет, «назад» есть."""
    started()
    stub_classify(monkeypatch, ModelUnavailable("нет сети"))
    stub_manual(monkeypatch, _hot_kitchen_manual_items())
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")

    await feed(dp, bot, photo_message("frame-1", caption="печь, посмотри что тут"))
    await feed(dp, bot, callback("rec:mp:2"))

    data = session.keyboard_data()
    item_buttons = [d for d in data if d.startswith("rec:mi:")]
    assert item_buttons == [
        f"rec:mi:{i}" for i in range(2 * MANUAL_PAGE_SIZE, len(_HOT_KITCHEN_ITEMS))
    ]
    page_buttons = [d for d in data if d.startswith("rec:mp:")]
    assert page_buttons == ["rec:mp:1"], "на последней странице должна быть только кнопка «назад»"
    assert session.last_text == t("record.manual_page", "ru", page=3, pages=3)


async def test_page_number_beyond_the_list_clamps_to_the_last_page(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`rec:mp:99` не роняет бота — показана последняя существующая страница."""
    started()
    stub_classify(monkeypatch, ModelUnavailable("нет сети"))
    stub_manual(monkeypatch, _hot_kitchen_manual_items())
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")

    await feed(dp, bot, photo_message("frame-1", caption="печь, посмотри что тут"))
    await feed(dp, bot, callback("rec:mp:99"))

    assert session.last_text == t("record.manual_page", "ru", page=3, pages=3)
    item_buttons = [d for d in session.keyboard_data() if d.startswith("rec:mi:")]
    assert item_buttons == [
        f"rec:mi:{i}" for i in range(2 * MANUAL_PAGE_SIZE, len(_HOT_KITCHEN_ITEMS))
    ]
    assert item_buttons, "перечень на клэмпленной странице не должен оказаться пустым"


async def test_item_picked_from_the_second_page_records_the_correct_code(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Нажатие `rec:mi:9` фиксирует именно тот код, что стоял под этим номером.

    Защита от сдвига нумерации: индекс несёт место в целом перечне, а не на
    открытой странице, поэтому фиксация обязана быть верной независимо от
    того, что аудитор успел пролистать.
    """
    started()
    stub_classify(monkeypatch, ModelUnavailable("нет сети"))
    stub_manual(monkeypatch, _hot_kitchen_manual_items())
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")

    await feed(dp, bot, photo_message("frame-1", caption="протухший фарш на разделке"))
    await feed(dp, bot, callback("rec:mp:1"))
    assert "rec:mi:9" in session.keyboard_data(), (
        "пункт с индексом 9 обязан быть на второй странице"
    )

    await feed(dp, bot, callback("rec:mi:9"))

    state = get_state(CHAT_ID)
    assert state is not None
    assert len(state.findings) == 1
    assert state.findings[0].code == "PRD11"
    assert state.findings[0].level == "D1"
    assert state.findings[0].zone == "hot_kitchen"
    assert state.findings[0].text == "протухший фарш на разделке"


async def test_unknown_zone_is_asked_before_the_list(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Зону взять неоткуда — сначала кнопки зон, перечень пунктов ещё не открыт."""
    started()
    stub_classify(monkeypatch, ModelUnavailable("нет сети"))
    stub_manual(monkeypatch, (manual("PRD06", ("D1",), "Пункт без сети"),))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, photo_message("frame-1", caption="печь, посмотри что тут"))

    assert session.last_text == t("record.ask_zone", "ru")
    data = session.keyboard_data()
    assert any(d.startswith("rec:zm:") for d in data)
    assert not any(d.startswith("rec:mi:") for d in data), (
        "перечень пунктов ещё не должен показаться"
    )
    assert findings() == []


async def test_zone_picked_by_button_opens_the_list_and_is_remembered(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """После кнопки зоны перечень открывается для неё, и она запоминается (D048)."""
    started()
    stub_classify(monkeypatch, ModelUnavailable("нет сети"))
    manual_calls = stub_manual(monkeypatch, (manual("PRD06", ("D1",), "Пункт без сети"),))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, photo_message("frame-1", caption="печь, посмотри что тут"))
    await feed(dp, bot, callback("rec:zm:hot_kitchen"))

    assert sidecar.read(CHAT_ID).zone == "hot_kitchen"
    assert manual_calls[-1] == ("hot_kitchen",)
    assert session.last_text == t("record.manual_page", "ru", page=1, pages=1)
    assert "rec:mi:0" in session.keyboard_data()
    assert findings() == []


async def test_stale_manual_pick_after_restart_records_nothing(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Перезапуск стирает память о показанном перечне — старая кнопка не фиксирует ничего."""
    started()
    stub_classify(monkeypatch, ModelUnavailable("нет сети"))
    stub_manual(monkeypatch, (manual("PRD06", ("D1",), "Пункт без сети"),))
    bot, session = make_bot()
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")

    first = build_dispatcher(SETTINGS)
    await feed(first, bot, photo_message("frame-1", caption="печь, посмотри что тут"))

    second = build_dispatcher(SETTINGS)
    session.clear()
    await feed(second, bot, callback("rec:mi:0"))

    assert session.last_text == t("record.stale", "ru")
    assert findings() == []


async def test_manual_pick_out_of_range_is_stale_not_a_crash(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Номер за пределами показанного перечня — тоже «устарело», без падения."""
    started()
    stub_classify(monkeypatch, ModelUnavailable("нет сети"))
    stub_manual(
        monkeypatch,
        (manual("PRD06", ("D1",), "Пункт 1"), manual("PRD07", ("D1",), "Пункт 2")),
    )
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")

    await feed(dp, bot, photo_message("frame-1", caption="печь, посмотри что тут"))
    session.clear()
    await feed(dp, bot, callback("rec:mi:99"))

    assert session.last_text == t("record.stale", "ru")
    assert findings() == []


async def test_config_error_from_classify_is_reported_not_treated_as_empty(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Отказ конфигурации разбора — текст отказа, а не молчаливый пустой ответ.

    Ручной перечень тоже не открывается: отказ конфигурации означает
    недонастроенный стенд, а не то, что перечень нечем собрать, и открывать
    его в этом состоянии означало бы делать вид, что стенд исправен.
    """
    started()
    stub_classify(monkeypatch, RecognizeConfigError("нет карты кадров"))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")

    await feed(dp, bot, photo_message("frame-1", caption="печь, посмотри что тут"))

    assert session.last_text == t("record.unavailable", "ru")
    assert session.keyboard_data() == [], "ни перечень, ни выбор зоны показаны быть не должны"
    assert findings() == []


async def test_config_error_building_the_manual_list_is_reported_and_bot_survives(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Отказ при сборке ручного перечня уходит текстом, а не роняет обработчик.

    `manual_candidates` подменяется напрямую там же, где её берёт роутер
    (`src.bot.routers.record.manual_candidates`) — так же честно, как подмена
    `bot.download` в тестах `photos.py`, а не вызовом внутренней функции.
    """
    started()
    stub_classify(monkeypatch, ModelUnavailable("нет ключа"))

    def failing_manual(zone: str | None, **kwargs: object) -> tuple[ManualCandidate, ...]:
        raise RecognizeConfigError("нет карты кадров")

    monkeypatch.setattr("src.bot.routers.record.manual_candidates", failing_manual)
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")

    await feed(dp, bot, photo_message("frame-1", caption="печь, посмотри что тут"))

    assert session.texts[-1] == t("record.unavailable", "ru")
    assert findings() == []

    # Бот жив: следующее событие обрабатывается штатно, а не повторяет отказ.
    stub_classify(monkeypatch, suggestion(candidate("PRD06", "D1", "hot_kitchen", "Формулировка")))
    await feed(dp, bot, photo_message("frame-2", caption="ещё одна проблема"))
    assert "PRD06" in session.last_text
