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
from src.domain.errors import ChecklistVersionMismatch, DomainError
from src.domain.models import Finding, Inspection, Score

from .config import check_environment
from .directory import resolve_unit_id
from .errors import PushError, VersionMismatchError
from .fingerprint import compute_fingerprint, previous_fingerprints
from .units import normalize_unit_name

#: Арендатор по умолчанию, пока в проверке своего не задано. Совпадает со
#: значением `domain.state.DEFAULT_TENANT` буквально: второй копии константы
#: здесь допустить нельзя, но импортировать внутреннюю переменную домена ради
#: одной строки — цена выше пользы, поэтому значение продублировано как
#: строковый литерал и закреплено тестом на конкретное значение "default".
DEFAULT_TENANT = "default"

# `where retracted_at is null` в `on conflict` — не украшение, а обязательная
# часть указания на индекс: с миграции `0010` отпечаток уникален только среди
# ЖИВЫХ проверок (T210), и без предиката PostgreSQL не находит подходящего
# индекса вовсе — слив падает «there is no unique or exclusion constraint
# matching the ON CONFLICT specification». Смысл же тот, ради которого
# уникальность стала частичной: слитая заново после снятия проверка ложится
# новой строкой, а повторный слив живой — по-прежнему не создаёт дубля.
#
# Проверка кладётся как `draft` и запечатывается последним действием ТОЙ ЖЕ
# транзакции (T111). Это не церемония: политика `findings_not_added_to_finalized`
# запрещает дописывать находки в запечатанную проверку — без черновой фазы слив
# не смог бы положить собственные находки, а без запрета «переписать проверку»
# осталось бы возможным через добавление ещё одной находки задним числом.
# Наружу черновик не виден никогда: транзакция одна, и незапечатанная проверка
# не переживает её конца.
_INSERT_INSPECTION_SQL = """
insert into inspections (
    tenant_code, unit_id, chat_id, kind, inspection_date, report_lang,
    ui_lang, speech_lang, checklist_version, auditor, city, partner, contact,
    pct, grade, deductions, counts, by_zone, source_fingerprint, status
) values (
    %(tenant_code)s, %(unit_id)s, %(chat_id)s, %(kind)s, %(inspection_date)s,
    %(report_lang)s, %(ui_lang)s, %(speech_lang)s, %(checklist_version)s,
    %(auditor)s, %(city)s, %(partner)s, %(contact)s, %(pct)s, %(grade)s,
    %(deductions)s, %(counts)s, %(by_zone)s, %(source_fingerprint)s, 'draft'
)
on conflict (source_fingerprint) where retracted_at is null do nothing
returning id
"""

#: Печать проверки. Условие `status = 'draft'` не украшение: запечатать можно
#: только незапечатанное, и число затронутых строк ниже проверяется, а не
#: считается заведомо единицей (конституция: у операции с наблюдаемым
#: результатом проверяется результат, а не отсутствие исключения).
_SEAL_INSPECTION_SQL = (
    "update inspections set status = 'finalized' where id = %s and status = 'draft'"
)

_SELECT_BY_FINGERPRINT_SQL = "select id from inspections where source_fingerprint = %s"

_UPSERT_UNIT_SQL = """
insert into units (tenant_code, name, name_normalized)
values (%s, %s, %s)
on conflict (tenant_code, name_normalized) do update set name = excluded.name
returning id
"""

_INSERT_TENANT_SQL = "insert into tenants (code) values (%s) on conflict (code) do nothing"

_INSERT_FINDING_SQL = """
insert into findings (
    inspection_id, n, code, level, zone, zone_unusual, source, words,
    suggested_code, suggested_level, suggested_zone, suggested_confidence
) values (
    %(inspection_id)s, %(n)s, %(code)s, %(level)s, %(zone)s, %(zone_unusual)s, %(source)s,
    %(words)s,
    %(suggested_code)s, %(suggested_level)s, %(suggested_zone)s, %(suggested_confidence)s
)
returning id
"""

_INSERT_PHOTO_SQL = """
insert into photos (finding_id, inspection_id, telegram_file_id)
values (%s, %s, %s)
"""

#: Одно поле информационной части (T200). Место в записанном порядке кладётся
#: явной колонкой: движок печатает поля в том порядке, в каком их записали, и
#: чтение «order by code» переставило бы разделы документа партнёру.
_INSERT_INFO_SQL = """
insert into inspection_info (inspection_id, position, code, text)
values (%(inspection_id)s, %(position)s, %(code)s, %(text)s)
returning id
"""

