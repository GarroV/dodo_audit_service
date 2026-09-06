"""Настройка подключения к MCP и небольшая админка вокруг неё (T209, T253).

Решения D087 (пункт меню и готовая строка), D098 (личный токен), D099 (пункт
виден не всем).

**Что человек получает.** Готовую строку настройки Claude Desktop со своим
личным токеном внутри — вставил в терминал, и подключение есть. Токен здесь
деталь строки, а не отдельный предмет разговора: человек не «получает токен», а
получает работающую настройку.

**Чего не было до T253.** Токен заводился руками в `.env` записью
«арендатор=токен» (`src/mcp/config.py`), принадлежал СТОРОНЕ и открывал всю её
историю проверок; связи «этот человек — этот токен» у бота не было никакой,
поэтому печатать он не мог ничей — напечатанный был бы выдан наугад. Отзыв же
был правкой файла с перезапуском сервера, то есть отъёмом доступа сразу у всех.

**Что появилось.** Токен выпускается на идентификатор человека, хранится
отпечатком (`src/db/mcp_access.py`), показывается один раз и при повторном
вызове заменяет прежний — о чём человеку говорится прямо, потому что снаружи
внезапно переставшая работать настройка выглядит поломкой Claude, а не
следствием собственного действия.

**Круг.** Пункт есть в меню не у всех (D099): меню телеграм объявляет на
аккаунт, поэтому состав объявляется разный (`app.announce_commands`). Круг
плоский — у всех в нём одинаковые возможности: выпустить себе токен, привести
следующего, отозвать поимённо. Системы прав здесь нет намеренно: это отдельная
работа, а не то, что тихо заводится вместе с первой таблицей.

**Основатель круга приходит из настройки стенда** и потому отзыву в чате не
поддаётся: отозванный, он вернулся бы при следующем подъёме бота, и отзыв
оказался бы сделанной и молча отменённой работой.
"""

from __future__ import annotations

import asyncio
import logging
import os

from aiogram import Bot, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message, User

from src.db.errors import DbError

from ..config import BotSettings
from ..lang import chat_ui_lang
from ..texts import t

logger = logging.getLogger(__name__)

#: Имя команды настройки. Она же объявляется в меню телеграма — но не всем
#: подряд (`app.MCP_MENU_COMMANDS`, решение D099).
MCP_COMMAND = "mcp"

#: Админка: привести в круг, отозвать поимённо, посмотреть, у кого доступ.
#: Три команды, и это весь её объём — по решению владельца о размере.
MCP_ADD_COMMAND = "mcp_add"
MCP_REVOKE_COMMAND = "mcp_revoke"
MCP_WHO_COMMAND = "mcp_who"

#: Адрес MCP-сервера, который бот ПЕЧАТАЕТ в команде установки. Не адрес, куда
#: бот ходит: он туда не ходит вовсе.
#:
#: Своя переменная, а не `MCP_HOST`/`MCP_PORT` соседнего блока, и причин две.
#: Первая — блоки пиры и друг друга не импортируют, а вторая важнее: сервер
#: слушает петлю (`127.0.0.1`), и человеку с другой машины эта строка не
#: подходит — он идёт туннелем, и в команде у него стоит свой адрес. Это разные
#: факты, и копией одного другой не является.
MCP_URL_VAR = "BOT_MCP_URL"


def setup_url(lang: str) -> str:
    """Адрес для команды установки — из окружения стенда или заглушка.

    Пусто — не отказ: пункт меню обязан работать и на стенде, где адрес никто
    не задавал, а заглушка ЗАГЛАВНЫМИ стоит в строке ровно там же, где стоял бы
    адрес, и видна человеку. Подставить сюда петлю по умолчанию значило бы
    выдать догадку за настройку — и человек искал бы, почему «готовая» команда
    не соединяется.
    """
    return (os.environ.get(MCP_URL_VAR) or "").strip() or t("mcp.url_unknown", lang)


async def _in_circle(user_id: int, settings: BotSettings) -> bool:
    """Доступна ли этому человеку настройка подключения прямо сейчас.

    **Основатель круга проверяется настройкой, а не таблицей**, и заодно в неё
    возвращается. Иначе стенд, поднятый с пустой базой, оставил бы админку без
    единого живого участника — попасть в круг можно только из круга, и завести
    его было бы нечем.

    Все остальные спрашиваются у базы КАЖДЫЙ раз, а не запоминаются на старте:
    отзыв обязан действовать немедленно, а список, снятый однажды в память, —
    ровно тот способ, которым «немедленно» превращается в «после перезапуска».
    """
    from src.db.mcp_access import add_admin, is_admin

    if settings.mcp_owner_id is not None and user_id == settings.mcp_owner_id:
        # Идемпотентно: живого участника не трогает, отозванного возвращает.
        await asyncio.to_thread(add_admin, user_id, by=None)
        return True
    return bool(await asyncio.to_thread(is_admin, user_id))


