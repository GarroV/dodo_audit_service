"""Заметки бота (`src/bot/sidecar.py`): присланные кадры и зона переживают перезапуск.

Модуль — чистое хранилище JSON рядом с проверкой, без aiogram и без сети.
Фикстура `domain_env` заводит синтетическую методику и временный `STATE_DIR`
(`tests/conftest.py`); `check_environment()` внутри `notes_path` читает
методику, но это `tests/methodology` из git (T141), а не боевая вне его —
поэтому тесты идут без `requires_data`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.bot.errors import BotNotesError
from src.bot.sidecar import (
    Notes,
    SeenFrame,
    notes_path,
    read,
    remember_frames,
    remember_zone,
    reset,
    unclaimed,
)

CHAT = 4101


def кадр(message_id: int, file_id: str) -> SeenFrame:
    return SeenFrame(message_id=message_id, file_id=file_id)


def test_заметок_нет_read_отдаёт_пустые(domain_env: Path) -> None:
    notes = read(CHAT)
    assert notes.frames == ()
    assert notes.zone == ""
    assert not notes_path(CHAT).is_file()


def test_remember_zone_сохраняется_и_перезаписывается(domain_env: Path) -> None:
    remember_zone(CHAT, "hot_kitchen")
    assert read(CHAT).zone == "hot_kitchen"

    remember_zone(CHAT, "bar")
    assert read(CHAT).zone == "bar", "повторный вызов должен перезаписать зону, а не сложить"

    remember_zone(CHAT, "")
    assert read(CHAT).zone == "", "пустая строка обязана стереть память о зоне"


def test_remember_frames_двумя_вызовами_сохраняет_порядок(domain_env: Path) -> None:
    remember_frames(CHAT, [кадр(1, "AAA"), кадр(2, "BBB")])
    remember_frames(CHAT, [кадр(3, "CCC")])

    assert read(CHAT).frames == (кадр(1, "AAA"), кадр(2, "BBB"), кадр(3, "CCC"))


def test_remember_frames_не_дублирует_известный_file_id(domain_env: Path) -> None:
    remember_frames(CHAT, [кадр(1, "AAA")])
    remember_frames(CHAT, [кадр(2, "AAA"), кадр(3, "BBB")])

    assert read(CHAT).frames == (кадр(1, "AAA"), кадр(3, "BBB")), (
        "повтор file_id из более раннего кадра не должен появиться снова"
    )


def test_unclaimed_отдаёт_только_кадры_вне_used(domain_env: Path) -> None:
    remember_frames(CHAT, [кадр(1, "AAA"), кадр(2, "BBB"), кадр(3, "CCC")])

    result = unclaimed(CHAT, {"BBB"})

    assert result == (кадр(1, "AAA"), кадр(3, "CCC"))


def test_unclaimed_с_пустым_used_отдаёт_все_кадры(domain_env: Path) -> None:
    remember_frames(CHAT, [кадр(1, "AAA"), кадр(2, "BBB")])

    assert unclaimed(CHAT, set()) == (кадр(1, "AAA"), кадр(2, "BBB"))


def test_reset_стирает_всё(domain_env: Path) -> None:
    remember_frames(CHAT, [кадр(1, "AAA")])
    remember_zone(CHAT, "hot_kitchen")

    reset(CHAT)

    assert read(CHAT) == Notes(frames=(), zone="")
    assert not notes_path(CHAT).is_file()


def test_reset_на_чате_без_заметок_не_падает(domain_env: Path) -> None:
    reset(CHAT)  # не должно поднять исключение

    assert read(CHAT) == Notes(frames=(), zone="")


def test_испорченный_json_даёт_botnoteserror_с_путём(domain_env: Path) -> None:
    path = notes_path(CHAT)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{не json", encoding="utf-8")

    with pytest.raises(BotNotesError) as отказ:
        read(CHAT)
    assert str(path) in str(отказ.value)


def test_список_вместо_объекта_даёт_botnoteserror(domain_env: Path) -> None:
    path = notes_path(CHAT)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(["не", "объект"]), encoding="utf-8")

    with pytest.raises(BotNotesError):
        read(CHAT)


def test_заметки_переживают_перезапуск(domain_env: Path) -> None:
    remember_frames(CHAT, [кадр(10, "AAA"), кадр(11, "BBB")])
    remember_zone(CHAT, "hot_kitchen")

    path = notes_path(CHAT)
    assert path.is_file(), "заметки обязаны лежать файлом в папке проверки"
    assert path.name == "bot.json"

    # «Перезапуск» — второе, независимое чтение с диска.
    notes = read(CHAT)
    assert notes.frames == (кадр(10, "AAA"), кадр(11, "BBB"))
    assert notes.zone == "hot_kitchen"


def test_заметки_разных_чатов_не_смешиваются(domain_env: Path) -> None:
    other = CHAT + 1
    remember_zone(CHAT, "hot_kitchen")
    remember_frames(CHAT, [кадр(10, "AAA")])
    remember_zone(other, "bar")
    remember_frames(other, [кадр(20, "BBB")])

    assert read(CHAT).zone == "hot_kitchen"
    assert read(CHAT).frames == (кадр(10, "AAA"),)
    assert read(other).zone == "bar"
    assert read(other).frames == (кадр(20, "BBB"),)


# --- пути отказа записи и чтения ---


def test_старые_заметки_с_источниками_читаются(domain_env: Path) -> None:
    """До T108 источник записи лежал здесь. Проверки, начатые тогда, ещё в работе.

    Отказ на незнакомом ключе означал бы, что после обновления бота такая
    проверка перестала завершаться: кадры не показать, отчёт не собрать. Ключ
    просто не нужен — источник теперь у самой записи.
    """
    remember_zone(CHAT, "hot_kitchen")
    remember_frames(CHAT, [кадр(10, "AAA")])
    path = notes_path(CHAT)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["sources"] = {"1": "photo", "2": "comment"}
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    notes = read(CHAT)

    assert notes.zone == "hot_kitchen"
    assert notes.frames == (кадр(10, "AAA"),)


def test_старый_файл_без_ключа_кадров_читается(domain_env: Path) -> None:
    """Заметки прежней версии кадров ещё не знали — читаться они обязаны.

    Отказ здесь означал бы, что после обновления бота проверка, начатая до него,
    перестала завершаться: список кадров пуст, а не «файл испорчен».
    """
    remember_zone(CHAT, "hot_kitchen")
    path = notes_path(CHAT)
    raw = json.loads(path.read_text(encoding="utf-8"))
    del raw["frames"]
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    notes = read(CHAT)
    assert notes.frames == ()
    assert notes.zone == "hot_kitchen"


@pytest.mark.parametrize(
    "битые_кадры",
    [
        [{"file_id": "нет номера сообщения"}],
        [{"message_id": "не число", "file_id": "x"}],
        ["строка вместо кадра"],
        [None],
    ],
)
def test_испорченный_список_кадров_это_отказ(domain_env: Path, битые_кадры: object) -> None:
    """Кадры не «читаются как получится»: половина списка хуже отказа.

    Молча потерянный кадр — то, ради чего заведена задача T068; вернуть тут
    пустоту значило бы обойти её же защиту через испорченный файл.
    """
    remember_zone(CHAT, "hot_kitchen")
    path = notes_path(CHAT)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["frames"] = битые_кадры
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(BotNotesError, match="кадр"):
        read(CHAT)


def test_сорванная_запись_не_оставляет_мусора(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Отказ посреди записи не оставляет временный файл рядом с проверкой.

    Мусор `.bot-notes-*.tmp` копился бы в папке проверки каждым сбоем и уехал бы
    вместе с ней; хуже того, читатель принял бы его за состояние.
    """
    remember_zone(CHAT, "hot_kitchen")
    папка = notes_path(CHAT).parent

    def сорвать(*_a: object, **_k: object) -> None:
        raise OSError("на диске нет места")

    monkeypatch.setattr("src.bot.sidecar.json.dump", сорвать)
    with pytest.raises(OSError, match="нет места"):
        remember_zone(CHAT, "dining")

    assert list(папка.glob(".bot-notes-*.tmp")) == []
    # Прежние заметки целы: сорванная запись не тронула файл.
    assert read(CHAT).zone == "hot_kitchen"


def test_повтор_тех_же_кадров_не_переписывает_файл(domain_env: Path) -> None:
    """Один и тот же кадр в пачке после потери связи не заставляет писать на диск.

    Пачка приходит десятками сообщений разом; лишняя запись файла на каждое из
    них — это работа на ровном месте в самый занятый момент проверки.
    """
    remember_frames(CHAT, [кадр(1, "a"), кадр(2, "b")])
    было = notes_path(CHAT).stat().st_mtime_ns

    remember_frames(CHAT, [кадр(1, "a"), кадр(2, "b")])

    assert notes_path(CHAT).stat().st_mtime_ns == было
    assert [f.file_id for f in read(CHAT).frames] == ["a", "b"]


def test_пустая_пачка_кадров_не_заводит_файл(domain_env: Path) -> None:
    """Пустой вызов не создаёт заметок: файл появляется только когда есть что помнить."""
    remember_frames(CHAT, [])
    assert not notes_path(CHAT).exists()
