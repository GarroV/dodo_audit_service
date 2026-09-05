"""Инлайн-клавиатуры мастера начала проверки (T050, T051, T052).

Callback-данные — код, а не формулировка (принцип проекта «сущности связываются
кодами, не текстом»): текст кнопки можно менять и переводить, `callback_data`
нет. Тем же кодом вид проверки и живёт дальше — в самой проверке, в базе, в
отпечатке (T152): таблица перевода лежит в предметной области
(`domain.INSPECTION_KINDS`), потому что перечень видов проверки — методика, а
не набор надписей на кнопках.

Языки у этой таблицы разные не для красоты, а потому, что языков у проекта три
(T131): на кнопке вид проверки стоит на языке ИНТЕРФЕЙСА, а в шапку отчёта
партнёру уезжает на языке ОТЧЁТА. На английском стенде с русским отчётом кнопка
обязана читаться «Planned», а в шапке обязана стоять «Плановая».
"""

from __future__ import annotations

from collections.abc import Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.domain import INSPECTION_KINDS
from src.domain import kind_title as domain_kind_title
from src.domain.errors import ValidationError

from .errors import BotTextError
from .info import KIND_YES_NO
from .texts import t

#: Код вида проверки → слово на каждом языке. Живёт в предметной области
#: (`src/domain/kinds.py`), здесь только имя для чтения: своей копии у бота нет
#: и быть не может — вид проверки нужен ещё и базе, и письму по записанной
#: проверке, а две копии таблицы разошлись бы молча.
#:
#: В каталоге текстов (`texts.py`) её нет намеренно: там строки, которые читает
#: только аудитор, а эта уезжает партнёру в документ, и переводится по языкам
#: методики, а не интерфейса.
KIND_TITLES = INSPECTION_KINDS

#: Код кнопки языка отчёта = сам код языка методики (`ru`/`en`, `domain.models.TEXT_LANGS`).
#: Второй копии не нужно: то, что летит в `callback_data`, и есть значение параметра.
#:
#: Надписи здесь не переводятся и переводиться не должны: язык называют на нём
#: самом. «Русский» в английском интерфейсе ищет глазами тот, кому он нужен, а
#: «Russian» — никто. Это тот редкий случай, когда строка мимо каталога не
#: дефект: перевода у неё нет по существу.
LANG_LABELS: dict[str, str] = {
    "ru": "Русский",
    "en": "English",
}


NEW_INSPECTION_CALLBACK = "start:new"
KIND_PREFIX = "start:kind:"
LANG_PREFIX = "start:lang:"
RESUME_CONTINUE_CALLBACK = "start:resume:continue"
RESUME_NEW_CALLBACK = "start:resume:new"


def kind_title(code: str, lang: str) -> str:
    """Вид проверки словами. Язык — параметр, и он тут не всегда язык интерфейса.

    Перевод берётся у предметной области; здесь только вид отказа приводится к
    боту — остальной разговор падает `BotTextError`, и разбирать в одном месте
    два разных класса ради одной надписи не за чем.

    Незаведённый язык — отказ, а не откат на русский: молчаливый откат поставил
    бы русское слово в шапку английского отчёта партнёру, и заметил бы это
    партнёр, а не мы.
    """
    try:
        return domain_kind_title(code, lang)
    except ValidationError as exc:
        raise BotTextError(str(exc)) from exc


def new_inspection_keyboard(lang: str) -> InlineKeyboardMarkup:
    """Единственная кнопка входа — «Новая проверка».

    Язык — параметр с T131: это первое, что видит человек, открывший демо, и
    русская надпись здесь была единственным русским местом английского стенда.
    """
    builder = InlineKeyboardBuilder()
    builder.add(
        InlineKeyboardButton(
            text=t("btn.new_inspection", lang), callback_data=NEW_INSPECTION_CALLBACK
        )
    )
    return builder.as_markup()


def kind_keyboard(lang: str) -> InlineKeyboardMarkup:
    """Вид проверки — по одной кнопке в ряд, порядок как в `docs/06-mvp-bot.md`.

    `lang` здесь — язык ИНТЕРФЕЙСА: аудитор читает кнопку сам. В шапку отчёта
    тот же вид уедет на языке отчёта, и берётся он тем же `kind_title` уже
    после того, как язык отчёта выбран следующим шагом мастера.
    """
    builder = InlineKeyboardBuilder()
    for code in KIND_TITLES:
        builder.button(text=kind_title(code, lang), callback_data=f"{KIND_PREFIX}{code}")
    builder.adjust(1)
    return builder.as_markup()


