"""Ветки `refusal`, `app._asked_here` и `finish._reader`, до которых сквозные
тесты не достают (задачи T123, T126, T127).

`tests/test_bot_refusal.py` проверяет отказ целиком — от нажатия в чате до
текста на экране. Здесь наоборот: каждая функция дёргается напрямую, потому
что путь к некоторым её веткам через диалог не собрать вовсе (незнакомый код
методики движок сам никогда не отдаст, испорченное состояние `occupied_by`
встречает уже после того, как `on_unexpected_error` его перехватил и ответил
аудитору, а до `finish._reader` с картой без нужного кадра дойти можно только
подменив саму карту). Смысл веток от этого не меняется — только способ до них
добраться.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from aiogram.types import ErrorEvent, Update
from bot_harness import CHAT_ID, text_message
from bot_harness import callback_query as callback

from src import domain
from src.bot import refusal
from src.bot.app import _asked_here
from src.bot.routers.finish import _reader
from src.bot.texts import t
from src.bot.view import zone_title
from src.domain.config import check_environment
from src.domain.engine import state_file
from src.domain.errors import ValidationError


def начата(lang: str = "ru") -> None:
    """Проверка начата — минимум, нужный `occupied_by`, `not_recorded`, `not_changed`."""
    domain.start_inspection(
        CHAT_ID, "Белград 2", "Плановая", lang, ui_lang=lang, date="2026-08-21", auditor="Гарро"
    )


def сломать_состояние() -> Path:
    """Начать проверку и испортить её файл — как при обрыве записи на точке.

    Своя копия, не импорт: у `tests/test_bot_error_handler.py` своя тема
    (молчание бота), здесь же — только то, что читает сам `occupied_by`.
    """
    начата()
    файл = state_file(CHAT_ID, check_environment())
    файл.write_text("{это не json", encoding="utf-8")
    return файл


# --- item_title ---------------------------------------------------------------


def test_item_title_на_неизвестном_коде_возвращает_код_и_пишет_в_журнал(
    domain_env: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Кода нет в методике — придумывать вопрос нельзя, честнее показать сам код."""
    with caplog.at_level("WARNING"):
        title = refusal.item_title("XX99", "ru")

    assert title == "XX99"
    assert "XX99" in caplog.text, "предупреждение о ненайденном пункте не попало в журнал"


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_item_title_на_настоящем_коде_возвращает_вопрос_чек_листа(
    domain_env: Path, lang: str
) -> None:
    """Известный код — тот же вопрос, что и напрямую у методики, на обоих языках."""
    assert refusal.item_title("CLN05", lang) == domain.get_item("CLN05").question(lang)


# --- occupied_by ---------------------------------------------------------------


def test_occupied_by_без_начатой_проверки_возвращает_none_без_исключения(
    domain_env: Path,
) -> None:
    """Проверки в этом чате ещё нет вовсе — `get_state` вернёт `None`, а не отказ."""
    assert refusal.occupied_by(CHAT_ID, "CLN05", "hot_kitchen") is None


