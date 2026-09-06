"""Личный доступ к MCP: круг людей и их токены (T253, решения D098 и D099).

Хранилище того, чего у продукта не было вовсе: связи «этот человек — этот
токен». До сих пор токен заводился руками в `.env` записью «арендатор=токен»
(`src/mcp/config.py`), принадлежал СТОРОНЕ и открывал всю её историю проверок,
а отозвать его можно было только правкой файла с перезапуском сервера — то
есть у всех сразу. Бот поэтому честно не печатал токен ни одному человеку
(T209): напечатанный был бы выдан наугад.

Блок выбран не по удобству. `src.bot` и `src.mcp` — пиры и друг друга не
импортируют (`pyproject.toml`, контракт слоёв): бот выпускает токен, сервер
MCP его сверяет, и общее место у них ровно одно — `src.db`, ярусом ниже. Так
что хранилище живёт здесь не «заодно с базой», а потому что это единственная
точка, где обе стороны встречаются, не нарушая контракт.

**ЗНАЧЕНИЕ ТОКЕНА НЕ ХРАНИТСЯ.** Ни в базе, ни в журнале, ни в возвращаемых
структурах после выпуска. Хранится SHA-256, и держит это не договорённость, а
ограничение схемы (миграция `0011`): колонка принимает 64 знака
шестнадцатеричной записи, и сырой токен в неё физически не запишется.

**Почему SHA-256, а не bcrypt/argon2.** Вопрос законный и ответ на него не
«так короче». Медленные функции с солью нужны там, где секрет придумал
человек: у пароля мало энтропии, и вся защита в том, чтобы каждая попытка
перебора стоила дорого. Здесь секрет — 256 случайных бит от
`secrets.token_urlsafe(32)`; перебирать нечего, словаря для него не
существует, и растягивание не добавляет стойкости ни на бит. Зато соль
отняла бы главное: посоленный отпечаток невозможно найти индексом, и сверка
предъявленного токена превратилась бы в перебор ВСЕЙ таблицы с медленной
функцией на каждую строку — на каждый запрос к серверу. То есть цена
известная, польза нулевая.
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import psycopg

from .config import check_environment
from .errors import AccessError

#: Сколько случайных байт в токене. 32 байта → 43 знака `token_urlsafe`, то
#: есть заведомо длиннее нижней границы, которую сервер требует от токенов из
#: `.env` (`src.mcp.config.MIN_TOKEN_LENGTH`, 24 знака). Совпадать они не
#: обязаны: там это ЗАСЛОН от короткого токена, набранного человеком, здесь —
#: длина того, что генерирует машина, и уменьшать её незачем.
TOKEN_BYTES = 32

#: Длина отпечатка шестнадцатеричной записью. Проверяется и схемой (миграция
#: `0011`), и здесь: до базы отпечаток идёт через эту функцию, и ошибка в ней
#: обязана быть видна на месте, а не отказом внешнего ограничения.
FINGERPRINT_LENGTH = 64

_SELECT_LIVE_ADMIN_SQL = """
select 1 from mcp_admins where telegram_id = %s and revoked_at is null
"""

#: Круг целиком, включая отозванных: это и есть след «кто кого привёл и кто
#: отнял». Отозванные идут последними, живые — по времени привода.
_SELECT_ADMINS_SQL = """
select telegram_id, added_by, added_at, revoked_at, revoked_by,
       exists (select 1 from mcp_tokens t
               where t.telegram_id = a.telegram_id and t.revoked_at is null) as has_token
from mcp_admins a
order by (revoked_at is not null), added_at, telegram_id
"""

#: Привод в круг. Повторный привод того же человека — не ошибка, а
#: ВОЗВРАЩЕНИЕ отозванного: строка одна на человека (`telegram_id` первичный
#: ключ), и пометка отзыва с неё снимается. Живого участника повторный привод
#: не трогает вовсе — `where` не даёт переписать, кто и когда его привёл.
_ADD_ADMIN_SQL = """
insert into mcp_admins (telegram_id, added_by) values (%(who)s, %(by)s)
on conflict (telegram_id) do update
    set revoked_at = null, revoked_by = null, added_by = %(by)s, added_at = now()
    where mcp_admins.revoked_at is not null
