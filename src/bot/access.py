"""Доступ по списку разрешённых Telegram ID (задача T050).

Незнакомому отправителю бот не отвечает ничего осмысленного (принцип
безопасности проекта, `docs/forge/constitution.md`): апдейт от него до хендлера
не доходит вовсе, а не получает отдельное сообщение об отказе — иначе бот сам
подтверждает постороннему, что он существует и что этот ID неверный.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

logger = logging.getLogger(__name__)

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


def is_allowed(user_id: int | None, allowed_ids: frozenset[int]) -> bool:
    """Разрешён ли этот Telegram ID. Отсутствие ID (служебное обновление) — нет."""
    return user_id is not None and user_id in allowed_ids


class AccessMiddleware(BaseMiddleware):
    """Внешняя мидлварь на `message` и `callback_query`: чужого не пускает дальше."""

    def __init__(self, allowed_ids: frozenset[int]) -> None:
        self._allowed_ids = allowed_ids

    async def __call__(
        self,
        handler: Handler,
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = getattr(event, "from_user", None)
        user_id = user.id if user is not None else None
        if not is_allowed(user_id, self._allowed_ids):
            logger.warning("отклонено обновление от постороннего Telegram ID %s", user_id)
            return None
        return await handler(event, data)
