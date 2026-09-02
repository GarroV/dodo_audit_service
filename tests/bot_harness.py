"""Оснастка тестов бота: настоящие события через настоящий диспетчер.

Хендлеры не вызываются напрямую — так проверялась бы не работа бота, а вызов
функции: мимо остались бы фильтры, порядок роутеров, мидлварь доступа и
конечный автомат, то есть ровно то, где живут ошибки диалога. Поэтому тесты
собирают `Update`, скармливают его `Dispatcher.feed_update` и смотрят, что бот
попытался отправить.

Сеть заменена `RecordingSession`: она складывает вызовы API в список и
возвращает правдоподобные ответы. Токен выдуманный — до Telegram ни один тест
не доходит.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from itertools import count
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.methods import TelegramMethod
from aiogram.methods.base import TelegramType
from aiogram.types import (
    CallbackQuery,
    Chat,
    Message,
    PhotoSize,
    Update,
    User,
    Voice,
)

FAKE_TOKEN = "111111:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
AUDITOR_ID = 4242
STRANGER_ID = 9999
CHAT_ID = 4242


class RecordingSession(BaseSession):
    """Сессия без сети: запоминает вызовы API и отвечает правдоподобно."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[TelegramMethod[Any]] = []
        self._ids = count(9000)

    async def close(self) -> None:
        return None

    async def make_request(
        self,
        bot: Bot,
        method: TelegramMethod[TelegramType],
        # ASYNC109: имя и вид параметра задан абстрактным классом aiogram,
        # менять его нельзя — оснастка обязана подходить под BaseSession.
        timeout: int | None = None,  # noqa: ASYNC109
    ) -> Any:
        self.calls.append(method)
        name = type(method).__name__
        if name in {"SendMessage", "SendPhoto", "SendDocument", "EditMessageText"}:
            return Message(
                message_id=next(self._ids),
                date=datetime.now(tz=timezone.utc),
                chat=Chat(id=getattr(method, "chat_id", CHAT_ID), type="private"),
                text=getattr(method, "text", None),
            ).as_(bot)
        return True

    async def stream_content(
        self,
        url: str,
        headers: dict[str, Any] | None = None,
        timeout: int = 30,  # noqa: ASYNC109 — сигнатура из BaseSession
        chunk_size: int = 65536,
        raise_for_status: bool = True,
    ) -> AsyncGenerator[bytes, None]:
        yield b""

    # --- то, ради чего оснастка и нужна ---

    @property
    def texts(self) -> list[str]:
        """Тексты всех отправленных сообщений — по ним и проверяется диалог."""
        return [
            str(getattr(c, "text", "")) for c in self.calls if type(c).__name__ == "SendMessage"
        ]

    @property
    def last_text(self) -> str:
        assert self.texts, "бот не отправил ни одного сообщения"
        return self.texts[-1]

    def keyboard_data(self) -> list[str]:
        """`callback_data` всех кнопок последнего сообщения с клавиатурой."""
        for call in reversed(self.calls):
            markup = getattr(call, "reply_markup", None)
            buttons = getattr(markup, "inline_keyboard", None)
            if buttons:
                return [b.callback_data or "" for row in buttons for b in row]
        return []

    def clear(self) -> None:
        self.calls.clear()


def make_bot() -> tuple[Bot, RecordingSession]:
    session = RecordingSession()
    return Bot(token=FAKE_TOKEN, session=session), session


_message_ids = count(100)


def next_message_id() -> int:
    return next(_message_ids)


def _user(user_id: int, full_name: str) -> User:
    first, _, last = full_name.partition(" ")
    return User(id=user_id, is_bot=False, first_name=first, last_name=last or None)


def text_message(
    text: str,
    *,
    user_id: int = AUDITOR_ID,
    chat_id: int = CHAT_ID,
    full_name: str = "Владимир Гарро",
    reply_to: Message | None = None,
    message_id: int | None = None,
) -> Message:
    return Message(
        message_id=next_message_id() if message_id is None else message_id,
        date=datetime.now(tz=timezone.utc),
        chat=Chat(id=chat_id, type="private"),
        from_user=_user(user_id, full_name),
        text=text,
        reply_to_message=reply_to,
    )


def photo_message(
    file_id: str,
    *,
    caption: str | None = None,
    media_group_id: str | None = None,
    user_id: int = AUDITOR_ID,
    chat_id: int = CHAT_ID,
    message_id: int | None = None,
) -> Message:
    """Кадр так, как его присылает Telegram: несколько размеров, крупный последним."""
    sizes = [
        PhotoSize(file_id=f"{file_id}-small", file_unique_id=f"{file_id}-s", width=90, height=60),
        PhotoSize(file_id=file_id, file_unique_id=f"{file_id}-l", width=1280, height=960),
    ]
    return Message(
        message_id=next_message_id() if message_id is None else message_id,
        date=datetime.now(tz=timezone.utc),
        chat=Chat(id=chat_id, type="private"),
        from_user=_user(user_id, "Владимир Гарро"),
        photo=sizes,
        caption=caption,
        media_group_id=media_group_id,
    )


def voice_message(
    file_id: str,
    *,
    user_id: int = AUDITOR_ID,
    chat_id: int = CHAT_ID,
    reply_to: Message | None = None,
) -> Message:
    return Message(
        message_id=next_message_id(),
        date=datetime.now(tz=timezone.utc),
        chat=Chat(id=chat_id, type="private"),
        from_user=_user(user_id, "Владимир Гарро"),
        voice=Voice(file_id=file_id, file_unique_id=f"{file_id}-u", duration=3),
        reply_to_message=reply_to,
    )


def callback_query(
    data: str,
    *,
    user_id: int = AUDITOR_ID,
    chat_id: int = CHAT_ID,
    full_name: str = "Владимир Гарро",
) -> CallbackQuery:
    return CallbackQuery(
        id=f"cb-{next_message_id()}",
        from_user=_user(user_id, full_name),
        chat_instance="chat-instance",
        data=data,
        message=Message(
            message_id=next_message_id(),
            date=datetime.now(tz=timezone.utc),
            chat=Chat(id=chat_id, type="private"),
        ),
    )


_update_ids = count(1)


async def feed(dp: Dispatcher, bot: Bot, event: Message | CallbackQuery) -> None:
    """Отдать событие диспетчеру так же, как это делает long polling."""
    update_id = next(_update_ids)
    if isinstance(event, Message):
        await dp.feed_update(bot, Update(update_id=update_id, message=event))
    else:
        await dp.feed_update(bot, Update(update_id=update_id, callback_query=event))
