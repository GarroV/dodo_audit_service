"""T092: справочник точек и карта синонимов.

Задача не про ввод: название пиццерии в первой версии по-прежнему приходит
текстом (D051), и бот здесь не меняется. Задача про то, ЧЕМ введённая строка
становится. Раньше «БГ2» и «Белград 2» заводили две несвязанные точки с двумя
историями — а история точки и есть то, ради чего проверки складываются в базу
(D035). Теперь синоним приводит к той же точке по её идентификатору.

Связь идёт кодами, не формулировками (конституция, принцип 5): проверка
ссылается на `units.id`, а написание живёт отдельной строкой и на связь не
влияет — синоним можно переименовать, добавить и убрать, ничего не сломав.

Ключ сопоставления один на всю карту — `normalize_unit_name` из `units.py`.
Второе правило нормализации здесь было бы худшим из возможных дефектов:
справочник и слив расходились бы молча и только на части написаний.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import psycopg

from .config import check_environment
from .errors import PushError
from .units import normalize_unit_name

#: Арендатор по умолчанию — то же значение, что у `push.DEFAULT_TENANT` и у
#: `domain.state.DEFAULT_TENANT`. Импортировать чужую внутреннюю константу ради
#: одной строки дороже, чем закрепить значение тестом (так же сделано в push).
DEFAULT_TENANT = "default"


@dataclass(frozen=True)
class Unit:
    """Точка справочника: идентификатор, каноничное название, синонимы."""

    id: str
    name: str
    code: str | None
    aliases: tuple[str, ...]


_SELECT_UNITS_SQL = """
select u.id, u.name, u.code, coalesce(array_agg(a.alias order by a.alias)
       filter (where a.alias is not null), '{}')
from units u
left join unit_aliases a on a.unit_id = u.id
where u.tenant_code = %s
group by u.id, u.name, u.code
order by u.name
"""

# Порядок ветвей задан ЯВНО колонкой приоритета, а не тем, что каноничное
# название написано в запросе первым: `union all` порядок строк не обещает, и
# планировщик волен отдать сперва ветку синонимов. Проверено на себе — тест
# «каноничное название сильнее синонима» на одном и том же коде то проходил,
# то падал, пока приоритет не стал явным.
_RESOLVE_SQL = """
select id, name, code from (
    select u.id, u.name, u.code, 0 as priority
    from units u
    where u.tenant_code = %(tenant)s and u.name_normalized = %(key)s
    union all
    select u.id, u.name, u.code, 1 as priority
    from unit_aliases a
    join units u on u.id = a.unit_id
    where a.tenant_code = %(tenant)s and a.alias_normalized = %(key)s
) candidates
order by priority
limit 1
"""

_INSERT_TENANT_SQL = "insert into tenants (code) values (%s) on conflict (code) do nothing"

_UPSERT_UNIT_BY_CODE_SQL = """
insert into units (tenant_code, name, name_normalized, code)
values (%(tenant)s, %(name)s, %(key)s, %(code)s)
on conflict (tenant_code, code) do update set name = excluded.name,
    name_normalized = excluded.name_normalized
returning id
"""

_UPSERT_UNIT_BY_NAME_SQL = """
insert into units (tenant_code, name, name_normalized, code)
values (%(tenant)s, %(name)s, %(key)s, %(code)s)
on conflict (tenant_code, name_normalized) do update set name = excluded.name,
    code = coalesce(excluded.code, units.code)
returning id
"""

_UPSERT_ALIAS_SQL = """
insert into unit_aliases (tenant_code, alias_normalized, unit_id, alias)
values (%s, %s, %s, %s)
on conflict (tenant_code, alias_normalized) do update
    set unit_id = excluded.unit_id, alias = excluded.alias
