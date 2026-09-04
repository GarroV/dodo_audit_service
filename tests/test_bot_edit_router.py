"""Тесты правок записи прямо в чате (T056): `src/bot/routers/edit.py`.

События идут через настоящий диспетчер (`tests/bot_harness.py`), хендлеры
напрямую не зовутся — иначе мимо тестов прошли бы фильтры, порядок роутеров и
конечный автомат правки формулировки, где и живут настоящие ошибки диалога.

Записи заводятся напрямую `domain.add_finding` — разбор кадра для этих тестов
не нужен, правится уже зафиксированное. Процент везде берётся заново у
движка (`domain.score`), а не пересчитывается в тесте: вторая копия правил
методики в тесте — тот же грех, что и в коде продукта.
"""

from __future__ import annotations

import pytest
from bot_harness import (
    AUDITOR_ID,
    CHAT_ID,
    callback_query,
    feed,
    make_bot,
    photo_message,
    text_message,
)

from src import domain
from src.bot import sidecar
from src.bot.app import build_dispatcher
from src.bot.config import BotSettings
from src.bot.texts import t
from src.bot.view import zone_title
from src.domain.errors import EngineError

pytestmark = [pytest.mark.asyncio]

SETTINGS = BotSettings(token="unused-in-tests", allowed_ids=frozenset({AUDITOR_ID}), mode="polling")

#: Название зоны `dining` на русском (`tests/methodology/zones.csv`) — тем же
#: значением, что и код зоны в постановке задачи. Тест сравнивает с данными
#: методики, а не с каталогом `t()`: названия зон в нём не заведены.
DINING_TITLE_RU = "Гостевой зал"
#: Название зоны `fridge` — там записи `PRD01` заводятся в тестах.
FRIDGE_TITLE_RU = "Среднетемпературный шкаф"


def _pct() -> str:
    """Текущий процент проверки так же, как его печатает бот (`view.percent`)."""
    return f"{domain.score(CHAT_ID).pct:.1f}"


async def test_drop_button_removes_the_finding(domain_env: object) -> None:
    """Кнопка «Удалить» и правда убирает запись из состояния, а не только из чата."""
    domain.start_inspection(CHAT_ID, "Белград 2", "Плановая", "ru")
    domain.add_finding(CHAT_ID, "PRD01", "D1", "fridge", "текст")
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, callback_query("edit:1:drop"))

    inspection = domain.get_state(CHAT_ID)
    assert inspection is not None
    assert inspection.finding(1) is None
    pct = _pct()
    assert pct == "100.0", "единственную запись убрали — накоплено должно вернуться к 100%"
    assert session.last_text == t("edit.dropped", "ru", n=1, pct=pct)


async def test_dropping_a_marked_record_does_not_move_its_mark(domain_env: object) -> None:
    """Удалили догадку по кадру — пометка уходит с ней, а не переезжает на соседа.

    Пометку несёт сама запись (`Finding.source`, задача T108), поэтому проверка
    идёт там, где аудитор её видит: в списке для предвычитки при завершении.
    Разметка по месту в списке, а не по записи, здесь и вскрывается — после
    удаления первой строки соседняя встала бы на её место вместе с пометкой.
    """
    domain.start_inspection(CHAT_ID, "Белград 2", "Плановая", "ru")
    domain.add_finding(CHAT_ID, "PRD01", "D1", "fridge", "догадка", source=domain.SOURCE_PHOTO)
    domain.add_finding(
        CHAT_ID, "PRD01", "D1", "hot_kitchen", "со слов", source=domain.SOURCE_COMMENT
    )
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, callback_query("edit:1:drop"))
    session.clear()
    await feed(dp, bot, text_message("/finish"))

    строки = [s for s in "\n".join(session.texts).splitlines() if s.startswith("#")]
    assert len(строки) == 1, "после удаления одной записи из двух в предвычитке осталась не одна"
    assert t("finish.source_photo", "ru").strip() not in строки[0], (
        "пометка удалённой догадки переехала на запись со слов аудитора"
    )


