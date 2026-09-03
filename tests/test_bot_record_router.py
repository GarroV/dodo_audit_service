"""Разбор по кнопке и фиксация по подтверждению (T055, T057, T067).

Главное, что защищает этот файл, — два запрета.

**Ни один кадр не уходит в модель без нажатия** (решение D046). Проверяется не
текстом ответа, а списком вызовов подменённого разбора: пустой список и есть
доказательство, что модель не звали. Требование денежное — оно защищает от
расхода на разборы, которых аудитор не просил.

**Ни одна запись не появляется без подтверждения** (принцип проекта «модель
предлагает, фиксирует человек»). Поэтому после показа кандидатов состояние
проверки проверяется на пустоту, и только нажатие кнопки её наполняет.

Разбор подменён (`stub_classify`): до модели тесты не ходят ни разу.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import pytest
from aiogram.types import Chat, Message
from bot_harness import (
    AUDITOR_ID,
    CHAT_ID,
    candidate,
    feed,
    make_bot,
    manual,
    photo_message,
    stub_classify,
    stub_manual,
    suggestion,
    text_message,
    voice_message,
)
from bot_harness import callback_query as callback
from conftest import requires_data

from src.bot import sidecar
from src.bot.app import build_dispatcher
from src.bot.config import BotSettings
from src.bot.pending import Offer
from src.bot.routers.record import _drop_question
from src.bot.texts import t
from src.domain import SOURCE_COMMENT, SOURCE_PHOTO, Finding, get_state, score, start_inspection
from src.domain.config import check_environment
from src.domain.engine import state_file
from src.domain.errors import EngineError
from src.recognize.errors import ModelUnavailable
from src.recognize.models import UNKNOWN_ZONE

pytestmark = [pytest.mark.asyncio, requires_data]

SETTINGS = BotSettings(token="unused-in-tests", allowed_ids=frozenset({AUDITOR_ID}), mode="polling")


def started() -> None:
    start_inspection(CHAT_ID, "Белград 2", "Плановая", "ru")


def findings() -> list[Finding]:
    state = get_state(CHAT_ID)
    return [] if state is None else list(state.findings)


async def test_bare_frame_only_asks_and_never_calls_the_model(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Кадр без комментария сам собой не разбирается (D046): вопрос есть, вызова нет."""
    started()
    asked = stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen")))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, photo_message("frame-1", message_id=501))

    assert session.last_text == t("material.photo_taken", "ru", count=1)
    assert session.keyboard_data() == ["rec:analyze:501"]
    assert asked == [], "кадр ушёл в модель без нажатия «Разобрать» — это расход впустую"


async def test_analyze_button_sends_the_frame_with_an_empty_comment(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """По нажатию разбирается именно голый кадр: комментария нет, байты кадра есть."""
    started()
    asked = stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen")))
    bot, _ = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, photo_message("frame-1", message_id=501))
    await feed(dp, bot, callback("rec:analyze:501"))

    assert len(asked) == 1
    note, photo, _zone, _lang = asked[0]
    assert note == ""
    assert photo is not None, "разбор голого кадра без самого кадра не имеет смысла"


async def test_comment_after_the_frame_cancels_the_question(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Слова аудитора сильнее догадки по картинке: вопрос снимается, кнопка гаснет."""
    started()
    asked = stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen")))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, photo_message("frame-1", message_id=501))
    await feed(dp, bot, text_message("печь в горячем цеху в нагаре"))

    assert [type(c).__name__ for c in session.calls].count("EditMessageReplyMarkup") == 1
    assert asked[-1][0] == "печь в горячем цеху в нагаре"

    session.clear()
    await feed(dp, bot, callback("rec:analyze:501"))
    assert session.last_text == t("record.analyze_gone", "ru")


async def test_candidates_are_shown_but_nothing_is_recorded_yet(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Показ — это не фиксация: без нажатия в проверке по-прежнему пусто."""
    started()
    stub_classify(
        monkeypatch,
        suggestion(
            candidate("CLN05", "D1", "hot_kitchen", "Печь в нагаре"),
            candidate("PRD01", "D2", "hot_kitchen", "Продукт размораживается на столе"),
        ),
    )
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, photo_message("frame-1", caption="печь грязная"))

    assert "CLN05" in session.last_text and "PRD01" in session.last_text
    assert session.keyboard_data()[:2] == ["rec:pick:0", "rec:pick:1"]
    assert findings() == []


