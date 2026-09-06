"""Слова без кадра называются при завершении так же, как кадры без записи (T241, #197).

Дефект, который чинит задача: с T229 очередь ожидания симметрична — кадр ждёт
слов, слова ждут кадра, — а называлась при завершении только одна её сторона
(T068, кадры). Аудитор сказал о находке, кадр не прислал, и о потере ему не
говорил никто. Здесь же — обратная сторона того же правила: пугать потерей
того, что кадр всё-таки дождался, тоже нельзя, иначе показ станет ложью другого
рода.

Показ придержанных слов зовётся одной и той же функцией на два входа
(`records.show_records`) — она же собирает и кадры без записи (T139/T068), и
слова без кадра (T241). Поэтому здесь же проверяется и второй вход, `/records`:
разошедшиеся копии одного списка — ровно тот дефект, ради которого показ и
собран в одну функцию (T147).
"""

from __future__ import annotations

from typing import Any

import pytest
from aiogram.exceptions import TelegramBadRequest
from bot_harness import (
    AUDITOR_ID,
    CHAT_ID,
    feed,
    make_bot,
    photo_message,
    text_message,
    voice_message,
)

from src.bot import sidecar
from src.bot.app import build_dispatcher
from src.bot.config import BotSettings
from src.bot.routers.records import HELD_SHOWN_LIMIT
from src.bot.texts import t
from src.bot.view import shorten
from src.domain import Finding, get_state, start_inspection

pytestmark = pytest.mark.asyncio

SETTINGS = BotSettings(token="unused-in-tests", allowed_ids=frozenset({AUDITOR_ID}), mode="polling")

#: Однозначная фраза синтетической карты (см. `tests/test_bot_words_before_frame.py`):
#: строка «Печь» произнесена целиком, колонка выбрана словом «грязная» → CLN05.
#: Зону слова не называют — её подставляет память, как на точке.
OVEN = "печь грязная"

#: Вторая фраза той же зоны, ведущая в другой пункт — нужна там, где важен
#: порядок, а не итоговый код: две одинаковые фразы подряд не показали бы,
#: перепутал ли бот их местами.
FURNITURE = "мебель участка грязная"


def started() -> None:
    start_inspection(CHAT_ID, "Белград 2", "planned", "ru")
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")


def findings() -> list[Finding]:
    state = get_state(CHAT_ID)
    return [] if state is None else list(state.findings)


def отправленные_голоса(session: Any) -> list[tuple[str, str | None]]:
    """Голосовое и подпись — по каждому `SendVoice`."""
    return [
        (str(call.voice), call.caption)
        for call in session.calls
        if type(call).__name__ == "SendVoice"
    ]


async def test_слова_без_кадра_называются_при_завершении(domain_env: object) -> None:
    """Сам дефект T241: сказанное без кадра обязано прозвучать при подведении итога."""
    started()
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, text_message(OVEN))
    session.clear()
    await feed(dp, bot, text_message("/finish"))

    assert t("finish.held", "ru", count=1) in session.texts, "число придержанных слов не названо"
    assert t("finish.held_words", "ru", note=shorten(OVEN)) in session.texts, (
        "сами придержанные слова не показаны"
    )


async def test_придержанное_голосовое_возвращается_самим_голосовым(domain_env: object) -> None:
    """Расшифровки у придержанного голоса ещё нет — пересказать его нечем, значит, звуком."""
    started()
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, voice_message("voice-held"))
    session.clear()
    await feed(dp, bot, text_message("/finish"))

    assert ("voice-held", t("finish.held_voice", "ru")) in отправленные_голоса(session), (
        "придержанное голосовое не вернулось самим голосовым"
    )


async def test_слова_дождавшиеся_кадра_не_называются(domain_env: object) -> None:
    """Защита от обратной лжи: находка, которую кадр всё-таки дождался, — не потеря."""
    started()
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, text_message(OVEN))
    await feed(dp, bot, photo_message("frame-oven"))
    assert [(f.code, f.zone, f.photos) for f in findings()] == [
        ("CLN05", "hot_kitchen", ["frame-oven"])
    ], "кадр не собрал запись из придержанных слов — проверять дальше нечего"
    session.clear()

    await feed(dp, bot, text_message("/finish"))

    assert t("finish.held", "ru", count=1) not in session.texts, (
        "собранная находка названа потерянной"
    )
    assert t("finish.held_words", "ru", note=shorten(OVEN)) not in session.texts


