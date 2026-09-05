"""Разбор материала и фиксация записи (T055, T057, T067, T117, T121).

Правила, из которых собран весь этот файл.

**Ни один кадр не уходит в модель без решения человека** (D046, задача T067). На
кадр без комментария бот отвечает вопросом «Разобрать?» с кнопкой. Не нажали и
прислали комментарий — вопрос снимается, и разбор идёт по словам: слова аудитора
сильнее догадки по картинке. Это не таймер и не окно ожидания (те были в
отменённом D045), а явное действие.

**Есть комментарий — разбирается комментарий, кадр в модель не уходит** (D081,
задача T202). Владелец, дословно: «фото с комментм - обрабатываем коммент. фото
без коммента - разбираем фотку, возвращаем то что мы вычитали». Два потока, а не
два источника одного разбора. Кадр без комментария по-прежнему разбирается сам
собой только по кнопке (D046, выше). Правило живёт одним местом —
`recognize.needs_photo(note)`, — и здесь оно спрашивается, а не повторяется: две
копии одного правила разошлись бы, и увидел бы это счёт за токены, а не человек.

**Запись ПО РАЗБОРУ появляется только после подтверждения** (задача T055,
принцип 3 конституции «модель предлагает, фиксирует человек»). Кандидаты
показываются кнопками, и по ним `add_finding` зовётся из обработчика нажатия.

**Фиксация СЛОВАМИ подтверждения не ждёт** (T121, D064). Владелец, дословно:
«снимаем с текста подтверждение, потом добавим». Сошлась сверка со списком
нарушений — запись появляется сразу, нажимать нечего. Принцип 3 этим уточнён, а
не отменён: он в силе там, где пункт угадывает система (разбор кадра, разбор
моделью), и снят там, где зона и суть названы человеком.

Цена решения известна заранее и была названа владельцу: сопоставление слов с
пунктом промахивается, и без кнопки промах становится тихим. Отсюда два
требования, которые здесь важнее самой правки. Показ записи обязан быть **виден**
— его собирает `view.fixed_block` из вопроса пункта, слов аудитора и строки
карты. И рядом с записью обязан остаться **выход к модели**: правка кода пункта
в чате не предусмотрена, а те же слова снова поднимут тот же пункт, поэтому без
«Разобрать моделью» неверный код чинить было бы нечем.

**Зона берётся из слов аудитора** (D047), последняя названная запоминается и
подставляется догадкой (D048). Отдельного шага «выберите зону» в потоке нет —
кнопки зон появляются ровно тогда, когда зону взять неоткуда.

Порядок вызовов: **сначала сверка со списком нарушений, модель — если сверка не
ответила** (T117, D063). Владелец: «у нас тут не нужны размышления, а нужна
сверка с текущим списком нарушений». `fast_path` зовётся ДО `classify`;
сработал — запись сделана, и разбора не происходит вовсе (замер блока
`recognize`: 4.3 с на текст, 5.3 с на кадр — ждём именно рассуждение модели).
Не сработал — всё дальше как было, а причина отказа (`FastPath.reason`) остаётся
в журнале: она для замера, а не для экрана.

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
from src.recognize.classify import classify, needs_photo
from src.recognize.errors import ModelUnavailable, RecognizeError
from src.recognize.fastpath import FastItem, fast_path
from src.recognize.manual import manual_candidates
from src.recognize.models import UNKNOWN_ZONE
from src.recognize.transcribe import transcribe

from .. import refusal, sidecar, view
from ..inspection import read_inspection
from ..keyboards import (
    ANALYZE_PREFIX,
    MANUAL_CALLBACK,
    MANUAL_LEVEL_PREFIX,
    MANUAL_PAGE_PREFIX,
    MANUAL_PAGE_SIZE,
    MANUAL_PICK_PREFIX,
    MODEL_CALLBACK,
    PICK_PREFIX,
    SKIP_CALLBACK,
    ZONE_FOR_MANUAL_PREFIX,
    ZONE_FOR_PICK_PREFIX,
    analyze_keyboard,
    candidates_keyboard,
    edit_keyboard,
    fixed_keyboard,
    levels_keyboard,
    manual_keyboard,
    zones_keyboard,
)
from ..lang import chat_langs
from ..material import Comment, Material, MaterialStore, PhotoGroup
from ..pending import Offer, PendingStore, Proposal
from ..photos import fetch_bytes
from ..texts import t
from ..zones import zone_from_words

logger = logging.getLogger(__name__)

#: Что делать с кадрами, которые пришли без подписи и ждут комментария.
WaitingHandler = Callable[[Message, PhotoGroup, str], Awaitable[None]]


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
    """Показать предложения модели, пометив уже занятые пары «пункт + зона» (T137).

    Занятый пункт приходит сюда штатно: сверка со списком нарушений упёрлась в
    отказ движка, и материал ушёл модели — на кадре бывает второе нарушение.
    Но модель предлагает и тот же пункт тоже, и непомеченным он неотличим от
    остальных: нажатие даёт второй отказ подряд по поводу, о котором бот только
    что сказал сам.
    """
    text = t(
        "record.candidates",
        lang,
        count=len(proposal.file_ids),
        lines=view.candidate_lines(
            proposal.candidates, lang, refusal.occupied_pairs(message.chat.id)
        ),
    )
    if proposal.question:
        text = t("record.question", lang, question=proposal.question) + "\n\n" + text
    await message.answer(text, reply_markup=candidates_keyboard(len(proposal.candidates), lang))


async def _show_manual_page(message: Message, proposal: Proposal, page: int, lang: str) -> None:
    """Страница ручного перечня: 70+ пунктов зоны кнопками разом не показать.

    Занятые пары «пункт + зона» помечаются здесь же (T173), но не на кнопке:
    под формулировку там отведено 34 знака, и пометка съела бы ровно то, ради
    чего аудитор перечень открыл. Она уходит в текст над клавиатурой — у
    ручного перечня он до сих пор нёс только номер страницы, тогда как у
    перечня модели там стоит сам список.

    Пометка нужна тут по той же причине, что и в перечне модели: занятый пункт
    попадает в перечень штатно (на кадре бывает второе нарушение), и
    непомеченным он неотличим от остальных — нажатие даёт отказ по поводу, о
    котором бот уже говорил.
    """
    pages = max(1, ceil(len(proposal.manual) / MANUAL_PAGE_SIZE))
    page = max(0, min(page, pages - 1))
    start = page * MANUAL_PAGE_SIZE
    shown = proposal.manual[start : start + MANUAL_PAGE_SIZE]
    titles = [(start + shift, item.code, item.title) for shift, item in enumerate(shown)]
    # Зона перечня — та, по которой он собран: пара, а не код. Тот же пункт в
    # другой зоне законен, и пометить его значило бы отговаривать от верного.
    taken = view.manual_taken_line(
        [item.code for item in shown],
        proposal.zone_hint,
        lang,
        refusal.occupied_pairs(message.chat.id),
    )
    await message.answer(
        t("record.manual_page", lang, page=page + 1, pages=pages) + taken,
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
    _, report_lang = chat_langs(chat_id)
    try:
        items = await asyncio.to_thread(manual_candidates, zone, lang=report_lang)
    except RecognizeError as exc:
        # Сырой текст исключения — в журнал, а не в чат (тот же принцип, что
        # у отказа движка, T127): в нём бывают пути на диске и ссылки на
        # внутренние документы.
        logger.warning("ручной перечень не собрался в чате %s: %s", chat_id, exc)
        await message.answer(t("record.unavailable", lang))
        return
    ready = replace(proposal, manual=items, zone_hint=zone)
    pending.propose(chat_id, ready)
    await _show_manual_page(message, ready, 0, lang)


def _model_suggestion(proposal: Proposal) -> domain.Suggestion | None:
    """Что предложила МОДЕЛЬ по этому материалу (D077, T181).

    Берётся ПЕРВЫЙ кандидат, а не тот, который аудитор нажал. Разница здесь и
    есть весь смысл задачи: запиши мы нажатую кнопку, предложенная и итоговая
    тройки совпадали бы всегда, и «модель предложила одно, аудитор поправил на
    другое» перестало бы существовать как явление. Выбор второго кандидата —
    это и есть правка, ради которой сигнал собирается.

    Пустая зона приводится к `UNKNOWN`: у модели это осмысленный ОТВЕТ — «из
    слов аудитора места не видно», — а пустая строка уехала бы в базу как
    `NULL`, то есть как «модель промолчала», рядом с названным пунктом.
    """
    top = proposal.candidates[0] if proposal.candidates else None
    if top is None:
        return None
    return domain.Suggestion(
        code=top.code,
        level=top.level,
        zone=top.zone or UNKNOWN_ZONE,
        confidence=top.confidence,
    )


async def _try_fast(
    message: Message, chat_id: int, base: Proposal, pending: PendingStore, lang: str
) -> bool:
    """Сверка со списком нарушений до модели: сошлось — записать сразу (T117, T121).

    Возвращает, взяла ли сверка материал на себя. Не взяла — дальше разбирает
    модель, как раньше.

    В поток вынесено намеренно, хотя ни сети, ни подпроцесса тут нет. Вызов
    читает с диска методику и карту кадров: 2.3 мс на сработавшем комментарии,
    1.2 мс на отказе — в пятьдесят раз дороже `get_state` (0.046 мс), который в
    цикле оставлен осознанно. Пока чтение идёт, бот не обслуживает никого, и на
    двадцати аудиторах это та же растущая очередь, из-за которой в поток уехали
    вызовы движка (T101).

    Язык здесь — интерфейса, а не отчёта, и это не оплошность: `item.title` —
    вопрос чек-листа, который аудитор читает у себя в чате. В запись он не
    попадает никогда (её текстом становятся слова самого аудитора), поэтому
    языку отчёта подчиняться ему незачем.

    Отказ движка (та же пара «пункт + зона» уже занята) тупиком не заканчивается:
    причину аудитор уже прочитал, а материал уходит модели — там пункт можно
    выбрать другой. Раньше эту роль играли кнопки рядом с предложением, но
    предложения больше нет, и упереться в отказ молча аудитор не должен.

    Предложение запоминается ПОСЛЕ удачной записи: оно живёт здесь только ради
    кнопки «Разобрать моделью» под ней. Не записалось — и кнопке нечего
    разбирать: материал уже у модели.
    """
    found = await asyncio.to_thread(fast_path, base.note, base.zone_hint or None, lang=lang)
    if found.item is None:
        # `reason` — для замера (`tools/fastpath_measure.py`) и разбора, поэтому
        # он идёт в журнал, а не в чат: аудитору он ничего не объясняет, а
        # объяснять отказ, за которым просто следует обычный разбор, нечем.
        logger.info("быстрый путь не сработал в чате %s: %s", chat_id, found.reason)
        return False
    item = found.item
    saved = await _save(
        message,
        chat_id,
        code=item.code,
        level=item.level,
        zone=item.zone,
        text=base.note,
        file_ids=base.file_ids,
        source=base.source,
        lang=lang,
        # Здесь слова аудитора СОВПАДАЮТ с текстом записи, и всё равно
        # передаются своим полем: совпадение это свойство быстрого пути, а не
        # правило. Полагаться на него значило бы читать выборку по-разному в
        # зависимости от того, каким путём легла запись.
        words=base.note,
        auto=item,
        zone_guessed=not base.zone_spoken,
        # Быстрый путь — это и есть тот список терминов, который владелец просил
        # пополнять (D077). Промах здесь опаснее всего: подтверждения у него нет
        # (D064), и увидеть его можно только правкой записи следом. Уверенности
        # у сверки нет вовсе — строгий критерий либо сходится, либо нет, — и
        # ноль вместо неё был бы ложью: «система ни в чём не уверена» это
        # осмысленное утверждение, и по нему ставят порог отбора.
        suggested=domain.Suggestion(code=item.code, level=item.level, zone=item.zone),
    )
    if not saved:
        return False
    pending.propose(chat_id, replace(base, fast=item))
    return True


async def _analyze(
    message: Message,
    chat_id: int,
    *,
    note: str,
    file_ids: tuple[str, ...],
    source: str,
    pending: PendingStore,
    fast: bool = True,
) -> None:
    """Сверить со списком нарушений, а если не сошлось — спросить разбор.

    Есть комментарий — разбирается комментарий, и кадр не уходит в модель
    (D081, T202). Скачивания у телеграма тогда тоже не происходит: байты,
    которые никуда не поедут, стоили бы запроса к телеграму на каждый
    прокомментированный кадр. Правило одно на продукт и живёт в `recognize`
    (`needs_photo`) — бот его не повторяет своими словами, а спрашивает.

    `fast=False` приходит с кнопки «Разобрать моделью»: второй заход обязан
    дойти до модели, иначе кнопка возвращала бы тот же быстрый ответ по кругу.
    """
    lang, report_lang = chat_langs(chat_id)
    # Слова текущего комментария — первыми, память — только если о зоне в них
    # ничего не сказано (T124). Обратный порядок и был дефектом: «в зале лужа»
    # ложилось в горячий цех, потому что там была прошлая запись.
    spoken = zone_from_words(note)
    zone_hint = spoken or sidecar.read(chat_id).zone
    base = Proposal(
        file_ids=file_ids,
        source=source,
        note=note,
        zone_hint=zone_hint,
        zone_spoken=spoken is not None,
    )

    if fast and note and await _try_fast(message, chat_id, base, pending, lang):
        return

    bot = message.bot
    photo = (
        await fetch_bytes(bot, file_ids[0])
        if needs_photo(note) and bot is not None and file_ids
        else None
    )

    await message.answer(t("record.thinking", lang))
    try:
        suggestion = await asyncio.to_thread(
            classify, note, photo, zone_hint or None, lang=report_lang
        )
    except ModelUnavailable as exc:
        # Модель недоступна — проверка не встаёт: тот же перечень пунктов
        # показывается кнопками, выбирает человек (контракт `recognize`).
        # Сырой текст исключения — в журнал, а не в чат: в нём бывают пути на
        # диске и ссылки на внутренние документы, аудитору они ни к чему.
        logger.warning("модель недоступна в чате %s: %s", chat_id, exc)
        await message.answer(t("record.degraded", lang))
        await _open_manual(message, chat_id, base, pending, lang)
        return
    except RecognizeError as exc:
        logger.warning("разбор недоступен в чате %s: %s", chat_id, exc)
        await message.answer(t("record.unavailable", lang))
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
    words: str = "",
    auto: FastItem | None = None,
    zone_guessed: bool = False,
    suggested: domain.Suggestion | None = None,
) -> bool:
    """Зафиксировать запись и показать её (T055, T121).

    Возвращает, получилось ли. Отказ движка — не редкость и не ошибка бота: тот
    же пункт в той же зоне аудитор снимает дважды за обход. Поэтому предложение
    после отказа не выбрасывается, и человек выбирает другого кандидата, а не
    пересылает кадр.

    `auto` не пуст, когда запись легла по словам сама, без подтверждения (T121,
    D064). Тогда и показ другой: не одна строка, а блок с вопросом пункта,
    словами аудитора и строкой карты, и под ним — выход к модели рядом с
    правками. Подтверждённая запись такого блока не получает: её пункт аудитор
    уже прочитал на кнопке, а таблица после каждого кадра запрещена
    (`docs/06-mvp-bot.md`, шаг 5).

    Комментарий аудитора в запись не пишется намеренно: поле `comment` движок
    печатает в отчёте партнёру, а сказанное вслух на точке для партнёра не
    предназначено. В отчёт идёт формулировка, собранная по правилам фиксации.

    `words` — те самые сказанные слова, и хранятся они ОТДЕЛЬНО от текста записи
    (T183). До этой задачи они не доживали нигде: `pending` держит их в памяти
    процесса, а в запись они не попадали по причине абзацем выше — и разобрать,
    почему система промахнулась, было не по чему. Словами считается весь
    материал аудитора об этом кадре, каким он пришёл: подпись, отдельное
    сообщение и расшифровка голоса приходят сюда одной строкой (`Proposal.note`),
    и разделять их нечем — для разбора промаха важно то, на чём система приняла
    решение. Пусто — аудитор не сказал ничего (разбор голого кадра).

    `suggested` — что система предложила ДО нажатия (D077, T181). Передаётся
    здесь и только здесь: это единственный момент, когда предложение и запись
    существуют одновременно. Предложения живут в `pending`, то есть в памяти
    процесса, и после перезапуска взять их будет неоткуда — сигнал о промахе
    терялся бы целиком, как терялся до этой задачи. Пусто — предложения не было
    (ручной перечень); домен запишет это как «система не предлагала ничего», и
    в базе такая запись не выглядит попаданием модели.
    """
    # Движок вызывается подпроцессом, и это 27 мс на вызов. В цикле событий
    # такой вызов останавливает бота ЦЕЛИКОМ — он не обслуживает ни других
    # аудиторов, ни таймеры альбомов (замер T101: подтверждение записи стоило
    # 47 мс, и очередь росла линейно — двадцать аудиторов, секунда последнему).
    try:
        finding = await asyncio.to_thread(
            domain.add_finding,
            chat_id,
            code,
            level,
            zone,
            text,
            source=source,
            words=words,
            suggested=suggested,
        )
    except DomainError as exc:
        # Отказ движка разбирается, а не пересказывается (T127): пункт и зона
        # называются по-человечески, а занятая пара «пункт + зона» — частый
        # случай — приводит к кнопкам той записи, которая её заняла.
        told = refusal.not_recorded(chat_id, code=code, zone=zone, lang=lang, exc=exc)
        await message.answer(
            told.text,
            reply_markup=edit_keyboard(told.clash.n, lang) if told.clash is not None else None,
        )
        return False
    for file_id in file_ids:
        try:
            await asyncio.to_thread(domain.attach_photo, chat_id, finding.n, file_id)
        except DomainError:
            # Запись уже есть, и терять её из-за одного кадра нельзя. Молчанием
            # это не станет: не прикрепившийся кадр остаётся в заметках без
            # записи и попадёт в список кадров без записи при завершении (T068).
            logger.exception("кадр %s не прикрепился к записи #%s", file_id, finding.n)
    sidecar.remember_zone(chat_id, zone)
    # `get_state` — чтение файла, 0.1 мс: в поток не выносится, обёртка стоила
    # бы дороже самой операции. Оценка здесь больше не считается вовсе (T162,
    # D072): процент по ходу обхода не показывается, а считать его ради
    # выброшенного числа значило бы платить подпроцессом (26 мс) за каждую
    # запись впустую.
    saved = read_inspection(chat_id)
    current = None if saved is None else saved.finding(finding.n)
    shown = current or finding
    if auto is None:
        # Подтверждённая запись показывается не строкой, а блоком (T135): к
        # строке добавлены вопрос пункта словами и то, что уйдёт в отчёт
        # партнёру. Код в строке глазами не проверяется, а формулировка —
        # проверяется, и прочитать её надо ДО того, как документ уедет.
        await message.answer(
            view.confirmed_block(
                shown,
                lang,
                title=refusal.item_title(shown.code, lang),
                zone_guessed=zone_guessed,
            ),
            reply_markup=edit_keyboard(finding.n, lang),
        )
    else:
        await message.answer(
            view.fixed_block(
                shown, lang, title=auto.title, cue=auto.cue, zone_guessed=zone_guessed
            ),
            reply_markup=fixed_keyboard(finding.n, lang),
        )
    return True


def build_record_router(*, store: MaterialStore, pending: PendingStore) -> Router:
    """Роутер разбора: вопрос «Разобрать?», предложения кнопками, фиксация."""
    router = Router(name="record")

    def chat_of(callback: CallbackQuery) -> tuple[Message, int, str] | None:
        """Сообщение, чат и язык интерфейса — или ничего, если нажатие пришло не оттуда."""
        message = callback.message
        if not isinstance(message, Message):
            return None
        chat_id = message.chat.id
        lang, _ = chat_langs(chat_id)
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
            source=domain.SOURCE_PHOTO,
            pending=pending,
        )

    @router.callback_query(F.data == MODEL_CALLBACK)
    async def on_model(callback: CallbackQuery) -> None:
        """«Разобрать моделью»: сверка по словам ответила не то или не на всё.

        После T121 стоит под уже сделанной записью и остаётся единственным
        способом починить неверный ПУНКТ: правка в чате меняет зону, класс и
        формулировку, но не код, а те же слова снова поднимут тот же пункт.

        Сама запись при этом не трогается. Разбор её не удаляет и не правит: он
        предлагает кандидатов, и что делать с прежней записью, решает аудитор
        кнопкой «Удалить». Удалять её здесь молча было бы хуже — отказ модели
        оставил бы аудитора и без записи, и без разбора.

        Материал тот же — те же слова и тот же кадр, — но сверка со списком в
        этот раз пропускается: иначе кнопка возвращала бы ту же запись по кругу.
        """
        await callback.answer()
        here = chat_of(callback)
        if here is None:
            return
        message, chat_id, lang = here
        proposal = pending.proposal(chat_id)
        if proposal is None or proposal.fast is None:
            await stale(message, lang)
            return
        await _analyze(
            message,
            chat_id,
            note=proposal.note,
            file_ids=proposal.file_ids,
            source=proposal.source,
            pending=pending,
            fast=False,
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
        if await _save(
            message,
            chat_id,
            code=candidate.code,
            level=candidate.level,
            zone=candidate.zone,
            text=candidate.wording,
            file_ids=proposal.file_ids,
            source=proposal.source,
            lang=lang,
            # Текстом записи стала формулировка МОДЕЛИ, а слова аудитора — те,
            # что он сказал об этом кадре (T183). Разница между ними и есть то,
            # по чему потом разбирают промах.
            words=proposal.note,
            # Зона — догадка ровно тогда, когда её никто не называл, а модель
            # вернула ту самую, которую ей подсказали из памяти (T156). Своя
            # зона модели памятью не является: это её ответ, а не прошлая
            # запись, и оговорка о нём соврала бы.
            zone_guessed=(
                not proposal.zone_spoken
                and bool(proposal.zone_hint)
                and candidate.zone == proposal.zone_hint
            ),
            suggested=_model_suggestion(proposal),
        ):
            pending.take_proposal(chat_id)

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
        if await _save(
            message,
            chat_id,
            code=candidate.code,
            level=candidate.level,
            zone=zone,
            text=candidate.wording,
            file_ids=proposal.file_ids,
            source=proposal.source,
            lang=lang,
            words=proposal.note,
            # Зону назвал человек кнопкой, а в предложении остаётся ответ
            # модели — `UNKNOWN`. Подставить сюда выбранную зону значило бы
            # спрятать её отказ и превратить промах в попадание.
            suggested=_model_suggestion(proposal),
        ):
            pending.take_proposal(chat_id)

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
        # Зону назвал сам аудитор, пусть и кнопкой, а не словами: догадкой из
        # памяти она после этого не является (T156).
        await _open_manual(
            message,
            chat_id,
            replace(proposal, zone_hint=zone, zone_spoken=True),
            pending,
            lang,
        )

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
        if await _save(
            message,
            chat_id,
            code=item.code,
            level=level,
            zone=proposal.zone_hint,
            text=proposal.note.strip() or item.title,
            file_ids=proposal.file_ids,
            source=proposal.source,
            lang=lang,
            # Модель тут промолчала, и слова аудитора становятся ценнее, а не
            # наоборот: по ним видно, чего в списке слов не хватило.
            words=proposal.note,
            # Перечень собран по зоне из памяти, и человек выбрал в нём пункт,
            # а не зону: пометка нужна здесь ровно по тому же правилу (T156).
            # Назвал зону сам — кнопкой или словами — `zone_spoken` уже стоит.
            zone_guessed=not proposal.zone_spoken,
            # Предложения здесь нет и быть не может: ручной перечень
            # показывается ровно тогда, когда модель не ответила ничего —
            # недоступна или вернула пустой список. Записать предложением
            # выбранный человеком пункт значило бы утопить настоящие промахи в
            # выборке для управляющей компании: ручных записей на порядок
            # больше, и все они выглядели бы попаданием модели (D077).
            suggested=None,
        ):
            pending.take_proposal(chat_id)

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
            source=domain.SOURCE_COMMENT,
            pending=pending,
        )

    return handle
