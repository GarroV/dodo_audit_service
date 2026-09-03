"""Быстрый путь одинаково срабатывает независимо от способа прислать материал (T117, D063).

`tests/test_bot_fastpath.py` проверяет саму логику быстрого пути — но только на
одном способе доставки, подписи к кадру. Способов пять (`src/bot/material.py`,
T053/T054): подпись, комментарий следующим сообщением, ответ на кадр, голос,
альбом. Все пять сходятся в одном и том же `_analyze` (`src/bot/routers/record.py`)
и обязаны отдавать быстрому пути одни и те же слова аудитора. Разошёлся один из
способов — значит, склейка материала потеряла слова или подменила кадр ещё до
`_try_fast`, и быстрый путь молча промолчал или прикрепил не тот снимок.

Здесь проверяется не логика самого быстрого пути (для неё есть сосед), а то, что
склейка материала её не портит: на всех пяти — модель не звали, кнопка и слова
аудитора на экране, а после «Записать» запись легла с правильными кадрами.
"""

from __future__ import annotations

import asyncio

import pytest
from bot_harness import (
    AUDITOR_ID,
    CHAT_ID,
    Calls,
    RecordingSession,
    candidate,
    feed,
    make_bot,
    photo_message,
    stub_classify,
    suggestion,
    text_message,
    voice_message,
)
from bot_harness import callback_query as callback
from conftest import requires_data

from src.bot import sidecar
from src.bot.app import build_dispatcher
from src.bot.config import BotSettings
from src.bot.keyboards import FAST_CALLBACK
from src.domain import SOURCE_COMMENT, Finding, get_state, start_inspection

pytestmark = [pytest.mark.asyncio, requires_data]

SETTINGS = BotSettings(token="unused-in-tests", allowed_ids=frozenset({AUDITOR_ID}), mode="polling")

#: Та же однозначная фраза, что в tests/test_bot_fastpath.py: строка «Печь» на
#: боевой карте покрыта целиком, колонка выбрана словом «грязная» → CLN05, D1.
CLEAR = "печь грязная"


def started() -> None:
    start_inspection(CHAT_ID, "Белград 2", "Плановая", "ru")


def findings() -> list[Finding]:
    state = get_state(CHAT_ID)
    return [] if state is None else list(state.findings)


def assert_fast_offer_shown(asked: Calls, session: RecordingSession) -> None:
    """Общая для всех пяти способов проверка: модель не звали, слова на экране.

    Без запомненной зоны быстрый путь не срабатывает никогда (правило 6) — этим
    и объясняется `sidecar.remember_zone` в каждом тесте перед доставкой.
    """
    assert asked == [], "модель звали, хотя слова однозначны — быстрый путь не сработал"
    assert FAST_CALLBACK in session.keyboard_data(), "кнопка быстрого пути не показана"
    assert CLEAR in session.last_text, "слова аудитора не дошли до сообщения целиком"


def assert_fast_record(photos: list[str]) -> None:
    """Общая для всех пяти способов проверка записи после нажатия «Записать»."""
    saved = findings()
    assert len(saved) == 1, "нажатие «Записать» дало не одну запись"
    finding = saved[0]
    assert (finding.code, finding.level, finding.zone) == ("CLN05", "D1", "hot_kitchen")
    assert finding.text == CLEAR, "текст записи — не дословные слова аудитора"
    assert finding.source == SOURCE_COMMENT
    assert finding.photos == photos, "кадры к записи прикрепились не те и не в том порядке"


async def test_быстрый_путь_срабатывает_на_подписи_к_кадру(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Способ 1: слова аудитора идут подписью к кадру."""
    started()
    asked = stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen")))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")

    await feed(dp, bot, photo_message("frame-1", caption=CLEAR))

    assert_fast_offer_shown(asked, session)
    await feed(dp, bot, callback(FAST_CALLBACK))
    assert_fast_record(["frame-1"])


async def test_быстрый_путь_срабатывает_на_комментарии_отдельным_сообщением(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Способ 2: кадр без подписи, слова аудитора — отдельным сообщением следом."""
    started()
    asked = stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen")))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")

    await feed(dp, bot, photo_message("frame-1", message_id=701))
    await feed(dp, bot, text_message(CLEAR))

    assert_fast_offer_shown(asked, session)
    await feed(dp, bot, callback(FAST_CALLBACK))
    assert_fast_record(["frame-1"])


async def test_быстрый_путь_срабатывает_на_ответе_на_кадр(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Способ 3: два кадра, слова аудитора — ответом на первый (не на второй)."""
    started()
    asked = stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen")))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")

    first = photo_message("frame-1")
    second = photo_message("frame-2")
    await feed(dp, bot, first)
    await feed(dp, bot, second)
    await feed(dp, bot, text_message(CLEAR, reply_to=first))

    assert_fast_offer_shown(asked, session)
    await feed(dp, bot, callback(FAST_CALLBACK))
    assert_fast_record(["frame-1"])


async def test_быстрый_путь_срабатывает_на_голосовом_комментарии(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Способ 4: слова аудитора идут голосом, расшифровка подменена на тот же текст."""
    started()
    asked = stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen")))
    monkeypatch.setattr("src.bot.routers.record.transcribe", lambda audio, **kw: CLEAR)
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")

    await feed(dp, bot, photo_message("frame-1"))
    await feed(dp, bot, voice_message("voice-1"))

    assert_fast_offer_shown(asked, session)
    await feed(dp, bot, callback(FAST_CALLBACK))
    assert_fast_record(["frame-1"])


async def test_быстрый_путь_срабатывает_на_альбоме(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Способ 5: альбом из двух кадров с подписью на первом — оба кадра идут в запись."""
    started()
    asked = stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen")))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS, album_window=0.01)
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")

    group_id = "album-fast"
    await feed(dp, bot, photo_message("frame-1", media_group_id=group_id, caption=CLEAR))
    await feed(dp, bot, photo_message("frame-2", media_group_id=group_id))
    await asyncio.sleep(0.1)

    assert_fast_offer_shown(asked, session)
    await feed(dp, bot, callback(FAST_CALLBACK))
    assert_fast_record(["frame-1", "frame-2"])
