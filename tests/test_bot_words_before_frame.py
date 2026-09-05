"""Слова, сказанные ДО кадра, придерживаются и ждут его (T229, решение D090).

До этой задачи комментарий без кадра получал отказ: «один комментарий записью
не станет». Про запись это была правда — фотофиксация обязательна всегда (D078),
и D090 её не отменяет, — а про слова неправда: они пропадали вместе с отказом,
и аудитору оставалось наговорить их заново после кадра. Решение владельца D090
разводит эти две вещи: записи без кадра по-прежнему нет, но бот **говорит, что
ждёт фотографию**, и собирает запись в момент прихода кадра.

Что защищает этот файл — пять вещей.

**Отказа нет, есть ожидание.** Аудитор должен понять, что его услышали и чего
ждут, а не что он сделал что-то не так.

**Запись без кадра всё равно не появляется.** Требование D078 в силе, и это
главное, что могло сломаться: придержать слова и записать их — разные вещи.

**Кадр собирает запись из придержанных слов.** Иначе ожидание было бы вежливым
способом потерять сказанное.

**Порядок сохраняется.** Две находки словами, следом два кадра — каждая пара
своя; иначе кадр уехал бы в отчёт партнёру под чужой формулировкой.

**Подпись к кадру сильнее придержанных слов.** Сказанное прямо об этих кадрах
относится к ним, а придержанное дожидается своего кадра.
"""

from __future__ import annotations

import pytest
from bot_harness import (
    AUDITOR_ID,
    CHAT_ID,
    candidate,
    feed,
    make_bot,
    photo_message,
    stub_classify,
    stub_transcribe,
    suggestion,
    text_message,
    voice_message,
)

from src.bot import sidecar
from src.bot.app import build_dispatcher
from src.bot.config import BotSettings
from src.bot.texts import t, with_photo_rule
from src.domain import Finding, get_state, start_inspection

pytestmark = pytest.mark.asyncio

SETTINGS = BotSettings(token="unused-in-tests", allowed_ids=frozenset({AUDITOR_ID}), mode="polling")

#: Однозначная фраза на синтетической карте: строка «Печь» произнесена целиком,
#: колонка выбрана словом «грязная» → CLN05. Зону слова не называют — её
#: подставит память, как на точке.
OVEN = "печь грязная"

#: Вторая фраза той же зоны, ведущая в ДРУГОЙ пункт: пара «пункт + зона»
#: занимается один раз, и две одинаковые фразы дали бы отказ движка вместо
#: второй записи — то есть тест зеленел бы на боте, который порядок не сохранил.
FURNITURE = "мебель участка грязная"


def started(lang: str = "ru") -> None:
    start_inspection(CHAT_ID, "Белград 2", "planned", lang, ui_lang=lang)
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")


def findings() -> list[Finding]:
    state = get_state(CHAT_ID)
    return [] if state is None else list(state.findings)


async def test_слова_до_кадра_получают_ожидание_а_не_отказ(domain_env: object) -> None:
    """Первая половина пары «принял / сохранено»: услышал и жду фотографию."""
    started()
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, text_message(OVEN))

    assert session.last_text == with_photo_rule(t("material.waiting_photo", "ru"), "ru")
    assert t("material.photo_required", "ru") in session.last_text, (
        "ожидание без правила читается сбоем продукта"
    )


async def test_одни_слова_записью_не_становятся(domain_env: object) -> None:
    """D078 в силе: придержать — не значит записать."""
    started()
    bot, _ = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, text_message(OVEN))

    assert findings() == [], "запись появилась без кадра — нарушено D078"


async def test_кадр_собирает_запись_из_придержанных_слов(domain_env: object) -> None:
    """Сердце задачи: сказанное до кадра доживает до кадра и становится записью."""
    started()
    bot, _ = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, text_message(OVEN))
    await feed(dp, bot, photo_message("frame-oven"))

    assert [(f.code, f.zone, f.photos) for f in findings()] == [
        ("CLN05", "hot_kitchen", ["frame-oven"])
    ], "придержанные слова не собрались в запись с пришедшим кадром"


