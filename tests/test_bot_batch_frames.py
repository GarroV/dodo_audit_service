"""Пачка кадров: отбивка списком отдельными сообщениями (T206, решение D081).

Дословная формулировка владельца лежит в `docs/forge/decisions.md`, D081; сюда
она не переносится — репозиторий публичный (сторож
`tests/test_methodology_leak.py`).

До задачи пачка кадров давала ОДНУ запись на все кадры, а в модель уходил
только первый кадр: второй и третий уезжали в отчёт прикреплёнными к чужому
пункту, никем не разобранные. Задача разводит два случая, и разводит их наличие
слов, а не число кадров.

Что защищает этот файл — пять вещей.

**Кадры БЕЗ комментария разбираются по кадру.** Сколько кадров — столько
разборов и столько сообщений в отбивке.

**Кадры С комментарием остаются одной записью.** Сказал человек об этих кадрах
одно — значит, нарушение одно, сколько бы ракурсов он ни снял
(`docs/06-mvp-bot.md`, шаг 3). Разводить эти случаи и есть задача.

**Нажатие под сообщением кадра записывает ТОТ кадр.** Предложений в чате
несколько разом, и кнопка обязана адресовать своё: иначе аудитор нажимает под
третьим кадром, а в запись уходит первый — молча.

**Неадресованное нажатие получает отказ, а не догадку.** Догадка по последнему
показанному записала бы не тот кадр, и увидеть это было бы некому.

**Ответ на сообщение о записи из пачки правит именно её.** Механизм тот же, что
у T204, — второго такого здесь не заводится.
"""

from __future__ import annotations

from typing import Any

import pytest
from bot_harness import (
    AUDITOR_ID,
    CHAT_ID,
    Calls,
    bot_message,
    candidate,
    feed,
    make_bot,
    photo_message,
    suggestion,
    text_message,
)
from bot_harness import callback_query as callback

from src.bot import sidecar
from src.bot.app import build_dispatcher
from src.bot.config import BotSettings
from src.bot.keyboards import ANALYZE_PREFIX, PICK_PREFIX
from src.bot.texts import t
from src.domain import Finding, get_state, start_inspection
from src.recognize.models import Suggestion

pytestmark = pytest.mark.asyncio

SETTINGS = BotSettings(token="unused-in-tests", allowed_ids=frozenset({AUDITOR_ID}), mode="polling")

#: Разные пункты одной зоны: пара «пункт + зона» занимается один раз, и три
#: одинаковых кандидата дали бы отказ движка вместо трёх записей — то есть тест
#: зеленел бы на боте, который пачку не разобрал.
BY_FRAME = (
    suggestion(candidate("CLN05", "D1", "hot_kitchen", "первый кадр")),
    suggestion(candidate("CLN06", "D1", "hot_kitchen", "второй кадр")),
    suggestion(candidate("PRD06", "D1", "hot_kitchen", "третий кадр")),
)


def started() -> None:
    start_inspection(CHAT_ID, "Белград 2", "planned", "ru")


def findings() -> list[Finding]:
    state = get_state(CHAT_ID)
    return [] if state is None else list(state.findings)


def stub_by_call(monkeypatch: pytest.MonkeyPatch, results: tuple[Suggestion, ...]) -> Calls:
    """Подменить разбор так, чтобы КАЖДЫЙ вызов отвечал своим предложением.

    Общая подмена набора (`stub_classify`) отвечает одним и тем же на все
    вызовы, а здесь важно именно то, что вызовов несколько и ответы у них
    разные: одинаковые ответы упёрлись бы в отказ движка по занятой паре, и
    отличить «пачка не разобралась» от «пачка разобралась, но записалась одна»
    было бы нечем.
    """
    calls = Calls()

    def fake(note: str, photo: object = None, zone_hint: object = None, **kw: Any) -> Suggestion:
        calls.append((note, photo, zone_hint))
        return results[min(len(calls) - 1, len(results) - 1)]

    monkeypatch.setattr("src.bot.routers.record.classify", fake)
    return calls


#: Кадр, которым тест закрывает альбом. Кадр чужой группы закрывает предыдущий
#: альбом немедленно (`AlbumBuffer.close_other`) — в отличие от комментария,
#: который тут закрывать нельзя: комментарий это уже другой случай D081, ровно
#: тот, от которого пачка и отводится.
CLOSING_FRAME = "closing-frame"


