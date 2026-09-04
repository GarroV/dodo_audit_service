"""T183: слова аудитора доезжают до записи со всех путей, где они звучали.

Слова живут у бота ровно один момент — пока идёт разбор материала. Дальше
`pending` их забывает (он в памяти процесса и перезапуска не переживает
намеренно), а в запись они не попадали: её текстом становится формулировка по
правилам фиксации, и это осознанно — сказанное на точке партнёру не
предназначено.

Что считается сырыми словами, решено здесь и обосновано в контракте блока:
**весь материал аудитора об этом кадре, каким он пришёл**, — подпись к кадру,
отдельное сообщение следом и расшифровка голоса. Все три приходят одним и тем
же путём (`Proposal.note`), и разделять их нечем: для разбора промаха важно то,
на чём система принимала решение.

Чего словами НЕ считается: формулировка модели, вопрос пункта методики и текст,
которым запись потом правят. Первые два — не слова человека вовсе, третий —
другой речевой акт: это уже сочинение отчёта, а не описание нарушения на точке.
"""

from __future__ import annotations

from pathlib import Path

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
    stub_transcribe,
    suggestion,
    text_message,
    voice_message,
)
from bot_harness import callback_query as callback
from conftest import requires_data

from src.bot import sidecar
from src.bot.app import build_dispatcher
from src.bot.config import BotSettings
from src.domain import Finding, get_state, start_inspection

pytestmark = [pytest.mark.asyncio, requires_data]

SETTINGS = BotSettings(token="unused-in-tests", allowed_ids=frozenset({AUDITOR_ID}), mode="polling")

#: Однозначная фраза синтетической карты: быстрый путь на ней срабатывает.
БЫСТРАЯ = "печь грязная"

#: Фраза владельца из D077 — ровно тот признак, который T166 померить не смог.
СКАЗАНО = "грязь на полке в горячем цехе, это чистота"


def начата() -> None:
    start_inspection(CHAT_ID, "Белград 2", "planned", "ru", date="2026-09-04", auditor="Гарро")


def записи() -> list[Finding]:
    state = get_state(CHAT_ID)
    assert state is not None, "проверки нет — смотреть не на что"
    return state.findings


async def test_подпись_к_кадру_сохраняется_словами_аудитора(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Главный случай: текстом записи стала формулировка модели, слова — свои."""
    начата()
    stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen", "Нагар на поде")))
    bot, _ = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, photo_message("frame-1", caption=СКАЗАНО))
    await feed(dp, bot, callback("rec:pick:0"))

    (запись,) = записи()
    assert запись.text == "Нагар на поде", "текст записи подменён словами аудитора"
    assert запись.words == СКАЗАНО, "слова аудитора потеряны вместе с процессом"


async def test_отдельное_сообщение_следом_тоже_слова(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Комментарий связывается с кадром тремя способами — словами он от этого быть не перестаёт."""
    начата()
    stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen", "Нагар на поде")))
    bot, _ = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, photo_message("frame-1"))
    await feed(dp, bot, text_message(СКАЗАНО))
    await feed(dp, bot, callback("rec:pick:0"))

    (запись,) = записи()
    assert запись.words == СКАЗАНО


async def test_расшифровка_голоса_сохраняется_словами(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """На точке говорят чаще, чем пишут: без голоса выборка потеряла бы главное.

    Расшифровка — не сами звуки, но именно она пришла в разбор, и разбирать
    промах модели придётся по ней же.
    """
    начата()
    stub_transcribe(monkeypatch, СКАЗАНО)
    stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen", "Нагар на поде")))
    bot, _ = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, photo_message("frame-1"))
    await feed(dp, bot, voice_message("voice-1"))
    await feed(dp, bot, callback("rec:pick:0"))

    (запись,) = записи()
    assert запись.words == СКАЗАНО


async def test_быстрый_путь_сохраняет_слова(domain_env: Path) -> None:
    """Там слова и есть текст записи — но храниться они обязаны своим полем.

    Совпадение это свойство одного пути, а не правило: полагаться на него
    значило бы читать выборку по-разному в зависимости от того, как легла
    запись.
    """
    начата()
    bot, _ = make_bot()
    dp = build_dispatcher(SETTINGS)

    sidecar.remember_zone(CHAT_ID, "hot_kitchen")

    await feed(dp, bot, photo_message("frame-1", caption=БЫСТРАЯ))

    (запись,) = записи()
    assert запись.words == БЫСТРАЯ


async def test_ручной_перечень_сохраняет_слова(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Модель молчит — слова аудитора становятся ценнее, а не наоборот."""
    начата()
    stub_classify(monkeypatch, suggestion())
    stub_manual(monkeypatch, (manual("CLN05", ("D1",), "печь"),))
    bot, _ = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, photo_message("frame-1", caption=СКАЗАНО))
    await feed(dp, bot, callback("rec:zm:hot_kitchen"))
    await feed(dp, bot, callback("rec:mi:0"))

    (запись,) = записи()
    assert запись.words == СКАЗАНО


async def test_кадр_без_комментария_слов_не_придумывает(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Аудитор не сказал ничего — записать формулировку модели его словами нельзя.

    Отличить «промолчал» от «запись старше T183» можно источником: у такой
    записи он `photo` (D044).
    """
    начата()
    stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen", "Нагар на поде")))
    bot, _ = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, photo_message("frame-1", message_id=808))
    await feed(dp, bot, callback("rec:analyze:808"))
    await feed(dp, bot, callback("rec:pick:0"))

    (запись,) = записи()
    assert запись.words == "", f"словами записана не речь человека: {запись.words!r}"
    assert запись.source == "photo"


async def test_правка_формулировки_слова_не_переписывает(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Правка — это сочинение отчёта, а не описание нарушения на точке.

    Допиши мы её к словам, «это чистота» стало бы неотличимо от правки текста, и
    признак, ради которого слова хранятся, перестал бы быть измеримым.
    """
    начата()
    stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen", "Нагар на поде")))
    bot, _ = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, photo_message("frame-1", caption=СКАЗАНО))
    await feed(dp, bot, callback("rec:pick:0"))
    await feed(dp, bot, callback("edit:1:text"))
    await feed(dp, bot, text_message("Нагар на подине печи, зона выпечки"))

    (запись,) = записи()
    assert запись.text == "Нагар на подине печи, зона выпечки"
    assert запись.words == СКАЗАНО
