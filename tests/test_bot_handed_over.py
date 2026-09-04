"""T153: сданная проверка не называется незавершённой (#124).

`/start` показывал одну и ту же фразу на два разных положения дел:

    В этом чате есть незавершённая проверка.
    …
    Продолжить её или начать новую? Новая сотрёт эту.

Для проверки в работе это правда. Для проверки, по которой отчёт уже собран и
отправлен, — неправда дважды: она завершена, и стирать в ней нечего. Звучит
это в начале каждой второй проверки за день: аудитор сдал утреннюю и открывает
бота на следующей точке.

У движка признака «завершена» нет, и придумывать его за движок бот не вправе.
Но он вправе помнить **то, что сделал сам**: отчёт по этой проверке он собрал
и отдал аудитору в этот чат. Признак живёт в заметках бота (`bot.json`) рядом
с проверкой — там же, где список кадров и последняя зона, — и обнуляется
вместе с ними на старте новой проверки.

Проверяется настоящим прогоном: отчёт собирается движком, PDF доезжает до
чата, и только после этого спрашивается `/start`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from bot_harness import AUDITOR_ID, CHAT_ID, build_report, feed, make_bot, text_message
from bot_harness import callback_query as callback

from src.bot import sidecar
from src.bot.app import build_dispatcher
from src.bot.config import BotSettings
from src.bot.texts import t
from src.domain import add_finding, start_inspection

# Меток у модуля нет: последний случай синхронный — он смотрит на файл заметок,
# а не на диалог, и метка asyncio на нём была бы неправдой. Боевая методика
# файлу не нужна: он идёт по синтетической через `domain_env` (T141).

SETTINGS = BotSettings(token="unused-in-tests", allowed_ids=frozenset({AUDITOR_ID}), mode="polling")


def начать() -> None:
    start_inspection(
        CHAT_ID, "Белград 2", "Плановая", "ru", date="2026-08-21", auditor="Владимир Гарро"
    )


def записать(code: str = "CLN05", text: str = "Нагар на подине печи") -> None:
    """Запись движком. Пункт — параметром: пара «пункт + зона» занимается один раз."""
    add_finding(CHAT_ID, code=code, level="D1", zone="hot_kitchen", text=text)


async def сдать(bot: object, session: object) -> None:
    """Довести проверку до отданного отчёта — так, как это делает аудитор."""
    dp = build_dispatcher(SETTINGS)
    await feed(dp, bot, text_message("/finish"))  # type: ignore[arg-type]
    await build_report(dp, bot)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_сданная_проверка_не_называется_незавершённой(domain_env: Path) -> None:
    """Главный случай задачи."""
    начать()
    записать()
    bot, session = make_bot()
    await сдать(bot, session)
    assert session.documents, "отчёт не доехал — сдавать было нечего"
    session.clear()

    await feed(build_dispatcher(SETTINGS), bot, text_message("/start"))

    сказанное = session.last_text
    assert "незавершённая" not in сказанное, "сданная проверка названа незавершённой"
    assert "сотрёт" not in сказанное, "боту неизвестно, что стирается что-то нужное"
    assert сказанное == t(
        "start.resume_handed_over",
        "ru",
        unit="Белград 2",
        date="2026-08-21",
        auditor="Владимир Гарро",
        findings=1,
    )


@pytest.mark.asyncio
async def test_выбор_остаётся_за_аудитором(domain_env: Path) -> None:
    """Сданная — не повод решать за человека: обе кнопки на месте.

    Продолжить сданную проверку — законный ход: аудитор мог вспомнить ещё одно
    нарушение и собрать отчёт заново.
    """
    начать()
    записать()
    bot, session = make_bot()
    await сдать(bot, session)
    session.clear()

    await feed(build_dispatcher(SETTINGS), bot, text_message("/start"))

    assert session.keyboard_data() == ["start:resume:continue", "start:resume:new"]


@pytest.mark.asyncio
async def test_проверка_в_работе_по_прежнему_названа_незавершённой(domain_env: Path) -> None:
    """Обратная половина: пока отчёт не сдан, старая фраза — правда."""
    начать()
    записать()
    bot, session = make_bot()

    await feed(build_dispatcher(SETTINGS), bot, text_message("/start"))

    assert session.last_text == t(
        "start.resume_found",
        "ru",
        unit="Белград 2",
        date="2026-08-21",
        auditor="Владимир Гарро",
        findings=1,
    )


@pytest.mark.asyncio
async def test_дописанная_после_сдачи_запись_возвращает_незавершённость(
    domain_env: Path,
) -> None:
    """Признак идёт за делом, а не живёт сам по себе.

    Аудитор продолжил сданную проверку и дописал запись — отчёт, который у него
    на руках, этой записи не содержит. Называть такую проверку сданной значит
    утверждать то, чего уже нет.
    """
    начать()
    записать()
    bot, session = make_bot()
    await сдать(bot, session)
    записать("CLN06", "Нагар на дверце")
    session.clear()

    await feed(build_dispatcher(SETTINGS), bot, text_message("/start"))

    assert "незавершённая" in session.last_text, (
        "после дописанной записи отчёт устарел, а проверка всё ещё зовётся сданной"
    )


@pytest.mark.asyncio
async def test_несобравшийся_отчёт_сданной_проверку_не_делает(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Считается отданный отчёт, а не нажатая кнопка.

    Иначе `/start` объявил бы сданной проверку, отчёта по которой не существует,
    и аудитор спокойно начал бы поверх неё новую.
    """
    from src.report.errors import PdfNotBuilt

    начать()
    записать()

    def отказ(*_a: object, **_k: object) -> Path:
        raise PdfNotBuilt("рендерер недоступен")

    monkeypatch.setattr("src.bot.routers.finish.build_pdf", отказ)
    bot, session = make_bot()
    await сдать(bot, session)
    assert session.documents == [], "отчёт не должен был собраться"
    session.clear()

    await feed(build_dispatcher(SETTINGS), bot, text_message("/start"))

    assert "незавершённая" in session.last_text, "проверка без отчёта названа сданной"


