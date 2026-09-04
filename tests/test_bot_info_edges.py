"""Информационная часть: разбор дат и краевые случаи (T158, D069, D070).

Отдельным файлом от `test_bot_info_part.py`: там сценарий, который проходит
аудитор, здесь — то, что ломается по дороге. Разбор даты вынесен в юнит-тесты
намеренно: он единственное место части, где бот трактует сказанное, а не
записывает как есть, и цена ошибки видна не в чате, а в письме партнёру —
`INF07` уезжает туда сроком плана действий.
"""

from __future__ import annotations

from datetime import date
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

from src.bot import sidecar
from src.bot.app import build_dispatcher
from src.bot.config import BotSettings
from src.bot.info import FIELDS, InfoField, fields_to_ask, parse_date, question
from src.bot.texts import t
from src.domain import add_finding, set_info, start_inspection
from src.domain.errors import ValidationError
from src.recognize.errors import TranscriptionFailed

pytestmark = pytest.mark.asyncio

SETTINGS = BotSettings(token="unused-in-tests", allowed_ids=frozenset({AUDITOR_ID}), mode="polling")


def начать() -> None:
    start_inspection(CHAT_ID, "Белград 2", "planned", "ru")
    add_finding(CHAT_ID, "CLN05", "D1", "hot_kitchen", "Нагар на подине печи")


async def дойти(dp: object, bot: object) -> None:
    await feed(dp, bot, text_message("/finish"))  # type: ignore[arg-type]
    await feed(dp, bot, callback("fin:build"))  # type: ignore[arg-type]


# --- разбор даты ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("сказано", "записано"),
    [
        ("14.09.2026", "14.09.2026"),
        ("14-09-2026", "14.09.2026"),
        ("14/09/2026", "14.09.2026"),
        ("2026-09-14", "14.09.2026"),
        ("14.09.26", "14.09.2026"),
        ("14.09.2026 18:30", "14.09.2026 18:30"),
        ("14.09.2026 в 9:05", "14.09.2026 09:05"),
    ],
)
async def test_дата_разбирается_как_её_пишут_люди(сказано: str, записано: str) -> None:
    """Форматы взяты те, которыми дату пишут в чате, а не те, что удобны коду."""
    assert parse_date(сказано, today=date(2026, 9, 4)) == записано


@pytest.mark.parametrize(
    "сказано",
    [
        "завтра после обеда",
        "",
        "31.02.2026",
        "14.45.2026",
        "созвон был",
    ],
)
async def test_не_дата_не_записывается(сказано: str) -> None:
    """«Завтра после обеда» в поле, которое письмо читает сроком, попасть не должно."""
    assert parse_date(сказано, today=date(2026, 9, 4)) is None


# --- состав полей задаёт методика, а не код ------------------------------------


async def test_поля_без_пункта_в_методике_не_спрашиваются(domain_env: Path) -> None:
    """Состав информационной части — данные управляющей компании, не код.

    `INF02` (вид проверки) в списке полей нет вовсе, а несуществующего пункта
    бот не спрашивает: методика без него означает, что такого вопроса в ней не
    задают, — тот же урок, что D068 и D075 про необязательные файлы.
    """
    assert "INF02" not in [f.code for f in FIELDS], "вид проверки спрашивается второй раз (D070)"
    assert question(InfoField("INF99", "text"), "ru") is None
    спрошены = [field.code for field, _ in fields_to_ask("ru")]
    assert спрошены == [f.code for f in FIELDS]


# --- краевые случаи разговора ---------------------------------------------------