def lang_keyboard() -> InlineKeyboardMarkup:
    """Язык отчёта — коды методики (`domain.models.TEXT_LANGS`), не что-либо ещё."""
    builder = InlineKeyboardBuilder()
    for code, label in LANG_LABELS.items():
        builder.button(text=label, callback_data=f"{LANG_PREFIX}{code}")
    builder.adjust(1)
    return builder.as_markup()


def resume_keyboard(lang: str) -> InlineKeyboardMarkup:
    """Незавершённая проверка найдена — предложить «Продолжить» или «Начать новую» (T052)."""
    builder = InlineKeyboardBuilder()
    builder.button(text=t("btn.resume_continue", lang), callback_data=RESUME_CONTINUE_CALLBACK)
    builder.button(text=t("btn.resume_new", lang), callback_data=RESUME_NEW_CALLBACK)
    builder.adjust(1)
    return builder.as_markup()


# --- вторая очередь: разбор, правки, завершение (T055-T058, T067, T068) ---

#: Надписи на кнопках берутся из каталога текстов, а не пишутся здесь строкой:
#: язык интерфейса — параметр проверки, и клавиатура обязана его слушаться так
#: же, как сообщения. Клавиатуры первой очереди этого ещё не умеют — их надписи
#: прибиты по-русски; переписывать принятый код второй очереди не поручено.

#: Кадр пришёл без комментария — «Разобрать?» (T067, D046). В коде кнопки едет
#: первый `message_id` группы: по нему группа снимается с очереди ожидания.
ANALYZE_PREFIX = "rec:analyze:"

#: Кандидат модели по его месту в показанном списке.
PICK_PREFIX = "rec:pick:"
SKIP_CALLBACK = "rec:skip"
#: Отдать модели тот же материал, который сверка по словам уже разобрала сама
#: (T117, D063). После T121 кнопка стоит не рядом с предложением, а под уже
#: сделанной записью: подтверждать нечего, но пункт бывает не тот. Место в
#: списке в коде кнопки не едет: материал ровно один — он лежит в предложении
#: чата.
MODEL_CALLBACK = "rec:model"
#: Открыть перечень пунктов для ручного выбора (T034 со стороны бота).
MANUAL_CALLBACK = "rec:manual"
#: Страница ручного перечня.
MANUAL_PAGE_PREFIX = "rec:mp:"
#: Пункт из ручного перечня и класс к нему.
MANUAL_PICK_PREFIX = "rec:mi:"
MANUAL_LEVEL_PREFIX = "rec:ml:"
#: Зона, названная кнопкой: для выбранного кандидата и для входа в перечень.
ZONE_FOR_PICK_PREFIX = "rec:zp:"
ZONE_FOR_MANUAL_PREFIX = "rec:zm:"

#: Правки записи (T056): что менять у записи №n.
EDIT_PREFIX = "edit:"
EDIT_ZONE = "zone"
EDIT_LEVEL = "level"
EDIT_TEXT = "text"
EDIT_DROP = "drop"
#: Новое значение поля записи.
EDIT_ZONE_PREFIX = "ez:"
EDIT_LEVEL_PREFIX = "el:"

#: Завершение (T058).
FINISH_BUILD_CALLBACK = "fin:build"
FINISH_BUILD_NO_PHOTOS_CALLBACK = "fin:nophoto"
FINISH_EDIT_CALLBACK = "fin:edit"
FINISH_RESUME_CALLBACK = "fin:resume"
FINISH_PICK_PREFIX = "fin:pick:"
#: Расхождение версии методики (T167). Две кнопки, потому что выходов два и оба
#: за человеком: молча выбрать любой из них бот права не имеет.
VERSION_SYNC_CALLBACK = "fin:ver:sync"
VERSION_KEEP_CALLBACK = "fin:ver:keep"

#: Информационная часть в конце проверки (T158, D069, D070). Пропуск есть у
#: каждого поля — ни одно из них не обязательно; «дальше к отчёту» снимает
#: сразу все оставшиеся, чтобы аудитор не упирался в семь вопросов подряд.
INFO_SKIP_CALLBACK = "info:skip"
INFO_DONE_CALLBACK = "info:done"
INFO_YES_CALLBACK = "info:yes"
INFO_NO_CALLBACK = "info:no"
INFO_SAVE_CALLBACK = "info:save"

