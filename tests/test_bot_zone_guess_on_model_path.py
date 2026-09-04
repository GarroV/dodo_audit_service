"""Зона из памяти называется догадкой и на пути через модель (T156, задача #127).

Правило одно на оба пути, а поведение было разное. Быстрый путь (T124, T121)
честно говорил под записью: зону в этих словах вы не называли, поставил
прошлую. На пути через модель того же предупреждения не было — подсказка
уходила в модель, модель возвращала ту же зону уже как свою, и подтверждение
печаталось без оговорки.

Опасность здесь меньше: зона видна на кнопке кандидата до подтверждения, то
есть промах не тихий. Но зона — то, куда уезжает вычет в отчёте партнёру, и
объяснять аудитору происхождение зоны в одном месте и умалчивать в другом
нельзя: он перестаёт доверять пометке вовсе.

Догадкой зона считается ровно тогда, когда её никто не называл в этих словах, а
запись легла в ту самую зону, которую бот подставил из памяти. Ответила модель
другой зоной — это её ответ, а не память, и пометки такая запись не получает:
пометка не про «зону выбрал не человек», а про «зону взяли из прошлой записи».
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from bot_harness import (
    AUDITOR_ID,
    CHAT_ID,
    callback_query,
    candidate,
    feed,
    make_bot,
    manual,
    photo_message,
    stub_classify,
    stub_manual,
    suggestion,
)

from src.bot import sidecar
from src.bot.app import build_dispatcher
from src.bot.config import BotSettings
from src.bot.texts import t
from src.domain import get_state, start_inspection

pytestmark = pytest.mark.asyncio

SETTINGS = BotSettings(
    token="unused-in-tests",
    allowed_ids=frozenset({AUDITOR_ID}),
    mode="polling",
    auditor_names={},
)

#: Оговорка про зону из памяти — та же самая, что у быстрого пути. Второй
#: формулировки заводить нельзя: разошлись бы, как разошлось само поведение.
GUESS = t("record.fixed_zone_guess", "ru").strip()


def начата() -> None:
    start_inspection(CHAT_ID, "Белград 2", "planned", "ru", date="2026-08-21", auditor="Гарро")


async def разобрать_моделью(
    dp: Any, bot: Any, monkeypatch: pytest.MonkeyPatch, *, зона: str, слова: str
) -> None:
    """Кадр с комментарием → модель отвечает кандидатом → аудитор его подтверждает."""
    stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", зона, "Нагар на поду печи")))
    await feed(dp, bot, photo_message("frame-1", caption=слова, message_id=501))
    await feed(dp, bot, callback_query("rec:pick:0"))


async def test_подтверждённая_запись_называет_зону_догадкой_если_её_не_называли(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Сам дефект: слова про зону молчат, зона пришла из прошлой записи."""
    начата()
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await разобрать_моделью(dp, bot, monkeypatch, зона="hot_kitchen", слова="мусор в углу")

    состояние = get_state(CHAT_ID)
    assert состояние is not None and состояние.findings, "запись не появилась"
    assert GUESS in session.last_text, "зона взята из памяти, а подтверждение об этом молчит"


async def test_названная_словами_зона_догадкой_не_называется(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Пометка не бывает дежурной: сказал человек — оговорки нет."""
    начата()
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await разобрать_моделью(dp, bot, monkeypatch, зона="dining", слова="в зале урна переполнена")

    assert GUESS not in session.last_text


async def test_своя_зона_модели_догадкой_из_памяти_не_считается(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Модель ответила не тем, что ей подсказали, — память тут ни при чём."""
    начата()
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await разобрать_моделью(dp, bot, monkeypatch, зона="dining", слова="мусор в углу")

    assert GUESS not in session.last_text


async def test_зона_выбранная_кнопкой_догадкой_не_называется(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Кандидат без зоны: её называет сам аудитор кнопкой, и это не память."""
    начата()
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")
    stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "", "Нагар на поду печи")))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, photo_message("frame-1", caption="мусор в углу", message_id=501))
    await feed(dp, bot, callback_query("rec:pick:0"))
    await feed(dp, bot, callback_query("rec:zp:hot_kitchen"))

    состояние = get_state(CHAT_ID)
    assert состояние is not None and состояние.findings, "запись не появилась"
    assert GUESS not in session.last_text


async def test_ручной_выбор_пункта_тоже_называет_зону_из_памяти_догадкой(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Модель недоступна — перечень собирается по зоне, и зона та же из памяти.

    Путь другой, правило то же: аудитор выбрал пункт, но не зону, а вычет
    уедет партнёру именно в неё.
    """
    начата()
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")
    stub_classify(monkeypatch, suggestion())
    stub_manual(monkeypatch, (manual("CLN05", ("D1",), "Печь чистая?"),))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, photo_message("frame-1", caption="мусор в углу", message_id=501))
    await feed(dp, bot, callback_query("rec:mi:0"))

    состояние = get_state(CHAT_ID)
    assert состояние is not None and состояние.findings, "запись не появилась"
    assert GUESS in session.last_text


async def test_зона_названная_кнопкой_в_ручном_перечне_догадкой_не_называется(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Памяти нет, зону спросили кнопкой — и записал пункт по ней сам аудитор.

    Ветка, на которой пометка легче всего становится дежурной: перечень тут
    собран по зоне, но зону эту назвал человек, а не прошлая запись.
    """
    начата()
    stub_classify(monkeypatch, suggestion())
    stub_manual(monkeypatch, (manual("CLN05", ("D1",), "Печь чистая?"),))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, photo_message("frame-1", caption="мусор в углу", message_id=501))
    await feed(dp, bot, callback_query("rec:zm:hot_kitchen"))
    await feed(dp, bot, callback_query("rec:mi:0"))

    состояние = get_state(CHAT_ID)
    assert состояние is not None and состояние.findings, "запись не появилась"
    assert GUESS not in session.last_text
