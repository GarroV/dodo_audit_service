"""Занятый пункт в перечне помечен, а не предложен молча (T137, issue #108).

Сцена целиком. Аудитор снимает то, что уже записал. Сверка со списком нарушений
поднимает тот же пункт в той же зоне, движок отказывает, и бот честно говорит
«уже зафиксировано» (`record.duplicate`). Дальше материал уходит модели — и это
задумано: на кадре бывает второе нарушение, и упереться в отказ молча аудитор не
должен. Неверно другое: модель предлагает **тот же самый** пункт, и в перечне он
ничем не отличается от остальных. Нажатие даст второй отказ подряд — по тому же
поводу, о котором продукт только что сказал сам.

Пометка идёт по ПАРЕ «пункт + зона», а не по одному коду. Тот же пункт в другой
зоне — законная и частая запись (движок отказывает именно на паре), и пометить
его значило бы отговаривать аудитора от верного действия.

Ручного перечня (`manual_keyboard`) это не касается: там на кнопку отведено 34
знака, пометка съела бы формулировку пункта, и разговор об этом отдельный.
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
    suggestion,
)
from bot_harness import callback_query as callback

from src.bot import sidecar
from src.bot.app import build_dispatcher
from src.bot.config import BotSettings
from src.bot.texts import t
from src.domain import add_finding, get_state, start_inspection

pytestmark = pytest.mark.asyncio

SETTINGS = BotSettings(
    token="unused-in-tests",
    allowed_ids=frozenset({AUDITOR_ID}),
    mode="polling",
    auditor_names={},
)

#: Однозначная фраза синтетической карты кадров: строка «Печь» произнесена
#: целиком, колонка выбрана словом «грязная» → CLN05, единственный класс D1.
CLEAR = "печь грязная"


async def test_после_отказа_модель_предлагает_занятый_пункт_с_пометкой(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Сам дефект, целиком: запись, второй такой же кадр, отказ, перечень модели."""
    start_inspection(CHAT_ID, "Белград 2", "planned", "ru")
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")
    stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen", "Печь в нагаре")))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, photo_message("frame-1", caption=CLEAR))
    state = get_state(CHAT_ID)
    assert state is not None
    assert len(state.findings) == 1, "сверка со списком не записала — тест проверяет не то"

    session.clear()
    await feed(dp, bot, photo_message("frame-2", caption=CLEAR))

    told = " ".join(session.texts)
    assert t("record.duplicate", "ru", n=1, item="", zone="")[:20] in told, "отказа не было"
    assert t("record.candidate_taken", "ru", n=1) in session.last_text


async def test_пометка_ставится_по_паре_пункт_и_зона_а_не_по_коду(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Тот же пункт в другой зоне — законная запись, отговаривать от неё нельзя."""
    start_inspection(CHAT_ID, "Белград 2", "planned", "ru")
    add_finding(CHAT_ID, "CLN05", "D1", "hot_kitchen", "Нагар на подине печи")
    stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "dining", "Нагар")))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, photo_message("frame-1", message_id=501))
    await feed(dp, bot, callback("rec:analyze:501"))

    assert t("record.candidate_taken", "ru", n=1) not in session.last_text


async def test_занятый_кандидат_остаётся_нажимаемым(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Кнопку не убираем: уход к модели задуман, и выбор остаётся за человеком.

    Нажатие приведёт к отказу с кнопками правки уже существующей записи (T127) —
    это законный путь «доснять фото» или «поправить», а не тупик. Пропади кнопка,
    поехали бы и номера остальных кандидатов.
    """
    start_inspection(CHAT_ID, "Белград 2", "planned", "ru")
    add_finding(CHAT_ID, "CLN05", "D1", "hot_kitchen", "Нагар на подине печи")
    stub_classify(
        monkeypatch,
        suggestion(
            candidate("CLN05", "D1", "hot_kitchen", "Нагар"),
            candidate("CLN02", "D1", "dishwashing", "Налёт на смесителе"),
        ),
    )
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, photo_message("frame-1", message_id=501))
    await feed(dp, bot, callback("rec:analyze:501"))

    assert session.keyboard_data()[:2] == ["rec:pick:0", "rec:pick:1"]
    assert t("record.candidate_taken", "ru", n=1) in session.last_text


async def test_пометка_называет_номер_занявшей_записи(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Номер — то, чем аудитор находит запись и правит её; без него пометка тупик."""
    start_inspection(CHAT_ID, "Белград 2", "planned", "ru")
    add_finding(CHAT_ID, "CLN02", "D1", "dishwashing", "Налёт")
    add_finding(CHAT_ID, "CLN05", "D1", "hot_kitchen", "Нагар")
    stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen", "Нагар")))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, photo_message("frame-1", message_id=501))
    await feed(dp, bot, callback("rec:analyze:501"))

    assert t("record.candidate_taken", "ru", n=2) in session.last_text
    assert t("record.candidate_taken", "ru", n=1) not in session.last_text


async def test_пометка_переводится(domain_env: object, monkeypatch: pytest.MonkeyPatch) -> None:
    """Язык — параметр: русская пометка в английском перечне так же неверна."""
    start_inspection(CHAT_ID, "Belgrade 2", "planned", "en", ui_lang="en")
    add_finding(CHAT_ID, "CLN05", "D1", "hot_kitchen", "Soot")
    stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen", "Soot")))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, photo_message("frame-1", message_id=501))
    await feed(dp, bot, callback("rec:analyze:501"))

    assert t("record.candidate_taken", "en", n=1) in session.last_text
    assert t("record.candidate_taken", "ru", n=1) not in session.last_text
