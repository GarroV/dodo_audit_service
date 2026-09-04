"""Информационная часть в конце проверки (T158, задача #131, решения D069, D070).

Требование владельца: после подтверждения о завершении проверки и **до** сборки
отчёта бот спрашивает информационную часть — сильные стороны, зоны роста, отзыв
о тестовом заказе, две пометки «да/нет» и две даты.

Что здесь защищается, по важности:

**Порядок.** «Завершить» → подтверждение → информационная часть → PDF и письмо.
Отчёт, собранный до вопросов, их ответов не содержит — и заметил бы это партнёр,
а не аудитор. Поэтому первый же случай проверяет, что после нажатия «Собрать
отчёт» документа ещё нет, а вопрос уже задан.

**Состав.** Семь полей, и ни одного лишнего: вид проверки задаёт мастер начала
(второй вопрос был бы шагом назад), фотофиксация работает обычным потоком
кадров.

**Способы ввода.** Текст записывается сразу (D064: человек написал сам), голос
показывается расшифровкой ДО записи и правится (D069), «да/нет» отвечается
кнопкой и уезжает в отчёт на языке ОТЧЁТА, кадр принимается — но бот честно
говорит, что в информационную часть попадает только текст.

**Пропуск.** Все поля пропускаемые (допущение D070). Пропущенное не
записывается вовсе — не пустой строкой, а отсутствием ключа: пустая строка в
отчёте выглядела бы как ответ «ничего».

**Ответ доезжает до отчёта.** Срок плана действий, названный аудитором,
подхватывается письмом партнёру вместо расчётного — это и есть доказательство,
что поля не просто записались в файл.

Проверка ведётся через настоящий диспетчер и настоящий движок: `info` пишется
подпроцессом `audit.py info`, состояние читается из файла проверки.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from bot_harness import (
    AUDITOR_ID,
    CHAT_ID,
    feed,
    make_bot,
    photo_message,
    stub_transcribe,
    text_message,
    voice_message,
)
from bot_harness import callback_query as callback

from src.bot.app import build_dispatcher
from src.bot.config import BotSettings
from src.bot.inspection import state_path
from src.bot.texts import t
from src.domain import add_finding, get_item, start_inspection

pytestmark = pytest.mark.asyncio

SETTINGS = BotSettings(token="unused-in-tests", allowed_ids=frozenset({AUDITOR_ID}), mode="polling")

#: Семь полей информационной части (D070). Вида проверки (`INF02`) и
#: фотофиксации (`INF09`–`INF11`) здесь нет намеренно.
ПОЛЯ = ("INF01", "INF03", "INF04", "INF05", "INF06", "INF07", "INF08")


def начать(report_lang: str = "ru", ui_lang: str = "ru") -> None:
    """Проверка с одной записью — чтобы отчёту было что собирать."""
    start_inspection(
        CHAT_ID, "Белград 2", "Плановая", report_lang, ui_lang=ui_lang, speech_lang=ui_lang
    )
    add_finding(CHAT_ID, "CLN05", "D1", "hot_kitchen", "Нагар на подине печи")


def записано() -> dict[str, str]:
    """Блок `info` проверки так, как его видит движок и отчёт."""
    state = json.loads(state_path(CHAT_ID).read_text(encoding="utf-8"))
    return dict(state.get("info") or {})


async def дойти_до_информационной_части(dp: object, bot: object) -> None:
    """«Завершить» и подтверждение — то, после чего начинаются вопросы."""
    await feed(dp, bot, text_message("/finish"))  # type: ignore[arg-type]
    await feed(dp, bot, callback("fin:build"))  # type: ignore[arg-type]


# --- порядок: отчёт собирается ПОСЛЕ вопросов, а не до -----------------------


async def test_отчёт_не_собирается_пока_не_пройдена_информационная_часть(
    domain_env: Path,
) -> None:
    """Главное требование задачи: сначала вопросы, потом документ."""
    начать()
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await дойти_до_информационной_части(dp, bot)

    assert session.documents == [], (
        "PDF собрался до информационной части — ответы в него уже не попадут"
    )
    assert get_item("INF01").question("ru") in "\n".join(session.texts), "первый вопрос не задан"


async def test_после_последнего_вопроса_отчёт_собирается(domain_env: Path) -> None:
    """Обратная сторона: часть пройдена — документ и письмо обязаны прийти."""
    начать()
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await дойти_до_информационной_части(dp, bot)
    for _ in ПОЛЯ[:-1]:
        await feed(dp, bot, callback("info:skip"))
    assert session.documents == [], "отчёт собрался, не дойдя до последнего вопроса"

    await feed(dp, bot, callback("info:skip"))

    assert len(session.documents) == 1, "после информационной части отчёт не собрался"


async def test_дальше_к_отчёту_снимает_оставшиеся_вопросы(domain_env: Path) -> None:
    """Шесть нажатий «Пропустить» подряд — тот же тупик, только длиннее."""
    начать()
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await дойти_до_информационной_части(dp, bot)
    assert session.documents == [], "отчёт собрался до информационной части"

    await feed(dp, bot, callback("info:done"))

    assert len(session.documents) == 1, "отчёт не собрался"
    assert записано() == {}, "пропущенные поля не записываются вовсе (D070)"


async def test_пропущенное_поле_не_записывается_даже_пустым(domain_env: Path) -> None:
    """Пропуск — это отсутствие ключа, а не ответ «ничего» (D070).

    Пустая строка или прочерк в отчёте партнёру читаются как ответ аудитора:
    «сильных сторон нет». Пропущенное поле не печатается вовсе — значит, и
    записываться оно не должно ничем.
    """
    начать()
    bot, _session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await дойти_до_информационной_части(dp, bot)
    await feed(dp, bot, callback("info:skip"))
    await feed(dp, bot, callback("info:yes"))

    assert "INF01" not in записано(), "пропущенное поле всё-таки записалось"
    assert записано()["INF03"] == "Да", "следующий вопрос после пропуска не задан"


# --- состав: семь полей, и ни одного лишнего ---------------------------------


async def test_спрашиваются_ровно_семь_полей_и_записываются_их_коды(domain_env: Path) -> None:
    """Вид проверки и фотофиксация в информационную часть не входят (D070)."""
    начать()
    bot, _session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await дойти_до_информационной_части(dp, bot)
    # Ответ — по виду поля: даты разбираются, остальное записывается словами.
    # Тот же список, что видит аудитор, и в том же порядке.
    for ответ in ("сильные", "да", "нет", "14.09.2026 18:30", "зоны роста", "30.09.2026", "отзыв"):
        await feed(dp, bot, text_message(ответ))

    assert tuple(записано()) == ПОЛЯ, "спрошено не то и не в том порядке"


# --- способы ввода ------------------------------------------------------------


async def test_текст_записывается_сразу_без_подтверждения(domain_env: Path) -> None:
    """D064: человек написал сам — подтверждать ему нечего."""
    начать()
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await дойти_до_информационной_части(dp, bot)
    await feed(dp, bot, text_message("Смена собранная, стандарты держат"))

    assert записано()["INF01"] == "Смена собранная, стандарты держат"
    assert t("info.saved", "ru", value="Смена собранная, стандарты держат") in session.texts


async def test_да_нет_кнопкой_уезжает_на_языке_отчёта(domain_env: Path) -> None:
    """Значение читает партнёр, а не аудитор: язык отчёта, а не интерфейса."""
    начать(report_lang="en", ui_lang="ru")
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await дойти_до_информационной_части(dp, bot)
    await feed(dp, bot, callback("info:skip"))  # INF01 — свободный текст
    assert "info:yes" in session.keyboard_data(), "у поля «да/нет» нет кнопок"

    await feed(dp, bot, callback("info:yes"))

    assert записано()["INF03"] == "Yes", "в отчёт уехало «Да» вместо языка отчёта"


async def test_голос_показывается_расшифровкой_до_записи(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D069: расшифровка показывается ДО записи, и её можно поправить."""
    начать()
    stub_transcribe(monkeypatch, "коллектив держит стандарт")
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await дойти_до_информационной_части(dp, bot)
    await feed(dp, bot, voice_message("voice-1"))

    assert "коллектив держит стандарт" in session.last_text, "услышанное не показано"
    assert записано() == {}, "расшифровка записана до того, как аудитор её увидел"
    assert "info:save" in session.keyboard_data()

    await feed(dp, bot, callback("info:save"))
    assert записано()["INF01"] == "коллектив держит стандарт"