async def send_album(dp: Any, bot: Any, *file_ids: str, caption: str | None = None) -> Any:
    """Прислать альбом и закрыть его СОБЫТИЕМ. Возвращает ПЕРВЫЙ кадр.

    Первый кадр нужен для `callback_data` кнопки «Разобрать?»: якорем группы
    служит его номер сообщения.

    Событием, а не таймером, и это не придирка: с окном в 10 мс таймер успевает
    сработать между кадрами и рвёт альбом посередине — проверено, тест на
    прокомментированный альбом получил два кадра из трёх и упал не на том, что
    в его имени.
    """
    first = photo_message(file_ids[0], media_group_id="album", caption=caption)
    await feed(dp, bot, first)
    for file_id in file_ids[1:]:
        await feed(dp, bot, photo_message(file_id, media_group_id="album"))
    await feed(dp, bot, photo_message(CLOSING_FRAME))
    return first


def dispatcher() -> Any:
    """Окно альбома длинное намеренно: закрывать альбом обязано событие (см. выше)."""
    return build_dispatcher(SETTINGS, album_window=5.0)


async def analyze_album(dp: Any, bot: Any, session: Any, *file_ids: str) -> list[int]:
    """Прислать пачку и нажать «Разобрать?». Возвращает номера сообщений отбивки."""
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")
    first = await send_album(dp, bot, *file_ids)
    mark = len(session.sent_ids)
    await feed(dp, bot, callback(f"{ANALYZE_PREFIX}{first.message_id}"))
    # Первым уходит объявление о разборе пачки, дальше — по сообщению на кадр.
    return session.sent_ids[mark + 1 :]


