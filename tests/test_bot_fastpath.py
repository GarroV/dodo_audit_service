"""Быстрый путь в разговоре: пункт без модели, когда слова однозначны (T117, D063).

Владелец: «у нас тут не нужны размышления, а нужна сверка с текущим списком
нарушений». Замер блока `recognize`: разбор кадра моделью идёт 5.3 с, текста —
4.3 с, то есть ждём именно рассуждение. Быстрый путь его пропускает.

Главное, что защищает этот файл, — три вещи, и все три про доверие к кнопке.

**Модель не зовётся вовсе.** Проверяется не текстом ответа, а списком вызовов
подменённого разбора: пустой список и есть доказательство. Иначе быстрый путь
экономил бы только слова, а не секунды.

**Слова аудитора показываются целиком.** Это не оформление. Правило 11
(`docs/03-recording-rules.md`): в одной фразе может быть два нарушения, а
быстрый путь покажет один пункт. Обрезанные слова спрятали бы второе нарушение
ровно там, где аудитор должен его заметить, — поэтому тест берёт фразу длиннее
предпросмотра (160 знаков) и требует хвост дословно.

**`reason` не доходит до экрана.** Это диагностика для замера
(`tools/fastpath_measure.py`), и человеку она не адресована.
"""

from __future__ import annotations

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

from src.bot import sidecar
from src.bot.app import build_dispatcher
from src.bot.config import BotSettings
from src.bot.keyboards import FAST_CALLBACK, MODEL_CALLBACK, SKIP_CALLBACK
from src.bot.texts import t
from src.domain import SOURCE_COMMENT, Finding, get_state, start_inspection
from src.recognize.fastpath import NO_COLUMN, NO_CUE, NO_ZONE, SEVERAL_ITEMS

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


def started() -> None:
    start_inspection(CHAT_ID, "Белград 2", "Плановая", "ru")


def findings() -> list[Finding]:
    state = get_state(CHAT_ID)
    return [] if state is None else list(state.findings)


async def test_однозначные_слова_фиксируются_без_единого_вызова_модели(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Смысл задачи: пункт показан сразу, модель не звали ни разу."""
    started()
    asked = stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen")))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")

    await feed(dp, bot, photo_message("frame-1", caption=CLEAR))

    assert asked == [], "модель звали, хотя слова однозначны — время потрачено впустую"
    assert t("record.thinking", "ru") not in session.texts, "«Разбираю…» без разбора"
    assert "CLN05" in session.last_text
    assert findings() == [], "запись появилась без подтверждения"


async def test_рядом_с_кнопкой_видны_слова_аудитора_и_строка_карты(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Из чего сделан вывод, видно человеку, а не только логу."""
    started()
    stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen")))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")

    await feed(dp, bot, photo_message("frame-1", caption=CLEAR))

    shown = session.last_text
    assert CLEAR in shown, "слов аудитора рядом с кнопкой нет"
    assert "Печь" in shown, "сработавшей строки карты рядом с кнопкой нет"


