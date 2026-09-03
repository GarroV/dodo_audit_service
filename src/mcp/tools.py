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

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date

from ..db.models import InspectionRow
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


def _read(*, tenant: str, unit: str | None = None, limit: int | None = None) -> _Page:
    """Страница проверок арендатора через официальный контракт блока `db`.

    Импорт внутри функции, а не наверху модуля: `src.db.queries` тянет
    `psycopg`, и жадный импорт ронял бы сбор всего `tests/` в окружении, где
    зависимость блока не поставлена (тот же приём и та же причина, что в
    `src/db/__init__.py` и `tests/conftest.py`).

    Предел и его потолок не переписываются здесь: и значение по умолчанию, и
    проверка границ живут в одном месте — в самом слое чтения.
    """
    from ..db.queries import DEFAULT_LIMIT, list_inspections

    rows_limit = DEFAULT_LIMIT if limit is None else limit
    rows = tuple(list_inspections(tenant=tenant, unit=unit, limit=rows_limit))
    return _Page(rows=rows, limit=rows_limit, truncated=len(rows) >= rows_limit)


def _read_all(*, tenant: str, unit: str | None = None) -> _Page:
    """Страница по потолку чтения — для ответов, которым нужна вся история.

    Фильтр по датам слой чтения пока не умеет (см. `docs/forge/blocks/mcp.md`,
    «Чего не хватило в слое чтения»), поэтому период отбирается уже здесь, по
    прочитанной странице. Страница берётся по потолку, а упёршееся в него
    чтение честно помечается `truncated`: ответ, молча посчитанный по первой
    сотне строк, выглядел бы полной сводкой по сети.
    """
    from ..db.queries import MAX_LIMIT

    return _read(tenant=tenant, unit=unit, limit=MAX_LIMIT)


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


def _in_window(row: InspectionRow, since: date | None, until: date | None) -> bool:
    """Попадает ли проверка в период — по дате ПРОВЕРКИ, не по дате слива.

    Историю за три года зальют одним заходом (D035): дата слива у всех строк
    будет одинаковой и сегодняшней, а спрашивают всегда про дату обхода.
    Обе границы включительно: «с 1 по 31 августа» человек понимает именно так.
    """
    if since is not None and row.inspection_date < since:
        return False
    return not (until is not None and row.inspection_date > until)


def _select(page: _Page, since: date | None, until: date | None) -> tuple[InspectionRow, ...]:
    return tuple(row for row in page.rows if _in_window(row, since, until))


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


def _limit_note(page: _Page) -> str:
    """Приписка про упёршееся в предел чтение. Пусто — значит, предел не мешал."""
    if not page.truncated:
        return ""
    return (
        f"; the read stopped at the limit of {page.limit} rows, so older inspections "
        f"are not counted here"
    )


def _grades(rows: Iterable[InspectionRow]) -> dict[str, int]:
    """Распределение ЗАПИСАННЫХ букв. Новых букв здесь не появляется."""
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.grade] = counts.get(row.grade, 0) + 1
    return dict(sorted(counts.items()))


def _by_unit(rows: Sequence[InspectionRow]) -> list[dict[str, object]]:
    """Свод по точкам: сколько проверок и какая из них последняя.

    Строки приходят свежими вперёд, поэтому первая встреченная точка и есть её
    последняя проверка — досортировывать нечего.
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

    Без периода выдача ограничена пределом и читается ровно на него. С
    периодом читается страница по потолку, а отбор идёт по дате проверки уже
    здесь — фильтра по датам в слое чтения пока нет, и ответ честно помечает,
    когда чтение упёрлось в предел.
    """
    since, until = _parse_window(date_from, date_to)
    windowed = since is not None or until is not None
    # Без периода предел отдаётся самой базе — она и читает ровно столько.
    # С периодом читается страница по потолку: отобрать по дате можно только
    # после чтения, и предел, применённый до отбора, дал бы неверный ответ.
    page = (
        _read_all(tenant=tenant, unit=unit)
        if windowed
        else _read(tenant=tenant, unit=unit, limit=limit)
    )
    selected = _select(page, since, until)
    shown = selected[:limit] if (windowed and limit is not None) else selected
    truncated = page.truncated or len(shown) < len(selected)
    note = _limit_note(page)
    if not note and truncated:
        note = f"; more inspections match the period than the limit of {limit} shown"
    return {
        "tenant": tenant,
        "filters": {
            "unit": unit,
            "date_from": since.isoformat() if since else None,
            "date_to": until.isoformat() if until else None,
            "limit": limit if limit is not None else page.limit,
        },
        "count": len(shown),
        "read_rows": len(page.rows),
        "truncated": truncated,
        "status": _found(len(shown)) + note,
        "inspections": [_inspection(row) for row in shown],
    }


def unit_history(*, tenant: str, unit: str, limit: int | None = None) -> dict[str, object]:
    """Ряд записанных оценок одной точки, свежие впереди.

    Разниц и трендов здесь нет: это числа, которых никто не записывал.
    Сравнивает их спрашивающий, видя весь ряд целиком.
    """
    name = _require_unit(unit)
    page = _read(tenant=tenant, unit=name, limit=limit)
    return {
        "tenant": tenant,
        "unit": name,
        "count": len(page.rows),
        "read_rows": len(page.rows),
        "truncated": page.truncated,
        "status": _found(len(page.rows)) + _limit_note(page),
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
    page = _read_all(tenant=tenant)
    rows = _select(page, since, until)
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
        "read_rows": len(page.rows),
        "truncated": page.truncated,
        "status": _found(len(rows)) + _limit_note(page),
    }
