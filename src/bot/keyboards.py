"""Инлайн-клавиатуры мастера начала проверки (T050, T051, T052).

Callback-данные — код, а не формулировка (принцип проекта «сущности связываются
кодами, не текстом»): текст кнопки можно менять и переводить, `callback_data`
нет. Значение вида проверки, которое уходит в движок (`--type`), — свободный
текст (см. `engine/audit.py: cmd_init`, поле `type` там без ограничения на
перечень), поэтому код кнопки и текст, который увидит движок и в итоге отчёт,
разведены явной таблицей `KIND_LABELS`.
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

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
