"""Окружение блока: где Postgres.

Площадка — деталь реализации (конституция, принцип 7): переезд с локального
Postgres на Supabase обязан стоить смену одной переменной, а не правку кода.
Поэтому строка подключения читается из `DATABASE_URL` и нигде не зашивается;
значение по умолчанию не подставляется намеренно — тихая работа против
случайной базы хуже явного отказа на старте.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from .errors import ConfigError

DATABASE_URL_VAR = "DATABASE_URL"


@dataclass(frozen=True)
class Settings:
    """Разобранное окружение блока."""

    dsn: str


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Прочитать `DATABASE_URL` из окружения. Проверки связи здесь нет."""
    src = os.environ if env is None else env
    dsn = (src.get(DATABASE_URL_VAR) or "").strip()
    if not dsn:
        raise ConfigError(
            f"Не задана переменная окружения {DATABASE_URL_VAR}. Без неё непонятно, "
            f"куда сливать завершённые проверки — пример значения в .env.example"
        )
    return Settings(dsn=dsn)


def check_environment(env: Mapping[str, str] | None = None) -> Settings:
    """Прочитать и вернуть окружение. Отказ — `ConfigError`.

    Саму связь с базой не проверяет: это сделает первый же вызов psycopg —
    дублировать здесь ping означало бы платить за два похода к сети вместо
    одного и всё равно ловить ту же ошибку подключения на реальном вызове.
    """
    return load_settings(env)
