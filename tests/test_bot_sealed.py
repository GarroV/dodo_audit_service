"""Сданная проверка не правится (T201, решение владельца D080).

Владелец, дословно: «отчеты мы не правим. в будущем доабвим функционал, сейчас
мы можем только удалить его, или завести новый».

Отчёт по проверке собран, отдан аудитору и уехал в историю точки. С этого
момента проверка запечатана: дописать в неё находку нельзя, поправить
записанное — тоже. Бот же до этой задачи сам предлагал «Продолжить её — можно
дописать и собрать отчёт заново».

**Почему это не про удобство.** История точки узнаёт проверку по слепку
содержимого. Дописанная после сдачи запись меняет содержимое, слепок больше не
совпадает — и тот же обход ложится в историю ВТОРОЙ строкой, а разделить их
потом нечем: завершённая проверка в базе не правится и не удаляется.

**Запрет закрывает все входы, а не только кнопку.** Кнопку «Продолжить» аудитор
на сданной проверке больше не видит, но материал он присылает и без неё —
кадром, комментарием, нажатием под старой записью, ответом на сообщение бота.
Каждый из этих путей меняет проверку, и закрыт должен быть каждый: закрытая
кнопка при открытом кадре была бы не запретом, а его видимостью.

**Отказ обязан объяснять.** Аудитор на точке, ему нужен выход, а не «нельзя»:
поэтому рядом с отказом стоит, почему так и что делать — начать новую проверку
или убрать сданную из чата.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from bot_harness import (
    AUDITOR_ID,
    CHAT_ID,
    bot_message,
    build_report,
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
from src.bot.keyboards import (
    EDIT_DROP,
    EDIT_PREFIX,
    NEW_INSPECTION_CALLBACK,
    PICK_PREFIX,
    RESUME_CONTINUE_CALLBACK,
    RESUME_NEW_CALLBACK,
    SEALED_DROP_CALLBACK,
)
from src.bot.texts import t
from src.domain import Finding, add_finding, get_state, start_inspection

pytestmark = pytest.mark.asyncio

SETTINGS = BotSettings(token="unused-in-tests", allowed_ids=frozenset({AUDITOR_ID}), mode="polling")


def начать() -> None:
    start_inspection(
        CHAT_ID, "Белград 2", "planned", "ru", date="2026-08-21", auditor="Владимир Гарро"
    )


def записать(code: str = "CLN05", text: str = "Нагар на подине печи") -> Finding:
    return add_finding(CHAT_ID, code=code, level="D1", zone="hot_kitchen", text=text)


def записи() -> list[Finding]:
    state = get_state(CHAT_ID)
    return [] if state is None else list(state.findings)


async def сдать(bot: object, session: object) -> None:
    """Довести проверку до отданного отчёта — так, как это делает аудитор."""
    dp = build_dispatcher(SETTINGS)
    await feed(dp, bot, text_message("/finish"))  # type: ignore[arg-type]
    await build_report(dp, bot)  # type: ignore[arg-type]
    assert session.documents, "отчёт не доехал — сдавать было нечего"  # type: ignore[attr-defined]


async def сданная() -> tuple[object, object, object]:
    """Чат со сданной проверкой: бот, сессия и свежий диспетчер."""
    начать()
    записать()
    bot, session = make_bot()
    await сдать(bot, session)
    session.clear()
    return bot, session, build_dispatcher(SETTINGS)


async def test_продолжить_сданную_больше_не_предлагается(domain_env: Path) -> None:
    """Главное требование D080: кнопки «Продолжить» на сданной проверке нет."""
    bot, session, dp = await сданная()

    await feed(dp, bot, text_message("/start"))

    assert RESUME_CONTINUE_CALLBACK not in session.keyboard_data(), (
        "бот снова предлагает дописать в сданную проверку"
    )
    assert session.keyboard_data() == [RESUME_NEW_CALLBACK, SEALED_DROP_CALLBACK]


async def test_отказ_объясняет_причину_и_называет_выход(domain_env: Path) -> None:
    """«Нельзя» без причины человек на точке читает как поломку продукта."""
    bot, session, dp = await сданная()

    await feed(dp, bot, text_message("/start"))

    сказанное = session.last_text
    assert "сдана" in сказанное
    assert "дописать" in сказанное.lower(), "не сказано, чего именно нельзя"
    assert "истории" in сказанное, "не сказано, что отданный отчёт остаётся у получателя"


async def test_устаревшая_кнопка_продолжить_не_стреляет(domain_env: Path) -> None:
    """Кнопка из вчерашней переписки живёт вечно — нажатие обязано упереться в тот же запрет."""
    bot, session, dp = await сданная()

    await feed(dp, bot, callback(RESUME_CONTINUE_CALLBACK))

    assert t("start.resumed", "ru", unit="Белград 2", date="2026-08-21", findings=1) not in (
        session.texts
    ), "старая кнопка продолжила сданную проверку"
    assert "сдана" in session.last_text


async def test_кадр_с_комментарием_в_сданную_не_ложится_записью(domain_env: Path) -> None:
    """Кадр — главный вход, и он же оставался открытым: кнопка не единственный путь."""
    bot, session, dp = await сданная()
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")

    await feed(dp, bot, photo_message("frame-late", caption="печь грязная"))

    assert len(записи()) == 1, "в сданную проверку дописалась запись"
    assert "сдана" in session.last_text


async def test_правка_кнопкой_в_сданной_не_проходит(domain_env: Path) -> None:
    """Правка записанного — тоже правка отчёта: он у получателя, и в нём другое."""
    bot, session, dp = await сданная()

    await feed(dp, bot, callback(f"{EDIT_PREFIX}1:{EDIT_DROP}"))

    assert len(записи()) == 1, "из сданной проверки удалилась запись"
    assert "сдана" in session.last_text


async def test_правка_ответом_в_сданной_не_проходит(domain_env: Path) -> None:
    """Тот же запрет и на правку ответом на сообщение бота (T204)."""
    начать()
    finding = записать()
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    # Сообщение о записи, на которое аудитор потом ответит: карта ведётся
    # показом, поэтому здесь она наполняется тем же способом, что в продукте.
    await feed(dp, bot, text_message("/records"))
    sidecar.remember_record(CHAT_ID, 9999, finding.n)
    await сдать(bot, session)
    session.clear()

    await feed(
        build_dispatcher(SETTINGS),
        bot,
        text_message("посудный участок, раковина и смеситель грязные", reply_to=bot_message(9999)),
    )

    assert [(f.code, f.zone) for f in записи()] == [("CLN05", "hot_kitchen")], (
        "запись сданной проверки поправлена ответом"
    )
    assert "сдана" in session.last_text


async def test_undo_в_сданной_не_проходит(domain_env: Path) -> None:
    """`/undo` снимает последнюю запись — в сданной проверке снимать нечего."""
    bot, session, dp = await сданная()

    await feed(dp, bot, text_message("/undo"))

    assert len(записи()) == 1
    assert "сдана" in session.last_text


async def test_нажатие_под_старым_предложением_после_сдачи_не_записывает(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Кнопка показана ДО сдачи, нажата ПОСЛЕ — вход, которого не видно с порога.

    Предложение модели живёт в памяти процесса, а сообщение с кнопками — в
    переписке. Аудитор успевает сдать отчёт и вернуться к нему; проверка на
    входе в бота такое нажатие не ловит, поэтому запрет стоит и у самой записи.
    """
    начать()
    записать()
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    stub_classify(monkeypatch, suggestion(candidate("CLN06", "D1", "hot_kitchen")))
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")
    await feed(dp, bot, photo_message("frame-x", caption="что-то не так с мебелью"))
    assert session.keyboard_data(), "предложение не показано — нажимать нечего"
    await сдать(bot, session)
    session.clear()

    await feed(dp, bot, callback(f"{PICK_PREFIX}0"))

    assert len(записи()) == 1, "нажатие под старым предложением дописало запись в сданную"
    assert "сдана" in session.last_text