async def test_confirmation_records_and_answers_with_one_line(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Подтверждение — одна строка: пункт, класс, зона, накопленный процент (T055)."""
    started()
    stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen", "Печь в нагаре")))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, photo_message("frame-1", caption="печь грязная"))
    session.clear()
    await feed(dp, bot, callback("rec:pick:0"))

    saved = findings()
    assert len(saved) == 1
    line = session.texts[0]
    assert line.count("\n") == 0, "подтверждение обязано быть одной строкой, без таблицы"
    for part in ("#1", "CLN05", "D1", "Горячий цех", f"{score(CHAT_ID).pct:.1f}%"):
        assert part in line


async def test_frames_are_attached_to_the_record(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Кадры альбома прикрепляются к одной записи — доказательство не теряется."""
    started()
    stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen", "Печь в нагаре")))
    bot, _ = make_bot()
    dp = build_dispatcher(SETTINGS, album_window=0.01)

    await feed(dp, bot, photo_message("album-1", media_group_id="a1", caption="печь грязная"))
    await feed(dp, bot, photo_message("album-2", media_group_id="a1"))
    await feed(dp, bot, text_message("следующее событие закрывает альбом"))
    await feed(dp, bot, callback("rec:pick:0"))

    state = get_state(CHAT_ID)
    assert state is not None
    assert state.findings[0].photos == ["album-1", "album-2"]


async def test_source_of_the_record_is_remembered(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Решение D044: за формулировку со слов и за догадку по кадру ответственность разная.

    Источник хранится в самой проверке (T108), а не в заметках бота: заметки
    начинаются с нуля с каждой новой проверкой этого чата, и пометка догадки
    исчезала бы вместе с ними.
    """
    started()
    stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen", "Печь в нагаре")))
    bot, _ = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, photo_message("frame-1", caption="печь грязная"))
    await feed(dp, bot, callback("rec:pick:0"))

    # Второй пункт другой: пара «пункт + зона» уникальна, и повтор движок
    # справедливо отвергнет — источник тут ни при чём.
    stub_classify(
        monkeypatch, suggestion(candidate("PRD01", "D1", "fridge", "Разморозка на столе"))
    )
    await feed(dp, bot, photo_message("frame-2", message_id=777))
    await feed(dp, bot, callback("rec:analyze:777"))
    await feed(dp, bot, callback("rec:pick:0"))

    записи = findings()
    assert len(записи) == 2
    assert [f.source for f in записи] == [SOURCE_COMMENT, SOURCE_PHOTO]

    заметки = json.loads(sidecar.notes_path(CHAT_ID).read_text(encoding="utf-8"))
    assert "sources" not in заметки, (
        "источник остался и в заметках бота — две правды об одном и том же разъедутся"
    )


async def test_the_source_set_at_recording_reaches_the_proofreading(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T108: источник, поставленный при фиксации, доезжает до предвычитки отчёта.

    Сквозной путь целиком, от кадра до `/finish`, и проверяется он там, где
    пометку читает аудитор, — в списке зафиксированного. Двух половин по
    отдельности мало: и запись с верным источником, и правильно собранная
    строка ничего не стоят, если между ними источник теряется.
    """
    started()
    stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen", "Печь в нагаре")))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, photo_message("frame-1", message_id=901))
    await feed(dp, bot, callback("rec:analyze:901"))
    await feed(dp, bot, callback("rec:pick:0"))
    session.clear()
    await feed(dp, bot, text_message("/finish"))

    строки = [s for s in "\n".join(session.texts).splitlines() if s.startswith("#")]
    assert len(строки) == 1, "зафиксированная запись не дошла до предвычитки"
    assert t("finish.source_photo", "ru").strip() in строки[0], (
        "запись, собранную по одному кадру без единого слова аудитора, "
        "предвычитка выдаёт за сказанное им"
    )


async def test_last_zone_is_remembered_and_offered_as_a_guess(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Решение D048: зона из слов запоминается и подставляется следующему разбору."""
    started()
    asked = stub_classify(
        monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen", "Печь в нагаре"))
    )
    bot, _ = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, photo_message("frame-1", caption="печь в горячем цеху грязная"))
    assert asked[0][2] is None, "первой зоны взять неоткуда — догадки быть не должно"
    await feed(dp, bot, callback("rec:pick:0"))

    await feed(dp, bot, photo_message("frame-2", caption="и стена там же"))
    assert asked[1][2] == "hot_kitchen"
    assert sidecar.read(CHAT_ID).zone == "hot_kitchen"


async def test_skip_records_nothing_and_promises_to_show_the_frame_later(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Отказ от всех кандидатов — не молчание: кадр всплывёт при завершении (T068)."""
    started()
    stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen")))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, photo_message("frame-1", caption="печь грязная"))
    session.clear()
    await feed(dp, bot, callback("rec:skip"))

    assert session.last_text == t("record.skipped", "ru")
    assert findings() == []


async def test_candidate_without_a_zone_asks_for_it_by_button(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Зону выводить из вида кадра нельзя (правило 6) — её называют кнопкой."""
    started()
    stub_classify(monkeypatch, suggestion(candidate("INF11", "D0", UNKNOWN_ZONE, "Фото продукта")))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, photo_message("frame-1", caption="вот продукт"))
    await feed(dp, bot, callback("rec:pick:0"))

    assert session.last_text == t("record.ask_zone", "ru")
    assert "rec:zp:dining" in session.keyboard_data()
    assert findings() == []

    await feed(dp, bot, callback("rec:zp:dining"))
    state = get_state(CHAT_ID)
    assert state is not None
    assert state.findings[0].zone == "dining"


async def test_measurement_is_a_finding_with_d0_and_not_the_info_block(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Задача T057: замеры лежат записями среди находок, блоком `info` бот не пользуется."""
    started()
    stub_classify(
        monkeypatch,
        suggestion(candidate("INF10", "D0", "fridge", "Температура холодильника +4 °C")),
    )
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    before = score(CHAT_ID).pct
    await feed(dp, bot, photo_message("frame-1", caption="температура холодильника плюс четыре"))
    session.clear()
    await feed(dp, bot, callback("rec:pick:0"))

    state = get_state(CHAT_ID)
    assert state is not None
    assert state.findings[0].code == "INF10"
    assert state.findings[0].level == "D0"
    assert "замер" in session.texts[0]
    # Информационная запись не нарушение: процент от неё не меняется.
    assert score(CHAT_ID).pct == before

    raw = json.loads(state_file(CHAT_ID, check_environment()).read_text(encoding="utf-8"))
    assert not raw.get("info"), "блок `info` движка бот использовать не должен"


async def test_model_outage_falls_back_to_manual_pick(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Недоступная модель не останавливает проверку: пункт выбирается кнопками (T034)."""
    started()
    stub_classify(monkeypatch, ModelUnavailable("таймаут"))
    stub_manual(monkeypatch, (manual("CLN05", ("D1",), "Печь без загрязнений"),))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    sidecar.remember_zone(CHAT_ID, "hot_kitchen")
    await feed(dp, bot, photo_message("frame-1", caption="печь грязная"))

    assert "таймаут" in session.texts[-2]
    assert "rec:mi:0" in session.keyboard_data()

    await feed(dp, bot, callback("rec:mi:0"))
    state = get_state(CHAT_ID)
    assert state is not None
    assert state.findings[0].code == "CLN05"
    # Единственный разрешённый класс не спрашивается: лишний вопрос на точке.
    assert state.findings[0].level == "D1"
    # Формулировкой стали слова аудитора — лучшего источника без модели нет.
    assert state.findings[0].text == "печь грязная"


async def test_manual_pick_asks_for_the_class_when_there_is_a_choice(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Два разрешённых класса — выбирает человек, а не бот за него."""
    started()
    stub_classify(monkeypatch, ModelUnavailable("нет ключа"))
    stub_manual(monkeypatch, (manual("PRD01", ("D1", "D2"), "Разморозка продуктов"),))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    sidecar.remember_zone(CHAT_ID, "fridge")
    await feed(dp, bot, photo_message("frame-1", caption="продукт размораживается на столе"))
    await feed(dp, bot, callback("rec:mi:0"))

    assert session.keyboard_data() == ["rec:ml:0:D1", "rec:ml:0:D2"]
    assert findings() == []

    await feed(dp, bot, callback("rec:ml:0:D2"))
    state = get_state(CHAT_ID)
    assert state is not None
    assert state.findings[0].level == "D2"


async def test_stale_button_after_restart_says_so_instead_of_recording(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Кнопка под предложением, которого бот уже не помнит, не фиксирует ничего."""
    started()
    stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen")))
    bot, session = make_bot()

    first = build_dispatcher(SETTINGS)
    await feed(first, bot, photo_message("frame-1", caption="печь грязная"))

    second = build_dispatcher(SETTINGS)
    session.clear()
    await feed(second, bot, callback("rec:pick:0"))

    assert session.last_text == t("record.stale", "ru")
    assert findings() == []


async def test_flagged_wording_is_marked_for_the_auditor(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Претензия к формулировке видна: подтвердить не глядя такую запись нельзя."""
    started()
    stub_classify(
        monkeypatch,
        suggestion(
            candidate(
                "CLN05", "D1", "hot_kitchen", "Печь в нагаре по всей кухне", flags=("масштаб",)
            )
        ),
    )
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, photo_message("frame-1", caption="печь грязная"))

    assert "⚠" in session.last_text


async def test_empty_answer_opens_the_manual_list(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Пустой ответ модели — валидный: бот переспрашивает человека, а не выдумывает."""
    started()
    stub_classify(monkeypatch, suggestion(question="Что именно на кадре загрязнено?"))
    stub_manual(monkeypatch, (manual("CLN05", ("D1",), "Печь без загрязнений"),))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    sidecar.remember_zone(CHAT_ID, "hot_kitchen")
    await feed(dp, bot, photo_message("frame-1", caption="тут грязно"))

    assert any("Что именно на кадре загрязнено?" in text for text in session.texts)
    assert "rec:mi:0" in session.keyboard_data()
    assert findings() == []


async def test_voice_comment_also_cancels_the_analyze_question(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Голосовое — такой же комментарий: вопрос по кадру снимается им же (D046)."""
    started()
    stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen")))
    monkeypatch.setattr("src.bot.routers.record.transcribe", lambda audio, **kw: "печь грязная")
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, photo_message("frame-1", message_id=601))
    await feed(dp, bot, voice_message("voice-1"))
    session.clear()
    await feed(dp, bot, callback("rec:analyze:601"))

    assert session.last_text == t("record.analyze_gone", "ru")


async def test_measurement_stays_a_measurement_after_an_edit(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Смена зоны не превращает замер в нарушение — пометка держится и после правки.

    Найдено просмотром живого разговора, а не тестом: строка правки собиралась
    одним шаблоном на все классы, и у записи `D0` пометка «замер» пропадала.
    """
    started()
    stub_classify(
        monkeypatch,
        suggestion(candidate("INF10", "D0", "fridge", "Температура холодильника +4 °C")),
    )
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, photo_message("frame-1", caption="температура холодильника"))
    await feed(dp, bot, callback("rec:pick:0"))
    session.clear()
    await feed(dp, bot, callback("ez:1:freezer"))

    assert "замер" in session.last_text


async def test_refused_record_keeps_the_other_candidates_on_the_table(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Отказ движка не выбрасывает предложение: аудитор выбирает другого кандидата.

    Тот же пункт в той же зоне аудитор снимает дважды за обход — движок вторую
    запись справедливо отвергает. Если бы предложение при этом пропадало, кадр
    пришлось бы присылать заново, стоя на точке.
    """
    started()
    stub_classify(
        monkeypatch,
        suggestion(
            candidate("CLN05", "D1", "hot_kitchen", "Печь в нагаре"),
            candidate("PRD01", "D1", "fridge", "Продукт без маркировки"),
        ),
    )
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    # Пункт уже занят: этот же пункт в этой же зоне зафиксирован раньше.
    await feed(dp, bot, photo_message("frame-0", caption="печь грязная"))
    await feed(dp, bot, callback("rec:pick:0"))

    await feed(dp, bot, photo_message("frame-1", caption="печь всё ещё грязная"))
    session.clear()
    await feed(dp, bot, callback("rec:pick:0"))
    assert session.last_text.startswith("Не записал")

    # Второй кандидат по-прежнему жив.
    await feed(dp, bot, callback("rec:pick:1"))
    state = get_state(CHAT_ID)
    assert state is not None
    assert [f.code for f in state.findings] == ["CLN05", "PRD01"]


# --- пути отказа: что бот делает, когда что-то пошло не так ---


async def test_button_without_an_inspection_does_not_crash_on_the_language(
    domain_env: object,
) -> None:
    """Нажатие в чате без начатой проверки: язык брать неоткуда, но падать нельзя.

    Так бывает после «Начать новую»: кнопки прошлой проверки остаются в
    переписке, а состояния под ними уже нет.
    """
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, callback("rec:skip"))

    assert session.last_text == t("record.skipped", "ru")


@pytest.mark.parametrize(
    "data", ["rec:zp:hot_kitchen", "rec:zm:hot_kitchen", "rec:manual", "rec:mp:1", "rec:ml:0:D1"]
)
async def test_buttons_without_a_remembered_proposal_say_it_is_stale(
    domain_env: object, data: str
) -> None:
    """Кнопка под предложением, которого бот не помнит, не фиксирует ничего.

    Предложения живут в памяти намеренно (`src/bot/pending.py`), поэтому после
    перезапуска бота такие нажатия — обычное дело, а не сбой.
    """
    started()
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, callback(data))

    assert session.last_text == t("record.stale", "ru")
    assert findings() == []


async def test_undelivered_voice_asks_to_type_instead_of_analyzing_silence(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Голосовое не скачалось — просим текстом, а не разбираем пустоту как комментарий."""
    started()
    asked = stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen")))

    async def не_скачалось(*_a: object, **_k: object) -> bytes | None:
        return None

    monkeypatch.setattr("src.bot.routers.record.fetch_bytes", не_скачалось)
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, photo_message("frame-1"))
    await feed(dp, bot, voice_message("voice-1"))

    assert session.last_text == t("record.voice_not_downloaded", "ru")
    assert asked == [], "пустой комментарий ушёл в модель — это разбор молчания"


async def test_question_from_the_model_is_shown_above_the_candidates(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Уточняющий вопрос не теряется, когда кандидаты всё-таки есть.

    Правило 7 методики: непонятно — спроси. Кандидаты рядом с вопросом означают
    «вот на что это похоже», и прятать вопрос за ними нельзя.
    """
    started()
    stub_classify(
        monkeypatch,
        suggestion(
            candidate("CLN05", "D1", "hot_kitchen", "Печь в нагаре"),
            question="Это подина печи или под конвейером?",
        ),
    )
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, photo_message("frame-1", caption="тут нагар"))

    показано = session.last_text
    assert "Это подина печи или под конвейером?" in показано
    assert показано.index("подина") < показано.index("CLN05"), "вопрос спрятан под кандидатами"


async def test_frame_that_did_not_attach_is_not_lost_silently(
    domain_env: object, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Кадр не прикрепился — запись остаётся, а кадр всплывает при завершении (T068).

    Терять запись из-за одного кадра нельзя: аудитор её уже подтвердил. Но и
    молчать нельзя — иначе отчёт уйдёт партнёру без доказательства, и никто об
    этом не узнает.
    """
    started()
    stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen", "Нагар")))

    def не_прикрепился(*_a: object, **_k: object) -> None:
        raise EngineError("движок отверг кадр", code=1, command="photo")

    monkeypatch.setattr("src.domain.attach_photo", не_прикрепился)
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    with caplog.at_level(logging.ERROR, logger="src.bot.routers.record"):
        await feed(dp, bot, photo_message("orphan-frame", message_id=811, caption="нагар на печи"))
        await feed(dp, bot, callback("rec:pick:0"))

    assert any("не прикрепился" in r.message for r in caplog.records)
    state = get_state(CHAT_ID)
    assert state is not None
    assert len(state.findings) == 1, "запись потеряна из-за кадра"
    assert state.findings[0].photos == []

    session.clear()
    await feed(dp, bot, text_message("/finish"))
    assert t("finish.unclaimed_line", "ru", message_id=811) in "\n".join(session.texts)


async def test_telegram_refusing_to_remove_the_button_does_not_stop_the_analysis(
    domain_env: object, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Кнопку снять не удалось — это в журнал, но разбор по комментарию идёт дальше.

    Кнопка вторична: ронять из-за неё разбор означало бы потерять комментарий,
    который аудитор уже произнёс.
    """
    started()
    stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen", "Нагар")))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, photo_message("frame-1", message_id=822))

    async def телеграм_отказал(*_a: object, **_k: object) -> None:
        raise RuntimeError("message is not modified")

    monkeypatch.setattr(bot, "edit_message_reply_markup", телеграм_отказал)
    session.clear()
    with caplog.at_level(logging.WARNING, logger="src.bot.routers.record"):
        await feed(dp, bot, text_message("нагар на печи"))

    assert any("снять кнопку" in r.message for r in caplog.records)
    assert "CLN05" in session.last_text, "разбор не дошёл до аудитора"


async def test_question_is_not_removed_when_there_is_nothing_to_remove() -> None:
    """Защита `_drop_question` от отсутствующих бота и номера сообщения.

    Вызов прямой, а не через диспетчер, и это осознанно: обе величины приходят
    от телеграма, и заставить настоящий диспетчер отдать их пустыми нельзя —
    защита существует ровно на случай, когда он всё-таки отдаст.
    """
    голое = Message(
        message_id=1,
        date=datetime.now(tz=timezone.utc),
        chat=Chat(id=CHAT_ID, type="private"),
    )
    await _drop_question(голое, CHAT_ID, Offer(anchor_id=1, file_ids=("a",), question_id=7))

    bot, session = make_bot()
    await _drop_question(
        голое.as_(bot), CHAT_ID, Offer(anchor_id=1, file_ids=("a",), question_id=None)
    )
    assert session.calls == [], "бот полез в телеграм, когда снимать было нечего"