#: Сколько пунктов показывать на странице ручного перечня. Больше десятка
#: кнопок на телефоне превращаются в свиток, а перечень зоны — это 70+ пунктов.
MANUAL_PAGE_SIZE = 8

#: Сколько знаков формулировки пункта влезает в кнопку, не разъезжаясь на
#: телефоне в три строки.
BUTTON_TITLE_LIMIT = 34

#: Сколько кнопок выбора кандидата ставить в ряд. Было пять — под голую цифру.
#: С надписью «Записать №1» (T136) пять в ряд на телефоне сжимаются в нечитаемое,
#: а кандидатов модель возвращает единицы, так что ряд почти всегда один.
PICK_BUTTONS_PER_ROW = 3

#: Предел подписи ЗОНЫ — свой, потому что зона стоит в ряду одна (T140, #111).
#:
#: Откуда число. Общий `BUTTON_TITLE_LIMIT` = 34 выбран под кнопку в ПОЛОВИНУ
#: ряда: два столбца делят ширину пополам. Ряд из одной кнопки — это те же две
#: половины плюс промежуток между ними, то есть тех же строк текста влезает
#: вдвое больше: 2 × 34 = 68. Промежуток в счёт не берётся намеренно — число
#: должно быть не больше того, что влезает, а меньше можно.
#:
#: Что это даёт на боевой методике: десять зон, самое длинное название — 36
#: знаков по-русски и 32 по-английски. При 34 резалось одно (по-русски) и
#: висел хвост «…курьерская з…»; при 68 не режется ни одно, и остаётся запас в
#: 32 знака на названия, которые управляющая компания напишет позже.
#: Проверяется фактом на самой методике — `tests/test_bot_zone_buttons.py`.
ZONE_TITLE_LIMIT = 68


def _short(text: str, limit: int = BUTTON_TITLE_LIMIT) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def analyze_keyboard(anchor_id: int, lang: str) -> InlineKeyboardMarkup:
    """«Разобрать?» на кадр без комментария (T067, D046).

    Единственная кнопка, и это не экономия: отказ выражается бездействием —
    аудитор просто присылает комментарий, и вопрос снимается сам. Кнопка «нет»
    заставляла бы отвечать на каждый кадр.
    """
    builder = InlineKeyboardBuilder()
    builder.button(text=t("btn.analyze", lang), callback_data=f"{ANALYZE_PREFIX}{anchor_id}")
    return builder.as_markup()


def candidates_keyboard(count: int, lang: str, *, correcting: bool = False) -> InlineKeyboardMarkup:
    """Кандидаты действием плюс выход на ручной перечень и отказ.

    В `callback_data` едет место в показанном списке — его переводить не надо, и
    менялось здесь не оно. Менялась НАДПИСЬ (T136, issue #107): она была голой
    цифрой, и ряд читался как «1 // Выбрать пункт // Не записывать» — номер
    рядом с двумя глаголами, то есть подпись к чему-то, а не действие.

    Номер в надписи остаётся ровно там, где он что-то связывает: кандидатов
    несколько, и надо понять, какая кнопка какой строке перечня отвечает. При
    единственном кандидате его нет — номер, у которого нет второго, лишний.

    Сама формулировка на кнопку не выносится: в тексте сообщения она занимает
    две строки, а в кнопку не влезает и обрезается до неузнаваемости.

    `correcting` меняет глагол на кнопках (T204): под правкой нажатие ПРАВИТ
    запись, а «Записать» обещало бы вторую строку в отчёте — ровно то, от чего
    аудитор и уходит, отвечая на сообщение о записи.
    """
    single, numbered, skip = (
        ("btn.fix_single", "btn.fix_numbered", "btn.fix_skip")
        if correcting
        else ("btn.pick_single", "btn.pick_numbered", "btn.skip")
    )
    builder = InlineKeyboardBuilder()
    for index in range(count):
        builder.button(
            text=t(single, lang) if count == 1 else t(numbered, lang, index=index + 1),
            callback_data=f"{PICK_PREFIX}{index}",
        )
    builder.adjust(min(count, PICK_BUTTONS_PER_ROW) or 1)
    builder.row(InlineKeyboardButton(text=t("btn.manual", lang), callback_data=MANUAL_CALLBACK))
    builder.row(InlineKeyboardButton(text=t(skip, lang), callback_data=SKIP_CALLBACK))
    return builder.as_markup()


