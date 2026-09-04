"""T128: мелочи, которые врут аудитору (задача #103).

Три штуки, все — про сказанное ботом неправду или про несказанное вовсе.

**Причина, названная наугад.** На повторное нажатие устаревшей кнопки бот
отвечал «Предложение устарело — бот перезапускался». Он не перезапускался:
предложение забирается сразу после фиксации, гасится началом новой проверки и
исчезает при нажатии на кнопку из старого сообщения. Названная наугад причина
хуже неназванной — по ней человек начинает искать несуществующую поломку.

**Язык — параметр, никогда не константа** (жёсткое требование проекта). Из
бота `ui_lang` и `speech_lang` не задавались НИКОГДА: аудитор выбирал
английский отчёт, а разговор оставался русским, потому что в состоянии стояло
значение по умолчанию.

**Название точки в 300 знаков.** Принималось молча и уезжало в шапку отчёта и
в имя файла. Замерено: на 300 знаках сборка отчёта падает с `File name too
long`, и узнаёт об этом аудитор в конце проверки, когда сделать уже ничего
нельзя.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from bot_harness import AUDITOR_ID, CHAT_ID, feed, make_bot, text_message
from bot_harness import callback_query as callback

from src.bot.app import build_dispatcher
from src.bot.config import BotSettings
from src.bot.routers.start import UNIT_NAME_LIMIT
from src.bot.texts import t
from src.domain import get_state

pytestmark = pytest.mark.asyncio

SETTINGS = BotSettings(token="unused-in-tests", allowed_ids=frozenset({AUDITOR_ID}), mode="polling")


async def пройти_мастер(lang: str, unit: str = "Белград 2") -> object:
    """Мастер начала проверки целиком: название, вид, язык."""
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    await feed(dp, bot, text_message("/start"))
    await feed(dp, bot, callback("start:new"))
    await feed(dp, bot, text_message(unit))
    await feed(dp, bot, callback("start:kind:planned"))
    await feed(dp, bot, callback(f"start:lang:{lang}"))
    return session


# --- причина не называется наугад --------------------------------------------


async def test_устаревшее_предложение_не_винит_перезапуск(domain_env: Path) -> None:
    """Бот не перезапускался — и говорить этого не должен.

    Проверяется настоящий путь: проверка идёт, предложения в памяти нет,
    нажатие приходит от старого сообщения.
    """
    await пройти_мастер("ru")
    bot, session = make_bot()

    await feed(build_dispatcher(SETTINGS), bot, callback("rec:pick:0"))

    assert session.last_text == t("record.stale", "ru")
    assert "перезапус" not in session.last_text.lower(), "названа причина, которой не было"


# --- язык интерфейса следует за выбором аудитора -----------------------------


async def test_выбранный_язык_ложится_во_все_три_поля(domain_env: Path) -> None:
    """Поля разные и остаются разными, но из бота их наконец задают."""
    await пройти_мастер("en")

    проверка = get_state(CHAT_ID)
    assert проверка is not None
    assert (проверка.report_lang, проверка.ui_lang, проверка.speech_lang) == ("en", "en", "en")


async def test_после_выбора_английского_разговор_идёт_по_английски(domain_env: Path) -> None:
    """То самое расхождение: отчёт английский, а диалог оставался русским."""
    session = await пройти_мастер("en")

    assert session.last_text.startswith("Inspection started"), "диалог остался на прежнем языке"


async def test_русский_выбор_разговор_не_меняет(domain_env: Path) -> None:
    """Обратная сторона: русский остаётся русским, и это тоже параметр."""
    session = await пройти_мастер("ru")

    assert session.last_text.startswith("Проверка начата")
    проверка = get_state(CHAT_ID)
    assert проверка is not None and проверка.ui_lang == "ru"


# --- название точки не бесконечное -------------------------------------------


async def test_название_длиннее_предела_не_принимается(domain_env: Path) -> None:
    """Отказ приходит на вводе, а не отказом сборки отчёта в конце проверки."""
    длинное = "П" * (UNIT_NAME_LIMIT + 1)
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, text_message("/start"))
    await feed(dp, bot, callback("start:new"))
    await feed(dp, bot, text_message(длинное))

    assert session.last_text == t("start.unit_too_long", "ru", limit=UNIT_NAME_LIMIT)
    assert get_state(CHAT_ID) is None, "проверка завелась на негодном названии"


async def test_название_в_предел_принимается(domain_env: Path) -> None:
    """Предел не должен мешать живым названиям — на границе всё работает."""
    session = await пройти_мастер("ru", unit="П" * UNIT_NAME_LIMIT)

    проверка = get_state(CHAT_ID)
    assert проверка is not None and len(проверка.unit) == UNIT_NAME_LIMIT
    assert session.last_text.startswith("Проверка начата")


async def test_предел_названия_влезает_в_имя_файла_отчёта(domain_env: Path) -> None:
    """Замер, а не прикидка: имя файла на пределе обязано влезать в 255 байт.

    255 байт — предел имени файла на ext4, то есть на площадке, где продукт
    живёт контейнером (D053). Кириллица в UTF-8 — два байта на знак, и на 100
    знаках названия имя уже 258 байт: отчёт не собирается вовсе, а узнаёт об
    этом аудитор в конце проверки.
    """
    длинный_аудитор = "И" * 40
    имя = f"Аудит {'П' * UNIT_NAME_LIMIT} - {длинный_аудитор} - 21.08.2026.pdf"

    assert len(имя.encode("utf-8")) < 255, "на пределе названия имя файла отчёта не влезает"
