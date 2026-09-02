"""T093: слив завершённой проверки в базу.

Единственная точка входа этого блока для записи — `push_inspection`. Вызывать
её положено на «Завершить» (block.md, DoD). Файл `inspection.json` остаётся
рабочим состоянием и после слива: эта функция его не трогает и не удаляет,
она только читает то, что уже отдаёт `domain` через официальный контракт
(`get_state`, `score`) и один раз записывает срез в Postgres.

Отказ здесь — всегда `PushError`, из чего бы он ни вырос: нет состояния, нет
связи с базой, оборвалась транзакция. Один тип исключения на весь блок даёт
вызывающему одно место для «база недоступна — проверка на точке всё равно
идёт своим чередом» (D027, конституция).
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import date
from typing import Any

import psycopg
from psycopg.types.json import Json

from src.domain import get_state
from src.domain import score as domain_score
from src.domain.errors import DomainError
from src.domain.models import Inspection, Score

from .config import check_environment
from .errors import PushError
from .fingerprint import compute_fingerprint
from .units import normalize_unit_name

#: Арендатор по умолчанию, пока в проверке своего не задано. Совпадает со
#: значением `domain.state.DEFAULT_TENANT` буквально: второй копии константы
#: здесь допустить нельзя, но импортировать внутреннюю переменную домена ради
#: одной строки — цена выше пользы, поэтому значение продублировано как
#: строковый литерал и закреплено тестом на конкретное значение "default".
DEFAULT_TENANT = "default"

_INSERT_INSPECTION_SQL = """
insert into inspections (
    tenant_code, unit_id, chat_id, kind, inspection_date, report_lang,
    ui_lang, speech_lang, checklist_version, auditor, city, partner, contact,
    pct, grade, deductions, counts, by_zone, source_fingerprint
) values (
    %(tenant_code)s, %(unit_id)s, %(chat_id)s, %(kind)s, %(inspection_date)s,
    %(report_lang)s, %(ui_lang)s, %(speech_lang)s, %(checklist_version)s,
    %(auditor)s, %(city)s, %(partner)s, %(contact)s, %(pct)s, %(grade)s,
    %(deductions)s, %(counts)s, %(by_zone)s, %(source_fingerprint)s
)
on conflict (source_fingerprint) do nothing
returning id
"""

_SELECT_BY_FINGERPRINT_SQL = "select id from inspections where source_fingerprint = %s"

_UPSERT_UNIT_SQL = """
insert into units (tenant_code, name, name_normalized)
values (%s, %s, %s)
on conflict (tenant_code, name_normalized) do update set name = excluded.name
returning id
"""

_INSERT_TENANT_SQL = "insert into tenants (code) values (%s) on conflict (code) do nothing"

_INSERT_FINDING_SQL = """
insert into findings (inspection_id, n, code, level, zone, zone_unusual, source)
values (%(inspection_id)s, %(n)s, %(code)s, %(level)s, %(zone)s, %(zone_unusual)s, %(source)s)
returning id
"""

_INSERT_PHOTO_SQL = """
insert into photos (finding_id, inspection_id, telegram_file_id)
values (%s, %s, %s)
"""

_INSERT_TRANSLATION_SQL = """
insert into translations (entity_type, entity_id, field, lang, text)
values (%s, %s, %s, %s, %s)
on conflict (entity_type, entity_id, field, lang) do update set text = excluded.text
"""


def _require_row(cur: psycopg.Cursor[Any]) -> tuple[Any, ...]:
    """Строка после INSERT/UPSERT с RETURNING. Её отсутствие — не «пусто», а поломка.

    Одно место вместо `fetchone()[0]` россыпью: `mypy --strict` не даёт
    индексировать `tuple | None` не глядя, а тихий `None` здесь никогда не
    ожидаемый исход — запросы всегда либо вставляют строку, либо явно её не
    находят через `if inserted is None`.
    """
    row: tuple[Any, ...] | None = cur.fetchone()
    if row is None:
        raise PushError("Postgres не вернул строку после INSERT — целостность транзакции нарушена")
    return row


def _parse_date(raw: str, *, chat_id: int) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise PushError(
            f"Дата проверки чата {chat_id} не в формате ГГГГ-ММ-ДД — слив отменён"
        ) from exc


def _by_zone_payload(score: Score) -> dict[str, object]:
    return {code: asdict(zone) for code, zone in score.by_zone.items()}


def _tenant_code(inspection: Inspection) -> str:
    return inspection.tenant or DEFAULT_TENANT


def _push(conn: psycopg.Connection[Any], inspection: Inspection, result: Score) -> str:
    tenant_code = _tenant_code(inspection)
    fingerprint = compute_fingerprint(inspection, result, tenant_code=tenant_code)
    inspection_date = _parse_date(inspection.date, chat_id=inspection.chat_id)

    with conn.cursor() as cur:
        cur.execute(_INSERT_TENANT_SQL, (tenant_code,))
        cur.execute(
            _UPSERT_UNIT_SQL,
            (tenant_code, inspection.unit, normalize_unit_name(inspection.unit)),
        )
        unit_id = _require_row(cur)[0]

        cur.execute(
            _INSERT_INSPECTION_SQL,
            {
                "tenant_code": tenant_code,
                "unit_id": unit_id,
                "chat_id": inspection.chat_id,
                "kind": inspection.kind,
                "inspection_date": inspection_date,
                "report_lang": inspection.report_lang,
                "ui_lang": inspection.ui_lang,
                "speech_lang": inspection.speech_lang,
                "checklist_version": inspection.checklist_version,
                "auditor": inspection.auditor,
                "city": inspection.city,
                "partner": inspection.partner,
                "contact": inspection.contact,
                "pct": result.pct,
                "grade": result.grade,
                "deductions": result.deductions,
                "counts": Json(dict(result.counts)),
                "by_zone": Json(_by_zone_payload(result)),
                "source_fingerprint": fingerprint,
            },
        )
        inserted = cur.fetchone()
        if inserted is None:
            # Отпечаток уже есть в базе — тот же слив уже случился раньше.
            # Второй вызов не создаёт дубль: возвращаем существующий id и
            # не трогаем находки — они уже записаны первым вызовом.
            cur.execute(_SELECT_BY_FINGERPRINT_SQL, (fingerprint,))
            existing_id = _require_row(cur)[0]
            conn.commit()
            return str(existing_id)

        inspection_id = inserted[0]

        for finding in inspection.findings:
            cur.execute(
                _INSERT_FINDING_SQL,
                {
                    "inspection_id": inspection_id,
                    "n": finding.n,
                    "code": finding.code,
                    "level": finding.level,
                    "zone": finding.zone,
                    "zone_unusual": finding.zone_unusual,
                    "source": getattr(finding, "source", None),
                },
            )
            finding_id = _require_row(cur)[0]

            for field, value in (("text", finding.text), ("comment", finding.comment)):
                if value:
                    cur.execute(
                        _INSERT_TRANSLATION_SQL,
                        ("finding", finding_id, field, inspection.speech_lang, value),
                    )

            for photo in finding.photos:
                cur.execute(_INSERT_PHOTO_SQL, (finding_id, inspection_id, photo))

        for lang, label in (("ru", result.label_ru), ("en", result.label_en)):
            if label:
                cur.execute(
                    _INSERT_TRANSLATION_SQL,
                    ("inspection", inspection_id, "grade_label", lang, label),
                )

    conn.commit()
    return str(inspection_id)


def push_inspection(chat_id: int, *, allow_unknown_version: bool = False) -> str:
    """Слить завершённую проверку чата в базу и вернуть её `id`.

    Повторяемо: второй вызов на той же завершённой проверке находит запись по
    отпечатку содержимого и возвращает тот же `id`, не создавая вторую строку
    и не переписывая находки заново.

    Падение базы не роняет проверку на точке — аудитор уже получил PDF
    независимо от этой функции (`report.build_pdf` его не вызывает и от него
    не зависит); отказ здесь означает только то, что слив нужно повторить
    позже тем же вызовом.

    Проверка без версии методики по умолчанию **не сливается**: отчёт заморожен
    на той версии, по которой посчитан (D033, D050), и запись без неё несравнима
    ни с чем — а молча положенная пустота портит будущую аналитику незаметно.
    Такие проверки бывают: файлы, созданные до того, как версия стала
    записываться. Чтобы залить их намеренно — историю за прошлые годы (D035), —
    передаётся `allow_unknown_version=True`, и тогда отсутствие версии
    становится осознанным решением вызывающего, а не случайностью.
    """
    settings = check_environment()
    try:
        inspection = get_state(chat_id)
        if inspection is None:
            raise PushError(f"В чате {chat_id} нет проверки — сливать нечего")
        if not (inspection.checklist_version or "").strip() and not allow_unknown_version:
            raise PushError(
                f"В проверке чата {chat_id} не записана версия методики. Такая "
                f"запись несравнима с другими: отчёт заморожен на своей версии и "
                f"задним числом не пересчитывается. Если проверка старая и версии "
                f"в ней нет по происхождению — слить можно явно, "
                f"push_inspection(chat_id, allow_unknown_version=True)"
            )
        result = domain_score(chat_id)
        with psycopg.connect(settings.dsn) as conn:
            return _push(conn, inspection, result)
    except PushError:
        raise
    except (psycopg.Error, DomainError) as exc:
        # Причина — в тексте, а не только в типе: движок с T106 отказывается
        # считать проверку с нечитаемой датой раньше, чем сюда дойдёт `_parse_date`,
        # и без этой подстановки вызывающий видел «не удался (EngineError)» без
        # единого слова о том, что чинить.
        raise PushError(
            f"Слив проверки чата {chat_id} в базу не удался ({type(exc).__name__}): {exc} "
            f"Файл проверки не тронут, слить можно будет позже — повторный вызов "
            f"не создаст дубль"
        ) from exc