"""

_REVOKE_ADMIN_SQL = """
update mcp_admins set revoked_at = now(), revoked_by = %(by)s
where telegram_id = %(who)s and revoked_at is null
"""

_REVOKE_TOKENS_SQL = """
update mcp_tokens set revoked_at = now(), revoked_by = %(by)s
where telegram_id = %(who)s and revoked_at is null
"""

_INSERT_TENANT_SQL = "insert into tenants (code) values (%s) on conflict (code) do nothing"

#: Выпуск. Имя запроса намеренно без слова «token»: линтер видит его в имени
#: константы и считает строку зашитым секретом (S105). Спорить с ним
#: подавлением здесь не за что — запрос вставляет ОТПЕЧАТОК, и «issued»
#: описывает происходящее точнее, чем подавленное предупреждение.
_INSERT_ISSUED_SQL = """
insert into mcp_tokens (telegram_id, tenant_code, fingerprint)
values (%(who)s, %(tenant)s, %(fingerprint)s)
returning id
"""

#: Сверка предъявленного токена. Идёт по отпечатку и только среди ЖИВЫХ:
#: отозванный токен не «находится и отклоняется», а не находится вовсе —
#: отдельной ветки «нашли, но он отозван» здесь нет намеренно, потому что
#: ветка, которую забыли, и есть способ, которым отзыв перестаёт работать.
_RESOLVE_FINGERPRINT_SQL = """
select telegram_id, tenant_code
from mcp_tokens
where fingerprint = %s and revoked_at is null
"""


@dataclass(frozen=True)
class IssuedToken:
    """Выпущенный токен — ЕДИНСТВЕННОЕ место, где его значение вообще есть.

    `repr=False` у значения по той же причине, что у карты токенов в
    `src/mcp/config.py`: `repr` структуры попадает в трейсбек любой соседней
    ошибки, а трейсбек — в журнал. Один такой журнал, показанный на созвоне, —
    и токен придётся выпускать заново.

    Структура живёт ровно до отправки сообщения человеку. Складывать её
    куда-либо — в состояние диалога, в кэш, в файл проверки — нельзя: «токен
    показан один раз» держится тем, что второй копии не существует.
    """

    value: str = field(repr=False)
    tenant: str
    #: Отозван ли при этом выпуске прежний токен того же человека. Нужен не
    #: для отчётности: человеку об этом говорится прямо в ответе, иначе он не
    #: поймёт, почему перестала работать настройка, сделанная в прошлый раз.
    replaced_previous: bool


@dataclass(frozen=True)
class TokenOwner:
    """Кому принадлежит предъявленный токен и чью историю он открывает.

    Значения токена здесь уже нет: дальше по коду оно не нужно ни для чего, а
    структура, которую передают между слоями, однажды уезжает в журнал целиком.
    """

    telegram_id: int
    tenant: str


@dataclass(frozen=True)
class AdminRow:
    """Строка круга — вместе со следом привода и отзыва."""

    telegram_id: int
    added_by: int | None
    added_at: str
    revoked_at: str | None
    revoked_by: int | None
    #: Есть ли у человека живой токен прямо сейчас. Отвечает на вопрос, ради
    #: которого след и заведён: «доступ у тех, кого я назвал» — это не то же
    #: самое, что «в круге те, кого я назвал».
    has_live_token: bool

    @property
    def is_live(self) -> bool:
        return self.revoked_at is None


@dataclass(frozen=True)
class Revocation:
    """Чем закончился отзыв доступа у человека."""

    telegram_id: int
    #: Был ли человек в круге до отзыва. Ложь означает «его там и не было» —
    #: и это не отказ, а честный ответ: отзыв идемпотентен.
    was_admin: bool
    #: Сколько живых токенов погашено. Ноль — законно: человек мог быть в
    #: круге и ни разу не выпустить себе токен.
    tokens_revoked: int


def token_fingerprint(token: str) -> str:
    """Отпечаток токена — то единственное, что от него остаётся в базе.

    Кодировка задана явно: отпечаток обязан совпадать у бота, который его
    записал, и у сервера MCP, который его ищет, а умолчание платформы под
    этим не подписывалось.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token() -> str:
    """Новый токен доступа.

    `token_urlsafe` — не вкусовщина: токен едет в заголовке HTTP (там
    latin-1), в командной строке и в файле настройки Claude Desktop, а из
    всех алфавитов только этот проходит везде без экранирования. Знаков вне
    ASCII в нём нет по построению — ровно тех, которые сервер отвергает у
    токенов из `.env`, потому что часть клиентов их не отправляет.
    """
    return secrets.token_urlsafe(TOKEN_BYTES)