#: Кадр информационного поля — та же таблица, что и у кадров записей: выгрузка
#: в хранилище (T094) берёт кадры проверки по `inspection_id` и про владельца
#: не спрашивает, поэтому кадр поля уезжает в хранилище без единой правки в ней.
_INSERT_INFO_PHOTO_SQL = """
insert into photos (info_id, inspection_id, telegram_file_id)
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


def _words(finding: Finding) -> str | None:
    """Сырые слова аудитора к записи — дословно, либо `NULL` (T185).

    Дословно, потому что это показание о моменте: расшифровка голоса приходит
    несколькими строками, и подправленная по дороге — обрезанная, склеенная —
    она перестаёт быть тем, что человек произнёс. По ней управляющая компания
    правит карту слов (D077), то есть сверяет с ней наш промах: подправленное
    показание сверять не с чем.

    Пусто — `NULL`, и пробельная строка тоже: молчание не речь, а пробелы в
    колонке выглядели бы записанной фразой, у которой не прочитать ни слова.
    Разных видов пустоты здесь не заводится: «слова не записаны» не бывает двух
    видов, а «слов не было вовсе» от «запись сделана до T183» отличает
    `findings.source` — так же, как это делает домен.

    Читается прямым обращением к полю, а не `getattr`, в отличие от `source` и
    предложения модели: те заводились со стороны базы РАНЬШЕ, чем домен начал
    отдавать значение, а слова домен отдаёт с T183. `getattr` со значением по
    умолчанию здесь означал бы только одно — что переименованное в домене поле
    молча превратится в «слов не было».
    """
    сказанное = finding.words
    return сказанное if сказанное.strip() else None


def _suggested_text(finding: object, field: str) -> str | None:
    """Одно текстовое поле предложения модели. Пусто — предложения нет.

    Пустая строка и `None` склеены намеренно: «модель не называла пункта» не
    бывает двух разных видов, а пустая строка в колонке выглядела бы ответом
    модели и попала бы в выборку для управляющей компании как промах.
    """
    значение = getattr(finding, field, None)
    if значение is None:
        return None
    текст = str(значение).strip()
    return текст or None


def _suggested_confidence(finding: object, *, n: int) -> float | None:
    """Уверенность модели: доля от нуля до единицы — или явный отказ.

    Не разбирается — не записывается. Записанное «непонятно что» неотличимо в
    базе от настоящего значения (тот же довод, что у `domain.read_sources`), а
    подстановка нуля хуже всего: «модель ни в чём не уверена» — осмысленное
    утверждение, и оно было бы ложью.

    Диапазон проверяется здесь, а не только ограничением схемы, чтобы отказ
    назвал число и запись: у нарушенного `check` в тексте psycopg стоит имя
    ограничения, а не то, что чинить.
    """
    значение = getattr(finding, "suggested_confidence", None)
    if значение is None:
        return None
    if isinstance(значение, bool) or not isinstance(значение, int | float | str):
        raise PushError(
            f"Уверенность модели в записи #{n} — «{значение!r}», а ожидается доля от 0 до 1. "
            f"Записать непонятное значение нельзя: в базе оно неотличимо от настоящего"
        )
    try:
        доля = float(значение)
    except ValueError as exc:
        raise PushError(
            f"Уверенность модели в записи #{n} — «{значение}», а ожидается доля от 0 до 1. "
            f"Записать непонятное значение нельзя: в базе оно неотличимо от настоящего"
        ) from exc
    if not 0.0 <= доля <= 1.0:
        raise PushError(
            f"Уверенность модели в записи #{n} равна {доля} — это не доля от 0 до 1. "
            f"Чужая шкала (проценты вместо доли) обесценила бы порог отбора предложений "
            f"для управляющей компании на всей выборке разом"
        )
    return доля


def _suggestion(finding: object) -> dict[str, object]:
    """Предложение модели к одной записи (T164, D077).

    Читается через `getattr` по той же причине и тем же приёмом, что и
    `source`: форма заложена со стороны базы раньше, чем домен начнёт отдавать
    значение. Пока не отдаёт — во всех четырёх колонках `NULL`, и это честно
    означает «модель не предлагала ничего».
    """
    номер = int(getattr(finding, "n", 0))
    return {
        "suggested_code": _suggested_text(finding, "suggested_code"),
        "suggested_level": _suggested_text(finding, "suggested_level"),
        "suggested_zone": _suggested_text(finding, "suggested_zone"),
        "suggested_confidence": _suggested_confidence(finding, n=номер),
    }


def _push_info(cur: psycopg.Cursor[Any], inspection: Inspection, *, inspection_id: Any) -> None:
    """Информационная часть проверки: ответы аудитора и приложенные к ним кадры (T200).

    Кладётся В ЗАПИСАННОМ ПОРЯДКЕ, и место каждого поля пишется явной колонкой.
    Порядок здесь — содержимое документа, а не оформление: движок печатает поля
    в том порядке, в каком их записали, и отсортированное по коду чтение
    переставило бы разделы отчёта партнёру. Сегодня коды и порядок вопросов
    совпадают, но совпадение не правило: порядок задаёт бот (`src/bot/info.py`),
    а состав части — чек-лист управляющей компании.

    Ответ уезжает ДОСЛОВНО. Это не формулировка по правилам фиксации, а слова,
    которые аудитор адресовал партнёру, и один из них — срок, по которому с
    партнёра спросят. Подправленный по дороге, он перестаёт быть тем, что было
    названо, а расхождение видно только когда обе бумаги уже на руках.

    Пустой ответ не пишется вовсе. Пропущенное поле по решению D070 не
    печатается, и пустая строка в базе выглядела бы ответом, которого не было:
    в выборке она неотличима от заполненного поля. Домен пустое и не пропустит
    (`domain.set_info` отказывает), но состояние — обычный JSON на диске, и
    сюда доезжает то, что в нём лежит; отказать в сливе всей проверки из-за
    пустого поля значило бы потерять документ целиком ради одной строки.
    """
    место = 0
    for code, answer in inspection.info.items():
        if not answer.text.strip():
            continue
        cur.execute(
            _INSERT_INFO_SQL,
            {
                "inspection_id": inspection_id,
                "position": место,
                # Код берётся ключом состояния, а не полем ответа: ключом
                # движок и адресует поле, и он же уедет в собранный заново
                # документ.
                "code": code,
                "text": answer.text,
            },
        )
        info_id = _require_row(cur)[0]
        for photo in answer.photos:
            cur.execute(_INSERT_INFO_PHOTO_SQL, (info_id, inspection_id, photo))
        место += 1


def _push(conn: psycopg.Connection[Any], inspection: Inspection, result: Score) -> str:
    tenant_code = _tenant_code(inspection)
    fingerprint = compute_fingerprint(inspection, result, tenant_code=tenant_code)
    inspection_date = _parse_date(inspection.date, chat_id=inspection.chat_id)

    with conn.cursor() as cur:
        # Прежде всего — не лежит ли проверка в базе под ОТПЕЧАТКОМ ПРЕЖНЕГО
        # РЕЦЕПТА (T193). Рецепт менялся, и слитая до этого проверка по
        # текущему отпечатку не находится: без этой сверки повторный слив
        # положил бы её второй строкой в историю точки, а разделить их обратно
        # уже нечем — запечатанная проверка не правится и не удаляется
        # (миграция `0004`). Сверка стоит до любой записи намеренно: у слива,
        # который ничего не сливает, не должно оставаться следов вовсе.
        for legacy in previous_fingerprints(inspection, result, tenant_code=tenant_code):
            cur.execute(_SELECT_BY_FINGERPRINT_SQL, (legacy,))
            row = cur.fetchone()
            if row is not None:
                return str(row[0])

        cur.execute(_INSERT_TENANT_SQL, (tenant_code,))
        # Сначала справочник и карта синонимов (T092): «БГ2», введённое на
        # бегу, обязано лечь в ту же точку, что «Белград 2», — иначе у одной
        # пиццерии заводятся две несвязанные истории, а история точки и есть
        # то, ради чего проверки складываются в базу (D035). Не нашлось —
        # точка заводится по нормализованному названию, как и раньше: справочник
        # может быть не заполнен, и это не повод отказать в сливе на точке.
        unit_id = resolve_unit_id(conn, inspection.unit, tenant=tenant_code)
        if unit_id is None:
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
                    "words": _words(finding),
                    **_suggestion(finding),
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

        _push_info(cur, inspection, inspection_id=inspection_id)

        for lang, label in (("ru", result.label_ru), ("en", result.label_en)):
            if label:
                cur.execute(
                    _INSERT_TRANSLATION_SQL,
                    ("inspection", inspection_id, "grade_label", lang, label),
                )

        # Проверка собрана целиком — печатаем. После этого её нельзя ни
        # переписать, ни удалить, ни дополнить (T111, миграция 0004): документ
        # ушёл партнёру и стал основанием для требований к нему.
        cur.execute(_SEAL_INSPECTION_SQL, (inspection_id,))
        if cur.rowcount != 1:
            raise PushError(
                f"Проверку чата {inspection.chat_id} не удалось запечатать: "
                f"обновлено строк — {cur.rowcount}, ожидалась одна. Слив отменён "
                f"целиком; незапечатанная проверка в базе не остаётся"
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
    except ChecklistVersionMismatch as exc:
        # Методику переиздали между итогом и сливом (T178). База здесь ни при
        # чём, и общий отказ слива отправил бы человека чинить не то: бот
        # разбирает исходы слива по ТИПУ, и в ветке «база не приняла» этот
        # случай выглядел бы отказом связи. Обе версии уходят полями — по ним
        # показывающий соберёт человеку выбор, не разбирая текст.
        raise VersionMismatchError(
            f"Проверку чата {chat_id} не слили: {exc} Файл проверки не тронут — "
            f"после того, как выбор сделан, слить можно тем же вызовом",
            recorded=exc.recorded,
            current=exc.current,
        ) from exc
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
