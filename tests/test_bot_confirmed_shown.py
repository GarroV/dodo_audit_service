"""Подтверждённая запись показана не скупее автоматической (T135, issue #106).

Асимметрия была обратной здравому смыслу. Запись, легшая по словам сама, без
подтверждения (T121, D064), показывалась блоком: вопрос пункта словами, слова
аудитора, сработавшая строка карты. Запись, которую аудитор подтвердил кнопкой,
показывалась одной строкой — `#1 CLN05 · D1 · Тепловой участок`, — то есть
подробно там, где человек ничего не подтверждал, и скупо там, где подтверждал.

Строка эта не читается. **Код глазами не проверяется** — это тот же довод, по
которому в блок быстрого пути вносили вопрос пункта: на примере владельца
«кассовая зона, просрочка чизкейк» и `CLN02`, и `PRD10` выглядят одинаково
правдоподобно, пока их не прочитать словами.

Спека (`docs/06-mvp-bot.md`, шаг 5) требовала здесь одну строку и объясняла это
тем, что пункт аудитор «прочитал на кнопке». На кнопке до T136 стояла голая
цифра, а формулировка — в перечне выше, откуда взгляд уже ушёл. Требование
поэтому пересмотрено вместе с задачей, и спека правится тем же изменением.

Меры при этом соблюдены: подтверждение — не блок быстрого пути. Строки карты
здесь нет (её не было), «ваших слов» нет (текстом записи стала формулировка
модели, и звать её словами аудитора — вранье). Есть ровно две добавки: вопрос
пункта и то, что уйдёт в отчёт партнёру. Таблицы после каждого кадра, которую
спека запрещает отдельно, нет и не появилось.
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
    manual,
    photo_message,
    stub_classify,
    stub_manual,
    suggestion,
)
from bot_harness import callback_query as callback

from src.bot.app import build_dispatcher
from src.bot.config import BotSettings
from src.bot.texts import t
from src.bot.view import zone_title
from src.domain import get_item, get_state, start_inspection

pytestmark = pytest.mark.asyncio

SETTINGS = BotSettings(
    token="unused-in-tests",
    allowed_ids=frozenset({AUDITOR_ID}),
    mode="polling",
    auditor_names={},
)

#: Формулировка, которую предлагает модель и которая уходит в отчёт партнёру.
#: Нарочно не совпадает с вопросом пункта: это разные вещи, и обе должны быть
#: видны аудитору до того, как документ уедет.
WORDING = "Нагар по всему поду печи, следы жира на дверце"


async def confirm_model_candidate(dp: Any, bot: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Обычный путь с подтверждением: кадр → «Разобрать» → нажатие на кандидата."""
    stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen", WORDING)))
    await feed(dp, bot, photo_message("frame-1", message_id=501))
    await feed(dp, bot, callback("rec:analyze:501"))
    await feed(dp, bot, callback("rec:pick:0"))


async def test_подтверждённая_запись_называет_пункт_словами(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Сам дефект: код глазами не читается, а вопрос пункта читается."""
    start_inspection(CHAT_ID, "Белград 2", "planned", "ru")
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await confirm_model_candidate(dp, bot, monkeypatch)

    state = get_state(CHAT_ID)
    assert state is not None and len(state.findings) == 1, "запись не появилась"
    assert get_item("CLN05").question("ru") in session.last_text


async def test_подтверждённая_запись_показывает_то_что_уйдёт_в_отчёт(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Формулировку партнёру аудитор обязан прочитать глазами, а не подтвердить вслепую."""
    start_inspection(CHAT_ID, "Белград 2", "planned", "ru")
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await confirm_model_candidate(dp, bot, monkeypatch)

    assert WORDING in session.last_text


async def test_под_записью_остались_кнопки_правки(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Показ стал шире, а править запись по-прежнему нечем, кроме этих кнопок (T056)."""
    start_inspection(CHAT_ID, "Белград 2", "planned", "ru")
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await confirm_model_candidate(dp, bot, monkeypatch)

    assert session.keyboard_data() == [
        "edit:1:zone",
        "edit:1:level",
        "edit:1:text",
        "edit:1:drop",
    ]


async def test_пункт_не_повторяется_дважды_когда_текст_записи_и_есть_вопрос_пункта(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ручной выбор без комментария: текстом записи становится сам вопрос пункта.

    Показать его дважды — вопросом и «в отчёт» — значит выдать за две вещи одну.
    """
    start_inspection(CHAT_ID, "Белград 2", "planned", "ru")
    stub_classify(monkeypatch, suggestion())
    question = get_item("CLN05").question("ru")
    stub_manual(monkeypatch, (manual("CLN05", ("D1",), question),))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, photo_message("frame-1", message_id=501))
    await feed(dp, bot, callback("rec:analyze:501"))
    await feed(dp, bot, callback("rec:zm:hot_kitchen"))
    await feed(dp, bot, callback("rec:mi:0"))

    state = get_state(CHAT_ID)
    assert state is not None and len(state.findings) == 1, "запись не появилась"
    assert session.last_text.count(question) == 1, "вопрос пункта показан дважды"


async def test_на_английской_проверке_пункт_назван_по_английски(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Язык — параметр: вопрос пункта берётся на языке разговора, а не константой."""
    start_inspection(CHAT_ID, "Belgrade 2", "planned", "en", ui_lang="en")
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await confirm_model_candidate(dp, bot, monkeypatch)

    assert get_item("CLN05").question("en") in session.last_text
    assert get_item("CLN05").question("ru") not in session.last_text


async def test_показ_подтверждённой_и_автоматической_записи_симметричен(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Асимметрия и была дефектом: вопрос пункта обязан стоять в обоих показах.

    Автоматическая запись сверх этого несёт слова аудитора и строку карты —
    вещи, которых у подтверждённой записи нет вовсе, — но общее у них одно:
    пункт, названный словами. Тест держит именно это общее.
    """
    start_inspection(CHAT_ID, "Белград 2", "planned", "ru")
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await confirm_model_candidate(dp, bot, monkeypatch)
    confirmed = session.last_text

    line = t(
        "record.saved",
        "ru",
        n=1,
        code="CLN05",
        level="D1",
        zone=zone_title("hot_kitchen", "ru"),
    )
    assert line in confirmed
    assert get_item("CLN05").question("ru") in confirmed
