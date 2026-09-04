"""T173: занятая пара «пункт + зона» видна и в РУЧНОМ перечне.

В перечне предложений модели такой пункт помечен с T137: он приходит туда
штатно — сверка со списком нарушений упёрлась в отказ движка, материал ушёл
модели, потому что на кадре бывает второе нарушение, — и непомеченным
неотличим от остальных. Нажатие даёт второй отказ подряд по поводу, о котором
бот только что сказал сам.

В ручном перечне пометки не было, и причина названа в контракте блока честно:
на кнопку отведено 34 знака, и пометка съела бы формулировку пункта. Это
отдельный разговор про ВИД перечня, а не дефект T137.

Решение и его защита — здесь. Пометка уезжает в ТЕКСТ сообщения над
клавиатурой: у ручного перечня это сообщение не несёт ничего, кроме номера
страницы, тогда как у перечня модели там стоит сам список. Кнопка при этом не
трогается вовсе — ни одним знаком, — а связывает строку с кнопкой КОД пункта,
который на кнопке и так стоит первым.

Что здесь защищается:

* пометка появляется и называет номер занявшей записи — тот же, который назовёт
  отказ движка, иначе аудитор пойдёт править не ту запись;
* формулировка на кнопке не пострадала ни на знак — ради этого всё и затевалось;
* помечается ПАРА, а не код: тот же пункт в другой зоне — законная запись;
* пометка говорит только о пунктах ЭТОЙ страницы: строка про пункт, которого на
  экране нет, — шум, а на девяти страницах она была бы длиннее самой страницы;
* пункт остаётся в перечне и остаётся нажимаемым: убрать его значило бы решить
  за человека и спрятать часть методики.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from bot_harness import (
    AUDITOR_ID,
    CHAT_ID,
    feed,
    make_bot,
    manual,
    photo_message,
    stub_classify,
    stub_manual,
    suggestion,
)
from bot_harness import callback_query as callback

from src.bot import sidecar
from src.bot.app import build_dispatcher
from src.bot.config import BotSettings
from src.bot.keyboards import MANUAL_PAGE_SIZE
from src.bot.texts import t
from src.domain import add_finding, start_inspection

pytestmark = pytest.mark.asyncio

SETTINGS = BotSettings(token="unused-in-tests", allowed_ids=frozenset({AUDITOR_ID}), mode="polling")

#: Комментарий, по которому модель не отвечает ничего, — тогда открывается
#: ручной перечень. Однозначным его делать нельзя: быстрый путь записал бы
#: пункт сам, и до перечня разговор не дошёл бы.
НЕЯСНО = "печь, посмотри что тут"

#: Перечень зоны: первым пунктом тот, который уже занят записью.
ПЕРЕЧЕНЬ = (
    manual("CLN05", ("D1",), "Загрязнение оборудования"),
    manual("CLN02", ("D1",), "Оборудование в зоне мойки"),
)


def начата() -> None:
    start_inspection(CHAT_ID, "Белград 2", "planned", "ru", date="2026-09-04", auditor="Гарро")


async def открыть_перечень(dp: object, bot: object) -> None:
    """Дойти до ручного перечня так, как до него доходит аудитор."""
    await feed(dp, bot, photo_message("frame-1", caption=НЕЯСНО))  # type: ignore[arg-type]


async def test_занятый_пункт_назван_в_тексте_страницы(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Главное требование: нажатие на такой пункт даст отказ, и сказать об этом надо до нажатия."""
    начата()
    add_finding(CHAT_ID, "CLN05", "D1", "hot_kitchen", "Нагар на подине печи")
    stub_classify(monkeypatch, suggestion())
    stub_manual(monkeypatch, ПЕРЕЧЕНЬ)
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await открыть_перечень(dp, bot)

    assert "CLN05" in session.last_text, f"занятый пункт не назван: {session.last_text!r}"
    assert "#1" in session.last_text, "номер занявшей записи не назван — править пойдут вслепую"