async def test_расшифровку_можно_поправить_текстом(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Бот ослышался — присланный текст заменяет услышанное, а не добавляется к нему."""
    начать()
    stub_transcribe(monkeypatch, "коллектив держит стандартный")
    bot, _session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await дойти_до_информационной_части(dp, bot)
    await feed(dp, bot, voice_message("voice-1"))
    await feed(dp, bot, text_message("коллектив держит стандарт"))

    assert записано()["INF01"] == "коллектив держит стандарт"


async def test_кадр_принят_но_печатается_только_текст(domain_env: Path) -> None:
    """Врать про кадр нельзя: в информационную часть отчёта уходит строка."""
    начать()
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await дойти_до_информационной_части(dp, bot)
    await feed(dp, bot, photo_message("info-frame", caption="Витрина собрана как надо"))

    assert t("info.photo_only_text", "ru") in session.texts, "бот промолчал про судьбу кадра"
    assert записано()["INF01"] == "Витрина собрана как надо", "подпись потеряна"


# --- даты ---------------------------------------------------------------------


async def test_дата_со_временем_записывается_разобранной(domain_env: Path) -> None:
    """`INF05` спрашивает дату И время созвона — терять половину ответа нельзя."""
    начать()
    bot, _session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await дойти_до_информационной_части(dp, bot)
    await feed(dp, bot, callback("info:skip"))  # INF01
    await feed(dp, bot, callback("info:skip"))  # INF03
    await feed(dp, bot, callback("info:skip"))  # INF04
    await feed(dp, bot, text_message("14.09.2026 18:30"))

    assert записано()["INF05"] == "14.09.2026 18:30"


async def test_неразобранная_дата_не_записывается(domain_env: Path) -> None:
    """«Завтра после обеда» в поле, которое письмо читает сроком, попасть не должно."""
    начать()
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await дойти_до_информационной_части(dp, bot)
    for _ in range(3):
        await feed(dp, bot, callback("info:skip"))
    await feed(dp, bot, text_message("завтра после обеда"))

    assert записано() == {}, "неразобранная дата записалась"
    assert t("info.bad_date", "ru", text="завтра после обеда") == session.last_text


async def test_срок_плана_действий_подхватывается_письмом(domain_env: Path) -> None:
    """Ответ доезжает до партнёра: письмо берёт срок аудитора вместо расчётного.

    Запись класса `D2` здесь обязательна: на проверке без D2 и D3 письмо
    собирается по шаблону «плана действий не ждём», и срока в нём нет вовсе —
    проверять было бы нечего.
    """
    начать()
    add_finding(CHAT_ID, "TEH22", "D2", "hot_kitchen", "Смеситель течёт")
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await дойти_до_информационной_части(dp, bot)
    for _ in range(5):
        await feed(dp, bot, callback("info:skip"))  # INF01, INF03, INF04, INF05, INF06
    await feed(dp, bot, text_message("30.09.2026"))  # INF07 — срок плана действий
    await feed(dp, bot, callback("info:done"))

    assert записано()["INF07"] == "30.09.2026"
    письмо = session.last_text
    assert "30.09.2026" in письмо, "срок аудитора не доехал до письма партнёру"


# --- краевые -------------------------------------------------------------------


async def test_пересборка_без_кадров_не_спрашивает_часть_заново(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Информационная часть уже пройдена — второй раз её задавать незачем."""
    начать()
    from src.domain import attach_photo

    attach_photo(CHAT_ID, 1, "gone-frame")

    async def ничего(*_a: object, **_k: object) -> dict[str, str]:
        return {}

    monkeypatch.setattr("src.bot.routers.finish.download_all", ничего)
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await дойти_до_информационной_части(dp, bot)
    await feed(dp, bot, callback("info:done"))
    assert session.keyboard_data() == ["fin:nophoto"], "сборка не упёрлась в потерянный кадр"
    session.clear()

    await feed(dp, bot, callback("fin:nophoto"))

    вопросы = [text for text in session.texts if t("info.intro", "ru") in text]
    assert вопросы == [], "информационная часть спрошена второй раз"
    assert len(session.documents) == 1, "отчёт не собрался"


async def test_ответ_в_информационной_части_не_уходит_в_разбор(domain_env: Path) -> None:
    """Текст ответа — это ответ, а не комментарий к кадру: разбор его не видит."""
    начать()
    bot, _session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await дойти_до_информационной_части(dp, bot)
    await feed(dp, bot, text_message("печь грязная"))

    assert записано()["INF01"] == "печь грязная", "ответ уехал не туда"
    state = json.loads(state_path(CHAT_ID).read_text(encoding="utf-8"))
    assert len(state["findings"]) == 1, "ответ на вопрос стал записью нарушения"
