"""Правки записи прямо в чате: зона, класс, формулировка, удаление (T056).

Аудитор идёт по точке с телефоном в одной руке, поэтому правка — это нажатие
кнопки под подтверждением, а не команда с номером записи. Команда всё же есть
одна: `/undo` снимает последнюю запись, когда подтверждение уже уехало вверх по
переписке.

Процент после любой правки берётся заново из `domain.score()` — он зовёт
движок. Пересчитывать его здесь нельзя ни в каком виде: это методика, и вторая
копия правил разошлась бы с первой незаметно для всех, кроме партнёра.

Ни одна правка не делается молча. Движок отказывает содержательно — пара
«пункт + зона» занята, класс не разрешён пункту, — и его отказ уходит аудитору
как есть: он на точке и может исправиться, а бот выбрать за него не вправе.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src import domain
from src.domain.errors import DomainError

from .. import sidecar, view
from ..keyboards import (
    EDIT_DROP,
    EDIT_LEVEL,
    EDIT_LEVEL_PREFIX,
    EDIT_PREFIX,
    EDIT_TEXT,
    EDIT_ZONE,
    EDIT_ZONE_PREFIX,
    edit_keyboard,
    levels_keyboard,
    zones_keyboard,
)
from ..states import EditFlow
from ..texts import t, ui_lang_or_default


def chat_ui_lang(chat_id: int) -> str:
    inspection = domain.get_state(chat_id)
    return ui_lang_or_default(None if inspection is None else inspection.ui_lang)


async def show_changed(message: Message, chat_id: int, n: int, lang: str) -> None:
    """Показать запись после правки: та же одна строка и те же кнопки.

    Кнопки возвращаются нарочно: правки редко приходят по одной — сменил зону,
    сразу видно, что и класс не тот.
    """
    inspection = domain.get_state(chat_id)
    finding = None if inspection is None else inspection.finding(n)
    if finding is None:
        await message.answer(t("edit.gone", lang, n=n))
        return
    await message.answer(
        view.changed_line(finding, domain.score(chat_id).pct, lang),
        reply_markup=edit_keyboard(n, lang),
    )


def _finding(chat_id: int, n: int) -> domain.Finding | None:
    inspection = domain.get_state(chat_id)
    return None if inspection is None else inspection.finding(n)


def build_edit_router() -> Router:
    """Роутер правок. Состояния хранит диспетчер, записи — движок."""
    router = Router(name="edit")

    def chat_of(callback: CallbackQuery) -> tuple[Message, int, str] | None:
        message = callback.message
        if not isinstance(message, Message):
            return None
        chat_id = message.chat.id
        return message, chat_id, chat_ui_lang(chat_id)

    async def apply(message: Message, chat_id: int, n: int, lang: str, **fields: str) -> None:
        try:
            domain.edit_finding(chat_id, n, **fields)
        except DomainError as exc:
            await message.answer(t("edit.failed", lang, reason=exc))
            return
        if "zone" in fields:
            # Аудитор назвал зону руками — она и становится догадкой для
            # следующего кадра (D048), а не та, что стояла до правки.
            sidecar.remember_zone(chat_id, fields["zone"])
        await show_changed(message, chat_id, n, lang)

    @router.message(Command("undo"))
    async def on_undo(message: Message) -> None:
        """Снять последнюю запись — та же операция, что кнопкой «Удалить»."""
        chat_id = message.chat.id
        lang = chat_ui_lang(chat_id)
        inspection = domain.get_state(chat_id)
        if inspection is None:
            await message.answer(t("material.no_inspection", lang))
            return
        if not inspection.findings:
            await message.answer(t("edit.nothing_to_undo", lang))
            return
        await drop(message, chat_id, max(f.n for f in inspection.findings), lang)

    async def drop(message: Message, chat_id: int, n: int, lang: str) -> None:
        try:
            domain.drop_finding(chat_id, n)
        except DomainError as exc:
            await message.answer(t("edit.failed", lang, reason=exc))
            return
        sidecar.forget_source(chat_id, n)
        await message.answer(
            t("edit.dropped", lang, n=n, pct=view.percent(domain.score(chat_id).pct))
        )

    @router.callback_query(F.data.startswith(EDIT_PREFIX))
    async def on_edit(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        here = chat_of(callback)
        if here is None:
            return
        message, chat_id, lang = here
        raw, _, what = (callback.data or "").removeprefix(EDIT_PREFIX).partition(":")
        if not raw.isdigit():
            return
        n = int(raw)
        finding = _finding(chat_id, n)
        if finding is None:
            await message.answer(t("edit.gone", lang, n=n))
            return
        if what == EDIT_DROP:
            await drop(message, chat_id, n, lang)
            return
        if what == EDIT_ZONE:
            zones = [(zone.code, zone.title(lang)) for zone in domain.list_zones()]
            await message.answer(
                t("edit.ask_zone", lang, n=n),
                reply_markup=zones_keyboard(f"{EDIT_ZONE_PREFIX}{n}:", zones),
            )
            return
        if what == EDIT_LEVEL:
            # Классы приходят из методики по коду пункта: предлагать аудитору
            # то, что движок всё равно отвергнет, — это лишний круг на точке.
            levels = domain.allowed_levels(finding.code)
            await message.answer(
                t("edit.ask_level", lang, n=n),
                reply_markup=levels_keyboard(f"{EDIT_LEVEL_PREFIX}{n}:", levels),
            )
            return
        if what == EDIT_TEXT:
            await state.set_state(EditFlow.waiting_text)
            await state.update_data(edit_n=n)
            await message.answer(t("edit.ask_text", lang, n=n))

    @router.callback_query(F.data.startswith(EDIT_ZONE_PREFIX))
    async def on_zone(callback: CallbackQuery) -> None:
        await callback.answer()
        here = chat_of(callback)
        if here is None:
            return
        message, chat_id, lang = here
        raw, _, zone = (callback.data or "").removeprefix(EDIT_ZONE_PREFIX).partition(":")
        if not raw.isdigit() or not zone:
            return
        await apply(message, chat_id, int(raw), lang, zone=zone)

    @router.callback_query(F.data.startswith(EDIT_LEVEL_PREFIX))
    async def on_level(callback: CallbackQuery) -> None:
        await callback.answer()
        here = chat_of(callback)
        if here is None:
            return
        message, chat_id, lang = here
        raw, _, level = (callback.data or "").removeprefix(EDIT_LEVEL_PREFIX).partition(":")
        if not raw.isdigit() or not level:
            return
        await apply(message, chat_id, int(raw), lang, level=level)

    @router.message(StateFilter(EditFlow.waiting_text), F.text, ~F.text.startswith("/"))
    async def on_new_text(message: Message, state: FSMContext) -> None:
        """Новая формулировка. Пустую не принимаем: запись без текста бесполезна."""
        chat_id = message.chat.id
        lang = chat_ui_lang(chat_id)
        data = await state.get_data()
        n = int(data.get("edit_n", 0))
        wording = (message.text or "").strip()
        if not wording:
            await message.answer(t("edit.ask_text", lang, n=n))
            return
        await state.clear()
        await apply(message, chat_id, n, lang, text=wording)

    @router.message(StateFilter(EditFlow.waiting_text))
    async def on_anything_else(message: Message, state: FSMContext) -> None:
        """Кадр, голос или команда вместо формулировки — аудитор вернулся к работе.

        Вопрос снимается, а сообщение идёт дальше своим обработчикам
        (`SkipHandler`). Без этого случилось бы худшее из возможного: кадр
        пропал бы, а следующий комментарий молча стал бы формулировкой старой
        записи — и узнал бы об этом партнёр из отчёта.
        """
        data = await state.get_data()
        n = int(data.get("edit_n", 0))
        await state.clear()
        await message.answer(t("edit.text_dropped", chat_ui_lang(message.chat.id), n=n))
        raise SkipHandler

    return router
