"""T253: личный доступ к MCP (`src/db/mcp_access.py`) — тесты уровня базы.

Модуль хранит связь «этот человек — этот токен»: круг допущенных людей и
живой токен каждого из них. Здесь проверяется поведение (кто в круге, кто
выпускает токен, что делает отзыв) и — отдельно и обязательно ЗАПУСКОМ, а не
чтением схемы — заслоны, которые держит миграция `0011`: значения токена в
базе нет, колонка отпечатка не принимает ничего, кроме SHA-256, и живым у
человека может быть только один токен разом.

Идёт под `db_env` (роль приложения `dodo_audit_app`), а не под суперпользова-
телем: суперпользователь обходит и ограничения-заслоны в духе `CheckViolation`
ничем не иначе, но частичный уникальный индекс и проверка формы отпечатка —
это ограничения ТАБЛИЦЫ, а не построчные политики, и держат они одинаково при
любой роли. Роль приложения выбрана здесь не ради RLS, а потому что это та
роль, под которой модуль реально работает на площадке (T111).
"""

from __future__ import annotations

from typing import Any

import pytest
from conftest import requires_db

psycopg = pytest.importorskip("psycopg")

from src.db.errors import AccessError  # noqa: E402
from src.db.mcp_access import (  # noqa: E402
    add_admin,
    is_admin,
    issue_token,
    list_admins,
    new_token,
    resolve_token,
    revoke_access,
    token_fingerprint,
)

pytestmark = requires_db

#: Роли в круге для тестов — не настоящие telegram_id, просто различимые числа.
ОСНОВАТЕЛЬ = 100
ПРИВЕДЁННЫЙ = 200
ПОСТОРОННИЙ = 999


def _строка(dsn: str, sql: str, params: tuple[Any, ...] = ()) -> tuple[Any, ...] | None:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def _выполнить(dsn: str, sql: str, params: tuple[Any, ...] = ()) -> int:
    """Выполнить запись и вернуть число затронутых строк."""
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        затронуто = cur.rowcount
        conn.commit()
    return затронуто


# --- круг: кто в нём и кто нет -------------------------------------------------


def test_приведённый_в_круг_опознаётся_а_посторонний_нет(db_env: str) -> None:
    """`is_admin` — единственный вопрос, на который отвечает круг (D099)."""
    add_admin(ОСНОВАТЕЛЬ, by=None)

    assert is_admin(ОСНОВАТЕЛЬ) is True
    assert is_admin(ПОСТОРОННИЙ) is False


def test_повторный_привод_живого_участника_ничего_не_меняет_и_не_переписывает_след(
    db_env: str,
) -> None:
    """Повторный привод не ошибка, но и не повод переписать, кто и когда привёл."""
    add_admin(ОСНОВАТЕЛЬ, by=None)
    первый_привод = add_admin(ПРИВЕДЁННЫЙ, by=ОСНОВАТЕЛЬ)
    assert первый_привод is True
    до = {строка.telegram_id: строка for строка in list_admins()}[ПРИВЕДЁННЫЙ]

    повторный_привод = add_admin(ПРИВЕДЁННЫЙ, by=ПОСТОРОННИЙ)

    assert повторный_привод is False, "повторный привод живого участника — не изменение"
    после = {строка.telegram_id: строка for строка in list_admins()}[ПРИВЕДЁННЫЙ]
    assert после.added_by == до.added_by == ОСНОВАТЕЛЬ, "след о первом приводе переписался"
    assert после.added_at == до.added_at, "время первого привода переписалось"


def test_у_основателя_added_by_none_а_у_приведённого_id_приводившего(db_env: str) -> None:
    """`added_by` — это ИМЕННО след привода, а не всегда заполненное поле."""
    add_admin(ОСНОВАТЕЛЬ, by=None)
    add_admin(ПРИВЕДЁННЫЙ, by=ОСНОВАТЕЛЬ)

    строки = {строка.telegram_id: строка for строка in list_admins()}

    assert строки[ОСНОВАТЕЛЬ].added_by is None
    assert строки[ПРИВЕДЁННЫЙ].added_by == ОСНОВАТЕЛЬ


