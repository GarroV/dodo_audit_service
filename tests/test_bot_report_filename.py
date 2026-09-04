"""T128 (переоткрытая часть, issue #103): предел имени файла отчёта — 255 байт.

Имя файла собирает движок (`engine/report.py: default_name`) как
«Аудит <точка> - <аудитор> - <дата>.pdf» (для английского — «Audit »). Предел
имени файла на ext4 (боевая площадка, D053) — 255 БАЙТ, а не знаков: macOS
дефект прячет (считает символы), alpine падает сборкой отчёта в самом конце
проверки, когда аудитор уже уехал с точки.

Название точки было ограничено 60 ЗНАКАМИ (`UNIT_NAME_LIMIT`), но не байтами:
60 эмодзи — это те же 60 знаков, но уже 240 байт, то есть одно название съедает
весь бюджет имени файла. Имя аудитора не было ограничено вовсе: оно берётся из
профиля Telegram (`first_name` + `last_name`, до 64 знаков каждое, с эмодзи).

Оба дефекта здесь и проверяются: байтовый предел названия точки (отказ на
вводе) и байтовая обрезка имени аудитора (обрезка молча не уезжает — она видна
в имени и названа аудитору отдельной строкой при старте проверки).
"""

from __future__ import annotations

from datetime import date as Date
from pathlib import Path

import pytest
from bot_harness import AUDITOR_ID, CHAT_ID, feed, make_bot, text_message
from bot_harness import callback_query as callback

from src.bot.app import build_dispatcher
from src.bot.auditor import AUDITOR_NAME_BYTE_LIMIT
from src.bot.config import BotSettings
from src.bot.routers.start import UNIT_NAME_BYTE_LIMIT, UNIT_NAME_LIMIT
from src.bot.texts import t
from src.domain import get_state

pytestmark = pytest.mark.asyncio

SETTINGS = BotSettings(token="unused-in-tests", allowed_ids=frozenset({AUDITOR_ID}), mode="polling")


async def пройти_мастер(
    unit: str = "Белград 2", *, full_name: str = "Владимир Гарро", lang: str = "ru"
) -> object:
    """Мастер начала проверки целиком, с именем профиля во всех шагах разом —
    как оно и приходит от Telegram в настоящем чате.
    """
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    await feed(dp, bot, text_message("/start", full_name=full_name))
    await feed(dp, bot, callback("start:new", full_name=full_name))
    await feed(dp, bot, text_message(unit, full_name=full_name))
    await feed(dp, bot, callback("start:kind:planned", full_name=full_name))
    await feed(dp, bot, callback(f"start:lang:{lang}", full_name=full_name))
    return session


def имя_файла_отчёта(unit: str, auditor: str, iso_date: str) -> str:
    """Та же формула, что в `engine/report.py: default_name`, руками.

    Импорт `engine` из тестов продукта запрещён контрактом import-linter
    (см. `tests/test_bot_small_lies.py` — там формула собрана так же). Чистка
    запрещённых для имени файла символов (`clean()` движка) здесь не нужна:
    в тестовых значениях таких символов нет, и по формуле она может только
    УКОРОТИТЬ строку (заменить символ 1-в-1 или схлопнуть пробелы), поэтому
    собранная руками строка — не меньше, а фактическая от движка — не длиннее.
    """
    дата = Date.fromisoformat(iso_date).strftime("%d.%m.%Y")
    return f"Аудит {unit} - {auditor} - {дата}.pdf"


# --- имя аудитора обрезается по байтам, а не остаётся безграничным ------------


async def test_имя_профиля_200_кириллических_знаков_влезает_в_имя_файла(
    domain_env: Path,
) -> None:
    """Худший источник — профиль Telegram: 200 знаков, что бот не выбирает сам."""
    await пройти_мастер(full_name="И" * 200)

    inspection = get_state(CHAT_ID)
    assert inspection is not None
    имя = имя_файла_отчёта(inspection.unit, inspection.auditor, inspection.date)
    assert len(имя.encode("utf-8")) <= 255, "имя файла отчёта не влезает в предел ext4"


