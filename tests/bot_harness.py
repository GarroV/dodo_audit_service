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
    File,
    Message,
    PhotoSize,
    Update,
    User,
    Voice,
)

from src.recognize.manual import ManualCandidate
from src.recognize.models import Candidate, Suggestion

FAKE_TOKEN = "111111:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
AUDITOR_ID = 4242
STRANGER_ID = 9999
CHAT_ID = 4242


class RecordingSession(BaseSession):
    """Сессия без сети: запоминает вызовы API и отвечает правдоподобно."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[TelegramMethod[Any]] = []
        #: Номера сообщений, которые «отправил» бот, — в порядке отправки.
        #: Нужны там, где аудитор отвечает НА СООБЩЕНИЕ БОТА (T204): без них
        #: тест не может сослаться на ту самую запись, а адресность правки и
        #: есть весь смысл задачи.
        self.sent_ids: list[int] = []
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
        if name == "GetFile":
            # Скачивание кадра — часть настоящего потока разбора: без ответа на
            # GetFile каждый тест разбора получал бы «файл не скачался».
            file_id = str(getattr(method, "file_id", "stub"))
            return File(file_id=file_id, file_unique_id=f"{file_id}-u", file_path="stub/photo.jpg")
        if name in {"SendMessage", "SendPhoto", "SendDocument", "EditMessageText"}:
            message_id = next(self._ids)
            self.sent_ids.append(message_id)
            return Message(
                message_id=message_id,
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

    def keyboard_texts(self) -> list[str]:
        """Надписи всех кнопок последнего сообщения с клавиатурой.

        Нужны там, где проверяется язык (T131): в `callback_data` едет код, и
        языка он не знает намеренно, — а русская надпись под английским
        вопросом ломает демо ровно так же, как русский текст, только незаметнее.
        """
        for call in reversed(self.calls):
            markup = getattr(call, "reply_markup", None)
            buttons = getattr(markup, "inline_keyboard", None)
            if buttons:
                return [b.text for row in buttons for b in row]
        return []

    @property
    def documents(self) -> list[Any]:
        """Отправленные файлы — по ним проверяется, что отчёт дошёл до аудитора."""
        return [c for c in self.calls if type(c).__name__ == "SendDocument"]

    @property
    def last_sent_id(self) -> int:
        assert self.sent_ids, "бот не отправил ни одного сообщения"
        return self.sent_ids[-1]

    def clear(self) -> None:
        """Забыть отправленное. Номера сообщений НЕ забываются намеренно:
        ответ аудитора приходит на сообщение, отправленное до очистки."""
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


def bot_message(message_id: int, *, chat_id: int = CHAT_ID) -> Message:
    """Сообщение БОТА — то, на которое отвечает аудитор (T204).

    Телеграм в ответе присылает исходное сообщение целиком; боту из него нужен
    только номер, по нему и находится запись. Текст здесь не подставляется
    нарочно: полагаться на него — значит связывать сущности формулировкой.
    """
    return Message(
        message_id=message_id,
        date=datetime.now(tz=timezone.utc),
        chat=Chat(id=chat_id, type="private"),
        from_user=User(id=1, is_bot=True, first_name="bot"),
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
    with_message: bool = True,
) -> CallbackQuery:
    """Нажатие на кнопку.

    `with_message=False` — настоящий случай телеграма, а не выдумка теста: у
    нажатия нет сообщения, если оно старше 48 часов или пришло из инлайн-режима.
    Бот тогда не знает, в какой чат отвечать, и обязан промолчать, а не упасть.
    """
    return CallbackQuery(
        id=f"cb-{next_message_id()}",
        from_user=_user(user_id, full_name),
        chat_instance="chat-instance",
        data=data,
        message=Message(
            message_id=next_message_id(),
            date=datetime.now(tz=timezone.utc),
            chat=Chat(id=chat_id, type="private"),
        )
        if with_message
        else None,
    )


_update_ids = count(1)


async def feed(dp: Dispatcher, bot: Bot, event: Message | CallbackQuery) -> None:
    """Отдать событие диспетчеру так же, как это делает long polling."""
    update_id = next(_update_ids)
    if isinstance(event, Message):
        await dp.feed_update(bot, Update(update_id=update_id, message=event))
    else:
        await dp.feed_update(bot, Update(update_id=update_id, callback_query=event))


async def build_report(dp: Dispatcher, bot: Bot) -> None:
    """Собрать отчёт так, как это делает аудитор.

    Между кнопкой «Собрать отчёт» и документом с T158 стоит информационная
    часть (D069): нажатие открывает вопросы, а сборка идёт за последним из них.
    «Дальше к отчёту» проходит их насквозь — тестам, которые проверяют сам
    отчёт, отвечать на них нечего.
    """
    await feed(dp, bot, callback_query("fin:build"))
    await feed(dp, bot, callback_query("info:done"))


# --- подмена разбора: до модели тесты не ходят ---


def candidate(
    code: str,
    level: str,
    zone: str,
    wording: str = "формулировка",
    *,
    confidence: float = 0.9,
    flags: tuple[str, ...] = (),
) -> Candidate:
    return Candidate(
        code=code, level=level, zone=zone, wording=wording, confidence=confidence, flags=flags
    )


def suggestion(*candidates: Candidate, question: str = "", used_photo: bool = False) -> Suggestion:
    return Suggestion(
        candidates=candidates,
        needs_human=not candidates,
        question=question,
        used_photo=used_photo,
    )


def manual(code: str, levels: tuple[str, ...], title: str = "пункт") -> ManualCandidate:
    return ManualCandidate(code=code, levels=levels, title=title)


class Calls(list[tuple[Any, ...]]):
    """Список вызовов подменённой функции — чтобы проверить, звали ли её вообще."""


def stub_classify(monkeypatch: Any, result: Suggestion | Exception) -> Calls:
    """Подменить разбор. Возвращает список вызовов: пустой — модель не звали.

    Пустой список и есть проверка требования D046: без нажатия «Разобрать» ни
    один кадр в модель не уходит.
    """
    calls = Calls()

    def fake(note: str, photo: object = None, zone_hint: object = None, **kw: object) -> Suggestion:
        calls.append((note, photo, zone_hint, kw.get("lang")))
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr("src.bot.routers.record.classify", fake)
    return calls


def stub_transcribe(monkeypatch: Any, result: str | Exception) -> Calls:
    """Подменить расшифровку голоса — в обоих местах, где бот её зовёт.

    Голосовое приходит и комментарием к кадру (`routers/record.py`), и ответом
    в информационной части (`routers/info.py`, T158). Подмена одна на оба: это
    одно и то же действие продукта, и тест, забывший подменить второе место,
    ушёл бы в сеть — то есть провалился бы не там, где причина.
    """
    calls = Calls()

    def fake(audio: bytes, **kw: object) -> str:
        calls.append((audio,))
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr("src.bot.routers.record.transcribe", fake)
    monkeypatch.setattr("src.bot.routers.info.transcribe", fake)
    return calls


def stub_manual(monkeypatch: Any, items: tuple[ManualCandidate, ...]) -> Calls:
    calls = Calls()

    def fake(zone_hint: object, **kw: object) -> tuple[ManualCandidate, ...]:
        calls.append((zone_hint,))
        return items

    monkeypatch.setattr("src.bot.routers.record.manual_candidates", fake)
    return calls
