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

Отсюда же завершённая проверка уходит в историю (задача T123, решение D027):
`db.push_inspection`, следом `db.upload_photos`. Место выбрано не случайно —
это единственный момент, когда проверка закончена: у движка признака
«завершена» нет, а после отчёта аудитор может начать новую, и она перепишет
`inspection.json` вместе со всеми находками и идентификаторами кадров.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, Message

from src import db, domain
from src.domain.errors import ChecklistVersionMismatch, DomainError
from src.domain.translation import untranslated
from src.report import PhotoMissing, ReportError, build_letter, build_pdf

from .. import sidecar, view
from ..errors import BotNotesError
from ..inspection import read_inspection
from ..keyboards import (
    FINISH_BUILD_CALLBACK,
    FINISH_BUILD_NO_PHOTOS_CALLBACK,
    FINISH_EDIT_CALLBACK,
    FINISH_PICK_PREFIX,
    FINISH_RESUME_CALLBACK,
    VERSION_KEEP_CALLBACK,
    VERSION_SYNC_CALLBACK,
    edit_keyboard,
    finish_keyboard,
    pick_record_keyboard,
    version_mismatch_keyboard,
    without_photos_keyboard,
)
from ..lang import chat_ui_lang
from ..photos import download_all
from ..texts import t
from .records import show_records

logger = logging.getLogger(__name__)


async def show_summary(message: Message, chat_id: int, lang: str) -> None:
    """Итог, список зафиксированного и кадры без записи — тремя сообщениями.

    Тремя, а не одним: список записей и список кадров по отдельности читаются, а
    склеенные упираются в предел длины сообщения телеграма на первой же реальной
    проверке (двадцать записей — это уже за две тысячи знаков).

    Список и кадры показывает `records.show_records` — та же функция, что и
    команда «что записано» (T139). Одна на оба пути намеренно: две копии списка
    разошлись бы молча, ровно как разошлась пометка нетипичной зоны в T147.
    Здесь к ней добавляется то, чего в середине обхода быть не должно, — оценка
    (T162, D072) и кнопки завершения.
    """
    inspection = read_inspection(chat_id)
    if inspection is None:
        await message.answer(t("material.no_inspection", lang))
        return
    # Подпроцесс (26 мс) — в поток, чтобы бот не вставал на время расчёта (T101).
    try:
        score = await asyncio.to_thread(domain.score, chat_id)
    except ChecklistVersionMismatch as exc:
        # Методику переиздали, пока проверка шла (T148). Отказ блока подробен и
        # верен, но написан тому, кто зовёт блок из кода: в чат он уезжал общим
        # текстом последнего рубежа, из которого не видно ни версий, ни выхода
        # (T167). Обе версии — человеку, оба выхода — кнопками, выбор — за ним.
        logger.warning("расхождение версии методики в чате %s: %s", chat_id, exc)
        await message.answer(
            t("finish.version_mismatch", lang, recorded=exc.recorded, current=exc.current),
            reply_markup=version_mismatch_keyboard(lang),
        )
        return
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

    await show_records(message, chat_id, lang)

    await message.answer(
        t("finish.ask", lang),
        reply_markup=finish_keyboard(lang, can_edit=bool(inspection.findings)),
    )


def _reader(photos: Mapping[str, str]) -> Callable[[str], bytes | None]:
    """Как достать байты кадра по его идентификатору телеграма.

    Синхронная функция намеренно: такой её ждёт `db.upload_photos`, и зовётся
    она уже в рабочем потоке. Читается то, что бот скачал для отчёта, — второй
    поход в телеграм за теми же кадрами стоил бы и времени, и лимитов.
    """

    def read(file_id: str) -> bytes | None:
        path = photos.get(file_id)
        if path is None:
            return None
        try:
            return Path(path).read_bytes()
        except OSError:
            # Кадр скачался, но не читается. Молча выдать «кадра нет» нельзя:
            # `upload_photos` перечислит его как потерянный, а причина — здесь.
            logger.exception("кадр %s не прочитался с диска", file_id)
            return None

    return read


