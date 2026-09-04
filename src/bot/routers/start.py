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

import asyncio
import logging

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src import domain
from src.domain.errors import DomainError

from .. import sidecar
from ..auditor import auditor_name, auditor_name_was_shortened
from ..config import BotSettings
from ..inspection import read_inspection
from ..keyboards import (
    KIND_PREFIX,
    KIND_TITLES,
    LANG_LABELS,
    LANG_PREFIX,
    NEW_INSPECTION_CALLBACK,
    RESUME_CONTINUE_CALLBACK,
    RESUME_NEW_CALLBACK,
    kind_keyboard,
    kind_title,
    lang_keyboard,
    new_inspection_keyboard,
    resume_keyboard,
)
from ..lang import chat_ui_lang
from ..pending import PendingStore
from ..states import StartFlow
from ..texts import t, ui_lang_or_default, with_photo_rule

logger = logging.getLogger(__name__)

#: Сколько знаков в названии точки бот принимает.
#:
#: Число замерено, а не выбрано на вкус. Имя файла отчёта движок собирает как
#: «Аудит <точка> - <аудитор> - <дата>.pdf»; кириллица в UTF-8 — два байта на
#: знак, а предел имени файла на ext4 (площадка продукта, D053) — 255 байт.
#: С аудитором в 40 знаков на название остаётся около шестидесяти. Проверено
#: фактическим прогоном: на 300 знаках сборка отчёта падает с «File name too
#: long», и узнаёт об этом аудитор в конце проверки, когда переснимать поздно.
UNIT_NAME_LIMIT = 60

#: Тот же предел, но БАЙТАМИ (T128, переоткрытая часть, issue #103): в знаках
#: он не ловит тяжёлые символы — 60 эмодзи это те же 60 знаков (в предел выше
#: укладываются), но уже 240 байт, то есть одно название съедает больше, чем
#: весь бюджет имени файла. Число — часть общего бюджета: постоянная часть
#: формулы имени файла (слово, два разделителя, дата, «.pdf») занимает 31 байт
#: (замерено, см. комментарий у `AUDITOR_NAME_BYTE_LIMIT` в `../auditor.py`),
#: остаётся 224 байта на название точки и имя аудитора вместе. Точке — 120
#: байт (ровно 60 кириллических знаков — старым живым названиям запрет не
#: мешает, старое поведение не меняется), аудитору — 100.
UNIT_NAME_BYTE_LIMIT = 120


async def _offer_resume(message: Message, inspection: domain.Inspection, lang: str) -> None:
    """Показать оставшуюся в чате проверку и дать выбор — продолжить или начать новую.

    Фраз две, и разница между ними не косметическая (T153). Проверке в работе
    правда, что она незавершённая и что новая её сотрёт. Проверке, по которой
    отчёт уже собран и отдан, — неправда и то и другое, а звучала эта неправда
    в начале каждой второй проверки за день.

    Признака «завершена» у движка нет, и бот его не выдумывает: он опирается на
    то, что сделал сам, — отдал ли он по этой проверке отчёт (`sidecar`).
    """
    ключ = (
        "start.resume_handed_over"
        if sidecar.handed_over(message.chat.id, len(inspection.findings))
        else "start.resume_found"
    )
    await message.answer(
        t(
            ключ,
            lang,
            unit=inspection.unit,
            date=inspection.date,
            auditor=inspection.auditor or "—",
            findings=len(inspection.findings),
        ),
        reply_markup=resume_keyboard(lang),
    )


async def _ask_unit(message: Message, state: FSMContext, lang: str) -> None:
    await state.set_state(StartFlow.waiting_unit)
    await message.answer(t("start.ask_unit", lang))