async def test_формулировка_на_кнопке_не_пострадала(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """То, ради чего пометка вынесена в текст: надписи кнопок обязаны совпасть до знака."""
    начата()
    stub_classify(monkeypatch, suggestion())
    stub_manual(monkeypatch, ПЕРЕЧЕНЬ)
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    ожидается = [f"{item.code} · {item.title}" for item in ПЕРЕЧЕНЬ]

    await открыть_перечень(dp, bot)

    assert session.keyboard_texts()[: len(ожидается)] == ожидается, (
        f"надпись кнопки не равна «код · формулировка»: {session.keyboard_texts()}"
    )
    без_пометки = session.keyboard_texts()

    add_finding(CHAT_ID, "CLN05", "D1", "hot_kitchen", "Нагар на подине печи")
    session.clear()
    await feed(dp, bot, callback("rec:manual"))

    assert session.keyboard_texts() == без_пометки, "пометка залезла в кнопку и съела формулировку"


async def test_тот_же_пункт_в_другой_зоне_не_помечается(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Помечается ПАРА: тот же пункт в другой зоне — законная и частая запись."""
    начата()
    add_finding(CHAT_ID, "CLN05", "D1", "dining", "Загрязнение в зале")
    stub_classify(monkeypatch, suggestion())
    stub_manual(monkeypatch, ПЕРЕЧЕНЬ)
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await открыть_перечень(dp, bot)

    assert session.last_text == t("record.manual_page", "ru", page=1, pages=1), (
        f"страница перечня не осталась чистой — пункт помечен занятым в чужой зоне, "
        f"и аудитора отговаривают от верного действия: {session.last_text!r}"
    )


async def test_без_занятых_пунктов_пометки_нет_вовсе(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Встречное утверждение: пустая пометка на каждой странице — это шум."""
    начата()
    stub_classify(monkeypatch, suggestion())
    stub_manual(monkeypatch, ПЕРЕЧЕНЬ)
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await открыть_перечень(dp, bot)

    assert session.last_text == t("record.manual_page", "ru", page=1, pages=1), (
        f"на странице без занятых пунктов появилось лишнее: {session.last_text!r}"
    )


async def test_пометка_только_про_пункты_этой_страницы(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Строка про пункт, которого на экране нет, — шум и повод искать не то."""
    начата()
    перечень = tuple(
        manual(f"CLN{n:02d}", ("D1",), f"пункт {n}") for n in range(1, MANUAL_PAGE_SIZE + 3)
    )
    # Занят пункт, который стоит на ВТОРОЙ странице.
    занятый = перечень[MANUAL_PAGE_SIZE].code
    add_finding(CHAT_ID, занятый, "D1", "hot_kitchen", "Запись со второй страницы")
    stub_classify(monkeypatch, suggestion())
    stub_manual(monkeypatch, перечень)
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await открыть_перечень(dp, bot)

    assert занятый not in session.last_text, (
        f"первая страница говорит о пункте со второй: {session.last_text!r}"
    )

    session.clear()
    await feed(dp, bot, callback("rec:mp:1"))

    assert занятый in session.last_text, (
        f"вторая страница о своём занятом пункте промолчала: {session.last_text!r}"
    )


async def test_занятый_пункт_остаётся_в_перечне_и_нажимаемым(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Пометка — это предупреждение, а не решение за человека.

    Убрать пункт из перечня значило бы спрятать часть методики: пункт может
    понадобиться, а отказ движка приводит к кнопкам занявшей записи и чинится
    правкой.
    """
    начата()
    add_finding(CHAT_ID, "CLN05", "D1", "hot_kitchen", "Нагар на подине печи")
    stub_classify(monkeypatch, suggestion())
    stub_manual(monkeypatch, ПЕРЕЧЕНЬ)
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await открыть_перечень(dp, bot)

    assert "rec:mi:0" in session.keyboard_data(), "занятый пункт исчез из перечня"