async def test_имя_профиля_из_эмодзи_тоже_влезает_в_имя_файла(domain_env: Path) -> None:
    """Эмодзи — 4 байта на знак: та же арифметика, что и для названия точки."""
    await пройти_мастер(full_name="🍕" * 60)

    inspection = get_state(CHAT_ID)
    assert inspection is not None
    имя = имя_файла_отчёта(inspection.unit, inspection.auditor, inspection.date)
    assert len(имя.encode("utf-8")) <= 255, "имя файла отчёта не влезает в предел ext4"


async def test_живое_короткое_имя_не_трогается_вовсе(domain_env: Path) -> None:
    """Живым именам предел не должен мешать: ни обрезки, ни лишней строки в чате."""
    session = await пройти_мастер(full_name="Владимир Гарро")

    inspection = get_state(CHAT_ID)
    assert inspection is not None
    assert inspection.auditor == "Владимир Гарро", "короткое имя обрезалось без нужды"
    assert "…" not in inspection.auditor
    предупреждение = t("start.auditor_name_shortened", "ru")
    assert предупреждение not in session.last_text, "лишняя строка про обрезку для живого имени"


async def test_обрезка_имени_названа_аудитору_в_чате(domain_env: Path) -> None:
    """Молчаливая обрезка хуже отказа: аудитор обязан узнать об этом сразу."""
    session = await пройти_мастер(full_name="И" * 200)

    inspection = get_state(CHAT_ID)
    assert inspection is not None
    assert inspection.auditor != "И" * 200, "имя аудитора не обрезалось вовсе"
    assert len(inspection.auditor.encode("utf-8")) <= AUDITOR_NAME_BYTE_LIMIT
    ожидаемый_текст = t("start.auditor_name_shortened", "ru")
    assert ожидаемый_текст in session.last_text, "чат не сказал аудитору про обрезку имени"


# --- название точки: предел уже не только в знаках, но и в байтах -------------


async def test_название_из_60_эмодзи_не_принимается(domain_env: Path) -> None:
    """60 эмодзи — это 60 ЗНАКОВ (в старый предел укладываются), но 240 байт —
    почти весь бюджет имени файла разом. Проверка не должна заводиться молча.
    """
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, text_message("/start"))
    await feed(dp, bot, callback("start:new"))
    await feed(dp, bot, text_message("🍕" * UNIT_NAME_LIMIT))

    assert get_state(CHAT_ID) is None, "проверка завелась на названии, негодном для имени файла"
    assert len(("🍕" * UNIT_NAME_LIMIT).encode("utf-8")) > UNIT_NAME_BYTE_LIMIT
    assert session.last_text == t("start.unit_too_long_bytes", "ru")


async def test_название_в_60_кириллических_знаков_принимается_по_прежнему(
    domain_env: Path,
) -> None:
    """Старое поведение не сломано: живым названиям байтовый предел не мешает."""
    session = await пройти_мастер(unit="П" * UNIT_NAME_LIMIT)

    inspection = get_state(CHAT_ID)
    assert inspection is not None
    assert len(inspection.unit) == UNIT_NAME_LIMIT
    assert session.last_text.startswith("Проверка начата")


# --- худший достижимый случай целиком ------------------------------------------


async def test_худший_случай_название_и_имя_на_пределе_влезает_в_255_байт(
    domain_env: Path,
) -> None:
    """Название точки на пределе + имя профиля на пределе — вместе, не порознь."""
    await пройти_мастер(unit="П" * UNIT_NAME_LIMIT, full_name="И" * 200)

    inspection = get_state(CHAT_ID)
    assert inspection is not None
    имя = имя_файла_отчёта(inspection.unit, inspection.auditor, inspection.date)
    assert len(имя.encode("utf-8")) <= 255, "худший достижимый случай не влезает в предел ext4"
