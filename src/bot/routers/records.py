"""Показ записанного посреди обхода: «что я записал» (T139, задача #110).

Находка второго юзабилити-прохода (T130). Список зафиксированного показывала
ровно одна команда — «Завершить». Она безопасна: показывает итог и даёт кнопки,
сама ничего не завершает. Но называется она так, что на середине обхода аудитор
её не нажмёт — он уверен, что закончит проверку и отчёт уйдёт партнёру. Функция
была, и была спрятана за пугающим именем.

Поэтому у показа появился свой вход, `/records`, и **содержимое у него то же**,
что в первой части `/finish`: одна функция `show_records` на оба пути. Две копии
списка разошлись бы молча — ровно так, как разошлась пометка нетипичной зоны в
T147.

Чего здесь нет и быть не должно:

* **Оценки.** Показ зовётся посреди обхода, а там процент не показывается
  (T162, решение владельца D072). Итог с процентом и буквой остаётся за
  завершением — там он и нужен.
* **Кнопок завершения.** Ни «Собрать отчёт», ни следа сдачи: вход задуман как
  безопасный, и если он хоть чем-то похож на завершение, аудитор не нажмёт и
  его.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from .. import sidecar, view
from ..inspection import read_inspection
from ..lang import chat_ui_lang
from ..texts import t

#: Имя команды. Она же объявляется в меню телеграма (`app.announce_commands`):
#: команда, о которой аудитор не знает, спрятана ровно так же, как была спрятана
#: эта функция за «Завершить».
RECORDS_COMMAND = "records"


async def show_records(message: Message, chat_id: int, lang: str) -> None:
    """Что уже записано и какие кадры остались без записи.

    Двумя сообщениями, а не одним: список записей и список кадров по отдельности
    читаются, а склеенные упираются в предел длины сообщения телеграма на первой
    же реальной проверке (двадцать записей — это уже за две тысячи знаков).

    Кадры без записи (T068) показываются и здесь, а не только при завершении:
    посреди обхода их ещё можно разобрать или переснять, а в конце проверки
    аудитор уже уехал с точки.
    """
    inspection = read_inspection(chat_id)
    if inspection is None:
        await message.answer(t("material.no_inspection", lang))
        return

    if inspection.findings:
        await message.answer(
            t("finish.records", lang, lines=view.record_lines(inspection.findings, lang))
        )
    else:
        await message.answer(t("finish.empty", lang))

    notes = sidecar.read(chat_id)
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


def build_records_router() -> Router:
    """Роутер показа записанного: одна команда и ничего больше."""
    router = Router(name="records")

    @router.message(Command(RECORDS_COMMAND))
    async def on_records(message: Message) -> None:
        chat_id = message.chat.id
        await show_records(message, chat_id, chat_ui_lang(chat_id))

    return router
