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
from aiogram.types import ErrorEvent, Message

from src import domain

from .access import AccessMiddleware
from .albums import ALBUM_WINDOW_SECONDS, AlbumBuffer
from .config import BotSettings, load_bot_settings
from .lang import chat_ui_lang
from .material import MaterialStore
from .pending import PendingStore
from .routers import (
    build_edit_router,
    build_finish_router,
    build_material_router,
    build_record_router,
    build_start_router,
)
from .routers.material import MaterialHandler
from .routers.record import make_material_handler, make_waiting_handler
from .texts import t

logger = logging.getLogger(__name__)


def _asked_here(event: ErrorEvent) -> Message | None:
    """Сообщение, на которое можно ответить, — или ничего.

    Ничего бывает по-настоящему: обновление без чата (нажатие старше 48 часов
    приходит без сообщения), служебные события. Отвечать тогда некому, и
    остаётся журнал.
    """
    update = event.update
    if update.message is not None:
        return update.message
    callback = update.callback_query
    if callback is not None and isinstance(callback.message, Message):
        return callback.message
    return None


async def on_unexpected_error(event: ErrorEvent) -> bool:
    """Сбой, который не поймал ни один хендлер (задача T126).

    Без этого обработчика aiogram неперехваченное исключение **глотает**:
    аудитор не получает ничего. Проверено на испорченном `inspection.json` —
    `/start`, `/finish`, приём кадра и правка бросали отказ, а в чат не
    приходило ни строки, и выйти из тупика было нечем.

    Аудитору уходит причина и что делать, разбор — в журнал. Текст исключения
    в чат не попадает: он написан для того, кто чинит, в нём пути к файлам и
    внутренние подробности, а человеку на точке нужен выход.

    Возвращает `True`: событие обработано. Ошибку это не прячет — она уже в
    журнале целиком, со стеком; `False` означало бы, что aiogram напишет её
    ещё раз своими словами.
    """
    logger.exception("необработанный сбой на обновлении", exc_info=event.exception)
    message = _asked_here(event)
    if message is None:
        return True
    try:
        await message.answer(t("error.unexpected", chat_ui_lang(message.chat.id)))
    except Exception:
        # Ответить не вышло — телеграм отказал или чат недоступен. Падать
        # отсюда нельзя: это последний рубеж, за ним обработчика уже нет.
        logger.exception("не удалось сказать аудитору о сбое в чате %s", message.chat.id)
    return True


def build_dispatcher(
    settings: BotSettings,
    *,
    album_window: float = ALBUM_WINDOW_SECONDS,
    on_material: MaterialHandler | None = None,
) -> Dispatcher:
    """Диспетчер со всеми роутерами и мидлварью доступа.

    `on_material` подменяется только тестами связывания: им важно, что с чем
    связалось, а не что предложила модель. В продукте это разбор из
    `routers/record.py` — тот самый, который показывает кандидатов кнопками.

    Порядок роутеров не случаен. Мастер начала проверки и правка формулировки
    ждут обычный текст в состоянии диалога, поэтому идут раньше приёма
    материала: иначе название пиццерии уедет в разбор как комментарий к кадру.

    Хранилище конечного автомата — в памяти намеренно: в нём живут только шаги
    мастера начала проверки и вопрос про новую формулировку, а сама проверка
    лежит в файле и переживает перезапуск без него (`src/bot/states.py`).
    """
    dispatcher = Dispatcher(storage=MemoryStorage())
    # Раньше роутеров: сбой в любом из них обязан дойти до аудитора, а не
    # раствориться в aiogram (T126).
    dispatcher.errors.register(on_unexpected_error)

    access = AccessMiddleware(settings.allowed_ids)
    dispatcher.message.outer_middleware(access)
    dispatcher.callback_query.outer_middleware(access)

    store = MaterialStore()
    pending = PendingStore()

    dispatcher.include_router(build_start_router(settings, pending))
    dispatcher.include_router(build_edit_router())
    dispatcher.include_router(build_finish_router())
    dispatcher.include_router(build_record_router(store=store, pending=pending))
    dispatcher.include_router(
        build_material_router(
            store=store,
            albums=AlbumBuffer(),
            on_material=on_material or make_material_handler(pending),
            on_waiting=make_waiting_handler(pending),
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