def manual_keyboard(
    titles: Sequence[tuple[int, str, str]], page: int, pages: int, lang: str
) -> InlineKeyboardMarkup:
    """Страница ручного перечня: тройки «место в перечне, код, формулировка»."""
    builder = InlineKeyboardBuilder()
    for index, code, title in titles:
        builder.button(
            text=f"{code} · {_short(title)}", callback_data=f"{MANUAL_PICK_PREFIX}{index}"
        )
    builder.adjust(1)
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                text=t("btn.back", lang), callback_data=f"{MANUAL_PAGE_PREFIX}{page - 1}"
            )
        )
    if page + 1 < pages:
        nav.append(
            InlineKeyboardButton(
                text=t("btn.more", lang), callback_data=f"{MANUAL_PAGE_PREFIX}{page + 1}"
            )
        )
    if nav:
        builder.row(*nav)
    builder.row(InlineKeyboardButton(text=t("btn.skip", lang), callback_data=SKIP_CALLBACK))
    return builder.as_markup()


def levels_keyboard(prefix: str, levels: Sequence[str]) -> InlineKeyboardMarkup:
    """Классы, разрешённые пункту. Перечень приходит из методики, не отсюда."""
    builder = InlineKeyboardBuilder()
    for level in levels:
        builder.button(text=level, callback_data=f"{prefix}{level}")
    builder.adjust(len(levels) or 1)
    return builder.as_markup()


def zones_keyboard(prefix: str, zones: Sequence[tuple[str, str]]) -> InlineKeyboardMarkup:
    """Зоны справочника: пары «код, название». Название переводится, код нет.

    **По одной в ряд (T140, #111).** Стояли по две, и на общем пределе подписи
    в 34 знака самая длинная зона боевой методики обрывалась хвостом
    «…курьерская з…». Аудитор жмёт эту кнопку на точке — одной рукой, в шуме, —
    и обрубленная подпись здесь стоит не «некрасиво», а выбранной не той зоны:
    зона входит и в запись, и в разбивку оценки.

    Свиток из десяти рядов вместо пяти — осознанная плата: выбирают зону раз на
    запись, читают подпись каждый раз. Ряд из одной кнопки поднимает предел
    вдвое (`ZONE_TITLE_LIMIT`), и на боевой методике не режется уже ни одно
    название ни на одном языке.

    Порядок зон здесь не трогается: его задаёт вызывающий, и он станет
    маршрутным, когда придут данные управляющей компании (T133).
    """
    builder = InlineKeyboardBuilder()
    for code, title in zones:
        builder.button(text=_short(title, ZONE_TITLE_LIMIT), callback_data=f"{prefix}{code}")
    builder.adjust(1)
    return builder.as_markup()


#: Правки записи: ключ надписи и код действия. Список один на две клавиатуры
#: (`edit_keyboard` и `fixed_keyboard`) намеренно — разойдись они, у записи,
#: сделанной без подтверждения, набор правок отличался бы от обычной, и это
#: заметил бы только аудитор на точке.
EDIT_BUTTONS: tuple[tuple[str, str], ...] = (
    ("btn.zone", EDIT_ZONE),
    ("btn.level", EDIT_LEVEL),
    ("btn.text", EDIT_TEXT),
    ("btn.drop", EDIT_DROP),
)


def edit_keyboard(n: int, lang: str) -> InlineKeyboardMarkup:
    """Правки записи прямо под подтверждением (T056).

    Четыре кнопки — ровно то, что просит задача: зона, класс, формулировка,
    удаление. Процент пересчитывается после любой из них.
    """
    builder = InlineKeyboardBuilder()
    for label, what in EDIT_BUTTONS:
        builder.button(text=t(label, lang), callback_data=f"{EDIT_PREFIX}{n}:{what}")
    builder.adjust(len(EDIT_BUTTONS))
    return builder.as_markup()


def fixed_keyboard(n: int, lang: str) -> InlineKeyboardMarkup:
    """Под записью, сделанной по словам сразу, без подтверждения (T121, D064).

    Те же четыре правки, что под подтверждённой записью, плюс пятая кнопка —
    «Разобрать моделью». Она обязательна, и не для симметрии с прошлой очередью.

    Правка меняет зону, класс и формулировку и удаляет запись, но НЕ код пункта
    (`routers/edit.py`). А сверка по словам промахивается: слова могут покрыть
    не ту строку карты, и тогда запись уйдёт в отчёт с неверным пунктом. Без
    выхода к модели починить такой промах было бы нечем: удаление и повтор тех
    же слов дают ровно тот же неверный пункт — петля.
    """
    builder = InlineKeyboardBuilder()
    for label, what in EDIT_BUTTONS:
        builder.button(text=t(label, lang), callback_data=f"{EDIT_PREFIX}{n}:{what}")
    builder.adjust(len(EDIT_BUTTONS))
    builder.row(InlineKeyboardButton(text=t("btn.model", lang), callback_data=MODEL_CALLBACK))
    return builder.as_markup()


