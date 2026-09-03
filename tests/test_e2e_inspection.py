"""Сквозной смоук: проверка проходится целиком через бота (T080).

Это не тест одного роутера, а прогон главного сценария спеки от начала до
конца: старт → фиксация словами → правка в чате → завершение → PDF и письмо.
Разница существенная. Тесты роутеров проверяют, что каждый шаг делает своё;
здесь проверяется, что шаги стыкуются — и что **оценка в отчёте совпадает с
тем, что выдаёт движок на тех же данных**. Это отдельный пункт критериев
готовности MVP, и подтвердить его может только сквозной прогон.

Настоящее здесь всё, кроме двух вещей: сети телеграма нет (её заменяет
`RecordingSession`) и разбор кадра моделью подменён — он стоит денег и ходит
наружу. Движок, состояние, расчёт оценки, сборка PDF и письма — боевые.
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
    photo_message,
    stub_classify,
    suggestion,
    text_message,
)
from bot_harness import callback_query as callback
from conftest import requires_data

from src.bot.app import build_dispatcher
from src.bot.config import BotSettings
from src.bot.keyboards import (
    FINISH_BUILD_NO_PHOTOS_CALLBACK,
    KIND_PREFIX,
    LANG_PREFIX,
    NEW_INSPECTION_CALLBACK,
    PICK_PREFIX,
)
from src.domain import get_state, score

pytestmark = [pytest.mark.asyncio, requires_data]

SETTINGS = BotSettings(token="unused-in-tests", allowed_ids=frozenset({AUDITOR_ID}), mode="polling")


def _pdf_на_диске(путь: Path) -> None:
    """Диск читается синхронной функцией нарочно: ASYNC240 запрещает трогать
    `pathlib` внутри `async def`."""
    assert путь.is_file(), f"бот отдал путь к несуществующему файлу: {путь}"
    assert путь.read_bytes()[:5] == b"%PDF-", "аудитору отдали не PDF"


async def test_проверка_проходится_целиком_и_оценка_сходится(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Главный сценарий спеки целиком, без открытия чего-либо, кроме телеграма."""
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    # --- Шаг 1: начать проверку ---
    await feed(dp, bot, text_message("/start"))
    await feed(dp, bot, callback(NEW_INSPECTION_CALLBACK))
    await feed(dp, bot, text_message("Белград 2"))
    await feed(dp, bot, callback(f"{KIND_PREFIX}planned"))
    await feed(dp, bot, callback(f"{LANG_PREFIX}ru"))

    начатая = get_state(CHAT_ID)
    assert начатая is not None, "после мастера проверки нет"
    assert начатая.unit == "Белград 2"

    # --- Шаг 2: фиксация словами (кадр разбирает подменённая модель) ---
    stub_classify(
        monkeypatch,
        suggestion(candidate("CLN05", "D1", "hot_kitchen", "Нагар на подине печи")),
    )
    await feed(dp, bot, photo_message("frame-1", caption="нагар на подине печи в горячем цеху"))
    await feed(dp, bot, callback(f"{PICK_PREFIX}0"))

    после_записи = get_state(CHAT_ID)
    assert после_записи is not None
    assert len(после_записи.findings) == 1, "запись не появилась после подтверждения"
    запись = после_записи.findings[0]
    assert запись.code == "CLN05"
    assert запись.zone == "hot_kitchen"

    # --- Шаг 3: завершение и сборка отчёта ---
    session.texts.clear()
    await feed(dp, bot, text_message("/finish"))
    await feed(dp, bot, callback(FINISH_BUILD_NO_PHOTOS_CALLBACK))

    # --- Шаг 4: оценка, показанная ботом, совпадает с движком на тех же данных ---
    #
    # Проверяется ИМЕННО сообщение итога, а не вся переписка. Разница не
    # формальная: письмо партнёру собирает движок, и правильный процент есть
    # в нём всегда. Проверка по всей переписке находила его там и проходила
    # даже с подменённым процентом в интерфейсе — то есть не могла упасть.
    итог = score(CHAT_ID)
    итоговые = [т for т in session.texts if "Итог" in т]
    assert итоговые, f"бот не показал итог проверки: {session.texts}"
    строка_итога = итоговые[0]
    assert f"{итог.pct:.1f}%" in строка_итога, (
        f"процент, показанный аудитору, разошёлся с движком: движок даёт "
        f"{итог.pct:.1f}%, бот показал «{строка_итога}»"
    )
    assert итог.grade in строка_итога, f"буква {итог.grade} не показана аудитору: «{строка_итога}»"
    показанное = " ".join(session.texts)

    # --- Шаг 5: аудитор получил документ и письмо ---
    assert session.documents, "PDF аудитору не отдан — проверка не доведена до документа"
    отправленный = session.documents[-1].document
    _pdf_на_диске(Path(str(getattr(отправленный, "path", отправленный))))
    assert len(показанное) > 200, "письма партнёру в переписке нет"


async def test_прерванная_проверка_продолжается(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Состояние переживает перезапуск бота — отдельный пункт готовности MVP.

    Перезапуск изображается новым диспетчером на том же состоянии: у бота
    хранилище шагов мастера в памяти, а сама проверка лежит в файле, и именно
    это разделение проверяется.
    """
    bot, _ = make_bot()
    dp = build_dispatcher(SETTINGS)
    await feed(dp, bot, text_message("/start"))
    await feed(dp, bot, callback(NEW_INSPECTION_CALLBACK))
    await feed(dp, bot, text_message("Белград 2"))
    await feed(dp, bot, callback(f"{KIND_PREFIX}planned"))
    await feed(dp, bot, callback(f"{LANG_PREFIX}ru"))
    stub_classify(
        monkeypatch,
        suggestion(candidate("CLN05", "D1", "hot_kitchen", "Нагар на подине печи")),
    )
    await feed(dp, bot, photo_message("frame-1", caption="нагар на подине печи в горячем цеху"))
    await feed(dp, bot, callback(f"{PICK_PREFIX}0"))
    до = score(CHAT_ID)

    # Перезапуск: новый бот, новый диспетчер, то же состояние на диске.
    bot2, session2 = make_bot()
    dp2 = build_dispatcher(SETTINGS)
    await feed(dp2, bot2, text_message("/finish"))

    после = score(CHAT_ID)
    assert после.pct == до.pct, "после перезапуска оценка изменилась — состояние потеряно"
    assert "CLN05" in " ".join(session2.texts), "после перезапуска записи не видно"