async def test_убрать_из_чата_не_трогает_проверку_в_работе(domain_env: Path) -> None:
    """Кнопка из старой переписки не должна стирать незавершённый обход.

    Это та же защита, ради которой существует T052: потерять зафиксированное
    одним нажатием хуже, чем не дать нажать.
    """
    начать()
    записать()
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, callback(SEALED_DROP_CALLBACK))

    assert get_state(CHAT_ID) is not None, "нажатие стёрло проверку, по которой отчёт не сдан"
    assert len(записи()) == 1
    assert session.last_text == t("sealed.drop_gone", "ru")


async def test_убрать_из_чата_освобождает_чат(domain_env: Path) -> None:
    """Второй выход, названный владельцем: удалить.

    Убирается копия в чате — та, которой аудитор мешает начать новую проверку.
    Отданный отчёт и строка в истории точки остаются: удалять их бот не умеет,
    и сказать об этом обязан прямо, а не молчанием.
    """
    bot, session, dp = await сданная()

    await feed(dp, bot, callback(SEALED_DROP_CALLBACK))

    assert get_state(CHAT_ID) is None, "проверка осталась в чате"
    assert sidecar.read(CHAT_ID).frames == (), "заметки прошлой проверки пережили удаление"
    assert NEW_INSPECTION_CALLBACK in session.keyboard_data(), "аудитор остался без выхода"


async def test_после_удаления_начинается_новая_проверка(domain_env: Path) -> None:
    """Удаление обязано быть полным: следующая проверка начинается с чистого листа."""
    bot, session, dp = await сданная()
    await feed(dp, bot, callback(SEALED_DROP_CALLBACK))
    session.clear()

    await feed(dp, bot, text_message("/start"))

    assert session.last_text == t("start.greeting", "ru")
    assert session.keyboard_data() == [NEW_INSPECTION_CALLBACK]


async def test_проверка_в_работе_запретом_не_задета(domain_env: Path) -> None:
    """Сторож: пока отчёт не сдан, всё работает как работало."""
    начать()
    записать()
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    sidecar.remember_zone(CHAT_ID, "dining")

    await feed(dp, bot, photo_message("frame-live", caption="урна в зале переполнена"))

    assert len(записи()) == 2, "запрет задел проверку, по которой отчёт не сдавали"
    await feed(dp, bot, text_message("/start"))
    assert RESUME_CONTINUE_CALLBACK in session.keyboard_data()
