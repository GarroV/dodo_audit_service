"""Отказы операций над записями: «успех, которого не было» (T023).

Все проверки здесь — про один класс: движок отчитался об успехе, а результата
нет. Такой сбой опаснее падения, потому что бот показывает аудитору
подтверждение и идёт дальше, а запись не появилась, не изменилась или не
удалилась. Узнают об этом при завершении проверки, когда возвращаться на точку
уже поздно.

Эти пути оставались непокрытыми: в `src/domain/findings.py` каждый отказ был
написан, но ни один не проверялся. Дописано диспетчером.
"""

from __future__ import annotations

import pytest

from src.domain import add_finding, attach_photo, drop_finding, start_inspection
from src.domain.errors import EngineError, ValidationError
from src.domain.findings import _finding_after, _number

CHAT = 793_000_777


def _начатая() -> None:
    start_inspection(
        CHAT,
        unit="Проверка отказов",
        kind="planned",
        report_lang="ru",
        date="2026-09-03",
        auditor="Тест",
    )


def test_ответ_без_номера_записи_это_отказ() -> None:
    """Движок обязан назвать номер. Не назвал — молча считать первым нельзя."""
    with pytest.raises(EngineError) as exc:
        _number("готово, всё хорошо", command="add")
    assert "не назвал номер" in str(exc.value)
    assert "готово" in str(exc.value), "в отказе нет ответа движка — чинить вслепую"


def test_номер_из_ответа_читается() -> None:
    """Встречное утверждение: на нормальном ответе отказа быть не должно."""
    assert _number("Добавлена запись #7", command="add") == 7


def test_успех_без_записи_в_состоянии_это_отказ(domain_env: object) -> None:
    """Движок отчитался, а записи нет — подтверждать аудитору нечего."""
    _начатая()
    from src.domain.config import check_environment

    with pytest.raises(EngineError) as exc:
        _finding_after(CHAT, check_environment(), 99, "add")
    assert "#99" in str(exc.value)
    assert "нет" in str(exc.value)


def test_удаление_несуществующей_записи_отказывает(domain_env: object) -> None:
    """Тихий успех на удалении означал бы, что аудитор считает запись снятой."""
    _начатая()
    with pytest.raises(EngineError):
        drop_finding(CHAT, 42)


def test_пустой_кадр_отклоняется(domain_env: object) -> None:
    _начатая()
    add_finding(CHAT, code="CLN05", level="D1", zone="hot_kitchen", text="проба")
    with pytest.raises(ValidationError) as exc:
        attach_photo(CHAT, 1, "")
    assert "Пустой идентификатор" in str(exc.value)


def test_запятая_в_кадре_отклоняется(domain_env: object) -> None:
    """Движок разрежет идентификатор по запятой на два кадра — молча."""
    _начатая()
    add_finding(CHAT, code="CLN05", level="D1", zone="hot_kitchen", text="проба")
    with pytest.raises(ValidationError) as exc:
        attach_photo(CHAT, 1, "AgAC123,AgAC456")
    assert "запятая" in str(exc.value)
    assert "два кадра" in str(exc.value), "в отказе не сказано, чем это грозит"


def test_кадр_к_несуществующей_записи_отказывает(domain_env: object) -> None:
    _начатая()
    with pytest.raises((EngineError, ValidationError)):
        attach_photo(CHAT, 77, "AgAC123")
