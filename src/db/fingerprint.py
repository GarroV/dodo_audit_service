"""Отпечаток содержимого завершённой проверки.

Чистая функция от того, что уже отдал `domain` — ни файла, ни базы здесь нет,
поэтому она проверяется без Postgres. Одинаковое содержимое всегда даёт
одинаковый отпечаток, а уникальный индекс на нём в `inspections` — техническая
гарантия того, что повторный `push_inspection` не создаёт вторую строку (DoD
блока), даже если локальная отметка в `inspection.json` потерялась.

**Что входит в отпечаток, а что нет.** Входит содержимое документа, ушедшего
партнёру: точка, вид и дата проверки, находки, информационная часть (T200) и
оценка движка. Не входит то, что этот документ сопровождает, но в нём не
печатается, — сырые слова аудитора (T185), предложение модели (T164) и
источник записи, «со слов» или «по кадру» (T193, задача #158). Все три
обстоятельства фиксации, а не содержимое, и все три появляются или уточняются
отдельно от него: попади они в отпечаток, одна и та же для читателя проверка
дала бы две строки в истории точки.

**Информационная часть входит ровно по тому правилу, по которому не входят
они.** Она в документе ПЕЧАТАЕТСЯ: приложением отчёта, а срок плана действий —
прямо в письме партнёру. Две проверки, у которых партнёру названы разные
сроки, — два разных требования к нему; слитые под одним отпечатком, они стали
бы одной строкой, и вторая не легла бы в базу вовсе.

**Рецепт отпечатка — это идентичность строк, УЖЕ лежащих в базе.** Меняя его,
пересчитать нельзя: слитая вчера проверка перестала бы находиться, повторный
слив вставил бы её второй раз, а разделить эти две строки обратно нечем —
миграция `0004` запрещает запечатанную проверку и править, и удалять. Поэтому
прежние рецепты отсюда не выбрасываются (`previous_fingerprints`): слив ищет
проверку сперва по ним и только не найдя — пишет по текущему.
"""

from __future__ import annotations

import hashlib
import json

from src.domain.models import Finding, Inspection, Score


def _finding_payload(finding: Finding, *, with_source: bool) -> dict[str, object]:
    payload: dict[str, object] = {
        "n": finding.n,
        "code": finding.code,
        "level": finding.level,
        "zone": finding.zone,
        "text": finding.text,
        "comment": finding.comment,
        "zone_unusual": finding.zone_unusual,
        "photos": list(finding.photos),
    }
    if with_source:
        # Только рецепт до T193. `getattr` — по той же причине, по какой он
        # стоял здесь тогда: прежний отпечаток должен пересчитываться и для
        # проверок, созданных до появления источника (T065).
        payload["source"] = getattr(finding, "source", None)
    return payload


def _info_payload(inspection: Inspection) -> list[dict[str, object]]:
    """Информационная часть в ЗАПИСАННОМ порядке, а не отсортированная по коду.

    Порядок здесь — содержимое, а не оформление: движок печатает поля в том
    порядке, в каком их записали, и это порядок разделов документа партнёру.
    Находки рядом, наоборот, сортируются по номеру — у них порядок задан самим
    номером, и порядок строк в файле на документ не влияет.

    Кадры поля входят по той же причине, по какой входят кадры записи: их
    печатает отчёт, то есть они часть того же документа.
    """
    return [
        {"code": answer.code, "text": answer.text, "photos": list(answer.photos)}
        for answer in inspection.info.values()
    ]


def _payload(
    inspection: Inspection, score: Score, *, tenant_code: str, with_source: bool, with_info: bool
) -> dict[str, object]:
    payload: dict[str, object] = {
        "tenant": tenant_code,
        "chat_id": inspection.chat_id,
        "unit": inspection.unit,
        "kind": inspection.kind,
        "date": inspection.date,
        "report_lang": inspection.report_lang,
        "checklist_version": inspection.checklist_version,
        "findings": [
            _finding_payload(f, with_source=with_source)
            for f in sorted(inspection.findings, key=lambda f: f.n)
        ],
        "pct": score.pct,
        "grade": score.grade,
    }
    if with_info:
        # Ключ добавляется, а не подставляется пустым списком в обоих рецептах:
        # проверка без единого заполненного поля обязана отличаться отпечатком
        # от той же проверки по прежнему рецепту, иначе «прежний» и «текущий»
        # совпали бы там, где информационной части нет, и совместимость стало
        # бы нечем проверить.
        payload["info"] = _info_payload(inspection)
    return payload


def _digest(payload: dict[str, object]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def compute_fingerprint(inspection: Inspection, score: Score, *, tenant_code: str) -> str:
    """Отпечаток проверки: чата, точки, находок, информационной части и оценки движка."""
    return _digest(
        _payload(inspection, score, tenant_code=tenant_code, with_source=False, with_info=True)
    )


def previous_fingerprints(
    inspection: Inspection, score: Score, *, tenant_code: str
) -> tuple[str, ...]:
    """Та же проверка по ВЫШЕДШИМ ИЗ УПОТРЕБЛЕНИЯ рецептам, новейший первым.

    Нужны сливу и только ему: проверка, лежащая в базе под прежним отпечатком,
    обязана найтись, а не лечь второй строкой (`push._push`). Запись уходит
    отсюда не тогда, когда рецепт надоел, а тогда, когда известно, что строк
    того рецепта не осталось ни в одной базе, — а такое знание есть у площадки,
    не у кода.

    Рецепты, новейший первым:

    * **до T200** — тот же набор полей БЕЗ информационной части.
    * **до T193** — тот же набор полей без информационной части и плюс
      источник записи у каждой находки.

    Комбинации «с источником и с информационной частью» здесь нет намеренно:
    рецепты перечисляют то, что БЫЛО в употреблении, а такого сочетания не было
    никогда. Лишний рецепт стоил бы лишнего запроса на каждом сливе и делал бы
    вид, что где-то лежат строки, которых не существует.
    """
    return (
        _digest(
            _payload(inspection, score, tenant_code=tenant_code, with_source=False, with_info=False)
        ),
        _digest(
            _payload(inspection, score, tenant_code=tenant_code, with_source=True, with_info=False)
        ),
    )
