"""Инструменты чтения: что именно агент может спросить у базы проверок.

Три правила, которые здесь важнее удобства.

**Арендатор приходит сверху, из токена.** Ни у одного инструмента нет
аргумента `tenant`: его назвал бы кто угодно, а слой чтения обязан
фильтровать по арендатору, а не угадывать, откуда тот взялся (T110). Значение
подставляет точка входа, разобрав личный токен (`src/mcp/config.py`).

**Оценка не считается заново.** Проценты, буквы и разбивка приходят из базы
такими, какими их положил движок при завершении проверки. Инструменты не
выводят из них новых чисел: ни среднего по сети, ни разницы между двумя
проверками. Такое число никто не записывал, а в ответе агента оно немедленно
начинает цитироваться как официальная оценка. Счёт (сколько проверок, сколько
точек, сколько находок) и выбор записанного значения (лучшая и худшая
проверка) этого свойства не нарушают — они ничего не выводят.

**Пусто — это ответ.** У каждой выдачи есть поле `status`, написанное словами:
«ничего не найдено» отличимо от «не смогли прочитать», а упёршееся в предел
чтение говорит об этом само.

Запросов к базе здесь нет: они живут в `src/db/queries.py`, и никто, кроме
блока `db`, в Postgres не ходит (`docs/forge/blocks/db.md`).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date

from ..db.models import FindingRow, InspectionRow
from .errors import ToolError

#: Формат даты в аргументах инструментов. ISO и только он: «15.08.2026» и
#: «08/15/2026» читаются по-разному в разных странах, а тихо угаданный порядок
#: дня и месяца даст ответ не за тот период, ничем себя не выдав.
DATE_FORMAT_HINT = "ГГГГ-ММ-ДД"


@dataclass(frozen=True)
class _Page:
    """Прочитанная страница проверок и то, чем она ограничена."""

    rows: tuple[InspectionRow, ...]
    limit: int
    #: Чтение упёрлось в предел: за выдачей могут остаться ещё проверки.
    truncated: bool


def _read(
    *,
    tenant: str,
    unit: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int | None = None,
) -> _Page:
    """Страница проверок арендатора через официальный контракт блока `db`.

    Импорт внутри функции, а не наверху модуля: `src.db.queries` тянет
    `psycopg`, и жадный импорт ронял бы сбор всего `tests/` в окружении, где
    зависимость блока не поставлена (тот же приём и та же причина, что в
    `src/db/__init__.py` и `tests/conftest.py`).

    Предел и его потолок не переписываются здесь: и значение по умолчанию, и
    проверка границ живут в одном месте — в самом слое чтения.

    Период уходит туда же (T119). Отбирать его здесь, поверх прочитанной
    страницы, значило бы терять проверки молча: страница набирается по самым
    свежим, а спрашивают про период, которого в ней может не быть вовсе.
    """
    from ..db.queries import DEFAULT_LIMIT, list_inspections

    rows_limit = DEFAULT_LIMIT if limit is None else limit
    rows = tuple(
        list_inspections(
            tenant=tenant, unit=unit, date_from=date_from, date_to=date_to, limit=rows_limit
        )
    )
    return _Page(rows=rows, limit=rows_limit, truncated=len(rows) >= rows_limit)


def _read_everything(*, tenant: str, date_from: date | None, date_to: date | None) -> _Page:
    """Чтение по потолку — для ответа, которому нужен весь период целиком.

    Сводке по сети предел не задают: её считают по всему, что попало в период.
    Потолок при этом остаётся — без него один вопрос агента вычитывал бы всю
    историю сети, — и упёршееся в него чтение помечается `truncated`, а не
    выдаёт себя за полную сводку.
    """
    from ..db.queries import MAX_LIMIT

    return _read(tenant=tenant, date_from=date_from, date_to=date_to, limit=MAX_LIMIT)


def _require_limit(limit: int | None) -> int | None:
    """Предел выдачи в границах слоя чтения — или явный отказ.

    Проверяется здесь, а не только в базе, ради ответа спрашивающему: негодный
    аргумент обязан вернуться как отказ по аргументу («предел вне
    допустимого»), а не как «не удалось прочитать проверки» — именно так
    выглядел бы отказ слоя чтения, дошедший до агента через общий перехват.
    Само число потолка не переписывается: оно берётся из слоя чтения, где и
    живёт.
    """
    if limit is None:
        return None
    from ..db.queries import MAX_LIMIT

    if limit < 1 or limit > MAX_LIMIT:
        raise ToolError(
            f"Предел выдачи {limit} вне допустимого: ожидается от 1 до {MAX_LIMIT}. "
            f"Ноль вернул бы пустоту вместо отказа, а число сверху — полный проход "
            f"по таблице под видом ограничения"
        )
    return limit


def _parse_date(value: str | None, *, field: str) -> date | None:
    """Строка из аргументов → дата. Кривая строка — отказ, а не «без фильтра»."""
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise ToolError(
            f"Аргумент {field}={value!r} — не дата. Ожидается {DATE_FORMAT_HINT}: "
            f"угаданный порядок дня и месяца дал бы ответ за другой период молча"
        ) from None


def _parse_window(date_from: str | None, date_to: str | None) -> tuple[date | None, date | None]:
    """Границы периода. Перевёрнутый период — отказ, а не пустая выдача."""
    since = _parse_date(date_from, field="date_from")
    until = _parse_date(date_to, field="date_to")
    if since is not None and until is not None and since > until:
        raise ToolError(
            f"Перевёрнутый период: date_from={since.isoformat()} позже "
            f"date_to={until.isoformat()}. Такой период вернул бы пустоту вместо ошибки"
        )
    return since, until


def _require_unit(unit: str) -> str:
    """Непустое название точки — или явный отказ.

    Пустое название вернуло бы пустую историю, и ошибка вызывающего выглядела
    бы как «у этой точки проверок нет».
    """
    name = (unit or "").strip()
    if not name:
        raise ToolError(
            "Не названа точка, чью историю читаем. Пустое название вернуло бы пустую "
            "историю вместо отказа — а это ошибка, которую никто не замечает"
        )
    return name


def _require_inspection_id(value: str) -> str:
    """Идентификатор проверки в форме UUID — или явный отказ.

    Тот же приём и тот же довод, что у `_require_limit`: кривой идентификатор
    обязан вернуться как отказ по аргументу, а не дойти до слоя чтения и
    обернуться его собственным отказом («не удалось прочитать проверку») —
    в котором опечатку в аргументе не разглядеть.
    """
    raw = (value or "").strip()
    try:
        return str(uuid.UUID(raw))
    except (ValueError, AttributeError, TypeError):
        raise ToolError(f"«{value}» не похоже на идентификатор проверки, ожидается UUID") from None


def _inspection(row: InspectionRow) -> dict[str, object]:
    """Проверка для списка.

    `chat_id` наружу не отдаётся: это чат аудитора в телеграме — личный
    идентификатор, агенту партнёра не нужный ни для одного вопроса
    (конституция, раздел «Правила безопасности»). `tenant_code` тоже: он один
    на весь ответ и стоит в его шапке.
    """
    return {
        "id": row.id,
        "unit": row.unit_name,
        "kind": row.kind,
        "inspection_date": row.inspection_date.isoformat(),
        "report_lang": row.report_lang,
        "checklist_version": row.checklist_version,
        "pct": row.pct,
        "grade": row.grade,
        "findings_count": row.findings_count,
        "pushed_at": row.pushed_at,
    }


def _history_entry(row: InspectionRow) -> dict[str, object]:
    """Точка ряда: записанная оценка и когда она получена.

    Разницы между соседними проверками здесь нет намеренно — ряд отдаётся как
    есть, а сравнивает спрашивающий.
    """
    return {
        "id": row.id,
        "inspection_date": row.inspection_date.isoformat(),
        "pct": row.pct,
        "grade": row.grade,
        "findings_count": row.findings_count,
        "checklist_version": row.checklist_version,
    }


def _finding(row: FindingRow) -> dict[str, object]:
    """Одна записанная находка — как лежит, без выведенных чисел.

    `text`/`comment` могут быть `None` — так и отдаётся наружу, `None` пустой
    строкой не подменяется: «перевода нет вовсе» и «аудитор ничего не
    написал» обязаны различаться (`docs/forge/blocks/db.md`).
    """
    return {
        "id": row.id,
        "inspection_id": row.inspection_id,
        "unit": row.unit_name,
        "inspection_date": row.inspection_date.isoformat(),
        "n": row.n,
        "code": row.code,
        "level": row.level,
        "zone": row.zone,
        "zone_unusual": row.zone_unusual,
        "source": row.source,
        "lang": row.lang,
        "text": row.text,
        "comment": row.comment,
    }


def _brief(row: InspectionRow) -> dict[str, object]:
    """Ссылка на конкретную записанную проверку внутри сводки."""
    return {
        "id": row.id,
        "unit": row.unit_name,
        "inspection_date": row.inspection_date.isoformat(),
        "pct": row.pct,
        "grade": row.grade,
    }


def _found(count: int, *, subject: str = "inspections") -> str:
    return f"no {subject} found" if count == 0 else f"{count} {subject} found"


def _limit_note(*, limit: int, truncated: bool, subject: str) -> str:
    """Приписка про упёршееся в предел чтение. Пусто — значит, предел не мешал.

    Параметризовано предметом чтения («inspections», «findings»): одна и та
    же формулировка обслуживает оба инструмента вместо второй копии текста.
    """
    if not truncated:
        return ""
    return (
        f"; the read stopped at the limit of {limit} rows, so older {subject} are not counted here"
    )


def _grades(rows: Iterable[InspectionRow]) -> dict[str, int]:
    """Распределение ЗАПИСАННЫХ букв. Новых букв здесь не появляется."""
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.grade] = counts.get(row.grade, 0) + 1
    return dict(sorted(counts.items()))


def _by_unit(rows: Sequence[InspectionRow]) -> list[dict[str, object]]:
    """Свод по точкам: сколько проверок и какая из них последняя.

    Строки приходят свежими вперёд по дате ОБХОДА точки, поэтому первая
    встреченная строка точки и есть её последняя проверка — досортировывать
    нечего. До T114 порядок шёл по дате слива, и у истории, залитой одним
    заходом (D035), «последней» оказывалась случайная проверка: допущение было
    неверным, а выглядело обычным полем ответа.
    """
    counts: dict[str, int] = {}
    latest: dict[str, InspectionRow] = {}
    for row in rows:
        counts[row.unit_name] = counts.get(row.unit_name, 0) + 1
        latest.setdefault(row.unit_name, row)
    return [
        {"unit": name, "inspections": count, "latest": _history_entry(latest[name])}
        for name, count in counts.items()
    ]


# --- инструменты -------------------------------------------------------------


def list_inspections(
    *,
    tenant: str,
    unit: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int | None = None,
) -> dict[str, object]:
    """Проверки арендатора, новые сначала.

    И период, и предел применяет слой чтения (T119): период первым, предел
    после него. Обратный порядок — предел по свежим строкам, а период поверх
    прочитанного — терял бы проверки молча, ответом «за этот период проверок
    нет». Упёршееся в предел чтение ответ помечает само.
    """
    rows_limit = _require_limit(limit)
    since, until = _parse_window(date_from, date_to)
    page = _read(tenant=tenant, unit=unit, date_from=since, date_to=until, limit=rows_limit)
    return {
        "tenant": tenant,
        "filters": {
            "unit": unit,
            "date_from": since.isoformat() if since else None,
            "date_to": until.isoformat() if until else None,
            "limit": page.limit,
        },
        "count": len(page.rows),
        "truncated": page.truncated,
        "status": _found(len(page.rows))
        + _limit_note(limit=page.limit, truncated=page.truncated, subject="inspections"),
        "inspections": [_inspection(row) for row in page.rows],
    }


def unit_history(*, tenant: str, unit: str, limit: int | None = None) -> dict[str, object]:
    """Ряд записанных оценок одной точки, свежие впереди.

    Разниц и трендов здесь нет: это числа, которых никто не записывал.
    Сравнивает их спрашивающий, видя весь ряд целиком.
    """
    name = _require_unit(unit)
    page = _read(tenant=tenant, unit=name, limit=_require_limit(limit))
    return {
        "tenant": tenant,
        "unit": name,
        "count": len(page.rows),
        "truncated": page.truncated,
        "status": _found(len(page.rows))
        + _limit_note(limit=page.limit, truncated=page.truncated, subject="inspections"),
        "history": [_history_entry(row) for row in page.rows],
    }


def network_summary(
    *, tenant: str, date_from: str | None = None, date_to: str | None = None
) -> dict[str, object]:
    """Сводка по сети арендатора за период.

    Период задаётся двумя датами, а не словом («за квартал»): разбор такого
    слова на сервере был бы догадкой о том, что имел в виду человек, а
    спрашивающий агент знает сегодняшнее число и назовёт границы точно.

    Средней оценки в сводке нет намеренно — такого числа никто не записывал.
    Есть распределение записанных букв, счёт проверок, точек и находок, а
    лучшая и худшая проверки — это ссылки на конкретные записанные строки.
    """
    since, until = _parse_window(date_from, date_to)
    page = _read_everything(tenant=tenant, date_from=since, date_to=until)
    rows = page.rows
    best = max(rows, key=lambda row: row.pct, default=None)
    worst = min(rows, key=lambda row: row.pct, default=None)
    return {
        "tenant": tenant,
        "window": {
            "date_from": since.isoformat() if since else None,
            "date_to": until.isoformat() if until else None,
        },
        "inspections": len(rows),
        "units": len({row.unit_name for row in rows}),
        "findings_total": sum(row.findings_count for row in rows),
        "grades": _grades(rows),
        "best": _brief(best) if best is not None else None,
        "worst": _brief(worst) if worst is not None else None,
        "by_unit": _by_unit(rows),
        "truncated": page.truncated,
        "status": _found(len(rows))
        + _limit_note(limit=page.limit, truncated=page.truncated, subject="inspections"),
    }


def get_inspection(*, tenant: str, id: str) -> dict[str, object]:
    """Одна проверка арендатора целиком: шапка, разбивка оценки и находки.

    Аргумент назван `id`, а не `inspection_id`: так же называется поле в
    выдаче `list_inspections`, откуда агент его и копирует. Внутри функции
    встроенный `id()` не используется — переопределённое имя ему не нужно.

    `None` от слоя чтения — это «ничего не найдено» (в том числе — «проверка
    есть, но чужая», T110), а не отказ. Набор ключей ответа одинаков в обоих
    исходах: агенту не нужно угадывать, есть ли поле.
    """
    ident = _require_inspection_id(id)
    from ..db.queries import get_inspection as db_get_inspection

    detail = db_get_inspection(ident, tenant=tenant)
    if detail is None:
        return {
            "tenant": tenant,
            "id": ident,
            "found": False,
            "status": "no inspection found with that id",
            "inspection": None,
            "deductions": None,
            "counts": None,
            "by_zone": None,
            "findings": [],
        }
    return {
        "tenant": tenant,
        "id": ident,
        "found": True,
        "status": f"inspection found; {len(detail.findings)} findings recorded",
        "inspection": _inspection(detail.inspection),
        "deductions": detail.deductions,
        "counts": detail.counts,
        "by_zone": detail.by_zone,
        "findings": [_finding(row) for row in detail.findings],
    }


def inspection_letter(*, tenant: str, id: str, lang: str | None = None) -> dict[str, object]:
    """Текст письма партнёру по уже записанной проверке (T171).

    Письмо отправляет человек руками из почты (Q010), и в системе оно нигде не
    лежит: бот показывает его один раз, при завершении проверки. Поэтому здесь
    оно собирается заново — движком, по методике ТОЙ версии, которой помечена
    проверка, и только если посчитанное движком сошлось с записанным. Разбор —
    `src/mcp/letters.py`; здесь тонкая обёртка, потому что обработчик
    инструмента проверок обязан лежать в этом модуле (`tests/test_mcp_server.py`).

    Записью это не является ни в каком виде: проверка не меняется, боевая
    методика открывается только на чтение, а состояние для движка собирается во
    временном каталоге, который тут же исчезает.

    Набор ключей ответа одинаков в обоих исходах — как у `get_inspection`.
    """
    ident = _require_inspection_id(id)
    from ..db.queries import get_inspection as db_get_inspection

    detail = db_get_inspection(ident, tenant=tenant)
    if detail is None:
        return {
            "tenant": tenant,
            "id": ident,
            "found": False,
            "status": "no inspection found with that id",
            "letter": None,
        }
    from .letters import build, sources

    return {
        "tenant": tenant,
        "id": ident,
        "found": True,
        "unit": detail.inspection.unit_name,
        "inspection_date": detail.inspection.inspection_date.isoformat(),
        **build(detail, lang=lang, papers=sources()),
    }


def findings_by_unit(*, tenant: str, unit: str, limit: int | None = None) -> dict[str, object]:
    """Записанные находки одной точки по всем её проверкам, свежие проверки первыми.

    Отвечает на вопрос «что у этой точки повторяется», но сам повтор здесь не
    считается: ни счёта повторов, ни группировки по коду, ни доли — такое
    число никто не записывал, а в ответе агента оно немедленно пошло бы как
    факт проверки. Отдаётся ряд записанных находок, обобщает спрашивающий.
    """
    name = _require_unit(unit)
    requested_limit = _require_limit(limit)
    from ..db.queries import DEFAULT_LIMIT
    from ..db.queries import findings_by_unit as db_findings_by_unit

    applied_limit = DEFAULT_LIMIT if requested_limit is None else requested_limit
    rows = db_findings_by_unit(tenant=tenant, unit=name, limit=applied_limit)
    truncated = len(rows) >= applied_limit
    return {
        "tenant": tenant,
        "unit": name,
        "limit": applied_limit,
        "count": len(rows),
        "truncated": truncated,
        "status": _found(len(rows), subject="findings")
        + _limit_note(limit=applied_limit, truncated=truncated, subject="findings"),
        "findings": [_finding(row) for row in rows],
    }