async def archive(
    message: Message,
    chat_id: int,
    photos: Mapping[str, str],
    lang: str,
    *,
    allow_missing: bool,
) -> None:
    """Отправить завершённую проверку в историю: сама проверка, затем кадры (T123).

    Зовётся ПОСЛЕ того, как отчёт и письмо уже у аудитора, и ни один её исход
    не отменяет сделанного: человек стоит на точке, документ он получил, и
    отказ базы для него — не повод переделывать проверку (D027).

    Четыре исхода, и они разные:

    * `ConfigError` — базы (или хранилища) в этой конфигурации просто нет.
      Работа без базы законна, и сообщать о ней нечего: строка в журнал.
    * `VersionMismatchError` — база в полном порядке, а методику переиздали,
      пока шла проверка (T182). Разбирается ОТДЕЛЬНО и до общей ветки: см. ниже.
    * прочий `DbError` — база есть, но не приняла. Молчать нельзя: аудитор
      иначе останется уверен, что история сохранена, а она нет.
    * успех — ни одного лишнего сообщения.

    Слив и выгрузка вынесены в поток: обе ходят в сеть, а цикл событий
    обслуживает и других аудиторов, и таймеры альбомов (T101).
    """
    try:
        inspection_id = await asyncio.to_thread(db.push_inspection, chat_id)
    except db.ConfigError as exc:
        logger.info("проверка чата %s не сливается в историю: %s", chat_id, exc)
        return
    except db.VersionMismatchError as exc:
        # Расхождение версии методики — не отказ базы (T182, задача #149). Связь
        # цела, строка не легла из-за того, что методику переиздали между итогом
        # и сливом, и текст «база не ответила» отправил бы аудитора чинить не то.
        # Отличается это ТИПОМ, а не разбором текста: сам текст `PushError`
        # написан тому, кто зовёт блок из кода, и правится вместе с кодом.
        #
        # Текст и клавиатура — те же, что на подсчёте (T167): случай тот же,
        # выходы те же, а вторая формулировка одного и того же разошлась бы с
        # первой молча. Клавиатура здесь обязательна: последняя строка текста
        # называет два выхода, и один из них — кнопка. Без неё сообщение врало
        # бы, а довести проверку до истории аудитору было бы нечем.
        logger.warning("расхождение версии методики на сливе чата %s: %s", chat_id, exc)
        await message.answer(
            t("finish.version_mismatch", lang, recorded=exc.recorded, current=exc.current),
            reply_markup=version_mismatch_keyboard(lang),
        )
        return
    except db.DbError:
        logger.exception("слив проверки чата %s не удался", chat_id)
        await message.answer(t("finish.not_archived", lang))
        return

    try:
        await asyncio.to_thread(
            db.upload_photos,
            inspection_id,
            fetch=_reader(photos),
            allow_missing=allow_missing,
        )
    except db.ConfigError as exc:
        logger.info("кадры проверки чата %s не выгружаются: %s", chat_id, exc)
    except db.DbError:
        logger.exception("выгрузка кадров проверки чата %s не удалась", chat_id)
        await message.answer(t("finish.photos_not_archived", lang))


async def warn_untranslated(message: Message, inspection: domain.Inspection, lang: str) -> None:
    """Сказать, что часть отчёта напечатана не на языке отчёта (T186, задача #153).

    Отчёт к этому моменту уже у аудитора, и это не оплошность порядка. Отказ
    здесь означал бы, что человек уезжает с точки без документа из-за
    непереведённого справочного поля: ошибка не его, править данные управляющей
    компании он не может, а партнёру нужен отчёт. Молча же печатать чужой язык
    нельзя — партнёр заметит это первым и будет прав.

    В чат идут коды: они не переводятся, и именно с ними идут в управляющую
    компанию. Сами значения полей — в журнал стенда: аудитору на точке они
    ничего не решают, а тому, кто понесёт правку, нужны дословно.

    Читается методика с диска, поэтому вызов уходит в поток — как и остальные
    обращения к ней из разговора.

    Собственный отказ этой проверки ничего за собой не роняет — по той же
    причине, по которой её не делают отказом: за ней стоят письмо партнёру и
    слив в историю (T123), и уронить их из-за непрочитанной методики было бы
    хуже, чем не прочитать её. Цена известна и названа: предупреждения не
    будет, причина — в журнале.
    """
    codes = {finding.code for finding in inspection.findings}
    zones = {finding.zone for finding in inspection.findings}
    try:
        found = await asyncio.to_thread(
            untranslated, inspection.report_lang, codes=codes, zones=zones
        )
    except (DomainError, OSError):
        logger.exception("язык методики не проверен для чата %s", message.chat.id)
        return
    if not found:
        return
    logger.warning(
        "методика не переведена на язык отчёта %s (чат %s): %s",
        inspection.report_lang,
        message.chat.id,
        "; ".join(f"{one.code}.{one.field} = «{one.text}»" for one in found),
    )
    await message.answer(
        t(
            "finish.untranslated",
            lang,
            lang=inspection.report_lang,
            codes=", ".join(dict.fromkeys(one.code for one in found)),
        )
    )


