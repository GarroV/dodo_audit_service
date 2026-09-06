"""Ответ на отбивку, назвавший только зону, переставляет зону записи (T231, D090).

Механизм правки ответом на сообщение бота работает с T204, и пункт он ищет
заново: сверка со списком нарушений, затем модель, затем ручной перечень. Один
случай эта дорога не обслуживала — самый частый. Бот отбился, что запись
сохранена, аудитор отвечает «это был другой цех», и в его словах нет никакого
объекта: сверке не за что зацепиться, модель возвращает не то или ничего, а без
ключа модели открывается ручной перечень. Аудитор просил переставить зону, а
получал вопрос, какое нарушение записать.

Что защищает этот файл — пять вещей.

**Зона переезжает, а записи не прибавляется.** Иначе одно нарушение уехало бы
партнёру двумя строками — ровно то, ради чего правка ответом и заведена.

**Пункт, класс и текст остаются прежними.** Аудитор поправил место, а не
находку; переписать заодно формулировку значило бы подменить сказанное.

**Правка подтверждается новой отбивкой.** Молчаливая правка отчёта партнёру —
это правка, которой никто не видел.

**Модель за такой ответ не платится.** Пункта в словах нет, искать его нечем и
незачем.

**Слова, называющие ОБЪЕКТ, идут прежней дорогой.** Признак «в словах только
зона» берётся у самой сверки (`NO_CUE` — ни одна строка карты не произнесена
целиком), а не выдумывается ботом: иначе ответ, называющий другое нарушение,
переставил бы зону и молча потерял бы найденное.
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
    suggestion,
    text_message,
)

from src.bot import sidecar
from src.bot.app import build_dispatcher
from src.bot.config import BotSettings
from src.bot.texts import t
from src.bot.view import stored_headline
from src.domain import Finding, get_state, start_inspection

pytestmark = pytest.mark.asyncio

SETTINGS = BotSettings(token="unused-in-tests", allowed_ids=frozenset({AUDITOR_ID}), mode="polling")

#: Однозначная фраза синтетической карты: строка «Печь» плюс колонка «грязная»
#: → CLN05. Зону слова не называют — её подставит память, как на точке.
OVEN = "печь грязная"

#: Ответ той же формы, что в примере владельца: названо только МЕСТО. Объекта в
#: словах нет вовсе — сверке не за что зацепиться, и до T231 это уходило модели.
ZONE_ONLY = "это был холодный участок"

#: Ответ, называющий и место, и ОБЪЕКТ: ведёт в другой пункт и другую зону, то
#: есть правит ровно то, в чём система промахнулась. Прежняя дорога T204.
ZONE_AND_ITEM = "посудный участок, раковина и смеситель грязные"

#: Ответ, где место названо, объект назван, а СВЕРКА не сошлась: строка карты
#: «Печь» произнесена, но по словам не видно, грязь это или поломка. Отказ
#: сверки тут не «объекта нет», и правкой одной зоны такой ответ быть не может —
#: пункт обязан искаться заново.
ZONE_AND_UNCLEAR_ITEM = "холодный участок, печь, посмотри что тут"


def started(lang: str = "ru") -> None:
    start_inspection(CHAT_ID, "Белград 2", "planned", lang, ui_lang=lang)
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")


def findings() -> list[Finding]:
    state = get_state(CHAT_ID)
    return [] if state is None else list(state.findings)


async def запись(dp: object, bot: object, session: object) -> int:
    """Завести запись словами и вернуть номер сообщения бота о ней."""
    await feed(dp, bot, photo_message("frame-oven", caption=OVEN))  # type: ignore[arg-type]
    return session.last_sent_id  # type: ignore[attr-defined,no-any-return]


async def test_ответ_с_одной_зоной_переставляет_зону_записи(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Сердце задачи: место поправлено, записей по-прежнему одна."""
    started()
    stub_classify(monkeypatch, suggestion())
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    сказано = await запись(dp, bot, session)
    assert [(f.n, f.code, f.zone) for f in findings()] == [(1, "CLN05", "hot_kitchen")]

    await feed(dp, bot, text_message(ZONE_ONLY, reply_to=bot_message(сказано)))

    assert [(f.n, f.code, f.zone) for f in findings()] == [(1, "CLN05", "cold_kitchen")], (
        "ответ, назвавший зону, её не переставил"
    )


