"""Завершение проверки: итог, предвычитка, отчёт и кадры без записи (T058, T068).

Порядок шага 7 из `docs/06-mvp-bot.md` проверяется буквально: сначала итог и
список зафиксированного с возможностью поправить, и только потом PDF файлом и
письмо сообщением. Отчёт, собранный до предвычитки, уходит партнёру с ошибкой,
которую аудитор увидел бы за секунду.

Отдельная половина файла — задача T068: кадр, который прислали и не разобрали,
обязан всплыть при завершении. Молча выброшенный кадр — потеря материала, за
которым на точку уже не вернуться.

Сборка отчёта здесь настоящая: движок, WeasyPrint, реальный PDF. Подменяется
только скачивание кадров из телеграма — сети в тестах нет.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from bot_harness import AUDITOR_ID, CHAT_ID, feed, make_bot, photo_message, text_message
from bot_harness import callback_query as callback

from src.bot import sidecar
from src.bot.app import build_dispatcher
from src.bot.config import BotSettings
from src.bot.texts import t
from src.domain import SOURCE_PHOTO, add_finding, attach_photo, score, start_inspection
from src.report.errors import PdfNotBuilt, ReportError

pytestmark = pytest.mark.asyncio

SETTINGS = BotSettings(token="unused-in-tests", allowed_ids=frozenset({AUDITOR_ID}), mode="polling")


def проверить_pdf(файл: Path) -> None:
    """Диск читается синхронной функцией нарочно: правило ASYNC240 запрещает
    трогать `pathlib` внутри `async def` — там место только цикла событий."""
    assert файл.is_file(), f"вернулся путь к несуществующему файлу: {файл}"
    assert файл.read_bytes()[:5] == b"%PDF-", "аудитору отдали не PDF"
    assert файл.name == "Аудит Белград 2 - Владимир Гарро - 21.08.2026.pdf"


def started() -> None:
    start_inspection(
        CHAT_ID,
        "Белград 2",
        "Плановая",
        "ru",
        date="2026-08-21",
        auditor="Владимир Гарро",
    )


async def test_finish_without_inspection_says_so(domain_env: object) -> None:
    bot, session = make_bot()
    await feed(build_dispatcher(SETTINGS), bot, text_message("/finish"))
    assert session.last_text == t("material.no_inspection", "ru")


async def test_summary_shows_score_and_the_recorded_list(domain_env: object) -> None:
    """Итог и список зафиксированного — то, по чему аудитор вычитывает отчёт."""
    started()
    add_finding(CHAT_ID, "CLN05", "D1", "hot_kitchen", "Нагар на подине печи")
    bot, session = make_bot()

    await feed(build_dispatcher(SETTINGS), bot, text_message("/finish"))

    итог = session.texts[0]
    assert f"{score(CHAT_ID).pct:.1f}%" in итог
    assert score(CHAT_ID).grade in итог
    assert "CLN05" in session.texts[1]
    assert "Нагар на подине печи" in session.texts[1]
    assert session.last_text == t("finish.ask", "ru")


def пометка_догадки() -> str:
    return t("finish.source_photo", "ru").strip()


def строки_записей(текст: str) -> list[str]:
    return [s for s in текст.splitlines() if s.startswith("#")]


async def test_records_recognized_from_the_photo_are_marked(domain_env: object) -> None:
    """Решение D044: догадка по картинке подсвечивается при предвычитке отчёта."""
    started()
    add_finding(CHAT_ID, "CLN05", "D1", "hot_kitchen", "Нагар на подине печи")
    add_finding(CHAT_ID, "PRD01", "D1", "fridge", "Продукт без маркировки", source=SOURCE_PHOTO)
    bot, session = make_bot()

    await feed(build_dispatcher(SETTINGS), bot, text_message("/finish"))

    строки = строки_записей(session.texts[1])
    assert пометка_догадки() not in строки[0], "запись со слов аудитора помечена как догадка"
    assert пометка_догадки() in строки[1], "догадка по кадру не отличима от слов аудитора"


async def test_the_photo_mark_outlives_the_bot_notes(domain_env: object) -> None:
    """T108: пометка держится на источнике из проверки, а не на заметках бота.

    Заметки бота стираются с началом новой проверки этого чата
    (`sidecar.reset`), и пока источник лежал там, аудитор терял подсветку
    догадок ровно там, где она нужна, — при предвычитке отчёта.
    """
    started()
    add_finding(CHAT_ID, "CLN05", "D1", "hot_kitchen", "Нагар на подине печи", source=SOURCE_PHOTO)
    sidecar.reset(CHAT_ID)
    bot, session = make_bot()

    await feed(build_dispatcher(SETTINGS), bot, text_message("/finish"))

    строки = строки_записей(session.texts[1])
    assert пометка_догадки() in строки[0], (
        "пометка догадки пропала вместе с заметками бота — источник читается не из проверки"
    )


async def test_a_record_from_before_the_source_existed_is_not_marked(domain_env: object) -> None:
    """У записей старых проверок источника нет вовсе: ни отказа, ни выдуманной пометки.

    Пустой источник — это «неизвестно», а не «со слов аудитора» и не «догадка».
    Показать такую запись догадкой значит оболгать аудитора, показать словами —
    спрятать от него то, что он обязан перечитать.
    """
    started()
    add_finding(CHAT_ID, "CLN05", "D1", "hot_kitchen", "Нагар на подине печи")
    bot, session = make_bot()

    await feed(build_dispatcher(SETTINGS), bot, text_message("/finish"))

    строки = строки_записей(session.texts[1])
    assert len(строки) == 1, "запись без источника выпала из предвычитки"
    assert пометка_догадки() not in строки[0], "источника нет, а пометка догадки взялась"


async def test_empty_inspection_says_nothing_was_recorded(domain_env: object) -> None:
    """Пустая проверка не притворяется отличной: сказано, что записей нет."""
    started()
    bot, session = make_bot()

    await feed(build_dispatcher(SETTINGS), bot, text_message("/finish"))

    assert t("finish.empty", "ru") in session.texts
    # Править нечего — кнопки правки в итоге нет.
    assert "fin:edit" not in session.keyboard_data()


async def test_frames_without_a_record_are_listed(domain_env: object) -> None:
    """Задача T068: кадр прислали, не разобрали — он не исчезает молча."""
    started()
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, photo_message("lonely-frame", message_id=321))
    await feed(dp, bot, text_message("/finish"))

    показано = "\n".join(session.texts)
    assert t("finish.unclaimed_line", "ru", message_id=321) in показано


async def test_frame_that_became_a_record_is_not_listed_as_lost(domain_env: object) -> None:
    """Кадр, попавший в запись, в списке потерянных не нужен — это шум."""
    started()
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, photo_message("used-frame", message_id=322))
    add_finding(CHAT_ID, "CLN05", "D1", "hot_kitchen", "Нагар на подине печи")
    attach_photo(CHAT_ID, 1, "used-frame")
    session.clear()
    await feed(dp, bot, text_message("/finish"))

    показано = "\n".join(session.texts)
    assert t("finish.unclaimed_line", "ru", message_id=322) not in показано


async def test_unclaimed_frames_survive_a_restart(domain_env: object) -> None:
    """Список кадров лежит файлом рядом с проверкой, а не в памяти диспетчера."""
    started()
    bot, session = make_bot()

    await feed(build_dispatcher(SETTINGS), bot, photo_message("lonely-frame", message_id=333))
    session.clear()
    await feed(build_dispatcher(SETTINGS), bot, text_message("/finish"))

    assert t("finish.unclaimed_line", "ru", message_id=333) in "\n".join(session.texts)


async def test_edit_from_the_summary_opens_the_record(domain_env: object) -> None:
    """«Дать шанс поправить» — это не обещание в спеке, а кнопки под итогом."""
    started()
    add_finding(CHAT_ID, "CLN05", "D1", "hot_kitchen", "Нагар на подине печи")
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, text_message("/finish"))
    await feed(dp, bot, callback("fin:edit"))
    assert session.keyboard_data() == ["fin:pick:1"]

    await feed(dp, bot, callback("fin:pick:1"))
    assert "CLN05" in session.last_text
    assert "edit:1:zone" in session.keyboard_data()
    assert "edit:1:drop" in session.keyboard_data()


async def test_report_is_delivered_as_a_file_and_the_letter_as_a_message(
    domain_env: object,
) -> None:
    """Сборка настоящая: PDF доезжает файлом, письмо — сообщением (T058)."""
    started()
    add_finding(CHAT_ID, "CLN05", "D1", "hot_kitchen", "Нагар на подине печи")
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, text_message("/finish"))
    session.clear()
    await feed(dp, bot, callback("fin:build"))

    документы = session.documents
    assert len(документы) == 1, "отчёт не доехал файлом"
    проверить_pdf(Path(документы[0].document.path))
    assert session.last_text.startswith(t("finish.letter", "ru", letter="").strip())


async def test_lost_photo_stops_the_build_and_leaves_the_choice_to_the_auditor(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Отчёт без доказательства партнёр оспорит — решает аудитор, а не код."""
    started()
    add_finding(CHAT_ID, "CLN05", "D1", "hot_kitchen", "Нагар на подине печи")
    attach_photo(CHAT_ID, 1, "gone-frame")

    async def ничего(*_a: object, **_k: object) -> dict[str, str]:
        return {}

    monkeypatch.setattr("src.bot.routers.finish.download_all", ничего)
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, text_message("/finish"))
    session.clear()
    await feed(dp, bot, callback("fin:build"))

    assert session.documents == [], "отчёт собрался, потеряв кадр, — этого делать нельзя"
    assert "запись №1" in session.last_text
    assert session.keyboard_data() == ["fin:nophoto"]

    await feed(dp, bot, callback("fin:nophoto"))
    assert len(session.documents) == 1, "аудитор разрешил — отчёт обязан собраться"


