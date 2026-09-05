"""Правка записи ОТВЕТОМ на сообщение бота (задача T204, решение D081).

Владелец, дословно: «Если комментарий надо поправить - ОТВЕТОМ на сообщение
бота пользователь вносит корректировку… условно: распознала система грязный
<объект> - но написала в отбивке что это стол в холодном цехе. значит
пользователь на это собщение пишет: горячий цех, <объект>. система берет
уже этот комментарий и ищет нужный пункт в чеклисте».

Название объекта здесь заменено: это строка методики управляющей компании, а
репозиторий публичный (сторож — `tests/test_methodology_leak.py`). Дословная
формулировка владельца целиком лежит в `docs/forge/decisions.md`, D081.

Три правила, из которых собран этот модуль.

**Адресует ответ КОНКРЕТНУЮ запись.** Телеграм присылает сообщение, на которое
отвечают, и по его номеру находится запись — карта ведётся в заметках бота
(`sidecar.record_of`). «Последняя запись» адресатом быть не может: на точке
между разбором и ответом проходят минуты, и за это время аудитор успевает
прислать ещё кадр.

**Пункт ищется заново, тем же путём.** Сверка со списком нарушений, а если она
не сошлась — модель, а если и модель молчит — ручной перечень. Своей дороги для
правки нет намеренно: одни и те же слова обязаны давать один и тот же ответ, а
две дороги разошлись бы молча.

**Ответ не на запись работает как раньше.** Аудитор отвечает и на свои кадры
(связывание комментария, T053), и на служебные сообщения бота. Такой ответ
уходит дальше по роутерам нетронутым — `SkipHandler`, тем же приёмом, что и
брошенный вопрос о новой формулировке (`routers/edit.py`).

Кадр в этом разборе не участвует: у ответа его нет, а у записи он уже есть.
Прикреплять к правке нечего, и модель смотрит на слова человека — как и велит
D081.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.types import Message

from src import domain

from .. import sealed, sidecar
from ..inspection import read_inspection
from ..lang import chat_ui_lang
from ..pending import PendingStore
from ..texts import t
from .record import analyze, hear_voice

logger = logging.getLogger(__name__)


def _addressed(message: Message) -> int | None:
    """Запись, о которой говорит ответ аудитора, — или ничего.

    Ничего — обычный исход: ответ на кадр, на вопрос «Разобрать?», на итог. В
    заметках карта только тех сообщений, которыми бот показывал записи.
    """
    replied = message.reply_to_message
    if replied is None:
        return None
    return sidecar.record_of(message.chat.id, replied.message_id)


def build_correct_router(*, pending: PendingStore) -> Router:
    """Роутер правки ответом. Стоит ДО приёма материала — иначе ответ уедет
    комментарием к ждущему кадру, и вместо правки появится вторая запись."""
    router = Router(name="correct")

    @router.message(F.reply_to_message, F.voice | (F.text & ~F.text.startswith("/")))
    async def on_reply(message: Message) -> None:
        chat_id = message.chat.id
        n = _addressed(message)
        if n is None:
            # Не про запись — пусть работает прежнее связывание комментария.
            raise SkipHandler
        lang = chat_ui_lang(chat_id)
        inspection = read_inspection(chat_id)
        if inspection is None:
            await message.answer(t("material.no_inspection", lang))
            return
        if sealed.is_sealed(chat_id):
            # Запрет T201 действует и здесь: правка ответом — та же правка
            # отчёта, только другой дверью.
            await sealed.refuse(message, lang)
            return
        if inspection.finding(n) is None:
            # Сообщения о снятых записях остаются в переписке навсегда, и
            # отвечают на них по ошибке. Завести по такому ответу новую запись
            # было бы худшим исходом: аудитор просил поправить, а получил бы
            # вторую строку в отчёте партнёру.
            await message.answer(t("edit.gone", lang, n=n))
            return

        note = (message.text or "").strip()
        if message.voice is not None:
            heard = await hear_voice(message, message.voice.file_id, lang)
            if heard is None:
                return
            note = heard.strip()
        if not note:
            await message.answer(t("correct.empty", lang, n=n))
            return

        await analyze(
            message,
            chat_id,
            note=note,
            # Кадры записи сюда не передаются намеренно: они уже прикреплены к
            # ней, и второй раз `attach_photo` получил бы тот же идентификатор.
            file_ids=(),
            source=domain.SOURCE_COMMENT,
            pending=pending,
            correcting=n,
        )

    return router
