"""Информационная часть в конце проверки (T158, решения D069, D070).

**Порядок обязателен и он же — вся суть задачи**: «Завершить» → подтверждение →
информационная часть → PDF и письмо. Отчёт, собранный до этих вопросов, их
ответов не содержит, и узнал бы об этом партнёр, а не аудитор. Поэтому кнопка
«Собрать отчёт» больше не собирает отчёт: она открывает информационную часть, а
сборка (`finish.deliver`) стоит за её последним вопросом.

Спрашивается семь полей, и состав их обоснован решением D070:

* **вид проверки не спрашивается** — его задаёт мастер начала проверки, и
  второй вопрос об одном и том же был бы шагом назад;
* **фотофиксация не спрашивается** — настройки печи, показания оборудования и
  фото продукта работают обычным потоком кадров и ложатся записями `D0`.

Три способа ответить и один способ показать услышанное:

* **текст** записывается сразу, без подтверждения — это D064: человек написал
  сам, подтверждать ему нечего;
* **голос** расшифровывается, и расшифровка показывается ДО записи (D069). Свести
  это с D064 «для единообразия» нельзя: там за словами стоит человек, здесь —
  машина, которая может ослышаться. Поправить расшифровку можно, просто прислав
  текст: он станет ответом вместо услышанного;
* **кнопки** отвечают за поля «да/нет», и их значение уезжает в отчёт на языке
  ОТЧЁТА, а не интерфейса: строку читает партнёр;
* **кадр** прикладывается к ответу и печатается в отчёте рядом с ним (T179).
  Кадра без ответа в отчёте не существует — движок печатает его ПОД ТЕКСТОМ
  своего поля, — поэтому снимок без подписи ждёт слов на тот же вопрос, а
  пропуск вопроса вместе с кадром бот называет вслух. Подпись к кадру ответом
  быть не перестала: она и есть ответ, кадр к ней приложением.

**Ни одно поле не обязательно** (допущение D070). Пропущенное не записывается
вовсе — и не печатается; срок плана действий при пропуске падает на прежний
расчёт движка. Рядом с пропуском стоит «дальше к отчёту»: шесть нажатий
«Пропустить» подряд — тот же тупик, только длиннее.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src import domain
from src.domain.errors import DomainError
from src.recognize.errors import RecognizeError
from src.recognize.transcribe import transcribe

from .. import sidecar, view
from ..info import KIND_DATE, KIND_TEXT, KIND_YES_NO, fields_to_ask, parse_date
from ..inspection import read_inspection
from ..keyboards import (
    INFO_DONE_CALLBACK,
    INFO_NO_CALLBACK,
    INFO_SAVE_CALLBACK,
    INFO_SKIP_CALLBACK,
    INFO_YES_CALLBACK,
    info_heard_keyboard,
    info_keyboard,
)
from ..lang import chat_langs
from ..photos import fetch_bytes
from ..states import InfoFlow
from ..texts import t
from .finish import deliver

logger = logging.getLogger(__name__)

#: Ключи данных диспетчера: на каком вопросе стоим и что расшифровано, но ещё
#: не записано. Полями, а не отдельным хранилищем: информационная часть живёт
#: минуты и перезапуск переживать не обязана (`states.InfoFlow`).
INDEX_KEY = "info_index"
FIELDS_KEY = "info_fields"
HEARD_KEY = "info_heard"
#: Кадры, присланные к вопросу, на котором стоит разговор, но ещё не записанные:
#: ответа на него пока нет, а кадр печатается рядом с ответом (T179).
PHOTOS_KEY = "info_photos"

#: Подсказка про способ ответа — по виду поля.
HINTS = {
    KIND_TEXT: "info.hint_text",
    KIND_YES_NO: "info.hint_yes_no",
    KIND_DATE: "info.hint_date",
}


async def start_info(message: Message, state: FSMContext, chat_id: int, lang: str) -> None:
    """Открыть информационную часть: вступление и первый вопрос.

    Список вопросов собирается один раз и кладётся в память диспетчера. Дважды
    читать методику с диска незачем, но причина не в скорости: спрошенный
    состав полей должен остаться тем же до конца разговора, даже если методику
    в этот момент подменят на стенде.

    Проверки нет — сказать об этом и не начинать: собирать отчёт всё равно не
    из чего. Испорченное состояние сюда доходит отказом чтения и уезжает в
    последний рубеж (T126), как и на всех остальных входах.
    """
    if read_inspection(chat_id) is None:
        await message.answer(t("material.no_inspection", lang))
        return
    asked = await asyncio.to_thread(fields_to_ask, lang)
    if not asked:
        # Методика без информационных пунктов — законная методика: их состав
        # задаёт управляющая компания. Тогда шага просто нет, и отчёт собирается
        # сразу, как собирался до этой задачи.
        await deliver(message, chat_id, lang, allow_missing=False)
        return
    await state.set_state(InfoFlow.waiting)
    await state.update_data(
        {
            FIELDS_KEY: [(field.code, field.kind, question) for field, question in asked],
            INDEX_KEY: 0,
            HEARD_KEY: None,
            PHOTOS_KEY: [],
        }
    )
    await message.answer(t("info.intro", lang))
    await _ask(message, state, lang)


async def _ask(message: Message, state: FSMContext, lang: str) -> None:
    """Задать текущий вопрос — или закончить часть, если вопросы кончились."""
    data = await state.get_data()
    asked = data.get(FIELDS_KEY) or []
    index = int(data.get(INDEX_KEY) or 0)
    if index >= len(asked):
        await _finish(message, state, message.chat.id, lang)
        return
    _code, kind, question = asked[index]
    await state.update_data({HEARD_KEY: None})
    await message.answer(
        t(
            "info.ask",
            lang,
            n=index + 1,
            total=len(asked),
            question=question,
            hint=t(HINTS[kind], lang),
        ),
        reply_markup=info_keyboard(kind, lang),
    )


async def _photos(state: FSMContext) -> list[str]:
    """Кадры, ждущие ответа на текущий вопрос."""
    data = await state.get_data()
    return [str(ref) for ref in (data.get(PHOTOS_KEY) or [])]


async def _forget_photos(message: Message, state: FSMContext, lang: str) -> None:
    """Уйти с вопроса, к которому приложен кадр, — и сказать об этом.

    Записанный ответ кадры уже забрал (`_save` их снимает), поэтому сюда они
    доходят только вместе с пропуском. Промолчать нельзя: аудитор прислал
    снимок и вправе считать, что тот в отчёте, — это ровно та тихая потеря,
    против которой заведена задача.
    """
    shots = await _photos(state)
    if not shots:
        return
    await state.update_data({PHOTOS_KEY: []})
    await message.answer(t("info.photo_dropped", lang, count=len(shots)))


async def _next(message: Message, state: FSMContext, lang: str) -> None:
    """Перейти к следующему вопросу."""
    await _forget_photos(message, state, lang)
    data = await state.get_data()
    await state.update_data({INDEX_KEY: int(data.get(INDEX_KEY) or 0) + 1})
    await _ask(message, state, lang)


async def _finish(message: Message, state: FSMContext, chat_id: int, lang: str) -> None:
    """Информационная часть кончилась — теперь и только теперь собирается отчёт."""
    await _forget_photos(message, state, lang)
    await state.clear()
    await message.answer(t("info.finished", lang))
    await deliver(message, chat_id, lang, allow_missing=False)


async def _current(state: FSMContext) -> tuple[str, str] | None:
    """Код и вид поля, на котором стоит разговор."""
    data = await state.get_data()
    asked = data.get(FIELDS_KEY) or []
    index = int(data.get(INDEX_KEY) or 0)
    if index >= len(asked):
        return None
    code, kind, _question = asked[index]
    return str(code), str(kind)


async def _save(message: Message, state: FSMContext, lang: str, value: str) -> None:
    """Записать ответ в проверку и перейти дальше.

    Отказ движка не проглатывается и не выбрасывает аудитора из части: он
    остаётся на том же вопросе и может повторить ответ или пропустить его.
    Молча пойти дальше значило бы напечатать отчёт без поля, которое аудитор
    считает записанным.
    """
    here = await _current(state)
    if here is None:
        await _ask(message, state, lang)
        return
    code, _kind = here
    shots = await _photos(state)
    try:
        # Подпроцесс движка — в поток, как и все остальные его вызовы (T101).
        await asyncio.to_thread(domain.set_info, message.chat.id, code, value, photos=shots)
    except DomainError:
        logger.exception("информационное поле %s чата %s не записалось", code, message.chat.id)
        await message.answer(t("info.not_saved", lang))
        return
    # Кадры сняты только после удачной записи: отказ движка оставляет их при
    # вопросе, и повторный ответ уносит их с собой, а не теряет по дороге.
    await state.update_data({PHOTOS_KEY: []})
    await message.answer(t("info.saved", lang, value=view.shorten(value)))
    if shots:
        await message.answer(t("info.photo_attached", lang, count=len(shots)))
    await _next(message, state, lang)


async def _save_free_text(message: Message, state: FSMContext, lang: str, text: str) -> None:
    """Ответ словами: дата разбирается, остальное записывается как сказано.

    Дата, которую не удалось разобрать, не записывается вовсе. Записать её как
    есть значило бы отправить партнёру «завтра после обеда» в поле, которое
    письмо читает сроком плана действий.
    """
    here = await _current(state)
    if here is None:
        await _ask(message, state, lang)
        return
    _code, kind = here
    value = text.strip()
    if not value:
        return
    if kind == KIND_DATE:
        stamp = parse_date(value)
        if stamp is None:
            await message.answer(t("info.bad_date", lang, text=view.shorten(value)))
            return
        value = stamp
    await _save(message, state, lang, value)


def build_info_router() -> Router:
    """Роутер информационной части. Работает только в своём состоянии диалога."""
    router = Router(name="info")

    def chat_of(callback: CallbackQuery) -> tuple[Message, int, str] | None:
        message = callback.message
        if not isinstance(message, Message):
            return None
        chat_id = message.chat.id
        ui_lang, _ = chat_langs(chat_id)
        return message, chat_id, ui_lang

    @router.callback_query(InfoFlow.waiting, F.data == INFO_SKIP_CALLBACK)
    async def on_skip(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        here = chat_of(callback)
        if here is None:
            return
        message, _chat_id, lang = here
        # Пропущенное поле не записывается вовсе (D070): пустая строка в отчёте
        # выглядела бы как ответ «ничего», а это разные вещи.
        await _next(message, state, lang)

    @router.callback_query(InfoFlow.waiting, F.data == INFO_DONE_CALLBACK)
    async def on_done(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        here = chat_of(callback)
        if here is None:
            return
        message, chat_id, lang = here
        await _finish(message, state, chat_id, lang)

    @router.callback_query(InfoFlow.waiting, F.data.in_({INFO_YES_CALLBACK, INFO_NO_CALLBACK}))
    async def on_yes_no(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        here = chat_of(callback)
        if here is None:
            return
        message, chat_id, lang = here
        _ui_lang, report_lang = chat_langs(chat_id)
        key = "info.value_yes" if callback.data == INFO_YES_CALLBACK else "info.value_no"
        # Язык значения — язык ОТЧЁТА: «Да» читает партнёр, а не аудитор.
        await _save(message, state, lang, t(key, report_lang))

    @router.callback_query(InfoFlow.waiting, F.data == INFO_SAVE_CALLBACK)
    async def on_save_heard(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        here = chat_of(callback)
        if here is None:
            return
        message, _chat_id, lang = here
        data = await state.get_data()
        heard = data.get(HEARD_KEY)
        if not heard:
            # Расшифровки нет — нажали кнопку из старого сообщения. Причина не
            # называется наугад (T128): просто спрашиваем вопрос заново.
            await _ask(message, state, lang)
            return
        await _save_free_text(message, state, lang, str(heard))

    @router.message(InfoFlow.waiting, F.voice)
    async def on_voice(message: Message, state: FSMContext) -> None:
        chat_id = message.chat.id
        lang, _report_lang = chat_langs(chat_id)
        bot = message.bot
        voice = message.voice
        raw = None if bot is None or voice is None else await fetch_bytes(bot, voice.file_id)
        if raw is None:
            await message.answer(t("record.voice_not_downloaded", lang))
            return
        try:
            # Язык речи в `transcribe` не передаётся: у него его нет в
            # сигнатуре, и разбор кадра зовёт его так же (`routers/record.py`).
            heard = await asyncio.to_thread(transcribe, raw)
        except RecognizeError as exc:
            await message.answer(t("record.voice_failed", lang, reason=f"{exc}."))
            return
        # Показать ДО записи и дать поправить (D069): дальше либо кнопка
        # «Записать так», либо присланный текст, который её заменит.
        await state.update_data({HEARD_KEY: heard})
        await message.answer(
            t("info.heard", lang, note=view.shorten(heard, view.FAST_NOTE_LIMIT)),
            reply_markup=info_heard_keyboard(lang),
        )

    @router.message(InfoFlow.waiting, F.photo)
    async def on_photo(message: Message, state: FSMContext) -> None:
        """Кадр в информационной части: он уйдёт в отчёт рядом с ответом (T179).

        Кадр запоминается заметками бота — тем же списком, которым собираются
        кадры без записи: присланное не должно исчезать молча. Подпись, если
        она есть, и есть ответ на вопрос, а кадр — приложение к нему.

        Без подписи кадр ждёт слов на тот же вопрос: движок печатает его ПОД
        ТЕКСТОМ поля, и кадра без ответа в отчёте не существует.
        """
        chat_id = message.chat.id
        lang, _ = chat_langs(chat_id)
        file_id = message.photo[-1].file_id if message.photo else None
        if file_id is not None:
            sidecar.remember_frames(
                chat_id, [sidecar.SeenFrame(message_id=message.message_id, file_id=file_id)]
            )
            shots = await _photos(state)
            if file_id not in shots:
                await state.update_data({PHOTOS_KEY: [*shots, file_id]})
        if message.caption:
            await _save_free_text(message, state, lang, message.caption)
            return
        await message.answer(t("info.photo_taken", lang))

    @router.message(InfoFlow.waiting, F.text & ~F.text.startswith("/"))
    async def on_text(message: Message, state: FSMContext) -> None:
        """Написанное словами записывается сразу — подтверждать нечего (D064)."""
        chat_id = message.chat.id
        lang, _ = chat_langs(chat_id)
        await _save_free_text(message, state, lang, message.text or "")

    return router