def build_start_router(settings: BotSettings, pending: PendingStore | None = None) -> Router:
    """Роутер мастера начала проверки.

    `settings` нужен ради карты имён (T063). `pending` — чтобы кнопки прошлой
    проверки не выстрелили в новую: предложение, показанное пять минут назад,
    зафиксировало бы запись уже в другой пиццерии.
    """
    router = Router(name="start")

    @router.message(Command("start"))
    async def on_start(message: Message, state: FSMContext) -> None:
        """`/start` обязан работать всегда — это единственный выход из тупика.

        Испорченное состояние роняло здесь всё: аудитор не мог ни продолжить
        проверку, ни начать новую, и бот на любое его сообщение отвечал
        молчанием (T126). Поэтому нечитаемое состояние тут не отказ, а
        отдельный ответ: сказать, что файл повреждён, и дать начать заново.
        Молчаливой перезаписи при этом нет — новую заводит человек кнопкой.
        """
        lang = chat_ui_lang(message.chat.id)
        await state.clear()
        try:
            inspection = read_inspection(message.chat.id)
        except DomainError:
            logger.exception("состояние чата %s не читается", message.chat.id)
            await message.answer(
                t("start.state_broken", lang), reply_markup=new_inspection_keyboard(lang)
            )
            return
        if inspection is not None:
            await _offer_resume(message, inspection, lang)
            return
        await message.answer(t("start.greeting", lang), reply_markup=new_inspection_keyboard(lang))

    @router.callback_query(F.data == NEW_INSPECTION_CALLBACK)
    async def on_new(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        message = callback.message
        if not isinstance(message, Message):
            return
        lang = chat_ui_lang(message.chat.id)
        try:
            inspection = read_inspection(message.chat.id)
        except DomainError:
            # Про повреждение аудитор уже прочитал на `/start` и нажал «Новая»
            # осознанно. Второй раз пугать его нечем, а тупик здесь означал бы,
            # что выхода нет и после нажатия единственной предложенной кнопки.
            logger.exception("состояние чата %s не читается", message.chat.id)
            await _ask_unit(message, state, lang)
            return
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
        inspection = read_inspection(message.chat.id)
        if inspection is None:
            await message.answer(t("start.resume_gone", lang))
            return
        # Правило фотофиксации повторяется и здесь (T160, D078): к прерванной
        # проверке аудитор возвращается через день и другой сессией, и первого
        # сообщения — того, где правило прозвучало заранее, — он мог не читать.
        await message.answer(
            with_photo_rule(
                t(
                    "start.resumed",
                    lang,
                    unit=inspection.unit,
                    date=inspection.date,
                    findings=len(inspection.findings),
                ),
                lang,
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
        if len(unit) > UNIT_NAME_LIMIT:
            # Отказ здесь, а не отказом сборки отчёта в конце проверки: там
            # аудитор уже уехал с точки, и переименовать пиццерию ему нечем.
            await message.answer(t("start.unit_too_long", lang, limit=UNIT_NAME_LIMIT))
            return
        if len(unit.encode("utf-8")) > UNIT_NAME_BYTE_LIMIT:
            # Предел в знаках это не ловит: 60 эмодзи проходят его же знаками,
            # а по байтам уже почти весь бюджет имени файла разом (T128).
            await message.answer(t("start.unit_too_long_bytes", lang))
            return
        await state.update_data(unit=unit)
        await state.set_state(StartFlow.waiting_kind)
        await message.answer(t("start.ask_kind", lang), reply_markup=kind_keyboard(lang))

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
        if code not in KIND_TITLES:
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
        if not unit or kind_code not in KIND_TITLES:
            await state.clear()
            await message.answer(
                t("start.greeting", lang), reply_markup=new_inspection_keyboard(lang)
            )
            return

        auditor = auditor_name(
            callback.from_user.id, callback.from_user.full_name, settings.auditor_names
        )
        # Считаем ДО обрезки, пока имя профиля ещё под рукой: `auditor` выше
        # уже обрезан (это и есть контракт `auditor_name`), а обрезалось ли
        # оно — узнать можно только сравнением с тем же необрезанным именем.
        auditor_shortened = auditor_name_was_shortened(
            callback.from_user.id, callback.from_user.full_name, settings.auditor_names
        )
        try:
            # Подпроцесс — в поток: старт проверки не должен останавливать бота
            # для остальных аудиторов (T101).
            inspection = await asyncio.to_thread(
                domain.start_inspection,
                message.chat.id,
                unit=unit,
                # Вид проверки уходит КОДОМ, а не словом (T152): словом он
                # жил как данные — в проверке, в колонке базы, в отпечатке — и
                # переводился обратно сопоставлением строк. Перевод по языку
                # отчёта делает сам `domain` в тот момент, когда шапку
                # заполняет для движка: на кнопке аудитор прочитал вид сам, а
                # партнёру в документ он уезжает на языке отчёта.
                kind=kind_code,
                report_lang=report_lang,
                # Языка в проверке три, и до T128 из бота не задавался ни один
                # из двух остальных: аудитор выбирал английский отчёт, а
                # разговор оставался русским — язык был константой, а не
                # параметром. Вопрос в мастере один, поэтому его ответ ложится
                # во все три поля; полями они остаются разными, и разъехаться
                # им ничто не мешает, когда вопросов станет больше.
                ui_lang=report_lang,
                speech_lang=report_lang,
                auditor=auditor,
            )
        except DomainError:
            # Отказ движка приходит стеком с путями к его файлам: он написан
            # тому, кто зовёт движок из командной строки. В журнал целиком, в
            # чат — что делать (T151, тот же принцип, что у T127).
            logger.exception("не удалось начать проверку в чате %s", message.chat.id)
            await message.answer(t("start.failed", lang))
            return
        finally:
            await state.clear()

        # Заметки бота — того же возраста, что и проверка: источники записей,
        # список кадров и последняя зона от прошлой к новой не относятся.
        sidecar.reset(message.chat.id)
        if pending is not None:
            pending.forget(message.chat.id)

        started_lang = ui_lang_or_default(inspection.ui_lang)
        # Обрезка не уезжает молча (T128): строка про неё — рядом с именем,
        # той же строкой сообщения, что и старт проверки, а не отдельным
        # сообщением, которое легко потерять среди присланных кадров.
        auditor_note = (
            f"{t('start.auditor_name_shortened', started_lang)}\n" if auditor_shortened else ""
        )
        # Правило фотофиксации — заранее, первым же сообщением проверки (T160,
        # решение D078). Это единственное место, где его можно сказать ДО
        # первой ошибки: дальше остаётся только отвечать правилом на неё, а
        # владелец просил не этого — «надо чтобы человек понял что фото должно
        # быть».
        await message.answer(
            with_photo_rule(
                t(
                    "start.started",
                    started_lang,
                    unit=inspection.unit,
                    # В проверке лежит код, аудитору показывается слово. Язык
                    # тут язык начатой проверки, а не язык кнопки: сообщение о
                    # старте читает он же, но уже внутри проверки.
                    kind=kind_title(inspection.kind, started_lang),
                    lang=LANG_LABELS[report_lang],
                    auditor=inspection.auditor,
                    auditor_note=auditor_note,
                    date=inspection.date,
                ),
                started_lang,
            )
        )

    return router