# --- токены: выпуск и сверка ---------------------------------------------------


def test_выпущенный_токен_сверяется_и_отдаёт_того_человека_и_арендатора(db_env: str) -> None:
    """`resolve_token` — то же самое, что спрашивает сервер MCP на каждый запрос."""
    add_admin(ОСНОВАТЕЛЬ, by=None)

    выпуск = issue_token(ОСНОВАТЕЛЬ, tenant="belgrade")

    assert выпуск.tenant == "belgrade"
    assert выпуск.replaced_previous is False
    владелец = resolve_token(выпуск.value)
    assert владелец is not None
    assert владелец.telegram_id == ОСНОВАТЕЛЬ
    assert владелец.tenant == "belgrade"


def test_повторный_выпуск_гасит_прежний_токен_и_ставит_replaced_previous(db_env: str) -> None:
    """У человека живым может быть только один токен — повторный выпуск это держит."""
    add_admin(ОСНОВАТЕЛЬ, by=None)
    первый = issue_token(ОСНОВАТЕЛЬ, tenant="belgrade")

    второй = issue_token(ОСНОВАТЕЛЬ, tenant="belgrade")

    assert второй.replaced_previous is True
    assert второй.value != первый.value
    assert resolve_token(первый.value) is None, "прежний токен всё ещё сверяется"
    владелец = resolve_token(второй.value)
    assert владелец is not None and владелец.telegram_id == ОСНОВАТЕЛЬ


def test_незнакомый_токен_отвечает_ничем(db_env: str) -> None:
    assert resolve_token("совсем-незнакомое-значение-которого-не-выпускали") is None


# --- отзыв -----------------------------------------------------------------


def test_отзыв_гасит_и_круг_и_живой_токен(db_env: str) -> None:
    """Отзыв — одно движение на обе таблицы, иначе отозванный просто выпустил бы новый."""
    add_admin(ОСНОВАТЕЛЬ, by=None)
    токен = issue_token(ОСНОВАТЕЛЬ, tenant="belgrade")

    отзыв = revoke_access(ОСНОВАТЕЛЬ, by=ПОСТОРОННИЙ)

    assert отзыв.was_admin is True
    assert отзыв.tokens_revoked == 1
    assert is_admin(ОСНОВАТЕЛЬ) is False
    assert resolve_token(токен.value) is None


def test_отозванный_не_может_выпустить_себе_новый_токен(db_env: str) -> None:
    add_admin(ОСНОВАТЕЛЬ, by=None)
    revoke_access(ОСНОВАТЕЛЬ, by=ПОСТОРОННИЙ)

    with pytest.raises(AccessError):
        issue_token(ОСНОВАТЕЛЬ, tenant="belgrade")


def test_отозванного_можно_привести_обратно_и_тогда_он_снова_выпускает_токен(
    db_env: str,
) -> None:
    add_admin(ОСНОВАТЕЛЬ, by=None)
    revoke_access(ОСНОВАТЕЛЬ, by=ПОСТОРОННИЙ)

    возврат = add_admin(ОСНОВАТЕЛЬ, by=ПРИВЕДЁННЫЙ)

    assert возврат is True, "возвращение отозванного — изменение, а не молчаливое ничего"
    assert is_admin(ОСНОВАТЕЛЬ) is True
    выпуск = issue_token(ОСНОВАТЕЛЬ, tenant="belgrade")
    assert resolve_token(выпуск.value) is not None


def test_отзыв_того_кого_не_было_в_круге_не_отказ(db_env: str) -> None:
    """Идемпотентность отзыва: «его там и не было» — честный ответ, а не ошибка."""
    отзыв = revoke_access(ПОСТОРОННИЙ, by=ОСНОВАТЕЛЬ)

    assert отзыв.was_admin is False
    assert отзыв.tokens_revoked == 0


