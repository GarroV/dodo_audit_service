"""Кадр ОТВЕТОМ на свои же слова уходит в ту самую запись (T205, решение D081).

Случай владельца: аудитор наговорил голосовое, бот собрал по нему запись, а
кадр к ней прислал следом — ответом на своё же голосовое. Дословная
формулировка владельца лежит в `docs/forge/decisions.md`, D081; сюда она не
переносится, репозиторий публичный (сторож — `tests/test_methodology_leak.py`).

Что защищает этот файл — пять вещей.

**Кадр попадает в ту же запись, а не заводит вторую.** Иначе одно нарушение
уехало бы партнёру двумя строками: одна со словами и первым кадром, вторая с
досланным кадром и без слов.

**Ответ адресует ТУ запись, о которой были слова, а не последнюю.** Между
голосовым и досланным кадром аудитор успевает записать ещё нарушение.

**Разбора у досланного кадра нет.** Пункт уже найден по словам человека, и
второй разбор стоил бы вызова модели, чтобы переспросить о том, на что человек
уже ответил (D046 про бездумный расход как раз об этом).

**Ответ не на свои слова работает как раньше.** Кадр ответом на что угодно
другое — обычный кадр с вопросом «Разобрать?».

**Сданная проверка кадром не правится.** Запрет T201 (D080) действует и здесь:
кадр уезжает в отчёт партнёру, то есть это правка отданного отчёта.
"""

from __future__ import annotations

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
    stub_transcribe,
    suggestion,
    text_message,
    voice_message,
)
from bot_harness import callback_query as callback

from src.bot import sidecar
from src.bot.app import build_dispatcher
from src.bot.config import BotSettings
from src.bot.keyboards import (
    ANALYZE_PREFIX,
    EDIT_DROP,
    EDIT_PREFIX,
    MODEL_CALLBACK,
    PICK_PREFIX,
)
from src.bot.texts import t
from src.domain import Finding, drop_inspection, get_state, start_inspection

pytestmark = pytest.mark.asyncio

SETTINGS = BotSettings(token="unused-in-tests", allowed_ids=frozenset({AUDITOR_ID}), mode="polling")

#: Однозначная фраза на синтетической методике набора: строка карты произнесена
#: целиком, колонка выбрана словом → ровно один код с единственным классом.
#: Зону слова не называют — её подставит память, как на точке.
OVEN = "печь грязная"

#: Вторая такая же фраза, ведущая в другой пункт: нужна там, где записей должно
#: быть две и проверяется адресность.
FURNITURE = "мебель участка в пятнах"


def started() -> None:
    start_inspection(CHAT_ID, "Белград 2", "planned", "ru")


def findings() -> list[Finding]:
    state = get_state(CHAT_ID)
    return [] if state is None else list(state.findings)


async def record_by_voice(dp: object, bot: object, said: str = OVEN, frame: str = "p1") -> object:
    """Записать нарушение голосом и вернуть СООБЩЕНИЕ аудитора с этим голосовым.

    Порядок именно такой — сначала кадр, потом голосовое: фотофиксация
    обязательна всегда (D078), и одни слова записью не становятся. Досланный
    ответом кадр — второй у этой записи, а не первый.
    """
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")
    await feed(dp, bot, photo_message(frame))  # type: ignore[arg-type]
    voice = voice_message(f"voice-{frame}")
    await feed(dp, bot, voice)  # type: ignore[arg-type]
    return voice