@contextmanager
def _writing(что: str) -> Iterator[psycopg.Connection[Any]]:
    """Подключение на время правки; отказ базы — `AccessError`, а не тишина.

    Наружу уходит ТИП исключения драйвера, а не его текст: в тексте psycopg
    может оказаться строка подключения целиком. Тот же приём и та же причина,
    что у `queries._reading`.
    """
    settings = check_environment()
    try:
        with psycopg.connect(settings.dsn) as conn:
            yield conn
    except psycopg.Error as exc:
        raise AccessError(f"Не удалось {что} ({type(exc).__name__})") from exc


def is_admin(telegram_id: int) -> bool:
    """В круге ли этот человек прямо сейчас (D099).

    Спрашивается на КАЖДОЕ обращение к настройке, а не запоминается на старте:
    отзыв обязан действовать немедленно и без перезапуска, а список, снятый
    однажды в память, — это ровно тот способ, которым «немедленно» превращается
    в «после следующего подъёма бота».
    """
    with _writing("проверить круг доступа") as conn, conn.cursor() as cur:
        cur.execute(_SELECT_LIVE_ADMIN_SQL, (telegram_id,))
        return cur.fetchone() is not None


def list_admins() -> list[AdminRow]:
    """Круг целиком со следом: кто кого привёл, у кого живой токен, кто отозван."""
    with _writing("прочитать круг доступа") as conn, conn.cursor() as cur:
        cur.execute(_SELECT_ADMINS_SQL)
        rows = cur.fetchall()
    return [
        AdminRow(
            telegram_id=int(row[0]),
            added_by=None if row[1] is None else int(row[1]),
            added_at=str(row[2]),
            revoked_at=None if row[3] is None else str(row[3]),
            revoked_by=None if row[4] is None else int(row[4]),
            has_live_token=bool(row[5]),
        )
        for row in rows
    ]


def add_admin(telegram_id: int, *, by: int | None) -> bool:
    """Привести человека в круг. Возвращает, изменилось ли что-нибудь.

    `by` — кто привёл; `None` только у основателя круга, которого называет
    настройка стенда. Записать вместо этого его же идентификатор значило бы
    внести в след неправду: он не приводил себя, его назвали настройкой.

    Ложь в ответе означает «он и так был в круге» — не отказ: привести уже
    приведённого не ошибка, и переписывать при этом, кто и когда его привёл,
    нельзя, иначе след теряет первого приводившего.
    """
    with _writing("привести человека в круг доступа") as conn, conn.cursor() as cur:
        cur.execute(_ADD_ADMIN_SQL, {"who": telegram_id, "by": by})
        return cur.rowcount > 0


def revoke_access(telegram_id: int, *, by: int) -> Revocation:
    """Отозвать доступ поимённо и немедленно: и круг, и живые токены.

    **Одним движением, а не двумя.** Отзыв только токена не отзывает ничего:
    человек остаётся в круге и следующим же вызовом выпускает себе новый.
    Отзыв только круга оставляет живой токен работать на сервере MCP, который
    про круг не спрашивает вовсе. Поэтому и то и другое — здесь, в одной
    транзакции, и раздельных функций для них не заводится: раздельные однажды
    позовут по одной.

    Немедленность держится не расторопностью, а тем, что нигде нет копии: и
    бот, и сервер MCP спрашивают базу на каждое обращение, а перезапуск не
    участвует ни в одной из двух проверок.
    """
    with _writing("отозвать доступ") as conn, conn.cursor() as cur:
        cur.execute(_REVOKE_ADMIN_SQL, {"who": telegram_id, "by": by})
        was_admin = cur.rowcount > 0
        cur.execute(_REVOKE_TOKENS_SQL, {"who": telegram_id, "by": by})
        tokens = cur.rowcount
    return Revocation(telegram_id=telegram_id, was_admin=was_admin, tokens_revoked=max(tokens, 0))


