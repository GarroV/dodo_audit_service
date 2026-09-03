"""Инлайн-клавиатуры мастера начала проверки (T050, T051, T052).

Callback-данные — код, а не формулировка (принцип проекта «сущности связываются
кодами, не текстом»): текст кнопки можно менять и переводить, `callback_data`
нет. Значение вида проверки, которое уходит в движок (`--type`), — свободный
текст (см. `engine/audit.py: cmd_init`, поле `type` там без ограничения на
перечень), поэтому код кнопки и текст, который увидит движок и в итоге отчёт,
разведены явной таблицей `KIND_LABELS`.
"""

from __future__ import annotations

from collections.abc import Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .texts import t

#: Код кнопки → формулировка вида проверки, которая уходит в `audit.py init --type`
#: и дальше в шапку отчёта (`docs/06-mvp-bot.md`, сценарий, шаг 1).
KIND_LABELS: dict[str, str] = {
    "planned": "Плановая",
    "repeat": "Повторная",
    "unscheduled": "Внеплановая",
}

#: Код кнопки языка отчёта = сам код языка методики (`ru`/`en`, `domain.models.TEXT_LANGS`).
#: Второй копии не нужно: то, что летит в `callback_data`, и есть значение параметра.
LANG_LABELS: dict[str, str] = {
    "ru": "Русский",
    "en": "English",
}

NEW_INSPECTION_CALLBACK = "start:new"
KIND_PREFIX = "start:kind:"
LANG_PREFIX = "start:lang:"
RESUME_CONTINUE_CALLBACK = "start:resume:continue"
RESUME_NEW_CALLBACK = "start:resume:new"


def new_inspection_keyboard() -> InlineKeyboardMarkup:
    """Единственная кнопка входа — «Новая проверка»."""
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(text="Новая проверка", callback_data=NEW_INSPECTION_CALLBACK))
    return builder.as_markup()


def kind_keyboard() -> InlineKeyboardMarkup:
    """Вид проверки — по одной кнопке в ряд, порядок как в `docs/06-mvp-bot.md`."""
    builder = InlineKeyboardBuilder()
    for code, label in KIND_LABELS.items():
        builder.button(text=label, callback_data=f"{KIND_PREFIX}{code}")
    builder.adjust(1)
    return builder.as_markup()


def lang_keyboard() -> InlineKeyboardMarkup:
    """Язык отчёта — коды методики (`domain.models.TEXT_LANGS`), не что-либо ещё."""
    builder = InlineKeyboardBuilder()
    for code, label in LANG_LABELS.items():
        builder.button(text=label, callback_data=f"{LANG_PREFIX}{code}")
    builder.adjust(1)
    return builder.as_markup()


def resume_keyboard() -> InlineKeyboardMarkup:
    """Незавершённая проверка найдена — предложить «Продолжить» или «Начать новую» (T052)."""
    builder = InlineKeyboardBuilder()
    builder.button(text="Продолжить", callback_data=RESUME_CONTINUE_CALLBACK)
    builder.button(text="Начать новую", callback_data=RESUME_NEW_CALLBACK)
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
#: Пункт, найденный без модели по словам аудитора (T117, D063), и выход к
#: модели рядом с ним. Место в списке в коде кнопки не едет: у быстрого пути
#: вариант ровно один — он лежит в предложении чата.
FAST_CALLBACK = "rec:fast"
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

#: Сколько пунктов показывать на странице ручного перечня. Больше десятка
#: кнопок на телефоне превращаются в свиток, а перечень зоны — это 70+ пунктов.
MANUAL_PAGE_SIZE = 8

#: Сколько знаков формулировки пункта влезает в кнопку, не разъезжаясь на
#: телефоне в три строки.
BUTTON_TITLE_LIMIT = 34


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


def candidates_keyboard(count: int, lang: str) -> InlineKeyboardMarkup:
    """Кандидаты номерами плюс выход на ручной перечень и отказ.

    Номер, а не формулировка: в `callback_data` едет место в показанном списке,
    и переводить его не надо. Сами формулировки — в тексте сообщения, кнопке
    такой длины не влезть.
    """
    builder = InlineKeyboardBuilder()
    for index in range(count):
        builder.button(text=str(index + 1), callback_data=f"{PICK_PREFIX}{index}")
    builder.adjust(min(count, 5) or 1)
    builder.row(InlineKeyboardButton(text=t("btn.manual", lang), callback_data=MANUAL_CALLBACK))
    builder.row(InlineKeyboardButton(text=t("btn.skip", lang), callback_data=SKIP_CALLBACK))
    return builder.as_markup()


def fast_keyboard(code: str, title: str, lang: str) -> InlineKeyboardMarkup:
    """Пункт, найденный без модели: записать, разобрать моделью, не записывать (T117).

    Вторая кнопка обязательна, и не для симметрии. Быстрый путь отвечает по
    одной сработавшей строке карты, а в одной фразе аудитора бывает два
    нарушения (правило 11 `docs/03-recording-rules.md`): «Печь в нагаре, пол
    грязный» покроет строку «Печь» и покажет один пункт из двух. Без выхода к
    модели аудитор с неполным ответом зажат в угол.

    На первой кнопке стоит вопрос чек-листа, как в ручном перечне: быстрый путь
    формулировок не сочиняет, и предлагать ему нечего, кроме самого пункта.
    """
    builder = InlineKeyboardBuilder()
    builder.button(text=f"{code} · {_short(title)}", callback_data=FAST_CALLBACK)
    builder.button(text=t("btn.model", lang), callback_data=MODEL_CALLBACK)
    builder.button(text=t("btn.skip", lang), callback_data=SKIP_CALLBACK)
    builder.adjust(1)
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
    """Зоны справочника: пары «код, название». Название переводится, код нет."""
    builder = InlineKeyboardBuilder()
    for code, title in zones:
        builder.button(text=_short(title), callback_data=f"{prefix}{code}")
    builder.adjust(2)
    return builder.as_markup()


def edit_keyboard(n: int, lang: str) -> InlineKeyboardMarkup:
    """Правки записи прямо под подтверждением (T056).

    Четыре кнопки — ровно то, что просит задача: зона, класс, формулировка,
    удаление. Процент пересчитывается после любой из них.
    """
    builder = InlineKeyboardBuilder()
    for label, what in (
        ("btn.zone", EDIT_ZONE),
        ("btn.level", EDIT_LEVEL),
        ("btn.text", EDIT_TEXT),
        ("btn.drop", EDIT_DROP),
    ):
        builder.button(text=t(label, lang), callback_data=f"{EDIT_PREFIX}{n}:{what}")
    builder.adjust(4)
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
