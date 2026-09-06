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
from aiogram.types import BotCommand, BotCommandScopeChat, ErrorEvent, Message

from src import domain

from .access import AccessMiddleware
from .albums import ALBUM_WINDOW_SECONDS, AlbumBuffer
from .config import MCP_OWNER_ID_VAR, BotSettings, load_bot_settings
from .lang import chat_ui_lang
from .material import MaterialStore
from .pending import PendingStore
from .routers import (
    build_correct_router,
    build_edit_router,
    build_fallback_router,
    build_finish_router,
    build_info_router,
    build_material_router,
    build_mcp_router,
    build_record_router,
    build_records_router,
    build_start_router,
    build_version_router,
)
from .routers.material import MaterialHandler
from .routers.mcp import (
    MCP_ADD_COMMAND,
    MCP_COMMAND,
    MCP_REVOKE_COMMAND,
    MCP_WHO_COMMAND,
)
from .routers.record import make_frame_handler, make_material_handler, make_waiting_handler
from .routers.records import RECORDS_COMMAND
from .routers.version import VERSION_COMMAND
from .texts import DEFAULT_UI_LANG, default_ui_lang, t
from .version import build_version

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


def _lang_here(chat_id: int) -> str:
    """Язык последнего рубежа: обычный язык чата, а если и он отказал — русский.

    Ответ на языке стенда хуже ответа на языке проверки, но несравнимо лучше
    молчания: аудитор на точке должен узнать о сбое и увидеть выход.
    """
    try:
        return chat_ui_lang(chat_id)
    except Exception:
        logger.exception("язык чата %s не выбрался — отвечаем на %s", chat_id, DEFAULT_UI_LANG)
        return DEFAULT_UI_LANG


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

    **Язык выбирается до попытки ответить и со своей защитой.** Раньше и то и
    другое стояло в одном `try`, и обработчик умирал сам: нечитаемое по правам
    состояние роняло `chat_ui_lang`, а в журнал уходило «не удалось сказать
    аудитору о сбое» — последний рубеж честно сообщал, что промолчал. Теперь
    `chat_ui_lang` не падает по построению (`src/bot/lang.py`), но здесь стоит
    вторая защита: за этим обработчиком нет никого, и полагаться в нём на чужую
    исправность — это ровно та ошибка, которой задача T126 и посвящена.
    """
    logger.exception("необработанный сбой на обновлении", exc_info=event.exception)
    message = _asked_here(event)
    if message is None:
        return True
    lang = _lang_here(message.chat.id)
    try:
        await message.answer(t("error.unexpected", lang))
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
    # Показ записанного и завершение получают ту же очередь ожидания, из которой
    # собираются записи (T241): при завершении называются не только кадры без
    # записи, но и слова, кадра не дождавшиеся.
    dispatcher.include_router(build_records_router(store))
    # Установка MCP с админкой круга (T209, T253) и версия сборки (T246) —
    # рядом с остальными командами: своих состояний диалога у них нет, обычного
    # текста они не ждут, и на порядок разбора материала не влияют.
    dispatcher.include_router(build_mcp_router(settings))
    dispatcher.include_router(build_version_router())
    dispatcher.include_router(build_finish_router(store))
    # Информационная часть ждёт обычный текст, голос и кадр в своём состоянии
    # диалога (T158), поэтому идёт раньше приёма материала: иначе ответ на
    # вопрос уехал бы в разбор как комментарий к кадру.
    dispatcher.include_router(build_info_router())
    dispatcher.include_router(build_record_router(store=store, pending=pending))
    # Правка ответом на сообщение бота (T204) — ДО приёма материала: иначе ответ
    # уедет комментарием к ждущему кадру, и вместо правки записи появится
    # вторая. Ответ не про запись этот роутер пропускает дальше нетронутым.
    dispatcher.include_router(build_correct_router(pending=pending))
    dispatcher.include_router(
        build_material_router(
            store=store,
            albums=AlbumBuffer(),
            on_material=on_material or make_material_handler(pending),
            on_waiting=make_waiting_handler(pending),
            # Кадр ответом на свои же слова уходит в ту запись, о которой они
            # были (T205): без этого он завёл бы вторую очередь ожидания и
            # второе нарушение о том же самом.
            on_frame=make_frame_handler(),
            album_window=album_window,
        )
    )
    # Последним и без фильтра данных (T134): нажатие, которое не подошло ни
    # одному обработчику выше, обязано получить ответ, иначе телеграм крутит
    # человеку часики до собственного таймаута. Порядок здесь и есть весь
    # механизм — стой этот роутер раньше, он съедал бы живые нажатия.
    dispatcher.include_router(build_fallback_router())
    return dispatcher


def create_bot(settings: BotSettings) -> Bot:
    return Bot(token=settings.token, default=DefaultBotProperties())


#: Команды в меню телеграма: имя и ключ описания в каталоге текстов.
#:
#: Порядок — порядок работы аудитора, а не алфавит: начать, посмотреть
#: записанное, снять последнее, завершить. Версия сборки (T246) стоит последней
#: и этот порядок не ломает: она не шаг обхода, а вопрос «что у нас крутится», —
#: и место ей за работой, а не посреди неё.
#:
#: Чего в меню нет намеренно: снятия сданной проверки. По решению D086 это
#: операция управляющей компании через MCP, а не кнопка у аудитора на точке, —
#: и входа в неё бот не получает вовсе (`src/mcp/retraction.py`).
MENU_COMMANDS = (
    ("start", "cmd.start"),
    (RECORDS_COMMAND, "cmd.records"),
    ("undo", "cmd.undo"),
    ("finish", "cmd.finish"),
    (VERSION_COMMAND, "cmd.version"),
)

#: Пункты, которые видит ТОЛЬКО круг доступа к MCP (T253, решение D099).
#:
#: Отдельным списком, а не флагом внутри общего: телеграм объявляет меню на
#: аккаунт, то есть «видно не всем» выражается двумя РАЗНЫМИ объявлениями, а не
#: одним отфильтрованным. Установка стоит первой — за ней приходят, остальные
#: три обслуживают сам круг.
#:
#: Меню — витрина, а не заслон: он стоит в самих обработчиках
#: (`routers/mcp.py`, `_guard`), потому что команду можно набрать руками, не
#: заглядывая в меню. Здесь решается только, кому её показывать.
MCP_MENU_COMMANDS = (
    (MCP_COMMAND, "cmd.mcp"),
    (MCP_ADD_COMMAND, "cmd.mcp_add"),
    (MCP_REVOKE_COMMAND, "cmd.mcp_revoke"),
    (MCP_WHO_COMMAND, "cmd.mcp_who"),
)


def _menu(commands: tuple[tuple[str, str], ...], lang: str) -> list[BotCommand]:
    return [BotCommand(command=name, description=t(key, lang)) for name, key in commands]


async def announce_commands(bot: Bot, circle: tuple[int, ...] = ()) -> None:
    """Объявить команды в меню телеграма (T139, T253).

    Без этого команда есть, но её не видно: аудитор на точке не набирает
    `/records` по памяти — он нажимает синюю кнопку меню и выбирает из списка.
    Ровно из-за этого показ записанного и был спрятан за «Завершить»: другого
    входа у него не было, а этот никто не показывал.

    **Объявлений теперь два вида.** Общее меню видят все, кого пускает мидлварь
    доступа; тем, кто в круге доступа к MCP, поверх общего объявляется своё, с
    настройкой подключения (D099). Иначе это не выражается вовсе: телеграм
    держит меню на аккаунт, а не на роль.

    Язык — язык стенда (`BOT_UI_LANG`, T131), а не проверки: меню телеграм
    спрашивает один раз при подъёме бота, когда никакой проверки ещё нет.

    Отказ телеграма сюда не пускается наружу: без меню бот работает, а не
    подняться из-за него — цена несоразмерная. В журнал он попадает целиком.
    Круг при этом обходится по одному, и отказ на одном человеке не отменяет
    объявления остальным: иначе один недоступный чат оставил бы без меню всех.
    """
    lang = default_ui_lang()
    try:
        await bot.set_my_commands(_menu(MENU_COMMANDS, lang))
    except Exception:
        logger.exception("команды в меню телеграма не объявились — бот работает без меню")
    for user_id in circle:
        try:
            await announce_personal_menu(bot, user_id, in_circle=True)
        except Exception:
            logger.exception("меню круга доступа не объявилось человеку %s", user_id)


async def announce_personal_menu(bot: Bot, user_id: int, *, in_circle: bool) -> None:
    """Объявить или убрать личное меню круга у одного человека (T253).

    Зовётся и при подъёме бота, и сразу после выдачи или отзыва доступа: пункт,
    появляющийся только после перезапуска, — это не выданный доступ, а отзыв,
    оставляющий пункт висеть, человек нажмёт, потому что тот у него был.

    Убирается именно УДАЛЕНИЕМ личного меню, а не объявлением общего под видом
    личного: удалённое возвращает человека к общему меню, и оно потом
    пополняется само собой. Объявленная копия общего меню осталась бы копией и
    молча отстала бы от него при следующем пополнении.

    Отказ телеграма отсюда наружу УХОДИТ — в отличие от `announce_commands`.
    Разница по существу: там меню объявляется скопом при подъёме, и падать
    из-за одного чата нельзя, а здесь зовущий (`routers/mcp.py`) сам решает,
    что делать с отказом, и уже это делает — пишет в журнал и продолжает.
    """
    lang = default_ui_lang()
    scope = BotCommandScopeChat(chat_id=user_id)
    if in_circle:
        await bot.set_my_commands(_menu(MENU_COMMANDS + MCP_MENU_COMMANDS, lang), scope=scope)
    else:
        await bot.delete_my_commands(scope=scope)


def log_startup(settings: BotSettings) -> None:
    """Первая строка журнала: режим, доступ и ВЕРСИЯ сборки.

    Версия здесь не для порядка. Когда бот отвечает отказом, первым уходит
    время на выяснение того, что вообще крутится на площадке, — а образ и
    каталог с кодом расходятся молча (T246, #201).
    """
    logger.info(
        "бот поднят в режиме %s, разрешённых ID: %s, сборка: %s",
        settings.mode,
        len(settings.allowed_ids),
        build_version(),
    )


def circle_at_startup(settings: BotSettings) -> tuple[int, ...]:
    """Кому объявлять меню круга при подъёме бота (T253).

    Основатель приводится в круг здесь же: стенд, поднятый с пустой базой,
    иначе остался бы без единого живого участника — попасть в круг можно
    только из круга, и завести его было бы нечем.

    **Отказ базы не мешает боту подняться.** Обход точки — работа аудитора, и
    останавливать её из-за недоступного списка доступа к MCP несоразмерно:
    круг тогда просто не объявляется в меню, а сама настройка всё равно
    спрашивает базу на каждое обращение и откажет по месту. В журнал отказ
    попадает целиком — молча это не проходит.
    """
    if settings.mcp_owner_id is None:
        logger.warning(
            "круг доступа к MCP не назначен (%s не задан) — настройка подключения "
            "не доступна никому",
            MCP_OWNER_ID_VAR,
        )
        return ()
    try:
        from src.db.mcp_access import add_admin, list_admins

        add_admin(settings.mcp_owner_id, by=None)
        return tuple(row.telegram_id for row in list_admins() if row.is_live)
    except Exception:
        logger.exception("круг доступа к MCP не прочитался — меню круга не объявлено")
        return ()


async def start_polling() -> None:
    """Поднять бота: проверить окружение, затем слушать Telegram."""
    settings = load_bot_settings()
    # Методика проверяется до первого сообщения: пустой чек-лист читался бы как
    # честный ответ «нарушений нет», а узнать об этом на точке — поздно.
    domain.check_environment()
    bot = create_bot(settings)
    await announce_commands(bot, circle_at_startup(settings))
    dispatcher = build_dispatcher(settings)
    log_startup(settings)
    try:
        await dispatcher.start_polling(bot, handle_as_tasks=False)
    finally:
        await bot.session.close()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    asyncio.run(start_polling())
