"""Чтение уже слитых проверок. Только `select` — решений и расчётов здесь нет.

Дополняет `push_inspection` (T093) собственным способом проверить слив, не
трогая psql руками; читает отсюда же MCP-сервер (T095, блок `mcp`), и своих
запросов к базе он не пишет — никто, кроме этого блока, в Postgres не ходит.

**Арендатор — обязательный параметр, а не фильтр** (T110). Схема стала
мультиарендной миграцией `0002`, а эта точка чтения оставалась общей на всех:
выборка отдавала проверки всех арендаторов сразу и без предела. Пока арендатор
в продукте один, течь нечему — но читает отсюда MCP-сервер, который мы сами
даём в руки агенту партнёра, и необязательный фильтр там однажды не передадут.
Поэтому у арендатора нет значения по умолчанию: вызов без него не проходит
вовсе, вместо того чтобы молча отдать чужую историю. То же правило и у чтения
по идентификатору (T114): угадать идентификатор нельзя, но неугадываемость —
это надежда, а не защита.

**Период отбирает база, а не вызывающий поверх прочитанной страницы** (T114).
Историю за три года зальют одним заходом программно (D035), и `pushed_at` у
всей истории окажется одной датой. Отбор поверх страницы, прочитанной «по дате
слива», после этого начнёт систематически терять проверки, а выглядеть будет
обычным списком. По той же причине и порядок выдачи — по дате ОБХОДА точки:
одинаковый `pushed_at` не упорядочивает ничего.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from typing import Any

import psycopg

from .config import check_environment
from .errors import DbError
from .models import FindingRow, InspectionDetail, InspectionRow
from .units import normalize_unit_name

#: Сколько строк отдаётся, если предел не назвали. Сотня — это и есть
#: «история точки глазами человека»: больше за один запрос не читают ни в
#: отчёте, ни через агента, а безграничная выдача на сети из сотен пиццерий
#: означает вычитать всю таблицу по случайному вопросу.
DEFAULT_LIMIT = 100

#: Потолок, выше которого предел перестаёт быть пределом. Без него
#: `limit=10_000_000` — тот же полный проход, только записанный так, будто
#: ограничение есть.
MAX_LIMIT = 1000

# Запросы печатают свой список колонок целиком (не собирают его из общего куска
# строкой): динамическая сборка текста SQL — ровно то, что ловит S608, и здесь
# ей взяться неоткуда не по обещанию, а по устройству кода.
#
# Точка присоединяется составной ссылкой `(tenant_code, id)` — той самой, что
# завела миграция `0002`. Так соединение физически не может подтянуть точку
# другого арендатора, даже если фильтр ниже когда-нибудь потеряется.
#
# Границы периода стоят в запросе ВСЕГДА, а не дописываются в текст по
# необходимости: незаданная граница — это `-infinity`/`infinity`, то есть
# отсутствие ограничения, записанное данными. Так текст запроса остаётся одним
# и тем же независимо от аргументов, а его план — проверяемым.
_LIST_ALL_SQL = """
select
    i.id, i.tenant_code, u.name, i.chat_id, i.kind, i.inspection_date,
    i.report_lang, i.checklist_version, i.pct, i.grade,
    (select count(*) from findings f where f.inspection_id = i.id),
    i.pushed_at, i.auditor, i.city, i.partner, i.contact
from inspections i
join units u on u.tenant_code = i.tenant_code and u.id = i.unit_id
where i.tenant_code = %(tenant)s
  and i.inspection_date >= coalesce(%(date_from)s::date, '-infinity'::date)
  and i.inspection_date <= coalesce(%(date_to)s::date, 'infinity'::date)
order by i.inspection_date desc, i.pushed_at desc
limit %(limit)s
"""

# Фильтр по точке идёт по обеим колонкам составного уникального индекса
# `units (tenant_code, name_normalized)`. Ведущая колонка — арендатор: без неё
# индекс не работал бы вовсе, а одноимённые точки двух арендаторов («Белград-1»
# есть и у управляющей компании, и у партнёра) склеились бы в одну историю.
_LIST_BY_UNIT_SQL = """
select
    i.id, i.tenant_code, u.name, i.chat_id, i.kind, i.inspection_date,
    i.report_lang, i.checklist_version, i.pct, i.grade,
    (select count(*) from findings f where f.inspection_id = i.id),
    i.pushed_at, i.auditor, i.city, i.partner, i.contact
from inspections i
join units u on u.tenant_code = i.tenant_code and u.id = i.unit_id
where i.tenant_code = %(tenant)s and u.name_normalized = %(unit)s
  and i.inspection_date >= coalesce(%(date_from)s::date, '-infinity'::date)
  and i.inspection_date <= coalesce(%(date_to)s::date, 'infinity'::date)