def test_occupied_by_на_испорченном_состоянии_возвращает_none_и_пишет_причину(
    domain_env: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Файл есть, но не читается — причина в журнале, аудитору по-прежнему `None`."""
    сломать_состояние()

    with caplog.at_level("ERROR"):
        result = refusal.occupied_by(CHAT_ID, "CLN05", "hot_kitchen")

    assert result is None
    assert "не читается" in caplog.text, "причина отказа не попала в журнал"


def test_occupied_by_с_skip_не_спорит_сама_с_собой(domain_env: Path) -> None:
    """Запись, которую сейчас правят, не находится под собственным номером."""
    начата()
    finding = domain.add_finding(CHAT_ID, "CLN05", "D1", "hot_kitchen", "нагар на подине печи")

    assert refusal.occupied_by(CHAT_ID, "CLN05", "hot_kitchen", skip=finding.n) is None
    assert refusal.occupied_by(CHAT_ID, "CLN05", "hot_kitchen") == finding


# --- not_recorded / not_changed при свободной паре ------------------------------


def test_not_recorded_на_свободной_паре_собирает_текст_по_record_failed(
    domain_env: Path,
) -> None:
    """Пара не занята — движок отказал по другой причине, а не дублем записи."""
    начата()

    итог = refusal.not_recorded(
        CHAT_ID,
        code="CLN05",
        zone="hot_kitchen",
        lang="ru",
        exc=ValidationError("класс не разрешён пункту"),
    )

    item = domain.get_item("CLN05").question("ru")
    место = zone_title("hot_kitchen", "ru")
    assert итог.clash is None
    assert итог.text == t("record.failed", "ru", item=item, zone=место)


def test_not_changed_на_свободной_паре_собирает_текст_по_edit_failed(
    domain_env: Path,
) -> None:
    """Правка не прошла не из-за дубля — текст и номер записи берутся из edit.failed."""
    начата()

    итог = refusal.not_changed(
        CHAT_ID,
        3,
        code="CLN05",
        zone="hot_kitchen",
        lang="ru",
        exc=ValidationError("класс не разрешён пункту"),
    )

    item = domain.get_item("CLN05").question("ru")
    место = zone_title("hot_kitchen", "ru")
    assert итог.clash is None
    assert итог.text == t("edit.failed", "ru", n=3, item=item, zone=место)


# --- app._asked_here -------------------------------------------------------------


def test_asked_here_на_обновлении_с_сообщением_возвращает_это_сообщение() -> None:
    """Обычное сообщение — сообщение из самого обновления, без гаданий."""
    сообщение = text_message("привет")
    событие = ErrorEvent(
        update=Update(update_id=1, message=сообщение), exception=RuntimeError("сбой")
    )

    assert _asked_here(событие) is сообщение


def test_asked_here_на_нажатии_с_сообщением_возвращает_сообщение_нажатия() -> None:
    """Нажатие моложе 48 часов несёт своё сообщение — отвечать нужно под ним."""
    нажатие = callback("edit:1:zone")
    событие = ErrorEvent(
        update=Update(update_id=1, callback_query=нажатие), exception=RuntimeError("сбой")
    )

    assert _asked_here(событие) is нажатие.message


def test_asked_here_на_нажатии_без_сообщения_возвращает_none() -> None:
    """Нажатие старше 48 часов приходит без сообщения — отвечать некуда, и это не сбой."""
    нажатие = callback("edit:1:zone", with_message=False)
    событие = ErrorEvent(
        update=Update(update_id=1, callback_query=нажатие), exception=RuntimeError("сбой")
    )

    assert _asked_here(событие) is None


# --- finish._reader ---------------------------------------------------------------


def test_reader_возвращает_байты_кадра_с_диска(tmp_path: Path) -> None:
    """Кадр есть и в карте, и на диске — читаются его настоящие байты."""
    файл = tmp_path / "frame-1.jpg"
    файл.write_bytes(b"jpeg-bytes")
    читать = _reader({"frame-1": str(файл)})

    assert читать("frame-1") == b"jpeg-bytes"


def test_reader_на_кадре_вне_карты_возвращает_none(tmp_path: Path) -> None:
    """Идентификатора, которого в карте нет вовсе, ждать не приходится — не ошибка."""
    читать = _reader({})

    assert читать("frame-неизвестный") is None


def test_reader_на_потерянном_файле_возвращает_none_и_пишет_в_журнал(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Путь в карте есть, а файла на диске нет — `upload_photos` не должен получить исключение."""
    путь = tmp_path / "frame-2.jpg"  # не создаём: ровно тот случай, что теряется
    читать = _reader({"frame-2": str(путь)})

    with caplog.at_level("ERROR"):
        result = читать("frame-2")

    assert result is None
    assert "frame-2" in caplog.text, "потеря кадра не записана в журнал"