async def _guard(message: Message, settings: BotSettings) -> User | None:
    """Пустить в админку или ответить отказом. Возвращает того, кого пустили.

    Возвращается ЧЕЛОВЕК, а не «да/нет», намеренно: дальше он нужен каждому
    обработчику — на него выпускается токен и им подписывается след. Ответ
    «да» заставлял бы доставать отправителя второй раз и проверять на пустоту
    ещё раз, уже без всякой защиты, — то есть разводить второй ответ на
    вопрос, на который здесь уже ответили.

    Отказ отвечается словами, а не молчанием: человек здесь свой (мидлварь
    доступа его пустила), и проглоченная команда уехала бы дальше по цепочке
    роутеров в разбор материала — то есть его `/mcp` стал бы комментарием к
    ждущему кадру и попал бы в отчёт.

    Отказ базы наружу не пускается: круг спрашивается у базы, и её отказ
    означает «не смогли посмотреть», а не «вам нельзя». Пустить при этом
    внутрь нельзя тем более — поэтому ответ честно говорит про недоступность.
    """
    lang = chat_ui_lang(message.chat.id)
    if settings.mcp_owner_id is None:
        await message.answer(t("mcp.circle_unset", lang))
        return None
    user = message.from_user
    if user is None:
        # Обновление без отправителя: отвечать некому и выпускать не на кого.
        return None
    try:
        внутри = await _in_circle(user.id, settings)
    except DbError:
        logger.exception("круг доступа к MCP не прочитался для %s", user.id)
        await message.answer(t("mcp.unavailable", lang))
        return None
    if not внутри:
        await message.answer(t("mcp.not_yours", lang))
        return None
    return user


def _requested_id(command: CommandObject) -> int | None:
    """Telegram ID из аргумента команды. `None` — аргумента нет или он не число."""
    piece = (command.args or "").strip()
    if not piece or not piece.lstrip("-").isdigit():
        return None
    return int(piece)


async def _announce_personal_menu(bot: Bot | None, user_id: int, *, in_circle: bool) -> None:
    """Пересобрать меню одного человека сразу после выдачи или отзыва.

    Без этого выданный доступ появлялся бы в меню только после перезапуска
    бота, а отозванный — оставался бы в нём висеть. Пункт, которого не должно
    быть, человек нажмёт: он у него был.

    Отказ телеграма наружу не пускается: доступ выдан и записан в базе, а меню
    — витрина. Уронить состоявшуюся выдачу из-за неё нельзя; в журнал отказ
    попадает целиком.
    """
    from ..app import announce_personal_menu

    if bot is None:
        # Сообщение, пришедшее не через диспетчер: у него нет бота, а значит и
        # некому объявлять меню. Выдача при этом уже состоялась и записана —
        # молчать о расхождении нельзя, но и отменять её не за что.
        logger.warning("меню человека %s не пересобрано: у сообщения нет бота", user_id)
        return
    try:
        await announce_personal_menu(bot, user_id, in_circle=in_circle)
    except Exception:
        logger.exception("меню человека %s не пересобралось после правки круга", user_id)