async def test_каждый_кадр_пачки_разбирается_своим_вызовом(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Раньше в модель уходил только первый кадр, а записывались все три."""
    started()
    asked = stub_by_call(monkeypatch, BY_FRAME)
    bot, session = make_bot()

    await analyze_album(dispatcher(), bot, session, "a1", "a2", "a3")

    assert len(asked) == 3, "пачка разобрана не по кадру"


async def test_отбивка_приходит_отдельными_сообщениями_с_номером_кадра(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """По сообщению на кадр, и каждое называет свой номер.

    Без номера пять одинаковых сообщений подряд аудитор не соотнесёт с тем, что
    снимал, — а соотносить их ему придётся, потому что отвечать он будет на
    одно из них.
    """
    started()
    stub_by_call(monkeypatch, BY_FRAME)
    bot, session = make_bot()

    shown = await analyze_album(dispatcher(), bot, session, "a1", "a2", "a3")

    assert len(shown) == 3, "отбивка пришла не тремя сообщениями"
    tail = session.texts[-3:]
    for no, text in enumerate(tail, start=1):
        assert text.startswith(t("record.candidates_batch", "ru", no=no, total=3, lines="")[:20]), (
            f"сообщение отбивки не называет кадр {no} из 3"
        )


async def test_нажатие_под_третьим_кадром_записывает_именно_третий(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Сердце задачи: кнопка адресует своё предложение, а не последнее показанное.

    Аудитор разбирает список не по порядку, и записаться обязан тот кадр, под
    сообщением о котором он нажал.
    """
    started()
    stub_by_call(monkeypatch, BY_FRAME)
    bot, session = make_bot()
    dp = dispatcher()
    shown = await analyze_album(dp, bot, session, "a1", "a2", "a3")

    await feed(dp, bot, callback(f"{PICK_PREFIX}0", message_id=shown[2]))

    assert [(f.code, f.photos) for f in findings()] == [("PRD06", ["a3"])], (
        "нажатие под третьим кадром записало не его"
    )


async def test_пачка_даёт_запись_на_кадр_а_не_одну_на_все(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Разобрав весь список, аудитор получает три записи, у каждой — свой кадр.

    До задачи получалась одна запись со всеми тремя кадрами, и два нарушения из
    трёх в отчёт партнёру не попадали вовсе.
    """
    started()
    stub_by_call(monkeypatch, BY_FRAME)
    bot, session = make_bot()
    dp = dispatcher()
    shown = await analyze_album(dp, bot, session, "a1", "a2", "a3")

    for at in shown:
        await feed(dp, bot, callback(f"{PICK_PREFIX}0", message_id=at))

    assert [(f.code, f.photos) for f in findings()] == [
        ("CLN05", ["a1"]),
        ("CLN06", ["a2"]),
        ("PRD06", ["a3"]),
    ]


async def test_неадресованное_нажатие_в_пачке_отвечает_отказом(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Кнопка из сообщения, о котором бот ничего не знает, ничего не записывает.

    Догадка «наверное, это про последний кадр» была бы худшим исходом: она
    записала бы чужой кадр, и заметить это аудитору было бы негде.
    """
    started()
    stub_by_call(monkeypatch, BY_FRAME)
    bot, session = make_bot()
    dp = dispatcher()
    await analyze_album(dp, bot, session, "a1", "a2", "a3")
    session.clear()

    await feed(dp, bot, callback(f"{PICK_PREFIX}0"))

    assert findings() == [], "неадресованное нажатие всё-таки что-то записало"
    assert session.last_text == t("record.stale", "ru")


async def test_кадры_с_комментарием_остаются_одной_записью(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Второй случай разводки: сказанное человеком слово — одно нарушение.

    Спека требует этого прямо (`docs/06-mvp-bot.md`, шаг 3): несколько кадров
    одного объекта — одно нарушение с несколькими фото. Задача T206 этот случай
    не отменяет, а отделяет от пачки без слов.
    """
    started()
    asked = stub_by_call(monkeypatch, BY_FRAME)
    bot, _ = make_bot()
    dp = dispatcher()
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")

    await send_album(dp, bot, "b1", "b2", "b3", caption="печь грязная")

    assert asked == [], "прокомментированные кадры уехали в модель (D081)"
    assert [(f.code, f.photos) for f in findings()] == [("CLN05", ["b1", "b2", "b3"])]


async def test_второе_нажатие_под_тем_же_кадром_ничего_не_дописывает(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Предложение забирается целиком, со всеми своими сообщениями.

    Аудитор на точке нажимает кнопку повторно, не дождавшись ответа. Сними мы
    предложение только с последнего его сообщения — кнопка под первым завела бы
    вторую запись по тому же кадру, и в отчёт партнёру уехали бы обе.
    """
    started()
    stub_by_call(monkeypatch, BY_FRAME)
    bot, session = make_bot()
    dp = dispatcher()
    shown = await analyze_album(dp, bot, session, "a1", "a2", "a3")
    await feed(dp, bot, callback(f"{PICK_PREFIX}0", message_id=shown[0]))
    session.clear()

    await feed(dp, bot, callback(f"{PICK_PREFIX}0", message_id=shown[0]))

    assert [(f.code, f.photos) for f in findings()] == [("CLN05", ["a1"])], (
        "второе нажатие дописало запись по уже разобранному кадру"
    )
    assert session.last_text == t("record.stale", "ru")


async def test_использованная_кнопка_пачки_не_записывает_чужой_материал(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Кнопка отвечает за СВОЙ разговор, даже когда его уже нет.

    Случай, ради которого сообщения не забываются после фиксации: аудитор
    разобрал кадр пачки, следом прислал и разобрал ещё один кадр, а потом
    нажал старую кнопку из списка. Ответь бот по последнему показанному —
    в отчёт партнёру уехала бы запись о ДРУГОМ кадре, и заметить подмену было
    бы негде: и кнопка, и текст под ней остались от прежнего разбора.
    """
    started()
    stub_by_call(monkeypatch, BY_FRAME)
    bot, session = make_bot()
    dp = dispatcher()
    shown = await analyze_album(dp, bot, session, "a1", "a2", "a3")
    await feed(dp, bot, callback(f"{PICK_PREFIX}0", message_id=shown[0]))

    single = photo_message("single")
    await feed(dp, bot, single)
    await feed(dp, bot, callback(f"{ANALYZE_PREFIX}{single.message_id}"))
    session.clear()

    await feed(dp, bot, callback(f"{PICK_PREFIX}0", message_id=shown[0]))

    assert [(f.code, f.photos) for f in findings()] == [("CLN05", ["a1"])], (
        "старая кнопка пачки записала материал другого кадра"
    )
    assert session.last_text == t("record.stale", "ru")


async def test_ответ_на_сообщение_о_записи_из_пачки_правит_именно_её(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Правка ответом (T204) работает на записях пачки без второго механизма.

    Работает она потому, что каждая запись показана СВОИМ сообщением: одна
    запись на три кадра оставляла бы аудитору одно сообщение на три нарушения,
    и адресовать правку было бы нечему.
    """
    started()
    stub_by_call(monkeypatch, BY_FRAME)
    bot, session = make_bot()
    dp = dispatcher()
    shown = await analyze_album(dp, bot, session, "a1", "a2", "a3")
    await feed(dp, bot, callback(f"{PICK_PREFIX}0", message_id=shown[0]))
    await feed(dp, bot, callback(f"{PICK_PREFIX}0", message_id=shown[2]))
    first_record = session.sent_ids[-2]
    assert [f.code for f in findings()] == ["CLN05", "PRD06"]

    reply = text_message(
        "посудный участок, раковина и смеситель грязные", reply_to=bot_message(first_record)
    )
    await feed(dp, bot, reply)

    got = {f.n: f.code for f in findings()}
    assert got[1] == "CLN02", "ответ на сообщение о первой записи её не поправил"
    assert got[2] == "PRD06", "тронута соседняя запись пачки"
