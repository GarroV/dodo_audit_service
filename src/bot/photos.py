"""Получение файлов из телеграма: кадры для разбора и для отчёта, голос для транскрипции.

Токен телеграма есть только у бота — ни `recognize`, ни `report` скачать кадр не
могут (контракт блока `report`: «кадры резолвит блок по карте, которую даёт
бот»). Поэтому качает этот модуль, а нижние слои получают уже байты или готовый
путь к файлу.

Отказ телеграма здесь не поднимается наружу исключением, а превращается в `None`
и запись в журнал. Причина в том, что делают вызывающие: разбор без кадра идёт
по одному комментарию, а сборка отчёта считает пропажу кадра потерей
доказательства и спрашивает аудитора (`report.PhotoMissing`). И то, и другое —
осмысленное поведение, а падение хендлера посреди проверки — нет.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from io import BytesIO
from pathlib import Path

from aiogram import Bot

logger = logging.getLogger(__name__)

#: Сколько ждать файл. Кадр с телефона на плохой связи доезжает не мгновенно, а
#: аудитор стоит на точке и ждёт ответа — держать его дольше бессмысленно.
DOWNLOAD_TIMEOUT_SEC = 30


def _save(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


async def fetch_bytes(bot: Bot, file_id: str) -> bytes | None:
    """Скачать файл телеграма в память. Не получилось — `None` и запись в журнал."""
    try:
        buffer = await bot.download(file_id, destination=BytesIO(), timeout=DOWNLOAD_TIMEOUT_SEC)
    except Exception:
        logger.exception("не удалось скачать файл %s", file_id)
        return None
    if buffer is None:
        return None
    return buffer.read()


#: Сколько кадров качаем одновременно. Не «побольше»: телеграм отвечает лимитом
#: на всплеск запросов от одного бота, и отказ вместо кадра дороже секунды.
MAX_PARALLEL = 5


async def download_all(bot: Bot, file_ids: Iterable[str], dest: Path) -> dict[str, str]:
    """Скачать кадры в папку и вернуть карту «идентификатор → путь к файлу».

    Карта нужна сборке отчёта: `report.build_pdf` принимает `fetch_photo` —
    синхронную функцию, а качать из неё нельзя, она выполняется в рабочем потоке
    вне цикла событий. Поэтому кадры скачиваются заранее, а резолверу остаётся
    заглянуть в готовую карту.

    Кадры, которых нет, в карту не попадают — и `report` честно назовёт записи,
    потерявшие доказательство, вместо того чтобы напечатать пустоту.

    **Качаются кадры одновременно, а не по одному** (T101). Проверка на точке —
    это несколько десятков кадров, и последовательная выкачка складывает
    задержки сети в минуты ожидания ровно там, где аудитор уже нажал
    «Завершить» и ждёт документ. Одновременность ограничена `MAX_PARALLEL`:
    без предела десятки запросов разом упираются в лимиты телеграма, и вместо
    ускорения выходит отказ.
    """
    ids = list(dict.fromkeys(file_ids))
    limit = asyncio.Semaphore(MAX_PARALLEL)

    async def one(number: int, file_id: str) -> tuple[str, str] | None:
        async with limit:
            raw = await fetch_bytes(bot, file_id)
        if raw is None:
            return None
        # Имя по порядковому номеру, а не по идентификатору телеграма: тот
        # длинный, приходит от чужой стороны и в имени файла ему не место.
        path = dest / f"photo-{number:03d}.jpg"
        # Запись — в рабочем потоке: цикл событий обслуживает и вход телеграма,
        # и таймеры альбомов, и вставать на диске ему нельзя.
        await asyncio.to_thread(_save, path, raw)
        return file_id, str(path)

    pairs = await asyncio.gather(*(one(n, f) for n, f in enumerate(ids)))
    # Порядок карты сохраняется тот же, что у входа: `gather` возвращает
    # результаты в порядке задач, а не завершения.
    return {file_id: path for pair in pairs if pair for file_id, path in [pair]}