async def test_фраза_с_двумя_нарушениями_показывает_слова_целиком(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Правило 11: ответ на одно нарушение из двух — но слова не обрезаны.

    Механизм второе нарушение не находит и найти не может. Единственное, что
    отделяет потерю от осознанного решения аудитора, — его собственные слова
    целиком и кнопка «Разобрать моделью» рядом.
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
    assert MODEL_CALLBACK in session.keyboard_data(), "выхода на модель нет — аудитор зажат"


async def test_кнопка_записать_фиксирует_словами_аудитора_со_слов(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Текст записи — слова аудитора, источник — «со слов аудитора» (D044)."""
    started()
    stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen")))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")

    await feed(dp, bot, photo_message("frame-1", caption=TWO_VIOLATIONS))
    await feed(dp, bot, callback(FAST_CALLBACK))

    saved = findings()
    assert len(saved) == 1
    assert (saved[0].code, saved[0].level, saved[0].zone) == ("CLN05", "D1", "hot_kitchen")
    assert saved[0].text == TWO_VIOLATIONS, "формулировку кто-то сочинил за аудитора"
    assert saved[0].source == SOURCE_COMMENT
    assert saved[0].photos == ["frame-1"], "кадр к записи не прикрепился"
    assert session.last_text.startswith("#1 CLN05 · D1 ·")


async def test_записать_второй_раз_нечего_предложение_забрано(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Одно предложение — одна запись: второе нажатие не удваивает находку."""
    started()
    stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen")))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")

    await feed(dp, bot, photo_message("frame-1", caption=CLEAR))
    await feed(dp, bot, callback(FAST_CALLBACK))
    await feed(dp, bot, callback(FAST_CALLBACK))

    assert len(findings()) == 1
    assert session.last_text == t("record.stale", "ru")


async def test_кнопка_разобрать_моделью_отдаёт_модели_тот_же_материал(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Быстрый путь показал не то — модель разбирает те же слова и тот же кадр."""
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
    assert findings() == [], "разбор моделью сам ничего не фиксирует"


async def test_разбор_моделью_после_быстрого_пути_не_зовёт_его_снова(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Иначе кнопка «Разобрать моделью» возвращала бы тот же быстрый ответ по кругу."""
    started()
    stub_classify(monkeypatch, suggestion(candidate("CLN12", "D1", "hot_kitchen")))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")

    await feed(dp, bot, photo_message("frame-1", caption=CLEAR))
    await feed(dp, bot, callback(MODEL_CALLBACK))

    assert FAST_CALLBACK not in session.keyboard_data(), "быстрый путь сработал второй раз"
    assert t("record.thinking", "ru") in session.texts


async def test_не_записывать_снимает_быстрое_предложение(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Отказ от предложения — обычный исход, и кадр при этом не теряется (T068)."""
    started()
    stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen")))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")

    await feed(dp, bot, photo_message("frame-1", caption=CLEAR))
    await feed(dp, bot, callback(SKIP_CALLBACK))
    await feed(dp, bot, callback(FAST_CALLBACK))

    assert findings() == []
    assert session.last_text == t("record.stale", "ru")


async def test_нажатия_без_предложения_отвечают_что_оно_устарело(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Бот перезапустился — кнопки под старым сообщением молчать не должны."""
    started()
    stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen")))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, callback(FAST_CALLBACK))
    assert session.last_text == t("record.stale", "ru")
    await feed(dp, bot, callback(MODEL_CALLBACK))
    assert session.last_text == t("record.stale", "ru")


async def test_разобрать_моделью_поверх_обычных_кандидатов_устарело(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Кнопка из-под прошлого быстрого предложения не разбирает чужой материал."""
    started()
    stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen")))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")

    await feed(dp, bot, photo_message("frame-1", caption="тут непорядок"))
    session.clear()
    await feed(dp, bot, callback(MODEL_CALLBACK))

    assert session.last_text == t("record.stale", "ru")


async def test_без_названной_зоны_быстрый_путь_молчит(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Зону определяет аудитор (правило 6), а не догадка по словам."""
    started()
    asked = stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen")))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, photo_message("frame-1", caption=CLEAR))

    assert len(asked) == 1, "без зоны разбирать обязана модель"
    assert FAST_CALLBACK not in session.keyboard_data()


async def test_голый_кадр_по_кнопке_разобрать_идёт_в_модель(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Слов нет — сверять не с чем; «Разобрать» остаётся вызовом модели (D046)."""
    started()
    asked = stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen")))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")

    await feed(dp, bot, photo_message("frame-1", message_id=501))
    await feed(dp, bot, callback("rec:analyze:501"))

    assert len(asked) == 1
    assert asked[0][0] == ""
    assert FAST_CALLBACK not in session.keyboard_data()


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
    assert "rec:pick:0" in session.keyboard_data()


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


async def test_быстрый_путь_работает_и_на_комментарии_следом(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Связка «кадр, потом комментарий» (T053) — тот же поток, тот же быстрый путь."""
    started()
    asked = stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen")))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")

    await feed(dp, bot, photo_message("frame-1", message_id=601))
    await feed(dp, bot, text_message(CLEAR))

    assert asked == []
    assert FAST_CALLBACK in session.keyboard_data()
    assert NO_ZONE not in "\n".join(session.texts)