async def test_сбой_расшифровки_не_роняет_часть(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Голос не распознался — сказать и остаться на вопросе, а не проглотить."""
    начать()
    stub_transcribe(monkeypatch, TranscriptionFailed("голосовое пустое"))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await дойти(dp, bot)
    await feed(dp, bot, voice_message("voice-1"))

    assert "голосовое пустое" in session.last_text
    assert session.documents == [], "сбой расшифровки не должен обрывать часть сборкой отчёта"

    # Разговор жив: тем же вопросом можно ответить текстом.
    await feed(dp, bot, text_message("смена собранная"))
    assert t("info.saved", "ru", value="смена собранная") in session.texts


async def test_кадр_без_подписи_не_теряется(domain_env: Path) -> None:
    """Присланное не исчезает молча: кадр попадает в заметки бота (T068)."""
    начать()
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await дойти(dp, bot)
    await feed(dp, bot, photo_message("info-frame", message_id=808))

    assert t("info.photo_taken", "ru") == session.last_text
    кадры = [frame.file_id for frame in sidecar.read(CHAT_ID).frames]
    assert "info-frame" in кадры, "кадр из информационной части потерялся"


async def test_записать_так_без_расшифровки_не_роняет_разговор(domain_env: Path) -> None:
    """Кнопка из старого сообщения: причина не называется наугад (T128), вопрос повторяется."""
    начать()
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await дойти(dp, bot)
    session.clear()
    await feed(dp, bot, callback("info:save"))

    assert session.texts, "бот промолчал на нажатие без расшифровки"
    assert session.documents == [], "нажатие без расшифровки собрало отчёт"


async def test_отказ_записи_оставляет_на_том_же_вопросе(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Не записалось — сказать и дать повторить, а не пойти дальше молча.

    Молчаливый переход к следующему вопросу означал бы отчёт без поля, которое
    аудитор считает записанным, — и узнал бы об этом партнёр.
    """
    начать()

    def отказ(*_a: object, **_k: object) -> None:
        from src.domain.errors import EngineError

        raise EngineError("движок не принял", code=1, command="info")

    monkeypatch.setattr("src.bot.routers.info.domain.set_info", отказ)
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await дойти(dp, bot)
    await feed(dp, bot, text_message("сильные стороны"))

    assert session.last_text == t("info.not_saved", "ru")
    assert session.documents == [], "после отказа записи часть поехала дальше к отчёту"


async def test_без_начатой_проверки_часть_не_начинается(domain_env: Path) -> None:
    """Собирать нечего — и спрашивать не о чем."""
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, callback("fin:build"))

    assert session.last_text == t("material.no_inspection", "ru")
    assert session.documents == []


async def test_методика_без_информационных_пунктов_сразу_собирает_отчёт(
    domain_env: Path, data_copy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Методика без `INF*` — законная методика, а не отказ в руках аудитора.

    Тот же урок, что D068 и D075: недостающие необязательные данные превращают
    шаг в пропущенный. Здесь это значит, что порядок возвращается к прежнему —
    подтверждение сразу даёт отчёт, потому что спрашивать нечего.
    """
    чеклист = data_copy / "checklist.csv"
    строки = [
        line
        for line in чеклист.read_text(encoding="utf-8").splitlines()
        if not line.startswith(("INF01", "INF03", "INF04", "INF05", "INF06", "INF07", "INF08"))
    ]
    чеклист.write_text("\n".join(строки) + "\n", encoding="utf-8")
    monkeypatch.setenv("AUDIT_DATA_DIR", str(data_copy))
    начать()
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await дойти(dp, bot)

    assert t("info.intro", "ru") not in session.texts, "спрошена часть, которой в методике нет"
    assert len(session.documents) == 1, "отчёт не собрался, хотя спрашивать было нечего"


async def test_пустое_поле_движку_не_отдаётся(domain_env: Path) -> None:
    """Пропуск — это отсутствие вызова, а не вызов с пустой строкой (D070).

    Проверяется на самой функции блока `domain`: она и есть то место, где
    пустота обязана остановиться, — иначе пустая строка уедет в отчёт полем
    без ответа.
    """
    начать()
    with pytest.raises(ValidationError, match="INF01"):
        set_info(CHAT_ID, "INF01", "   ")
    with pytest.raises(ValidationError):
        set_info(CHAT_ID, "  ", "сильные стороны")