def issue_token(telegram_id: int, *, tenant: str) -> IssuedToken:
    """Выпустить человеку личный токен, погасив прежний. Значение — только здесь.

    **Круг проверяется в той же транзакции, что и выпуск**, а не заранее
    вызывающим. Проверка снаружи оставила бы щель между «спросили» и
    «выпустили»: отзыв, случившийся в этот промежуток, не помешал бы выдать
    токен — и отозванный человек получил бы работающий доступ ПОСЛЕ отзыва.
    Щель узкая, но закрывается она даром, а «немедленно» из требования иначе
    перестаёт быть правдой.

    **Прежний токен гасится здесь же.** Не из аккуратности: у человека живым
    может быть ровно один токен, и держит это частичный уникальный индекс
    (миграция `0011`). Забытое погашение стало бы отказом записи, а не тихо
    накопленной пачкой живых токенов, — но лучше не доводить: повторный вызов
    обязан работать, а не отказывать.
    """
    token = new_token()
    with _writing("выпустить токен доступа") as conn, conn.cursor() as cur:
        cur.execute(_SELECT_LIVE_ADMIN_SQL, (telegram_id,))
        if cur.fetchone() is None:
            raise AccessError(
                "Токен доступа выпускается только тому, кто состоит в круге. "
                "Кто в нём состоит — решают те, кто в нём уже есть"
            )
        cur.execute(_REVOKE_TOKENS_SQL, {"who": telegram_id, "by": telegram_id})
        replaced = cur.rowcount > 0
        cur.execute(_INSERT_TENANT_SQL, (tenant,))
        cur.execute(
            _INSERT_ISSUED_SQL,
            {"who": telegram_id, "tenant": tenant, "fingerprint": token_fingerprint(token)},
        )
        if cur.fetchone() is None:
            raise AccessError("База не вернула строку после выпуска токена")
    return IssuedToken(value=token, tenant=tenant, replaced_previous=replaced)


def resolve_token(token: str) -> TokenOwner | None:
    """Предъявленный токен → чей он и чью историю открывает. Незнакомый — `None`.

    Это то, что сервер MCP спрашивает на каждый запрос (`src/mcp/config.py`).
    Поиск идёт по отпечатку одним обращением по индексу — потому и SHA-256 без
    соли, см. заголовок модуля.

    Постоянного времени сравнения здесь нет, и это не упущение. Оно нужно там,
    где сверяются СЕКРЕТЫ и по времени ответа секрет подбирается посимвольно
    (`Settings.tenant_for` сравнивает именно так). Здесь же в базу уходит
    отпечаток — необратимая свёртка 256 случайных бит: узнать по времени, что
    отпечаток не найден, не даёт ничего, потому что от отпечатка к токену пути
    нет. Подбирать пришлось бы сам токен, а он не подбирается.

    Отказ базы наружу уходит отказом (`AccessError`), а не `None`. Разница
    существенная: `None` означает «токен незнакомый», а упавшая база означает
    «мы не смогли посмотреть», и выдать второе за первое — это тихо закрыть
    доступ всем и объяснить это каждому неверным словом.
    """
    with _writing("сверить токен доступа") as conn, conn.cursor() as cur:
        cur.execute(_RESOLVE_FINGERPRINT_SQL, (token_fingerprint(token),))
        row = cur.fetchone()
    if row is None:
        return None
    return TokenOwner(telegram_id=int(row[0]), tenant=str(row[1]))
