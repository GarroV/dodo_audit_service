"""Отпечаток содержимого завершённой проверки.

Чистая функция от того, что уже отдал `domain` — ни файла, ни базы здесь нет,
поэтому она проверяется без Postgres. Одинаковое содержимое всегда даёт
одинаковый отпечаток, а уникальный индекс на нём в `inspections` — техническая
гарантия того, что повторный `push_inspection` не создаёт вторую строку (DoD
блока), даже если локальная отметка в `inspection.json` потерялась.
"""

from __future__ import annotations

import hashlib
import json

from src.domain.models import Finding, Inspection, Score


def _finding_payload(finding: Finding) -> dict[str, object]:
    return {
        "n": finding.n,
        "code": finding.code,
        "level": finding.level,
        "zone": finding.zone,
        "text": finding.text,
        "comment": finding.comment,
        "zone_unusual": finding.zone_unusual,
        "photos": list(finding.photos),
        # Домен пока не отдаёт источник записи (задача T065, D044) — как
        # появится, отпечаток начнёт учитывать и его без правки этой функции.
        "source": getattr(finding, "source", None),
    }


def compute_fingerprint(inspection: Inspection, score: Score, *, tenant_code: str) -> str:
    """Отпечаток проверки: чата, точки, находок и итоговой оценки движка."""
    payload = {
        "tenant": tenant_code,
        "chat_id": inspection.chat_id,
        "unit": inspection.unit,
        "kind": inspection.kind,
        "date": inspection.date,
        "report_lang": inspection.report_lang,
        "checklist_version": inspection.checklist_version,
        "findings": [_finding_payload(f) for f in sorted(inspection.findings, key=lambda f: f.n)],
        "pct": score.pct,
        "grade": score.grade,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