def test_след_отзыва_остаётся_в_списке_а_не_стирается(db_env: str) -> None:
    """`list_admins` обязан показать отозванного КАК ОТОЗВАННОГО, а не забыть про него."""
    add_admin(ОСНОВАТЕЛЬ, by=None)
    revoke_access(ОСНОВАТЕЛЬ, by=ПРИВЕДЁННЫЙ)

    строки = {строка.telegram_id: строка for строка in list_admins()}

    assert ОСНОВАТЕЛЬ in строки, "отозванный пропал из круга целиком, а не только из is_admin"
    строка = строки[ОСНОВАТЕЛЬ]
    assert строка.is_live is False
    assert строка.revoked_by == ПРИВЕДЁННЫЙ
    assert строка.revoked_at is not None


# --- заслоны схемы: проверено запуском, а не рассуждением ----------------------


def test_значения_токена_в_базе_нет(db_env: str) -> None:
    """Заголовок модуля обещает: значение токена не хранится нигде.

    Обещание кода проверяется прямым запросом к базе — читать нужно
    отпечаток, а не сырое значение, и сырого значения не должно найтись ни
    в одном столбце строки.
    """
    add_admin(ОСНОВАТЕЛЬ, by=None)
    выпуск = issue_token(ОСНОВАТЕЛЬ, tenant="belgrade")

    строка = _строка(
        db_env,
        "select id, telegram_id, tenant_code, fingerprint, issued_at, revoked_at, revoked_by "
        "from mcp_tokens where telegram_id = %s",
        (ОСНОВАТЕЛЬ,),
    )
    assert строка is not None
    отпечаток = строка[3]
    assert отпечаток == token_fingerprint(выпуск.value)
    сериализовано = "|".join("" if поле is None else str(поле) for поле in строка)
    assert выпуск.value not in сериализовано, "сырое значение токена нашлось в строке таблицы"


def test_сырой_токен_в_колонку_отпечатка_не_записывается(db_env: str) -> None:
    """Заслон — ограничение схемы (миграция `0011`), а не аккуратность кода модуля."""
    _выполнить(
        db_env, "insert into tenants (code) values ('mcp-schema-guard') on conflict do nothing"
    )
    похожий_на_сырой_токен = new_token()  # 43 знака, есть - и _ — не 64 hex-знака

    with pytest.raises(psycopg.errors.CheckViolation):
        _выполнить(
            db_env,
            "insert into mcp_tokens (telegram_id, tenant_code, fingerprint) "
            "values (%s, 'mcp-schema-guard', %s)",
            (ПОСТОРОННИЙ, похожий_на_сырой_токен),
        )


def test_у_человека_не_бывает_двух_живых_токенов_одновременно(db_env: str) -> None:
    """Заслон — частичный уникальный индекс `mcp_tokens_one_live_per_person_idx`.

    Отпечатки у двух вставок РАЗНЫЕ, чтобы отказ был именно об индексе «один
    живой на человека», а не о совпадении самого отпечатка.
    """
    _выполнить(
        db_env, "insert into tenants (code) values ('mcp-schema-guard') on conflict do nothing"
    )
    _выполнить(
        db_env,
        "insert into mcp_tokens (telegram_id, tenant_code, fingerprint) "
        "values (%s, 'mcp-schema-guard', %s)",
        (ПОСТОРОННИЙ, token_fingerprint("первый-фиктивный-токен-теста")),
    )

    with pytest.raises(psycopg.errors.UniqueViolation):
        _выполнить(
            db_env,
            "insert into mcp_tokens (telegram_id, tenant_code, fingerprint) "
            "values (%s, 'mcp-schema-guard', %s)",
            (ПОСТОРОННИЙ, token_fingerprint("второй-фиктивный-токен-теста")),
        )
