"""Отпечаток проверки: идентичное содержимое → идентичный отпечаток (T093).

Чистая функция от дата-классов `domain` — ни файла, ни базы не нужно, поэтому
тесты покрывают именно ту гарантию, на которой держится DoD блока: «второй
вызов не создаёт дубль». Уникальный индекс в базе — вторая линия защиты, а
не единственная: если отпечаток окажется недетерминированным, индекс её не
спасёт.
"""

from __future__ import annotations

from src.db.fingerprint import compute_fingerprint
from src.domain.models import Finding, Inspection, Score


def _finding(**kw: object) -> Finding:
    base: dict[str, object] = {
        "n": 1,
        "code": "CLN05",
        "level": "D1",
        "zone": "hot_kitchen",
        "text": "нагар на печи",
        "comment": "",
        "photos": ["p1.jpg"],
        "zone_unusual": False,
    }
    base.update(kw)
    return Finding(**base)  # type: ignore[arg-type]


def _inspection(**kw: object) -> Inspection:
    base: dict[str, object] = {
        "chat_id": 1,
        "unit": "Белград-1",
        "kind": "planned",
        "date": "2026-08-21",
        "report_lang": "ru",
        "ui_lang": "ru",
        "speech_lang": "ru",
        "checklist_version": "local-abc123",
        "tenant": "default",
        "findings": [_finding()],
    }
    base.update(kw)
    return Inspection(**base)  # type: ignore[arg-type]


def _score(**kw: object) -> Score:
    base: dict[str, object] = {
        "pct": 97.5,
        "grade": "A",
        "label_ru": "Отлично",
        "label_en": "Excellent",
        "counts": {"D1": 1},
        "deductions": 2.5,
        "by_zone": {},
    }
    base.update(kw)
    return Score(**base)  # type: ignore[arg-type]


def test_одинаковое_содержимое_даёт_одинаковый_отпечаток() -> None:
    a = compute_fingerprint(_inspection(), _score(), tenant_code="default")
    b = compute_fingerprint(_inspection(), _score(), tenant_code="default")
    assert a == b


def test_порядок_находок_в_файле_не_влияет_на_отпечаток() -> None:
    """Домен не гарантирует порядок при чтении JSON — отпечаток обязан сортировать сам."""
    f1, f2 = _finding(n=1, code="CLN05"), _finding(n=2, code="CLN06")
    прямой = compute_fingerprint(_inspection(findings=[f1, f2]), _score(), tenant_code="default")
    обратный = compute_fingerprint(_inspection(findings=[f2, f1]), _score(), tenant_code="default")
    assert прямой == обратный


def test_другая_находка_меняет_отпечаток() -> None:
    базовый = compute_fingerprint(_inspection(), _score(), tenant_code="default")
    другая = compute_fingerprint(
        _inspection(findings=[_finding(text="другой текст")]), _score(), tenant_code="default"
    )
    assert базовый != другая


def test_другой_арендатор_меняет_отпечаток() -> None:
    """Мультиарендность (D017): одна на вид проверка в разных пространствах — разные строки."""
    первый = compute_fingerprint(_inspection(), _score(), tenant_code="default")
    второй = compute_fingerprint(_inspection(), _score(), tenant_code="partner-x")
    assert первый != второй


def test_другая_оценка_меняет_отпечаток() -> None:
    """Правка ставок вычетов и пересчёт по новой версии — это уже другая проверка."""
    базовый = compute_fingerprint(_inspection(), _score(), tenant_code="default")
    другой = compute_fingerprint(_inspection(), _score(pct=50.0, grade="C"), tenant_code="default")
    assert базовый != другой


def test_источник_записи_не_влияет_на_отпечаток() -> None:
    """Откуда взялась запись — обстоятельство фиксации, а не содержимое (#158).

    Тем же доводом из отпечатка исключены сырые слова аудитора и предложение
    модели: партнёру уходит документ, и «со слов» против «по кадру» в нём не
    печатается ни строкой. Оставшись в отпечатке, источник делал бы две
    одинаковые для читателя проверки разными строками истории точки.
    """
    со_слов = compute_fingerprint(
        _inspection(findings=[_finding(source="comment")]), _score(), tenant_code="default"
    )
    по_кадру = compute_fingerprint(
        _inspection(findings=[_finding(source="photo")]), _score(), tenant_code="default"
    )
    assert со_слов == по_кадру
