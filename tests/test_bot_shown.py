"""Карта «сообщение бота → запись» не роняет показ записи (T204).

Модуль `src/bot/shown.py` решает ровно один вопрос: что делать, когда запомнить
сообщение не вышло. Ответ — сказать в журнал и жить дальше, потому что запись
уже сделана и уже показана: терять её из-за заметки нельзя, а цена потерянной
заметки — одна возможность поправить эту запись ответом (кнопки правки под ней
на месте).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import pytest
from aiogram.types import Chat, Message

from src.bot import sidecar
from src.bot.shown import remember

CHAT = 4242


def сообщение(message_id: int) -> Message:
    return Message(
        message_id=message_id,
        date=datetime.now(tz=timezone.utc),
        chat=Chat(id=CHAT, type="private"),
    )


def test_сообщение_запоминается(domain_env: Path) -> None:
    remember(CHAT, сообщение(9001), 1)

    assert sidecar.record_of(CHAT, 9001) == 1


def test_отправки_не_было_запоминать_нечего(domain_env: Path) -> None:
    """Телеграм не сказал, каким сообщением показана запись, — заметок не заводим.

    Файла заметок в этом случае не появляется вовсе: пустая карта в файле
    выглядела бы как «сообщений о записях не было», хотя их просто не помнят.
    """
    remember(CHAT, None, 1)

    assert not sidecar.notes_path(CHAT).is_file()


def test_испорченные_заметки_показ_записи_не_роняют(
    domain_env: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Заметки не читаются — запись всё равно сделана и показана.

    Отказ здесь стоил бы аудитору самой записи (её уже приняли), а сказать о
    нём всё равно надо: без строки в журнале потерянная правка ответом
    выглядела бы капризом бота.
    """
    путь = sidecar.notes_path(CHAT)
    путь.parent.mkdir(parents=True, exist_ok=True)
    путь.write_text(json.dumps({"schema": 3, "records": [{"нет": "номера"}]}), encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        remember(CHAT, сообщение(9001), 1)

    assert "правка ответом" in caplog.text, "потеря карты не названа в журнале"
