"""Скачивание файлов из телеграма: кадры для разбора и для отчёта.

`src/bot/photos.py` нарочно не поднимает исключений наружу: отказ телеграма
превращается в `None`, а вызывающие решают, что с этим делать (разбор идёт по
одному комментарию, сборка отчёта — спрашивает аудитора). Молчаливым отказ
при этом оставаться не должен: если он не попал в журнал, найти пропавший
кадр постфактум нечем. Поэтому здесь тесты доказывают именно это — отказ не
проглочен, а виден в логе с идентификатором файла, — а не только то, что
функция вернула `None`.

Сеть заменена оснасткой `bot_harness.RecordingSession`: до Telegram тесты не
доходят. Там, где нужно настоящее содержимое файла (а не пустой поток из
`RecordingSession.stream_content`), сессия расширена локально.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any, BinaryIO

import pytest
from aiogram import Bot
from bot_harness import FAKE_TOKEN, RecordingSession, make_bot

from src.bot.photos import download_all, fetch_bytes

pytestmark = pytest.mark.asyncio


class _ContentSession(RecordingSession):
    """`RecordingSession`, отдающая на скачивание настоящие байты, а не пустой поток.

    Базовая `stream_content` всегда возвращает `b""` — этого достаточно, чтобы
    проверить, что `fetch_bytes` вообще что-то вернула, но недостаточно, чтобы
    проверить, что на диске лежит настоящий файл с ожидаемым содержимым
    (`download_all`, случаи 3–5 ниже).
    """

    def __init__(self, content: bytes) -> None:
        super().__init__()
        self._content = content

    async def stream_content(
        self,
        url: str,
        headers: dict[str, Any] | None = None,
        timeout: int = 30,  # noqa: ASYNC109 — сигнатура из BaseSession
        chunk_size: int = 65536,
        raise_for_status: bool = True,
    ) -> AsyncGenerator[bytes, None]:
        yield self._content


def _content_bot(content: bytes) -> tuple[Bot, _ContentSession]:
    """Бот, чьё скачивание отдаёт настоящее содержимое файла."""
    session = _ContentSession(content)
    return Bot(token=FAKE_TOKEN, session=session), session


def _assert_saved_file(path_str: str, content: bytes) -> None:
    """Диск читается синхронной функцией нарочно: правило ASYNC240 запрещает
    трогать `pathlib` внутри `async def` — там место только циклу событий."""
    path = Path(path_str)
    assert path.is_file(), f"путь есть в карте, а файла на диске нет: {path}"
    assert path.read_bytes() == content


async def test_fetch_bytes_returns_the_file_when_telegram_has_it() -> None:
    """Телеграм отдал файл — `fetch_bytes` возвращает именно его байты."""
    bot, _session = _content_bot(b"jpeg-frame-content")

    result = await fetch_bytes(bot, "frame-1")

    assert result == b"jpeg-frame-content"


async def test_fetch_bytes_returns_none_and_logs_the_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Отказ телеграма — `None`, а не исключение, и запись в журнал по `file_id`.

    Отказ моделируется честной подменой `bot.download` (то, что реально
    вызывает `fetch_bytes`), а не обращением к внутренностям модуля.
    """
    bot, _session = make_bot()

    async def failing_download(*args: object, **kwargs: object) -> BinaryIO | None:
        raise RuntimeError("сеть недоступна")

    monkeypatch.setattr(bot, "download", failing_download)

    with caplog.at_level(logging.ERROR, logger="src.bot.photos"):
        result = await fetch_bytes(bot, "frame-missing")

    assert result is None
    assert any(
        "frame-missing" in record.message and record.name == "src.bot.photos"
        for record in caplog.records
    ), "отказ обязан попасть в журнал с идентификатором файла, а не потеряться молча"


async def test_download_all_saves_frames_as_real_files(tmp_path: Path) -> None:
    """Кадры ложатся файлами во временную папку, и по каждому пути — настоящий файл."""
    bot, _session = _content_bot(b"real-frame-bytes")
    dest = tmp_path / "frames"

    result = await download_all(bot, ["frame-1", "frame-2"], dest)

    assert set(result) == {"frame-1", "frame-2"}
    for path_str in result.values():
        _assert_saved_file(path_str, b"real-frame-bytes")


async def test_download_all_downloads_a_repeated_id_only_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Повторяющийся `file_id` — одна запись в карте, и скачивание вызвано один раз."""
    bot, _session = _content_bot(b"real-frame-bytes")
    calls: list[str] = []
    original = bot.download

    async def counting_download(
        file: str,
        destination: BinaryIO | Path | str | None = None,
        timeout: int = 30,  # noqa: ASYNC109 — сигнатура из `Bot.download`
        chunk_size: int = 65536,
        seek: bool = True,
    ) -> BinaryIO | None:
        calls.append(file)
        return await original(
            file, destination=destination, timeout=timeout, chunk_size=chunk_size, seek=seek
        )

    monkeypatch.setattr(bot, "download", counting_download)

    result = await download_all(bot, ["dup", "dup", "other"], tmp_path)

    assert set(result) == {"dup", "other"}
    assert calls.count("dup") == 1, "повторный file_id не должен скачиваться второй раз"
    assert calls.count("other") == 1


async def test_download_all_drops_the_frame_that_failed_to_download(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Кадр, который не скачался, не попадает в карту — остальные попадают.

    Это то, на чём держится решение блока `report`: пропажу файла из карты он
    читает как потерю доказательства и спрашивает аудитора, а не печатает
    пустоту. Поэтому карта обязана честно не содержать то, чего не скачалось.
    """
    bot, _session = _content_bot(b"real-frame-bytes")
    original = bot.download

    async def flaky_download(
        file: str,
        destination: BinaryIO | Path | str | None = None,
        timeout: int = 30,  # noqa: ASYNC109 — сигнатура из `Bot.download`
        chunk_size: int = 65536,
        seek: bool = True,
    ) -> BinaryIO | None:
        if file == "missing-frame":
            raise RuntimeError("сеть недоступна")
        return await original(
            file, destination=destination, timeout=timeout, chunk_size=chunk_size, seek=seek
        )

    monkeypatch.setattr(bot, "download", flaky_download)

    result = await download_all(bot, ["ok-frame", "missing-frame"], tmp_path)

    assert set(result) == {"ok-frame"}
    _assert_saved_file(result["ok-frame"], b"real-frame-bytes")


async def test_download_all_with_no_frames_returns_an_empty_map(tmp_path: Path) -> None:
    """Пустой список кадров — пустая карта, без падения и без обязательной папки."""
    bot, _session = _content_bot(b"unused")
    dest = tmp_path / "never-created"

    result = await download_all(bot, [], dest)

    assert result == {}
    assert not dest.exists(), "папка создаётся только при первой настоящей записи"
