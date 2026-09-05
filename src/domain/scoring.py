"""Оценка проверки. Считает её движок, блок только разбирает ответ.

Здесь нет и не может быть ни одной цифры методики: ни ставок вычетов, ни
порогов букв, ни долей зон. Один отчёт уже ушёл партнёру с буквой, поставленной
«на глаз», — поэтому расчёт живёт в одном месте, а его дублирование в коде
продукта запрещено технически (контракт `engine-not-imported` в `lint-imports`).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from .config import check_environment
from .edition import pin
from .engine import run_audit
from .errors import EngineError
from .models import Score, ZoneScore
from .state import assert_checklist_version

#: Ключи разбивки по зоне, которые не являются счётчиками записей. Всё
#: остальное движок называет по классу (`D1`, `D2`, `D3`, а при информационных
#: записях ещё и `D0`) — перечислять классы здесь нельзя, это методика.
ZONE_FIELDS = ("name_ru", "name_en", "share", "loss", "score", "zeroed")


def _counts(raw: Mapping[str, Any]) -> dict[str, int]:
    return {k: int(v) for k, v in raw.items() if k not in ZONE_FIELDS}


def _zone(code: str, raw: Mapping[str, Any]) -> ZoneScore:
    return ZoneScore(
        code=code,
        name_ru=str(raw.get("name_ru") or code),
        name_en=str(raw.get("name_en") or code),
        share=float(raw.get("share") or 0.0),
        counts=_counts(raw),
        loss=float(raw.get("loss") or 0.0),
        # У движка это поле называется `score` — «сколько от доли зоны осталось».
        left=float(raw.get("score") or 0.0),
        zeroed=bool(raw.get("zeroed")),
    )


def score(chat_id: int) -> Score:
    """Оценка проверки этого чата — разбор ответа `audit.py score --json`.

    Считать можно только по той методике, которой проверка помечена: иначе
    оценка выходит по новой методике под старой отметкой (T148). Издание
    берётся из снимка, снятого на старте проверки (T169), — переиздание
    методики посреди выезда цифру не двигает. Снимка нет и издание другое —
    `ChecklistVersionMismatch`, а не тихий пересчёт.
    """
    settings = check_environment()
    # Сверка ДО вызова движка: движок посчитает по тому, что ему передали, и
    # отличить ответ по чужой методике от честного по одной цифре уже нельзя.
    assert_checklist_version(chat_id, settings)
    # Методика — ТОЙ проверки, а не действующая: `pin` подставляет снимок
    # издания, при котором проверку начали.
    out = run_audit(["score", "--json"], chat_id=chat_id, settings=pin(chat_id, settings))
    try:
        raw: Any = json.loads(out)
    except json.JSONDecodeError as exc:
        raise EngineError(
            f"Движок ответил на score не разбираемым JSON: {out.strip()[:200]!r}",
            code=0,
            command="score",
        ) from exc
    counts: Mapping[str, Any] = raw.get("counts") or {}
    zones: Mapping[str, Any] = raw.get("zones") or {}
    # Счётчики берутся как есть: у движка в них попадает и `D0` —
    # информационные записи, которые на процент не влияют, но в отчёте нужны.
    return Score(
        pct=float(raw["pct"]),
        grade=str(raw["grade"]),
        label_ru=str(raw.get("grade_label_ru") or ""),
        label_en=str(raw.get("grade_label_en") or ""),
        counts={level: int(number) for level, number in counts.items()},
        deductions=float(raw.get("deductions") or 0.0),
        by_zone={code: _zone(code, value) for code, value in zones.items()},
    )