async def test_пункт_класс_текст_и_кадры_правкой_зоны_не_трогаются(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Аудитор поправил место, а не находку."""
    started()
    stub_classify(monkeypatch, suggestion())
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    сказано = await запись(dp, bot, session)
    было = findings()[0]

    await feed(dp, bot, text_message(ZONE_ONLY, reply_to=bot_message(сказано)))

    стало = findings()[0]
    # Сначала — что правка вообще случилась: без этой строки тест зеленел бы и
    # на боте, который ответ просто не понял и не тронул ничего.
    assert стало.zone == "cold_kitchen", "зона не переехала — тест проверяет не то"
    assert (стало.code, стало.level, стало.text, стало.photos) == (
        было.code,
        было.level,
        было.text,
        было.photos,
    ), "правка зоны переписала то, чего аудитор не менял"


async def test_правка_зоны_подтверждается_новой_отбивкой(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Молчаливая правка отчёта партнёру — правка, которой никто не видел.

    Отбивка та же, что у всякой правки ответом (T204), а не отбивка о
    сохранении: записи не прибавилось.
    """
    started()
    stub_classify(monkeypatch, suggestion())
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    сказано = await запись(dp, bot, session)
    session.clear()

    await feed(dp, bot, text_message(ZONE_ONLY, reply_to=bot_message(сказано)))

    показ = session.last_text
    assert t("record.corrected", "ru", n=1, line="", guess="", title="", note="", cue="")[:10] in (
        показ
    ), f"правка зоны не названа правкой: {показ!r}"
    assert stored_headline("ru") not in показ, "правка выдана за новую запись"
    assert "Холодный участок" in показ, "новая зона в отбивке не названа"
    assert t("record.fixed_zone_guess", "ru") not in показ, (
        "зона названа этими же словами — оговорка про догадку здесь неправда"
    )


async def test_правка_зоны_не_зовёт_модель(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Пункта в словах нет — искать его нечем, и платить за это незачем."""
    started()
    зовы = stub_classify(monkeypatch, suggestion())
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    сказано = await запись(dp, bot, session)

    await feed(dp, bot, text_message(ZONE_ONLY, reply_to=bot_message(сказано)))

    assert list(зовы) == [], "за правку зоны заплачен вызов модели"


async def test_ответ_с_объектом_ищет_пункт_заново(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Граница задачи: правка зоны не съедает правку пункта.

    Слова называют объект — работает прежняя дорога T204, и меняется не только
    зона, но и пункт. Спутай эти два случая, и ответ «в другом цехе течёт кран»
    переставил бы место, а найденное нарушение потерялось бы молча.
    """
    started()
    stub_classify(monkeypatch, suggestion())
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    сказано = await запись(dp, bot, session)

    await feed(dp, bot, text_message(ZONE_AND_ITEM, reply_to=bot_message(сказано)))

    assert [(f.code, f.zone) for f in findings()] == [("CLN02", "dishwashing")], (
        "ответ с объектом поправил только зону — пункт потерян"
    )


async def test_зона_не_из_списка_пункта_называется_вслух(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Правка — самый частый способ увести запись туда, где пункта нет (T147).

    Движок такую запись пропускает и лишь помечает флагом, а в отчёт партнёру
    пометка не попадает: единственное место, где её видно, — чат.
    """
    started()
    stub_classify(monkeypatch, suggestion())
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    сказано = await запись(dp, bot, session)
    session.clear()

    await feed(dp, bot, text_message("это был гостевой зал", reply_to=bot_message(сказано)))

    assert findings()[0].zone == "dining"
    assert findings()[0].zone_unusual, "тест проверяет не то: зона оказалась обычной для пункта"
    assert t("record.zone_unusual", "ru").strip() in session.last_text, (
        "нетипичная зона после правки ответом не названа"
    )


async def test_ответ_зоной_на_снятую_запись_не_заводит_новую(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Сообщения о снятых записях живут в переписке вечно, и отвечают на них."""
    started()
    stub_classify(monkeypatch, suggestion())
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    сказано = await запись(dp, bot, session)
    await feed(dp, bot, text_message("/undo"))
    session.clear()

    await feed(dp, bot, text_message(ZONE_ONLY, reply_to=bot_message(сказано)))

    assert findings() == [], "ответ на сообщение о снятой записи завёл новую"
    assert t("edit.gone", "ru", n=1) in session.last_text


async def test_отказ_сверки_не_по_отсутствию_объекта_зону_не_правит(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Правкой зоны считается ТОЛЬКО «объекта в словах нет» (`NO_CUE`), а не любой отказ.

    Сверка отказывает по-разному: строка карты задета, но непонятна колонка;
    строк несколько; пункт не той зоны. Во всех этих случаях объект НАЗВАН, и
    решает дальше модель. Спутай их с «объекта нет» — и ответ, называющий
    другое нарушение, тихо переставил бы место, а находку потерял.
    """
    started()
    зовы = stub_classify(
        monkeypatch, suggestion(candidate("TEH05", "D1", "cold_kitchen", "печь не греет"))
    )
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    сказано = await запись(dp, bot, session)

    await feed(dp, bot, text_message(ZONE_AND_UNCLEAR_ITEM, reply_to=bot_message(сказано)))

    assert len(зовы) == 1, "пункт не искался заново — ответ принят за правку одной зоны"
    assert [(f.code, f.zone) for f in findings()] == [("CLN05", "hot_kitchen")], (
        "запись изменилась до того, как аудитор выбрал пункт"
    )
    assert any(data.startswith("rec:pick:") for data in session.keyboard_data()), (
        "кандидаты модели не показаны"
    )