async def test_кадр_после_слов_не_спрашивает_разобрать(domain_env: object) -> None:
    """Кадр, забравший слова, — не кадр без комментария: вопроса «Разобрать?» нет.

    Спросить его значило бы предложить заплатить вызовом модели за то, на что
    человек уже ответил словами.
    """
    started()
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, text_message(OVEN))
    session.clear()
    await feed(dp, bot, photo_message("frame-oven"))

    assert not any(data.startswith("rec:analyze:") for data in session.keyboard_data()), (
        "по кадру с уже сказанными словами предложен разбор кадра"
    )


async def test_голосовое_до_кадра_ждёт_его_и_расшифровывается_при_сборке(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Голосовое — тот же случай D090, ради которого решение и принято."""
    started()
    heard = stub_transcribe(monkeypatch, OVEN)
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, voice_message("voice-1"))
    assert session.last_text == with_photo_rule(t("material.waiting_photo", "ru"), "ru")
    assert findings() == [], "голосовое без кадра стало записью"

    await feed(dp, bot, photo_message("frame-voice"))

    assert len(heard) == 1, "расшифровка не случилась"
    assert [(f.code, f.photos) for f in findings()] == [("CLN05", ["frame-voice"])]


async def test_две_находки_словами_ложатся_на_свои_кадры_по_порядку(
    domain_env: object,
) -> None:
    """Порядок прихода — единственное, чем эти пары можно связать."""
    started()
    bot, _ = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, text_message(OVEN))
    await feed(dp, bot, text_message(FURNITURE))
    await feed(dp, bot, photo_message("frame-1"))
    await feed(dp, bot, photo_message("frame-2"))

    assert [(f.code, f.photos) for f in findings()] == [
        ("CLN05", ["frame-1"]),
        ("CLN06", ["frame-2"]),
    ], "кадры разошлись со словами по порядку"


async def test_подпись_к_кадру_сильнее_придержанных_слов(domain_env: object) -> None:
    """Сказанное прямо об этих кадрах относится к ним, придержанное ждёт своего."""
    started()
    bot, _ = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, text_message(OVEN))
    await feed(dp, bot, photo_message("frame-captioned", caption=FURNITURE))

    assert [(f.code, f.photos) for f in findings()] == [("CLN06", ["frame-captioned"])], (
        "подпись к кадру склеилась с чужими придержанными словами"
    )

    await feed(dp, bot, photo_message("frame-oven"))

    assert [(f.code, f.photos) for f in findings()] == [
        ("CLN06", ["frame-captioned"]),
        ("CLN05", ["frame-oven"]),
    ], "придержанные слова не дождались своего кадра"


async def test_ждущий_кадр_забирает_слова_как_и_раньше(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Обратный порядок не сломан: кадр первым — слова садятся на него.

    Проверяется здесь потому, что придержанная очередь появилась рядом с
    очередью кадров: перепутай их приоритет — и комментарий уходил бы в
    ожидание, пока кадр ждёт его в соседней очереди.
    """
    started()
    stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen", "печь")))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, photo_message("frame-first"))
    session.clear()
    await feed(dp, bot, text_message(OVEN))

    assert t("material.waiting_photo", "ru") not in session.texts, (
        "бот ждёт фотографию, хотя кадр уже прислан"
    )
    assert [(f.code, f.photos) for f in findings()] == [("CLN05", ["frame-first"])]


async def test_на_английской_проверке_ожидание_звучит_по_английски(
    domain_env: object,
) -> None:
    """Язык — параметр и здесь."""
    started(lang="en")
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, text_message("dirty oven"))

    assert t("material.waiting_photo", "en") in session.last_text
    assert t("material.waiting_photo", "ru") not in session.last_text
    assert t("material.photo_required", "en") in session.last_text


async def test_слова_до_кадра_в_сданной_проверке_по_прежнему_отказ(
    domain_env: object,
) -> None:
    """Запрет T201 сильнее ожидания: в сданную проверку не придерживается ничего.

    Придержи бот слова здесь — он пообещал бы запись, которой в сданной
    проверке не будет никогда.
    """
    started()
    sidecar.mark_handed_over(CHAT_ID, 0)
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, text_message(OVEN))
    assert t("material.waiting_photo", "ru") not in session.last_text

    await feed(dp, bot, photo_message("frame-after-seal"))

    assert findings() == [], "в сданной проверке появилась запись"