async def test_кадр_ответом_на_своё_голосовое_попадает_в_ту_же_запись(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Главное требование задачи: кадров у записи два, а записей — одна."""
    started()
    stub_classify(monkeypatch, suggestion())
    stub_transcribe(monkeypatch, OVEN)
    bot, _ = make_bot()
    dp = build_dispatcher(SETTINGS)

    voice = await record_by_voice(dp, bot)
    assert [f.photos for f in findings()] == [["p1"]], "запись по голосовому не легла"

    await feed(dp, bot, photo_message("p2", reply_to=voice))  # type: ignore[arg-type]

    assert len(findings()) == 1, "досланный кадр завёл вторую запись о том же нарушении"
    assert findings()[0].photos == ["p1", "p2"], "кадр не попал в запись, к которой его прислали"


async def test_кадр_ответом_адресует_ту_запись_о_которой_были_слова(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Между голосовым и досланным кадром аудитор успевает записать ещё нарушение."""
    started()
    stub_classify(monkeypatch, suggestion())
    stub_transcribe(monkeypatch, OVEN)
    bot, _ = make_bot()
    dp = build_dispatcher(SETTINGS)

    first = await record_by_voice(dp, bot)
    await feed(dp, bot, photo_message("p9", caption=FURNITURE))
    assert len(findings()) == 2, "второе нарушение не записалось — тест проверяет не то"

    await feed(dp, bot, photo_message("p2", reply_to=first))  # type: ignore[arg-type]

    got = {f.n: f.photos for f in findings()}
    assert got[1] == ["p1", "p2"], "кадр ушёл не в ту запись, на слова о которой ответили"
    assert got[2] == ["p9"], "кадр приписался соседней записи"


async def test_досланный_кадр_в_модель_не_уходит_и_разбора_не_просит(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Пункт уже найден по словам человека — переспрашивать не о чем.

    Вопрос «Разобрать?» здесь был бы не просто лишним: нажатие на него завело бы
    по тому же кадру ВТОРУЮ запись о том же нарушении.
    """
    started()
    asked = stub_classify(monkeypatch, suggestion())
    stub_transcribe(monkeypatch, OVEN)
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    voice = await record_by_voice(dp, bot)
    session.clear()

    await feed(dp, bot, photo_message("p2", reply_to=voice))  # type: ignore[arg-type]

    assert asked == [], "досланный кадр уехал в модель"
    assert not any((data or "").startswith(ANALYZE_PREFIX) for data in session.keyboard_data()), (
        "по досланному кадру задан вопрос «Разобрать?»"
    )
    assert session.last_text == t("record.frame_attached", "ru", n=1, count=2)


async def test_досланный_кадр_остаётся_в_списке_присланного(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Кадр записывается в заметки, как и всякий присланный (T068).

    Иначе кадр, не прикрепившийся из-за отказа движка, исчез бы бесследно: при
    завершении показываются те кадры, которых нет ни в одной записи, а собирают
    их именно из заметок.
    """
    started()
    stub_classify(monkeypatch, suggestion())
    stub_transcribe(monkeypatch, OVEN)
    bot, _ = make_bot()
    dp = build_dispatcher(SETTINGS)
    voice = await record_by_voice(dp, bot)

    await feed(dp, bot, photo_message("p2", reply_to=voice))  # type: ignore[arg-type]

    assert "p2" in [frame.file_id for frame in sidecar.read(CHAT_ID).frames]


async def test_кадр_ответом_на_свою_подпись_попадает_в_ту_же_запись(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Слова аудитора приходят не только голосом: подпись к кадру — те же слова.

    Правило одно на все способы сказать, и разойдись оно по способам —
    досланный кадр работал бы у голосового и молчал у подписи, а объяснить эту
    разницу человеку на точке было бы нечем.
    """
    started()
    stub_classify(monkeypatch, suggestion())
    bot, _ = make_bot()
    dp = build_dispatcher(SETTINGS)
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")
    said = photo_message("c1", caption=OVEN)
    await feed(dp, bot, said)
    assert [f.photos for f in findings()] == [["c1"]]

    await feed(dp, bot, photo_message("c2", reply_to=said))

    assert len(findings()) == 1
    assert findings()[0].photos == ["c1", "c2"]


async def test_исток_переживает_разбор_моделью(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """«Разобрать моделью» — те же слова аудитора, и кадр к ним досылается так же.

    Потеряй разбор моделью исток — досылка кадра молча перестала бы работать
    ровно у тех записей, которые переразобраны, то есть у самых спорных.
    """
    started()
    stub_classify(monkeypatch, suggestion(candidate("CLN06", "D1", "hot_kitchen")))
    stub_transcribe(monkeypatch, OVEN)
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    voice = await record_by_voice(dp, bot)

    await feed(dp, bot, callback(MODEL_CALLBACK, message_id=session.last_sent_id))
    await feed(dp, bot, callback(f"{PICK_PREFIX}0", message_id=session.last_sent_id))
    assert [f.code for f in findings()] == ["CLN05", "CLN06"], "разбор моделью не завёл запись"

    await feed(dp, bot, photo_message("p2", reply_to=voice))  # type: ignore[arg-type]

    # У переразобранной записи тот же кадр, что и у первой: материал не менялся,
    # менялся разбор. Досланный кадр становится у неё вторым.
    assert {f.n: f.photos for f in findings()}[2] == ["p1", "p2"], (
        "кадр не дошёл до записи, сделанной разбором моделью по тем же словам"
    )


async def test_кадр_ответом_на_чужое_сообщение_работает_как_раньше(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ответить кадром можно на что угодно, и такой кадр — обычный кадр.

    Сообщение бота записью не является, и подхватывать по нему нечего: аудитор
    получает вопрос «Разобрать?», как и на кадр без всякого ответа.
    """
    started()
    stub_classify(monkeypatch, suggestion())
    stub_transcribe(monkeypatch, OVEN)
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    await record_by_voice(dp, bot)
    shown = session.last_sent_id
    session.clear()

    await feed(dp, bot, photo_message("p2", reply_to=bot_message(shown)))

    assert findings()[0].photos == ["p1"], "кадр ответом на сообщение БОТА попал в запись"
    assert any((data or "").startswith(ANALYZE_PREFIX) for data in session.keyboard_data()), (
        "обычный кадр остался без вопроса «Разобрать?»"
    )


async def test_кадр_ответом_на_слова_снятой_записи_говорит_что_записи_нет(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Сообщения остаются в переписке навсегда, а записи снимают.

    Завести по такому ответу новую запись было бы худшим исходом: кадр без слов
    человека — это не находка, а вопрос, на который аудитор в этот момент не
    отвечал.
    """
    started()
    stub_classify(monkeypatch, suggestion())
    stub_transcribe(monkeypatch, OVEN)
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    voice = await record_by_voice(dp, bot)
    await feed(dp, bot, callback(f"{EDIT_PREFIX}1:{EDIT_DROP}"))
    assert findings() == []
    session.clear()

    await feed(dp, bot, photo_message("p2", reply_to=voice))  # type: ignore[arg-type]

    assert findings() == [], "кадр ответом на снятую запись что-то записал"
    assert session.last_text == t("edit.gone", "ru", n=1), (
        "аудитору не сказано, что записи, к которой он шлёт кадр, больше нет"
    )


async def test_кадр_ответом_без_проверки_говорит_что_её_нет(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Проверку убрали из чата, а переписка осталась — молчать на кадр нельзя.

    Убрать проверку аудитору предлагает сам бот (выход из запрета T201), и
    ответить кадром на старое сообщение после этого — обычная ошибка. Отказ
    здесь тот же, что и на любой кадр без проверки: другой текст об одном и том
    же читался бы как другой запрет.
    """
    started()
    stub_classify(monkeypatch, suggestion())
    stub_transcribe(monkeypatch, OVEN)
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    voice = await record_by_voice(dp, bot)
    drop_inspection(CHAT_ID)
    session.clear()

    await feed(dp, bot, photo_message("p2", reply_to=voice))  # type: ignore[arg-type]

    assert session.last_text == t("material.no_inspection", "ru")


async def test_непринятый_движком_кадр_называется_вслух(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Движок кадр не принял — аудитор обязан это узнать.

    Иначе он считает фотофиксацию сделанной (D078), а её нет, и увидит это
    только партнёр — в отчёте без снимка. Запятая в идентификаторе кадра —
    настоящий отказ движка: он режет список кадров по запятой, и один кадр
    молча превратился бы в два несуществующих.
    """
    started()
    stub_classify(monkeypatch, suggestion())
    stub_transcribe(monkeypatch, OVEN)
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    voice = await record_by_voice(dp, bot)
    session.clear()

    await feed(dp, bot, photo_message("bad,id", reply_to=voice))  # type: ignore[arg-type]

    assert findings()[0].photos == ["p1"], "движок принял кадр, которого не должен был"
    assert session.last_text == t("record.frame_failed", "ru", n=1)
    assert "bad,id" in [frame.file_id for frame in sidecar.read(CHAT_ID).frames], (
        "не принятый движком кадр пропал бесследно — при завершении его не покажут"
    )


async def test_кадр_ответом_в_сданную_проверку_отклоняется(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Отданный отчёт не правится ничем (T201, D080) — кадр не исключение.

    Кадр уезжает в отчёт партнёру, значит, досылка кадра — это правка сданного
    отчёта той же дверью, только с другой стороны.
    """
    started()
    stub_classify(monkeypatch, suggestion())
    stub_transcribe(monkeypatch, OVEN)
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    voice = await record_by_voice(dp, bot)
    await build_report(dp, bot)
    session.clear()

    await feed(dp, bot, photo_message("p2", reply_to=voice))  # type: ignore[arg-type]

    assert findings()[0].photos == ["p1"], "кадр дописался в сданную проверку"
    assert session.last_text == t("sealed.blocked", "ru")


async def test_правка_ответом_кадром_не_подменяется(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Две карты сообщений не путаются между собой (T204 и T205).

    Ответ СЛОВАМИ на своё же голосовое правкой записи не становится: правку
    владелец описал как ответ на сообщение бота, а слова, сказанные вслед своим
    же словам, — это обычный комментарий, которому нужен свой кадр.
    """
    started()
    stub_classify(monkeypatch, suggestion())
    stub_transcribe(monkeypatch, OVEN)
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    voice = await record_by_voice(dp, bot)
    session.clear()

    await feed(dp, bot, text_message(FURNITURE, reply_to=voice))  # type: ignore[arg-type]

    assert [(f.n, f.code) for f in findings()] == [(1, "CLN05")], (
        "ответ словами на своё же голосовое поправил запись вместо обычного разбора"
    )