async def test_failed_build_is_reported_not_passed_off_as_a_report(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Провал сборки уходит текстом: путь к несуществующему файлу отдавать нельзя."""
    started()
    add_finding(CHAT_ID, "CLN05", "D1", "hot_kitchen", "Нагар на подине печи")

    def отказ(*_a: object, **_k: object) -> Path:
        raise PdfNotBuilt("рендерер недоступен")

    monkeypatch.setattr("src.bot.routers.finish.build_pdf", отказ)
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, text_message("/finish"))
    session.clear()
    await feed(dp, bot, callback("fin:build"))

    assert session.documents == []
    assert "рендерер недоступен" in session.last_text


async def test_resume_returns_to_the_inspection(domain_env: object) -> None:
    """Завершение — не точка невозврата: аудитор мог просто посмотреть итог."""
    started()
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, text_message("/finish"))
    await feed(dp, bot, callback("fin:resume"))

    assert session.last_text == t("finish.resumed", "ru")


async def test_build_without_an_inspection_says_so_instead_of_calling_the_engine(
    domain_env: object,
) -> None:
    """Кнопка «Собрать отчёт» из старой переписки: проверки уже нет, собирать нечего."""
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, callback("fin:build"))

    assert session.documents == []
    assert session.last_text == t("material.no_inspection", "ru")


async def test_failed_letter_is_reported_after_the_pdf_was_already_sent(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Письмо не собралось — сказать об этом, хотя отчёт уже уехал.

    Промолчать тут хуже всего: аудитор видит PDF, считает завершение удавшимся и
    уходит с точки без текста письма партнёру.
    """
    started()
    add_finding(CHAT_ID, "CLN05", "D1", "hot_kitchen", "Нагар на подине печи")

    def отказ(*_a: object, **_k: object) -> str:
        raise ReportError("шаблон письма не прочитался")

    monkeypatch.setattr("src.bot.routers.finish.build_letter", отказ)
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, text_message("/finish"))
    session.clear()
    await feed(dp, bot, callback("fin:build"))

    assert len(session.documents) == 1, "отчёт обязан был доехать: он собрался"
    assert "шаблон письма не прочитался" in session.last_text
