"""Процент во время обхода не показывается — только в конце (T162, D072).

Решение владельца: «показывать только в конце». Причина, названная ему и
оставшаяся в силе: во время обхода число аудитору ничего не даёт, а соблазн не
записывать мелочь создаёт — и такое влияние не видно ни в отчёте, ни в базе.

Проверяется ровно граница: всё, что бот говорит по ходу проверки (фиксация по
кадру, фиксация словами, правка, удаление), процента не содержит, а итог при
завершении — содержит. Итог и есть тот «конец», ради которого решение принято:
снять процент и там значило бы, что аудитор не видит оценку никогда.

Ищется знак `%`, а не конкретное число: строка `· 99.5%` собирается из полей, и
проверка на «97.5» прошла бы мимо любой другой проверки. Процент — единственное
место в диалоге, где этот знак вообще появляется.
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
    text_message,
)
from bot_harness import callback_query as callback

from src.bot import sidecar
from src.bot.app import build_dispatcher
from src.bot.config import BotSettings
from src.domain import Finding, get_state, start_inspection

pytestmark = pytest.mark.asyncio

SETTINGS = BotSettings(token="unused-in-tests", allowed_ids=frozenset({AUDITOR_ID}), mode="polling")

#: Однозначные слова синтетической карты: строка «Печь» плюс колонка «грязная»
#: → CLN05 с единственным классом D1. Запись по ним появляется без нажатия
#: (T121, D064) — то есть по ходу обхода, где процента быть не должно.
CLEAR = "печь грязная"


def started() -> None:
    start_inspection(CHAT_ID, "Белград 2", "Плановая", "ru")


def findings() -> list[Finding]:
    state = get_state(CHAT_ID)
    return [] if state is None else list(state.findings)


async def test_подтверждение_записи_по_кадру_без_процента(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Запись сделана — строка о ней есть, процента в ней нет."""
    started()
    stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen", "Печь в нагаре")))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, photo_message("frame-1", caption="печь грязная в нагаре"))
    session.clear()
    await feed(dp, bot, callback("rec:pick:0"))

    line = session.texts[0]
    assert "#1" in line and "CLN05" in line, "запись перестала показываться вовсе"
    assert "%" not in line, "процент во время обхода показывать нельзя (D072)"


async def test_фиксация_словами_без_процента(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Запись без подтверждения (T121) — то же правило: процента нет."""
    started()
    stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen")))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")

    await feed(dp, bot, photo_message("frame-1", caption=CLEAR))

    assert len(findings()) == 1, "запись словами не появилась — проверять нечего"
    assert "%" not in session.last_text, "процент во время обхода показывать нельзя (D072)"


async def test_правка_записи_без_процента(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """После правки процент пересчитывается движком, но аудитору не показывается."""
    started()
    stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen", "Печь в нагаре")))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, photo_message("frame-1", caption="печь грязная в нагаре"))
    await feed(dp, bot, callback("rec:pick:0"))
    session.clear()
    await feed(dp, bot, callback("ez:1:dining"))

    записи = findings()
    assert записи and записи[0].zone == "dining", "правка не применилась — проверять нечего"
    assert "%" not in session.last_text, "процент после правки показывать нельзя (D072)"


async def test_удаление_записи_без_процента(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`/undo` называет снятую запись, а не новую оценку."""
    started()
    stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen", "Печь в нагаре")))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, photo_message("frame-1", caption="печь грязная в нагаре"))
    await feed(dp, bot, callback("rec:pick:0"))
    session.clear()
    await feed(dp, bot, text_message("/undo"))

    assert findings() == [], "запись не снялась — проверять нечего"
    assert "#1" in session.last_text, "бот перестал называть снятую запись"
    assert "%" not in session.last_text, "процент после удаления показывать нельзя (D072)"


async def test_итог_при_завершении_процент_показывает(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Обратная сторона решения: в конце оценка видна, иначе её не видно нигде."""
    started()
    stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen", "Печь в нагаре")))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, photo_message("frame-1", caption="печь грязная в нагаре"))
    await feed(dp, bot, callback("rec:pick:0"))
    session.clear()
    await feed(dp, bot, text_message("/finish"))

    assert any("%" in text for text in session.texts), "в конце оценка обязана быть видна"