async def test_undo_removes_the_last_record_not_the_first(domain_env: object) -> None:
    """`/undo` снимает последнюю запись: аудитор поправляет то, что сказал только что."""
    domain.start_inspection(CHAT_ID, "Белград 2", "Плановая", "ru")
    domain.add_finding(CHAT_ID, "PRD01", "D1", "fridge", "первая")
    domain.add_finding(CHAT_ID, "PRD01", "D1", "cold_kitchen", "вторая")
    domain.add_finding(CHAT_ID, "PRD01", "D1", "hot_kitchen", "третья")
    bot, _ = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, text_message("/undo"))

    inspection = domain.get_state(CHAT_ID)
    assert inspection is not None
    numbers = {f.n for f in inspection.findings}
    assert numbers == {1, 2}, "должна остаться первая пара записей, а не любые две"


async def test_undo_on_empty_inspection_says_nothing_to_remove(domain_env: object) -> None:
    """`/undo` без единой записи — отказ словами, а не попытка снять несуществующее."""
    domain.start_inspection(CHAT_ID, "Белград 2", "Плановая", "ru")
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, text_message("/undo"))

    assert session.last_text == t("edit.nothing_to_undo", "ru")
    inspection = domain.get_state(CHAT_ID)
    assert inspection is not None and inspection.findings == []


async def test_undo_without_started_inspection(domain_env: object) -> None:
    """`/undo` до «Новая проверка» — то же приглашение начать, что и у приёма материала."""
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, text_message("/undo"))

    assert session.last_text == t("material.no_inspection", "ru")


async def test_zone_button_lists_the_whole_zone_catalogue(domain_env: object) -> None:
    """Кнопка «Зона» предлагает весь справочник, а не подмножество из пункта."""
    domain.start_inspection(CHAT_ID, "Белград 2", "Плановая", "ru")
    domain.add_finding(CHAT_ID, "PRD01", "D1", "fridge", "текст")
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, callback_query("edit:1:zone"))

    expected = {f"ez:1:{zone.code}" for zone in domain.list_zones()}
    assert set(session.keyboard_data()) == expected


async def test_zone_change_updates_the_finding(domain_env: object) -> None:
    """Смена зоны кнопкой меняет запись, и в ответе видно название, а не код."""
    domain.start_inspection(CHAT_ID, "Белград 2", "Плановая", "ru")
    domain.add_finding(CHAT_ID, "PRD01", "D1", "fridge", "текст")
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, callback_query("ez:1:dining"))

    inspection = domain.get_state(CHAT_ID)
    assert inspection is not None
    finding = inspection.finding(1)
    assert finding is not None and finding.zone == "dining"
    assert DINING_TITLE_RU in session.last_text
    assert "dining" not in session.last_text, "код зоны не должен утекать в текст для аудитора"


async def test_zone_change_is_remembered_as_the_last_named_zone(domain_env: object) -> None:
    """Правка зоны кнопкой запоминается как последняя названная (решение D048)."""
    domain.start_inspection(CHAT_ID, "Белград 2", "Плановая", "ru")
    domain.add_finding(CHAT_ID, "PRD01", "D1", "fridge", "текст")
    bot, _ = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, callback_query("ez:1:dining"))

    assert sidecar.read(CHAT_ID).zone == "dining"


async def test_level_button_offers_only_levels_allowed_for_the_item(domain_env: object) -> None:
    """`CLN05` разрешает только `D1` — кнопки не должны предлагать весь перечень методики."""
    domain.start_inspection(CHAT_ID, "Белград 2", "Плановая", "ru")
    domain.add_finding(CHAT_ID, "CLN05", "D1", "hot_kitchen", "текст")
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, callback_query("edit:1:level"))

    assert session.keyboard_data() == ["el:1:D1"]


async def test_level_change_recomputes_the_percent(domain_env: object) -> None:
    """Смена класса на более тяжёлый уменьшает процент — движок пересчитывает заново."""
    domain.start_inspection(CHAT_ID, "Белград 2", "Плановая", "ru")
    domain.add_finding(CHAT_ID, "PRD01", "D1", "fridge", "текст")
    pct_before = domain.score(CHAT_ID).pct
    bot, _ = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, callback_query("el:1:D2"))

    inspection = domain.get_state(CHAT_ID)
    assert inspection is not None
    finding = inspection.finding(1)
    assert finding is not None and finding.level == "D2"
    assert domain.score(CHAT_ID).pct < pct_before