def finish_keyboard(lang: str, *, can_edit: bool) -> InlineKeyboardMarkup:
    """Итог показан — что дальше: поправить, собрать отчёт, вернуться к проверке."""
    builder = InlineKeyboardBuilder()
    if can_edit:
        builder.button(text=t("btn.edit", lang), callback_data=FINISH_EDIT_CALLBACK)
    builder.button(text=t("btn.build", lang), callback_data=FINISH_BUILD_CALLBACK)
    builder.button(text=t("btn.resume", lang), callback_data=FINISH_RESUME_CALLBACK)
    builder.adjust(1)
    return builder.as_markup()


def version_mismatch_keyboard(lang: str) -> InlineKeyboardMarkup:
    """Расхождение версии методики: перевести проверку или разбираться с методикой (T167).

    Кнопки две, и ни одна не нажимается за аудитора: перевод меняет то, чем
    проверка будет измеряться, а возврат прежней версии делается вообще не в
    боте. Молчаливого третьего варианта — посчитать как-нибудь — нет.
    """
    builder = InlineKeyboardBuilder()
    builder.button(text=t("btn.version_sync", lang), callback_data=VERSION_SYNC_CALLBACK)
    builder.button(text=t("btn.version_keep", lang), callback_data=VERSION_KEEP_CALLBACK)
    builder.adjust(1)
    return builder.as_markup()


def pick_record_keyboard(numbers: Sequence[int]) -> InlineKeyboardMarkup:
    """Номера записей — выбрать, какую править (T058, «дать шанс поправить»)."""
    builder = InlineKeyboardBuilder()
    for n in numbers:
        builder.button(text=f"#{n}", callback_data=f"{FINISH_PICK_PREFIX}{n}")
    builder.adjust(5)
    return builder.as_markup()


def without_photos_keyboard(lang: str) -> InlineKeyboardMarkup:
    """Кадр потерян — собирать ли отчёт без него. Решает аудитор, не код."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text=t("btn.build_without_photos", lang), callback_data=FINISH_BUILD_NO_PHOTOS_CALLBACK
    )
    return builder.as_markup()


def info_keyboard(kind: str, lang: str) -> InlineKeyboardMarkup:
    """Кнопки под вопросом информационной части (T158).

    У поля «да/нет» кнопки и есть единственный удобный ответ, у остальных под
    вопросом стоит только пропуск: текст и дату аудитор присылает сообщением
    или голосом.

    Пропуск виден всегда и у каждого поля — это допущение D070: аудитор в конце
    обхода не должен упираться в семь обязательных вопросов. Рядом «дальше к
    отчёту»: она снимает все оставшиеся вопросы разом, потому что шесть нажатий
    «Пропустить» подряд — это тот же тупик, только длиннее.
    """
    builder = InlineKeyboardBuilder()
    if kind == KIND_YES_NO:
        builder.button(text=t("btn.yes", lang), callback_data=INFO_YES_CALLBACK)
        builder.button(text=t("btn.no", lang), callback_data=INFO_NO_CALLBACK)
    builder.button(text=t("btn.info_skip", lang), callback_data=INFO_SKIP_CALLBACK)
    builder.button(text=t("btn.info_done", lang), callback_data=INFO_DONE_CALLBACK)
    builder.adjust(2)
    return builder.as_markup()


def info_heard_keyboard(lang: str) -> InlineKeyboardMarkup:
    """Под показанной расшифровкой голоса: записать так или пропустить (D069).

    Расшифровка показывается ДО записи и правится — это не противоречит D064,
    снявшему подтверждение с текста: там человек написал сам, а здесь машина
    могла ослышаться. Поправить её можно, просто прислав текст: он и станет
    ответом вместо услышанного.
    """
    builder = InlineKeyboardBuilder()
    builder.button(text=t("btn.info_save", lang), callback_data=INFO_SAVE_CALLBACK)
    builder.button(text=t("btn.info_skip", lang), callback_data=INFO_SKIP_CALLBACK)
    builder.adjust(2)
    return builder.as_markup()