async def test_показ_не_пустошит_очередь(domain_env: object) -> None:
    """Снимок, а не потребление: кадр, присланный ПОСЛЕ показа, обязан всё равно собрать запись."""
    started()
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, text_message(OVEN))
    session.clear()
    await feed(dp, bot, text_message("/finish"))
    assert t("finish.held", "ru", count=1) in session.texts, (
        "показа не случилось — проверять нечего"
    )

    await feed(dp, bot, photo_message("frame-after-show"))

    assert [(f.code, f.zone, f.photos) for f in findings()] == [
        ("CLN05", "hot_kitchen", ["frame-after-show"])
    ], "показ съел придержанные слова из очереди связывания"


async def test_порядок_прихода_сохраняется(domain_env: object) -> None:
    """Порядок — тот же, в каком аудитор их сказал, иначе он не узнает свои слова."""
    started()
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, text_message(OVEN))
    await feed(dp, bot, text_message(FURNITURE))
    session.clear()
    await feed(dp, bot, text_message("/finish"))

    первое = t("finish.held_words", "ru", note=shorten(OVEN))
    второе = t("finish.held_words", "ru", note=shorten(FURNITURE))
    показанные = [text for text in session.texts if text in {первое, второе}]
    assert показанные == [первое, второе], "придержанные слова показаны не в том порядке"


async def test_сверх_предела_показа_остаток_назван(domain_env: object) -> None:
    """Сверх предела бот говорит полное число и отдельно — сколько ещё осталось."""
    started()
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    сколько = HELD_SHOWN_LIMIT + 3
    for _ in range(сколько):
        await feed(dp, bot, text_message(OVEN))
    session.clear()

    await feed(dp, bot, text_message("/finish"))

    assert t("finish.held", "ru", count=сколько) in session.texts, "полное число не названо"
    показано = session.texts.count(t("finish.held_words", "ru", note=shorten(OVEN)))
    assert показано == HELD_SHOWN_LIMIT, "показано не ровно по пределу"
    assert t("finish.held_rest", "ru", rest=3) in session.texts, "остаток не назван"


async def test_отказ_телеграма_на_одном_голосовом_не_отменяет_остальных(
    domain_env: object, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Один голос телеграм не отдал — второй показать всё равно обязаны, и не молчком."""
    started()
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, voice_message("voice-bad"))
    await feed(dp, bot, voice_message("voice-good"))
    session.clear()

    исходный = type(session).make_request

    async def падать_на_первом(
        self: Any,
        bot: Any,
        method: Any,
        # ASYNC109: имя и вид параметра задан абстрактным классом aiogram —
        # оснастка обязана подходить под `BaseSession`, как и сама подмена.
        timeout: Any = None,  # noqa: ASYNC109
    ) -> Any:
        if type(method).__name__ == "SendVoice" and str(method.voice) == "voice-bad":
            raise TelegramBadRequest(method=method, message="voice not found")
        return await исходный(self, bot, method, timeout)

    monkeypatch.setattr(type(session), "make_request", падать_на_первом)

    await feed(dp, bot, text_message("/finish"))

    assert ("voice-good", t("finish.held_voice", "ru")) in отправленные_голоса(session), (
        "второй голос не показан"
    )
    assert t("finish.held_failed", "ru", failed=1) in session.texts
    assert any("не показалось" in r.message for r in caplog.records)


async def test_показ_есть_и_по_records_посреди_обхода(domain_env: object) -> None:
    """Та же функция на оба входа: посреди обхода кадр ещё можно прислать."""
    started()
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, text_message(OVEN))
    session.clear()
    await feed(dp, bot, text_message("/records"))

    assert t("finish.held", "ru", count=1) in session.texts
    assert t("finish.held_words", "ru", note=shorten(OVEN)) in session.texts
