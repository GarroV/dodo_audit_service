"""Начало проверки и продолжение незавершённой (T050, T051, T052, T063).

Мастер спрашивает ровно три вещи: название пиццерии текстом (решение D051 —
справочника точек в MVP нет), вид проверки и язык отчёта. Остальное бот знает
сам: проверяющего берёт по Telegram ID, дату ставит сегодняшнюю (решение D032,
задача T063). Ни одного шага, который аудитор заполняет руками впустую.

Незавершённая проверка не затирается молча (задача T052): движок `init`
переписывает состояние целиком и без вопросов, поэтому спросить обязан бот —
показать, чья проверка и от какого числа, и дать выбрать.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src import domain
from src.domain.errors import DomainError

from ..auditor import auditor_name
from ..config import BotSettings
from ..keyboards import (
    KIND_LABELS,
    KIND_PREFIX,
    LANG_LABELS,
    LANG_PREFIX,
    NEW_INSPECTION_CALLBACK,
    RESUME_CONTINUE_CALLBACK,
    RESUME_NEW_CALLBACK,
    kind_keyboard,
    lang_keyboard,
    new_inspection_keyboard,
    resume_keyboard,
)
from ..states import StartFlow
from ..texts import t, ui_lang_or_default

logger = logging.getLogger(__name__)


def chat_ui_lang(chat_id: int) -> str:
    """Язык интерфейса этого чата: из начатой проверки, иначе умолчание.

    До старта проверки состояния нет — спрашивать язык интерфейса отдельным
    шагом мастера спека не просит, а падать на приветствии нельзя.
    """
    inspection = domain.get_state(chat_id)
    return ui_lang_or_default(None if inspection is None else inspection.ui_lang)


async def _offer_resume(message: Message, inspection: domain.Inspection, lang: str) -> None:
    """Показать незавершённую проверку и дать выбор — продолжить или начать новую."""
    await message.answer(
        t(
            "start.resume_found",
            lang,
            unit=inspection.unit,
            date=inspection.date,
            auditor=inspection.auditor or "—",
            findings=len(inspection.findings),
        ),
        reply_markup=resume_keyboard(),
    )


async def _ask_unit(message: Message, state: FSMContext, lang: str) -> None:
    await state.set_state(StartFlow.waiting_unit)
    await message.answer(t("start.ask_unit", lang))


def build_start_router(settings: BotSettings) -> Router:
    """Роутер мастера начала проверки. `settings` нужен ради карты имён (T063)."""
    router = Router(name="start")

    @router.message(Command("start"))
    async def on_start(message: Message, state: FSMContext) -> None:
        lang = chat_ui_lang(message.chat.id)
        await state.clear()
        inspection = domain.get_state(message.chat.id)
        if inspection is not None:
            await _offer_resume(message, inspection, lang)
            return
        await message.answer(t("start.greeting", lang), reply_markup=new_inspection_keyboard())

    @router.callback_query(F.data == NEW_INSPECTION_CALLBACK)
    async def on_new(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        message = callback.message
        if not isinstance(message, Message):
            return
        lang = chat_ui_lang(message.chat.id)
        inspection = domain.get_state(message.chat.id)
        if inspection is not None:
            await _offer_resume(message, inspection, lang)
            return
        await _ask_unit(message, state, lang)

    @router.callback_query(F.data == RESUME_CONTINUE_CALLBACK)
    async def on_resume_continue(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        message = callback.message
        if not isinstance(message, Message):
            return
        await state.clear()
        lang = chat_ui_lang(message.chat.id)
        inspection = domain.get_state(message.chat.id)
        if inspection is None:
            await message.answer(t("start.resume_gone", lang))
            return
        await message.answer(
            t(
                "start.resumed",
                lang,
                unit=inspection.unit,
                date=inspection.date,
                findings=len(inspection.findings),
            )
        )

    @router.callback_query(F.data == RESUME_NEW_CALLBACK)
    async def on_resume_new(callback: CallbackQuery, state: FSMContext) -> None:
        """«Начать новую» — только вход в мастер.

        Старая проверка на диске остаётся до последнего шага: аудитор ещё может
        бросить мастер на полпути, и потерять из-за этого зафиксированное было бы
        ровно тем молчаливым затиранием, от которого защищает задача T052.
        """
        await callback.answer()
        message = callback.message
        if not isinstance(message, Message):
            return
        await _ask_unit(message, state, chat_ui_lang(message.chat.id))

    @router.message(StateFilter(StartFlow.waiting_unit), F.text)
    async def on_unit(message: Message, state: FSMContext) -> None:
        lang = chat_ui_lang(message.chat.id)
        unit = (message.text or "").strip()
        if not unit:
            await message.answer(t("start.unit_empty", lang))
            return
        await state.update_data(unit=unit)
        await state.set_state(StartFlow.waiting_kind)
        await message.answer(t("start.ask_kind", lang), reply_markup=kind_keyboard())

    @router.message(StateFilter(StartFlow.waiting_unit))
    async def on_unit_not_text(message: Message) -> None:
        """Кадр или голос вместо названия: сказать, чего ждём, а не молчать."""
        await message.answer(t("start.unit_expected", chat_ui_lang(message.chat.id)))

    @router.callback_query(StateFilter(StartFlow.waiting_kind), F.data.startswith(KIND_PREFIX))
    async def on_kind(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        message = callback.message
        if not isinstance(message, Message):
            return
        code = (callback.data or "").removeprefix(KIND_PREFIX)
        if code not in KIND_LABELS:
            return
        await state.update_data(kind=code)
        await state.set_state(StartFlow.waiting_lang)
        await message.answer(
            t("start.ask_lang", chat_ui_lang(message.chat.id)), reply_markup=lang_keyboard()
        )

    @router.callback_query(StateFilter(StartFlow.waiting_lang), F.data.startswith(LANG_PREFIX))
    async def on_lang(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        message = callback.message
        if not isinstance(message, Message):
            return
        report_lang = (callback.data or "").removeprefix(LANG_PREFIX)
        if report_lang not in LANG_LABELS:
            return
        lang = chat_ui_lang(message.chat.id)
        data = await state.get_data()
        unit = str(data.get("unit", "")).strip()
        kind_code = str(data.get("kind", ""))
        if not unit or kind_code not in KIND_LABELS:
            await state.clear()
            await message.answer(t("start.greeting", lang), reply_markup=new_inspection_keyboard())
            return

        auditor = auditor_name(
            callback.from_user.id, callback.from_user.full_name, settings.auditor_names
        )
        try:
            inspection = domain.start_inspection(
                message.chat.id,
                unit=unit,
                kind=KIND_LABELS[kind_code],
                report_lang=report_lang,
                auditor=auditor,
            )
        except DomainError as exc:
            logger.exception("не удалось начать проверку в чате %s", message.chat.id)
            await message.answer(t("start.failed", lang, reason=str(exc)))
            return
        finally:
            await state.clear()

        await message.answer(
            t(
                "start.started",
                ui_lang_or_default(inspection.ui_lang),
                unit=inspection.unit,
                kind=inspection.kind,
                lang=LANG_LABELS[report_lang],
                auditor=inspection.auditor,
                date=inspection.date,
            )
        )

    return router