def build_mcp_router(settings: BotSettings) -> Router:
    """Роутер настройки MCP и круга доступа к ней."""
    router = Router(name="mcp")

    @router.message(Command(MCP_COMMAND))
    async def on_mcp(message: Message) -> None:
        """Готовая строка настройки с личным токеном — тремя сообщениями.

        Разными сообщениями не для красоты: в телеграме копируется сообщение
        ЦЕЛИКОМ одним движением, и команда, склеенная с объяснением, приезжала
        бы в терминал вместе с ним. Здесь это важнее прежнего — в строке стоит
        настоящий токен.

        Порядок: объяснение, команда, и только потом — что прежний токен
        отозван. Последнее идёт после, а не до: человек, вызвавший пункт второй
        раз, первое сообщение читает по диагонали, а мимо последнего не пройдёт.
        """
        lang = chat_ui_lang(message.chat.id)
        user = await _guard(message, settings)
        if user is None:
            return
        from src.db.mcp_access import issue_token

        try:
            выпущен = await asyncio.to_thread(issue_token, user.id, tenant=settings.mcp_tenant)
        except DbError:
            # В журнал — разбор, человеку — что делать. Значения токена нет ни
            # там, ни там: выпуск до него не дошёл.
            logger.exception("токен доступа к MCP не выпущен для %s", user.id)
            await message.answer(t("mcp.unavailable", lang))
            return
        await message.answer(t("mcp.setup", lang))
        await message.answer(t("mcp.command", lang, url=setup_url(lang), token=выпущен.value))
        if выпущен.replaced_previous:
            await message.answer(t("mcp.replaced", lang))

    @router.message(Command(MCP_ADD_COMMAND))
    async def on_add(message: Message, command: CommandObject) -> None:
        """Привести следующего в круг: каждый, кто внутри, может позвать ещё одного.

        Токен приведённому здесь не выпускается и не показывается — он выпустит
        его себе сам. Это не удобство, а то же требование, что и «показан один
        раз»: токен, показанный не своему владельцу, уже утёк.
        """
        lang = chat_ui_lang(message.chat.id)
        user = await _guard(message, settings)
        if user is None:
            return
        кому = _requested_id(command)
        if кому is None:
            сказано = (command.args or "").strip()
            await message.answer(
                t("mcp.id_not_a_number", lang, given=сказано)
                if сказано
                else t("mcp.add_usage", lang)
            )
            return
        if кому not in settings.allowed_ids:
            # Доступ, выданный тому, кого мидлварь до бота не пускает, — это
            # запись в базе и ничего больше. Отказ здесь честнее.
            await message.answer(t("mcp.add_not_allowed", lang, who=кому))
            return
        from src.db.mcp_access import add_admin

        try:
            добавлен = await asyncio.to_thread(add_admin, кому, by=user.id)
        except DbError:
            logger.exception("не удалось привести %s в круг доступа к MCP", кому)
            await message.answer(t("mcp.unavailable", lang))
            return
        if not добавлен:
            await message.answer(t("mcp.already_in", lang, who=кому))
            return
        await _announce_personal_menu(message.bot, кому, in_circle=True)
        await message.answer(t("mcp.added", lang, who=кому))

    @router.message(Command(MCP_REVOKE_COMMAND))
    async def on_revoke(message: Message, command: CommandObject) -> None:
        """Отзыв поимённый и немедленный: и круг, и живые токены разом.

        Одним движением, а не двумя, и это не сокращение: отзыв одного токена
        оставил бы человека в круге, и следующим же вызовом он выпустил бы себе
        новый (`db.mcp_access.revoke_access`).
        """
        lang = chat_ui_lang(message.chat.id)
        user = await _guard(message, settings)
        if user is None:
            return
        у_кого = _requested_id(command)
        if у_кого is None:
            сказано = (command.args or "").strip()
            await message.answer(
                t("mcp.id_not_a_number", lang, given=сказано)
                if сказано
                else t("mcp.revoke_usage", lang)
            )
            return
        if у_кого == settings.mcp_owner_id:
            # Основателя называет настройка стенда: отозванный, он вернётся при
            # следующем подъёме. Отказ честнее молча отменённой работы.
            await message.answer(t("mcp.revoke_founder", lang, who=у_кого))
            return
        from src.db.mcp_access import revoke_access

        try:
            отзыв = await asyncio.to_thread(revoke_access, у_кого, by=user.id)
        except DbError:
            logger.exception("не удалось отозвать доступ к MCP у %s", у_кого)
            await message.answer(t("mcp.unavailable", lang))
            return
        if not отзыв.was_admin and отзыв.tokens_revoked == 0:
            await message.answer(t("mcp.revoke_nobody", lang, who=у_кого))
            return
        await _announce_personal_menu(message.bot, у_кого, in_circle=False)
        await message.answer(t("mcp.revoked", lang, who=у_кого, tokens=отзыв.tokens_revoked))

    @router.message(Command(MCP_WHO_COMMAND))
    async def on_who(message: Message) -> None:
        """След: кто в круге, кто кого привёл, у кого выпущен токен, кто отозван.

        Ради этого след и заведён: «доступ у тех, кого я назвал» через месяц
        нечем проверить, если этого не показать. Отвечает идентификаторами и
        датами — по ним и сверяют.
        """
        lang = chat_ui_lang(message.chat.id)
        if await _guard(message, settings) is None:
            return
        from src.db.mcp_access import list_admins

        try:
            круг = await asyncio.to_thread(list_admins)
        except DbError:
            logger.exception("не удалось прочитать круг доступа к MCP")
            await message.answer(t("mcp.unavailable", lang))
            return
        if not круг:
            await message.answer(t("mcp.who_empty", lang))
            return
        строки = [t("mcp.who_header", lang)]
        for запись in круг:
            if запись.is_live:
                кем = (
                    t("mcp.who_founder", lang)
                    if запись.added_by is None
                    else str(запись.added_by)
                )
                токен = t(
                    "mcp.who_has_token" if запись.has_live_token else "mcp.who_no_token", lang
                )
                строки.append(
                    t(
                        "mcp.who_line",
                        lang,
                        who=запись.telegram_id,
                        by=кем,
                        at=запись.added_at[:10],
                        token=токен,
                    )
                )
            else:
                строки.append(
                    t(
                        "mcp.who_line_revoked",
                        lang,
                        who=запись.telegram_id,
                        by=запись.revoked_by,
                        at=(запись.revoked_at or "")[:10],
                    )
                )
        await message.answer("\n".join(строки))

    return router
