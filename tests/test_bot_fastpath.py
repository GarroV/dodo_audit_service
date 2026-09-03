"""Фиксация словами: запись появляется сразу, без кнопки (T121, D064).

Владелец, дословно: «снимаем с текста подтверждение, потом добавим». Слова
аудитора, однозначно легшие на карту нарушений, больше не ждут нажатия — запись
появляется в тот же момент. Для кадра подтверждение остаётся: там пункт
угадывает система, а в словах зона и суть названы человеком.

Что защищает этот файл — четыре вещи, и все четыре про цену снятой кнопки.

**Модель не зовётся вовсе** (T117, D063). Проверяется не текстом ответа, а
списком вызовов подменённого разбора: пустой список и есть доказательство.

**Запись появляется без нажатия.** Раньше тесты требовали обратного
(`findings() == []` до кнопки) — теперь ровно наоборот, и это и есть T121.

**Показ обязан быть виден.** Оговорка, высказанная владельцу до решения:
сопоставление слов с пунктом промахивается, и без подтверждения промах станет
тихим. На его же примере «кассовая зона, просрочка чизкейк» слова поднимают
`CLN02` («оборудование в зоне мойки без загрязнений») вместо `PRD10`. Строка
`#1 CLN02 · D1 · Кассовая зона · 99.5%` такой промах не показывает никак: код
глазами не читается. Поэтому в показе обязаны стоять **вопрос пункта словами**,
**слова аудитора целиком** и **сработавшая строка карты** — три вещи, по
которым промах видно, — и тесты требуют каждую.

**Выход к модели остаётся.** Правка записи меняет зону, класс, формулировку и
удаляет запись, но НЕ код пункта. Значит, промах по коду чинится только
разбором заново, а те же слова снова поднимут тот же неверный пункт: без кнопки
«Разобрать моделью» рядом с записью аудитор оказался бы в петле.
"""

from __future__ import annotations

from typing import Any

