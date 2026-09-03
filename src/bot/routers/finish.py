"""Завершение проверки: итог, предвычитка, отчёт и письмо (T058, T068).

Порядок ровно такой, как в `docs/06-mvp-bot.md`, шаг 7, и переставлять его
нельзя: сначала итог и список зафиксированного с возможностью поправить, и
только потом PDF файлом и письмо сообщением. Отчёт, собранный до предвычитки,
уходит партнёру с ошибкой, которую аудитор увидел бы за секунду.

Здесь же показываются кадры, оставшиеся без записи (задача T068): аудитор
прислал их, а разбирать не стал и не прокомментировал. Молча выбросить их — это
и есть потеря материала, ради которой задача заведена: на точку уже не
вернуться.

Оценка не считается: процент, буква и разбивка приходят из `domain.score()`,
который зовёт движок. Отчёт и письмо собирает блок `report` — здесь только
кадры, потому что токен телеграма есть у одного бота.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, FSInputFile, Message

from src import domain
from src.report import PhotoMissing, ReportError, build_letter, build_pdf

from .. import sidecar, view
from ..keyboards import (
    FINISH_BUILD_CALLBACK,
    FINISH_BUILD_NO_PHOTOS_CALLBACK,
    FINISH_EDIT_CALLBACK,
    FINISH_PICK_PREFIX,
    FINISH_RESUME_CALLBACK,
    edit_keyboard,
    finish_keyboard,
    pick_record_keyboard,
    without_photos_keyboard,
)
from ..photos import download_all
from ..texts import t, ui_lang_or_default

logger = logging.getLogger(__name__)


def chat_ui_lang(chat_id: int) -> str:
    inspection = domain.get_state(chat_id)
    return ui_lang_or_default(None if inspection is None else inspection.ui_lang)


async def show_summary(message: Message, chat_id: int, lang: str) -> None:
    """Итог, список зафиксированного и кадры без записи — тремя сообщениями.

    Тремя, а не одним: список записей и список кадров по отдельности читаются, а
    склеенные упираются в предел длины сообщения телеграма на первой же реальной
    проверке (двадцать записей — это уже за две тысячи знаков).
    """
    inspection = domain.get_state(chat_id)
    if inspection is None:
        await message.answer(t("material.no_inspection", lang))
        return
    # Подпроцесс (26 мс) — в поток, чтобы бот не вставал на время расчёта (T101).
    score = await asyncio.to_thread(domain.score, chat_id)
    await message.answer(
        t(
            "finish.summary",
            lang,
            pct=view.percent(score.pct),
            grade=score.grade,
            label=score.label(lang),
            total=len(inspection.findings),
            counts=view.counts_line(score.counts),
        )
    )

    notes = sidecar.read(chat_id)
    if inspection.findings:
        await message.answer(
            t(
                "finish.records",
                lang,
                lines=view.record_lines(inspection.findings, notes.sources, lang),
            )
        )
    else:
        await message.answer(t("finish.empty", lang))

    used = {ref for finding in inspection.findings for ref in finding.photos}
    orphans = tuple(frame for frame in notes.frames if frame.file_id not in used)
    if orphans:
        await message.answer(
            t(
                "finish.unclaimed",
                lang,
                count=len(orphans),
                lines=view.unclaimed_lines(orphans, lang),
            )
        )

    await message.answer(
        t("finish.ask", lang),
        reply_markup=finish_keyboard(lang, can_edit=bool(inspection.findings)),
    )


async def deliver(message: Message, chat_id: int, lang: str, *, allow_missing: bool) -> None:
    """Собрать отчёт и письмо и отдать их в чат.

    Кадры скачиваются заранее и отдаются `report` готовой картой: `build_pdf`
    синхронный и выполняется в рабочем потоке, а качать из него нельзя — там нет
    цикла событий. Карта же держит правило «где лежит кадр» в одном месте.
    """
    bot = message.bot
    inspection = domain.get_state(chat_id)
    if bot is None or inspection is None:
        await message.answer(t("material.no_inspection", lang))
        return
    await message.answer(t("finish.building", lang))
    refs = [ref for finding in inspection.findings for ref in finding.photos]
    with tempfile.TemporaryDirectory(prefix="bot-report-") as tmp:
        # Кадры живут только на время сборки: своего хранилища фотографий у
        # продукта нет (`docs/06-mvp-bot.md`, технические требования), хватает
        # идентификатора телеграма.
        found = await download_all(bot, refs, Path(tmp))
        try:
            pdf = await asyncio.to_thread(
                build_pdf,
                chat_id,
                fetch_photo=found.get,
                allow_missing_photos=allow_missing,
            )
        except PhotoMissing as exc:
            # Решение «собрать без кадра» принимает аудитор, а не код: отчёт без
            # доказательства партнёр справедливо оспорит.
            await message.answer(
                t("finish.photos_missing", lang, reason=exc),
                reply_markup=without_photos_keyboard(lang),
            )
            return
        except ReportError as exc:
            await message.answer(t("finish.pdf_failed", lang, reason=exc))
            return

    await message.answer_document(FSInputFile(pdf))
    try:
        letter = await asyncio.to_thread(build_letter, chat_id)
    except ReportError as exc:
        await message.answer(t("finish.pdf_failed", lang, reason=exc))
        return
    await message.answer(t("finish.letter", lang, letter=letter))


def build_finish_router() -> Router:
    """Роутер завершения: команда `/finish` и кнопки под итогом."""
    router = Router(name="finish")

    def chat_of(callback: CallbackQuery) -> tuple[Message, int, str] | None:
        message = callback.message
        if not isinstance(message, Message):
            return None
        chat_id = message.chat.id
        return message, chat_id, chat_ui_lang(chat_id)

    @router.message(Command("finish"))
    async def on_finish(message: Message) -> None:
        chat_id = message.chat.id
        await show_summary(message, chat_id, chat_ui_lang(chat_id))

    @router.callback_query(F.data == FINISH_EDIT_CALLBACK)
    async def on_edit(callback: CallbackQuery) -> None:
        await callback.answer()
        here = chat_of(callback)
        if here is None:
            return
        message, chat_id, lang = here
        inspection = domain.get_state(chat_id)
        if inspection is None or not inspection.findings:
            await message.answer(t("finish.empty", lang))
            return
        await message.answer(
            t("finish.pick_edit", lang),
            reply_markup=pick_record_keyboard([f.n for f in inspection.findings]),
        )

    @router.callback_query(F.data.startswith(FINISH_PICK_PREFIX))
    async def on_pick(callback: CallbackQuery) -> None:
        await callback.answer()
        here = chat_of(callback)
        if here is None:
            return
        message, chat_id, lang = here
        raw = (callback.data or "").removeprefix(FINISH_PICK_PREFIX)
        inspection = domain.get_state(chat_id)
        finding = None if inspection is None or not raw.isdigit() else inspection.finding(int(raw))
        if finding is None:
            await message.answer(t("edit.gone", lang, n=raw))
            return
        notes = sidecar.read(chat_id)
        await message.answer(
            view.record_lines([finding], notes.sources, lang),
            reply_markup=edit_keyboard(finding.n, lang),
        )

    @router.callback_query(F.data == FINISH_BUILD_CALLBACK)
    async def on_build(callback: CallbackQuery) -> None:
        await callback.answer()
        here = chat_of(callback)
        if here is None:
            return
        message, chat_id, lang = here
        await deliver(message, chat_id, lang, allow_missing=False)

    @router.callback_query(F.data == FINISH_BUILD_NO_PHOTOS_CALLBACK)
    async def on_build_without_photos(callback: CallbackQuery) -> None:
        await callback.answer()
        here = chat_of(callback)
        if here is None:
            return
        message, chat_id, lang = here
        await deliver(message, chat_id, lang, allow_missing=True)

    @router.callback_query(F.data == FINISH_RESUME_CALLBACK)
    async def on_resume(callback: CallbackQuery) -> None:
        await callback.answer()
        here = chat_of(callback)
        if here is None:
            return
        message, _, lang = here
        await message.answer(t("finish.resumed", lang))

    return router
