"""Доступ по списку разрешённых Telegram ID.

`docs/forge/blocks/bot.md`: «Отвечает только Telegram ID из списка разрешённых;
чужому не отвечает ничего осмысленного» (T050).
"""

from __future__ import annotations

from typing import Any

import pytest
from aiogram.types import Chat, Message, TelegramObject, Update, User

from src.bot.access import AccessMiddleware, is_allowed


def _user(user_id: int) -> User:
    return User(id=user_id, is_bot=False, first_name="Т")


def test_allowed_id_passes() -> None:
    assert is_allowed(111, frozenset({111, 222})) is True


def test_unknown_id_is_rejected() -> None:
    assert is_allowed(999, frozenset({111, 222})) is False


def test_no_user_is_rejected() -> None:
    """Обновление без отправителя (например, служебное) — не пропускаем."""
    assert is_allowed(None, frozenset({111})) is False


@pytest.mark.asyncio
async def test_middleware_calls_handler_for_allowed_user() -> None:
    middleware = AccessMiddleware(allowed_ids=frozenset({111}))
    called_with: list[TelegramObject] = []

    async def handler(event: TelegramObject, data: dict[str, Any]) -> str:
        called_with.append(event)
        return "handled"

    message = Message(
        message_id=1,
        date=0,  # type: ignore[arg-type]
        chat=Chat(id=111, type="private"),
        from_user=_user(111),
        text="привет",
    )
    result = await middleware(handler, message, {})
    assert result == "handled"
    assert called_with == [message]


@pytest.mark.asyncio
async def test_middleware_drops_update_for_stranger() -> None:
    """Чужому бот не должен отвечать ничего осмысленного: хендлер не зовётся."""
    middleware = AccessMiddleware(allowed_ids=frozenset({111}))
    calls = 0

    async def handler(event: TelegramObject, data: dict[str, Any]) -> str:
        nonlocal calls
        calls += 1
        return "handled"

    message = Message(
        message_id=1,
        date=0,  # type: ignore[arg-type]
        chat=Chat(id=999, type="private"),
        from_user=_user(999),
        text="привет",
    )
    result = await middleware(handler, message, {})
    assert result is None
    assert calls == 0


@pytest.mark.asyncio
async def test_middleware_ignores_non_user_events() -> None:
    """Событие без `from_user` (например, канал) — тоже не зовёт хендлер и не падает."""
    middleware = AccessMiddleware(allowed_ids=frozenset({111}))

    async def handler(event: TelegramObject, data: dict[str, Any]) -> str:
        return "handled"

    update = Update(update_id=1)
    result = await middleware(handler, update, {})
    assert result is None