async def test_wording_edit_is_taken_from_the_next_plain_message(domain_env: object) -> None:
    """Главный тест файла: формулировка правится следующим сообщением, не уходит в разбор кадра.

    Без состояния диалога (`EditFlow.waiting_text`) это же сообщение перехватил
    бы приём материала как комментарий к кадру — правка формулировки существует
    только благодаря тому, что роутер правок стоит раньше в диспетчере.
    """
    domain.start_inspection(CHAT_ID, "Белград 2", "Плановая", "ru")
    domain.add_finding(CHAT_ID, "PRD01", "D1", "fridge", "старая формулировка")
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, callback_query("edit:1:text"))
    assert session.last_text == t("edit.ask_text", "ru", n=1)

    await feed(dp, bot, text_message("Скол эмали на бортике печи"))

    inspection = domain.get_state(CHAT_ID)
    assert inspection is not None
    finding = inspection.finding(1)
    assert finding is not None
    assert finding.text == "Скол эмали на бортике печи"
    expected = t(
        "edit.changed",
        "ru",
        n=1,
        code=finding.code,
        level=finding.level,
        zone=FRIDGE_TITLE_RU,
        pct=_pct(),
    )
    assert session.last_text == expected


async def test_empty_wording_is_not_accepted(domain_env: object) -> None:
    """Формулировка из одних пробелов не проходит — вопрос звучит снова, запись не портится."""
    domain.start_inspection(CHAT_ID, "Белград 2", "Плановая", "ru")
    domain.add_finding(CHAT_ID, "PRD01", "D1", "fridge", "исходная формулировка")
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, callback_query("edit:1:text"))
    await feed(dp, bot, text_message("   "))

    assert session.last_text == t("edit.ask_text", "ru", n=1)
    inspection = domain.get_state(CHAT_ID)
    assert inspection is not None
    finding = inspection.finding(1)
    assert finding is not None and finding.text == "исходная формулировка"


async def test_editing_a_gone_record_does_not_crash_the_bot(domain_env: object) -> None:
    """Правка исчезнувшей записи — вежливый отказ, и бот продолжает работать дальше."""
    domain.start_inspection(CHAT_ID, "Белград 2", "Плановая", "ru")
    domain.add_finding(CHAT_ID, "PRD01", "D1", "fridge", "текст")
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, callback_query("edit:9:zone"))
    assert session.last_text == t("edit.gone", "ru", n=9)

    # Бот жив: следующее событие он всё ещё обрабатывает как обычно.
    await feed(dp, bot, callback_query("edit:1:drop"))
    assert session.last_text == t("edit.dropped", "ru", n=1, pct=_pct())


async def test_engine_refusal_reaches_the_auditor_in_his_own_words(domain_env: object) -> None:
    """Отказ движка доходит до аудитора — но словами бота, а не движка (T127).

    Проверено фактическим прогоном: `CLN05` разрешает только `D1`
    (`data/checklist.csv`), и движок на `D3` действительно отказывает
    (`engine/audit.py: cmd_edit`) вместо того, чтобы молча принять правку.

    Дословно его текст больше не пересказывается: он написан для командной
    строки, а у аудитора её нет. Разбор — `tests/test_bot_refusal.py`; здесь
    важно, что отказ дошёл и запись не испортилась.
    """
    domain.start_inspection(CHAT_ID, "Белград 2", "Плановая", "ru")
    domain.add_finding(CHAT_ID, "CLN05", "D1", "hot_kitchen", "текст")
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, callback_query("el:1:D3"))

    assert session.last_text == t(
        "edit.failed",
        "ru",
        n=1,
        item=domain.get_item("CLN05").question("ru"),
        zone=zone_title("hot_kitchen", "ru"),
    )
    inspection = domain.get_state(CHAT_ID)
    assert inspection is not None
    finding = inspection.finding(1)
    assert finding is not None and finding.level == "D1"


async def test_edit_buttons_return_after_a_successful_change(domain_env: object) -> None:
    """Кнопки правок возвращаются после любой правки: они редко приходят по одной."""
    domain.start_inspection(CHAT_ID, "Белград 2", "Плановая", "ru")
    domain.add_finding(CHAT_ID, "PRD01", "D1", "fridge", "текст")
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, callback_query("ez:1:dining"))

    data = session.keyboard_data()
    assert "edit:1:zone" in data
    assert "edit:1:drop" in data


async def test_malformed_button_code_does_not_crash_the_bot(domain_env: object) -> None:
    """Битый номер записи в `callback_data` не роняет бота — следующее событие он обрабатывает."""
    domain.start_inspection(CHAT_ID, "Белград 2", "Плановая", "ru")
    domain.add_finding(CHAT_ID, "PRD01", "D1", "fridge", "текст")
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, callback_query("edit:abc:zone"))
    assert session.texts == [], "нечисловой номер записи — тихий отказ, а не ответ невпопад"

    # Бот жив: обычная правка следом проходит как ни в чём не бывало.
    await feed(dp, bot, callback_query("edit:1:drop"))
    assert session.last_text == t("edit.dropped", "ru", n=1, pct=_pct())


