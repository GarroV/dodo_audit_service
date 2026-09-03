"""Заметки бота рядом с проверкой: то, чего нет ни у движка, ни в отчёте.

Две вещи, которые бот обязан помнить о проверке, а движок никогда не узнает:

* все кадры, присланные за проверку (задача T068) — в конце проверки нужно
  показать те, что не попали ни в одну запись, иначе они бесследно исчезают;
* последняя названная зона (решение D048) — подставляется догадкой к
  следующему кадру.

Источник записи (решение D044) здесь когда-то тоже лежал и уехал отсюда
задачей T108: он оказался нужен не одному боту, а всем — в базу источник
доезжает вместе с записью. Теперь его хранит сама проверка
(`domain.add_finding(..., source=...)`, `Finding.source`), и заметки бота о
нём ничего не знают. Второй копии здесь заводить нельзя: разошедшись, две
копии спорили бы о том, за какую формулировку отвечает аудитор.

Заметки обязаны пережить перезапуск бота, поэтому лежат файлом `bot.json` в
той же папке проверки, что и `inspection.json` (см. `src/report/photos.py` —
блок `report` берёт папку проверки тем же способом). Файл заводит и читает
только этот модуль; движок про него не знает и не тронет.

Испорченный файл здесь — не повод молча начать с пустого места: так из
проверки бесследно исчез бы список кадров, а задача T068 существует ровно
затем, чтобы кадры не терялись молча. Поэтому любая нечитаемая или неполная
форма поднимает `BotNotesError`, а не подменяется пустыми заметками.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Container, Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from src.domain import check_environment
from src.domain.engine import chat_dir

from .errors import BotNotesError

NOTES_FILE_NAME = "bot.json"
SCHEMA = 1


@dataclass(frozen=True)
class SeenFrame:
    """Кадр, который аудитор прислал в чат, — независимо от того, стал ли он записью."""

    message_id: int
    file_id: str


@dataclass(frozen=True)
class Notes:
    """Заметки одной проверки: все присланные кадры и последняя названная зона."""

    #: Все присланные кадры в порядке прихода. Не только те, что стали записью.
    frames: tuple[SeenFrame, ...]
    #: Последняя названная зона; пустая строка — её не было или её забыли.
    zone: str


def notes_path(chat_id: int) -> Path:
    """Файл заметок этого чата: та же папка проверки, что и у `inspection.json`."""
    return chat_dir(chat_id, check_environment()) / NOTES_FILE_NAME


def _frames_from_raw(raw: Any, path: Path) -> tuple[SeenFrame, ...]:
    if raw is None:
        return ()
    try:
        return tuple(
            SeenFrame(message_id=int(item["message_id"]), file_id=str(item["file_id"]))
            for item in raw
        )
    except (TypeError, KeyError, ValueError) as exc:
        raise BotNotesError(f"Список кадров в заметках {path} не похож на список кадров") from exc


def _decode(path: Path) -> dict[str, Any]:
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BotNotesError(f"Заметки бота испорчены и не читаются: {path} ({exc})") from exc
    if not isinstance(data, dict):
        raise BotNotesError(f"Заметки бота не похожи на заметки — не объект: {path}")
    return data


def _parse(raw: dict[str, Any], path: Path) -> Notes:
    # Ключ `sources` заметок, написанных до T108, просто не читается: проверки,
    # начатые тогда, ещё в работе, и отказ на незнакомом ключе означал бы, что
    # после обновления бота их стало нечем завершить.
    return Notes(
        frames=_frames_from_raw(raw.get("frames"), path),
        zone=str(raw.get("zone") or ""),
    )


def read(chat_id: int) -> Notes:
    """Заметки чата. Файла нет — пустые, а не отказ: до первой записи это норма."""
    path = notes_path(chat_id)
    if not path.is_file():
        return Notes(frames=(), zone="")
    return _parse(_decode(path), path)


def _write(chat_id: int, notes: Notes) -> None:
    """Временный файл рядом, `os.replace` — та же схема, что у движка (`domain/state.py`).

    Обновления бота идут строго последовательно (`handle_as_tasks=False`),
    поэтому блокировка не нужна: конкурентной записи в один файл заметок
    здесь не бывает, в отличие от `inspection.json`, куда пишет ещё и движок.
    """
    path = notes_path(chat_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw: dict[str, Any] = {
        "schema": SCHEMA,
        "zone": notes.zone,
        "frames": [{"message_id": f.message_id, "file_id": f.file_id} for f in notes.frames],
    }
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".bot-notes-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(raw, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def reset(chat_id: int) -> None:
    """Начата новая проверка — заметки с нуля. Файла не было — тоже нормальный исход."""
    notes_path(chat_id).unlink(missing_ok=True)


def remember_frames(chat_id: int, frames: Iterable[SeenFrame]) -> None:
    """Дописать кадры в конец списка. Уже известный `file_id` не дублируется.

    Дедуп идёт и против уже сохранённых кадров, и внутри самого добавляемого
    пакета — иначе повтор `file_id` в одном вызове проскочил бы мимо проверки.
    """
    notes = read(chat_id)
    seen = {f.file_id for f in notes.frames}
    additions: list[SeenFrame] = []
    for frame in frames:
        if frame.file_id in seen:
            continue
        seen.add(frame.file_id)
        additions.append(frame)
    if not additions:
        return
    _write(chat_id, replace(notes, frames=notes.frames + tuple(additions)))


def remember_zone(chat_id: int, zone: str) -> None:
    """Запомнить последнюю названную зону. Пустая строка стирает память о ней."""
    notes = read(chat_id)
    _write(chat_id, replace(notes, zone=zone))


def unclaimed(chat_id: int, used: Container[str]) -> tuple[SeenFrame, ...]:
    """Кадры, чьего `file_id` нет в `used`, — в порядке прихода."""
    notes = read(chat_id)
    return tuple(f for f in notes.frames if f.file_id not in used)
