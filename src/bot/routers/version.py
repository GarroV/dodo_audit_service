"""Команда «какая это сборка» (T246, #201).

Отдельный роутер, а не строка в приветствии: вопрос задают не в начале работы,
а когда что-то пошло не так, — и тогда нужен вход, который можно назвать по
имени, не завершая и не начиная проверку.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from ..texts import default_ui_lang, t
from ..version import build_version

#: Имя команды: одно место на роутер, меню и тесты.
VERSION_COMMAND = "version"


def build_version_router() -> Router:
    """Роутер ответа о версии сборки: одна команда и ничего больше."""
    router = Router(name="version")

    @router.message(Command(VERSION_COMMAND))
    async def on_version(message: Message) -> None:
        # Язык берётся у СТЕНДА, а не у начатой проверки: чтение состояния
        # проверки тянет за собой методику, и на ненастроенном окружении
        # команда падала бы ровно в тот момент, ради которого нужна —
        # когда с продуктом что-то не так (T246).
        await message.answer(t("version.answer", default_ui_lang(), v=build_version()))

    return router
