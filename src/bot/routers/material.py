"""Приём материала: кадры, альбомы и связывание с комментарием (T053, T054, T059).

Комментарий связывается с кадром тремя способами (`docs/06-mvp-bot.md`, шаг 3):
подписью к фото, отдельным сообщением следом, ответом на любое из ранее
отправленных фото. Альбом склеивается по `media_group_id` в один материал.

**Уточняющего вопроса на кадр без комментария здесь нет.** Требование спеки
отменено решением D043 и заменено кнопкой «Разобрать?» (D046): без нажатия
человека ни один кадр в модель не уходит. Сама кнопка и разбор — задача T067,
вторая очередь; здесь кадр просто ждёт, и пришедший следом комментарий его
забирает. Поэтому в этом модуле нет ни одного места, которое T067 придётся
переделывать: ему остаётся заменить текст подтверждения приёма на вопрос с
кнопкой и подписаться на нажатие.

Порядок событий держится не таймингом, а тем, что обновления обрабатываются
последовательно (`src/bot/app.py`, `handle_as_tasks=False`). Это и есть ответ
на задачу T059: пачка сообщений, пришедшая разом после потери связи, разбирается
в том же порядке, в каком аудитор её отправлял, — Telegram порядок доставки
внутри чата сохраняет, а бот его больше не перемешивает.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from aiogram import F, Router
from aiogram.types import Message

from src import domain

from ..albums import ALBUM_WINDOW_SECONDS, AlbumBuffer, Frame
from ..material import Comment, Material, MaterialStore, PhotoGroup
from ..texts import t, ui_lang_or_default

logger = logging.getLogger(__name__)

#: Что делать с готовым материалом. Во второй очереди сюда встанет разбор
#: (задача T055): показать кандидатов кнопками и зафиксировать по подтверждению.
MaterialHandler = Callable[[Message, Material, str], Awaitable[None]]

#: Сколько знаков комментария показывать в подтверждении связывания.
COMMENT_PREVIEW_LIMIT = 120


def chat_ui_lang(chat_id: int) -> str:
    inspection = domain.get_state(chat_id)
    return ui_lang_or_default(None if inspection is None else inspection.ui_lang)


async def confirm_link(message: Message, material: Material, lang: str) -> None:
    """Обработчик материала первой очереди: подтвердить связывание.

    Разбор и предложение пунктов появляются во второй очереди (T055), когда
    будет принят блок `recognize`. До тех пор бот честно показывает, что именно
    он связал, — молчать на присланный материал нельзя.
    """
    count = len(material.photo_file_ids)
    if material.comment.voice_file_id is not None:
        await message.answer(t("material.linked_voice", lang, count=count))
        return
    preview = (material.comment.text or "").strip()
    if len(preview) > COMMENT_PREVIEW_LIMIT:
        preview = preview[: COMMENT_PREVIEW_LIMIT - 1].rstrip() + "…"
    await message.answer(t("material.linked", lang, count=count, comment=preview))


def largest_photo_id(message: Message) -> str | None:
    """`file_id` самого крупного размера кадра. Telegram отдаёт размеры по возрастанию."""
    return message.photo[-1].file_id if message.photo else None


def build_material_router(
    *,
    store: MaterialStore,
    albums: AlbumBuffer,
    on_material: MaterialHandler = confirm_link,
    album_window: float = ALBUM_WINDOW_SECONDS,
) -> Router:
    """Роутер приёма материала.

    `album_window` вынесен параметром ради тестов: настоящие полторы секунды на
    каждый случай превратили бы прогон альбомов в минуты ожидания.
    """
    router = Router(name="material")
    timers: set[asyncio.Task[None]] = set()

    async def register(message: Message, group: PhotoGroup) -> None:
        """Принять закрытую группу кадров: либо она уже с подписью, либо ждёт комментарий."""
        lang = chat_ui_lang(group.chat_id)
        if domain.get_state(group.chat_id) is None:
            await message.answer(t("material.no_inspection", lang))
            return
        material = store.queue(group.chat_id).add_group(group)
        if material is not None:
            await on_material(message, material, lang)
            return
        count = len(group.photo_file_ids)
        key = "material.photo_taken" if count == 1 else "material.album_taken"
        await message.answer(t(key, lang, count=count))

    async def close_after_window(message: Message, chat_id: int, group_key: str) -> None:
        """Закрыть альбом, если за окно ожидания больше ничего не пришло."""
        try:
            await asyncio.sleep(album_window)
            group = albums.close_group(chat_id, group_key)
            if group is not None:
                await register(message, group)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Задача фоновая: без явного журнала её отказ не увидит никто, а
            # аудитор останется без ответа на присланный альбом.
            logger.exception("не удалось закрыть альбом %s в чате %s", group_key, chat_id)

    def schedule_close(message: Message, chat_id: int, group_key: str) -> None:
        task = asyncio.create_task(close_after_window(message, chat_id, group_key))
        timers.add(task)
        task.add_done_callback(timers.discard)

    async def close_open_album(message: Message, chat_id: int) -> None:
        """Любое следующее событие закрывает альбом, не дожидаясь окна."""
        pending = albums.close_open(chat_id)
        if pending is not None:
            await register(message, pending)

    @router.message(F.photo)
    async def on_photo(message: Message) -> None:
        chat_id = message.chat.id
        file_id = largest_photo_id(message)
        if file_id is None:
            return
        group_key = message.media_group_id or f"single-{message.message_id}"

        previous = albums.close_other(chat_id, group_key)
        if previous is not None:
            await register(message, previous)

        frame = Frame(message_id=message.message_id, file_id=file_id, caption=message.caption)
        opened = albums.add(chat_id, group_key, frame)

        if message.media_group_id is None:
            single = albums.close_group(chat_id, group_key)
            if single is not None:
                await register(message, single)
            return
        if opened:
            schedule_close(message, chat_id, group_key)

    @router.message(F.voice | (F.text & ~F.text.startswith("/")))
    async def on_comment(message: Message) -> None:
        chat_id = message.chat.id
        await close_open_album(message, chat_id)

        lang = chat_ui_lang(chat_id)
        if domain.get_state(chat_id) is None:
            await message.answer(t("material.no_inspection", lang))
            return

        comment = (
            Comment(voice_file_id=message.voice.file_id)
            if message.voice is not None
            else Comment(text=message.text)
        )
        queue = store.queue(chat_id)

        material: Material | None = None
        if message.reply_to_message is not None:
            material = queue.resolve_reply(message.reply_to_message.message_id, comment)
        if material is None:
            # Ответ не на кадр (например, на сообщение самого бота) — не терять
            # комментарий, а связать его со старейшим ждущим кадром. Запись всё
            # равно появится только после подтверждения аудитором.
            material = queue.resolve_next(comment)
        if material is None:
            await message.answer(t("material.no_photo", lang))
            return

        await on_material(message, material, lang)

    return router
