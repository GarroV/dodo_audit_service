"""Сборка бота: диспетчер, роутеры, доступ, запуск (задача T050).

Точка входа — `python -m src.bot`. Режим только long polling (решение D004,
`BOT_MODE`): постоянного публичного адреса нет ни на ноутбуке, ни на домашнем
сервере, а вебхук без него не поднять.

**Обновления обрабатываются последовательно** (`handle_as_tasks=False`). Это не
экономия и не осторожность, а требование задачи T059: по умолчанию aiogram
заводит задачу на каждое обновление, и пачка сообщений, пришедшая разом после
потери связи, разбирается вперемешку — комментарии привязываются не к тем
кадрам. Последовательная обработка даёт порядок по построению: Telegram
сохраняет порядок доставки внутри чата, и бот его больше не перемешивает.
Цена — пока идёт разбор одного сообщения, следующее ждёт; для проверки, где
работает один аудитор и шаги идут строго друг за другом, это и есть желаемое
поведение.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage

from src import domain

from .access import AccessMiddleware
from .albums import ALBUM_WINDOW_SECONDS, AlbumBuffer
from .config import BotSettings, load_bot_settings
from .material import MaterialStore
from .routers import build_material_router, build_start_router
from .routers.material import MaterialHandler, confirm_link

logger = logging.getLogger(__name__)


def build_dispatcher(
    settings: BotSettings,
    *,
    album_window: float = ALBUM_WINDOW_SECONDS,
    on_material: MaterialHandler = confirm_link,
) -> Dispatcher:
    """Диспетчер со всеми роутерами и мидлварью доступа.

    `on_material` — что делать с готовым материалом. Во второй очереди сюда
    встанет разбор (задача T055); сейчас это подтверждение связывания, и оно же
    точка, за которую держатся тесты приёма материала.

    Хранилище конечного автомата — в памяти намеренно: в нём живут только шаги
    мастера начала проверки, а сама проверка лежит в файле и переживает
    перезапуск без него (`src/bot/states.py`).
    """
    dispatcher = Dispatcher(storage=MemoryStorage())

    access = AccessMiddleware(settings.allowed_ids)
    dispatcher.message.outer_middleware(access)
    dispatcher.callback_query.outer_middleware(access)

    dispatcher.include_router(build_start_router(settings))
    dispatcher.include_router(
        build_material_router(
            store=MaterialStore(),
            albums=AlbumBuffer(),
            on_material=on_material,
            album_window=album_window,
        )
    )
    return dispatcher


def create_bot(settings: BotSettings) -> Bot:
    return Bot(token=settings.token, default=DefaultBotProperties())


async def start_polling() -> None:
    """Поднять бота: проверить окружение, затем слушать Telegram."""
    settings = load_bot_settings()
    # Методика проверяется до первого сообщения: пустой чек-лист читался бы как
    # честный ответ «нарушений нет», а узнать об этом на точке — поздно.
    domain.check_environment()
    bot = create_bot(settings)
    dispatcher = build_dispatcher(settings)
    logger.info(
        "бот поднят в режиме %s, разрешённых ID: %s", settings.mode, len(settings.allowed_ids)
    )
    try:
        await dispatcher.start_polling(bot, handle_as_tasks=False)
    finally:
        await bot.session.close()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    asyncio.run(start_polling())