import pytest
from bot_harness import (
    AUDITOR_ID,
    CHAT_ID,
    Calls,
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

from src.bot import sidecar
from src.bot.app import build_dispatcher
from src.bot.config import BotSettings
from src.bot.keyboards import EDIT_PREFIX, MODEL_CALLBACK, PICK_PREFIX
from src.bot.texts import t
from src.bot.view import percent, zone_title
from src.domain import SOURCE_COMMENT, Finding, get_item, get_state, score, start_inspection
from src.recognize.fastpath import (
    NO_COLUMN,
    NO_CUE,
    NO_ZONE,
    SEVERAL_ITEMS,
    FastPath,
    fast_path,
)

pytestmark = [pytest.mark.asyncio, requires_data]

SETTINGS = BotSettings(token="unused-in-tests", allowed_ids=frozenset({AUDITOR_ID}), mode="polling")

#: Однозначная фраза на боевой карте: строка «Печь» произнесена целиком,
#: колонка выбрана словом «грязная» → CLN05, единственный класс D1.
CLEAR = "печь грязная"

#: Правило 11 живьём: покрыта строка «Печь», а про пол сказано словом, которого
#: строке карты не хватает. Второе нарушение остаётся за кадром ответа — и
#: увидеть его аудитор может только в собственных словах. 185 знаков: хвост
#: заведомо за пределами предпросмотра в 160.
TWO_VIOLATIONS = (
    "Печь в нагаре, нагар по всему поду, отмывали похоже давно, я снял со всех сторон, "
    "потом ещё раз проверю на выходе, и это не всё что тут не так, потому что пол "
    "у входа в цех тоже грязный"
)


def spy_fast_path(monkeypatch: pytest.MonkeyPatch) -> Calls:
    """Считать вызовы сверки, не подменяя её: сама сверка остаётся настоящей.

    Нужен там, где проверяется не ответ, а сам факт обращения к карте: на голом
    кадре слов нет, сверять не с чем, и лезть за этим на диск незачем.
    """
    calls = Calls()

    def counted(note: str, zone_hint: str | None, **kw: Any) -> FastPath:
        calls.append((note, zone_hint))
        return fast_path(note, zone_hint, **kw)

    monkeypatch.setattr("src.bot.routers.record.fast_path", counted)
    return calls


def started() -> None:
    start_inspection(CHAT_ID, "Белград 2", "Плановая", "ru")


def findings() -> list[Finding]:
    state = get_state(CHAT_ID)
    return [] if state is None else list(state.findings)


async def test_однозначные_слова_ложатся_записью_без_нажатия_и_без_модели(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Смысл T121: аудитор написал словами — запись уже есть, нажимать нечего."""
    started()
    asked = stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen")))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")

    await feed(dp, bot, photo_message("frame-1", caption=CLEAR))

    assert asked == [], "модель звали, хотя слова однозначны — время потрачено впустую"
    assert t("record.thinking", "ru") not in session.texts, "«Разбираю…» без разбора"
    saved = findings()
    assert len(saved) == 1, "запись не появилась — подтверждение всё ещё требуется"
    assert (saved[0].code, saved[0].level, saved[0].zone) == ("CLN05", "D1", "hot_kitchen")


async def test_запись_ложится_словами_аудитора_с_источником_со_слов(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Текст записи — дословные слова аудитора, источник — «со слов» (D044)."""
    started()
    stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen")))
    bot, _session = make_bot()
    dp = build_dispatcher(SETTINGS)
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")

    await feed(dp, bot, photo_message("frame-1", caption=TWO_VIOLATIONS))

    saved = findings()
    assert len(saved) == 1
    assert saved[0].text == TWO_VIOLATIONS, "формулировку кто-то сочинил за аудитора"
    assert saved[0].source == SOURCE_COMMENT
    assert saved[0].photos == ["frame-1"], "кадр к записи не прикрепился"


async def test_показ_записи_называет_пункт_словами_а_не_только_кодом(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Без кнопки промах сопоставления виден только отсюда — значит, отсюда он и виден.

    Сообщение сверяется целиком, а не поиском подстрок. Проверено порчей на
    прошлой очереди: «строка карты» и вопрос чек-листа начинаются одинаково
    («Печь…»), и проверка `"Печь" in shown` оставалась зелёной с пустой строкой
    карты. Отдельной строкой — что вопрос пункта дошёл целиком: именно он
    отличает `CLN02` «оборудование в зоне мойки» от `PRD10` в примере владельца.
    """
    started()
    stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen")))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")

    await feed(dp, bot, photo_message("frame-1", caption=CLEAR))

    question = get_item("CLN05").question("ru")
    assert session.last_text == t(
        "record.fixed",
        "ru",
        line=t(
            "record.saved",
            "ru",
            n=1,
            code="CLN05",
            level="D1",
            zone=zone_title("hot_kitchen", "ru"),
            pct=percent(score(CHAT_ID).pct),
        ),
        title=question,
        note=CLEAR,
        cue="Печь",
    )
    assert question in session.last_text, "пункт назван одним кодом — промах не прочитать"


async def test_фраза_с_двумя_нарушениями_показывает_слова_целиком(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Правило 11: записано одно нарушение из двух — но слова не обрезаны.

    Механизм второе нарушение не находит и найти не может. Единственное, что
    отделяет потерю от осознанного решения аудитора, — его собственные слова
    целиком в том самом сообщении, где стоит запись.
    """
    started()
    stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen")))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")

    await feed(dp, bot, photo_message("frame-1", caption=TWO_VIOLATIONS))

    shown = session.last_text
    assert len(TWO_VIOLATIONS) > 160, "фраза короче предпросмотра — тест ничего не проверяет"
    assert TWO_VIOLATIONS in shown, "слова обрезаны — второе нарушение потерялось молча"
    assert "пол у входа в цех тоже грязный" in shown
    assert "…" not in shown, "многоточие: слова всё-таки урезаны"


async def test_под_записью_есть_и_правка_и_выход_к_модели(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Замена подтверждению: поправить или удалить — и разобрать заново.

    Правка кода пункта в чате не предусмотрена (`routers/edit.py`), а те же
    слова снова поднимут тот же пункт. Без «Разобрать моделью» рядом с записью
    неверный код чинился бы удалением и повтором по кругу.
    """
    started()
    stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen")))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")

    await feed(dp, bot, photo_message("frame-1", caption=CLEAR))

    buttons = session.keyboard_data()
    assert MODEL_CALLBACK in buttons, "выхода к модели нет — неверный код не починить"
    for what in ("zone", "level", "text", "drop"):
        assert f"{EDIT_PREFIX}1:{what}" in buttons, f"под записью нет правки «{what}»"


async def test_отказ_движка_не_оставляет_тупика_а_передаёт_модели(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Тот же пункт в той же зоне второй раз движок не берёт — и это обычный исход.

    Раньше отказ приходил на нажатие, и рядом оставались кнопки. Теперь нажатия
    нет, и молча упереться в отказ аудитор не должен: причина названа, а
    материал уходит в разбор моделью — там пункт можно выбрать другой.
    """
    started()
    asked = stub_classify(monkeypatch, suggestion(candidate("CLN12", "D1", "hot_kitchen")))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")

    await feed(dp, bot, photo_message("frame-1", caption=CLEAR))
    assert len(findings()) == 1
    session.clear()

    await feed(dp, bot, photo_message("frame-2", caption=CLEAR))

    assert len(findings()) == 1, "движок взял тот же пункт в ту же зону дважды"
    assert any(text.startswith("Не записал:") for text in session.texts), "отказ не назван"
    assert len(asked) == 1, "после отказа материал никуда не пошёл — тупик"
    assert f"{PICK_PREFIX}0" in session.keyboard_data()


async def test_кнопка_разобрать_моделью_отдаёт_модели_тот_же_материал(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Сверка дала не тот пункт — модель разбирает те же слова и тот же кадр."""
    started()
    asked = stub_classify(monkeypatch, suggestion(candidate("CLN12", "D1", "hot_kitchen")))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")

    await feed(dp, bot, photo_message("frame-1", caption=TWO_VIOLATIONS))
    assert asked == []
    session.clear()
    await feed(dp, bot, callback(MODEL_CALLBACK))

    assert len(asked) == 1, "кнопка «Разобрать моделью» модель не позвала"
    note, photo, zone, _lang = asked[0]
    assert note == TWO_VIOLATIONS
    assert photo is not None, "кадр до модели не доехал"
    assert zone == "hot_kitchen"
    assert "CLN12" in session.last_text
    assert len(findings()) == 1, "разбор моделью сам ничего не фиксирует"


async def test_разбор_моделью_после_быстрого_пути_не_зовёт_его_снова(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Иначе кнопка «Разобрать моделью» возвращала бы ту же запись по кругу."""
    started()
    stub_classify(monkeypatch, suggestion(candidate("CLN12", "D1", "hot_kitchen")))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")

    await feed(dp, bot, photo_message("frame-1", caption=CLEAR))
    assert len(findings()) == 1
    await feed(dp, bot, callback(MODEL_CALLBACK))

    assert len(findings()) == 1, "сверка сработала второй раз и удвоила запись"
    assert t("record.thinking", "ru") in session.texts


async def test_нажатие_без_предложения_отвечает_что_оно_устарело(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Бот перезапустился — кнопка под старой записью молчать не должна."""
    started()
    stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen")))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, callback(MODEL_CALLBACK))

    assert session.last_text == t("record.stale", "ru")


async def test_выход_к_модели_поверх_кандидатов_модели_устарел(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Кнопка из-под прошлой записи не трогает чужой материал.

    Живое предложение в чате есть, но оно от модели: быстрого пункта в нём нет.
    Нажатие обязано ответить «устарело», а не разбирать заново чужие слова.
    """
    started()
    stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen")))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")

    await feed(dp, bot, photo_message("frame-1", caption="тут непорядок"))
    assert f"{PICK_PREFIX}0" in session.keyboard_data(), "нужно живое предложение от модели"

    session.clear()
    await feed(dp, bot, callback(MODEL_CALLBACK))
    assert session.last_text == t("record.stale", "ru")
    assert findings() == []


async def test_без_названной_зоны_ничего_не_фиксируется_само(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Зону определяет аудитор (правило 6), а не догадка по словам."""
    started()
    asked = stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen")))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, photo_message("frame-1", caption=CLEAR))

    assert len(asked) == 1, "без зоны разбирать обязана модель"
    assert findings() == [], "запись появилась сама, хотя зона не названа"
    assert f"{PICK_PREFIX}0" in session.keyboard_data()


async def test_кадр_без_слов_фиксируется_только_подтверждением(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Граница D064: подтверждение снято с текста, но не с кадра.

    Слов нет — сверять не с чем; «Разобрать» остаётся вызовом модели (D046), и
    запись появляется только после нажатия на кандидата. Карту при этом не
    читают вовсе: сверять пустые слова со списком нечем, а чтение методики и
    карты стоит миллисекунды в цикле событий.
    """
    started()
    asked = stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen")))
    checked = spy_fast_path(monkeypatch)
    bot, _session = make_bot()
    dp = build_dispatcher(SETTINGS)
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")

    await feed(dp, bot, photo_message("frame-1", message_id=501))
    await feed(dp, bot, callback("rec:analyze:501"))

    assert len(asked) == 1
    assert asked[0][0] == ""
    assert checked == [], "сверка со списком звалась на пустых словах — впустую"
    assert findings() == [], "кадр записался сам — подтверждение по кадру снято, а не должно"

    await feed(dp, bot, callback(f"{PICK_PREFIX}0"))
    assert len(findings()) == 1, "по нажатию запись так и не появилась"


async def test_неоднозначные_слова_идут_модели_как_раньше(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Отказ быстрого пути — не сбой: дальше всё как было."""
    started()
    asked = stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen")))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")

    await feed(dp, bot, photo_message("frame-1", caption="печь"))

    assert [call[0] for call in asked] == ["печь"]
    assert f"{PICK_PREFIX}0" in session.keyboard_data()
    assert findings() == [], "неоднозначные слова записались сами"


async def test_причина_отказа_человеку_не_показывается(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`reason` — строка для замера и разбора, а не сообщение аудитору."""
    started()
    stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen")))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")

    await feed(dp, bot, photo_message("frame-1", caption="тут непорядок"))
    await feed(dp, bot, photo_message("frame-2", caption="печь"))

    shown = "\n".join(session.texts)
    for reason in (NO_CUE, NO_COLUMN, SEVERAL_ITEMS):
        assert reason not in shown, f"диагностика уехала на экран: {reason}"


async def test_фиксация_словами_работает_и_на_комментарии_следом(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Связка «кадр, потом комментарий» (T053) — тот же поток, та же запись сразу."""
    started()
    asked = stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen")))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")

    await feed(dp, bot, photo_message("frame-1", message_id=601))
    await feed(dp, bot, text_message(CLEAR))

    assert asked == []
    assert len(findings()) == 1
    assert findings()[0].photos == ["frame-1"], "кадр к записи не прикрепился"
    assert NO_ZONE not in "\n".join(session.texts)