order by i.inspection_date desc, i.pushed_at desc
limit %(limit)s
"""

# Проверка целиком: та же шапка плюс разбивка оценки, которой в списке нет.
# Числа отдаются как лежат — ни одного действия над ними.
_GET_INSPECTION_SQL = """
select
    i.id, i.tenant_code, u.name, i.chat_id, i.kind, i.inspection_date,
    i.report_lang, i.checklist_version, i.pct, i.grade,
    (select count(*) from findings f where f.inspection_id = i.id),
    i.pushed_at, i.auditor, i.city, i.partner, i.contact,
    i.deductions, i.counts, i.by_zone
from inspections i
join units u on u.tenant_code = i.tenant_code and u.id = i.unit_id
where i.tenant_code = %(tenant)s and i.id = %(id)s
"""

# Формулировки лежат строками `(entity_type, entity_id, field, lang)` (D025) и
# берутся на языке речи ТОЙ проверки, в которой находка записана, а не на
# языке, выбранном этим слоем. Подзапросы идут по первичному ключу таблицы
# переводов, поэтому это точечное чтение, а не N+1.
#
# Арендатор здесь не проверяется намеренно: находки берутся у проверки, которую
# `get_inspection` уже сверил с арендатором, а принадлежать другой проверке
# находка не может — это внешний ключ. Второй заслон поверх первого нельзя было
# бы снять и увидеть красное, то есть проверить его работу стало бы нечем.
_FINDINGS_OF_INSPECTION_SQL = """
select
    f.id, f.inspection_id, u.name, i.inspection_date, f.n, f.code, f.level,
    f.zone, f.zone_unusual, f.source, i.speech_lang,
    f.suggested_code, f.suggested_level, f.suggested_zone, f.suggested_confidence,
    (select t.text from translations t
      where t.entity_type = 'finding' and t.entity_id = f.id
        and t.field = 'text' and t.lang = i.speech_lang),
    (select t.text from translations t
      where t.entity_type = 'finding' and t.entity_id = f.id
        and t.field = 'comment' and t.lang = i.speech_lang)
from findings f
join inspections i on i.id = f.inspection_id
join units u on u.tenant_code = i.tenant_code and u.id = i.unit_id
where f.inspection_id = %(id)s
order by f.n
"""

# Находки одной точки через все её проверки, свежие проверки впереди. Здесь
# арендатор — единственный заслон, и он проверяется снятием (тест на утечку).
_FINDINGS_BY_UNIT_SQL = """
select
    f.id, f.inspection_id, u.name, i.inspection_date, f.n, f.code, f.level,
    f.zone, f.zone_unusual, f.source, i.speech_lang,
    f.suggested_code, f.suggested_level, f.suggested_zone, f.suggested_confidence,
    (select t.text from translations t
      where t.entity_type = 'finding' and t.entity_id = f.id
        and t.field = 'text' and t.lang = i.speech_lang),
    (select t.text from translations t
      where t.entity_type = 'finding' and t.entity_id = f.id
        and t.field = 'comment' and t.lang = i.speech_lang)