@pytest.mark.asyncio
async def test_новая_проверка_признак_обнуляет(domain_env: Path) -> None:
    """Заметки прошлой проверки к новой не относятся — включая признак сдачи."""
    начать()
    записать()
    bot, session = make_bot()
    await сдать(bot, session)

    dp = build_dispatcher(SETTINGS)
    await feed(dp, bot, text_message("/start"))
    await feed(dp, bot, callback("start:resume:new"))
    await feed(dp, bot, text_message("Белград 1"))
    await feed(dp, bot, callback("start:kind:planned"))
    await feed(dp, bot, callback("start:lang:ru"))
    session.clear()

    await feed(build_dispatcher(SETTINGS), bot, text_message("/start"))

    assert "незавершённая" in session.last_text, "признак сдачи достался новой проверке"


def test_признак_переживает_перезапуск_бота(domain_env: Path) -> None:
    """Заметки лежат файлом рядом с проверкой — иначе признак жил бы до перезапуска."""
    начать()
    записать()
    sidecar.mark_handed_over(CHAT_ID, findings=1)

    заметки = sidecar.read(CHAT_ID)

    assert заметки.handed_over_findings == 1
    assert sidecar.notes_path(CHAT_ID).is_file(), "признак не записан на диск"


@pytest.mark.asyncio
async def test_чистая_проверка_без_записей_не_считается_сданной(domain_env: Path) -> None:
    """Ноль записей — это «ничего не нашли», а не «отчёт отдан».

    Признак хранит число записей на момент сдачи, и умолчание у него не ноль:
    ноль — законное число записей чистой точки. Спутай эти два состояния — и
    только что начатая проверка объявлялась бы сданной с первой секунды.
    """
    начать()
    bot, session = make_bot()

    await feed(build_dispatcher(SETTINGS), bot, text_message("/start"))

    assert "незавершённая" in session.last_text, "только что начатая проверка названа сданной"


@pytest.mark.asyncio
async def test_сданная_чистая_проверка_называется_сданной(domain_env: Path) -> None:
    """Обратная половина того же: у чистой точки отчёт тоже собирается и отдаётся."""
    начать()
    bot, session = make_bot()
    await сдать(bot, session)
    assert session.documents, "отчёт по чистой проверке не доехал"
    session.clear()

    await feed(build_dispatcher(SETTINGS), bot, text_message("/start"))

    assert "незавершённая" not in session.last_text, "сданная чистая проверка названа незавершённой"
