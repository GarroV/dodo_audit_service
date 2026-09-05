"""Правка ОТВЕТОМ на сообщение бота (T204, решение D081).

Владелец, дословно: «Если комментарий надо поправить - ОТВЕТОМ на сообщение
бота пользователь вносит корректировку… условно: распознала система грязную
линию начинения - но написала в отбивке что это стол в холодном цехе. значит
пользователь на это собщение пишет: горячий цех, линия начинения. система берет
уже этот комментарий и ищет нужный пункт в чеклисте».

Что защищает этот файл — четыре вещи.

**Ответ правит, а не добавляет.** Иначе исправленное нарушение попадало бы в
отчёт партнёру дважды: один раз неверным пунктом, второй — верным.

**Ответ адресует ТУ САМУЮ запись, а не последнюю.** Телеграм сообщает номер
сообщения, на которое отвечают, и по нему находится запись. Без этого правка
последней записи была бы правкой не той, стоило аудитору прислать кадр между
разбором и ответом, — а на точке между ними проходят минуты.

**Ответ не на запись работает как раньше.** Аудитор отвечает и на свои кадры, и
на служебные сообщения бота; сломать это связывание значило бы поменять правку
записи на потерю комментария.

**Ответ на сообщение о снятой записи не трогает соседнюю.** Сообщения остаются
в переписке навсегда, и адресат правки ищется не в карте «сообщение → запись», а
в самой проверке: снятой записи там нет ни под каким номером.
"""

from __future__ import annotations

import pytest
from bot_harness import (
    AUDITOR_ID,
    CHAT_ID,
    bot_message,
    candidate,
    feed,
    make_bot,
    photo_message,
    stub_classify,
    stub_transcribe,
    suggestion,
    text_message,
    voice_message,
)
from bot_harness import callback_query as callback

from src.bot import sidecar
from src.bot.app import build_dispatcher
from src.bot.config import BotSettings
from src.bot.keyboards import EDIT_DROP, EDIT_PREFIX, PICK_PREFIX
from src.bot.texts import t
from src.domain import Finding, get_state, start_inspection

pytestmark = pytest.mark.asyncio

SETTINGS = BotSettings(token="unused-in-tests", allowed_ids=frozenset({AUDITOR_ID}), mode="polling")

#: Однозначная фраза боевого вида на синтетической карте: строка «Печь»
#: произнесена целиком, колонка выбрана словом «грязная» → CLN05, класс один.
#: Зону слова не называют — её подставит память, как на точке.
OVEN = "печь грязная"

#: Ответ аудитора той же формы, что в примере владельца: названы и место, и
#: объект. Ведёт к другому пункту И в другую зону — то есть правит ровно то,
#: в чём система промахнулась.
SINK_REPLY = "посудный участок, раковина и смеситель грязные"


def started() -> None:
    start_inspection(CHAT_ID, "Белград 2", "planned", "ru")


def findings() -> list[Finding]:
    state = get_state(CHAT_ID)
    return [] if state is None else list(state.findings)


async def make_record(dp: object, bot: object, session: object, caption: str = OVEN) -> int:
    """Записать нарушение словами и вернуть номер сообщения бота о нём."""
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")
    await feed(dp, bot, photo_message(f"frame-{caption[:6]}", caption=caption))  # type: ignore[arg-type]
    return session.last_sent_id  # type: ignore[attr-defined,no-any-return]