from findings f
join inspections i on i.id = f.inspection_id
join units u on u.tenant_code = i.tenant_code and u.id = i.unit_id
where i.tenant_code = %(tenant)s and u.name_normalized = %(unit)s
order by i.inspection_date desc, i.pushed_at desc, f.n
limit %(limit)s
"""


def _row_to_inspection(row: Any) -> InspectionRow:
    """Строка курсора → `InspectionRow`.

    Тип строки психкопг не даёт статически по позиции колонки — `row: Any`
    здесь ровно на этой границе, а не расползается по модулю: дальше в коде
    типы снова конкретные.
    """
    return InspectionRow(
        id=str(row[0]),
        tenant_code=str(row[1]),
        unit_name=str(row[2]),
        chat_id=int(row[3]),
        kind=str(row[4]),
        inspection_date=row[5],
        report_lang=str(row[6]),
        checklist_version=str(row[7]),
        pct=float(row[8]),
        grade=str(row[9]),
        findings_count=int(row[10]),
        pushed_at=row[11].isoformat(),
        # Шапка письма (T176). Колонки объявлены `not null default ''`, но
        # `or ""` здесь не украшение: историю за прошлые годы зальют программно
        # из чужой выгрузки (D035), и `None` в подписи письма партнёру
        # напечатался бы словом «None».
        auditor=str(row[12] or ""),
        city=str(row[13] or ""),
        partner=str(row[14] or ""),
        contact=str(row[15] or ""),
    )


def _row_to_finding(row: Any) -> FindingRow:
    """Строка курсора → `FindingRow`. Порядок колонок — как в запросах выше.

    `source` склеивает NULL и пустую строку: и то, и другое означает «источник
    не записан», и двух разных видов у этого не бывает. Формулировка и
    комментарий, наоборот, остаются `None`, когда строки перевода нет вовсе:
    подменённые пустой строкой, они стали бы неотличимы от «аудитор ничего не
    написал».
    """
    return FindingRow(
        id=str(row[0]),
        inspection_id=str(row[1]),
        unit_name=str(row[2]),
        inspection_date=row[3],
        n=int(row[4]),
        code=str(row[5]),
        level=str(row[6]),
        zone=str(row[7]),
        zone_unusual=bool(row[8]),
        source=str(row[9] or ""),
        lang=str(row[10]),
        # Предложение модели (T164). `None` во всех четырёх — модель не
        # предлагала ничего; пустая строка сюда не доезжает, её склеивает с
        # `None` ещё слив. Уверенность приходит из `numeric` десятичной дробью
        # (`Decimal`), и `float` здесь — не округление, а приведение к тому же
        # типу, которым её отдал распознаватель.
        suggested_code=row[11],
        suggested_level=row[12],
        suggested_zone=row[13],
        suggested_confidence=None if row[14] is None else float(row[14]),
        text=row[15],
        comment=row[16],
    )


def _require_tenant(tenant: str) -> str:
    """Непустой код арендатора — или явный отказ.

    Пустая строка не совпала бы ни с одним `tenant_code` и вернула бы пустой
    список: ошибка вызывающего выглядела бы как «проверок нет». Это худший из
    исходов — он не чинится, потому что его никто не замечает.
    """
    code = (tenant or "").strip()
    if not code:
        raise DbError(
            "Не задан арендатор, чьи проверки читаем. Выборка без него отдала бы "
            "либо чужие проверки, либо пустоту вместо ошибки — оба исхода тихие"
        )
    return code


def _require_limit(limit: int) -> int:
    """Предел выдачи в осмысленных границах — или явный отказ."""
    if limit < 1 or limit > MAX_LIMIT:
        raise DbError(
            f"Предел выдачи {limit} вне допустимого: ожидается от 1 до {MAX_LIMIT}. "
            f"Ноль вернул бы пустоту вместо отказа, а число сверху — тот же полный "
            f"проход по таблице под видом ограничения"
        )
    return limit


def _require_unit(unit: str) -> str:
    """Непустое название точки — или явный отказ.

    Пустое название вернуло бы пустой список нарушений, то есть «у этой точки
    всё хорошо» вместо «вы не назвали точку».
    """
    name = (unit or "").strip()
    if not name:
        raise DbError(
            "Не названа точка, чьи находки читаем. Пустое название вернуло бы пустой "
            "список нарушений вместо отказа — а такой ответ никто не перепроверит"
        )
    return name


def _require_window(date_from: date | None, date_to: date | None) -> None:
    """Границы периода в правильном порядке — или явный отказ.

    Перевёрнутый период не совпадёт ни с одной проверкой и вернёт пустой
    список: перепутанные местами границы выглядели бы как «за этот период
    проверок не было».
    """
    if date_from is not None and date_to is not None and date_from > date_to:
        raise DbError(
            f"Перевёрнутый период: начало {date_from.isoformat()} позже конца "
            f"{date_to.isoformat()}. Такой период вернул бы пустоту вместо ошибки"
        )


def _require_inspection_id(inspection_id: str) -> str:
    """Идентификатор проверки — или явный отказ, а не «не найдено».

    Кривой идентификатор и несуществующий — разные вещи: первое ошибка
    вызывающего, второе законный ответ. Слитые в одно «проверка не найдена»,
    они прячут опечатку в аргументе за правдоподобным ответом.
    """
    raw = (inspection_id or "").strip()
    try:
        return str(uuid.UUID(raw))
    except (ValueError, AttributeError, TypeError) as exc:
        raise DbError(
            f"«{inspection_id}» не похоже на идентификатор проверки (ожидается UUID). "
            f"Ответ «такой проверки нет» здесь скрыл бы опечатку в запросе"
        ) from exc


@contextmanager
def _reading(что: str) -> Iterator[psycopg.Connection[Any]]:
    """Подключение на время чтения; отказ базы — `DbError`, а не пустая выдача.

    Пустой список вместо отказа означал бы «ничего не найдено» — а на деле
    прочитать не смогли, и это разные ответы. Наружу уходит тип исключения, а
    не его текст: в тексте драйвера может оказаться строка подключения.
    """
    settings = check_environment()
    try:
        with psycopg.connect(settings.dsn) as conn:
            yield conn
    except psycopg.Error as exc:
        raise DbError(f"Не удалось прочитать {что} ({type(exc).__name__})") from exc


def list_inspections(
    *,
    tenant: str,
    unit: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[InspectionRow]:
    """Проверки одного арендатора, свежие по дате обхода — первыми.

    `tenant` обязателен и значения по умолчанию не имеет намеренно (T110):
    подстановка «default» выглядела бы работающей ровно до появления второго
    арендатора, а потом отдала бы агенту партнёра A историю партнёра B.

    `unit` фильтрует по точному названию точки в пределах того же арендатора
    (по тому же правилу нормализации, что и слив). Карту синонимов (T092) эта
    выборка не спрашивает: «БГ2» здесь не найдёт проверок «Белград 2» — при
    появлении потребителя это отдельная работа, а не молчаливое расширение.

    `date_from`/`date_to` — период по дате ОБХОДА точки, обе границы
    включительно, любая может быть опущена. Отбор идёт в базе, а не поверх
    прочитанной страницы: после программной заливки истории (D035) дата слива
    у всей истории одна, и «первая страница по дате слива» перестаёт быть
    связана с периодом вовсе.

    `limit` ограничивает выдачу всегда: без предела один вопрос агента
    вычитывал бы всю историю сети.
    """
    tenant_code = _require_tenant(tenant)
    rows_limit = _require_limit(limit)
    _require_window(date_from, date_to)
    params: dict[str, object] = {
        "tenant": tenant_code,
        "limit": rows_limit,
        "date_from": date_from,
        "date_to": date_to,
    }
    with _reading("список проверок") as conn, conn.cursor() as cur:
        if unit is None:
            cur.execute(_LIST_ALL_SQL, params)
        else:
            cur.execute(_LIST_BY_UNIT_SQL, {**params, "unit": normalize_unit_name(unit)})
        rows = cur.fetchall()
    return [_row_to_inspection(row) for row in rows]


def get_inspection(inspection_id: str, *, tenant: str) -> InspectionDetail | None:
    """Одна проверка арендатора целиком: шапка, разбивка оценки и находки.

    `None` — проверки нет либо она принадлежит другому арендатору. Это один и
    тот же ответ намеренно: «такой проверки нет» и «такая проверка есть, но не
    ваша» — второе подтверждало бы существование чужого документа тому, кто
    перебирает идентификаторы.

    Оценка не пересчитывается: `pct`, `grade`, `deductions`, `counts` и
    `by_zone` отдаются ровно такими, какими их положил движок при завершении
    проверки (конституция, принцип 2).
    """
    tenant_code = _require_tenant(tenant)
    ident = _require_inspection_id(inspection_id)
    with _reading("проверку по идентификатору") as conn, conn.cursor() as cur:
        cur.execute(_GET_INSPECTION_SQL, {"tenant": tenant_code, "id": ident})
        row = cur.fetchone()
        if row is None:
            return None
        # Находки читаются тем же соединением и в той же транзакции: между
        # двумя подключениями проверка могла бы измениться, и шапка разъехалась
        # бы с телом документа.
        cur.execute(_FINDINGS_OF_INSPECTION_SQL, {"id": ident})
        findings = cur.fetchall()
    return InspectionDetail(
        inspection=_row_to_inspection(row),
        deductions=float(row[16]),
        counts=dict(row[17]),
        by_zone=dict(row[18]),
        findings=tuple(_row_to_finding(строка) for строка in findings),
    )


def findings_by_unit(*, tenant: str, unit: str, limit: int = DEFAULT_LIMIT) -> list[FindingRow]:
    """Находки одной точки по всем её проверкам, свежие проверки — первыми.

    Отвечает на вопрос «что у этой пиццерии повторяется», но сам повтор здесь
    не считается: отдаётся ряд записанных находок, а обобщает спрашивающий.
    Выведенное тут число («нарушение повторилось четыре раза») никто не
    записывал, а в ответе агента оно немедленно пошло бы как факт проверки.

    Арендатор обязателен по той же причине, что и у списка проверок, и здесь
    он единственный заслон: находки достаются через проверки, своей ссылки на
    арендатора у них нет.
    """
    tenant_code = _require_tenant(tenant)
    name = _require_unit(unit)
    rows_limit = _require_limit(limit)
    with _reading("находки точки") as conn, conn.cursor() as cur:
        cur.execute(
            _FINDINGS_BY_UNIT_SQL,
            {"tenant": tenant_code, "unit": normalize_unit_name(name), "limit": rows_limit},
        )
        rows = cur.fetchall()
    return [_row_to_finding(row) for row in rows]
