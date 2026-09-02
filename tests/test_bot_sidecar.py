"""Заметки бота (`src/bot/sidecar.py`): источник записи, кадры, зона переживают перезапуск.

Модуль — чистое хранилище JSON рядом с проверкой, без aiogram и без сети.
Фикстура `domain_env` заводит боевую методику и временный `STATE_DIR`
(`tests/conftest.py`); `check_environment()` внутри `notes_path` читает
методику, поэтому тесты помечены `requires_data` — на машине без неё (вне
git, решение D002) они пропускаются, а не падают.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import requires_data

from src.bot.errors import BotNotesError
from src.bot.sidecar import (
    SOURCE_COMMENT,
    SOURCE_PHOTO,
    Notes,
    SeenFrame,
    forget_source,
    notes_path,
    read,
    remember_frames,
    remember_source,
    remember_zone,
    reset,
    unclaimed,
)

pytestmark = requires_data

CHAT = 4101


def кадр(message_id: int, file_id: str) -> SeenFrame:
    return SeenFrame(message_id=message_id, file_id=file_id)


def test_заметок_нет_read_отдаёт_пустые(domain_env: Path) -> None:
    notes = read(CHAT)
    assert notes.sources == {}
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


def test_remember_source_на_двух_записях(domain_env: Path) -> None:
    remember_source(CHAT, 1, SOURCE_COMMENT)
    remember_source(CHAT, 2, SOURCE_PHOTO)

    sources = read(CHAT).sources
    assert sources == {1: SOURCE_COMMENT, 2: SOURCE_PHOTO}
    assert all(isinstance(n, int) for n in sources), "ключи наружу обязаны быть int"


def test_remember_source_с_чужим_источником_отказывает_и_не_портит_файл(domain_env: Path) -> None:
    remember_source(CHAT, 1, SOURCE_COMMENT)

    with pytest.raises(BotNotesError):
        remember_source(CHAT, 2, "догадка")

    assert read(CHAT).sources == {1: SOURCE_COMMENT}, "отказ не должен был тронуть файл"


def test_forget_source_убирает_источник(domain_env: Path) -> None:
    remember_source(CHAT, 1, SOURCE_COMMENT)
    remember_source(CHAT, 2, SOURCE_PHOTO)

    forget_source(CHAT, 1)

    assert read(CHAT).sources == {2: SOURCE_PHOTO}


def test_forget_source_на_неизвестном_номере_не_падает(domain_env: Path) -> None:
    remember_source(CHAT, 1, SOURCE_COMMENT)

    forget_source(CHAT, 99)  # не должно поднять исключение

    assert read(CHAT).sources == {1: SOURCE_COMMENT}, (
        "вызов на чужом номере не должен ничего менять"
    )


def test_unclaimed_отдаёт_только_кадры_вне_used(domain_env: Path) -> None:
    remember_frames(CHAT, [кадр(1, "AAA"), кадр(2, "BBB"), кадр(3, "CCC")])

    result = unclaimed(CHAT, {"BBB"})

    assert result == (кадр(1, "AAA"), кадр(3, "CCC"))


def test_unclaimed_с_пустым_used_отдаёт_все_кадры(domain_env: Path) -> None:
    remember_frames(CHAT, [кадр(1, "AAA"), кадр(2, "BBB")])

    assert unclaimed(CHAT, set()) == (кадр(1, "AAA"), кадр(2, "BBB"))


def test_reset_стирает_всё(domain_env: Path) -> None:
    remember_source(CHAT, 1, SOURCE_COMMENT)
    remember_frames(CHAT, [кадр(1, "AAA")])
    remember_zone(CHAT, "hot_kitchen")

    reset(CHAT)

    assert read(CHAT) == Notes(sources={}, frames=(), zone="")
    assert not notes_path(CHAT).is_file()


def test_reset_на_чате_без_заметок_не_падает(domain_env: Path) -> None:
    reset(CHAT)  # не должно поднять исключение

    assert read(CHAT).sources == {}


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


def test_нечисловой_ключ_в_sources_даёт_botnoteserror(domain_env: Path) -> None:
    path = notes_path(CHAT)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"schema": 1, "zone": "", "sources": {"первая": "comment"}, "frames": []}),
        encoding="utf-8",
    )

    with pytest.raises(BotNotesError):
        read(CHAT)


def test_незнакомый_источник_в_файле_даёт_botnoteserror(domain_env: Path) -> None:
    path = notes_path(CHAT)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"schema": 1, "zone": "", "sources": {"1": "догадка"}, "frames": []}),
        encoding="utf-8",
    )

    with pytest.raises(BotNotesError):
        read(CHAT)


def test_заметки_переживают_перезапуск(domain_env: Path) -> None:
    remember_source(CHAT, 1, SOURCE_COMMENT)
    remember_source(CHAT, 2, SOURCE_PHOTO)
    remember_frames(CHAT, [кадр(10, "AAA"), кадр(11, "BBB")])
    remember_zone(CHAT, "hot_kitchen")

    path = notes_path(CHAT)
    assert path.is_file(), "заметки обязаны лежать файлом в папке проверки"
    assert path.name == "bot.json"

    # «Перезапуск» — второе, независимое чтение с диска.
    notes = read(CHAT)
    assert notes.sources == {1: SOURCE_COMMENT, 2: SOURCE_PHOTO}
    assert notes.frames == (кадр(10, "AAA"), кадр(11, "BBB"))
    assert notes.zone == "hot_kitchen"


def test_заметки_разных_чатов_не_смешиваются(domain_env: Path) -> None:
    other = CHAT + 1
    remember_source(CHAT, 1, SOURCE_COMMENT)
    remember_zone(CHAT, "hot_kitchen")
    remember_source(other, 1, SOURCE_PHOTO)
    remember_zone(other, "bar")

    assert read(CHAT).sources == {1: SOURCE_COMMENT}
    assert read(CHAT).zone == "hot_kitchen"
    assert read(other).sources == {1: SOURCE_PHOTO}
    assert read(other).zone == "bar"