async def deliver(message: Message, chat_id: int, lang: str, *, allow_missing: bool) -> None:
    """Собрать отчёт и письмо, отдать их в чат и записать проверку в историю.

    Кадры скачиваются заранее и отдаются `report` готовой картой: `build_pdf`
    синхронный и выполняется в рабочем потоке, а качать из него нельзя — там нет
    цикла событий. Карта же держит правило «где лежит кадр» в одном месте.

    Временная папка с кадрами живёт до конца работы, а не до отдачи PDF: та же
    карта нужна выгрузке кадров в хранилище (T123). Скачивать их второй раз
    значило бы платить телеграму дважды за один и тот же кадр.
    """
    bot = message.bot
    inspection = read_inspection(chat_id)
    if bot is None or inspection is None:
        await message.answer(t("material.no_inspection", lang))
        return
    await message.answer(t("finish.building", lang))
    # Кадры записей и кадры информационной части (T179) — одним списком: карту
    # «ссылка → файл» блок `report` строит по обоим, и кадр поля, не скачанный
    # здесь, напечатался бы партнёру красной отметкой «фотография не приложена».
    refs = [ref for finding in inspection.findings for ref in finding.photos]
    refs += [ref for field in inspection.info.values() for ref in field.photos]
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
        except ReportError:
            # Текст движка — для того, кто чинит стенд: в нём внутренности
            # рендерера, пути во временный каталог и совет поставить системные
            # библиотеки (T151). Аудитору на точке нужен не он.
            logger.exception("отчёт чата %s не собрался", chat_id)
            await message.answer(t("finish.pdf_failed", lang))
            return

        await message.answer_document(FSInputFile(pdf))
        # Сразу под документом, а не до сборки: предупреждение говорит о том,
        # что в этом самом файле напечатано, и читается вместе с ним.
        await warn_untranslated(message, inspection, lang)
        # Отчёт у аудитора — с этой секунды проверка сдана (T153). Отмечается
        # именно отдача документа, а не нажатие кнопки: несобравшийся отчёт
        # сданной проверку не делает.
        try:
            sidecar.mark_handed_over(chat_id, len(inspection.findings))
        except BotNotesError:
            # Отчёт уже у человека, письмо и слив в историю впереди — уронить
            # их из-за незаписанной заметки было бы хуже самой заметки. Цена
            # известна и названа: `/start` снова назовёт проверку незавершённой.
            logger.exception("отметка о сдаче проверки чата %s не записалась", chat_id)
        try:
            letter = await asyncio.to_thread(build_letter, chat_id)
        except ReportError:
            # Свой текст, а не тот же: отчёт аудитор к этому моменту уже
            # получил, и «отчёт не собрался» было бы неправдой (T151).
            logger.exception("письмо чата %s не собралось", chat_id)
            await message.answer(t("finish.letter_failed", lang))
        else:
            await message.answer(t("finish.letter", lang, letter=letter))

        # После отчёта, а не вместо: не собравшееся письмо проверку в истории
        # не отменяет — она завершена ровно тем, что документ уже у аудитора.
        await archive(message, chat_id, found, lang, allow_missing=allow_missing)


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

    @router.callback_query(F.data == VERSION_SYNC_CALLBACK)
    async def on_version_sync(callback: CallbackQuery) -> None:
        """Перевести проверку на действующую методику — по явному нажатию (T167).

        Перевод меняет то, чем проверка будет измеряться, поэтому он и в блоке
        `domain` сделан отдельным вызовом, а не поведением подсчёта. След
        остаётся в самой проверке: отвечать «по какой методике это посчитали»
        придётся тогда, когда логов стенда уже не будет.
        """
        await callback.answer()
        here = chat_of(callback)
        if here is None:
            return
        message, chat_id, lang = here
        try:
            # Подпроцесса тут нет, но есть чтение и запись файла проверки под
            # блокировкой — в поток по той же причине, что и подсчёт (T101).
            inspection = await asyncio.to_thread(domain.sync_checklist_version, chat_id)
        except DomainError:
            logger.exception("перевод проверки чата %s на действующую методику не удался", chat_id)
            await message.answer(t("finish.version_sync_failed", lang))
            return
        await message.answer(t("finish.version_synced", lang, current=inspection.checklist_version))
        # Тупик кончается там же, где начался: аудитор снова видит итог и кнопки
        # завершения, а не остаётся с одним сообщением о переводе.
        await show_summary(message, chat_id, lang)

    @router.callback_query(F.data == VERSION_KEEP_CALLBACK)
    async def on_version_keep(callback: CallbackQuery) -> None:
        """Второй выход: вернуть прежнюю версию методики. Делается не в боте.

        Бот тут ничего не переставляет — он говорит, в каком положении осталась
        проверка и что она никуда не делась. Решение принято человеком, и
        подталкивать его ко второму нажатию нечем.
        """
        await callback.answer()
        here = chat_of(callback)
        if here is None:
            return
        message, chat_id, lang = here
        inspection = read_inspection(chat_id)
        if inspection is None:
            await message.answer(t("material.no_inspection", lang))
            return
        await message.answer(t("finish.version_kept", lang, recorded=inspection.checklist_version))

    @router.callback_query(F.data == FINISH_EDIT_CALLBACK)
    async def on_edit(callback: CallbackQuery) -> None:
        await callback.answer()
        here = chat_of(callback)
        if here is None:
            return
        message, chat_id, lang = here
        inspection = read_inspection(chat_id)
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
        inspection = read_inspection(chat_id)
        finding = None if inspection is None or not raw.isdigit() else inspection.finding(int(raw))
        if finding is None:
            await message.answer(t("edit.gone", lang, n=raw))
            return
        await message.answer(
            view.record_lines([finding], lang, chat_id=chat_id),
            reply_markup=edit_keyboard(finding.n, lang),
        )

    @router.callback_query(F.data == FINISH_BUILD_CALLBACK)
    async def on_build(callback: CallbackQuery, state: FSMContext) -> None:
        """«Собрать отчёт» открывает информационную часть, а не собирает отчёт.

        Порядок задан решениями D069 и D070 (задача T158): «Завершить» →
        подтверждение → информационная часть → PDF и письмо. Собранный раньше
        документ ответов на эти вопросы не содержит, и заметил бы это партнёр,
        а не аудитор. Сама сборка стоит за последним вопросом — `deliver`
        зовётся оттуда.

        Импорт местный и намеренно: информационная часть зовёт `deliver` из
        этого же модуля, и на верхнем уровне два модуля ссылались бы друг на
        друга. Разрывается связь здесь, в одной строке, а не переносом сборки
        отчёта в третий модуль — её подменяют тесты по имени
        `src.bot.routers.finish.build_pdf`.
        """
        from .info import start_info

        await callback.answer()
        here = chat_of(callback)
        if here is None:
            return
        message, chat_id, lang = here
        await start_info(message, state, chat_id, lang)

    @router.callback_query(F.data == FINISH_BUILD_NO_PHOTOS_CALLBACK)
    async def on_build_without_photos(callback: CallbackQuery) -> None:
        """Пересборка без кадров. Информационную часть заново не спрашиваем:
        она уже пройдена — эта кнопка появляется после неудавшейся сборки."""
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