returning unit_id
"""


def _row_to_unit(row: tuple[Any, ...], aliases: tuple[str, ...] = ()) -> Unit:
    return Unit(id=str(row[0]), name=str(row[1]), code=row[2], aliases=aliases)


def resolve_unit_id(
    conn: psycopg.Connection[Any], name: str, *, tenant: str = DEFAULT_TENANT
) -> str | None:
    """Идентификатор точки по любому её написанию. Не нашлось — `None`.

    Сначала каноничное название, потом карта синонимов: если строка совпала с
    названием точки, спрашивать карту незачем, а обратный порядок дал бы
    синониму право перекрыть настоящее название чужой точки.

    Работает на переданном подключении: вызывается изнутри транзакции слива,
    и своё подключение здесь означало бы решение о точке, принятое вне той
    транзакции, которая её же и пишет.
    """
    key = normalize_unit_name(name)
    if not key:
        return None
    with conn.cursor() as cur:
        cur.execute(_RESOLVE_SQL, {"tenant": tenant, "key": key})
        row = cur.fetchone()
    return None if row is None else str(row[0])


def resolve_unit(name: str, *, tenant: str = DEFAULT_TENANT) -> Unit | None:
    """То же, но со своим подключением и полной карточкой точки."""
    settings = check_environment()
    key = normalize_unit_name(name)
    if not key:
        return None
    try:
        with psycopg.connect(settings.dsn) as conn, conn.cursor() as cur:
            cur.execute(_RESOLVE_SQL, {"tenant": tenant, "key": key})
            row = cur.fetchone()
            if row is None:
                return None
            cur.execute(
                "select alias from unit_aliases where unit_id = %s order by alias", (row[0],)
            )
            aliases = tuple(str(a[0]) for a in cur.fetchall())
    except psycopg.Error as exc:
        raise PushError(f"Справочник точек недоступен ({type(exc).__name__}): {exc}") from exc
    return _row_to_unit(row, aliases)


def list_units(*, tenant: str = DEFAULT_TENANT) -> list[Unit]:
    """Весь справочник арендатора с синонимами. То, из чего бот однажды покажет список."""
    settings = check_environment()
    try:
        with psycopg.connect(settings.dsn) as conn, conn.cursor() as cur:
            cur.execute(_SELECT_UNITS_SQL, (tenant,))
            rows = cur.fetchall()
    except psycopg.Error as exc:
        raise PushError(f"Справочник точек недоступен ({type(exc).__name__}): {exc}") from exc
    return [_row_to_unit(row, tuple(str(a) for a in row[3])) for row in rows]


def upsert_unit(
    name: str,
    *,
    code: str | None = None,
    aliases: tuple[str, ...] = (),
    tenant: str = DEFAULT_TENANT,
) -> str:
    """Завести или обновить точку справочника вместе с её синонимами.

    Повторяемо: та же точка с тем же кодом (а без кода — с тем же названием)
    не создаёт вторую строку, а обновляет существующую. Именно поэтому у точки
    есть код: повторная загрузка справочника из внешнего источника иначе
    опознавала бы уже заведённые точки по названию — по тому самому, от чего
    задача и уходит.

    Синоним, совпавший с каноничным названием точки, в карту не пишется: он
    там ничего не решает, а место занимает и путает читающего.

    Отказ — `PushError`: справочник ведётся тем же блоком и теми же правилами,
    что слив, и вызывающему не нужно знать про второй тип ошибки.
    """
    settings = check_environment()
    key = normalize_unit_name(name)
    if not key:
        raise PushError("У точки пустое название — в справочник её завести нечем")

    normalized_code = (code or "").strip() or None
    try:
        with psycopg.connect(settings.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(_INSERT_TENANT_SQL, (tenant,))
                sql = _UPSERT_UNIT_BY_CODE_SQL if normalized_code else _UPSERT_UNIT_BY_NAME_SQL
                cur.execute(
                    sql,
                    {"tenant": tenant, "name": name.strip(), "key": key, "code": normalized_code},
                )
                row = cur.fetchone()
                if row is None:
                    raise PushError(
                        "Postgres не вернул точку после записи — целостность транзакции нарушена"
                    )
                unit_id = str(row[0])

                for alias in aliases:
                    alias_key = normalize_unit_name(alias)
                    if not alias_key or alias_key == key:
                        continue
                    cur.execute(
                        _UPSERT_ALIAS_SQL, (tenant, alias_key, UUID(unit_id), alias.strip())
                    )
            conn.commit()
    except PushError:
        raise
    except psycopg.Error as exc:
        raise PushError(
            f"Не удалось записать точку «{name}» в справочник ({type(exc).__name__}): {exc}"
        ) from exc
    return unit_id
