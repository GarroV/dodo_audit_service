"""Заметки бота (`src/bot/sidecar.py`): карта «сообщение аудитора → запись» (T205).

Бот ведёт о проверке ДВЕ карты сообщений, а не одну. `records` (T204) — это
сообщения САМОГО БОТА: ответ на такое сообщение правит запись. `origins`
(T205) — сообщения САМОГО АУДИТОРА, из которых запись выросла: аудитор
присылает кадр ответом на свои же слова, и такой кадр обязан уйти в ту же
запись, а не завести новую очередь ожидания. Смешай карты — и ответ словами на
своё голосовое стал бы правкой записи, хотя правкой владелец (D081) назвал
ровно ответ на сообщение бота. Поэтому главный тест этого файла — не то, что
каждая карта работает сама по себе, а то, что они не путаются местами.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.bot.errors import BotNotesError
from src.bot.sidecar import (
    SCHEMA,
    notes_path,
    origin_of,
    read,
    record_of,
    remember_origin,
    remember_record,
    reset,
)

CHAT = 4201


def test_запись_находится_по_сообщению_аудитора(domain_env: Path) -> None:
    """То, ради чего карта заведена: ответ на своё сообщение приводит к записи."""
    remember_origin(CHAT, 500, 1)

    assert origin_of(CHAT, 500) == 1


def test_незнакомое_сообщение_даёт_none(domain_env: Path) -> None:
    """Ответить кадром можно на что угодно — это обычный исход, а не отказ."""
    assert origin_of(CHAT, 999) is None


def test_повтор_той_же_пары_не_дублируется(domain_env: Path) -> None:
    """Повторный вызов с той же парой не должен множить записи в карте."""
    remember_origin(CHAT, 500, 1)
    remember_origin(CHAT, 500, 1)

    assert len(read(CHAT).origins) == 1


def test_две_карты_не_путаются(domain_env: Path) -> None:
    """Главный тест файла: карта сообщений бота и карта сообщений аудитора — разные.

    Запись в одну карту не обязана быть видна через доступ к другой: иначе
    ответ словами на своё же голосовое стал бы правкой записи (см. docstring
    модуля).
    """
    remember_record(CHAT, 700, 2)
    assert origin_of(CHAT, 700) is None, "карта сообщений бота не должна отвечать за origin_of"

    remember_origin(CHAT, 800, 3)
    assert record_of(CHAT, 800) is None, "карта сообщений аудитора не должна отвечать за record_of"


def test_карта_переживает_перезапуск(domain_env: Path) -> None:
    """Заметки — файл, а не память процесса: новое чтение видит то же, что записано."""
    remember_origin(CHAT, 500, 1)

    лежит = json.loads(notes_path(CHAT).read_text(encoding="utf-8"))
    assert лежит["origins"] == [{"message_id": 500, "n": 1}]

    # «Перезапуск» — второе, независимое чтение с диска.
    assert origin_of(CHAT, 500) == 1


def test_старые_заметки_без_origins_читаются(domain_env: Path) -> None:
    """Проверки старого издания (без T205) дочитывают своё: ключа нет — и это не отказ."""
    notes_path(CHAT).parent.mkdir(parents=True, exist_ok=True)
    notes_path(CHAT).write_text(
        json.dumps(
            {"schema": 3, "zone": "hot_kitchen", "frames": [], "records": []},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    notes = read(CHAT)

    assert notes.origins == ()
    assert origin_of(CHAT, 500) is None


@pytest.mark.parametrize(
    "битые_origins",
    [
        [{"message_id": "не число", "n": 1}],
        [{"n": 1}],
        ["строка вместо пары"],
        5,
    ],
)
def test_испорченный_origins_это_отказ(domain_env: Path, битые_origins: object) -> None:
    """Половина карты хуже отказа: молчаливая пустота отняла бы у аудитора привязку кадра.

    По тому же правилу, что и у `frames`/`records`: непонятная форма поднимает
    `BotNotesError`, а не подменяется пустой картой.
    """
    notes_path(CHAT).parent.mkdir(parents=True, exist_ok=True)
    notes_path(CHAT).write_text(
        json.dumps(
            {"schema": SCHEMA, "zone": "", "frames": [], "records": [], "origins": битые_origins},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(BotNotesError):
        read(CHAT)


def test_новая_проверка_обнуляет_карту_происхождения(domain_env: Path) -> None:
    """`reset` начинает проверку с нуля: сообщения прошлой проверки ничего не значат."""
    remember_origin(CHAT, 500, 1)

    reset(CHAT)

    assert origin_of(CHAT, 500) is None
