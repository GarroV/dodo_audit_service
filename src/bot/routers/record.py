"""Разбор материала и фиксация записи по подтверждению (T055, T057, T067).

Три правила, из которых собран весь этот файл.

**Ни один кадр не уходит в модель без решения человека** (D046, задача T067). На
кадр без комментария бот отвечает вопросом «Разобрать?» с кнопкой. Не нажали и
прислали комментарий — вопрос снимается, и разбор идёт по словам: слова аудитора
сильнее догадки по картинке. Это не таймер и не окно ожидания (те были в
отменённом D045), а явное действие.

**Запись появляется только после подтверждения** (задача T055, принцип проекта
«модель предлагает, фиксирует человек»). Кандидаты показываются кнопками,
`add_finding` зовётся из обработчика нажатия и больше ниоткуда.

**Зона берётся из слов аудитора** (D047), последняя названная запоминается и
подставляется догадкой (D048). Отдельного шага «выберите зону» в потоке нет —
кнопки зон появляются ровно тогда, когда зону взять неоткуда.

Замеры (`INF09`, `INF10`, `INF11`) идут этим же путём и ничем не выделены,
кроме класса `D0` в подтверждении (задача T057): блоком `info` движка бот не
пользуется, они лежат записями среди находок.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from math import ceil

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from src import domain
from src.domain.errors import DomainError
from src.recognize.classify import classify
from src.recognize.errors import ModelUnavailable, RecognizeError
from src.recognize.manual import manual_candidates
from src.recognize.models import UNKNOWN_ZONE
from src.recognize.transcribe import transcribe

from .. import sidecar, view
from ..keyboards import (
    ANALYZE_PREFIX,
    MANUAL_CALLBACK,
    MANUAL_LEVEL_PREFIX,
    MANUAL_PAGE_PREFIX,
    MANUAL_PAGE_SIZE,
    MANUAL_PICK_PREFIX,
    PICK_PREFIX,
    SKIP_CALLBACK,
    ZONE_FOR_MANUAL_PREFIX,
    ZONE_FOR_PICK_PREFIX,
    analyze_keyboard,
    candidates_keyboard,
    edit_keyboard,
    levels_keyboard,
    manual_keyboard,
    zones_keyboard,
)
from ..material import Comment, Material, MaterialStore, PhotoGroup
from ..pending import Offer, PendingStore, Proposal
from ..photos import fetch_bytes
from ..texts import t, ui_lang_or_default

logger = logging.getLogger(__name__)

#: Что делать с кадрами, которые пришли без подписи и ждут комментария.
WaitingHandler = Callable[[Message, PhotoGroup, str], Awaitable[None]]


def _langs(chat_id: int) -> tuple[str, str]:
    """Язык интерфейса и язык отчёта этого чата.

    Разные языки, и путать их нельзя: интерфейс аудитор читает сам, а
    формулировка от модели уходит партнёру и обязана быть на языке отчёта.
    """
    inspection = domain.get_state(chat_id)
    if inspection is None:
        return ui_lang_or_default(None), ui_lang_or_default(None)
    return ui_lang_or_default(inspection.ui_lang), inspection.report_lang


def make_waiting_handler(pending: PendingStore) -> WaitingHandler:
    """Кадр без комментария — спросить «Разобрать?» и запомнить вопрос (T067)."""

    async def ask(message: Message, group: PhotoGroup, lang: str) -> None:
        count = len(group.photo_file_ids)
        key = "material.photo_taken" if count == 1 else "material.album_taken"
        anchor = group.message_ids[0]
        sent = await message.answer(
            t(key, lang, count=count), reply_markup=analyze_keyboard(anchor, lang)
        )
        pending.offer(
            group.chat_id,
            Offer(
                anchor_id=anchor,
                file_ids=group.photo_file_ids,
                question_id=getattr(sent, "message_id", None),
            ),
        )

    return ask


async def _drop_question(message: Message, chat_id: int, offer: Offer) -> None:
    """Снять кнопку «Разобрать?»: кадр уже забрал комментарий.

    Не снять её — значит оставить аудитору кнопку, которая потратит вызов
    модели на кадр, по которому запись уже сделана. Отказ телеграма на это
    молчаливым не остаётся, но и разбор не роняет: кнопка вторична.
    """
    bot = message.bot
    if bot is None or offer.question_id is None:
        return
    try:
        await bot.edit_message_reply_markup(
            chat_id=chat_id, message_id=offer.question_id, reply_markup=None
        )
    except Exception:
        logger.warning("не удалось снять кнопку «Разобрать» в чате %s", chat_id, exc_info=True)


async def _hear_voice(message: Message, file_id: str, lang: str) -> str | None:
    """Голосовое в текст. Не вышло — сказать и попросить текстом, не роняя проверку."""
    bot = message.bot
    raw = None if bot is None else await fetch_bytes(bot, file_id)
    if raw is None:
        await message.answer(t("record.voice_not_downloaded", lang))
        return None
    try:
        heard = await asyncio.to_thread(transcribe, raw)
    except RecognizeError as exc:
        await message.answer(t("record.voice_failed", lang, reason=f"{exc}."))
        return None
    await message.answer(t("record.heard", lang, note=view.shorten(heard)))
    return heard


async def _show_candidates(message: Message, proposal: Proposal, lang: str) -> None:
    text = t(
        "record.candidates",
        lang,
        count=len(proposal.file_ids),
        lines=view.candidate_lines(proposal.candidates, lang),
    )
    if proposal.question:
        text = t("record.question", lang, question=proposal.question) + "\n\n" + text
    await message.answer(text, reply_markup=candidates_keyboard(len(proposal.candidates), lang))


async def _show_manual_page(message: Message, proposal: Proposal, page: int, lang: str) -> None:
    """Страница ручного перечня: 70+ пунктов зоны кнопками разом не показать."""
    pages = max(1, ceil(len(proposal.manual) / MANUAL_PAGE_SIZE))
    page = max(0, min(page, pages - 1))
    start = page * MANUAL_PAGE_SIZE
    titles = [
        (start + shift, item.code, item.title)
        for shift, item in enumerate(proposal.manual[start : start + MANUAL_PAGE_SIZE])
    ]
    await message.answer(
        t("record.manual_page", lang, page=page + 1, pages=pages),
        reply_markup=manual_keyboard(titles, page, pages, lang),
    )


async def _ask_zone(message: Message, prefix: str, lang: str) -> None:
    """Зону взять неоткуда — назвать её кнопкой (D047: обычно она из слов)."""
    zones = [(zone.code, zone.title(lang)) for zone in domain.list_zones()]
    await message.answer(t("record.ask_zone", lang), reply_markup=zones_keyboard(prefix, zones))


async def _open_manual(
    message: Message, chat_id: int, proposal: Proposal, pending: PendingStore, lang: str
) -> None:
    """Ручной выбор пункта: без сети и без ранжирования (T034 со стороны бота)."""
    zone = proposal.zone_hint or sidecar.read(chat_id).zone
    if not zone:
        pending.propose(chat_id, proposal)
        await _ask_zone(message, ZONE_FOR_MANUAL_PREFIX, lang)
        return
    _, report_lang = _langs(chat_id)
    try:
        items = await asyncio.to_thread(manual_candidates, zone, lang=report_lang)
    except RecognizeError as exc:
        await message.answer(t("record.unavailable", lang, reason=exc))
        return
    ready = replace(proposal, manual=items, zone_hint=zone)
    pending.propose(chat_id, ready)
    await _show_manual_page(message, ready, 0, lang)


async def _analyze(
    message: Message,
    chat_id: int,
    *,
    note: str,
    file_ids: tuple[str, ...],
    source: str,
    pending: PendingStore,
) -> None:
    """Спросить разбор и показать предложения кнопками.

    Кадр в запрос кладёт `recognize`, а не бот: он один знает, неоднозначен ли
    комментарий. Здесь кадр только скачивается — байты нужны и для разбора
    голого кадра по «Разобрать», и для случая, когда слов не хватило.
    """
    lang, report_lang = _langs(chat_id)
    zone_hint = sidecar.read(chat_id).zone
    bot = message.bot
    photo = await fetch_bytes(bot, file_ids[0]) if bot is not None and file_ids else None
    base = Proposal(file_ids=file_ids, source=source, note=note, zone_hint=zone_hint)

    await message.answer(t("record.thinking", lang))
    try:
        suggestion = await asyncio.to_thread(
            classify, note, photo, zone_hint or None, lang=report_lang
        )
    except ModelUnavailable as exc:
        # Модель недоступна — проверка не встаёт: тот же перечень пунктов
        # показывается кнопками, выбирает человек (контракт `recognize`).
        await message.answer(t("record.degraded", lang, reason=exc))
        await _open_manual(message, chat_id, base, pending, lang)
        return
    except RecognizeError as exc:
        await message.answer(t("record.unavailable", lang, reason=exc))
        return

    if not suggestion.candidates:
        if suggestion.question:
            await message.answer(t("record.question", lang, question=suggestion.question))
        await message.answer(t("record.nothing_found", lang))
        await _open_manual(message, chat_id, base, pending, lang)
        return

    proposal = replace(base, candidates=suggestion.candidates, question=suggestion.question)
    pending.propose(chat_id, proposal)
    await _show_candidates(message, proposal, lang)


async def _save(
    message: Message,
    chat_id: int,
    *,
    code: str,
    level: str,
    zone: str,
    text: str,
    file_ids: Sequence[str],
    source: str,
    lang: str,
) -> None:
    """Зафиксировать подтверждённое и ответить одной строкой (T055).

    Комментарий аудитора в запись не пишется намеренно: поле `comment` движок
    печатает в отчёте партнёру, а сказанное вслух на точке для партнёра не
    предназначено. В отчёт идёт формулировка, собранная по правилам фиксации.
    """
    try:
        finding = domain.add_finding(chat_id, code, level, zone, text)
    except DomainError as exc:
        await message.answer(t("record.failed", lang, reason=exc))
        return
    for file_id in file_ids:
        try:
            domain.attach_photo(chat_id, finding.n, file_id)
        except DomainError:
            # Запись уже есть, и терять её из-за кадра нельзя: пропажу кадра
            # поймает сборка отчёта и спросит аудитора (`report.PhotoMissing`).
            logger.exception("кадр %s не прикрепился к записи #%s", file_id, finding.n)
    sidecar.remember_source(chat_id, finding.n, source)
    sidecar.remember_zone(chat_id, zone)
    saved = domain.get_state(chat_id)
    current = None if saved is None else saved.finding(finding.n)
    await message.answer(
        view.confirm_line(current or finding, domain.score(chat_id).pct, lang),
        reply_markup=edit_keyboard(finding.n, lang),
    )


def build_record_router(*, store: MaterialStore, pending: PendingStore) -> Router:
    """Роутер разбора: вопрос «Разобрать?», предложения кнопками, фиксация."""
    router = Router(name="record")

    def chat_of(callback: CallbackQuery) -> tuple[Message, int, str] | None:
        """Сообщение, чат и язык интерфейса — или ничего, если нажатие пришло не оттуда."""
        message = callback.message
        if not isinstance(message, Message):
            return None
        chat_id = message.chat.id
        lang, _ = _langs(chat_id)
        return message, chat_id, lang

    async def stale(message: Message, lang: str) -> None:
        await message.answer(t("record.stale", lang))

    @router.callback_query(F.data.startswith(ANALYZE_PREFIX))
    async def on_analyze(callback: CallbackQuery) -> None:
        await callback.answer()
        here = chat_of(callback)
        if here is None:
            return
        message, chat_id, lang = here
        anchor = (callback.data or "").removeprefix(ANALYZE_PREFIX)
        if not anchor.isdigit():
            return
        offer = pending.take_offer(chat_id, int(anchor))
        if offer is None:
            await message.answer(t("record.analyze_gone", lang))
            return
        # Снять группу с очереди ожидания: иначе следующий комментарий сядет на
        # кадр, по которому разбор уже идёт.
        store.queue(chat_id).resolve_reply(offer.anchor_id, Comment(text=""))
        await _drop_question(message, chat_id, offer)
        await _analyze(
            message,
            chat_id,
            note="",
            file_ids=offer.file_ids,
            source=sidecar.SOURCE_PHOTO,
            pending=pending,
        )

    @router.callback_query(F.data.startswith(PICK_PREFIX))
    async def on_pick(callback: CallbackQuery) -> None:
        await callback.answer()
        here = chat_of(callback)
        if here is None:
            return
        message, chat_id, lang = here
        raw = (callback.data or "").removeprefix(PICK_PREFIX)
        proposal = pending.proposal(chat_id)
        if proposal is None or not raw.isdigit() or int(raw) >= len(proposal.candidates):
            await stale(message, lang)
            return
        index = int(raw)
        candidate = proposal.candidates[index]
        if not candidate.zone or candidate.zone == UNKNOWN_ZONE:
            pending.propose(chat_id, replace(proposal, picked=index))
            await _ask_zone(message, ZONE_FOR_PICK_PREFIX, lang)
            return
        pending.take_proposal(chat_id)
        await _save(
            message,
            chat_id,
            code=candidate.code,
            level=candidate.level,
            zone=candidate.zone,
            text=candidate.wording,
            file_ids=proposal.file_ids,
            source=proposal.source,
            lang=lang,
        )

    @router.callback_query(F.data.startswith(ZONE_FOR_PICK_PREFIX))
    async def on_zone_for_pick(callback: CallbackQuery) -> None:
        await callback.answer()
        here = chat_of(callback)
        if here is None:
            return
        message, chat_id, lang = here
        zone = (callback.data or "").removeprefix(ZONE_FOR_PICK_PREFIX)
        proposal = pending.proposal(chat_id)
        if proposal is None or proposal.picked is None:
            await stale(message, lang)
            return
        candidate = proposal.candidates[proposal.picked]
        pending.take_proposal(chat_id)
        await _save(
            message,
            chat_id,
            code=candidate.code,
            level=candidate.level,
            zone=zone,
            text=candidate.wording,
            file_ids=proposal.file_ids,
            source=proposal.source,
            lang=lang,
        )

    @router.callback_query(F.data.startswith(ZONE_FOR_MANUAL_PREFIX))
    async def on_zone_for_manual(callback: CallbackQuery) -> None:
        await callback.answer()
        here = chat_of(callback)
        if here is None:
            return
        message, chat_id, lang = here
        zone = (callback.data or "").removeprefix(ZONE_FOR_MANUAL_PREFIX)
        proposal = pending.proposal(chat_id)
        if proposal is None:
            await stale(message, lang)
            return
        sidecar.remember_zone(chat_id, zone)
        await _open_manual(message, chat_id, replace(proposal, zone_hint=zone), pending, lang)

    @router.callback_query(F.data == MANUAL_CALLBACK)
    async def on_manual(callback: CallbackQuery) -> None:
        await callback.answer()
        here = chat_of(callback)
        if here is None:
            return
        message, chat_id, lang = here
        proposal = pending.proposal(chat_id)
        if proposal is None:
            await stale(message, lang)
            return
        await _open_manual(message, chat_id, proposal, pending, lang)

    @router.callback_query(F.data.startswith(MANUAL_PAGE_PREFIX))
    async def on_manual_page(callback: CallbackQuery) -> None:
        await callback.answer()
        here = chat_of(callback)
        if here is None:
            return
        message, chat_id, lang = here
        raw = (callback.data or "").removeprefix(MANUAL_PAGE_PREFIX)
        proposal = pending.proposal(chat_id)
        if proposal is None or not raw.isdigit():
            await stale(message, lang)
            return
        await _show_manual_page(message, proposal, int(raw), lang)

    async def _save_manual(
        message: Message, chat_id: int, proposal: Proposal, index: int, level: str, lang: str
    ) -> None:
        """Фиксация ручного выбора.

        Формулировкой становится комментарий аудитора, если он был: это его
        собственные слова, а лучшего источника без модели нет. Кадр без
        комментария оставляет только вопрос пункта — и он же сразу правится
        кнопкой «Формулировка» под подтверждением (T056), поэтому запись не
        уходит в отчёт с чужим текстом молча.
        """
        item = proposal.manual[index]
        pending.take_proposal(chat_id)
        await _save(
            message,
            chat_id,
            code=item.code,
            level=level,
            zone=proposal.zone_hint,
            text=proposal.note.strip() or item.title,
            file_ids=proposal.file_ids,
            source=proposal.source,
            lang=lang,
        )

    @router.callback_query(F.data.startswith(MANUAL_PICK_PREFIX))
    async def on_manual_pick(callback: CallbackQuery) -> None:
        await callback.answer()
        here = chat_of(callback)
        if here is None:
            return
        message, chat_id, lang = here
        raw = (callback.data or "").removeprefix(MANUAL_PICK_PREFIX)
        proposal = pending.proposal(chat_id)
        if proposal is None or not raw.isdigit() or int(raw) >= len(proposal.manual):
            await stale(message, lang)
            return
        item = proposal.manual[int(raw)]
        if len(item.levels) == 1:
            await _save_manual(message, chat_id, proposal, int(raw), item.levels[0], lang)
            return
        await message.answer(
            t("record.ask_level", lang, code=item.code),
            reply_markup=levels_keyboard(f"{MANUAL_LEVEL_PREFIX}{raw}:", item.levels),
        )

    @router.callback_query(F.data.startswith(MANUAL_LEVEL_PREFIX))
    async def on_manual_level(callback: CallbackQuery) -> None:
        await callback.answer()
        here = chat_of(callback)
        if here is None:
            return
        message, chat_id, lang = here
        raw, _, level = (callback.data or "").removeprefix(MANUAL_LEVEL_PREFIX).partition(":")
        proposal = pending.proposal(chat_id)
        if proposal is None or not raw.isdigit() or int(raw) >= len(proposal.manual):
            await stale(message, lang)
            return
        await _save_manual(message, chat_id, proposal, int(raw), level, lang)

    @router.callback_query(F.data == SKIP_CALLBACK)
    async def on_skip(callback: CallbackQuery) -> None:
        await callback.answer()
        here = chat_of(callback)
        if here is None:
            return
        message, chat_id, lang = here
        pending.take_proposal(chat_id)
        await message.answer(t("record.skipped", lang))

    return router


def make_material_handler(
    pending: PendingStore,
) -> Callable[[Message, Material, str], Awaitable[None]]:
    """Готовый материал — разобрать по словам аудитора (T055).

    Первым делом снимается вопрос «Разобрать?», если он висел на этих кадрах:
    комментарий сильнее догадки по картинке (D046), и предлагать разбор кадра,
    по которому уже идёт разбор по словам, нельзя.
    """

    async def handle(message: Message, material: Material, lang: str) -> None:
        chat_id = material.chat_id
        offer = pending.take_offer_for(chat_id, material.photo_file_ids)
        if offer is not None:
            await _drop_question(message, chat_id, offer)

        note = (material.comment.text or "").strip()
        if material.comment.voice_file_id is not None:
            heard = await _hear_voice(message, material.comment.voice_file_id, lang)
            if heard is None:
                return
            note = heard.strip()

        await _analyze(
            message,
            chat_id,
            note=note,
            file_ids=material.photo_file_ids,
            source=sidecar.SOURCE_COMMENT,
            pending=pending,
        )

    return handle
