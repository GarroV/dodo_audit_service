"""Клавиатуры мастера начала проверки: коды кнопок, а не формулировки."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.bot.keyboards import (
    KIND_PREFIX,
    LANG_PREFIX,
    NEW_INSPECTION_CALLBACK,
    RESUME_CONTINUE_CALLBACK,
    RESUME_NEW_CALLBACK,
    kind_keyboard,
    lang_keyboard,
    new_inspection_keyboard,
    resume_keyboard,
)


def _flat_buttons(markup: InlineKeyboardMarkup) -> list[InlineKeyboardButton]:
    return [button for row in markup.inline_keyboard for button in row]


def test_new_inspection_keyboard_has_single_button() -> None:
    buttons = _flat_buttons(new_inspection_keyboard())
    assert len(buttons) == 1
    assert buttons[0].callback_data == NEW_INSPECTION_CALLBACK
    assert buttons[0].text == "Новая проверка"


def test_kind_keyboard_has_three_buttons_with_coded_callback() -> None:
    buttons = _flat_buttons(kind_keyboard())
    assert len(buttons) == 3
    for button in buttons:
        assert button.callback_data is not None
        assert button.callback_data.startswith(KIND_PREFIX)
    texts = {button.text for button in buttons}
    assert texts == {"Плановая", "Повторная", "Внеплановая"}


def test_lang_keyboard_callback_codes_match_report_lang_values() -> None:
    """Код кнопки языка — это и есть значение report_lang (`ru`/`en`), без второй таблицы."""
    buttons = _flat_buttons(lang_keyboard())
    codes = {button.callback_data.removeprefix(LANG_PREFIX) for button in buttons}
    assert codes == {"ru", "en"}


def test_resume_keyboard_has_continue_and_new_buttons() -> None:
    buttons = _flat_buttons(resume_keyboard())
    callbacks = {button.callback_data for button in buttons}
    assert callbacks == {RESUME_CONTINUE_CALLBACK, RESUME_NEW_CALLBACK}