async def test_ответ_на_запись_правит_её_а_не_создаёт_новую(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Главное требование задачи: записей остаётся одна, и она — исправленная."""
    started()
    stub_classify(monkeypatch, suggestion())
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    said = await make_record(dp, bot, session)
    assert [(f.code, f.zone) for f in findings()] == [("CLN05", "hot_kitchen")]

    await feed(dp, bot, text_message(SINK_REPLY, reply_to=bot_message(said)))

    assert [(f.code, f.zone) for f in findings()] == [("CLN02", "dishwashing")], (
        "ответ на сообщение бота не поправил запись"
    )
    assert findings()[0].n == 1, "правка завела запись под новым номером"


async def test_поправленная_запись_показывается_вопросом_пункта(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Код глазами не читается — аудитор обязан увидеть, ЧТО теперь записано.

    Тот же довод, по которому вопрос пункта попал в показ записи по словам
    (T135): промах правки иначе виден только партнёру в отчёте.
    """
    started()
    stub_classify(monkeypatch, suggestion())
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    said = await make_record(dp, bot, session)
    session.clear()

    await feed(dp, bot, text_message(SINK_REPLY, reply_to=bot_message(said)))

    shown = session.last_text
    assert "CLN02" in shown
    assert "Раковина и смеситель без налёта" in shown, "вопрос пункта аудитору не показан"
    assert "Посудный участок" in shown, "новая зона в показе не названа"


async def test_ответ_адресует_конкретную_запись_а_не_последнюю(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Между разбором и ответом аудитор успевает прислать ещё кадр."""
    started()
    stub_classify(monkeypatch, suggestion())
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    first = await make_record(dp, bot, session)
    await make_record(dp, bot, session, caption="тепловой участок, мебель участка в пятнах")
    assert sorted(f.code for f in findings()) == ["CLN05", "CLN06"]

    await feed(dp, bot, text_message(SINK_REPLY, reply_to=bot_message(first)))

    got = {f.n: (f.code, f.zone) for f in findings()}
    assert got[1] == ("CLN02", "dishwashing"), "поправлена не та запись, на которую ответили"
    assert got[2] == ("CLN06", "hot_kitchen"), "тронута соседняя запись"


async def test_ответ_без_быстрого_пути_уходит_модели_и_правит_ту_же_запись(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Сверка со списком не сошлась — пункт ищет модель, но записи не прибавляется.

    Кнопка под предложением здесь означает «поправить на этот пункт», а не
    «завести ещё один»: подтверждение остаётся за человеком (принцип 3), а
    адресат правки уже назван ответом.
    """
    started()
    asked = stub_classify(monkeypatch, suggestion(candidate("CLN06", "D1", "hot_kitchen")))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    said = await make_record(dp, bot, session)

    await feed(dp, bot, text_message("это вообще про мебель", reply_to=bot_message(said)))

    assert len(asked) == 1, "модель не позвали"
    assert asked[0][1] is None, "в модель уехала картинка, хотя аудитор написал словами (D081)"
    assert len(findings()) == 1, "показ кандидатов сам по себе что-то записал"

    await feed(dp, bot, callback(f"{PICK_PREFIX}0"))

    assert [(f.n, f.code) for f in findings()] == [(1, "CLN06")], (
        "нажатие завело вторую запись вместо правки первой"
    )


async def test_перечень_под_правкой_говорит_о_правке_а_не_о_новой_записи(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Найдено смоуком: перечень спрашивал «Что записать? Кадров: 0».

    Аудитор ответил на запись, чтобы её поправить, а читал вопрос о новой — и,
    нажав «Записать», ждал бы второй строки в отчёте. Ни номера правимой записи,
    ни глагола «поправить» в показе не было вовсе.
    """
    started()
    stub_classify(monkeypatch, suggestion(candidate("CLN06", "D1", "hot_kitchen")))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    said = await make_record(dp, bot, session)

    await feed(dp, bot, text_message("это вообще про мебель", reply_to=bot_message(said)))

    assert session.last_text == t(
        "record.candidates_correcting",
        "ru",
        n=1,
        lines=session.last_text.split("\n\n", 1)[1],
    )
    assert t("btn.fix_single", "ru") in session.keyboard_texts()
    assert t("btn.pick_single", "ru") not in session.keyboard_texts()
    assert "Кадров: 0" not in session.last_text, "кадры записи выглядят потерянными"


async def test_голосовой_ответ_правит_запись(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Аудитор идёт по точке и правит голосом так же, как записывает голосом."""
    started()
    stub_classify(monkeypatch, suggestion())
    stub_transcribe(monkeypatch, SINK_REPLY)
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    said = await make_record(dp, bot, session)

    await feed(dp, bot, voice_message("voice-1", reply_to=bot_message(said)))

    assert [(f.code, f.zone) for f in findings()] == [("CLN02", "dishwashing")]


async def test_ответ_не_на_запись_связывает_комментарий_как_раньше(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ответ на служебное сообщение бота — прежнее связывание, а не правка.

    Кадр прислан без подписи, бот спросил «Разобрать?»; аудитор отвечает на этот
    вопрос словами. Записи об этом кадре ещё нет, и правка тут ни при чём.
    """
    started()
    stub_classify(monkeypatch, suggestion())
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")

    await feed(dp, bot, photo_message("frame-bare"))
    asked = session.last_sent_id

    await feed(dp, bot, text_message(OVEN, reply_to=bot_message(asked)))

    assert [(f.code, f.zone) for f in findings()] == [("CLN05", "hot_kitchen")], (
        "комментарий ответом на вопрос бота потерялся"
    )


async def test_ответ_на_снятую_запись_ничего_не_заводит(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Запись удалена — правке некуда лечь, и молчать об этом нельзя."""
    started()
    stub_classify(monkeypatch, suggestion())
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    said = await make_record(dp, bot, session)
    await feed(dp, bot, callback(f"{EDIT_PREFIX}1:{EDIT_DROP}"))
    assert findings() == []
    session.clear()

    await feed(dp, bot, text_message(SINK_REPLY, reply_to=bot_message(said)))

    assert findings() == [], "ответ на снятую запись завёл новую"
    assert session.last_text == t("edit.gone", "ru", n=1)


async def test_ответ_на_сообщение_о_снятой_записи_не_трогает_соседнюю(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Сообщения о снятых записях остаются в переписке, и на них отвечают.

    Привязка «сообщение → запись» переживает удаление намеренно: адресат ищется
    не в ней, а в самой проверке, и снятая запись не находится там ни под каким
    номером. Проверено фактом: движок номера НЕ переиспользует — после снятия
    записи №1 следующая получает №3, а не №1 (прогон `domain.add_finding` над
    настоящим движком). Поэтому мёртвая привязка не может достаться чужой
    записи, и чистить её нечем и незачем.
    """
    started()
    stub_classify(monkeypatch, suggestion())
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    stale = await make_record(dp, bot, session)
    await feed(dp, bot, callback(f"{EDIT_PREFIX}1:{EDIT_DROP}"))
    await make_record(dp, bot, session, caption="тепловой участок, мебель участка в пятнах")
    living = [(f.n, f.code) for f in findings()]
    session.clear()

    await feed(dp, bot, text_message(SINK_REPLY, reply_to=bot_message(stale)))

    assert [(f.n, f.code) for f in findings()] == living, "правка ушла в чужую запись"
    assert session.last_text == t("edit.gone", "ru", n=1)
