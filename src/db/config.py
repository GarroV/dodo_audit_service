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
from .storage import StorageSettings

DATABASE_URL_VAR = "DATABASE_URL"

#: Подключение АДМИНИСТРАТОРА ИСТОРИИ — роли `dodo_audit_admin` (миграция
#: `0010`). Единственной роли, которой видны снятые проверки и которой
#: позволено снимать: обычная роль приложения снятой проверки не видит вовсе,
#: и держит это построчная политика, а не фильтр в запросе.
#:
#: Не путать с `DATABASE_ADMIN_URL` (`src/db/migrate.py`): та про НАКАТ СХЕМЫ и
#: обычно привилегированная, а привилегированная роль обходит политики всегда —
#: подставленная сюда, она отдала бы «администраторский» ответ кому угодно.
DATABASE_RETRACTION_URL_VAR = "DATABASE_RETRACTION_URL"

# Хранилище кадров. Имена переменных S3-совместимые, а не «супабейзовые»:
# поставщик обязан меняться правкой значений, а не имён (D004, D054, D061).
S3_BUCKET_VAR = "S3_BUCKET"
S3_ENDPOINT_URL_VAR = "S3_ENDPOINT_URL"
S3_ACCESS_KEY_ID_VAR = "S3_ACCESS_KEY_ID"
# Подавление ниже — потому что это ИМЯ переменной окружения, а не значение
# секрета: сам ключ живёт только в .env и в код не попадает (конституция).
S3_SECRET_ACCESS_KEY_VAR = "S3_SECRET_ACCESS_KEY"  # noqa: S105
S3_REGION_VAR = "S3_REGION"

#: Регион по умолчанию. У S3-совместимых серверов он формальность, но
#: подпись запроса без него не собирается — поэтому значение есть всегда.
DEFAULT_S3_REGION = "us-east-1"


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


def load_retraction_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Прочитать подключение администратора истории. Отказ — `ConfigError`.

    Отдельная переменная, а не роль, выбранная по ходу: право видеть снятые
    проверки живёт в базе (миграция `0010`), а не в коде, и получить его можно
    только придя под другой ролью. Подставить сюда `DATABASE_URL` нельзя —
    роль приложения снятого не увидит и вернёт пустоту вместо отказа, то есть
    «таких проверок нет» вместо «вам их не видно».
    """
    src = os.environ if env is None else env
    dsn = (src.get(DATABASE_RETRACTION_URL_VAR) or "").strip()
    if not dsn:
        raise ConfigError(
            f"Не задана переменная окружения {DATABASE_RETRACTION_URL_VAR}. Снятые "
            f"проверки видны только администратору истории (роль dodo_audit_admin), "
            f"и приходит он отдельным подключением — пример значения в .env.example"
        )
    return Settings(dsn=dsn)


def load_storage_settings(env: Mapping[str, str] | None = None) -> StorageSettings:
    """Прочитать доступ к хранилищу кадров из окружения.

    Пустой `S3_ENDPOINT_URL` — это настоящий AWS S3, и только он: подставлять
    сюда адрес по умолчанию нельзя, иначе кадры проверок партнёров однажды
    уедут не туда, куда думал запускающий, и молча.

    Отсутствие корзины или ключей — отказ на месте. Выгрузка кадров случается
    один раз в конце проверки, и узнавать о незаполненном доступе в этот
    момент — значит узнавать поздно.
    """
    src = os.environ if env is None else env
    missing = [
        name
        for name in (S3_BUCKET_VAR, S3_ACCESS_KEY_ID_VAR, S3_SECRET_ACCESS_KEY_VAR)
        if not (src.get(name) or "").strip()
    ]
    if missing:
        raise ConfigError(
            f"Не заданы переменные окружения хранилища кадров: {', '.join(missing)}. "
            f"Без них кадр останется идентификатором телеграма, который снаружи "
            f"не открывается — имена и назначение в .env.example"
        )
    return StorageSettings(
        bucket=(src[S3_BUCKET_VAR]).strip(),
        access_key_id=(src[S3_ACCESS_KEY_ID_VAR]).strip(),
        secret_access_key=(src[S3_SECRET_ACCESS_KEY_VAR]).strip(),
        endpoint_url=(src.get(S3_ENDPOINT_URL_VAR) or "").strip() or None,
        region=(src.get(S3_REGION_VAR) or "").strip() or DEFAULT_S3_REGION,
    )