async def test_photo_instead_of_wording_cancels_the_question_and_is_still_taken(
    domain_env: object,
) -> None:
    """Кадр вместо формулировки: вопрос снят, а сам кадр не пропал.

    Худший исход без этой ветки — молчаливый: кадр съедается ожиданием текста, а
    следующий комментарий аудитора становится формулировкой старой записи, и
    узнаёт об этом партнёр из отчёта.
    """
    domain.start_inspection(CHAT_ID, "Белград 2", "Плановая", "ru")
    domain.add_finding(CHAT_ID, "PRD01", "D1", "fridge", "прежний текст")
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, callback_query("edit:1:text"))
    session.clear()
    await feed(dp, bot, photo_message("escape-frame", message_id=777))

    assert t("edit.text_dropped", "ru", n=1) in session.texts
    assert session.keyboard_data() == ["rec:analyze:777"], "кадр не дошёл до приёма материала"

    # Следующий комментарий относится к кадру, а не к формулировке записи.
    state = domain.get_state(CHAT_ID)
    assert state is not None
    assert state.findings[0].text == "прежний текст"


async def test_command_instead_of_wording_still_runs(domain_env: object) -> None:
    """Команда вместо формулировки исполняется, а не становится текстом записи."""
    domain.start_inspection(CHAT_ID, "Белград 2", "Плановая", "ru")
    domain.add_finding(CHAT_ID, "PRD01", "D1", "fridge", "прежний текст")
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, callback_query("edit:1:text"))
    session.clear()
    await feed(dp, bot, text_message("/finish"))

    assert t("edit.text_dropped", "ru", n=1) in session.texts
    assert any(text.startswith("Итог:") for text in session.texts), "команда не отработала"
    state = domain.get_state(CHAT_ID)
    assert state is not None
    assert state.findings[0].text == "прежний текст"


async def test_engine_refusing_to_delete_is_reported_not_swallowed(
    domain_env: object, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Отказ движка на удаление уходит аудитору, а не тонет в «удалено».

    Молчаливое «удалено» на неудалённой записи — худший исход: аудитор уходит с
    точки уверенным, что ошибку убрал, а она едет партнёру в отчёте.

    Своими словами движка в чате больше нет (T127): «состояние заблокировано»
    написано для того, кто чинит. Аудитору называется запись, которая осталась
    на месте, а разбор уходит в журнал — и проверяются оба конца.
    """
    domain.start_inspection(CHAT_ID, "Белград 2", "Плановая", "ru")
    domain.add_finding(CHAT_ID, "PRD01", "D1", "fridge", "текст")

    def отказ(*_a: object, **_k: object) -> None:
        raise EngineError("состояние заблокировано", code=1, command="drop")

    monkeypatch.setattr("src.domain.drop_finding", отказ)
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    with caplog.at_level("ERROR"):
        await feed(dp, bot, text_message("/undo"))

    assert session.last_text == t("edit.drop_failed", "ru", n=1)
    assert "состояние заблокировано" in caplog.text, "причина отказа потеряна совсем"
    state = domain.get_state(CHAT_ID)
    assert state is not None
    assert len(state.findings) == 1, "запись исчезла, хотя движок отказал"


async def test_record_gone_between_the_edit_and_the_answer_is_reported(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Запись исчезла сразу после правки — бот говорит это, а не показывает пустую строку.

    Так бывает при работе с двух устройств: правку приняли, а запись успели
    снять. Собрать строку подтверждения не из чего, и врать про неё нельзя.
    """
    domain.start_inspection(CHAT_ID, "Белград 2", "Плановая", "ru")
    domain.add_finding(CHAT_ID, "PRD01", "D1", "fridge", "текст")
    настоящая_правка = domain.edit_finding

    def правка_и_исчезновение(chat_id: int, n: int, **fields: str) -> object:
        finding = настоящая_правка(chat_id, n, **fields)
        domain.drop_finding(chat_id, n)
        return finding

    monkeypatch.setattr("src.domain.edit_finding", правка_и_исчезновение)
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, callback_query("ez:1:dining"))

    assert session.last_text == t("edit.gone", "ru", n=1)
